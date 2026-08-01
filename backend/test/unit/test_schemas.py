from datetime import datetime, timezone

import pytest
from pydantic import ValidationError
from src.domain.schemas import (
    FoodMessageV1,
    OccupancyMessageV1,
    OrderMessageV1,
    SeatRequestMessageV1,
    SeatStatusMessageV1,
    SeatVacateMessageV1,
)

VALID_ORDER_JSON = '{"food_name": "tacos", "client_order_id": "abc", "session_id": "sess-1"}'


def test_order_message_parses_minimal_payload():
    msg = OrderMessageV1.model_validate_json(VALID_ORDER_JSON)
    assert msg.food_name == "tacos"
    assert msg.client_order_id == "abc"
    assert msg.session_id == "sess-1"


def test_order_message_strips_and_trims_food_name():
    msg = OrderMessageV1.model_validate_json(
        '{"food_name": "  tacos  ", "client_order_id": "abc", "session_id": "sess-1"}'
    )
    assert msg.food_name == "tacos"


def test_order_message_strips_and_trims_client_order_id():
    msg = OrderMessageV1.model_validate_json(
        '{"food_name": "tacos", "client_order_id": "  abc  ", "session_id": "sess-1"}'
    )
    assert msg.client_order_id == "abc"


def test_order_message_strips_and_trims_session_id():
    msg = OrderMessageV1.model_validate_json(
        '{"food_name": "tacos", "client_order_id": "abc", "session_id": "  sess-1  "}'
    )
    assert msg.session_id == "sess-1"


def test_order_message_missing_food_name_raises():
    with pytest.raises(ValidationError):
        OrderMessageV1.model_validate_json('{"client_order_id": "abc", "session_id": "sess-1"}')


def test_order_message_missing_client_order_id_raises():
    with pytest.raises(ValidationError):
        OrderMessageV1.model_validate_json('{"food_name": "tacos", "session_id": "sess-1"}')


def test_order_message_missing_session_id_raises():
    with pytest.raises(ValidationError):
        OrderMessageV1.model_validate_json('{"food_name": "tacos", "client_order_id": "abc"}')


def test_order_message_empty_client_order_id_raises():
    with pytest.raises(ValidationError):
        OrderMessageV1.model_validate_json(
            '{"food_name": "tacos", "client_order_id": "   ", "session_id": "sess-1"}'
        )


def test_order_message_empty_session_id_raises():
    with pytest.raises(ValidationError):
        OrderMessageV1.model_validate_json(
            '{"food_name": "tacos", "client_order_id": "abc", "session_id": "   "}'
        )


def test_order_message_oversized_client_order_id_raises():
    with pytest.raises(ValidationError):
        OrderMessageV1.model_validate_json(
            '{"food_name": "tacos", "client_order_id": "%s", "session_id": "sess-1"}'
            % ("x" * 129)
        )


def test_order_message_oversized_session_id_raises():
    with pytest.raises(ValidationError):
        OrderMessageV1.model_validate_json(
            '{"food_name": "tacos", "client_order_id": "abc", "session_id": "%s"}' % ("x" * 129)
        )


def test_order_message_rejects_malformed_json():
    with pytest.raises(ValueError):
        OrderMessageV1.model_validate_json("not json")


def test_food_message_round_trips_with_schema_alias():
    msg = FoodMessageV1(
        order_id="o1",
        client_order_id="c1",
        food_name="ramen",
        table_id=1,
        ready_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        prep_seconds=12.5,
    )
    dumped = msg.model_dump_json(by_alias=True)
    assert '"schema":"food.v1"' in dumped.replace(" ", "")


def test_seat_request_message_defaults_schema_tag():
    msg = SeatRequestMessageV1.model_validate_json("{}")
    dumped = msg.model_dump_json(by_alias=True)
    assert '"schema":"seat.request.v1"' in dumped.replace(" ", "")


def test_seat_vacate_message_reason_is_optional():
    msg = SeatVacateMessageV1.model_validate_json("{}")
    assert msg.reason is None

    msg_with_reason = SeatVacateMessageV1.model_validate_json('{"reason": "disconnected"}')
    assert msg_with_reason.reason == "disconnected"


def test_seat_status_message_round_trips():
    msg = SeatStatusMessageV1(state="assigned", table_id=3)
    dumped = msg.model_dump_json(by_alias=True)
    assert '"schema":"seat.status.v1"' in dumped.replace(" ", "")
    assert '"state":"assigned"' in dumped.replace(" ", "")


def test_occupancy_message_round_trips():
    msg = OccupancyMessageV1(occupied_tables=[1, 3], num_tables=4, queue_length=2)
    dumped = msg.model_dump_json(by_alias=True)
    assert '"schema":"seating.occupancy.v1"' in dumped.replace(" ", "")
    assert '"occupied_tables":[1,3]' in dumped.replace(" ", "")
