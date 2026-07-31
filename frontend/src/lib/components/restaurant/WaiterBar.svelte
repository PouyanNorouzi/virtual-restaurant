<script lang="ts">
	import { getRestaurantContext } from '$lib/restaurant/context';
	import WaiterAvatar from './WaiterAvatar.svelte';
	import SeatedPrompt from './SeatedPrompt.svelte';
	import ServedPrompt from './ServedPrompt.svelte';
	import PostEatPrompt from './PostEatPrompt.svelte';

	const engine = getRestaurantContext();

	let visible = $derived(
		engine.state.phase === 'seated' ||
			engine.state.phase === 'served' ||
			engine.state.phase === 'post-eat'
	);
</script>

{#if visible}
	<div class="dock">
		<div class="bar">
			<WaiterAvatar size={40} />
			<div class="content">
				{#if engine.state.phase === 'seated'}
					<SeatedPrompt />
				{:else if engine.state.phase === 'served'}
					<ServedPrompt />
				{:else if engine.state.phase === 'post-eat'}
					<PostEatPrompt />
				{/if}
			</div>
		</div>
	</div>
{/if}

<style>
	.dock {
		position: fixed;
		left: 0;
		right: 0;
		bottom: 0;
		display: flex;
		justify-content: center;
		padding: 24px;
		z-index: 15;
	}
	.bar {
		max-width: 520px;
		width: 100%;
		background: var(--rv-panel);
		border: 1px solid var(--rv-border);
		border-radius: 14px;
		padding: 20px 26px;
		display: flex;
		align-items: center;
		gap: 18px;
		box-shadow: 0 10px 40px oklch(0 0 0 / 0.4);
	}
	.content {
		flex: 1;
		display: flex;
		flex-direction: column;
		gap: 10px;
	}
</style>
