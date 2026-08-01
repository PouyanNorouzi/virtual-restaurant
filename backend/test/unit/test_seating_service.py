import asyncio

import pytest

from src.domain.seating_service import SeatingService


class FakeSeatingStatusPublisher:
    def __init__(self) -> None:
        self.assigned: list[tuple[str, int]] = []
        self.queued: list[tuple[str, int]] = []
        self.vacated: list[str] = []
        self.warned: list[str] = []

    async def publish_assigned(self, session_id: str, table_id: int) -> None:
        self.assigned.append((session_id, table_id))

    async def publish_queued(self, session_id: str, queue_position: int) -> None:
        self.queued.append((session_id, queue_position))

    async def publish_vacated(self, session_id: str) -> None:
        self.vacated.append(session_id)

    async def publish_warning(self, session_id: str, grace_seconds: float) -> None:
        self.warned.append(session_id)


class FakeOccupancyPublisher:
    def __init__(self) -> None:
        self.snapshots: list[tuple[list[int], int, int]] = []

    async def publish_occupancy(
        self, occupied_tables: list[int], num_tables: int, queue_length: int
    ) -> None:
        self.snapshots.append((list(occupied_tables), num_tables, queue_length))


# Near-zero so tests can just yield control back to the loop (a bare
# `await asyncio.sleep(0)`) to let a scheduled eviction run, instead of
# actually waiting out a real grace period.
TEST_GRACE_SECONDS = 0
TEST_MAX_DINING_SECONDS = 10_000  # effectively "never" unless a test overrides it


def make_service(
    num_tables: int = 2,
    *,
    max_dining_seconds: float = TEST_MAX_DINING_SECONDS,
    eviction_warning_grace_seconds: float = TEST_GRACE_SECONDS,
) -> tuple[SeatingService, FakeSeatingStatusPublisher, FakeOccupancyPublisher]:
    status = FakeSeatingStatusPublisher()
    occupancy = FakeOccupancyPublisher()
    service = SeatingService(
        num_tables=num_tables,
        status_publisher=status,
        occupancy_publisher=occupancy,
        max_dining_seconds=max_dining_seconds,
        eviction_warning_grace_seconds=eviction_warning_grace_seconds,
    )
    return service, status, occupancy


async def let_pending_evictions_run() -> None:
    """Yields control back to the event loop so a _start_eviction task
    (warning published synchronously, then a TEST_GRACE_SECONDS sleep) gets
    to actually run and complete.
    """
    await asyncio.sleep(0)
    await asyncio.sleep(0)


async def test_first_request_assigns_first_free_table_by_index():
    service, status, occupancy = make_service(num_tables=2)

    await service.request_seat("s1")

    assert status.assigned == [("s1", 1)]
    assert not status.queued
    assert service.is_seated("s1", 1)
    assert occupancy.snapshots[-1] == ([1], 2, 0)


async def test_requests_fill_tables_in_index_order_then_queue():
    service, status, _ = make_service(num_tables=2)

    await service.request_seat("s1")
    await service.request_seat("s2")
    await service.request_seat("s3")

    assert status.assigned == [("s1", 1), ("s2", 2)]
    assert status.queued == [("s3", 1)]
    assert service.queue_length == 1


async def test_multiple_queued_sessions_get_increasing_positions():
    service, status, _ = make_service(num_tables=1)

    await service.request_seat("s1")
    await service.request_seat("s2")
    await service.request_seat("s3")

    assert status.queued == [("s2", 1), ("s3", 2)]


async def test_request_seat_is_idempotent_while_seated():
    service, status, _ = make_service(num_tables=2)
    await service.request_seat("s1")

    await service.request_seat("s1")

    assert status.assigned == [("s1", 1), ("s1", 1)]
    assert service.occupied_count == 1  # no double-booking


async def test_request_seat_is_idempotent_while_queued():
    service, status, _ = make_service(num_tables=1)
    await service.request_seat("s1")
    await service.request_seat("s2")  # queued at position 1

    await service.request_seat("s2")

    assert status.queued == [("s2", 1), ("s2", 1)]
    assert service.queue_length == 1  # not enqueued twice


