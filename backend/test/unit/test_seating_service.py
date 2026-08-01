import asyncio

import pytest

from src.domain.seating_service import SeatingService


class FakeSeatingStatusPublisher:
    def __init__(self) -> None:
        self.assigned: list[tuple[str, int]] = []
        self.queued: list[tuple[str, int]] = []
        self.vacated: list[str] = []

    async def publish_assigned(self, session_id: str, table_id: int) -> None:
        self.assigned.append((session_id, table_id))

    async def publish_queued(self, session_id: str, queue_position: int) -> None:
        self.queued.append((session_id, queue_position))

    async def publish_vacated(self, session_id: str) -> None:
        self.vacated.append(session_id)


class FakeOccupancyPublisher:
    def __init__(self) -> None:
        self.snapshots: list[tuple[list[int], int, int]] = []

    async def publish_occupancy(
        self, occupied_tables: list[int], num_tables: int, queue_length: int
    ) -> None:
        self.snapshots.append((list(occupied_tables), num_tables, queue_length))


def make_service(num_tables: int = 2) -> tuple[SeatingService, FakeSeatingStatusPublisher, FakeOccupancyPublisher]:
    status = FakeSeatingStatusPublisher()
    occupancy = FakeOccupancyPublisher()
    service = SeatingService(
        num_tables=num_tables, status_publisher=status, occupancy_publisher=occupancy
    )
    return service, status, occupancy


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


def test_invalid_num_tables_raises():
    with pytest.raises(ValueError):
        make_service(num_tables=0)
