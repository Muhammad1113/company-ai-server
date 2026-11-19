# Live regulatory sources (FDA, UK, EU)

Chatbot ka model kabhi live internet par qanoon nahi dhoondta. Server in sources se text laata hai, **admin approve** karta hai,
aur phir wo RAG mein jata hai. Har jawab ke saath section-level citation aur "version / retrieved" tareekh milti hai.

| Region | Source | Key? | Collection | Granularity | Admin API |
|---|---|---|---|---|---|
| US | openFDA (classification, 510(k), recalls) | optional | `reg__fda` | record | `/admin/regulatory/openfda/*` |
| US | eCFR (21 CFR 801/807/820/878) | no | `reg__fda` | section | `/admin/regulatory/ecfr/*` |
| UK | legislation.gov.uk (UK MDR 2002 = `uksi/2002/618`) | no | `reg__uk` | regulation / schedule paragraph | `/admin/regulatory/law/uk/*` |
| EU | EUR-Lex / CELLAR (MDR = CELEX `32017R0745`, latest consolidated) | no | `reg__eu` | article / annex | `/admin/regulatory/law/eu/*` |

## Flow (sab sources ke liye ek jaisa)
1. `POST /admin/regulatory/law/{uk|eu}/sync` (ya `OPENFDA_AUTO_SYNC_HOURS > 0` par khud) -> sirf **pending** banta hai.
2. `GET /admin/regulatory/pending` -> ek inbox: openFDA, eCFR, UK, EU ke sab pending updates.
3. `GET /admin/regulatory/openfda/pending/{id}` -> section-wise new / changed / removed (old vs new text).
4. `POST /admin/regulatory/openfda/pending/{id}/approve` (ya `/reject`) -> approve par hi RAG badalta hai.

Items: `GET /admin/regulatory/law/uk` (default UK MDR 2002), `POST .../law/uk/items {"code": "ukpga/1987/43"}`,
`DELETE .../law/uk/items/ukpga/1987/43`. EU mein CELEX ya EUR-Lex link do. `32017R0745` = latest consolidated khud dhoondta hai;
`02017R0745-20250110` = ek khaas version (khud update nahi hoga).

## Region-aware jawab
Sawal mein UK / EU / FDA (ya MHRA, CE mark, UKCA, ...) ho to sirf usi region ki `reg__*` collections search hoti hain.
Region na ho to sab. "UK MDR 2002" UK gina jata hai, EU nahi. "FDA MDR report" (21 CFR 803) EU nahi gina jata.
Region saaf ho to **fallback bhi usi region mein rehta hai**: EU poochha aur `reg__eu` mein kuch na mila to US FDA ka jawab nahi diya jata
(company ki apni documents collections ab bhi dekhi jati hain). Code: `app/services/regsearch.py`.

## EU MDR Annex VIII (classification rules)
`eurlex.py` Annex VIII ko **Rule 1..22 mein alag records** mein todta hai (`eurlex:32017R0745-annex-viii-rule-6`, citation `[EU Reg 2017/745 Annex VIII Rule 6]`),
har record ke saath chapter/section context. `chunker.py` do Rules ko ek chunk mein nahi milata. Classification ke sawal (Class I/Is/Im/Ir/IIa, "Rule 6", "Annex VIII")
par retrieval bara candidate pool leta hai aur Annex VIII ko Annex I (labelling) se upar rakhta hai. Deploy ke baad: `python scripts/check_live_sources.py`
(22/22 rules), phir EU sync + approve taake `reg__eu` dobara ingest ho.

## Hifazatein
* Kuch RAG mein admin approval ke baghair nahi jata.
* Source ka text achanak bohat chhota aaye (parse tootna / bot-check page) to koi "removed" propose nahi hota; error dikhta hai.
* Lamba section truncate nahi hota, parts mein tootta hai.
* EU consolidated text sirf jankari ke liye hai (authentic = Official Journal); UK text mein bhi recent amendments ki der ho sakti hai. Header mein likha jata hai.

## Pehli baar asli internet par check
    python scripts/check_live_sources.py --uk uksi/2002/618 --eu 32017R0745
