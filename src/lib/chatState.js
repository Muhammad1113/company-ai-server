// Streaming jawab ki state (pure functions: test ho sakte hain)
export const initialStream = () => ({ text: '', sources: null, status: 'streaming', error: null });

export function reduceStream(state, ev) {
  switch (ev.type) {
    case 'sources': return { ...state, sources: ev.sources };
    case 'token': return { ...state, text: state.text + ev.text };
    case 'error': return { ...state, status: 'error', error: ev.detail };
    case 'done': return state.status === 'error' ? state : { ...state, status: 'complete' };
    case 'abort': return state.status === 'streaming' ? { ...state, status: 'stopped' } : state;
    default: return state;
  }
}

// Server ke SSE event (event name + data string) => {type,...} (ya null)
export function normalizeEvent({ event, data }) {
  if (event === 'sources') {
    try { return { type: 'sources', sources: JSON.parse(data).sources || [] }; } catch { return null; }
  }
  if (event === 'error') {
    let detail = 'Generation failed';
    try { detail = JSON.parse(data).detail || detail; } catch { /* plain */ }
    return { type: 'error', detail };
  }
  if (data === '[DONE]') return { type: 'done' };
  try {
    const obj = JSON.parse(data);
    const text = obj?.choices?.[0]?.delta?.content;
    return typeof text === 'string' && text ? { type: 'token', text } : null;
  } catch { return null; }
}

export const dropLastAssistant = (messages) =>
  messages.length && messages[messages.length - 1].role === 'assistant' ? messages.slice(0, -1) : messages;
