<script lang="ts">
	import type { ToastMsg } from '$lib/restaurant/protocol';

	interface Props {
		toast: ToastMsg;
	}

	let { toast }: Props = $props();
</script>

<div class="toast">{toast.msg}</div>

<style>
	.toast {
		background: var(--rv-danger);
		color: var(--rv-danger-text);
		padding: 12px 18px;
		border-radius: 8px;
		font-size: 14px;
		max-width: 280px;
		box-shadow: 0 8px 24px oklch(0 0 0 / 0.4);
		animation: rv-toast-in 0.25s ease;
	}
</style>
