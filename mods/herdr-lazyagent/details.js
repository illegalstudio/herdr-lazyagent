export function duration(seconds) {
  const n = Math.max(0, Math.floor(seconds || 0));
  if (n >= 86400) return `${Math.floor(n / 86400)}d ${Math.floor(n % 86400 / 3600)}h`;
  if (n >= 3600) return `${Math.floor(n / 3600)}h ${Math.floor(n % 3600 / 60)}m`;
  return n >= 60 ? `${Math.floor(n / 60)}m` : `${n}s`;
}

export function windowTitle(window) {
  return `${window.label || "Usage"}${window.scope ? ` · ${window.scope}` : ""}`;
}

export function remainingSeconds(at, seconds, fetchedAt, now) {
  const timestamp = Date.parse(at);
  if (Number.isFinite(timestamp)) return Math.max(0, (timestamp - now) / 1000);
  return Math.max(0, Number(seconds || 0) - Math.max(0, now / 1000 - fetchedAt));
}

export function absoluteTime(at) {
  const timestamp = Date.parse(at);
  return Number.isFinite(timestamp) ? new Date(timestamp).toLocaleString() : "";
}

function deadline(at, seconds, fetchedAt, now, due) {
  const remaining = remainingSeconds(at, seconds, fetchedAt, now);
  const absolute = absoluteTime(at);
  return `${remaining > 0 ? `in ${duration(remaining)}` : due}${absolute ? ` (${absolute})` : ""}`;
}

export function resetLines(reset, fetchedAt, now) {
  const count = reset.total > 0 ? `${reset.left} of ${reset.total} left` : `${reset.left} left`;
  const status = reset.status || (reset.usable ? "Usable now" : "Not usable now");
  const lines = [`${count} · ${status}`];
  if (reset.expires_at || reset.expires_in_seconds > 0) {
    lines.push(`Expiry: ${deadline(reset.expires_at, reset.expires_in_seconds, fetchedAt, now, "passed")}`);
  }
  if (reset.next_available_at || reset.next_available_in_seconds > 0) {
    lines.push(`Next available ${deadline(reset.next_available_at, reset.next_available_in_seconds, fetchedAt, now, "now")}`);
  }
  return lines;
}
