import type { Table } from './protocol';

export interface TableStyle {
	label: number;
	bg: string;
	border: string;
	opacity: number;
	animated: boolean;
}

export function tableStyle(table: Table, index: number, userTable: number | null): TableStyle {
	const isUser = table.occupant === 'user';
	return {
		label: index + 1,
		bg: isUser
			? 'oklch(0.35 0.09 25)'
			: table.occupant === 'npc'
				? 'oklch(0.30 0.05 60)'
				: 'oklch(0.22 0.02 40)',
		border: isUser ? '2px solid oklch(0.78 0.13 85)' : '1px solid oklch(0.34 0.02 40)',
		opacity: isUser ? 1 : userTable != null ? 0.5 : 1,
		animated: isUser
	};
}
