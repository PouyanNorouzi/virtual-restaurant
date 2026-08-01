<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import { PUBLIC_ENGINE_MODE } from '$env/static/public';
	import { LocalRestaurantEngine } from '$lib/restaurant/local-engine.svelte';
	import { MqttRestaurantEngine } from '$lib/restaurant/mqtt-engine.svelte';
	import { setRestaurantContext } from '$lib/restaurant/context';
	import RestaurantHeader from './RestaurantHeader.svelte';
	import TableFloor from './TableFloor.svelte';
	import WaiterOverlay from './WaiterOverlay.svelte';
	import WaiterBar from './WaiterBar.svelte';
	import CookingCard from './CookingCard.svelte';
	import OrderModal from './OrderModal.svelte';
	import KickedOutScreen from './KickedOutScreen.svelte';
	import ToastStack from './ToastStack.svelte';

	// 'local' opts into the fully client-side simulation (no broker needed);
	// anything else, including unset, uses the real networked backend.
	const engine =
		PUBLIC_ENGINE_MODE === 'local' ? new LocalRestaurantEngine() : new MqttRestaurantEngine();
	setRestaurantContext(engine);

	onMount(() => engine.start());
	onDestroy(() => engine.destroy());
</script>

<svelte:head>
	<link rel="preconnect" href="https://fonts.googleapis.com" />
	<link
		href="https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,500;0,700;1,500&family=Work+Sans:wght@400;500;600&display=swap"
		rel="stylesheet"
	/>
</svelte:head>

<div class="room">
	<RestaurantHeader />
	<TableFloor />
	<WaiterOverlay />
	<WaiterBar />
	<CookingCard />
	<OrderModal />
	<KickedOutScreen />
	<ToastStack />
</div>

<style>
	.room {
		min-height: 100vh;
		width: 100%;
		background: radial-gradient(
			ellipse at 50% -10%,
			var(--rv-bg-radial-start),
			var(--rv-bg-radial-end) 60%
		);
		font-family: 'Work Sans', sans-serif;
		color: var(--rv-text);
		display: flex;
		flex-direction: column;
		position: relative;
		overflow: hidden;
	}
</style>
