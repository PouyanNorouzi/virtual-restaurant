<script lang="ts">
	import { getRestaurantContext } from '$lib/restaurant/context';

	const engine = getRestaurantContext();

	let assignedLabel = $derived(engine.state.userTable != null ? engine.state.userTable + 1 : '');
</script>

<div class="line">
	There's a space at table {assignedLabel}. Follow me, and try to look impressed.
</div>
<button class="cta" onclick={() => engine.dispatch({ type: 'follow' })}>FOLLOW</button>

<style>
	.line {
		font-family: 'Playfair Display', serif;
		font-size: 20px;
		line-height: 1.4;
	}
	.cta {
		background: var(--rv-accent);
		color: var(--rv-accent-text);
		border: none;
		padding: 12px 28px;
		border-radius: 8px;
		font-size: 15px;
		font-weight: 600;
		cursor: pointer;
		letter-spacing: 0.03em;
	}
</style>
