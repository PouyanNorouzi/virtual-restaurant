<script lang="ts">
	interface Props {
		size?: number;
		beard?: boolean;
	}

	let { size = 44, beard = false }: Props = $props();

	let faceTop = $derived(size * 0.2);
	let faceLeft = $derived(size * 0.2);
	let faceWidth = $derived(size * 0.6);
	let faceHeight = $derived(size * 0.5);
	let beardBottom = $derived(size * 0.136);
	let beardLeft = $derived(size * 0.341);
	let beardWidth = $derived(size * 0.318);
	let beardHeight = $derived(size * 0.136);
</script>

<div class="avatar" style:width="{size}px" style:height="{size}px">
	<div
		class="face"
		style:top="{faceTop}px"
		style:left="{faceLeft}px"
		style:width="{faceWidth}px"
		style:height="{faceHeight}px"
	></div>
	{#if beard}
		<div
			class="beard"
			style:bottom="{beardBottom}px"
			style:left="{beardLeft}px"
			style:width="{beardWidth}px"
			style:height="{beardHeight}px"
		></div>
	{/if}
</div>

<style>
	.avatar {
		border-radius: 50%;
		background: oklch(0.5 0.03 60);
		position: relative;
		flex-shrink: 0;
	}
	.face {
		position: absolute;
		border-radius: 50% 50% 40% 40%;
		background: oklch(0.72 0.1 80);
	}
	.beard {
		position: absolute;
		background: var(--rv-danger);
		clip-path: polygon(0 0, 100% 0, 50% 100%);
	}
</style>
