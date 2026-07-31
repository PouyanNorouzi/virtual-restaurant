import type { RestaurantAction, RestaurantSnapshot } from './protocol';

/**
 * Contract every restaurant engine implements. `LocalRestaurantEngine` runs
 * the simulation client-side; a future `WsRestaurantEngine` would speak the
 * same interface over a websocket. UI components only depend on this.
 */
export interface RestaurantEngine {
	readonly state: RestaurantSnapshot;
	dispatch(action: RestaurantAction): void;
	start(): void;
	destroy(): void;
}
