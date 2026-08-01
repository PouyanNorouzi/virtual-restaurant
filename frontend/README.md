# Virtual Restaurant: Frontend

Svelte 5 / SvelteKit UI for the virtual restaurant simulation, driven live over
MQTT by the [backend](../backend/README.md).

## How it's built

The UI is engine-agnostic: every screen under `src/lib/components/restaurant/`
reads from and dispatches actions to a `RestaurantEngine`
(`src/lib/restaurant/engine.ts`) pulled from Svelte context, with no idea
whether that engine talks to a real broker or simulates everything locally.

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

- **`MqttRestaurantEngine`** (`src/lib/restaurant/mqtt-engine.svelte.ts`,
  default): speaks MQTT over WebSockets to the real backend. See
  [`../backend/README.md`](../backend/README.md) for the topic/payload
  contract it implements against.
- **`LocalRestaurantEngine`** (`src/lib/restaurant/local-engine.svelte.ts`): a
  fully self-contained simulation with no network calls, for demoing the UI
  offline.

`src/lib/restaurant/protocol.ts` defines the shared `Phase`/`RestaurantSnapshot`/
`RestaurantAction` shapes both engines produce and consume; components are
built against that contract, not either engine directly.

## Setup

Requires [pnpm](https://pnpm.io) and Node 20+.

```sh
pnpm install
cp .env.example .env   # see "Connecting to the backend" below
pnpm dev                # or: pnpm dev -- --open
```

## Connecting to the backend

Configuration is via env vars (SvelteKit's `$env/static/public`, safe to be
public), set in `.env` (copy from `.env.example`, gitignored):

| Var                             | Purpose                                                        |
| ------------------------------- | -------------------------------------------------------------- |
| `PUBLIC_MQTT_URL`               | Mosquitto's WebSocket listener, e.g. `ws://localhost:9001`     |
| `PUBLIC_MQTT_CUSTOMER_PASSWORD` | Password for the shared `customer` broker account              |
| `PUBLIC_ENGINE_MODE`            | `mqtt` (default, real backend) or `local` (offline simulation) |

`PUBLIC_MQTT_CUSTOMER_PASSWORD` must match the password
`../backend/mosquitto/generate_passwd.sh` set for the `customer` account. It's
intentionally public, baked into the browser bundle by design. Per-customer
isolation comes instead from each tab's random `session_id`, used as its MQTT
Client Identifier and scoped by the broker's ACL; see the backend's Security
section.

### Running the full stack locally

```sh
# one terminal, in backend/
./mosquitto/generate_passwd.sh          # once: sets the backend + customer passwords
export MOSQUITTO_BACKEND_PASSWORD=...   # the "backend" password you just set
docker compose up --build

# another terminal, in frontend/
cp .env.example .env                    # fill in PUBLIC_MQTT_CUSTOMER_PASSWORD to match
pnpm install
pnpm dev
```

Open two browser tabs to see two independent customer sessions interact
(queueing, eviction, etc.); see the backend README's "Finished eating"
section for the details.

## Disconnect handling

A closed tab should free its table promptly. I use two layers in
`MqttRestaurantEngine` (`src/lib/restaurant/mqtt-engine.svelte.ts`):

- **`pagehide` (best effort).** Publishes an explicit vacate and ends the
  connection on tab close/quit/navigation - the same cleanup `destroy()` runs
  on unmount. Not guaranteed: browsers don't promise a WebSocket write
  survives unload (unlike `sendBeacon`, which I can't use since this app is
  MQTT-only).
- **5s `keepalive` (the real guarantee).** Backs it with a broker Last Will,
  so any session that vanishes without a clean disconnect gets vacated within
  ~5-8s regardless. `SeatingService.vacate()` is idempotent, so both layers
  firing for the same session is safe.

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

- `src/lib/restaurant/mqtt-topics.spec.ts`: pure topic-builder unit tests
  (Node, no browser/broker).
- `src/lib/restaurant/mqtt-engine.svelte.spec.ts`: drives
  `MqttRestaurantEngine` against a mocked `mqtt.js` client, asserting on the
  resulting `RestaurantSnapshot` for every inbound message and dispatched
  action. Runs in a real browser via `vitest-browser-svelte` (Svelte 5 runes).
- Not covered here: a real `mqtt.connect()` handshake, broker ACL enforcement,
  or a live seat/order/eviction round trip. Those are exercised by the
  backend's integration suite (`../backend/test/integration/`).

## Known limitations

- No automated end-to-end test drives both the real frontend and a live
  backend together; frontend and backend test suites aren't wired into a
  single CI job.
- A page refresh or new tab always starts a fresh `session_id` (session ids
  are never persisted, by design), so there's no "resume my previous
  session" behavior.
