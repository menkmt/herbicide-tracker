/**
 * Typed client for the tracker API.
 *
 * Pages are server-rendered, so the API is called from the server and the
 * browser never needs a key. That keeps the subscriber key out of client
 * bundles and means every public page is crawlable HTML rather than an empty
 * shell that fills in later.
 */

const API_BASE = process.env.TRACKER_API_URL ?? "http://localhost:8000";
const API_KEY = process.env.TRACKER_API_KEY;

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (API_KEY) headers["X-API-Key"] = API_KEY;

  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { ...headers, ...(init?.headers as Record<string, string>) },
    // Published records change only when an administrator publishes, so a
    // short revalidation window keeps pages fast without going stale.
    next: { revalidate: 300 },
  });

  if (!response.ok) {
    throw new ApiError(
      `${path} returned ${response.status}`,
      response.status,
    );
  }
  return (await response.json()) as T;
}

export interface FlagSummary {
  highest_level: "red" | "orange" | "yellow" | null;
  headline: string | null;
  has_regulatory_restriction: boolean;
  has_watchlist_entry: boolean;
  flags: Array<{
    level: string;
    label: string;
    detail: string;
    is_regulatory: boolean;
    source_citation: string;
    source?: { source_url?: string | null };
  }>;
}

export interface ActiveIngredientRef {
  name: string;
  slug: string;
  url: string;
  is_california_restricted: boolean;
  is_watchlisted: boolean;
  products: string[];
}

export interface ProductRef {
  name: string;
  epa_reg_no: string | null;
  registrant: string | null;
  active_ingredients: string[];
  identified: boolean;
}

export interface AdjuvantRef {
  name: string;
  epa_reg_no: string | null;
  type: string | null;
  type_label: string;
  description: string;
}

export interface Materials {
  active_ingredients: ActiveIngredientRef[];
  products: ProductRef[];
  adjuvants: AdjuvantRef[];
}

export interface ApplicationRow {
  slug: string;
  title: string;
  title_basis: string | null;
  owner: string | null;
  county: string | null;
  county_slug: string | null;
  date_start: string | null;
  date_end: string | null;
  acres: number | null;
  acreage_is_partial: boolean;
  method: string;
  is_planned: boolean;
  record_count: number;
  chemicals: string[];
  active_ingredients: string[];
  adjuvants: string[];
  materials: Materials;
  flag_level: string | null;
  flag_headline: string | null;
  has_regulatory_restriction: boolean;
  has_watchlist_entry: boolean;
  url: string;
}

export interface ApplicationDetail extends ApplicationRow {
  mtrs: string[];
  site_ids: string[];
  permit_numbers: string[];
  confidence: string;
  flags: FlagSummary | null;
  parcels: Array<{
    apn: string;
    owner: string | null;
    acreage: number | null;
    match_basis: string | null;
    confidence: string;
    source: string | null;
  }>;
  records: Array<{
    document_number: string | null;
    kind: string;
    site_id: string | null;
    mtrs: string | null;
    date_start: string | null;
    date_end: string | null;
    method: string;
    operator: string | null;
    applicator: string | null;
    applicator_license: string | null;
    treated_amount: number | null;
    treated_units: string | null;
    commodity: string | null;
    products: Array<{
      name: string | null;
      epa_reg_no: string | null;
      quantity: number | null;
      units: string | null;
      active_ingredients: string[];
      is_adjuvant: boolean;
      adjuvant_type: string | null;
      adjuvant_label: string | null;
      identified: boolean;
    }>;
  }>;
}

export interface County {
  name: string;
  slug: string;
  applications: number;
  first_date: string | null;
  last_date: string | null;
  acres: number;
  url: string;
}

export interface Chemical {
  name: string;
  slug: string;
  pesticide_type: string | null;
  is_california_restricted: boolean;
  is_watchlisted: boolean;
  url: string;
}

export interface ChemicalDetail extends Chemical {
  cas_number: string | null;
  chemical_class: string | null;
  sections: Record<string, string | null>;
  flags: Array<{
    level: string;
    label: string;
    detail: string;
    is_regulatory: boolean;
    source: string;
    source_url: string | null;
  }>;
  products: Array<{ name: string | null; epa_reg_no: string; registrant: string | null }>;
  applications: ApplicationRow[];
}

export interface RadiusResult {
  centre: { lat: number; lon: number; resolved: string | null };
  radius_miles: number;
  count: number;
  distance_basis: string;
  privacy_note: string;
  results: Array<{
    slug: string;
    title: string;
    owner: string | null;
    date: string | null;
    acres: number | null;
    method: string;
    distance_miles: number;
    flag_level: string | null;
    flag_headline: string | null;
    url: string;
  }>;
}

export interface Meta {
  coverage_start: string;
  coverage_end: string | null;
  document_kinds: Array<{ kind: string; label: string }>;
  published_site_categories: string[];
  planned_site_categories: string[];
  notes: string[];
}

export const api = {
  meta: () => request<Meta>("/api/meta"),
  counties: () => request<{ counties: County[] }>("/api/counties"),
  applications: (params: Record<string, string | number | undefined>) => {
    const query = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== "") query.set(key, String(value));
    }
    return request<{
      total: number;
      page: number;
      pages: number;
      page_size: number;
      applications: ApplicationRow[];
    }>(`/api/applications?${query.toString()}`);
  },
  application: (slug: string) =>
    request<ApplicationDetail>(`/api/applications/${encodeURIComponent(slug)}`),
  chemicals: () => request<{ chemicals: Chemical[] }>("/api/chemicals"),
  chemical: (slug: string) =>
    request<ChemicalDetail>(`/api/chemicals/${encodeURIComponent(slug)}`),
  radius: (params: Record<string, string | number>) => {
    const query = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) query.set(key, String(value));
    return request<RadiusResult>(`/api/search/radius?${query.toString()}`);
  },
};

/** Formats a date range the way the application grid shows it. */
export function formatDateRange(start: string | null, end: string | null): string {
  if (!start) return "Date not reported";
  const startDate = new Date(`${start}T00:00:00`);
  const options: Intl.DateTimeFormatOptions = { month: "short", day: "numeric" };
  if (!end || end === start) {
    return startDate.toLocaleDateString("en-US", { ...options, year: "numeric" });
  }
  const endDate = new Date(`${end}T00:00:00`);
  if (startDate.getMonth() === endDate.getMonth()) {
    return `${startDate.toLocaleDateString("en-US", options)}–${endDate.getDate()}, ${endDate.getFullYear()}`;
  }
  return `${startDate.toLocaleDateString("en-US", options)} – ${endDate.toLocaleDateString(
    "en-US",
    { ...options, year: "numeric" },
  )}`;
}

export function formatAcres(acres: number | null, partial = false): string {
  if (acres === null || acres === undefined) return "Not reported";
  return `${acres.toLocaleString("en-US", { maximumFractionDigits: 1 })}${partial ? "+" : ""}`;
}
