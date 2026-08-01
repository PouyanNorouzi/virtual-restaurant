import mqtt, { type MqttClient } from 'mqtt';
import { PUBLIC_MQTT_CUSTOMER_PASSWORD, PUBLIC_MQTT_URL } from '$env/static/public';
import type { RestaurantEngine } from './engine';
import type { RestaurantAction, RestaurantSnapshot, Table, ToastMsg } from './protocol';
import {
	foodTopic,
	OCCUPANCY_TOPIC,
	orderAcceptedTopic,
	orderRejectedTopic,
	orderTopic,
	seatingFinishedTopic,
	seatingRequestTopic,
	seatingStatusTopic,
	seatingVacateTopic
} from './mqtt-topics';

const REASON_MESSAGES: Record<string, string> = {
	empty_food_name: 'You have to type something. Even "nothing" counts.',
	food_name_too_long: "That's a bit much - try something shorter.",
	unknown_table: 'Something went wrong with your table assignment.',
	too_many_pending_orders: 'Slow down! Wait for your other orders first.',
	not_seated_at_table: 'Looks like you lost your seat - finding you a new one.'
};

function reasonToMessage(reason: string): string {
	return REASON_MESSAGES[reason] ?? 'Your order was rejected.';
}

interface StatusMessage {
	state: 'assigned' | 'queued' | 'vacated';
	table_id?: number;
	queue_position?: number;
}

interface OccupancyMessage {
	occupied_tables: number[];
	num_tables: number;
	queue_length: number;
}

interface AcceptedMessage {
	client_order_id: string;
	table_id: number;
	prep_seconds: number;
}

interface FoodMessage {
	client_order_id: string;
	food_name: string;
}

interface RejectedMessage {
	client_order_id: string;
	reason: string;
}

/**
 * Networked engine speaking MQTT over WebSockets to the real backend (see
 * backend/README.md for the full topic/payload contract). Implements the
 * same RestaurantEngine interface as LocalRestaurantEngine, so every
 * component under DiningRoom works unchanged regardless of which engine is
 * active - see restaurant/engine.ts and DiningRoom.svelte.
 */
export class MqttRestaurantEngine implements RestaurantEngine {
	state: RestaurantSnapshot = $state({
		phase: 'waiting-room',
		tables: [],
		userTable: null,
		orderText: '',
		lastOrder: '',
		cookTotal: 0,
		cookSecondsLeft: 0,
		queueCount: 0,
		toasts: []
	});

	// The session's MQTT Client Identifier MUST equal this session_id - the
	// broker's %c-pattern ACL depends on that equivalence (see
	// backend/mosquitto/acl.conf and backend/README.md's Security section).
	private readonly sessionId = crypto.randomUUID();

	private client: MqttClient | undefined;
	private assignedTableId: number | null = null;
	private numTables = 0;
	private pendingClientOrderId: string | null = null;
	private cookdownTimer: ReturnType<typeof setInterval> | undefined;

	start(): void {
		const willPayload = JSON.stringify({ schema: 'seat.vacate.v1', reason: 'disconnected' });
		this.client = mqtt.connect(PUBLIC_MQTT_URL, {
			clientId: this.sessionId,
			username: 'customer',
			password: PUBLIC_MQTT_CUSTOMER_PASSWORD,
			clean: true,
			reconnectPeriod: 2000,
			connectTimeout: 10000,
			// Well below mqtt.js's 60s default. The pagehide handler below is
			// only a best-effort speedup - browsers don't guarantee a
			// WebSocket write initiated during pagehide/unload actually
			// flushes before the tab's network stack is torn down (unlike
			// sendBeacon/fetch-keepalive, which aren't usable here since
			// this app is MQTT-over-WebSockets only, no HTTP surface). The
			// broker's Will (fired ~1.5x keepalive after the last PINGREQ)
			// is what actually guarantees cleanup, for both tab closes that
			// silently drop the pagehide publish and for crashes/force-quit/
			// killed network that never run JS at all - keeping this short
			// bounds how long a dead session's table looks occupied.
			keepalive: 5,
			will: {
				topic: seatingVacateTopic(this.sessionId),
				payload: willPayload,
				qos: 1,
				retain: false
			}
		});

		this.client.on('connect', () => {
			this.client?.subscribe([seatingStatusTopic(this.sessionId), OCCUPANCY_TOPIC], { qos: 1 });
		});
		this.client.on('message', (topic, payload) => this.onMessage(topic, payload.toString()));
		this.client.on('reconnect', () => this.pushToast('Reconnecting to the restaurant...'));
		this.client.on('offline', () => this.pushToast('Connection lost - reconnecting...'));
		this.client.on('error', (err) => console.error('MQTT connection error', err));

		// A clean MQTT disconnect (what client.end() sends) does NOT trigger
		// the Will, and Svelte's onDestroy only fires on component unmount/
		// navigation, not on an actual browser tab close - so without this,
		// closing a tab leaves the table looking occupied until the Will's
		// keepalive grace period elapses. pagehide fires reliably on tab
		// close across desktop and mobile browsers (unlike beforeunload,
		// which also blocks bfcache).
		window.addEventListener('pagehide', this.vacateAndEnd);
	}

