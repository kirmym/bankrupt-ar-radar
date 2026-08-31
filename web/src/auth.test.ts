import { describe, expect, it } from "vitest";

import { canRetryApiKey, normalizeApiKey } from "./auth";

describe("API-key auth retry", () => {
  it("allows only one retry after a 401", () => {
    expect(canRetryApiKey(401, false)).toBe(true);
    expect(canRetryApiKey(401, true)).toBe(false);
    expect(canRetryApiKey(403, false)).toBe(false);
  });

  it("normalizes empty and padded keys", () => {
    expect(normalizeApiKey("  secret  ")).toBe("secret");
    expect(normalizeApiKey("   ")).toBeNull();
    expect(normalizeApiKey(undefined)).toBeNull();
  });
});
