import { beforeEach, describe, expect, it, vi } from 'vitest';
import { OCCUPANCY_TOPIC } from './mqtt-topics';
import { MqttRestaurantEngine } from './mqtt-engine.svelte';

// Fake mqtt.js client: captures event handlers so tests can simulate
// server messages, and records subscribe/publish/end calls to assert on.
type Handler = (...args: unknown[]) => void;

function createFakeClient() {
	const handlers: Record<string, Handler[]> = {};
	return {
		on: vi.fn((event: string, handler: Handler) => {
			(handlers[event] ??= []).push(handler);
		}),
		subscribe: vi.fn(),
		unsubscribe: vi.fn(),
		publish: vi.fn(),
		end: vi.fn(),
		emit(event: string, ...args: unknown[]) {
			for (const h of handlers[event] ?? []) h(...args);
		}
	};
}

let fakeClient: ReturnType<typeof createFakeClient>;
const connectMock = vi.fn<(...args: unknown[]) => ReturnType<typeof createFakeClient>>(
	() => fakeClient
);

vi.mock('mqtt', () => ({
	default: { connect: (...args: unknown[]) => connectMock(...args) }
}));

// Payload objects only need toString() - avoids depending on Buffer/Node
// APIs that may not exist in the browser test environment.
function jsonPayload(value: unknown) {
	return { toString: () => JSON.stringify(value) };
}

function startedEngine() {
	const engine = new MqttRestaurantEngine();
	engine.start();
	fakeClient.emit('connect');
	return engine;
}

function statusTopicFromFirstSubscribe(): string {
	const [topics] = fakeClient.subscribe.mock.calls[0] as [string[]];
	const found = topics.find((t) => t !== OCCUPANCY_TOPIC);
	if (!found) throw new Error('status topic not found in first subscribe call');
	return found;
}

beforeEach(() => {
	fakeClient = createFakeClient();
	connectMock.mockClear();
});

