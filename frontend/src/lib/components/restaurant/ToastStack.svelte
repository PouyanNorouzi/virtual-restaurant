<script lang="ts">
	import { getRestaurantContext } from '$lib/restaurant/context';
	import ToastItem from './ToastItem.svelte';

	const engine = getRestaurantContext();
</script>

<div class="stack">
	{#each engine.state.toasts as toast (toast.id)}
		<ToastItem {toast} />
	{/each}
</div>

<style>
	.stack {
		position: fixed;
		bottom: 20px;
		right: 20px;
		display: flex;
		flex-direction: column;
		gap: 10px;
		z-index: 50;
	}
</style>
