<script lang="ts">
	import { getRestaurantContext } from '$lib/restaurant/context';

	const engine = getRestaurantContext();

	let visible = $derived(engine.state.phase === 'order-modal');

	function cancel() {
		engine.dispatch({ type: 'order-cancel' });
	}

	function onInput(e: Event) {
		const target = e.currentTarget as HTMLTextAreaElement;
		engine.dispatch({ type: 'order-change', text: target.value });
	}

	function submit() {
		engine.dispatch({ type: 'order-submit' });
	}
</script>

{#if visible}
	<div class="backdrop" onclick={cancel} role="presentation">
		<div class="panel" onclick={(e) => e.stopPropagation()} role="presentation">
			<div class="title">What would you like to virtually eat today?</div>
			<textarea
				value={engine.state.orderText}
				oninput={onInput}
				maxlength="100"
				rows="3"
				placeholder="e.g. a sandwich made of regret"></textarea>
			<div class="footer">
				<div class="count">{engine.state.orderText.length}/100</div>
				<div class="actions">
					<button class="ghost" onclick={cancel}>Cancel</button>
					<button class="cta" onclick={submit}>SUBMIT</button>
				</div>
			</div>
		</div>
	</div>
{/if}

<style>
	.backdrop {
		position: fixed;
		inset: 0;
		background: oklch(0.1 0.02 40 / 0.75);
		display: flex;
		align-items: center;
		justify-content: center;
		z-index: 30;
		padding: 20px;
	}
	.panel {
		max-width: 440px;
		width: 100%;
		background: var(--rv-panel);
		border: 1px solid var(--rv-border);
		border-radius: 14px;
		padding: 28px;
		display: flex;
		flex-direction: column;
		gap: 14px;
		box-shadow: 0 20px 60px oklch(0 0 0 / 0.5);
	}
	.title {
		font-family: 'Playfair Display', serif;
		font-size: 19px;
	}
	textarea {
		resize: none;
		background: var(--rv-bg);
		border: 1px solid var(--rv-border-strong);
		border-radius: 8px;
		color: var(--rv-text);
		font-family: 'Work Sans', sans-serif;
		font-size: 15px;
		padding: 12px;
		outline: none;
	}
	.footer {
		display: flex;
		justify-content: space-between;
		align-items: center;
	}
	.count {
		font-size: 12px;
		color: var(--rv-faint);
	}
	.actions {
		display: flex;
		gap: 10px;
	}
	.ghost {
		background: transparent;
		color: var(--rv-muted);
		border: 1px solid var(--rv-border-strong);
		padding: 9px 18px;
		border-radius: 8px;
		font-size: 14px;
		cursor: pointer;
	}
	.cta {
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
