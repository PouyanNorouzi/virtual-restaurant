<script lang="ts">
	import { getRestaurantContext } from '$lib/restaurant/context';
	import { tableStyle } from '$lib/restaurant/table-style';
	import TableCard from './TableCard.svelte';

	const engine = getRestaurantContext();

	let floorOpacity = $derived(engine.state.phase === 'kicked-out' ? 0.15 : 1);
</script>

<div class="floor-wrap">
	<div class="grid" style:opacity={floorOpacity}>
		{#each engine.state.tables as table, i (i)}
			<TableCard style={tableStyle(table, i, engine.state.userTable)} />
		{/each}
	</div>
</div>

<style>
	.floor-wrap {
		flex: 1;
		display: flex;
		align-items: center;
		justify-content: center;
		padding: 20px 40px 140px;
		position: relative;
	}
	.grid {
		display: grid;
		grid-template-columns: repeat(3, 1fr);
		gap: 28px;
		transition: opacity 0.6s ease;
	}
</style>
