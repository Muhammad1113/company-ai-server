// Jawab ki zabaan/script control. Server ka API sirf `message` leta hai, is liye
// ek chhoti hidden instruction message ke aakhir mein lagti hai aur UI mein chhupa di jati hai.
// (Saaf hal: backend system prompt mein yehi rule. Phir ye file hata sakte hain.)
export const LANGS = [
  { id: 'auto', label: 'Auto (same as my question)' },
  { id: 'english', label: 'English' },
  { id: 'roman_urdu', label: 'Roman Urdu' },
  { id: 'urdu', label: 'اردو (Urdu script)' },
];

const RULES = {
  auto: 'Reply in the same language and script as the question. If the question is in Roman Urdu, reply in Roman Urdu using English letters only. Never reply in Hindi or Devanagari script.',
  english: 'Reply in English only.',
  roman_urdu: 'IMPORTANT: Jawab sirf Roman Urdu mein dein (Urdu zabaan English huroof mein, jaise: "Ji haan, ye item MOQ 12 ke saath available hai"). Technical words jaise MOQ, ISO, CE, TC English mein hi rakhein. Hindi ya Devanagari bilkul nahi. Reply in Roman Urdu, not English.',
  urdu: 'IMPORTANT: جواب صرف اردو رسم الخط میں دیں۔ ہندی یا دیوناگری بالکل نہیں۔ Technical words like MOQ, ISO, CE can stay in English. Reply in Urdu script.',
};

const TAG = '[Reply instruction: ';
export const MAX_LANG_SUFFIX = 260;   // Composer ki limit isi hisab se (server limit 12000)

export function withLang(text, lang = 'auto') {
  const rule = RULES[lang] || RULES.auto;
  return `${text}\n\n${TAG}${rule}]`;
}

// Server se wapas aayi history mein se instruction hata do
export function stripLang(text) {
  if (typeof text !== 'string') return text;
  const i = text.lastIndexOf(`\n\n${TAG}`);
  return i >= 0 && text.endsWith(']') ? text.slice(0, i) : text;
}
