# Virtual Restaurant Backend

Python/asyncio backend for the virtual restaurant simulation. Talks to the
frontend exclusively over MQTT (no REST API), via a Mosquitto broker with a
WebSockets listener.

## Architecture

```txt
   Frontend (browser)                Mosquitto broker              Backend (this service)
  MQTT.js over WS  <---- WS:9001 ---->  (auth + ACLs)  <---- TCP:1883 ---->  aiomqtt client
                                                                              |
                                                              SeatingService  |  OrderService
                                                              (assigns/queues |  (validates + asks
                                                               tables)        |   SeatingService,
                                                                              |   simulates cooking)
                                                                     (pure asyncio domain
                                                                      core, no MQTT here)
```

- The **broker** (Mosquitto) is the only thing both sides connect to; it also
  enforces authentication and topic ACLs (see [Security](#security)).
- The **backend** is a single `aiomqtt` client: `src/mqtt/client.py` owns the
  connection lifecycle, `src/mqtt/subscriber.py` parses inbound JSON and
  dispatches to domain services, `src/mqtt/publisher_adapter.py` serializes
  domain events back to JSON.
- **`SeatingService`** (`src/domain/seating_service.py`) tracks which session
  occupies each table, a FIFO wait queue, and finished-but-not-evicted tables.
  Eviction is queue-driven, not timer-driven. **`OrderService`**
  (`src/domain/order_service.py`) validates orders (asking `SeatingService` if
  the session is seated there) and simulates cooking. Neither has any
  MQTT/JSON imports, so both are unit-testable with no broker.

## Topic & payload contract

| Topic | Direction | Retained | Purpose |
| --- | --- | --- | --- |
| `restaurant/table/{table_id}/order` | frontend → backend | no | place an order |
| `restaurant/table/{table_id}/order/accepted` | backend → frontend | no | order passed validation, cooking started |
| `restaurant/table/{table_id}/food` | backend → frontend | no | order is ready |
| `restaurant/table/{table_id}/order/rejected` | backend → frontend | no | order failed validation |
| `restaurant/seating/{session_id}/request` | frontend → backend | no | "seat me" |
| `restaurant/seating/{session_id}/vacate` | frontend → backend | no | leave now, unconditionally (explicit, or via Last Will on a crashed tab) |
| `restaurant/seating/{session_id}/finished` | frontend → backend | no | "I've eaten", keeps the table unless someone else needs it |
| `restaurant/seating/{session_id}/status` | backend → that session | no | assigned / queued / warning / vacated |
| `restaurant/seating/occupancy` | backend → everyone | **yes** | live occupied-table list + queue length |

QoS 1 everywhere. `session_id` is client-generated (e.g. `crypto.randomUUID()`)
and **must also be used as the connection's MQTT Client Identifier**, since
the broker's `%c`-pattern ACL keys on it (see [Security](#security)).

**ORDER** (frontend → `restaurant/table/{table_id}/order`):

```json
{ "food_name": "margherita pizza", "client_order_id": "uuid-per-order", "session_id": "uuid-per-session" }
```

`food_name`: required, non-empty, ≤200 chars. `client_order_id`: required,
≤128 chars, dedupes redelivered QoS 1 messages. `session_id`: required, must
match the session seated at `table_id` (`not_seated_at_table` otherwise). A
malformed order is dropped (logged, not published to `order/rejected`; see
[Known limitations](#known-limitations)).

**Accepted** (backend → `order/accepted`, right after validation, before the
cook delay starts, so the frontend learns `prep_seconds` ahead of time):

```json
{ "schema": "order.accepted.v1", "client_order_id": "echoed-back-from-the-order", "table_id": 2, "prep_seconds": 23.4 }
```

**FOOD** (backend → `food`):

```json
{
  "schema": "food.v1",
  "order_id": "backend-generated-uuid",
  "client_order_id": "echoed-back-from-the-order",
  "food_name": "margherita pizza",
  "table_id": 2,
  "ready_at": "2026-07-31T12:00:42.000Z",
  "prep_seconds": 23.4
}
```

**Rejected** (backend → `order/rejected`):

```json
{ "schema": "order.rejected.v1", "client_order_id": "echoed-back-from-the-order", "reason": "empty_food_name" }
```

Reasons: `empty_food_name`, `food_name_too_long`, `unknown_table`,
`too_many_pending_orders`, `not_seated_at_table`.

**Seat request** (frontend → `seating/{session_id}/request`):
`{"schema": "seat.request.v1"}`. Idempotent: re-requesting while already
seated/queued just re-sends current status (how a refreshed tab resyncs). If
nothing's free, a lingering finished (or overdue - see below) session gets
warned and eventually evicted; the new requester queues in the meantime.

**Vacate** (frontend, or the broker via MQTT Last Will, → `vacate`):
`{"schema": "seat.vacate.v1", "reason": "user_action" | "disconnected"}`.
Frees the table unconditionally and immediately.

**Finished eating** (frontend → `finished`): `{"schema": "seat.finished.v1"}`.
Means "done, but keep my table unless someone else needs it." A session that
hasn't sent this is also eligible for eviction once it's been seated past
`max_dining_seconds` (still ordering/eating too long) - same demand-driven
rule: nobody's evicted while the restaurant has room. Either way, eviction
always warns first (`state: "warning"`) and waits
`eviction_warning_grace_seconds` before actually reclaiming the table. The
evicted session gets `{"state": "vacated"}` on its status topic once that
grace period elapses.

**Seat status** (backend → `status`):

```json
{ "schema": "seat.status.v1", "state": "assigned", "table_id": 2 }
{ "schema": "seat.status.v1", "state": "queued", "queue_position": 3 }
{ "schema": "seat.status.v1", "state": "warning", "grace_seconds": 20.0 }
{ "schema": "seat.status.v1", "state": "vacated" }
```

**Occupancy** (backend, retained, → `occupancy`):

```json
{ "schema": "seating.occupancy.v1", "occupied_tables": [1, 3], "num_tables": 4, "queue_length": 2 }
```

### MQTT Last Will: handling a closed/crashed browser tab

The frontend sets its MQTT **Will** at connect time to the same topic/payload
as an explicit vacate: `topic=restaurant/seating/{session_id}/vacate`,
`payload={"schema":"seat.vacate.v1","reason":"disconnected"}`, `qos=1`,
`retain=false`. The broker auto-publishes this if the connection drops
uncleanly. `SeatingService.vacate()` is idempotent, so no special-case code is
needed; see
`test/integration/test_seating_flow.py::test_lwt_triggers_auto_vacate_and_promotes_next_queued`.

## Running it

```bash
# one-time: generate broker credentials (bcrypt-hashed passwd file)
./mosquitto/generate_passwd.sh

export MOSQUITTO_BACKEND_PASSWORD=<the password you set for the "backend" user above>
docker compose up --build
```

Starts Mosquitto (`1883` plain MQTT, `9001` MQTT-over-WebSockets) and the
backend service. Configured via env vars (see `src/config.py`): `NUM_TABLES`
(default 4), `MIN_DELAY_SECONDS`/`MAX_DELAY_SECONDS` (default 10-30s cook
time), `MAX_FOOD_NAME_LEN`, `MAX_PENDING_PER_TABLE`.

A frontend client connects to `ws://localhost:9001` using the shared
`customer` credential from `mosquitto/acl.conf`, with its MQTT Client
Identifier set to its own generated `session_id`.

## Testing

Requires Python 3.10+.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

pytest                    # unit tests only (no broker needed, ~0.5s)
pytest -m integration     # + integration/concurrency tests (needs docker)
```

- **Unit tests** (`test/unit/`) drive `OrderService`/`SeatingService` directly
  against fakes: validation failures, concurrent orders, backpressure,
  duplicate-id dropping, seat assignment/queueing/eviction, and the
  `order.accepted.v1`/FOOD `prep_seconds` match.
- **Integration tests** (`test/integration/`) run against a real Mosquitto
  container: the full request → assign → order → vacate cycle, ACL
  enforcement, LWT-triggered auto-vacate, and broker-restart recovery.
- **Concurrency test** (`test/concurrency/`) seats four sessions on four real
  tables concurrently and fires concurrent orders, asserting no double-booking
  and correct food/order matching.

## Security

**Implemented:**

- No anonymous broker access: every client authenticates via
  `mosquitto/passwd` (bcrypt). Two accounts exist: `backend`, and a single
  shared `customer` credential used by every browser client.
- Since a session's table isn't known until runtime, the `customer` ACL grants
  access to all table topics. `OrderService.validate()` enforces "session X
  may only order from table Y" via `SeatingService.is_seated()`, rejecting
  with `not_seated_at_table` otherwise.
- Per-session isolation for seating topics is enforced by a static Mosquitto
  ACL `pattern` rule keyed on `%c` (the Client Identifier):

  ```txt
  pattern write restaurant/seating/%c/request
  pattern write restaurant/seating/%c/vacate
  pattern read restaurant/seating/%c/status
  ```

  This is only sound because session ids are unguessable random UUIDv4s,
  never exposed via URL, query string, or log line.
- Input validation: Pydantic schemas, length caps, stripped control
  characters, plus broker `message_size_limit` as defense in depth.
- Per-table in-flight order cap (`MAX_PENDING_PER_TABLE`).

**Considered and rejected:** Mosquitto's dynamic-security plugin, which would
let the broker grant/revoke per-table access at assign/vacate time. Rejected
because it requires a bootstrap/grant/revoke lifecycle that just relocates the
same authority into broker-plugin state, without a guarantee this project's
threat model needs.

**Documented trade-offs, not implemented:**

- TLS/WSS: broker listens on plain `ws://`/`mqtt://`.
- Shared `customer` credential instead of per-session tokens (would need a
  non-MQTT provisioning channel).
- Broker-level rate limiting.

## Known limitations

- **State is in-memory and per-process.** A restart drops all in-flight
  orders and seating state (matches the product spec).
- **A malformed order is only logged, not surfaced on `order/rejected`.**
  That topic is only for domain-level validation failures.
- **The broker doesn't prevent ordering for a table you're not seated at.**
  Enforced by `OrderService` instead; see [Security](#security).
- **`restaurant/seating/occupancy` is the only retained topic**, since a
  client that hasn't subscribed yet has no other way to learn occupancy.
- **A session can't "un-finish."** No message cancels `mark_finished_eating`;
  the only way back is a fresh `request_seat` after eviction.
- **Frontend integration**: `frontend/src/lib/restaurant/mqtt-engine.svelte.ts`
  implements this contract. The offline `local-engine.svelte.ts` fallback is
  selectable via `PUBLIC_ENGINE_MODE=local`; see `frontend/README.md`.
