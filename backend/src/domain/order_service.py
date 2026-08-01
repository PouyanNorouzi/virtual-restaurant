"""Pure-asyncio domain core for turning ORDER requests into FOOD events.

No MQTT (or any other transport) is imported here. handle_order() is driven
by MQTT adapters in production and by fakes in unit tests - see
test/unit/test_order_service.py.
"""

import asyncio
import logging
import random
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from .models import FoodReady, OrderRequest
from .ports import FoodPublisher, RejectionPublisher, SeatingQuery

logger = logging.getLogger(__name__)

MAX_FOOD_NAME_LEN_DEFAULT = 200
MAX_PENDING_PER_TABLE_DEFAULT = 5


class OrderValidationError(Exception):
    """Raised by OrderService.validate(); str(exc) is the rejection reason."""


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class Random(Protocol):
    def uniform(self, a: float, b: float) -> float: ...


@dataclass
class _Recent:
    """Small ring-buffer-ish recent-id tracker used for order dedup."""

    max_size: int = 512
    _ids: set[str] | None = None
    _order: list[str] | None = None

    def __post_init__(self) -> None:
        self._ids = set()
        self._order = []

    def seen(self, key: str) -> bool:
        assert self._ids is not None and self._order is not None
        if key in self._ids:
            return True
        self._ids.add(key)
        self._order.append(key)
        if len(self._order) > self.max_size:
            oldest = self._order.pop(0)
            self._ids.discard(oldest)
        return False


class OrderService:
    """Validates orders, simulates a random cook time, and publishes results.

    State is entirely in-memory and process-local.
    """

    def __init__(
        self,
        *,
        num_tables: int,
        min_delay_seconds: float,
        max_delay_seconds: float,
        publisher: FoodPublisher,
        rejection_publisher: RejectionPublisher,
        seating_query: SeatingQuery,
        max_food_name_len: int = MAX_FOOD_NAME_LEN_DEFAULT,
        max_pending_per_table: int = MAX_PENDING_PER_TABLE_DEFAULT,
        clock: Clock | None = None,
        rng: Random | None = None,
    ) -> None:
        if num_tables < 1:
            raise ValueError("num_tables must be >= 1")
        if min_delay_seconds < 0 or max_delay_seconds < min_delay_seconds:
            raise ValueError("require 0 <= min_delay_seconds <= max_delay_seconds")

        self._num_tables = num_tables
        self._min_delay = min_delay_seconds
        self._max_delay = max_delay_seconds
        self._publisher = publisher
        self._rejection_publisher = rejection_publisher
        self._seating_query = seating_query
        self._max_food_name_len = max_food_name_len
        self._max_pending_per_table = max_pending_per_table
        self._clock: Clock = clock or SystemClock()
        self._rng: Random = rng or random.Random()

        self._tasks: dict[str, asyncio.Task] = {}
        self._pending_by_table: dict[int, set[str]] = defaultdict(set)
        self._recent_client_order_ids = _Recent()

    @property
    def pending_count(self) -> int:
        return len(self._tasks)

    def pending_for_table(self, table_id: int) -> int:
        return len(self._pending_by_table.get(table_id, ()))

    def validate(self, table_id: int, food_name: str, session_id: str) -> str:
        """Returns the sanitized food name, or raises OrderValidationError."""
        if not (1 <= table_id <= self._num_tables):
            raise OrderValidationError("unknown_table")

        if not self._seating_query.is_seated(session_id, table_id):
            raise OrderValidationError("not_seated_at_table")

        cleaned = food_name.strip()
        if not cleaned:
            raise OrderValidationError("empty_food_name")
        if len(cleaned) > self._max_food_name_len:
            raise OrderValidationError("food_name_too_long")

        if self.pending_for_table(table_id) >= self._max_pending_per_table:
            raise OrderValidationError("too_many_pending_orders")

        return cleaned

    async def handle_order(self, request: OrderRequest) -> None:
        if self._recent_client_order_ids.seen(request.client_order_id):
            logger.info(
                "duplicate order ignored",
                extra={"event": "duplicate_dropped", "client_order_id": request.client_order_id},
            )
            return

        try:
            clean_name = self.validate(request.table_id, request.food_name, request.session_id)
        except OrderValidationError as exc:
            reason = str(exc)
            logger.warning(
                "order rejected",
                extra={
                    "event": "order_rejected",
                    "table_id": request.table_id,
                    "reason": reason,
                },
            )
            await self._rejection_publisher.publish_rejected(
                request.table_id, request.client_order_id, reason
            )
            return

        order_id = str(uuid.uuid4())
        delay = self._rng.uniform(self._min_delay, self._max_delay)

        logger.info(
            "order received",
            extra={
                "event": "order_received",
                "order_id": order_id,
                "table_id": request.table_id,
                "food_name": clean_name[:64],
            },
        )
        logger.debug(
            "order task scheduled",
            extra={
                "event": "order_scheduled",
                "order_id": order_id,
                "table_id": request.table_id,
                "delay_seconds": delay,
            },
        )

        self._pending_by_table[request.table_id].add(order_id)
        task = asyncio.create_task(
            self._cook(order_id, request, clean_name, delay),
            name=f"cook-order-{order_id}",
        )
        self._tasks[order_id] = task
        task.add_done_callback(
            lambda t, oid=order_id, tid=request.table_id: self._cleanup(t, oid, tid)
        )

    async def _cook(
        self,
        order_id: str,
        request: OrderRequest,
        clean_name: str,
        delay: float,
    ) -> None:
        await asyncio.sleep(delay)
        food = FoodReady(
            order_id=order_id,
            client_order_id=request.client_order_id,
            table_id=request.table_id,
            food_name=clean_name,
            ready_at=self._clock.now(),
            prep_seconds=delay,
        )
        await self._publisher.publish_food(food)
        logger.info(
            "food dispatched",
            extra={
                "event": "food_ready",
                "order_id": order_id,
                "table_id": request.table_id,
                "prep_seconds": delay,
            },
        )

    def _cleanup(self, task: asyncio.Task, order_id: str, table_id: int) -> None:
        self._tasks.pop(order_id, None)
        self._pending_by_table[table_id].discard(order_id)

        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            # Fire-and-forget asyncio.create_task() swallows exceptions
            # unless explicitly inspected here - without this, a bug (e.g. a
            # publish failure) would vanish silently.
            logger.error(
                "unhandled exception in order cook task",
                exc_info=exc,
                extra={"event": "food_publish_failed", "order_id": order_id, "table_id": table_id},
            )

    async def shutdown(self) -> None:
        """Cancels all in-flight cook tasks. Call on graceful shutdown."""
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
