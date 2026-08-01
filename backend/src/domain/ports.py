"""Ports the domain layer depends on, implemented by MQTT adapters in tests
and in production. Keeping these as Protocols (rather than importing aiomqtt
here) is what lets OrderService be unit tested with no broker involved.
"""

from typing import Protocol

from .models import FoodReady, OrderAccepted


class FoodPublisher(Protocol):
    async def publish_accepted(self, accepted: OrderAccepted) -> None: ...
    async def publish_food(self, food: FoodReady) -> None: ...


class RejectionPublisher(Protocol):
    async def publish_rejected(
        self, table_id: int, client_order_id: str, reason: str
    ) -> None: ...


class SeatingQuery(Protocol):
    """Read-only surface OrderService depends on, implemented by SeatingService.

    One-directional: OrderService depends on this port, SeatingService
    implements it, so the two services never import each other.
    """

    def is_seated(self, session_id: str, table_id: int) -> bool: ...


class SeatingStatusPublisher(Protocol):
    async def publish_assigned(self, session_id: str, table_id: int) -> None: ...
    async def publish_queued(self, session_id: str, queue_position: int) -> None: ...
    async def publish_vacated(self, session_id: str) -> None: ...


class OccupancyPublisher(Protocol):
    async def publish_occupancy(
        self, occupied_tables: list[int], num_tables: int, queue_length: int
    ) -> None: ...
