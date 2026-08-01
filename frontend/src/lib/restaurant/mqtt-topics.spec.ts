import { describe, expect, it } from 'vitest';
import {
	OCCUPANCY_TOPIC,
	foodTopic,
	orderAcceptedTopic,
	orderRejectedTopic,
	orderTopic,
	seatingFinishedTopic,
	seatingRequestTopic,
	seatingStatusTopic,
	seatingVacateTopic
} from './mqtt-topics';

describe('mqtt-topics', () => {
	it('builds order-related topics from a table id', () => {
		expect(orderTopic(3)).toBe('restaurant/table/3/order');
		expect(orderAcceptedTopic(3)).toBe('restaurant/table/3/order/accepted');
		expect(foodTopic(3)).toBe('restaurant/table/3/food');
		expect(orderRejectedTopic(3)).toBe('restaurant/table/3/order/rejected');
	});

	it('builds seating topics from a session id', () => {
		const sessionId = 'sess-abc';
		expect(seatingRequestTopic(sessionId)).toBe('restaurant/seating/sess-abc/request');
		expect(seatingVacateTopic(sessionId)).toBe('restaurant/seating/sess-abc/vacate');
		expect(seatingFinishedTopic(sessionId)).toBe('restaurant/seating/sess-abc/finished');
		expect(seatingStatusTopic(sessionId)).toBe('restaurant/seating/sess-abc/status');
	});

	it('exposes a fixed occupancy topic', () => {
		expect(OCCUPANCY_TOPIC).toBe('restaurant/seating/occupancy');
	});
});
