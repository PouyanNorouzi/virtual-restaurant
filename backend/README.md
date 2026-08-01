# Virtual Restaurant Backend

Python/asyncio backend for the virtual restaurant ordering simulation. Talks
to the frontend exclusively over MQTT (no REST API), via a Mosquitto broker
with a WebSockets listener.

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

- The **broker** (Mosquitto) is the only thing both frontend and backend
  connect to; they never talk to each other directly. It also enforces
  authentication and topic ACLs (see [Security](#security)).
- The **backend** is a single `aiomqtt` client. `src/mqtt/client.py` owns the
  connection lifecycle (connect, subscribe, reconnect-with-backoff);
  `src/mqtt/subscriber.py` parses inbound JSON and dispatches to the
  appropriate domain service; `src/mqtt/publisher_adapter.py` turns domain
  events back into JSON on the way out.
- **`SeatingService`** (`src/domain/seating_service.py`) tracks which
  session (if any) occupies each table, a FIFO queue of sessions waiting
  for one to free up, and which occupied tables belong to sessions that
  finished eating but haven't been evicted yet — eviction is queue-driven,
  not timer-driven: a finished session keeps its table until someone else
  actually needs it (see `mark_finished_eating` and `request_seat`'s
  finished-table fallback). **`OrderService`** (`src/domain/order_service.py`)
  validates orders (including asking `SeatingService` whether the ordering
  session is actually seated at that table) and simulates cooking. Neither
  has any MQTT/JSON imports, so both are unit-testable with no broker at
  all — `OrderService` depends on `SeatingService` only through a small
  read-only `SeatingQuery` port (`src/domain/ports.py`), never the other
  way around.

## Topic & payload contract

| Topic | Direction | Retained | Purpose |
| --- | --- | --- | --- |
| `restaurant/table/{table_id}/order` | frontend → backend | no | place an order |
| `restaurant/table/{table_id}/order/accepted` | backend → frontend | no | order passed validation, cooking started |
| `restaurant/table/{table_id}/food` | backend → frontend | no | order is ready |
| `restaurant/table/{table_id}/order/rejected` | backend → frontend | no | order failed validation |
| `restaurant/seating/{session_id}/request` | frontend → backend | no | "seat me" |
| `restaurant/seating/{session_id}/vacate` | frontend → backend | no | leave now, unconditionally (explicit, or via Last Will on a crashed tab) |
| `restaurant/seating/{session_id}/finished` | frontend → backend | no | "I've eaten" — keeps the table unless someone else needs it |
| `restaurant/seating/{session_id}/status` | backend → that session | no | assigned / queued / vacated |
| `restaurant/seating/occupancy` | backend → everyone | **yes** | live occupied-table list + queue length, for a lobby view |

QoS 1 (at-least-once) everywhere. `session_id` is client-generated (e.g.
`crypto.randomUUID()`) **and must also be used as that connection's MQTT
Client Identifier** — this is a hard protocol requirement, not just a
convention, since it's what the broker's `%c`-pattern ACL keys on (see
[Security](#security)).

**ORDER** (frontend publishes to `restaurant/table/{table_id}/order`):

```json
{ "food_name": "margherita pizza", "client_order_id": "uuid-per-order", "session_id": "uuid-per-session" }
```

`food_name` is required, non-empty after trimming, capped at 200 characters
(`MAX_FOOD_NAME_LEN`). `client_order_id` is required (non-empty, ≤128
chars) and used to drop duplicate deliveries (MQTT QoS 1 can redeliver).
`session_id` is required (same length rule) and must match the session
currently seated at `table_id` — see `not_seated_at_table` below. A
malformed order (including either id missing) fails schema validation and
is dropped (logged, not published to `.../order/rejected` — see
[Known limitations](#known-limitations)).

**Accepted** (backend publishes to `restaurant/table/{table_id}/order/accepted`, immediately after validation succeeds and before the cook delay starts — the whole reason this event exists is that the backend never tells the frontend how long cooking will take otherwise; `prep_seconds` here is the real chosen delay, not a placeholder, and matches the eventual FOOD event's `prep_seconds` exactly):

```json
{ "schema": "order.accepted.v1", "client_order_id": "echoed-back-from-the-order", "table_id": 2, "prep_seconds": 23.4 }
```

**FOOD** (backend publishes to `restaurant/table/{table_id}/food`):

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

**Rejected** (backend publishes to `restaurant/table/{table_id}/order/rejected`):

```json
{ "schema": "order.rejected.v1", "client_order_id": "echoed-back-from-the-order", "reason": "empty_food_name" }
```

Reasons: `empty_food_name`, `food_name_too_long`, `unknown_table`,
`too_many_pending_orders`, `not_seated_at_table`.

**Seat request** (frontend publishes to `restaurant/seating/{session_id}/request`): `{"schema": "seat.request.v1"}` — no other fields; `session_id` lives in the topic. Idempotent: requesting again while already seated/queued just re-sends the current status (this is also how a refreshed browser tab resyncs, with no separate "query" topic needed). If nothing is free, a session that finished eating but is only lingering (see below) gets evicted to make room for the new requester before anyone is enqueued.

**Vacate** (frontend publishes, or the broker publishes on that client's behalf via MQTT Last Will — see below, to `restaurant/seating/{session_id}/vacate`): `{"schema": "seat.vacate.v1", "reason": "user_action" | "disconnected"}` — `reason` is log-only, never branched on; an explicit vacate and an LWT-triggered one hit the identical code path. Frees the table *unconditionally and immediately*, regardless of whether anyone's waiting.

**Finished eating** (frontend publishes to `restaurant/seating/{session_id}/finished`): `{"schema": "seat.finished.v1"}`. Distinct from vacate on purpose: this means "I'm done, but don't take my table unless someone else needs it" — a session that finishes eating with nobody waiting just keeps sitting there indefinitely; the table is only reclaimed later, either the moment someone is *already* queued when this arrives, or by a later `request_seat()` finding nothing free and evicting the oldest finished-but-lingering table instead of enqueueing the new arrival. Either way the evicted session receives `{"state": "vacated"}` on its own status topic — the backend doesn't distinguish "you left" from "you got kicked out" at the protocol level; the frontend infers the latter from receiving `vacated` while it's still sitting in a "finished eating" phase.

**Seat status** (backend publishes to `restaurant/seating/{session_id}/status`):

```json
{ "schema": "seat.status.v1", "state": "assigned", "table_id": 2 }
{ "schema": "seat.status.v1", "state": "queued", "queue_position": 3 }
{ "schema": "seat.status.v1", "state": "vacated" }
```

**Occupancy** (backend publishes, retained, to `restaurant/seating/occupancy`):

```json
{ "schema": "seating.occupancy.v1", "occupied_tables": [1, 3], "num_tables": 4, "queue_length": 2 }
```

### MQTT Last Will and Testament: handling a closed/crashed browser tab

A customer's browser tab can close without clicking "Leave," which would
otherwise strand a table as permanently occupied. The frontend is expected
to set its MQTT **Will** at connect time to the *exact same* topic/payload
an explicit vacate uses: `topic=restaurant/seating/{session_id}/vacate`,
`payload={"schema":"seat.vacate.v1","reason":"disconnected"}`, `qos=1`,
`retain=false`. The broker auto-publishes this on the client's behalf if
its connection drops uncleanly. Because `SeatingService.vacate()` is
idempotent, the backend needs zero special-case code for this — see
`test/integration/test_seating_flow.py::test_lwt_triggers_auto_vacate_and_promotes_next_queued`.

## Running it

```bash
# one-time: generate broker credentials (bcrypt-hashed passwd file)
./mosquitto/generate_passwd.sh

export MOSQUITTO_BACKEND_PASSWORD=<the password you set for the "backend" user above>
docker compose up --build
```

This starts Mosquitto (ports `1883` plain MQTT, `9001` MQTT-over-WebSockets)
and the backend service. Configuration is via environment variables (see
`src/config.py`): `NUM_TABLES` (default 4, reused as both the order
table-range check and the seat count), `MIN_DELAY_SECONDS` /
`MAX_DELAY_SECONDS` (default 10–30s cook time), `MAX_FOOD_NAME_LEN`,
`MAX_PENDING_PER_TABLE`.

A frontend client should connect to `ws://localhost:9001` using the single
shared `customer` credential from `mosquitto/acl.conf`, with its MQTT
Client Identifier set to its own generated `session_id`.

## Testing

Requires Python 3.10+.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

pytest                    # unit tests only (no broker needed, ~0.5s)
pytest -m integration     # + integration/concurrency tests (spins up a real
                           #   Mosquitto via testcontainers; needs docker)
```

- **Unit tests** (`test/unit/`) drive `OrderService`/`SeatingService`
  directly against fakes — no network involved. `test_order_service.py`
  covers the happy path, every validation failure (including
  `not_seated_at_table` via a fake `SeatingQuery`), concurrent orders
  across/within tables, the per-table backpressure cap, duplicate-id
  dropping, task cleanup/shutdown, and that the `order.accepted.v1` event
  is published synchronously with a `prep_seconds` that later matches the
  FOOD event exactly. `test_seating_service.py` covers first-free-by-index
  assignment, FIFO queueing, idempotent re-requests, vacate + auto-promotion,
  canceling a queued wait, the vacate-when-neither no-op (the case that
  makes LWT safe), a concurrency test asserting no table is ever
  double-booked, and the full finished-eating eviction matrix: no eviction
  while the queue is empty, immediate eviction when someone's already
  waiting, a later request reclaiming the oldest finished-but-lingering
  table instead of enqueueing, preferring a genuinely free table over
  evicting one, and an explicit vacate after "finished" not double-firing.
- **Integration tests** (`test/integration/`) spin up a real Mosquitto
  container (same `mosquitto.conf`/`acl.conf` as production).
  `test_seating_flow.py` exercises the full request → assign → order →
  vacate → auto-reassign cycle, the `order.accepted.v1` → FOOD delay match
  over a real broker, the "finished eating lingers until reclaimed" flow,
  the `not_seated_at_table` rejection, `%c`-pattern isolation between two
  sessions' seating topics, and the LWT-triggered auto-vacate.
  `test_acl_enforcement.py` documents that the broker now deliberately
  *allows* any customer to publish to any table's order topic (see
  [Security](#security)) while still blocking forged FOOD events.
  `test_reconnect.py` verifies recovery after a broker restart.
- **Concurrency test** (`test/concurrency/`) seats four sessions onto four
  distinct real tables concurrently, then fires two concurrent orders per
  session, asserting every order gets exactly one correctly-matched food
  event and no table was ever double-booked.

## Security

**Implemented:**

- No anonymous broker access — every client authenticates via
  `mosquitto/passwd` (bcrypt, generated by `generate_passwd.sh`). Only two
  accounts exist: `backend`, and a single shared `customer` credential used
  by **every** browser client (baked into the frontend's build config — it
  is not a per-customer secret, and provides zero customer-to-customer
  isolation by itself).
- Since which table a session may use isn't known until runtime seat
  assignment, the broker's ACL for `customer` grants access to *all* table
  topics (`restaurant/table/+/order` write, `+/food` and `+/order/rejected`
  read) — enforcing "session X may only order from table Y" is the
  responsibility of `OrderService.validate()`, which asks
  `SeatingService.is_seated(session_id, table_id)` and rejects with
  `not_seated_at_table` otherwise. See `test_acl_enforcement.py` and
  `test_seating_flow.py`.
- Per-**session** isolation for the seating control topics
  (request/vacate/status) is recovered via Mosquitto's static `pattern` ACL
  directive keyed on `%c` (the connecting client's MQTT Client Identifier):

  ```txt
  pattern write restaurant/seating/%c/request
  pattern write restaurant/seating/%c/vacate
  pattern read restaurant/seating/%c/status
  ```

  Since every session's `session_id` *is* its Client Identifier by
  protocol convention, this statically grants each session access to only
  its own seating topics, with no per-client `user` blocks and no dynamic
  provisioning. **This is only sound if session ids are unguessable**:
  client-generated random UUIDv4s, never exposed via a URL, query string,
  or log line. Leaking one is equivalent to leaking a session cookie. (A
  secondary, incidental guard: MQTT brokers reject duplicate Client
  Identifiers, so two connections can never simultaneously hold the same
  `session_id`.)
- Input validation/sanitization: Pydantic schema validation, length caps on
  every id/name field, stripped control characters, plus `message_size_limit`
  at the broker as defense in depth.
- Per-table in-flight order cap (`MAX_PENDING_PER_TABLE`) guards against one
  session flooding the backend with orders.

**Why not Mosquitto's dynamic-security plugin instead** (the alternative
that would let the broker keep enforcing per-table authorization even under
dynamic assignment, by having the backend grant/revoke a specific client's
topic access at the moment of seat assign/vacate, over MQTT `$CONTROL`
topics): it requires switching the entire broker config model, building a
bootstrap flow for provisioning a client that doesn't have a role yet, and
a grant/revoke lifecycle tied to assign/vacate/disconnect — real added
complexity that doesn't eliminate the backend's need to be the one holding
this authority (it would just relocate it into broker-plugin state instead
of `OrderService`'s in-memory map), for a guarantee our actual threat model
doesn't need on the order-topic side. It stays a documented alternative,
not the chosen design.

**Documented trade-offs, not implemented (given the exercise's time budget):**

- **TLS/WSS**: the broker listens on plain `ws://`/`mqtt://` for local/demo
  use. Production would terminate TLS either directly in Mosquitto
  (`certfile`/`keyfile` on the `9001` listener) or via a reverse proxy in
  front of it, and use `wss://` from the browser.
- **Shared `customer` credential rather than per-session tokens**: real
  production auth would issue short-lived, per-session credentials on
  connect. That requires some non-MQTT provisioning channel, which is in
  tension with the "MQTT only, no REST" constraint — the shared credential
  plus the `%c`-pattern/domain-check combination above is the pragmatic
  stand-in.
- **Broker-level rate limiting**: Mosquitto has no rich built-in rate
  limiting; only the application-level per-table cap above is implemented.

## Known limitations

- **State is entirely in-memory and per-process.** A backend restart drops
  all in-flight orders *and* all seating assignments/queue state silently —
  this matches the product spec ("state does not need to survive a
  restart") but is worth naming explicitly.
- **A malformed order (including a missing/empty `client_order_id` or
  `session_id`) is only logged, not surfaced on `.../order/rejected`** —
  that topic is only used for *domain*-level validation failures (empty
  food name, unknown table, too many pending, not seated), which by
  definition already parsed successfully and have a valid `client_order_id`
  to echo back.
- **The broker does not prevent a customer from publishing an order to a
  table it isn't seated at** — that check is enforced by `OrderService`
  instead; see the Security section above.
- **`restaurant/seating/occupancy` is the first retained topic in this
  project** — a deliberate, narrow exception, since a client that hasn't
  subscribed yet has no other way to learn current occupancy (unlike seat
  status, which can always be resynced on demand via `request_seat`'s
  idempotency).
- **A session can't "un-finish."** Once `mark_finished_eating` is called,
  there's no protocol message to cancel it — the only way back to normal
  occupancy is a fresh `request_seat` after being evicted. This matches the
  product intent (finishing a meal is a one-way signal) but is worth
  naming as an intentional simplification, not an oversight.
- **Frontend integration**: `frontend/src/lib/restaurant/mqtt-engine.svelte.ts`
  implements this contract (MQTT.js, `$env/static/public` for the broker URL
  and shared `customer` password, a `session_id`/Client Identifier generated
  per tab, and the Last Will described above). The original fully local,
  fake-NPC simulation (`local-engine.svelte.ts`) is kept as an offline/demo
  fallback, selectable via `PUBLIC_ENGINE_MODE=local` — see `frontend/README.md`.
