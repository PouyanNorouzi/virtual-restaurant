<script lang="ts">
	import { getRestaurantContext } from '$lib/restaurant/context';
	import WaiterAvatar from './WaiterAvatar.svelte';
	import WaitingRoomPanel from './WaitingRoomPanel.svelte';
	import FullyBookedPanel from './FullyBookedPanel.svelte';
	import AssignedPanel from './AssignedPanel.svelte';

	const engine = getRestaurantContext();

	let visible = $derived(
		engine.state.phase === 'waiting-room' ||
			engine.state.phase === 'full' ||
			engine.state.phase === 'assigned'
	);
</script>

{#if visible}
	<div class="backdrop">
		<div class="panel">
			<div class="who">
				<WaiterAvatar size={44} beard />
				<div class="who-label">the waiter</div>
			</div>

			{#if engine.state.phase === 'waiting-room'}
				<WaitingRoomPanel />
			{:else if engine.state.phase === 'full'}
				<FullyBookedPanel />
			{:else if engine.state.phase === 'assigned'}
				<AssignedPanel />
			{/if}
		</div>
	</div>
{/if}

<style>
	.backdrop {
		position: fixed;
		inset: 0;
		background: oklch(0.1 0.02 40 / 0.72);
		display: flex;
		align-items: center;
		justify-content: center;
		z-index: 20;
		padding: 20px;
	}
	.panel {
		max-width: 460px;
		width: 100%;
		background: var(--rv-panel);
		border: 1px solid var(--rv-border);
		border-radius: 14px;
		padding: 32px;
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: 18px;
		text-align: center;
		box-shadow: 0 20px 60px oklch(0 0 0 / 0.5);
	}
	.who {
		display: flex;
		align-items: center;
		gap: 12px;
	}
	.who-label {
		font-family: 'Playfair Display', serif;
		font-style: italic;
		font-size: 14px;
		color: var(--rv-muted);
	}
</style>
