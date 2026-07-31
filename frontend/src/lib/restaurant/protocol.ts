export type TableOccupant = 'npc' | 'user' | null;

export interface Table {
	occupant: TableOccupant;
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
