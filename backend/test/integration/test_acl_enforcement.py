"""Verifies the Mosquitto ACL model matches what's documented in
backend/README.md's Security section:

- Any "customer"-role client may publish to any table's order topic -
  which table a session may use isn't known until runtime seat assignment,
  so per-table authorization is enforced by OrderService's
  not_seated_at_table check (see test_seating_flow.py) rather than by the
  broker.
- The broker prevents a customer from forging FOOD/rejection events.
- The %c-pattern ACL isolates each session's own seating topics from every
  other session - see test_seating_flow.py for that test.

Note: MQTT v3.1.1 (what Mosquitto/aiomqtt use here) has no protocol-level
error code for an ACL-denied PUBLISH - the broker silently drops the message
rather than rejecting the PUBACK. So these tests assert on absence/presence
of delivery to a legitimate subscriber, not on a client-side exception.

Requires docker. Run with: pytest -m integration
"""

import asyncio

import aiomqtt
import pytest

from src.mqtt.topics import food_topic, order_topic

pytestmark = pytest.mark.integration


async def test_customer_can_publish_orders_for_any_table(mosquitto_broker):
    """The broker doesn't restrict which table a customer client may
    publish an order to - per-table correctness lives in OrderService
    instead (see test_seating_flow.py's not_seated_at_table test).
    """
    async with (
        aiomqtt.Client(
            hostname=mosquitto_broker["host"],
            port=mosquitto_broker["port"],
            username="backend",
            password=mosquitto_broker["password"],
        ) as backend_observer,
        aiomqtt.Client(
            hostname=mosquitto_broker["host"],
            port=mosquitto_broker["port"],
            username="customer",
            password=mosquitto_broker["password"],
        ) as customer_client,
    ):
        await backend_observer.subscribe(order_topic(2), qos=1)

        await customer_client.publish(
            order_topic(2), payload=b'{"food_name": "any table is fine now"}', qos=1
        )

        message = await asyncio.wait_for(anext(aiter(backend_observer.messages)), timeout=5)
        assert message.topic.value == order_topic(2)


async def test_customer_cannot_publish_fake_food_events(mosquitto_broker):
    async with (
        aiomqtt.Client(
            hostname=mosquitto_broker["host"],
            port=mosquitto_broker["port"],
            username="customer",
            password=mosquitto_broker["password"],
        ) as observer,
        aiomqtt.Client(
            hostname=mosquitto_broker["host"],
            port=mosquitto_broker["port"],
            username="customer",
            password=mosquitto_broker["password"],
        ) as attacker,
    ):
        await observer.subscribe(food_topic(1), qos=1)

        # "customer" is only ACL'd to *read* food topics, never publish.
        await attacker.publish(food_topic(1), payload=b"{}", qos=1)

        with pytest.raises(TimeoutError):
            await asyncio.wait_for(anext(aiter(observer.messages)), timeout=2)
