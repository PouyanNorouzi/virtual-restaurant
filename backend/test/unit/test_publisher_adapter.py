"""Unit tests for the MQTT publisher adapters, against a fake aiomqtt
client - no broker needed. This is the layer that translates domain
dataclasses -> wire JSON, and it's easy for a bug here (e.g. forgetting to
exclude unset optional fields) to slip past domain/schema-only tests while
only surfacing when something actually inspects the payload over a real
broker - see MqttSeatingStatusPublisher's exclude_none fix, which the
integration suite caught and this file now guards against directly.
"""

import json
from datetime import datetime, timezone

import aiomqtt
from src.domain.models import FoodReady, OrderAccepted
from src.mqtt.publisher_adapter import (
    MqttFoodPublisher,
    MqttOccupancyPublisher,
    MqttRejectionPublisher,
    MqttSeatingStatusPublisher,
)


class FakeAiomqttClient:
    def __init__(self, *, raise_on_publish: bool = False) -> None:
        self.published: list[tuple[str, str, int, bool]] = []
        self._raise_on_publish = raise_on_publish

    async def publish(self, topic: str, payload: str, qos: int, retain: bool) -> None:
        if self._raise_on_publish:
            raise aiomqtt.MqttError("simulated publish failure")
        self.published.append((topic, payload, qos, retain))


async def test_food_publisher_publish_accepted_shape():
    client = FakeAiomqttClient()
    publisher = MqttFoodPublisher(client)  # type: ignore[arg-type]
    await publisher.publish_accepted(
        OrderAccepted(client_order_id="abc", table_id=2, prep_seconds=17.5)
    )

    topic, payload, qos, retain = client.published[0]
    assert topic == "restaurant/table/2/order/accepted"
    assert json.loads(payload) == {
        "schema": "order.accepted.v1",
        "client_order_id": "abc",
        "table_id": 2,
        "prep_seconds": 17.5,
    }
    assert qos == 1
    assert retain is False


async def test_food_publisher_publish_food_shape():
    client = FakeAiomqttClient()
    publisher = MqttFoodPublisher(client)  # type: ignore[arg-type]
    await publisher.publish_food(
        FoodReady(
            order_id="o1",
            client_order_id="abc",
            table_id=2,
            food_name="ramen",
            ready_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            prep_seconds=17.5,
        )
    )

    topic, payload, _qos, retain = client.published[0]
    assert topic == "restaurant/table/2/food"
    parsed = json.loads(payload)
    assert parsed["schema"] == "food.v1"
    assert parsed["food_name"] == "ramen"
    assert retain is False


async def test_food_publisher_swallows_publish_errors():
    client = FakeAiomqttClient(raise_on_publish=True)
    publisher = MqttFoodPublisher(client)  # type: ignore[arg-type]
    # Should not raise - a failed publish is logged, not propagated.
    await publisher.publish_accepted(
        OrderAccepted(client_order_id="abc", table_id=1, prep_seconds=1.0)
    )
    assert not client.published


async def test_rejection_publisher_shape():
    client = FakeAiomqttClient()
    await MqttRejectionPublisher(client).publish_rejected(  # type: ignore[arg-type]
        3, "abc", "empty_food_name"
    )

    topic, payload, _qos, retain = client.published[0]
    assert topic == "restaurant/table/3/order/rejected"
    assert json.loads(payload) == {
        "schema": "order.rejected.v1",
        "client_order_id": "abc",
        "reason": "empty_food_name",
    }
    assert retain is False


async def test_seating_status_publisher_assigned_omits_queue_position():
    client = FakeAiomqttClient()
    await MqttSeatingStatusPublisher(client).publish_assigned(  # type: ignore[arg-type]
        "sess-1", 2
    )

    topic, payload, _qos, retain = client.published[0]
    assert topic == "restaurant/seating/sess-1/status"
    # Regression guard: table_id/queue_position are optional fields on
    # SeatStatusMessageV1 - without exclude_none this payload would also
    # carry "queue_position": null, which doesn't match README.md's
    # documented contract and broke exact-dict assertions in the
    # integration suite.
    assert json.loads(payload) == {
        "schema": "seat.status.v1",
        "state": "assigned",
        "table_id": 2,
    }
    assert retain is False


async def test_seating_status_publisher_queued_omits_table_id():
    client = FakeAiomqttClient()
    await MqttSeatingStatusPublisher(client).publish_queued(  # type: ignore[arg-type]
        "sess-1", 4
    )

    _topic, payload, _qos, _retain = client.published[0]
    assert json.loads(payload) == {
        "schema": "seat.status.v1",
        "state": "queued",
        "queue_position": 4,
    }


async def test_seating_status_publisher_vacated_omits_both():
    client = FakeAiomqttClient()
    publisher = MqttSeatingStatusPublisher(client)  # type: ignore[arg-type]
    await publisher.publish_vacated("sess-1")

    _topic, payload, _qos, _retain = client.published[0]
    assert json.loads(payload) == {"schema": "seat.status.v1", "state": "vacated"}


async def test_occupancy_publisher_is_retained():
    client = FakeAiomqttClient()
    await MqttOccupancyPublisher(client).publish_occupancy(  # type: ignore[arg-type]
        occupied_tables=[1, 3], num_tables=4, queue_length=2
    )

    topic, payload, _qos, retain = client.published[0]
    assert topic == "restaurant/seating/occupancy"
    assert json.loads(payload) == {
        "schema": "seating.occupancy.v1",
        "occupied_tables": [1, 3],
        "num_tables": 4,
        "queue_length": 2,
    }
    # The one intentionally-retained topic in the whole system.
    assert retain is True
