# Virtual Restaurant — Frontend

Svelte 5 / SvelteKit UI for the virtual restaurant simulation. A customer requests a
table, gets seated (or queued if the restaurant is full), orders food, watches it
cook, eats, and eventually gives up their table to the next waiting customer — all
driven live over MQTT by the [backend](../backend/README.md).

## How it's built

The UI is engine-agnostic: every screen under `src/lib/components/restaurant/`
reads from and dispatches actions to a `RestaurantEngine` (`src/lib/restaurant/engine.ts`)
pulled from Svelte context, and has no idea whether that engine is talking to a
real broker or simulating everything locally.

```txt
DiningRoom.svelte                    picks + provides an engine via context
  ├─ RestaurantHeader, TableFloor     read engine.state (tables, phase, ...)
  ├─ WaiterOverlay / WaiterBar        phase-specific panels (waiting room,
  │    ├─ WaitingRoomPanel, FullyBookedPanel, AssignedPanel        assigned, seated, ...)
  │    └─ SeatedPrompt, ServedPrompt, PostEatPrompt
  ├─ OrderModal, CookingCard          order text entry + live cook countdown
  ├─ KickedOutScreen, ToastStack      eviction screen + transient notifications
  └─ TableCard / WaiterAvatar         presentational leaves, no engine access
```

Two implementations of `RestaurantEngine` exist:

- **`MqttRestaurantEngine`** (`src/lib/restaurant/mqtt-engine.svelte.ts`, default) —
  speaks MQTT over WebSockets to the real backend. Publishes seat requests, orders,
  and "finished eating" signals; subscribes to seat status, table occupancy, order
  acceptance, food-ready, and rejection events. See
  [`../backend/README.md`](../backend/README.md) for the full topic/payload contract
  this engine implements against.
- **`LocalRestaurantEngine`** (`src/lib/restaurant/local-engine.svelte.ts`) — a
  fully self-contained simulation (fake diners randomly occupying tables, a random
  cook time, a random queue count) with no network calls at all. Useful for
  demoing the UI offline or without standing up the backend.

`src/lib/restaurant/protocol.ts` defines the shared `Phase`/`RestaurantSnapshot`/
`RestaurantAction` shapes both engines produce and consume — components are built
against that contract, not against either engine directly.

## Setup

Requires [pnpm](https://pnpm.io) and Node 20+.

```sh
pnpm install
cp .env.example .env   # see "Connecting to the backend" below
pnpm dev                # or: pnpm dev -- --open
```

## Connecting to the backend

Configuration is via env vars (SvelteKit's `$env/static/public` — safe to be
public, see below), set in `.env` (copy from `.env.example`, gitignored):

| Var                             | Purpose                                                        |
| ------------------------------- | -------------------------------------------------------------- |
| `PUBLIC_MQTT_URL`               | Mosquitto's WebSocket listener, e.g. `ws://localhost:9001`     |
| `PUBLIC_MQTT_CUSTOMER_PASSWORD` | Password for the shared `customer` broker account              |
| `PUBLIC_ENGINE_MODE`            | `mqtt` (default, real backend) or `local` (offline simulation) |

`PUBLIC_MQTT_CUSTOMER_PASSWORD` must match whatever password
`../backend/mosquitto/generate_passwd.sh` was run with for the `customer` account
(see `../backend/mosquitto/acl.conf`). It's intentionally public — baked into the
browser bundle by design, not a secret. Per-customer isolation doesn't come from
this password; it comes from each browser tab generating its own random
`session_id`, using it as its MQTT Client Identifier, and the broker's ACL scoping
each identifier to only its own seating topics. See the backend's Security section
for the full reasoning.

### Running the full stack locally

```sh
# one terminal, in backend/
./mosquitto/generate_passwd.sh          # once — sets the backend + customer passwords
export MOSQUITTO_BACKEND_PASSWORD=...   # the "backend" password you just set
docker compose up --build

# another terminal, in frontend/
cp .env.example .env                    # fill in PUBLIC_MQTT_CUSTOMER_PASSWORD to match
pnpm install
pnpm dev
```

Open two browser tabs to see two independent customer sessions interact (queueing,
eviction once a table's occupant has finished eating and someone else needs it,
etc.) — see the backend README's "Finished eating" section for exactly how that
works.

## Scripts

| Command                       | Purpose                                |
| ----------------------------- | -------------------------------------- |
| `pnpm dev`                    | Start the dev server                   |
| `pnpm build` / `pnpm preview` | Production build / preview it locally  |
| `pnpm check`                  | Type-check (`svelte-check`)            |
| `pnpm test`                   | Run the unit/component test suite once |
| `pnpm test:unit`              | Run tests in watch mode                |
| `pnpm lint` / `pnpm format`   | Check / apply Prettier + ESLint        |

## Testing

- `src/lib/restaurant/mqtt-topics.spec.ts` — pure topic-builder unit tests, run in
  Node (no browser, no broker).
- `src/lib/restaurant/mqtt-engine.svelte.spec.ts` — drives `MqttRestaurantEngine`
  against a mocked `mqtt.js` client (no real broker), asserting on the resulting
  `RestaurantSnapshot` for every inbound message type (assigned/queued/vacated
  status, occupancy, order accepted/food/rejected) and every dispatched action.
  Runs in a real browser (Playwright/Chromium, via `vitest-browser-svelte`) since
  the engine uses Svelte 5 runes.
- What's **not** covered here: an actual `mqtt.connect()` handshake, broker ACL
  enforcement, or a real seat/order/eviction round trip — those need a live
  broker + backend and are exercised by the backend's own integration test suite
  (`../backend/test/integration/`) plus manual verification (see above).

## Known limitations

- No automated end-to-end test drives both the real frontend and a live backend
  together in this repo; `pnpm test` covers the frontend's own logic in
  isolation, and the backend's `pytest -m integration` covers the protocol over
  a real broker — the two haven't been wired into a single CI job.
- A page refresh or a new tab always starts a brand-new `session_id` (by design —
  see the backend's Security section on why session ids are never persisted), so
  there's no "resume my previous session" behavior.
