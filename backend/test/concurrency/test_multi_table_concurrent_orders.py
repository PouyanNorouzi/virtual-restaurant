"""Seats several sessions concurrently (each landing on a distinct real
table via SeatingService, no more static per-table credentials), then fires
near-simultaneous orders - including two concurrent orders from the same
seated session - and asserts every order gets exactly one correctly-matched
food event.

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


async def test_concurrent_orders_across_all_tables_each_get_matching_food(mosquitto_broker):
    settings = Settings(
        broker_host=mosquitto_broker["host"],
        broker_port=mosquitto_broker["port"],
        broker_username="backend",
        broker_password=mosquitto_broker["password"],
        num_tables=4,
        min_delay_seconds=0.2,
        max_delay_seconds=0.6,
        max_pending_per_table=10,
    )
    stop_event = asyncio.Event()
    backend = asyncio.create_task(run_forever(settings, stop_event))
    await asyncio.sleep(0.5)

    try:
        session_ids = [f"session-{i}" for i in range(4)]
        received: dict[str, dict] = {}

        async def seat_and_order_twice(session_id: str) -> None:
            async with aiomqtt.Client(
                hostname=mosquitto_broker["host"],
                port=mosquitto_broker["port"],
                username="customer",
                password=mosquitto_broker["password"],
                identifier=session_id,
            ) as client:
                table_id = await request_seat_and_wait_assigned(client, session_id)
                await client.subscribe(food_topic(table_id), qos=1)

                # Two concurrent orders from the same seated session, to
                # exercise same-table concurrency alongside cross-table
                # concurrency (a table now holds exactly one session, so
                # "two customers at one table" becomes "one session ordering
                # twice" - the equivalent concurrency shape).
                for suffix in ("a", "b"):
                    client_order_id = f"{session_id}-{suffix}"
                    await client.publish(
                        order_topic(table_id),
                        payload=json.dumps(
                            {
                                "food_name": f"food-{client_order_id}",
                                "client_order_id": client_order_id,
                                "session_id": session_id,
                            }
                        ),
                        qos=1,
                    )

                for _ in range(2):
                    message = await asyncio.wait_for(anext(aiter(client.messages)), timeout=5)
                    payload = json.loads(message.payload)
                    assert payload["table_id"] == table_id
                    assert payload["food_name"] == f"food-{payload['client_order_id']}"
                    received[payload["client_order_id"]] = payload

        await asyncio.gather(*(seat_and_order_twice(sid) for sid in session_ids))

        expected_ids = {f"{sid}-{suffix}" for sid in session_ids for suffix in ("a", "b")}
        assert set(received) == expected_ids
        # No two sessions were double-booked onto the same table.
        assert len({payload["table_id"] for payload in received.values()}) == 4
    finally:
        stop_event.set()
        await asyncio.wait_for(backend, timeout=5)
