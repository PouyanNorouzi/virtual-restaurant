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

from .conftest import request_seat_and_wait_assigned

pytestmark = pytest.mark.integration


async def test_backend_recovers_after_broker_restart(mosquitto_broker):
    settings = Settings(
        broker_host=mosquitto_broker["host"],
        broker_port=mosquitto_broker["port"],
        broker_username="backend",
        broker_password=mosquitto_broker["password"],
        min_delay_seconds=0.1,
        max_delay_seconds=0.2,
        reconnect_initial_backoff_seconds=0.2,
        reconnect_max_backoff_seconds=1.0,
    )
    stop_event = asyncio.Event()
    task = asyncio.create_task(run_forever(settings, stop_event))
    await asyncio.sleep(0.5)

    mosquitto_broker["container"].get_wrapped_container().restart()
    await asyncio.sleep(2.0)  # give the backend's backoff loop time to reconnect

    try:
        session_id = "session-after-restart"
        async with aiomqtt.Client(
            hostname=mosquitto_broker["host"],
            port=mosquitto_broker["port"],
            username="customer",
            password=mosquitto_broker["password"],
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
