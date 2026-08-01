import type { RestaurantEngine } from './engine';
import type { RestaurantAction, RestaurantSnapshot, Table, ToastMsg } from './protocol';

const TABLE_COUNT = 6;

function emptyTable(): Table {
	return { occupant: null, dineUntil: null };
}

/**
 * Client-side simulation of the restaurant. Implements `RestaurantEngine` so
 * it can later be swapped for a websocket-backed engine without touching any
 * UI component — see restaurant/engine.ts.
 */
export class LocalRestaurantEngine implements RestaurantEngine {
	state: RestaurantSnapshot = $state({
		phase: 'waiting-room',
		tables: Array.from({ length: TABLE_COUNT }, emptyTable),
		userTable: null,
		orderText: '',
		lastOrder: '',
		cookTotal: 0,
		cookSecondsLeft: 0,
		queueCount: 0,
		toasts: []
	});

	private npcTimer: ReturnType<typeof setInterval> | undefined;
	private cookTimer: ReturnType<typeof setInterval> | undefined;
	private idleTimer: ReturnType<typeof setTimeout> | undefined;

	start(): void {
		this.npcTimer = setInterval(() => this.tickNpc(), 1500);
	}

	destroy(): void {
		clearInterval(this.npcTimer);
		clearInterval(this.cookTimer);
		clearTimeout(this.idleTimer);
	}

	dispatch(action: RestaurantAction): void {
		switch (action.type) {
			case 'want-table':
				this.onWantTable();
				break;
			case 'follow':
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

	private tickNpc(): void {
		const now = Date.now();
		const tables = this.state.tables.map((t) => {
			if (t.occupant === 'occupied' && t.dineUntil && now > t.dineUntil) return emptyTable();
			if (!t.occupant && Math.random() < 0.3) {
				return { occupant: 'occupied' as const, dineUntil: now + 6000 + Math.random() * 10000 };
			}
			return t;
		});
		this.state.tables = tables;
		if (this.state.phase === 'full') {
			const idx = tables.findIndex((t) => !t.occupant);
			if (idx >= 0) this.seatUser(idx);
		}
	}

	private seatUser(idx: number): void {
		this.state.tables = this.state.tables.map((t, i) =>
			i === idx ? { occupant: 'user' as const, dineUntil: null } : t
		);
		this.state.userTable = idx;
		this.state.phase = 'assigned';
	}

	private pushToast(msg: string): void {
		const toast: ToastMsg = { id: `${Date.now()}-${Math.random()}`, msg };
		this.state.toasts = [...this.state.toasts, toast];
		setTimeout(() => {
			this.state.toasts = this.state.toasts.filter((t) => t.id !== toast.id);
		}, 4000);
	}

	private onWantTable(): void {
		const idx = this.state.tables.findIndex((t) => !t.occupant);
		if (idx >= 0) {
			this.seatUser(idx);
			return;
		}
		this.state.phase = 'full';
	}

	private onOrderSubmit(): void {
		if (this.state.orderText.trim().length === 0) {
			this.pushToast('You have to type something. Even "nothing" counts.');
			return;
		}
		const cookTotal = 10 + Math.floor(Math.random() * 21);
		this.state.cookTotal = cookTotal;
		this.state.cookSecondsLeft = cookTotal;
		this.state.lastOrder = this.state.orderText.trim();
		this.state.phase = 'cooking';
		this.cookTimer = setInterval(() => {
			const left = this.state.cookSecondsLeft - 1;
			if (left <= 0) {
				clearInterval(this.cookTimer);
				this.state.cookSecondsLeft = 0;
				this.state.phase = 'served';
				return;
			}
			this.state.cookSecondsLeft = left;
		}, 1000);
	}

	private onEat(): void {
		const queueCount = Math.random() < 0.4 ? 0 : 1 + Math.floor(Math.random() * 9);
		this.state.queueCount = queueCount;
		this.state.phase = 'post-eat';
		this.idleTimer = setTimeout(() => {
			const userTable = this.state.userTable;
			if (userTable != null) {
				this.state.tables = this.state.tables.map((t, i) => (i === userTable ? emptyTable() : t));
			}
			this.state.userTable = null;
			this.state.phase = 'kicked-out';
		}, 20000);
	}
}
