<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import { FACTS } from '$lib/restaurant/facts';

	let factIndex = $state(0);
	let timer: ReturnType<typeof setInterval> | undefined;

	onMount(() => {
		timer = setInterval(() => {
			factIndex = (factIndex + 1) % FACTS.length;
		}, 4000);
	});

	onDestroy(() => clearInterval(timer));

	let factText = $derived(FACTS[factIndex % FACTS.length]);
</script>

<div class="line">We are, regrettably, fully booked right now.</div>
{#key factIndex}
	<div class="fact">{factText}</div>
{/key}
<div class="note">We'll seat you the moment a table opens up.</div>

<style>
	.line {
		font-size: 15px;
		color: var(--rv-muted-strong);
	}
	.fact {
		font-family: 'Playfair Display', serif;
		font-size: 18px;
		font-style: italic;
		line-height: 1.5;
		min-height: 52px;
		animation: rv-fact-fade 0.5s ease;
	}
	.note {
		font-size: 12px;
		color: var(--rv-muted);
	}
</style>
