<script lang="ts">
	import { getRestaurantContext } from '$lib/restaurant/context';

	const engine = getRestaurantContext();
</script>

<div class="line">Is the table ready to order?</div>
<button class="cta" onclick={() => engine.dispatch({ type: 'order-open' })}>ORDER</button>

<style>
	.line {
		font-size: 15px;
		line-height: 1.4;
	}
	.cta {
		align-self: flex-start;
		background: var(--rv-accent);
		color: var(--rv-accent-text);
		border: none;
		padding: 9px 22px;
		border-radius: 8px;
		font-size: 14px;
		font-weight: 600;
		cursor: pointer;
	}
</style>
