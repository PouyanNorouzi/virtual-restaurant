<script lang="ts">
	import { getRestaurantContext } from '$lib/restaurant/context';

	const engine = getRestaurantContext();

	let message = $derived(
		engine.state.queueCount === 0
			? 'Please leave anyway, or I will have to kick you out.'
			: `There are currently ${engine.state.queueCount} people in the queue — please leave.`
	);
</script>

<div class="line">That'll be nothing — this was never actually food.</div>
<div class="message">{message}</div>

<style>
	.line {
		font-size: 15px;
		line-height: 1.4;
	}
	.message {
		font-size: 14px;
		color: var(--rv-muted-strong);
	}
</style>
