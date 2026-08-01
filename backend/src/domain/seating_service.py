"""Pure-asyncio domain core for seat assignment: request a table, get seated
immediately if one's free or queued FIFO if not, and vacate when done -
freeing the table for whoever has waited longest.

Finishing a meal (mark_finished_eating) is deliberately NOT the same as
vacating: a finished session keeps its table indefinitely - it's only
evicted once someone else actually needs the table, either because the
queue was already non-empty at the moment they finished, or because a
later request_seat() finds nothing free and reclaims the oldest
finished-but-lingering table instead of enqueueing the new requester.

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
    """Tracks which session (if any) occupies each table, a FIFO queue of
    sessions waiting for one to free up, and which occupied tables belong
    to sessions that have finished eating but not yet been evicted.

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
        self._finished: set[str] = set()  # sessions done eating, still seated
        self._finished_order: deque[str] = deque()  # oldest-finished first

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

    async def _evict(self, table_id: int, session_id: str) -> None:
        """Frees table_id and notifies the evicted session. Does not touch
        the queue or publish occupancy - callers decide what happens to the
        freed table next (promote a queued session, or seat a new
        requester directly) and publish occupancy once that's settled.
        """
        del self._table_occupant[table_id]
        self._session_table.pop(session_id, None)
        self._finished.discard(session_id)
        if session_id in self._finished_order:
            self._finished_order.remove(session_id)
        logger.info(
            "session evicted from table",
            extra={"event": "seat_evicted", "session_id": session_id, "table_id": table_id},
        )
        await self._status_publisher.publish_vacated(session_id)

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
        if table_id is None and self._finished_order:
            # Nothing free, but someone's done eating and just lingering -
            # reclaim their table for this new requester instead of making
            # them wait behind a table that's sitting idle.
            evicted_session_id = self._finished_order[0]
            table_id = self._session_table[evicted_session_id]
            await self._evict(table_id, evicted_session_id)

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

    async def mark_finished_eating(self, session_id: str) -> None:
        """Records that session_id is done eating. Their table is NOT
        freed unless someone is already waiting right now - otherwise they
        keep it until a later request_seat() needs to reclaim it (see
        request_seat's finished-table fallback above).
        """
        table_id = self._session_table.get(session_id)
        if table_id is None:
            logger.info(
                "finished-eating signal from session with no table (ignored)",
                extra={"event": "seat_finished_noop", "session_id": session_id},
            )
            return

        if session_id in self._finished:
            logger.info(
                "duplicate finished-eating signal (ignored)",
                extra={"event": "seat_finished_noop", "session_id": session_id},
            )
            return

        self._finished.add(session_id)
        self._finished_order.append(session_id)
        logger.info(
            "session finished eating",
            extra={
                "event": "seat_finished",
                "session_id": session_id,
                "table_id": table_id,
                "evicted_immediately": bool(self._queue),
            },
        )

        if not self._queue:
            return  # nobody waiting - they can keep sitting there

        await self._evict(table_id, session_id)
        next_session_id = self._queue.popleft()
        self._queued_set.discard(next_session_id)
        self._seat(next_session_id, table_id)
        logger.info(
            "queued session promoted after eviction",
            extra={"event": "seat_promoted", "session_id": next_session_id, "table_id": table_id},
        )
        await self._status_publisher.publish_assigned(next_session_id, table_id)
        await self._publish_occupancy()

    async def vacate(self, session_id: str) -> None:
        table_id = self._session_table.get(session_id)
        if table_id is not None:
            await self._evict(table_id, session_id)

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
