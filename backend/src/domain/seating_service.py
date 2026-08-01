"""Pure-asyncio domain core for seat assignment: request a table, get seated
immediately if one's free or queued FIFO if not, and vacate when done -
freeing the table for whoever has waited longest.

No MQTT (or any other transport) is imported here, same separation as
order_service.py. handle_order() in that file consults this service's
is_seated() (via the SeatingQuery port) to confirm an order is coming from
whoever is actually sitting at that table.
"""

import logging
from collections import deque

from .ports import OccupancyPublisher, SeatingStatusPublisher

logger = logging.getLogger(__name__)


class SeatingService:
    """Tracks which session (if any) occupies each table, plus a FIFO queue
    of sessions waiting for one to free up.

    State is entirely in-memory and process-local, same as OrderService -
    a restart forgets every seating assignment.
    """

    def __init__(
        self,
        *,
        num_tables: int,
        status_publisher: SeatingStatusPublisher,
        occupancy_publisher: OccupancyPublisher,
    ) -> None:
        if num_tables < 1:
            raise ValueError("num_tables must be >= 1")

        self._num_tables = num_tables
        self._status_publisher = status_publisher
        self._occupancy_publisher = occupancy_publisher

        self._table_occupant: dict[int, str] = {}  # table_id -> session_id
        self._session_table: dict[str, int] = {}  # reverse index
        self._queue: deque[str] = deque()
        self._queued_set: set[str] = set()

    @property
    def occupied_count(self) -> int:
        return len(self._table_occupant)

    @property
    def queue_length(self) -> int:
        return len(self._queue)

    def is_seated(self, session_id: str, table_id: int) -> bool:
        return self._session_table.get(session_id) == table_id

    def _first_free_table(self) -> int | None:
        for table_id in range(1, self._num_tables + 1):
            if table_id not in self._table_occupant:
                return table_id
        return None

    def _seat(self, session_id: str, table_id: int) -> None:
        self._table_occupant[table_id] = session_id
        self._session_table[session_id] = table_id

    async def request_seat(self, session_id: str) -> None:
        # Idempotent: a session re-requesting while already seated/queued
        # just gets its current status re-sent (also how a refreshed
        # browser tab resyncs, with no separate "query" topic needed).
        if session_id in self._session_table:
            table_id = self._session_table[session_id]
            logger.info(
                "seat request while already seated",
                extra={"event": "seat_already_assigned", "session_id": session_id, "table_id": table_id},
            )
            await self._status_publisher.publish_assigned(session_id, table_id)
            return

        if session_id in self._queued_set:
            position = list(self._queue).index(session_id) + 1
            logger.info(
                "seat request while already queued",
                extra={"event": "seat_already_queued", "session_id": session_id, "position": position},
            )
            await self._status_publisher.publish_queued(session_id, position)
            return

        table_id = self._first_free_table()
        if table_id is not None:
            self._seat(session_id, table_id)
            logger.info(
                "seat assigned",
                extra={"event": "seat_assigned", "session_id": session_id, "table_id": table_id},
            )
            await self._status_publisher.publish_assigned(session_id, table_id)
        else:
            self._queue.append(session_id)
            self._queued_set.add(session_id)
            position = len(self._queue)
            logger.info(
                "restaurant full, session queued",
                extra={"event": "seat_queued", "session_id": session_id, "position": position},
            )
            await self._status_publisher.publish_queued(session_id, position)

        await self._publish_occupancy()

    async def vacate(self, session_id: str) -> None:
        table_id = self._session_table.pop(session_id, None)
        if table_id is not None:
            del self._table_occupant[table_id]
            logger.info(
                "session vacated table",
                extra={"event": "seat_vacated", "session_id": session_id, "table_id": table_id},
            )
            await self._status_publisher.publish_vacated(session_id)

            if self._queue:
                next_session_id = self._queue.popleft()
                self._queued_set.discard(next_session_id)
                self._seat(next_session_id, table_id)
                logger.info(
                    "queued session promoted to freed table",
                    extra={
                        "event": "seat_promoted",
                        "session_id": next_session_id,
                        "table_id": table_id,
                    },
                )
                await self._status_publisher.publish_assigned(next_session_id, table_id)

            await self._publish_occupancy()
            return

        if session_id in self._queued_set:
            self._queued_set.discard(session_id)
            self._queue.remove(session_id)
            logger.info(
                "queued session canceled its wait",
                extra={"event": "seat_wait_canceled", "session_id": session_id},
            )
            await self._status_publisher.publish_vacated(session_id)
            await self._publish_occupancy()
            return

        # Neither seated nor queued - a no-op, not an error. This is what
        # makes an LWT-triggered vacate safe even after an explicit vacate
        # already ran for the same session (no double-free, no crash).
        logger.info(
            "vacate for session with no seat or queue entry (already handled)",
            extra={"event": "seat_vacate_noop", "session_id": session_id},
        )

    async def _publish_occupancy(self) -> None:
        await self._occupancy_publisher.publish_occupancy(
            occupied_tables=sorted(self._table_occupant),
            num_tables=self._num_tables,
            queue_length=len(self._queue),
        )

    async def shutdown(self) -> None:
        """No-op today - kept for symmetry with OrderService.shutdown(),
        which cancels in-flight cook tasks. SeatingService holds no tasks.
        """
