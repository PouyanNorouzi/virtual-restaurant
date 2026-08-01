/**
 * 'occupied' means some other real customer is there - the backend never
 * exposes another customer's identity, only which table ids are occupied
 * (see restaurant/seating/occupancy in backend/README.md). LocalRestaurantEngine
 * still uses this value for its fake NPC diners, matching the same visual
 * treatment ("someone else is here").
 */
export type TableOccupant = 'occupied' | 'user' | null;

export interface Table {
	occupant: TableOccupant;
	/** Only meaningful for LocalRestaurantEngine's fake NPC departure timer;
	 *  always null when driven by MqttRestaurantEngine, since the backend
	 *  never reveals when another real customer might leave. */
	dineUntil: number | null;
}

export type Phase =
	| 'waiting-room'
	| 'full'
	| 'assigned'
	| 'seated'
	| 'order-modal'
	| 'cooking'
	| 'served'
	| 'post-eat'
	| 'kicked-out';

export interface ToastMsg {
	id: string;
	msg: string;
}

/**
 * The reactive state shared by every engine implementation. This is the
 * shape a websocket server would eventually push down — deliberately does
 * not include `factIndex`, which is local-only presentation state owned by
 * FullyBookedPanel.svelte regardless of which engine is active.
 */
export interface RestaurantSnapshot {
	phase: Phase;
	tables: Table[];
	userTable: number | null;
	orderText: string;
	lastOrder: string;
	cookTotal: number;
	cookSecondsLeft: number;
	queueCount: number;
	toasts: ToastMsg[];
	/** Seconds left before eviction once the backend has warned this session
	 *  someone else needs its table; null when no eviction is pending. */
	kickWarningSecondsLeft: number | null;
}

/**
 * Commands the UI sends. A websocket engine would forward these as outbound
 * messages instead of handling them locally.
 */
export type RestaurantAction =
	| { type: 'want-table' }
	| { type: 'follow' }
	| { type: 'order-open' }
	| { type: 'order-cancel' }
	| { type: 'order-change'; text: string }
	| { type: 'order-submit' }
	| { type: 'eat' };
