<script lang="ts">
	import { getRestaurantContext } from '$lib/restaurant/context';

	const engine = getRestaurantContext();
</script>

{#if engine.state.kickWarningSecondsLeft != null}
	<div class="banner">
		Someone's waiting for your table — you have {engine.state.kickWarningSecondsLeft}s to wrap up,
		or you'll be asked to leave.
	</div>
{/if}

<style>
	.banner {
		position: fixed;
		top: 20px;
		left: 50%;
		transform: translateX(-50%);
		background: var(--rv-danger);
		color: var(--rv-danger-text);
		padding: 10px 20px;
		border-radius: 8px;
		font-size: 14px;
		text-align: center;
		max-width: 90vw;
		box-shadow: 0 8px 24px oklch(0 0 0 / 0.4);
		z-index: 60;
	}
</style>
