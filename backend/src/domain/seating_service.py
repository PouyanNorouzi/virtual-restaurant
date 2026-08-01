"""Pure-asyncio domain core for seat assignment: request a table, get seated
immediately if one's free or queued FIFO if not, and vacate when done -
freeing the table for whoever has waited longest.

Finishing a meal (mark_finished_eating) is deliberately NOT the same as
vacating: a finished session keeps its table indefinitely - it's only
evicted once someone else actually needs the table, either because the
queue was already non-empty at the moment they finished, or because a
later request_seat() finds nothing free and reclaims the oldest
finished-but-lingering table instead of enqueueing the new requester.

Nobody is evicted instantly, though: eviction always goes through
_start_eviction(), which warns the session first and only evicts after a
grace period (see the module-level docstring on that method). The same
demand-driven "no timer while nobody's waiting" rule now also applies to a
session that's still ordering/eating (not yet finished) but has been
seated past max_dining_seconds - see _sweep_for_eviction_candidate().

No MQTT (or any other transport) is imported here, same separation as
order_service.py. handle_order() in that file consults this service's
is_seated() (via the SeatingQuery port) to confirm an order is coming from
whoever is actually sitting at that table.
"""

import asyncio
import logging
import time
from collections import deque

from .ports import OccupancyPublisher, SeatingStatusPublisher

logger = logging.getLogger(__name__)

