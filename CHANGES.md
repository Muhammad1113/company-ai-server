# Phase 7 (v5): tools + internet search + Auto mode + routing fixes

## Backend (zip ke files apne project par copy karo)
NEW   app/services/tools.py, router.py, agent.py
EDIT  app/services/websearch.py, container.py, app/api/routes/conversations.py, app/core/config.py,
      app/rag/service.py (search_many), requirements.txt
NEW   tests/test_agent_phase7.py

## v5: search fail ho to khali jawab nahi
- Pehle: search fail => model sirf "Not verified online." likhta tha. Ab:
  * aam sawal ("what are scissors", "what is vllm"): jawab general knowledge se poora, pehli line mein wajah.
  * specs/standard/dose: general samjhata hai, number/standard nahi deta.
  * current/"check online": guess nahi, wajah batata hai.
- Wajah ab jawab mein aur status mein ("busy" + retry_after). Server log mein WARNING: `web search not used (state): <error>`:
  "rate limited (user|global)" = limit, "duckduckgo: cooling down" = DDG ne rok diya, "no results" = kuch nahi mila.
- Classifier ke examples barhe (scissors, scalpel kya hota hai => GENERAL; vllm, qwen vs mistral => LOOKUP).

## v4: ChatGPT jaisi routing (WEB_SEARCH_POLICY=auto, default)
Faisla is tarteeb se hota hai (pehla jo lage):
 1. Collection select / Auto mein documents ka score >= RAG_AUTO_MIN_SCORE  -> documents (internet kabhi nahi)
 2. Calculator / unit / date-time / BMI                                       -> server tool
 3. Salam, shukriya, "draft/write/translate", lambi text                      -> seedha model
 4. "current/latest/aaj", office holder, "search/check online"                -> internet (fail ho to guess nahi)
 5. Size, dose, standard, ISO/FDA/MDR/CE, price, supplier, ya anjaan abbreviation (EUMDR) -> internet
 6. Baaqi: model se ek chhota faisla (GENERAL / LOOKUP, <=8 token, ~0.5s GPU). GENERAL -> model apni training se
    jawab deta hai ("what is a hernia", "what is AI"); LOOKUP ya samajh na aaye ya error -> internet.
 Classifier band karna: AGENT_CLASSIFIER_ENABLED=false (phir ambiguous sawal par internet).
 Purane modes: WEB_SEARCH_POLICY=factual (sab par search) / fresh (sirf current wale).
Bug fix: relevance filter ab sirf Wikipedia fallback par (DDG ke sahi results "eumdr" par gir jate the).

## v3 mein kya theek hua (aap ke test se)
- Unit tool: "millimeters/inches" jama alfaz, aur 15 Fr = 5 mm (1 Fr = 1/3 mm).
- WEB_SEARCH_POLICY=factual (default): har factual sawal par search (forceps sizes, EUMDR ...).
  Salam/shukriya, "draft/write/translate" aur lambi paste ki hui text par nahi.
  WEB_SEARCH_POLICY=fresh: purana tareeqa (sirf current/latest/news).
- "check online", "net par dekho" ab search ka hukm hai; pichla sawal khud jur jata hai.
- Search fail ho to:
  * current/explicit sawal: model sirf "verify nahi kar saka" kehta hai, naam/number nahi deta.
  * aam factual sawal: "Not verified online." aur koi size/dose/standard/price nahi.
- Irrelevant results (jaise "Reproductive rights") ab girte hain; Wikipedia query mein sirf keywords.
- Burst limit: per-user 5, server 10 ek saath (pehle 3 / 5).

## Frontend: frontend_changed.zip mein koi tabdeeli nahi (pehle wala hi lagayein).

## .env
frontend/.env :  VITE_ALL_COLLECTIONS=1
backend .env  :  RAG_AUTO_MIN_SCORE=0.40  WEB_SEARCH_CONTACT=it@company.com  WEB_SEARCH_POLICY=auto
