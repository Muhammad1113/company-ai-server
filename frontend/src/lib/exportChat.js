export function toMarkdown(messages, title = 'Chat') {
  const out = [`# ${title}`, ''];
  for (const m of messages) {
    out.push(m.role === 'user' ? '**Question:**' : '**Answer:**', '', String(m.content || ''), '');
    const docs = [...new Set((m.sources || []).map((s) => s.doc_id))];
    if (m.role !== 'user' && docs.length) out.push(`_Sources: ${docs.join(', ')}_`, '');
  }
  return out.join('\n');
}

export function download(name, text, type = 'text/markdown') {
  const url = URL.createObjectURL(new Blob([text], { type: `${type};charset=utf-8` }));
  const a = document.createElement('a');
  a.href = url; a.download = name;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
