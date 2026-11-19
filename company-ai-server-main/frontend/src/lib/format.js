const DAY = 86400;

// conversations ko Today / Yesterday / Previous 7 days / Older mein baantna (updated_at epoch seconds)
export function groupByRecency(conversations, nowMs = Date.now()) {
  const now = new Date(nowMs);
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime() / 1000;
  const groups = [
    { label: 'Today', items: [] },
    { label: 'Yesterday', items: [] },
    { label: 'Previous 7 days', items: [] },
    { label: 'Older', items: [] },
  ];
  for (const c of conversations) {
    const t = c.updated_at;
    if (t >= startOfToday) groups[0].items.push(c);
    else if (t >= startOfToday - DAY) groups[1].items.push(c);
    else if (t >= startOfToday - 7 * DAY) groups[2].items.push(c);
    else groups[3].items.push(c);
  }
  return groups.filter((g) => g.items.length);
}

export function clamp(text, max) {
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

export function initials(name) {
  return (name || '?').trim().slice(0, 2).toUpperCase();
}

export function percent(score) {
  return `${Math.round(Math.max(0, Math.min(1, score)) * 100)}%`;
}

// "qasim" -> "Qasim", "qasim.ali" -> "Qasim", "ALI_KHAN" -> "Ali"  (dashboard greeting)
export function displayName(username) {
  const first = String(username || '').trim().split(/[^\p{L}\p{N}]+/u).filter(Boolean)[0] || String(username || '').trim();
  if (!first) return 'there';
  return first.charAt(0).toUpperCase() + first.slice(1).toLowerCase();
}