	destroy(): void {
		clearInterval(this.cookdownTimer);
		// start() (and thus the pagehide listener) only ever runs client-side
		// via onMount, but onDestroy can still fire during SSR with no
		// matching onMount - guard against `window` not existing there.
		if (typeof window !== 'undefined') {
			window.removeEventListener('pagehide', this.vacateAndEnd);
		}
		this.vacateAndEnd();
	}

	private readonly vacateAndEnd = (): void => {
		if (!this.client) return;
		if (this.assignedTableId !== null) {
			this.client.publish(
				seatingVacateTopic(this.sessionId),
				JSON.stringify({ schema: 'seat.vacate.v1', reason: 'user_action' }),
				{ qos: 1 }
			);
		}
		this.client.end();
	};

	dispatch(action: RestaurantAction): void {
		switch (action.type) {
			case 'want-table':
				this.publish(seatingRequestTopic(this.sessionId), { schema: 'seat.request.v1' });
				break;
			case 'follow':
				// Purely cosmetic "walk to your table" step - no backend concept.
				this.state.phase = 'seated';
				break;
			case 'order-open':
				this.state.orderText = '';
				this.state.phase = 'order-modal';
				break;
			case 'order-cancel':
				this.state.phase = 'seated';
				break;
			case 'order-change':
				this.state.orderText = action.text.slice(0, 100);
				break;
			case 'order-submit':
				this.onOrderSubmit();
				break;
			case 'eat':
				this.onEat();
				break;
		}
	}

	private onOrderSubmit(): void {
		const text = this.state.orderText.trim();
		if (text.length === 0) {
			this.pushToast('You have to type something. Even "nothing" counts.');
			return;
		}
		if (this.assignedTableId === null) return; // shouldn't happen given the UI flow

		const clientOrderId = crypto.randomUUID();
		this.pendingClientOrderId = clientOrderId;
		this.state.lastOrder = text;
		this.publish(orderTopic(this.assignedTableId), {
			food_name: text,
			client_order_id: clientOrderId,
			session_id: this.sessionId
		});
		// Phase deliberately stays where it is (order-modal) until the
		// backend's order.accepted.v1 event arrives with the real cook
		// duration - see backend/README.md's Accepted payload.
	}

	private onEat(): void {
		this.state.phase = 'post-eat';
		// No local timer: eviction is queue-driven on the backend now, not
		// timer-driven. This session keeps its table indefinitely until a
		// vacated status arrives (see onStatus), which only happens once
		// someone else actually needs the table.
		if (this.assignedTableId !== null) {
			this.publish(seatingFinishedTopic(this.sessionId), { schema: 'seat.finished.v1' });
		}
	}

	private publish(topic: string, payload: unknown): void {
		this.client?.publish(topic, JSON.stringify(payload), { qos: 1 });
	}

	private pushToast(msg: string): void {
		const toast: ToastMsg = { id: `${Date.now()}-${Math.random()}`, msg };
		this.state.toasts = [...this.state.toasts, toast];
		setTimeout(() => {
			this.state.toasts = this.state.toasts.filter((t) => t.id !== toast.id);
		}, 4000);
	}