async def test_vacate_frees_table_and_promotes_queue_head():
    service, status, _ = make_service(num_tables=1)
    await service.request_seat("s1")
    await service.request_seat("s2")  # queued

    await service.vacate("s1")

    assert status.vacated == ["s1"]
    assert ("s2", 1) in status.assigned
    assert service.is_seated("s2", 1)
    assert not service.is_seated("s1", 1)
    assert service.queue_length == 0


async def test_vacate_while_only_queued_cancels_the_wait():
    service, status, _ = make_service(num_tables=1)
    await service.request_seat("s1")
    await service.request_seat("s2")  # queued

    await service.vacate("s2")

    assert status.vacated == ["s2"]
    assert service.queue_length == 0
    assert service.is_seated("s1", 1)  # table 1 untouched


async def test_vacate_when_neither_seated_nor_queued_is_a_noop():
    service, status, _ = make_service(num_tables=1)

    await service.vacate("ghost")  # never requested a seat at all

    assert not status.vacated
    assert service.occupied_count == 0
    assert service.queue_length == 0


async def test_double_vacate_is_safe_lwt_and_explicit_race():
    service, status, _ = make_service(num_tables=1)
    await service.request_seat("s1")

    await service.vacate("s1")  # e.g. explicit "Leave" click
    await service.vacate("s1")  # e.g. LWT firing moments later

    assert status.vacated == ["s1"]  # only published once


async def test_is_seated_false_for_wrong_table_or_unknown_session():
    service, _, _ = make_service(num_tables=2)
    await service.request_seat("s1")

    assert service.is_seated("s1", 1)
    assert not service.is_seated("s1", 2)
    assert not service.is_seated("unknown", 1)


async def test_occupancy_reflects_state_after_each_mutation():
    service, _, occupancy = make_service(num_tables=2)

    await service.request_seat("s1")
    await service.request_seat("s2")
    await service.request_seat("s3")  # full, queued

    assert occupancy.snapshots[-1] == ([1, 2], 2, 1)

    await service.vacate("s1")

    assert occupancy.snapshots[-1] == ([1, 2], 2, 0)  # s3 promoted into table 1


async def test_concurrent_requests_assign_each_table_exactly_once():
    service, status, _ = make_service(num_tables=4)
    session_ids = [f"s{i}" for i in range(10)]

    await asyncio.gather(*(service.request_seat(sid) for sid in session_ids))

    assigned_tables = [table_id for _, table_id in status.assigned]
    assert sorted(assigned_tables) == [1, 2, 3, 4]
    assert len(set(assigned_tables)) == 4  # no table double-booked
    assert service.queue_length == 6


async def test_finished_eating_with_empty_queue_keeps_the_table():
    service, status, occupancy = make_service(num_tables=1)
    await service.request_seat("s1")
    occupancy.snapshots.clear()

    await service.mark_finished_eating("s1")

    assert not status.vacated  # not evicted - nobody's waiting
    assert service.is_seated("s1", 1)
    assert not occupancy.snapshots  # nothing about occupancy changed


async def test_finished_eating_is_noop_if_not_seated():
    service, status, _ = make_service(num_tables=1)

    await service.mark_finished_eating("ghost")

    assert not status.vacated


async def test_finished_eating_is_idempotent():
    service, status, _ = make_service(num_tables=1)
    await service.request_seat("s1")

    await service.mark_finished_eating("s1")
    await service.mark_finished_eating("s1")  # duplicate signal, e.g. redelivered

    assert not status.vacated
    assert service.is_seated("s1", 1)


async def test_finished_eating_with_queue_already_waiting_warns_then_evicts():
    service, status, _ = make_service(num_tables=1)
    await service.request_seat("s1")
    await service.request_seat("s2")  # queued, table is full

    await service.mark_finished_eating("s1")
    await asyncio.sleep(0)  # let the scheduled warning task publish its warning

    # Not instant - s1 is warned first and still holds the table.
    assert status.warned == ["s1"]
    assert not status.vacated
    assert service.is_seated("s1", 1)

    await let_pending_evictions_run()

    assert status.vacated == ["s1"]
    assert not service.is_seated("s1", 1)
    assert service.is_seated("s2", 1)
    assert service.queue_length == 0


