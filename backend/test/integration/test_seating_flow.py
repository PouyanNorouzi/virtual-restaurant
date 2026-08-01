"""End-to-end seat-assignment tests against a real broker + running backend:
the full request -> assign -> order -> vacate -> auto-reassign cycle, the
domain-level "you're not seated here" order rejection, %c-pattern isolation
between two different sessions' seating topics, and MQTT
Last-Will-triggered auto-vacate on an unclean disconnect.

Requires docker. Run with: pytest -m integration
"""

import asyncio
import json

import aiomqtt
import pytest
from src.config import Settings
from src.mqtt.client import run_forever
from src.mqtt.topics import (
    food_topic,
    order_accepted_topic,
    order_topic,
    rejected_topic,
    seating_finished_topic,
    seating_status_topic,
    seating_vacate_topic,
)

from .conftest import request_seat_and_wait_assigned

pytestmark = pytest.mark.integration


@pytest.fixture
async def backend_task(mosquitto_broker):
    settings = Settings(
        broker_host=mosquitto_broker["host"],
        broker_port=mosquitto_broker["port"],
        broker_username="backend",
        broker_password=mosquitto_broker["password"],
        num_tables=1,  # forces the second session to queue, deterministically
        min_delay_seconds=0.1,
        max_delay_seconds=0.2,
    )
    stop_event = asyncio.Event()
    task = asyncio.create_task(run_forever(settings, stop_event))
    await asyncio.sleep(0.5)
    yield settings
    stop_event.set()
    await asyncio.wait_for(task, timeout=5)


async def test_request_assign_order_vacate_and_requeue_flow(mosquitto_broker, backend_task):
    session_a, session_b = "seat-flow-a", "seat-flow-b"

    async with (
        aiomqtt.Client(
            hostname=mosquitto_broker["host"],
            port=mosquitto_broker["port"],
            username="customer",
            password=mosquitto_broker["password"],
            identifier=session_a,
        ) as client_a,
        aiomqtt.Client(
            hostname=mosquitto_broker["host"],
            port=mosquitto_broker["port"],
            username="customer",
            password=mosquitto_broker["password"],
            identifier=session_b,
        ) as client_b,
    ):
        table_id = await request_seat_and_wait_assigned(client_a, session_a)
        assert table_id == 1

        # Only one table exists in this fixture, so B must queue.
        await client_b.subscribe(seating_status_topic(session_b), qos=1)
        await client_b.publish(
            f"restaurant/seating/{session_b}/request", payload=b"{}", qos=1
        )
        b_status = json.loads(
            (await asyncio.wait_for(anext(aiter(client_b.messages)), timeout=5)).payload
        )
        assert b_status == {"schema": "seat.status.v1", "state": "queued", "queue_position": 1}

        # A orders successfully from its assigned table.
        await client_a.subscribe(food_topic(table_id), qos=1)
        await client_a.publish(
            order_topic(table_id),
            payload=json.dumps(
                {"food_name": "ramen", "client_order_id": "order-1", "session_id": session_a}
            ),
            qos=1,
        )
        food = json.loads(
            (await asyncio.wait_for(anext(aiter(client_a.messages)), timeout=5)).payload
        )
        assert food["food_name"] == "ramen"

        # A vacates; B should be auto-promoted onto the freed table with no
        # re-request, and can now order there too.
        await client_a.publish(seating_vacate_topic(session_a), payload=b"{}", qos=1)
        b_assigned = json.loads(
            (await asyncio.wait_for(anext(aiter(client_b.messages)), timeout=5)).payload
        )
        assert b_assigned == {"schema": "seat.status.v1", "state": "assigned", "table_id": 1}

        await client_b.subscribe(food_topic(1), qos=1)
        await client_b.publish(
            order_topic(1),
            payload=json.dumps(
                {"food_name": "udon", "client_order_id": "order-2", "session_id": session_b}
            ),
            qos=1,
        )
        food_b = json.loads(
            (await asyncio.wait_for(anext(aiter(client_b.messages)), timeout=5)).payload
        )
        assert food_b["food_name"] == "udon"


