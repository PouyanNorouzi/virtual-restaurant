/**
 * MQTT topic builders, mirroring backend/src/mqtt/topics.py. Kept as pure
 * functions (no mqtt.js import here) so they're trivially unit-testable
 * and so mqtt-engine.svelte.ts is the only file that needs to know about
 * the actual MQTT client.
 */

export function orderTopic(tableId: number): string {
	return `restaurant/table/${tableId}/order`;
}

export function orderAcceptedTopic(tableId: number): string {
	return `restaurant/table/${tableId}/order/accepted`;
}

export function foodTopic(tableId: number): string {
	return `restaurant/table/${tableId}/food`;
}

export function orderRejectedTopic(tableId: number): string {
	return `restaurant/table/${tableId}/order/rejected`;
}

export function seatingRequestTopic(sessionId: string): string {
	return `restaurant/seating/${sessionId}/request`;
}

export function seatingVacateTopic(sessionId: string): string {
	return `restaurant/seating/${sessionId}/vacate`;
}

export function seatingFinishedTopic(sessionId: string): string {
	return `restaurant/seating/${sessionId}/finished`;
}

export function seatingStatusTopic(sessionId: string): string {
	return `restaurant/seating/${sessionId}/status`;
}

export const OCCUPANCY_TOPIC = 'restaurant/seating/occupancy';
