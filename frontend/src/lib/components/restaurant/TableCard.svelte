<script lang="ts">
	import type { TableStyle } from '$lib/restaurant/table-style';

	interface Props {
		style: TableStyle;
	}

	let { style }: Props = $props();
</script>

<div
	class="table"
	class:pulse={style.animated}
	style:background={style.bg}
	style:border={style.border}
	style:opacity={style.opacity}
>
	<div class="label">Table {style.label}</div>
	<div class="dots">
		<span class="dot"></span>
		<span class="dot"></span>
		<span class="dot"></span>
		<span class="dot"></span>
	</div>
</div>

<style>
	.table {
		width: 120px;
		height: 88px;
		border-radius: 10px;
		display: flex;
		flex-direction: column;
		align-items: center;
		justify-content: center;
		gap: 6px;
		transition: all 0.5s ease;
	}
	.pulse {
		animation: rv-ring-pulse 2s ease-in-out infinite;
	}
	.label {
		font-family: 'Playfair Display', serif;
		font-size: 15px;
		color: oklch(0.9 0.01 80);
	}
	.dots {
		display: flex;
		gap: 5px;
	}
	.dot {
		width: 7px;
		height: 7px;
		border-radius: 50%;
		background: var(--rv-faint);
	}
</style>
