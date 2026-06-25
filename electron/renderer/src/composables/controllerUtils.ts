export function cleanString(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

export function looksLikeUrl(rawValue: string): boolean {
  try {
    const parsed = new URL(rawValue);
    return ["http:", "https:"].includes(parsed.protocol);
  } catch {
    return false;
  }
}

export function statusPillClass(status: { available: boolean; source: string }): string {
  return status.available ? `status-pill is-${status.source}` : "status-pill is-missing";
}