describe('MqttRestaurantEngine', () => {
	it('connects and subscribes to its own status topic plus occupancy on start', async () => {
		startedEngine();

		expect(connectMock).toHaveBeenCalledTimes(1);
		const [topics] = fakeClient.subscribe.mock.calls[0] as [string[]];
		expect(topics).toContain(OCCUPANCY_TOPIC);
		expect(topics.some((t) => t.startsWith('restaurant/seating/') && t.endsWith('/status'))).toBe(
			true
		);
	});

	it('moves to the assigned phase and subscribes to that table on an assigned status', async () => {
		const engine = startedEngine();
		const statusTopic = statusTopicFromFirstSubscribe();

		fakeClient.emit('message', statusTopic, jsonPayload({ state: 'assigned', table_id: 2 }));

		expect(engine.state.phase).toBe('assigned');
		expect(engine.state.userTable).toBe(1); // 0-indexed
		const subscribedTopics = fakeClient.subscribe.mock.calls.flatMap(([t]) => t as string[]);
		expect(subscribedTopics).toContain('restaurant/table/2/order/accepted');
		expect(subscribedTopics).toContain('restaurant/table/2/food');
		expect(subscribedTopics).toContain('restaurant/table/2/order/rejected');
	});

	it('moves to the full phase on a queued status', async () => {
		const engine = startedEngine();
		const statusTopic = statusTopicFromFirstSubscribe();

		fakeClient.emit('message', statusTopic, jsonPayload({ state: 'queued', queue_position: 2 }));

		expect(engine.state.phase).toBe('full');
	});

	it('builds the tables array from occupancy, marking its own table as user', async () => {
		const engine = startedEngine();
		const statusTopic = statusTopicFromFirstSubscribe();
		fakeClient.emit('message', statusTopic, jsonPayload({ state: 'assigned', table_id: 2 }));

		fakeClient.emit(
			'message',
			OCCUPANCY_TOPIC,
			jsonPayload({ occupied_tables: [1, 2], num_tables: 3, queue_length: 4 })
		);

		expect(engine.state.tables).toEqual([
			{ occupant: 'occupied', dineUntil: null }, // table 1: someone else
			{ occupant: 'user', dineUntil: null }, // table 2: us
			{ occupant: null, dineUntil: null } // table 3: free
		]);
		expect(engine.state.queueCount).toBe(4);
	});

	it('publishes an order with a fresh client_order_id and its own session_id, and stays put until accepted', async () => {
		const engine = startedEngine();
		const statusTopic = statusTopicFromFirstSubscribe();
		fakeClient.emit('message', statusTopic, jsonPayload({ state: 'assigned', table_id: 1 }));

		engine.dispatch({ type: 'order-change', text: 'ramen' });
		engine.dispatch({ type: 'order-submit' });

		const publishCall = fakeClient.publish.mock.calls.find(
			([topic]) => topic === 'restaurant/table/1/order'
		);
		expect(publishCall).toBeDefined();
		const [, payload] = publishCall as [string, string];
		const parsed = JSON.parse(payload);
		expect(parsed.food_name).toBe('ramen');
		expect(parsed.client_order_id).toMatch(/^[0-9a-f-]{36}$/);
		expect(typeof parsed.session_id).toBe('string');
		// Phase doesn't move to 'cooking' until order.accepted.v1 arrives.
		expect(engine.state.phase).not.toBe('cooking');
	});

	it('moves to cooking with the real prep_seconds once accepted, then to served on food', async () => {
		const engine = startedEngine();
		const statusTopic = statusTopicFromFirstSubscribe();
		fakeClient.emit('message', statusTopic, jsonPayload({ state: 'assigned', table_id: 1 }));
		engine.dispatch({ type: 'order-change', text: 'ramen' });
		engine.dispatch({ type: 'order-submit' });

		const [, payload] = fakeClient.publish.mock.calls.find(
			([topic]) => topic === 'restaurant/table/1/order'
		) as [string, string];
		const clientOrderId = JSON.parse(payload).client_order_id;

		fakeClient.emit(
			'message',
			'restaurant/table/1/order/accepted',
			jsonPayload({ client_order_id: clientOrderId, table_id: 1, prep_seconds: 17.5 })
		);
		expect(engine.state.phase).toBe('cooking');
		expect(engine.state.cookTotal).toBe(17.5);
		expect(engine.state.cookSecondsLeft).toBe(17.5);

		fakeClient.emit(
			'message',
			'restaurant/table/1/food',
			jsonPayload({ client_order_id: clientOrderId, food_name: 'ramen' })
		);
		expect(engine.state.phase).toBe('served');
		expect(engine.state.lastOrder).toBe('ramen');
	});

	it('ignores accepted/food events for a stale client_order_id', async () => {
		const engine = startedEngine();
		const statusTopic = statusTopicFromFirstSubscribe();
		fakeClient.emit('message', statusTopic, jsonPayload({ state: 'assigned', table_id: 1 }));
		engine.dispatch({ type: 'order-change', text: 'ramen' });
		engine.dispatch({ type: 'order-submit' });

		fakeClient.emit(
			'message',
			'restaurant/table/1/order/accepted',
			jsonPayload({ client_order_id: 'not-the-right-id', table_id: 1, prep_seconds: 99 })
		);

		expect(engine.state.phase).not.toBe('cooking');
	});

	it('shows a toast and returns to order-modal on a validation rejection', async () => {
		const engine = startedEngine();
		const statusTopic = statusTopicFromFirstSubscribe();
		fakeClient.emit('message', statusTopic, jsonPayload({ state: 'assigned', table_id: 1 }));
		engine.dispatch({ type: 'order-change', text: 'ramen' });
		engine.dispatch({ type: 'order-submit' });
		const [, payload] = fakeClient.publish.mock.calls.find(
			([topic]) => topic === 'restaurant/table/1/order'
		) as [string, string];
		const clientOrderId = JSON.parse(payload).client_order_id;

		fakeClient.emit(
			'message',
			'restaurant/table/1/order/rejected',
			jsonPayload({ client_order_id: clientOrderId, reason: 'food_name_too_long' })
		);

		expect(engine.state.phase).toBe('order-modal');
		expect(engine.state.toasts).toHaveLength(1);
	});

	it('resyncs by re-requesting a seat on a not_seated_at_table rejection', async () => {
		const engine = startedEngine();
		const statusTopic = statusTopicFromFirstSubscribe();
		fakeClient.emit('message', statusTopic, jsonPayload({ state: 'assigned', table_id: 1 }));
		engine.dispatch({ type: 'order-change', text: 'ramen' });
		engine.dispatch({ type: 'order-submit' });
		const [, payload] = fakeClient.publish.mock.calls.find(
			([topic]) => topic === 'restaurant/table/1/order'
		) as [string, string];
		const clientOrderId = JSON.parse(payload).client_order_id;
		fakeClient.publish.mockClear();

		fakeClient.emit(
			'message',
			'restaurant/table/1/order/rejected',
			jsonPayload({ client_order_id: clientOrderId, reason: 'not_seated_at_table' })
		);

		expect(engine.state.phase).toBe('waiting-room');
		expect(engine.state.userTable).toBeNull();
		expect(
			fakeClient.publish.mock.calls.some(
				([topic]) => topic === statusTopic.replace('status', 'request')
			)
		).toBe(true);
	});

	it('publishes a finished signal (not vacate) on eat, and moves to post-eat', async () => {
		const engine = startedEngine();
		const statusTopic = statusTopicFromFirstSubscribe();
		fakeClient.emit('message', statusTopic, jsonPayload({ state: 'assigned', table_id: 1 }));

		engine.dispatch({ type: 'eat' });

		expect(engine.state.phase).toBe('post-eat');
		const finishedCall = fakeClient.publish.mock.calls.find(
			([topic]) => topic === statusTopic.replace('status', 'finished')
		);
		expect(finishedCall).toBeDefined();
		const vacateCall = fakeClient.publish.mock.calls.find(
			([topic]) => topic === statusTopic.replace('status', 'vacate')
		);
		expect(vacateCall).toBeUndefined();
	});

	it('moves to kicked-out when a vacated status arrives (eviction)', async () => {
		const engine = startedEngine();
		const statusTopic = statusTopicFromFirstSubscribe();
		fakeClient.emit('message', statusTopic, jsonPayload({ state: 'assigned', table_id: 1 }));
		engine.dispatch({ type: 'eat' });

		fakeClient.emit('message', statusTopic, jsonPayload({ state: 'vacated' }));

		expect(engine.state.phase).toBe('kicked-out');
		expect(engine.state.userTable).toBeNull();
	});

	it('publishes an explicit vacate on destroy when currently seated', async () => {
		const engine = startedEngine();
		const statusTopic = statusTopicFromFirstSubscribe();
		fakeClient.emit('message', statusTopic, jsonPayload({ state: 'assigned', table_id: 1 }));

		engine.destroy();

		const vacateCall = fakeClient.publish.mock.calls.find(
			([topic]) => topic === statusTopic.replace('status', 'vacate')
		);
		expect(vacateCall).toBeDefined();
		expect(JSON.parse((vacateCall as [string, string])[1]).reason).toBe('user_action');
		expect(fakeClient.end).toHaveBeenCalled();
	});
});
