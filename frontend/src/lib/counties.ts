/**
 * All 58 California counties.
 *
 * The API only knows counties that have published records. The site is
 * statewide, so every county gets a page from day one: with records it shows
 * them, without it says so plainly and explains how records get here. Slugs
 * follow the importer's rule (lower case, spaces to hyphens) so the two agree.
 */
export const CALIFORNIA_COUNTIES: string[] = [
  "Alameda", "Alpine", "Amador", "Butte", "Calaveras", "Colusa", "Contra Costa",
  "Del Norte", "El Dorado", "Fresno", "Glenn", "Humboldt", "Imperial", "Inyo", "Kern",
  "Kings", "Lake", "Lassen", "Los Angeles", "Madera", "Marin", "Mariposa", "Mendocino",
  "Merced", "Modoc", "Mono", "Monterey", "Napa", "Nevada", "Orange", "Placer", "Plumas",
  "Riverside", "Sacramento", "San Benito", "San Bernardino", "San Diego", "San Francisco",
  "San Joaquin", "San Luis Obispo", "San Mateo", "Santa Barbara", "Santa Clara",
  "Santa Cruz", "Shasta", "Sierra", "Siskiyou", "Solano", "Sonoma", "Stanislaus",
  "Sutter", "Tehama", "Trinity", "Tulare", "Tuolumne", "Ventura", "Yolo", "Yuba",
];

export function countySlug(name: string): string {
  return name.trim().toLowerCase().replace(/\s+/g, "-");
}

export function countyName(slug: string): string | null {
  return CALIFORNIA_COUNTIES.find((name) => countySlug(name) === slug) ?? null;
}

/**
 * Counties where commercial timber harvesting — and so forestry herbicide
 * use — is concentrated. Used only to order the list so the counties most
 * people are looking for come first; it is not a statement about any county.
 */
export const TIMBER_COUNTIES = new Set([
  "Humboldt", "Mendocino", "Siskiyou", "Shasta", "Trinity", "Del Norte", "Lassen",
  "Plumas", "Tehama", "Butte", "Sierra", "Nevada", "Placer", "El Dorado", "Amador",
  "Calaveras", "Tuolumne", "Mariposa", "Modoc", "Sonoma", "Santa Cruz", "Lake",
  "Glenn", "Fresno", "Tulare", "Madera",
]);
