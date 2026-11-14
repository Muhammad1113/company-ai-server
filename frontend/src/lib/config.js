// .env mein VITE_ALL_COLLECTIONS=1 tab lagayen jab backend collection:"*" (user ki sab allowed collections) support kare.
export const ALL_COLLECTIONS = '*';
export const ALL_ENABLED = import.meta.env?.VITE_ALL_COLLECTIONS === '1';
