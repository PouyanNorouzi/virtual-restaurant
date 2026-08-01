# Virtual Restaurant

A live simulation of ordering food at a restaurant: a customer requests a table,
gets seated (or queued if full), places an order, watches it cook, eats, and
gives up the table when someone else needs it. Runs over an event-driven backend
and a Svelte frontend that talk exclusively via MQTT (no REST).

```txt
frontend/  Svelte 5 + SvelteKit UI, talks MQTT-over-WebSockets to the backend
backend/   Python asyncio backend + Mosquitto broker
```

## How to run the app

Requires Docker, Python 3.10+, and pnpm/Node 20+.

```sh
# 1. Backend: broker + Python service
cd backend
./mosquitto/generate_passwd.sh          # one-time: sets "backend" + "customer" broker passwords
export MOSQUITTO_BACKEND_PASSWORD=...   # the "backend" password you just set
docker compose up --build

# 2. Frontend, in a second terminal
cd frontend
cp .env.example .env                    # set PUBLIC_MQTT_CUSTOMER_PASSWORD to match
pnpm install
pnpm dev
```

Open `http://localhost:5173` in two browser tabs to see two independent
customers interact.

**Tests:**

```sh
cd backend && pytest                  # unit tests, no broker needed (~0.5s)
cd backend && pytest -m integration   # + integration tests over a real broker (needs docker)
cd frontend && pnpm test              # unit/component tests
```

Full details, including the MQTT topic/payload contract, live in
[`backend/README.md`](backend/README.md) and [`frontend/README.md`](frontend/README.md).

## Key design decisions

**MQTT-only, broker as the only intermediary.** Frontend and backend never talk
directly; both only talk to Mosquitto. There's no request/response, only
pub/sub, so flows like order acceptance and seat assignment get their own
explicit events instead of a synchronous return value.

**Domain logic has no transport code.** `OrderService` and `SeatingService`
(`backend/src/domain/`) are plain `asyncio` classes with no MQTT/JSON imports;
they depend on small `Protocol` ports instead. This keeps the domain layer
(56 tests) fast and broker-free to test.

**Seat assignment is dynamic, not fixed.** `SeatingService` assigns the first
free table on request, queues you if full, and auto-promotes the next queued
customer when a table frees. Since a customer's table isn't known until
runtime, the broker can't enforce "customer X may only order for table Y" by
itself; `OrderService` does that check instead (`not_seated_at_table`), and the
broker ACL uses one shared `customer` credential for order topics. Per-session
isolation for seat status/vacate is still enforced by the broker via a static
ACL rule keyed on each client's MQTT Client Identifier.

**A finished diner keeps their table until someone else needs it, no timer.**
Finishing a meal just marks a table reclaimable; it's taken back only when a
new customer actually needs it. There's no "undo" for this signal.

**Order acceptance is announced before cooking starts.** A small
`order.accepted.v1` event fires right after validation, before the random cook
delay, so the frontend can show an accurate countdown instead of waiting for
the final "food ready" event.

**Table occupancy is the one retained MQTT topic.** Every other topic is a
point-in-time event; occupancy is retained so a client that just subscribed can
learn current state immediately.

## What I'd improve with more time

- Actually run the integration suite against a live broker+backend.
- TLS/WSS (currently plain `ws://`/`mqtt://`).
- Per-session dynamic credentials (e.g. Mosquitto's dynamic-security plugin).
- Broker-side rate limiting (only an app-level per-table cap exists today).
- An explicit "cancel my wait" / "leave" UI action while queued.
- A single CI job exercising backend + broker + frontend end-to-end.
- Persistence: state is in-memory, so a backend crash drops all orders/seating.
- Observability beyond logs: no metrics or tracing.
