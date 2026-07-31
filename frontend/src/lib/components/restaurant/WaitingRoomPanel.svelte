<script lang="ts">
	import { getRestaurantContext } from '$lib/restaurant/context';

	const engine = getRestaurantContext();
</script>

<div class="line">Welcome to Pouyan's virtual fine dining experience.</div>
<button class="cta" onclick={() => engine.dispatch({ type: 'want-table' })}>I WANT A TABLE</button>

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
