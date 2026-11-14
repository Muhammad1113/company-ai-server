import { useMemo, useState } from 'react';
import { calcQuote, fmt, INCOTERMS, quoteText } from '../lib/quote.js';
import { copyText } from '../lib/clipboard.js';

let uid = 1;
const blank = () => ({ id: uid++, name: '', price: '', qty: '' });

export default function Calculator({ onMenu }) {
  const [items, setItems] = useState([blank()]);
  const [f, setF] = useState({ discountPct: '', packing: '', local: '', freight: '', insurancePct: '0.5', incoterm: 'FOB', currency: 'USD', target: '', rate: '' });
  const [copied, setCopied] = useState(false);
  const set = (k) => (e) => setF((s) => ({ ...s, [k]: e.target.value }));
  const edit = (id, k, v) => setItems((xs) => xs.map((x) => (x.id === id ? { ...x, [k]: v } : x)));
  const q = useMemo(() => calcQuote({ items, ...f }), [items, f]);
  const showFreight = ['CFR', 'CIF'].includes(f.incoterm);

  const copy = async () => {
    if (await copyText(quoteText(items, q, f.currency, f.target))) { setCopied(true); setTimeout(() => setCopied(false), 1500); }
  };

  const Row = ({ label, value, minus }) => (value ? (
    <div className="qrow"><span>{label}</span><span>{minus ? '−' : ''}{fmt(value)}</span></div>
  ) : null);

  return (
    <section className="chat calc">
      <header className="chat-head">
        <button type="button" className="icon-btn menu-btn" onClick={onMenu} aria-label="Open conversations">
          <svg viewBox="0 0 16 16" width="18" height="18"><path d="M2 4h12M2 8h12M2 12h12" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" /></svg>
        </button>
        <h1>Quotation calculator</h1>
      </header>
      <div className="scroll">
        <div className="calc-body">
          <p className="hint">Hisaab seedha code se hota hai, AI model se nahi. Price aur MOQ hamesha apni price list se dalo.</p>

          <div className="panel">
            <h3>Items</h3>
            {items.map((it) => (
              <div className="item-row" key={it.id}>
                <input placeholder="Item (e.g. Mayo scissors 5.5)" value={it.name} onChange={(e) => edit(it.id, 'name', e.target.value)} aria-label="Item name" />
                <input type="number" min="0" step="0.01" placeholder={`Price (${f.currency})`} value={it.price} onChange={(e) => edit(it.id, 'price', e.target.value)} aria-label="Unit price" />
                <input type="number" min="0" step="1" placeholder="Qty" value={it.qty} onChange={(e) => edit(it.id, 'qty', e.target.value)} aria-label="Quantity" />
                <button type="button" className="icon-btn" disabled={items.length === 1} onClick={() => setItems((xs) => xs.filter((x) => x.id !== it.id))} aria-label="Remove item">✕</button>
              </div>
            ))}
            <div><button type="button" className="btn" onClick={() => setItems((xs) => [...xs, blank()])}>+ Add item</button></div>
          </div>

          <div className="panel">
            <h3>Terms</h3>
            <div className="grid2">
              <label>Incoterm
                <select value={f.incoterm} onChange={set('incoterm')}>{INCOTERMS.map((t) => <option key={t}>{t}</option>)}</select>
              </label>
              <label>Currency
                <input value={f.currency} onChange={set('currency')} maxLength={4} />
              </label>
              <label>Discount %
                <input type="number" min="0" max="100" step="0.1" value={f.discountPct} onChange={set('discountPct')} placeholder="e.g. 5 for 500+ pcs" />
              </label>
              <label>Packing / branding (total)
                <input type="number" min="0" step="0.01" value={f.packing} onChange={set('packing')} />
              </label>
              {f.incoterm !== 'EXW' && (
                <label>Local charges to port (total)
                  <input type="number" min="0" step="0.01" value={f.local} onChange={set('local')} />
                </label>
              )}
              {showFreight && (
                <label>International freight (total)
                  <input type="number" min="0" step="0.01" value={f.freight} onChange={set('freight')} />
                </label>
              )}
              {f.incoterm === 'CIF' && (
                <label>Insurance rate %
                  <input type="number" min="0" step="0.01" value={f.insurancePct} onChange={set('insurancePct')} />
                </label>
              )}
            </div>
            <div className="grid2">
              <label>Convert to (optional)
                <input value={f.target} onChange={set('target')} maxLength={4} placeholder="PKR, EUR…" />
              </label>
              <label>Rate (1 {f.currency || '…'} = ?)
                <input type="number" min="0" step="0.0001" value={f.rate} onChange={set('rate')} />
              </label>
            </div>
            {f.incoterm === 'CIF' && <p className="hint">CIF insurance = (goods + freight) × 110% × rate.</p>}
          </div>

          <div className="panel quote">
            <h3>Result ({f.incoterm})</h3>
            <Row label="Goods" value={q.goods} />
            <Row label="Discount" value={q.discount} minus />
            <Row label="Packing / branding" value={q.packing} />
            <Row label="Local charges" value={q.local} />
            <Row label="Freight" value={q.freight} />
            <Row label="Insurance" value={q.insurance} />
            <div className="qrow total"><span>Total {f.currency}</span><span>{fmt(q.total)}</span></div>
            <div className="qrow muted"><span>Per piece ({q.qty} pcs)</span><span>{fmt(q.perUnit)}</span></div>
            {q.converted != null && f.target && <div className="qrow"><span>Total {f.target}</span><span>{fmt(q.converted)}</span></div>}
            <div><button type="button" className="btn primary" onClick={copy}>{copied ? 'Copied' : 'Copy quote summary'}</button></div>
          </div>
        </div>
      </div>
    </section>
  );
}
