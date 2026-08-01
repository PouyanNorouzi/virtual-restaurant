"""Pydantic models for the MQTT wire payloads (JSON in, JSON out).

These are deliberately kept separate from the domain dataclasses in
models.py: schemas.py knows about the wire format (field names, optional
fields, a "schema" version tag); models.py knows nothing about JSON or MQTT
at all. The MQTT adapter is responsible for translating between the two.
"""

from datetime import datetime, timezone

from pydantic import BaseModel, Field, field_validator

CLIENT_ORDER_ID_MAX_LEN = 128
SESSION_ID_MAX_LEN = 128


def _validate_id_field(value: str, error_prefix: str, max_len: int) -> str:
    """Shared shape for every "id" field we accept over the wire: non-empty
    after trimming, capped in length. Used by client_order_id and session_id.
    """
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{error_prefix} must be a non-empty string")
    if len(cleaned) > max_len:
        raise ValueError(f"{error_prefix}_too_long")
    return cleaned


class OrderMessageV1(BaseModel):
    schema_: str = Field(default="order.v1", alias="schema")
    food_name: str
    # Mandatory: it's what lets the backend dedup redelivered QoS-1 messages
    # and lets the frontend unambiguously match a FOOD event back to the
    # order it fired, even when the same table places two identical-looking
    # orders back to back. Cheap for a client to generate (crypto.randomUUID()
    # / uuid4()), so there's no good reason to make it optional.
    client_order_id: str
    # Identifies which seated customer placed the order - OrderService
    # rejects an order if this session isn't currently seated at the table
    # the order was published to (see SeatingQuery). Also the customer's
    # MQTT Client Identifier, by protocol convention - see seating topics.
    session_id: str
    submitted_at: datetime | None = None

    model_config = {"populate_by_name": True}

    @field_validator("food_name")
    @classmethod
    def strip_food_name(cls, value: str) -> str:
        # Control characters are stripped defensively even though food_name
        # is never used in a shell/SQL/HTML execution path in this backend -
        # it is only ever logged and re-serialized as JSON. The frontend is
        # still responsible for HTML-escaping it when rendering.
        cleaned = "".join(ch for ch in value if ch.isprintable() or ch.isspace())
        return cleaned.strip()

    @field_validator("client_order_id")
    @classmethod
    def validate_client_order_id(cls, value: str) -> str:
        return _validate_id_field(value, "client_order_id", CLIENT_ORDER_ID_MAX_LEN)

    @field_validator("session_id")
    @classmethod
    def validate_session_id(cls, value: str) -> str:
        return _validate_id_field(value, "session_id", SESSION_ID_MAX_LEN)


class FoodMessageV1(BaseModel):
    schema_: str = Field(default="food.v1", alias="schema")
    order_id: str
    client_order_id: str
    food_name: str
    table_id: int
    ready_at: datetime
    prep_seconds: float

    model_config = {"populate_by_name": True}


class RejectionMessageV1(BaseModel):
    schema_: str = Field(default="order.rejected.v1", alias="schema")
    client_order_id: str
    reason: str

    model_config = {"populate_by_name": True}


class SeatRequestMessageV1(BaseModel):
    """Published to restaurant/seating/{session_id}/request. No fields of
    its own - session_id lives in the topic, mirroring table_id-in-topic
    for orders. Still schema-validated (rather than treated as opaque) so a
    malformed request is dropped/logged the same way a malformed order is.
    """

    schema_: str = Field(default="seat.request.v1", alias="schema")

    model_config = {"populate_by_name": True}


class SeatVacateMessageV1(BaseModel):
    """Published to restaurant/seating/{session_id}/vacate - either by an
    explicit "Leave" action, or by the broker on the client's behalf via
    MQTT Last Will and Testament if its connection drops uncleanly.
    """

    schema_: str = Field(default="seat.vacate.v1", alias="schema")
    # Log-only context (which of the two above triggered it); never
    # branched on, since SeatingService.vacate() treats both identically.
    reason: str | None = None

    model_config = {"populate_by_name": True}


class SeatStatusMessageV1(BaseModel):
    """Published by the backend to restaurant/seating/{session_id}/status."""

    schema_: str = Field(default="seat.status.v1", alias="schema")
    state: str  # "assigned" | "queued" | "vacated"
    table_id: int | None = None
    queue_position: int | None = None

    model_config = {"populate_by_name": True}


class OccupancyMessageV1(BaseModel):
    """Published by the backend to restaurant/seating/occupancy (retained)."""

    schema_: str = Field(default="seating.occupancy.v1", alias="schema")
    occupied_tables: list[int]
    num_tables: int
    queue_length: int

    model_config = {"populate_by_name": True}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
