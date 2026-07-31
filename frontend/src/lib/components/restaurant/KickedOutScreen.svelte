<script lang="ts">
	import { getRestaurantContext } from '$lib/restaurant/context';

	const engine = getRestaurantContext();

	let visible = $derived(engine.state.phase === 'kicked-out');
</script>

{#if visible}
	<div class="screen">
		<div class="message">
			You have been kicked out.<br />Refresh the page if you want to eat again.
		</div>
	</div>
{/if}

<style>
	.screen {
		position: fixed;
		inset: 0;
		background: oklch(0.08 0.01 40);
		display: flex;
		align-items: center;
		justify-content: center;
		z-index: 40;
		padding: 20px;
	}
	.message {
		max-width: 440px;
		text-align: center;
		font-family: 'Playfair Display', serif;
		font-size: 22px;
		line-height: 1.5;
		color: oklch(0.8 0.01 70);
	}
</style>
