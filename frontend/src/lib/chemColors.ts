/**
 * Chart colours for chemicals. Colour follows the chemical, never its rank:
 * the five most-used forestry herbicides always wear the same hue on every
 * page and filter, and everything else is grey "Other". Validated as a set
 * against the page surface (#131a2e) for colour-blind separation and contrast.
 */
export const FIXED_CHEMICAL_COLORS: Array<[string, string]> = [
  ["Glyphosate", "#3987e5"],
  ["Imazapyr", "#d95926"],
  ["Triclopyr", "#199e70"],
  ["Hexazinone", "#c98500"],
  ["2,4-D", "#d55181"],
];
export const OTHER_COLOR = "#6b7591";

export function colorFor(label: string): string {
  return FIXED_CHEMICAL_COLORS.find(([name]) => name === label)?.[1] ?? OTHER_COLOR;
}