MAX_DINING_SECONDS_DEFAULT = 300.0
EVICTION_WARNING_GRACE_SECONDS_DEFAULT = 20.0
DAWDLE_CHECK_INTERVAL_SECONDS_DEFAULT = 5.0


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
        max_dining_seconds: float = MAX_DINING_SECONDS_DEFAULT,
        eviction_warning_grace_seconds: float = EVICTION_WARNING_GRACE_SECONDS_DEFAULT,
        dawdle_check_interval_seconds: float = DAWDLE_CHECK_INTERVAL_SECONDS_DEFAULT,
    ) -> None:
        if num_tables < 1:
            raise ValueError("num_tables must be >= 1")

        self._num_tables = num_tables
        self._status_publisher = status_publisher
        self._occupancy_publisher = occupancy_publisher
        self._max_dining_seconds = max_dining_seconds
        self._eviction_warning_grace_seconds = eviction_warning_grace_seconds
        self._dawdle_check_interval_seconds = dawdle_check_interval_seconds

        self._table_occupant: dict[int, str] = {}  # table_id -> session_id
        self._session_table: dict[str, int] = {}  # reverse index
        self._seated_at: dict[str, float] = {}  # session_id -> monotonic seat time
        self._queue: deque[str] = deque()
        self._queued_set: set[str] = set()
        self._finished: set[str] = set()  # sessions done eating, still seated
        self._finished_order: deque[str] = deque()  # oldest-finished first
        self._pending_eviction: dict[str, asyncio.Task] = {}  # session_id -> grace-period task
        self._sweep_task: asyncio.Task | None = None

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
        self._seated_at[session_id] = time.monotonic()

    async def _evict(self, table_id: int, session_id: str) -> None:
        """Frees table_id and notifies the evicted session. Does not touch
        the queue or publish occupancy - callers decide what happens to the
        freed table next (promote a queued session, or seat a new
        requester directly) and publish occupancy once that's settled.
        """
        del self._table_occupant[table_id]
        self._session_table.pop(session_id, None)
        self._seated_at.pop(session_id, None)
        self._finished.discard(session_id)
        if session_id in self._finished_order:
            self._finished_order.remove(session_id)
        pending = self._pending_eviction.pop(session_id, None)
        if pending is not None and pending is not asyncio.current_task():
            # Only cancel someone else's grace-period task (e.g. an explicit
            # vacate arriving mid-grace). If _evict() was called BY that
            # task once its own grace period elapsed, cancelling it here
            # would abort the very coroutine that's running this code.
            pending.cancel()
        logger.info(
            "session evicted from table",
            extra={"event": "seat_evicted", "session_id": session_id, "table_id": table_id},
        )
        await self._status_publisher.publish_vacated(session_id)

    async def _promote_queue_head(self, table_id: int) -> None:
        """Seats whoever's been waiting longest into a table that just freed
        up. Callers publish occupancy afterwards - shared by _evict_after_grace,
        mark_finished_eating, and vacate.
        """
        if not self._queue:
            return
        next_session_id = self._queue.popleft()
        self._queued_set.discard(next_session_id)
        self._seat(next_session_id, table_id)
        logger.info(
            "queued session promoted to freed table",
            extra={"event": "seat_promoted", "session_id": next_session_id, "table_id": table_id},
        )
        await self._status_publisher.publish_assigned(next_session_id, table_id)

    def _start_eviction(self, table_id: int, session_id: str) -> None:
        """Warns session_id that it's about to lose its table, then evicts
        it after eviction_warning_grace_seconds - never instantly. A warning
        is a commitment: once sent, the session is evicted after the grace
        period regardless of small queue fluctuations in between, unless it
        vacates (or gets evicted some other way) first.
        """
        if session_id in self._pending_eviction:
            return  # already warned, grace period already running

        task = asyncio.create_task(
            self._evict_after_grace(table_id, session_id),
            name=f"evict-{session_id}",
        )
        self._pending_eviction[session_id] = task

    async def _evict_after_grace(self, table_id: int, session_id: str) -> None:
        grace_seconds = self._eviction_warning_grace_seconds
        logger.info(
            "warning session of upcoming eviction",
            extra={
                "event": "seat_eviction_warned",
                "session_id": session_id,
                "table_id": table_id,
                "grace_seconds": grace_seconds,
            },
        )
        await self._status_publisher.publish_warning(session_id, grace_seconds)

        await asyncio.sleep(grace_seconds)

        # The session may have vacated, finished-and-been-evicted some other
        # way, or otherwise moved on during the grace period - only evict if
        # it's still exactly where it was when warned.
        if self._session_table.get(session_id) != table_id:
            return

        await self._evict(table_id, session_id)
        await self._promote_queue_head(table_id)
        await self._publish_occupancy()

    def _sweep_for_eviction_candidate(self) -> tuple[int, str] | None:
        """Finds the best (table_id, session_id) to warn-then-evict right
        now, or None if nobody's waiting or nobody's overdue yet. Only
        called when self._queue is non-empty - an empty room never evicts
        anyone, finished or not (see module docstring).
        """
        for session_id in self._finished_order:
            if session_id not in self._pending_eviction:
                return self._session_table[session_id], session_id

        now = time.monotonic()
        oldest: tuple[int, str] | None = None
        oldest_seated_at = float("inf")
        for session_id, seated_at in self._seated_at.items():
            if session_id in self._finished or session_id in self._pending_eviction:
                continue
            if now - seated_at < self._max_dining_seconds:
                continue
            if seated_at < oldest_seated_at:
                oldest_seated_at = seated_at
                oldest = (self._session_table[session_id], session_id)
        return oldest

    def _check_for_eviction_candidate(self) -> None:
        if not self._queue:
            return
        if len(self._pending_eviction) >= len(self._queue):
            # Already warning as many sessions as there are parties waiting
            # - no need to line up another one yet.
            return
        candidate = self._sweep_for_eviction_candidate()
        if candidate is not None:
            self._start_eviction(*candidate)

    async def _sweep_loop(self) -> None:
        while True:
            await asyncio.sleep(self._dawdle_check_interval_seconds)
            self._check_for_eviction_candidate()

    def start(self) -> None:
        """Starts the periodic dawdle sweep - call once after construction,
        from within a running event loop.
        """
        if self._sweep_task is None:
            self._sweep_task = asyncio.create_task(self._sweep_loop(), name="seating-dawdle-sweep")

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
            # Nothing free for this new requester - if someone's overdue
            # (finished-and-lingering, or dawdling past max_dining_seconds),
            # warn them now rather than waiting for the next sweep tick.
            self._check_for_eviction_candidate()

        await self._publish_occupancy()

    async def mark_finished_eating(self, session_id: str) -> None:
        """Records that session_id is done eating. Their table is NOT
        freed unless someone is already waiting right now - otherwise they
        keep it until demand shows up (see _sweep_for_eviction_candidate).
        Even when someone is already waiting, eviction goes through the
        warn-then-grace path, same as everyone else - see _start_eviction.
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
                "eviction_pending": bool(self._queue),
            },
        )

        self._check_for_eviction_candidate()

    async def vacate(self, session_id: str) -> None:
        table_id = self._session_table.get(session_id)
        if table_id is not None:
            await self._evict(table_id, session_id)
            await self._promote_queue_head(table_id)
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
        """Cancels the dawdle sweep and any in-flight eviction grace-period
        tasks. Call on graceful shutdown, mirroring OrderService.shutdown().
        """
        if self._sweep_task is not None:
            self._sweep_task.cancel()

        tasks = list(self._pending_eviction.values())
        for task in tasks:
            task.cancel()

        all_tasks = ([self._sweep_task] if self._sweep_task is not None else []) + tasks
        if all_tasks:
            await asyncio.gather(*all_tasks, return_exceptions=True)
