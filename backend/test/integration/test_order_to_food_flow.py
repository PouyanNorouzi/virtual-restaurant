"""End-to-end test against a real Mosquitto broker: request a seat, then
publish an ORDER over MQTT and assert the corresponding FOOD event is
delivered on the expected topic within the configured delay window.

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


@pytest.fixture
async def backend_task(mosquitto_broker):
    settings = Settings(
        broker_host=mosquitto_broker["host"],
        broker_port=mosquitto_broker["port"],
        broker_username="backend",
        broker_password=mosquitto_broker["password"],
        num_tables=4,
        min_delay_seconds=0.2,
        max_delay_seconds=0.5,
    )
    stop_event = asyncio.Event()
    task = asyncio.create_task(run_forever(settings, stop_event))
    await asyncio.sleep(0.5)  # let the backend connect + subscribe
    yield settings
    stop_event.set()
    await asyncio.wait_for(task, timeout=5)


async def test_order_produces_matching_food_event(mosquitto_broker, backend_task):
    settings = backend_task
    session_id = "session-order-flow"
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
                {"food_name": "sushi", "client_order_id": "int-test-1", "session_id": session_id}
            ),
            qos=1,
        )

        message = await asyncio.wait_for(anext(aiter(customer.messages)), timeout=5)
        payload = json.loads(message.payload)

        assert payload["food_name"] == "sushi"
        assert payload["table_id"] == table_id
        assert payload["client_order_id"] == "int-test-1"
        assert settings.min_delay_seconds <= payload["prep_seconds"] <= settings.max_delay_seconds
