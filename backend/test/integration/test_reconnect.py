"""Verifies the backend's reconnect/backoff loop recovers after the broker
container is restarted mid-test, and resumes processing seat requests and
orders afterward.

Requires docker. Run with: pytest -m integration
"""

import asyncio
import json

import aiomqtt
import pytest
from src.config import Settings
from src.mqtt.client import run_forever
from src.mqtt.topics import food_topic, order_topic

from ..conftest import request_seat_and_wait_assigned

pytestmark = pytest.mark.integration


async def _wait_until_broker_accepts_connections(
    testcontainer, username: str, password: str, original_port: int, timeout: float = 30.0
) -> int:
    """Polls with a real MQTT connect attempt until the broker accepts one,
    or raises. Returns the port that actually worked.

    A fixed sleep here is unreliable (restart timing varies), and re-checking
    wait_for_logs' startup-banner pattern would false-positive on the
    *first* startup's banner, since Mosquitto's container logs aren't
    cleared across a restart.

    Also re-resolves the container's exposed port on every attempt rather
    than trusting `original_port`: in a nested/sandboxed Docker setup, a
    restart can occasionally remap a container to a *different* host port,
    which would otherwise manifest as this test polling a now-dead port
    forever - a permanent failure indistinguishable from "broker never
    came back" unless checked for explicitly.
    """
    host = testcontainer.get_container_host_ip()
    docker_container = testcontainer.get_wrapped_container()
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    last_exc: Exception | None = None
    port_changed = False
    while loop.time() < deadline:
        current_port = int(testcontainer.get_exposed_port(1883))
        if current_port != original_port:
            port_changed = True
        try:
            async with aiomqtt.Client(
                hostname=host, port=current_port, username=username, password=password
            ):
                return current_port
        except aiomqtt.MqttError as exc:
            last_exc = exc
            await asyncio.sleep(0.3)

    docker_container.reload()
    logs = docker_container.logs(tail=50).decode(errors="replace")
    port_note = (
        f"port changed from {original_port} to {testcontainer.get_exposed_port(1883)} after restart - "
        "the backend under test is still configured with the old port and could never have "
        "reconnected regardless of broker health"
        if port_changed
        else f"port stayed at {original_port}"
    )
    raise TimeoutError(
        f"broker did not accept connections after restart: {last_exc}\n"
        f"{port_note}\n"
        f"container status: {docker_container.status}\n"
        f"recent container logs:\n{logs}"
    )


async def test_backend_recovers_after_broker_restart(restartable_mosquitto_broker):
    # Uses its own dedicated broker (not the session-scoped one every other
    # test shares) - restarting it here must not be able to take down the
    # rest of the suite if the restart doesn't come back cleanly.
    broker = restartable_mosquitto_broker
    settings = Settings(
        broker_host=broker["host"],
        broker_port=broker["port"],
        broker_username="backend",
        broker_password=broker["password"],
        min_delay_seconds=0.1,
        max_delay_seconds=0.2,
        reconnect_initial_backoff_seconds=0.2,
        reconnect_max_backoff_seconds=1.0,
    )
    stop_event = asyncio.Event()
    task = asyncio.create_task(run_forever(settings, stop_event))
    try:
        await asyncio.sleep(0.5)

        broker["container"].get_wrapped_container().restart()
        port_after_restart = await _wait_until_broker_accepts_connections(
            broker["container"], "customer", broker["password"], broker["port"]
        )
        if port_after_restart != broker["port"]:
            # Not a code bug: a real broker at a fixed address never changes
            # port when it restarts, so the backend has no reason to handle
            # that case. This environment's Docker networking (nested/
            # sandboxed) remaps the container's published port on restart,
            # which no amount of backend-side reconnect logic can paper
            # over - skip rather than fail, since a hard failure here would
            # misleadingly read as a reconnect regression instead of an
            # environment limitation. (Still hits `finally` below via the
            # Skipped exception, so the backend task gets cleaned up.)
            pytest.skip(
                f"broker restart changed its published port ({broker['port']} -> "
                f"{port_after_restart}) - this environment's Docker setup remaps ports on "
                "restart, which no real deployment does; the backend has no way to discover "
                "a new port and isn't expected to"
            )
        # The backend's own reconnect loop needs a moment after the broker
        # itself is reachable again to notice, back off, and resubscribe.
        await asyncio.sleep(1.0)

        session_id = "session-after-restart"
        async with aiomqtt.Client(
            hostname=broker["host"],
            port=broker["port"],
            username="customer",
            password=broker["password"],
            identifier=session_id,
        ) as customer:
            table_id = await request_seat_and_wait_assigned(customer, session_id)

            await customer.subscribe(food_topic(table_id), qos=1)
            await customer.publish(
                order_topic(table_id),
                payload=json.dumps(
                    {
                        "food_name": "after-restart",
                        "client_order_id": "after-restart-1",
                        "session_id": session_id,
                    }
                ),
                qos=1,
            )
            message = await asyncio.wait_for(anext(aiter(customer.messages)), timeout=5)
            assert json.loads(message.payload)["food_name"] == "after-restart"
    finally:
        stop_event.set()
        await asyncio.wait_for(task, timeout=5)