	private onMessage(topic: string, raw: string): void {
		let payload: unknown;
		try {
			payload = JSON.parse(raw);
		} catch {
			return; // malformed - nothing sensible to do client-side
		}

		if (topic === seatingStatusTopic(this.sessionId)) {
			this.onStatus(payload as StatusMessage);
		} else if (topic === OCCUPANCY_TOPIC) {
			this.onOccupancy(payload as OccupancyMessage);
		} else if (
			this.assignedTableId !== null &&
			topic === orderAcceptedTopic(this.assignedTableId)
		) {
			this.onAccepted(payload as AcceptedMessage);
		} else if (this.assignedTableId !== null && topic === foodTopic(this.assignedTableId)) {
			this.onFood(payload as FoodMessage);
		} else if (
			this.assignedTableId !== null &&
			topic === orderRejectedTopic(this.assignedTableId)
		) {
			this.onRejected(payload as RejectedMessage);
		}
	}

	private onStatus(msg: StatusMessage): void {
		if (msg.state === 'assigned' && msg.table_id != null) {
			this.setAssignedTable(msg.table_id);
			this.state.userTable = msg.table_id - 1;
			this.state.phase = 'assigned';
			return;
		}

		if (msg.state === 'queued') {
			this.state.phase = 'full';
			return;
		}

		// 'vacated' only ever arrives at a still-connected client via the
		// queue-driven eviction path (see backend/README.md's Finished
		// eating section) - a session only publishes vacate for itself when
		// it's already leaving/disconnecting, so it never sees the reply.
		this.setAssignedTable(null);
		this.state.userTable = null;
		this.state.phase = 'kicked-out';
	}

	private setAssignedTable(tableId: number | null): void {
		if (this.assignedTableId === tableId) return;
		if (this.assignedTableId !== null) {
			this.client?.unsubscribe([
				orderAcceptedTopic(this.assignedTableId),
				foodTopic(this.assignedTableId),
				orderRejectedTopic(this.assignedTableId)
			]);
		}
		this.assignedTableId = tableId;
		if (tableId !== null) {
			this.client?.subscribe(
				[orderAcceptedTopic(tableId), foodTopic(tableId), orderRejectedTopic(tableId)],
				{ qos: 1 }
			);
		}
	}

	private onOccupancy(msg: OccupancyMessage): void {
		this.numTables = msg.num_tables;
		const occupied = msg.occupied_tables;
		const tables: Table[] = Array.from({ length: this.numTables }, (_, i) => {
			const tableId = i + 1;
			if (tableId === this.assignedTableId) return { occupant: 'user', dineUntil: null };
			return { occupant: occupied.includes(tableId) ? 'occupied' : null, dineUntil: null };
		});
		this.state.tables = tables;
		this.state.queueCount = msg.queue_length;
	}

	private onAccepted(msg: AcceptedMessage): void {
		if (msg.client_order_id !== this.pendingClientOrderId) return;
		this.state.cookTotal = msg.prep_seconds;
		this.state.cookSecondsLeft = msg.prep_seconds;
		this.state.phase = 'cooking';

		clearInterval(this.cookdownTimer);
		this.cookdownTimer = setInterval(() => {
			// Cosmetic only - the real transition to 'served' happens when
			// the FOOD event actually arrives (onFood), not when this timer
			// reaches zero, since the backend is authoritative on timing.
			this.state.cookSecondsLeft = Math.max(0, this.state.cookSecondsLeft - 1);
		}, 1000);
	}

	private onFood(msg: FoodMessage): void {
		if (msg.client_order_id !== this.pendingClientOrderId) return;
		clearInterval(this.cookdownTimer);
		this.pendingClientOrderId = null;
		this.state.cookSecondsLeft = 0;
		this.state.lastOrder = msg.food_name;
		this.state.phase = 'served';
	}

	private onRejected(msg: RejectedMessage): void {
		if (msg.client_order_id !== this.pendingClientOrderId) return;
		clearInterval(this.cookdownTimer);
		this.pendingClientOrderId = null;
		this.pushToast(reasonToMessage(msg.reason));

		if (msg.reason === 'not_seated_at_table') {
			// State desync (e.g. evicted mid-order) - resync from scratch.
			this.setAssignedTable(null);
			this.state.userTable = null;
			this.state.phase = 'waiting-room';
			this.publish(seatingRequestTopic(this.sessionId), { schema: 'seat.request.v1' });
			return;
		}

		this.state.phase = 'order-modal';
	}
}
