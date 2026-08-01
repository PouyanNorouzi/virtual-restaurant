<script lang="ts">
	import { getRestaurantContext } from '$lib/restaurant/context';

	const engine = getRestaurantContext();

	let visible = $derived(engine.state.phase === 'cooking');
	let cookPercent = $derived(
		engine.state.cookTotal
			? Math.round(
					((engine.state.cookTotal - engine.state.cookSecondsLeft) / engine.state.cookTotal) * 100
				)
			: 0
	);
	let secondsLeftDisplay = $derived(Math.ceil(engine.state.cookSecondsLeft));
</script>

{#if visible}
	<div class="dock">
		<div class="card">
			<div class="eyebrow">Ticket in progress</div>
			<div class="summary">"{engine.state.lastOrder}"</div>
			<div class="track">
				<div class="fill" style:width="{cookPercent}%"></div>
			</div>
			<div class="note">Being conceptually prepared — ready in {secondsLeftDisplay}s</div>
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
	.card {
		max-width: 420px;
		width: 100%;
		background: var(--rv-panel);
		border: 1px solid var(--rv-border);
		border-radius: 14px;
		padding: 24px 28px;
		display: flex;
		flex-direction: column;
		gap: 10px;
		box-shadow: 0 10px 40px oklch(0 0 0 / 0.4);
	}
	.eyebrow {
		font-size: 13px;
		letter-spacing: 0.08em;
		text-transform: uppercase;
		color: var(--rv-muted);
	}
	.summary {
		font-family: 'Playfair Display', serif;
		font-style: italic;
		font-size: 16px;
		color: var(--rv-text-dim);
	}
	.track {
		height: 6px;
		border-radius: 3px;
		background: oklch(0.28 0.02 45);
		overflow: hidden;
		margin-top: 6px;
	}
	.fill {
		height: 100%;
		background: var(--rv-accent);
		transition: width 1s linear;
	}
	.note {
		font-size: 13px;
		color: var(--rv-faint);
	}
</style>
