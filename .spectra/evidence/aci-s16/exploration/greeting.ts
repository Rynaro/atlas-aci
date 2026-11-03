// Saudação — "café" 😀 with non-ASCII before identifiers.
export interface Pessoa { nome: string }
export const emoji = "😀"; export function saudação(p: Pessoa): string { return `Olá ${p.nome} ${emoji}`; }
export class Café { brew(): string { return saudação({ nome: "Zoë" }); } }
