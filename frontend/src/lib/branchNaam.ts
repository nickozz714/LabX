/**
 * branchNaam.ts — van een titel naar een branchnaam.
 *
 * Deze vertaling staat ook in de backend (`services/lab/branch_naam.py`), en
 * dat is met opzet: het scherm laat vooraf zien hoe de branch gaat heten, en
 * de backend maakt hem werkelijk aan. Lopen ze uiteen, dan push je naar een
 * andere branch dan er op je scherm stond — en dat merk je pas achteraf.
 * Wijzig je er één, wijzig dan allebei; de tests aan beide kanten dekken
 * dezelfde voorbeelden af.
 */

/** Waar een nieuwe branch standaard onder valt. Geen regel, een gewoonte. */
export const VOORVOEGSEL = "feature/";
const MAX_LENGTE = 80;

/** De kale naam zonder voorvoegsel, of "" als er niets bruikbaars in zit. */
export function slug(titel: string): string {
  const tekst = (titel || "")
    .normalize("NFKD")
    // eslint-disable-next-line no-control-regex
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/-{2,}/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, MAX_LENGTE)
    .replace(/^-+|-+$/g, "");
  // Een naam die `lock` heet botst met git's eigen lockbestanden.
  return tekst === "lock" ? "" : tekst;
}

export function uitTitel(titel: string, voorvoegsel = VOORVOEGSEL): string {
  const kern = slug(titel);
  return kern ? `${voorvoegsel}${kern}` : "";
}
