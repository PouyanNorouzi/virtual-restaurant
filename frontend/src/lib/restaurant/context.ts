import { getContext, setContext } from 'svelte';
import type { RestaurantEngine } from './engine';

const RESTAURANT_ENGINE_KEY = Symbol('restaurant-engine');

export function setRestaurantContext(engine: RestaurantEngine): void {
	setContext(RESTAURANT_ENGINE_KEY, engine);
}

export function getRestaurantContext(): RestaurantEngine {
	const engine = getContext<RestaurantEngine>(RESTAURANT_ENGINE_KEY);
	if (!engine) throw new Error('getRestaurantContext() called outside <DiningRoom>');
	return engine;
}
