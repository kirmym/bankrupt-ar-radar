/** Small, environment-independent helpers for API-key retry behaviour. */
export const API_KEY_STORAGE_KEY = "ar_radar_api_key";

export function canRetryApiKey(status: number | undefined, alreadyRetried: boolean): boolean {
  return status === 401 && !alreadyRetried;
}

export function normalizeApiKey(value: string | null | undefined): string | null {
  const normalized = value?.trim() || "";
  return normalized || null;
}
