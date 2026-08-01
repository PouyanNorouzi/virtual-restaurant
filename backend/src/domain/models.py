"""Transport-agnostic domain models for the order/food lifecycle."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class OrderRequest:
    """A validated request to cook a food item for a table."""

    table_id: int
    food_name: str
    client_order_id: str
    session_id: str
    submitted_at: datetime


@dataclass(frozen=True)
class FoodReady:
    """Emitted once an order has finished "cooking"."""

    order_id: str
    client_order_id: str
    table_id: int
    food_name: str
    ready_at: datetime
    prep_seconds: float
