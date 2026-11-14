// Quotation hisaab: sab kuch code se (model se nahi). Paise cents mein (integer) taake 0.1+0.2 jaisi ghalti na ho.
export const INCOTERMS = ['EXW', 'FCA', 'FOB', 'CFR', 'CIF'];

const cents = (n) => Math.round((Number(n) || 0) * 100);
const pos = (n) => Math.max(0, Number(n) || 0);

/**
 * items: [{ name, price, qty }]
 * EXW = goods + packing | FCA/FOB = + local charges | CFR = + freight | CIF = + insurance (110% of CFR value)
 */
export function calcQuote({ items = [], discountPct = 0, packing = 0, local = 0, freight = 0, insurancePct = 0, incoterm = 'FOB', rate = 0 }) {
  const qty = items.reduce((s, i) => s + Math.floor(pos(i.qty)), 0);
  const goods = items.reduce((s, i) => s + cents(pos(i.price)) * Math.floor(pos(i.qty)), 0);
  const disc = Math.round((goods * Math.min(100, pos(discountPct))) / 100);
  const net = goods - disc;
  const pack = cents(pos(packing));
  const loc = ['FCA', 'FOB', 'CFR', 'CIF'].includes(incoterm) ? cents(pos(local)) : 0;
  const fr = ['CFR', 'CIF'].includes(incoterm) ? cents(pos(freight)) : 0;
  const cfr = net + pack + loc + fr;
  const ins = incoterm === 'CIF' ? Math.round((cfr * 1.1 * pos(insurancePct)) / 100) : 0;
  const total = cfr + ins;
  const r = pos(rate);
  return {
    qty, incoterm,
    goods: goods / 100, discount: disc / 100, net: net / 100, packing: pack / 100, local: loc / 100,
    freight: fr / 100, insurance: ins / 100, total: total / 100,
    perUnit: qty ? Math.round(total / qty) / 100 : 0,
    converted: r ? Math.round(total * r) / 100 : null,
  };
}

export const fmt = (n) => (Number(n) || 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export function quoteText(items, q, currency, target) {
  const lines = items.filter((i) => Number(i.qty) > 0).map((i) => `- ${i.name || 'Item'}: ${i.qty} pcs x ${fmt(i.price)} ${currency}`);
  const out = [`Quotation (${q.incoterm})`, ...lines, `Goods: ${fmt(q.goods)} ${currency}`];
  if (q.discount) out.push(`Discount: -${fmt(q.discount)} ${currency}`);
  if (q.packing) out.push(`Packing / branding: ${fmt(q.packing)} ${currency}`);
  if (q.local) out.push(`Local charges: ${fmt(q.local)} ${currency}`);
  if (q.freight) out.push(`Freight: ${fmt(q.freight)} ${currency}`);
  if (q.insurance) out.push(`Insurance: ${fmt(q.insurance)} ${currency}`);
  out.push(`Total ${q.incoterm}: ${fmt(q.total)} ${currency} (${fmt(q.perUnit)} per piece)`);
  if (q.converted != null && target) out.push(`In ${target}: ${fmt(q.converted)}`);
  return out.join('\n');
}
