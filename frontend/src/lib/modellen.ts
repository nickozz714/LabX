/**
 * lib/modellen.ts — de modelkeuzes, op één plek.
 *
 * Stond eerst alleen in ChatPage; sinds een lab zelf een model kan hebben
 * staan dezelfde keuzes op twee schermen, en twee lijstjes die uit elkaar
 * lopen is precies hoe je een model aanbiedt dat de CLI niet kent.
 */
export const MODEL_OPTIONS = [
  { value: "", label: "Standaard (instellingen)" },
  { value: "sonnet", label: "Sonnet" },
  { value: "opus", label: "Opus" },
  { value: "fable", label: "Fable" },
  { value: "haiku", label: "Haiku" },
];

/** Zoals het in een badge of regel hoort te lezen: leeg = de standaard. */
export function modelLabel(waarde: string | null | undefined): string {
  if (!waarde) return "standaard";
  return MODEL_OPTIONS.find((m) => m.value === waarde)?.label ?? waarde;
}
