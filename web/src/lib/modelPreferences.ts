// Persisted, app-global preference for the last model + reasoning effort the
// user picked on the new-session landing composer's Claude Code menu, keyed by
// harness.
//
// Mirrors modePreferences: the landing screen keeps live React state as the
// source of truth; these helpers only snapshot a pick and seed it back on a
// later visit, so a returning user starts a new session on the model/effort
// they used last instead of always resetting to the harness default. The
// consumer validates each stored value against the harness's current
// model/effort vocabulary and falls back to the default when it no longer
// exists.
//
// Only claude-native exposes this picker today, but the map is keyed by harness
// (like modePreferences) so a future native harness with its own model menu
// remembers independently. Model and effort are written independently (the two
// radios change separately), so writes merge into the existing entry.

const STORAGE_KEY = "omnigent:last-model-by-harness";

export interface ModelPref {
  model?: string;
  effort?: string;
}

type PrefMap = Record<string, ModelPref>;

function readMap(): PrefMap {
  if (typeof window === "undefined") return {};
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return {};
    const parsed: unknown = JSON.parse(raw);
    if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) return {};
    // Keep only string model/effort fields; tolerate a corrupted/partial blob.
    const out: PrefMap = {};
    for (const [k, v] of Object.entries(parsed as Record<string, unknown>)) {
      if (v === null || typeof v !== "object" || Array.isArray(v)) continue;
      const entry: ModelPref = {};
      const { model, effort } = v as Record<string, unknown>;
      if (typeof model === "string") entry.model = model;
      if (typeof effort === "string") entry.effort = effort;
      if (entry.model != null || entry.effort != null) out[k] = entry;
    }
    return out;
  } catch {
    return {};
  }
}

/**
 * Read the last model/effort the user picked for `harness` on the landing
 * composer. Returns `null` when nothing is stored, on a server render (no
 * `window`), or when storage is inaccessible/corrupted — never throws.
 */
export function readLastModelForHarness(harness: string | null | undefined): ModelPref | null {
  if (!harness) return null;
  return readMap()[harness] ?? null;
}

/**
 * Persist the given model/effort fields as the user's last explicit pick for
 * `harness`, merging into any existing entry so a model-only or effort-only
 * change preserves the other. Swallows quota/access errors so a failed write
 * can't break session creation.
 */
export function writeLastModelForHarness(
  harness: string | null | undefined,
  pref: ModelPref,
): void {
  if (typeof window === "undefined" || !harness) return;
  try {
    const map = readMap();
    map[harness] = { ...map[harness], ...pref };
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(map));
  } catch {
    // localStorage quota or access errors shouldn't break the composer.
  }
}