async def test_order_accepted_event_arrives_before_food_with_matching_delay(
    mosquitto_broker, backend_task
):
    session_id = "seat-flow-accepted"
    async with aiomqtt.Client(
        hostname=mosquitto_broker["host"],
        port=mosquitto_broker["port"],
        username="customer",
        password=mosquitto_broker["password"],
        identifier=session_id,
    ) as client:
        table_id = await request_seat_and_wait_assigned(client, session_id)

        await client.subscribe(order_accepted_topic(table_id), qos=1)
        await client.subscribe(food_topic(table_id), qos=1)
        await client.publish(
            order_topic(table_id),
            payload=json.dumps(
                {"food_name": "gyoza", "client_order_id": "order-acc", "session_id": session_id}
            ),
            qos=1,
        )

        accepted = json.loads(
            (await asyncio.wait_for(anext(aiter(client.messages)), timeout=5)).payload
        )
        assert accepted["schema"] == "order.accepted.v1"
        assert accepted["client_order_id"] == "order-acc"
        assert accepted["table_id"] == table_id
        assert backend_task.min_delay_seconds <= accepted["prep_seconds"] <= backend_task.max_delay_seconds

        food = json.loads(
            (await asyncio.wait_for(anext(aiter(client.messages)), timeout=5)).payload
        )
        assert food["prep_seconds"] == accepted["prep_seconds"]


async def test_finished_eating_lingers_until_new_request_evicts(mosquitto_broker, backend_task):
    session_a, session_b = "seat-finish-a", "seat-finish-b"
    async with (
        aiomqtt.Client(
            hostname=mosquitto_broker["host"],
            port=mosquitto_broker["port"],
            username="customer",
            password=mosquitto_broker["password"],
            identifier=session_a,
        ) as client_a,
        aiomqtt.Client(
            hostname=mosquitto_broker["host"],
            port=mosquitto_broker["port"],
            username="customer",
            password=mosquitto_broker["password"],
            identifier=session_b,
        ) as client_b,
    ):
        table_id = await request_seat_and_wait_assigned(client_a, session_a)
        assert table_id == 1

        await client_a.subscribe(seating_status_topic(session_a), qos=1)
        await client_a.publish(seating_finished_topic(session_a), payload=b"{}", qos=1)

        # Nobody's waiting yet, so A keeps the table - no status update at all.
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(anext(aiter(client_a.messages)), timeout=1)

        # B requests a seat; since the only table is finished-eating (not
        # free), B should be seated directly - never queued at all.
        table_id_for_b = await request_seat_and_wait_assigned(client_b, session_b)
        assert table_id_for_b == 1

        a_vacated = json.loads(
            (await asyncio.wait_for(anext(aiter(client_a.messages)), timeout=5)).payload
        )
        assert a_vacated == {"schema": "seat.status.v1", "state": "vacated"}


async def test_order_rejected_for_session_not_seated_at_table(mosquitto_broker, backend_task):
    session_id = "seat-flow-unseated"
    async with aiomqtt.Client(
        hostname=mosquitto_broker["host"],
        port=mosquitto_broker["port"],
        username="customer",
        password=mosquitto_broker["password"],
        identifier=session_id,
    ) as client:
        # Never requests a seat - orders directly, which the broker's
        # broadened ACL now allows to reach the backend, but OrderService
        # must still reject it.
        await client.subscribe(rejected_topic(1), qos=1)
        await client.publish(
            order_topic(1),
            payload=json.dumps(
                {"food_name": "sneaky", "client_order_id": "order-x", "session_id": session_id}
            ),
            qos=1,
        )
        rejection = json.loads(
            (await asyncio.wait_for(anext(aiter(client.messages)), timeout=5)).payload
        )
        assert rejection["reason"] == "not_seated_at_table"
        assert rejection["client_order_id"] == "order-x"


