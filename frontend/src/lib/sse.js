// SSE reader: fetch ke ReadableStream se events (POST ke saath EventSource nahi chalta).
export function parseEvent(raw) {
  let event = 'message';
  const data = [];
  for (const line of raw.split(/\r?\n/)) {
    if (!line || line.startsWith(':')) continue;
    const idx = line.indexOf(':');
    const field = idx === -1 ? line : line.slice(0, idx);
    let value = idx === -1 ? '' : line.slice(idx + 1);
    if (value.startsWith(' ')) value = value.slice(1);
    if (field === 'event') event = value;
    else if (field === 'data') data.push(value);
  }
  return data.length ? { event, data: data.join('\n') } : null;
}

export async function* iterateSse(body) {
  const reader = body.getReader();
  const decoder = new TextDecoder();      // stream:true => multi-byte (Urdu) chunk ke beech na toote
  let buffer = '';
  let finished = false;
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let m;
      while ((m = /\r?\n\r?\n/.exec(buffer))) {
        const raw = buffer.slice(0, m.index);
        buffer = buffer.slice(m.index + m[0].length);
        const ev = parseEvent(raw);
        if (ev) yield ev;
      }
    }
    buffer += decoder.decode();
    if (buffer.trim()) {
      const ev = parseEvent(buffer);
      if (ev) yield ev;
    }
    finished = true;
  } finally {
    if (!finished) reader.cancel().catch(() => {});   // consumer ruk gaya / abort
    try { reader.releaseLock(); } catch { /* pending read */ }
  }
}
