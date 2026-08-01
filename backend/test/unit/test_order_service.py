import asyncio
import uuid
from datetime import datetime, timezone

import pytest
from src.domain.models import FoodReady, OrderAccepted, OrderRequest
from src.domain.order_service import OrderService

# Real (but tiny) delays are used instead of mocking asyncio.sleep, so these
# tests exercise the actual scheduling path end to end while staying fast.
MIN_DELAY = 0.01
MAX_DELAY = 0.02


class FakeFoodPublisher:
    def __init__(self) -> None:
        self.accepted: list[OrderAccepted] = []
        self.published: list[FoodReady] = []

    async def publish_accepted(self, accepted: OrderAccepted) -> None:
        self.accepted.append(accepted)

    async def publish_food(self, food: FoodReady) -> None:
        self.published.append(food)


class FakeRejectionPublisher:
    def __init__(self) -> None:
        self.rejections: list[tuple[int, str, str]] = []

    async def publish_rejected(self, table_id: int, client_order_id: str, reason: str) -> None:
        self.rejections.append((table_id, client_order_id, reason))


class FakeSeatingQuery:
    """Defaults to "everyone is seated everywhere" so existing tests don't
    need to care about seating at all; pass always_seated=False + an
    explicit seated set to exercise the not_seated_at_table rejection path.
    """

    def __init__(self, always_seated: bool = True, seated: set[tuple[str, int]] | None = None) -> None:
        self._always_seated = always_seated
        self._seated = seated or set()

    def is_seated(self, session_id: str, table_id: int) -> bool:
        if self._always_seated:
            return True
        return (session_id, table_id) in self._seated


def make_request(
    table_id: int = 1,
    food_name: str = "pizza",
    client_order_id: str | None = None,
    session_id: str | None = None,
) -> OrderRequest:
    return OrderRequest(
        table_id=table_id,
        food_name=food_name,
        client_order_id=client_order_id or str(uuid.uuid4()),
        session_id=session_id or str(uuid.uuid4()),
        submitted_at=datetime.now(timezone.utc),
    )


def make_service(
    num_tables: int = 4,
    max_food_name_len: int = 200,
    max_pending_per_table: int = 5,
    min_delay: float = MIN_DELAY,
    max_delay: float = MAX_DELAY,
    seating_query: FakeSeatingQuery | None = None,
) -> tuple[OrderService, FakeFoodPublisher, FakeRejectionPublisher]:
    publisher = FakeFoodPublisher()
    rejections = FakeRejectionPublisher()
    service = OrderService(
        num_tables=num_tables,
        min_delay_seconds=min_delay,
        max_delay_seconds=max_delay,
        publisher=publisher,
        rejection_publisher=rejections,
        seating_query=seating_query or FakeSeatingQuery(),
        max_food_name_len=max_food_name_len,
        max_pending_per_table=max_pending_per_table,
    )
    return service, publisher, rejections


async def wait_until(predicate, timeout: float = 1.0) -> None:
    async def _poll():
        while not predicate():
            await asyncio.sleep(0.005)

    await asyncio.wait_for(_poll(), timeout=timeout)


async def test_happy_path_publishes_food_matching_order():
    service, publisher, rejections = make_service()
    request = make_request(table_id=2, food_name="  ramen  ", client_order_id="abc")

    await service.handle_order(request)
    await wait_until(lambda: len(publisher.published) == 1)

    assert not rejections.rejections
    food = publisher.published[0]
    assert food.table_id == 2
    assert food.food_name == "ramen"  # whitespace trimmed
    assert food.client_order_id == "abc"
    assert MIN_DELAY <= food.prep_seconds <= MAX_DELAY


async def test_accepted_event_is_published_before_food_with_matching_delay():
    service, publisher, _ = make_service()
    await service.handle_order(make_request(table_id=3, client_order_id="acc-1"))

    # publish_accepted happens synchronously in handle_order, before the
    # cook task is even scheduled - no need to wait_until for it.
    assert len(publisher.accepted) == 1
    accepted = publisher.accepted[0]
    assert accepted.table_id == 3
    assert accepted.client_order_id == "acc-1"
    assert MIN_DELAY <= accepted.prep_seconds <= MAX_DELAY

    await wait_until(lambda: len(publisher.published) == 1)
    # The delay announced up front must match what actually happened.
    assert publisher.published[0].prep_seconds == accepted.prep_seconds


async def test_accepted_event_is_not_published_for_rejected_orders():
    service, publisher, rejections = make_service()
    await service.handle_order(make_request(food_name="   "))
    await wait_until(lambda: len(rejections.rejections) == 1)

    assert not publisher.accepted


async def test_empty_food_name_is_rejected():
    service, publisher, rejections = make_service()
    await service.handle_order(make_request(food_name="   "))
    await wait_until(lambda: len(rejections.rejections) == 1)

    assert not publisher.published
    assert rejections.rejections[0][2] == "empty_food_name"


