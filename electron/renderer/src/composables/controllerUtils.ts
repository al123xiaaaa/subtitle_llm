export { cleanString, looksLikeUrl } from "../effect/utils";

export function statusPillClass(status: { available: boolean; source: string }): string {
  return status.available ? `status-pill is-${status.source}` : "status-pill is-missing";
}