async def test_new_request_eventually_reclaims_finished_table_instead_of_waiting_forever():
    service, status, _ = make_service(num_tables=1)
    await service.request_seat("s1")
    await service.mark_finished_eating("s1")  # queue empty, s1 just lingers

    await service.request_seat("s2")
    await asyncio.sleep(0)  # let the scheduled warning task publish its warning

    # No free table yet - s2 queues, but s1 gets warned immediately rather
    # than waiting for the next sweep tick.
    assert status.queued == [("s2", 1)]
    assert status.warned == ["s1"]
    assert not status.vacated

    await let_pending_evictions_run()

    assert ("s2", 1) in status.assigned
    assert status.vacated == ["s1"]
    assert service.is_seated("s2", 1)
    assert not service.is_seated("s1", 1)
    assert service.queue_length == 0


async def test_new_request_prefers_free_table_over_evicting_a_finished_one():
    service, status, _ = make_service(num_tables=2)
    await service.request_seat("s1")
    await service.mark_finished_eating("s1")  # table 1 finished but lingering

    await service.request_seat("s2")

    # Table 2 is free, so s2 goes there - s1 is left alone.
    assert ("s2", 2) in status.assigned
    assert not status.vacated
    assert service.is_seated("s1", 1)
    assert service.is_seated("s2", 2)


async def test_oldest_finished_table_is_evicted_first():
    service, status, _ = make_service(num_tables=2)
    await service.request_seat("s1")
    await service.request_seat("s2")
    await service.mark_finished_eating("s2")  # finishes first
    await service.mark_finished_eating("s1")  # finishes second

    await service.request_seat("s3")
    await let_pending_evictions_run()

    assert status.vacated == ["s2"]  # oldest-finished evicted, not s1
    assert service.is_seated("s3", 2)
    assert service.is_seated("s1", 1)  # untouched


async def test_finished_then_explicit_vacate_does_not_double_evict():
    service, status, _ = make_service(num_tables=1)
    await service.request_seat("s1")
    await service.mark_finished_eating("s1")  # lingers, queue empty

    await service.vacate("s1")  # explicit leave before anyone reclaimed it

    assert status.vacated == ["s1"]
    assert service.occupied_count == 0

    # A later request must not find a stale "finished" entry for s1.
    await service.request_seat("s2")
    assert service.is_seated("s2", 1)
    assert status.vacated == ["s1"]  # s1 never appears again


async def test_dawdling_session_is_warned_then_evicted_once_someone_needs_the_table():
    service, status, _ = make_service(num_tables=1, max_dining_seconds=0)
    await service.request_seat("s1")  # seated, instantly "overdue" (cap is 0)

    await service.request_seat("s2")  # queued -> triggers the overdue check
    await asyncio.sleep(0)  # let the scheduled warning task publish its warning

    assert status.warned == ["s1"]
    assert not status.vacated
    assert service.is_seated("s1", 1)  # not evicted yet - still mid-grace

    await let_pending_evictions_run()

    assert status.vacated == ["s1"]
    assert service.is_seated("s2", 1)


async def test_dawdling_session_is_never_warned_while_nobody_is_waiting():
    service, status, _ = make_service(num_tables=1, max_dining_seconds=0)

    await service.request_seat("s1")  # seated, instantly "overdue", but alone

    assert not status.warned
    assert not status.vacated
    assert service.is_seated("s1", 1)


async def test_second_queued_request_does_not_warn_an_already_pending_session():
    service, status, _ = make_service(num_tables=1, max_dining_seconds=0)
    await service.request_seat("s1")  # seated, instantly "overdue"
    await service.request_seat("s2")  # queued, warns s1
    await service.request_seat("s3")  # also queues - s1 already pending, no re-warn
    await asyncio.sleep(0)  # let the scheduled warning task publish its warning

    assert status.warned == ["s1"]
    assert service.queue_length == 2

    await let_pending_evictions_run()

    assert status.vacated == ["s1"]
    assert service.is_seated("s2", 1)  # FIFO: s2 promoted, not s3
    assert service.queue_length == 1


def test_invalid_num_tables_raises():
    with pytest.raises(ValueError):
        make_service(num_tables=0)