async def test_oversized_food_name_is_rejected():
    service, publisher, rejections = make_service(max_food_name_len=10)
    await service.handle_order(make_request(food_name="x" * 11))
    await wait_until(lambda: len(rejections.rejections) == 1)

    assert not publisher.published
    assert rejections.rejections[0][2] == "food_name_too_long"


async def test_out_of_range_table_is_rejected():
    service, publisher, rejections = make_service(num_tables=4)
    await service.handle_order(make_request(table_id=99))
    await wait_until(lambda: len(rejections.rejections) == 1)

    assert not publisher.published
    assert rejections.rejections[0][2] == "unknown_table"


async def test_order_rejected_when_session_not_seated_at_table():
    service, publisher, rejections = make_service(seating_query=FakeSeatingQuery(always_seated=False))
    await service.handle_order(make_request(table_id=1, session_id="s1"))
    await wait_until(lambda: len(rejections.rejections) == 1)

    assert not publisher.published
    assert rejections.rejections[0][2] == "not_seated_at_table"


async def test_order_accepted_when_session_is_seated_at_table():
    seating = FakeSeatingQuery(always_seated=False, seated={("s1", 2)})
    service, publisher, rejections = make_service(seating_query=seating)

    await service.handle_order(make_request(table_id=2, session_id="s1", client_order_id="ok"))
    await wait_until(lambda: len(publisher.published) == 1)

    assert not rejections.rejections
    assert publisher.published[0].table_id == 2

    # A different session at the same table is still rejected.
    await service.handle_order(make_request(table_id=2, session_id="s2", client_order_id="bad"))
    await wait_until(lambda: len(rejections.rejections) == 1)
    assert rejections.rejections[0][2] == "not_seated_at_table"


async def test_concurrent_orders_across_tables_do_not_cross_contaminate():
    service, publisher, _ = make_service(num_tables=4, max_pending_per_table=10)
    orders = [
        make_request(table_id=(i % 4) + 1, food_name=f"food-{i}", client_order_id=f"id-{i}")
        for i in range(12)
    ]

    await asyncio.gather(*(service.handle_order(o) for o in orders))
    await wait_until(lambda: len(publisher.published) == 12)

    by_client_id = {food.client_order_id: food for food in publisher.published}
    assert len(by_client_id) == 12
    for i, order in enumerate(orders):
        food = by_client_id[f"id-{i}"]
        assert food.table_id == order.table_id
        assert food.food_name == order.food_name


async def test_per_table_cap_rejects_excess_then_frees_up():
    service, publisher, rejections = make_service(max_pending_per_table=2, min_delay=0.05, max_delay=0.05)

    await service.handle_order(make_request(table_id=1, client_order_id="a"))
    await service.handle_order(make_request(table_id=1, client_order_id="b"))
    await service.handle_order(make_request(table_id=1, client_order_id="c"))  # over cap

    await wait_until(lambda: len(rejections.rejections) == 1)
    assert rejections.rejections[0][2] == "too_many_pending_orders"

    await wait_until(lambda: len(publisher.published) == 2)
    assert service.pending_for_table(1) == 0

    # Now that earlier orders resolved, the table has room again.
    await service.handle_order(make_request(table_id=1, client_order_id="d"))
    await wait_until(lambda: len(publisher.published) == 3)


async def test_duplicate_client_order_id_is_dropped():
    service, publisher, rejections = make_service()
    request = make_request(client_order_id="dup-1")

    await service.handle_order(request)
    await service.handle_order(request)
    await wait_until(lambda: len(publisher.published) == 1)
    await asyncio.sleep(MAX_DELAY * 2)  # ensure no delayed second publish sneaks in

    assert len(publisher.published) == 1
    assert not rejections.rejections


async def test_task_bookkeeping_cleans_up_after_completion():
    service, publisher, _ = make_service()
    await service.handle_order(make_request(client_order_id="x"))
    await wait_until(lambda: len(publisher.published) == 1)
    await asyncio.sleep(0.01)  # let the done-callback run

    assert service.pending_count == 0
    assert service.pending_for_table(1) == 0


async def test_shutdown_cancels_in_flight_tasks():
    service, publisher, _ = make_service(min_delay=5.0, max_delay=5.0)
    await service.handle_order(make_request(client_order_id="slow"))
    assert service.pending_count == 1

    await service.shutdown()

    assert service.pending_count == 0
    assert not publisher.published


@pytest.mark.parametrize("min_delay,max_delay", [(-1.0, 1.0), (2.0, 1.0)])
def test_invalid_delay_bounds_raise(min_delay, max_delay):
    with pytest.raises(ValueError):
        make_service(min_delay=min_delay, max_delay=max_delay)


def test_invalid_num_tables_raises():
    with pytest.raises(ValueError):
        make_service(num_tables=0)
