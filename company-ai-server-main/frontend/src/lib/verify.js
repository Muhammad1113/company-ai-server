import { isNotFound } from './sources.js';

// Jawab mein number/unit ho to "source se milao" ka note (4B model number ghalat de sakta hai)
const FACT = /\d+\s*(?:to|-|–)?\s*\d*\s*(?:days?|weeks?|pcs|pieces|units|usd|\$|eur|%|kg|cm|mm)|\b(?:moq|iso|ce|mdr|hs code|aisi)\b|\b\d{3,}\b/i;

export function needsVerify(text) {
  const t = String(text || '');
  return t.length > 0 && !isNotFound(t) && FACT.test(t);
}