async def test_session_cannot_access_another_sessions_seating_topics(mosquitto_broker):
    session_a, session_b = "seat-acl-a", "seat-acl-b"
    async with (
        aiomqtt.Client(
            hostname=mosquitto_broker["host"],
            port=mosquitto_broker["port"],
            username="customer",
            password=mosquitto_broker["password"],
            identifier=session_a,
        ) as client_a,
        aiomqtt.Client(
            hostname=mosquitto_broker["host"],
            port=mosquitto_broker["port"],
            username="customer",
            password=mosquitto_broker["password"],
            identifier=session_b,
        ) as client_b,
    ):
        # B legitimately subscribes to its own status topic.
        await client_b.subscribe(seating_status_topic(session_b), qos=1)

        # A tries to publish to B's vacate topic - %c-pattern ACL should
        # silently drop this (A's Client Identifier is session_a, not
        # session_b, so it doesn't match the pattern's %c substitution).
        await client_a.publish(seating_vacate_topic(session_b), payload=b"{}", qos=1)

        with pytest.raises(TimeoutError):
            await asyncio.wait_for(anext(aiter(client_b.messages)), timeout=2)


async def test_lwt_triggers_auto_vacate_and_promotes_next_queued(mosquitto_broker):
    settings = Settings(
        broker_host=mosquitto_broker["host"],
        broker_port=mosquitto_broker["port"],
        broker_username="backend",
        broker_password=mosquitto_broker["password"],
        num_tables=1,
        min_delay_seconds=0.1,
        max_delay_seconds=0.2,
    )
    stop_event = asyncio.Event()
    backend = asyncio.create_task(run_forever(settings, stop_event))
    await asyncio.sleep(0.5)

    try:
        session_a, session_b = "lwt-a", "lwt-b"
        will_payload = json.dumps({"schema": "seat.vacate.v1", "reason": "disconnected"}).encode()

        client_a = aiomqtt.Client(
            hostname=mosquitto_broker["host"],
            port=mosquitto_broker["port"],
            username="customer",
            password=mosquitto_broker["password"],
            identifier=session_a,
            will=aiomqtt.Will(
                topic=seating_vacate_topic(session_a), payload=will_payload, qos=1, retain=False
            ),
        )
        # Managed manually (not `async with`) because we deliberately kill
        # the socket below - a normal `async with` exit would then try to
        # send a graceful DISCONNECT over an already-closed socket.
        await client_a.__aenter__()
        try:
            table_id = await request_seat_and_wait_assigned(client_a, session_a)
            assert table_id == 1

            async with aiomqtt.Client(
                hostname=mosquitto_broker["host"],
                port=mosquitto_broker["port"],
                username="customer",
                password=mosquitto_broker["password"],
                identifier=session_b,
            ) as client_b:
                await client_b.subscribe(seating_status_topic(session_b), qos=1)
                await client_b.publish(
                    f"restaurant/seating/{session_b}/request", payload=b"{}", qos=1
                )
                b_status = json.loads(
                    (await asyncio.wait_for(anext(aiter(client_b.messages)), timeout=5)).payload
                )
                assert b_status["state"] == "queued"

                # Drop A's connection uncleanly (not a graceful MQTT
                # DISCONNECT) so the broker fires its Will on A's behalf.
                # Reaching into aiomqtt/paho internals is the only way to
                # simulate a crash rather than a clean disconnect - a plain
                # `async with` exit sends a real DISCONNECT packet, which
                # suppresses the Will per the MQTT spec.
                raw_socket = client_a._client._sock
                assert raw_socket is not None, "client_a should already be connected"
                raw_socket.close()

                b_promoted = json.loads(
                    (await asyncio.wait_for(anext(aiter(client_b.messages)), timeout=10)).payload
                )
                assert b_promoted == {
                    "schema": "seat.status.v1",
                    "state": "assigned",
                    "table_id": 1,
                }
        finally:
            try:
                await client_a.__aexit__(None, None, None)
            except aiomqtt.MqttError:
                pass  # expected - the socket is already dead
    finally:
        stop_event.set()
        await asyncio.wait_for(backend, timeout=5)
