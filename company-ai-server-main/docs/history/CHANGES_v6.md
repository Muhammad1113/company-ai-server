# Phase 7 (v6): pichla sawal ghalat jurna band + "check online" Auto mode fix

Copy karo (v5 ke upar): app/services/router.py, app/api/routes/conversations.py, tests/test_followup_v6.py
Frontend: koi tabdeeli nahi. .env: koi nayi setting nahi.

## Transcript ke bugs aur wajah
1. "what is computer" => KP ke CM ka naam; "vllm kia hai" => search nakam; "what is llama.cpp" => "private information".
   Wajah: 3 lafz tak ka har sawal pichle sawal se jur jata tha ("what are the sizes of forceps? vllm kia hai",
   "453+5465-5645+45 what is llama.cpp" => lamba number => privacy gate).
   Ab: sirf asli follow-up jurta hai ("check online", "aur Punjab ka?", "what about its price") aur sirf tab jab pichle
   sawal mein kam az kam 2 lafz hon (sirf numbers wala calculator sawal kabhi nahi). Documents ki retrieval par bhi yahi rule.
2. "check online", "latest treatment for my patient", "who is current cm of KP" => "I don't know" / "not in context".
   Wajah: Auto mode pehle documents mein dhoondta tha, kamzor match par bhi documents wala prompt chal jata tha.
   Ab: "check online/search/google" wale sawal par documents chhode jate hain; "latest/current/who is PM" par documents ka
   score bar 0.40 se 0.55 ho jata hai; Search toggle ON ho to bhi documents chhode jate hain.
3. Web query mein Roman Urdu filler ("kia hai", "kya hota hai") hata diya: "vllm kia hai" => search "vllm".

## Jo is zip se theek NAHI hota (4B ki hadd)
- Model kabhi kabhi apni yaaddasht se ghalat jawab deta hai (jaise vLLM ko "Very Large Language Model"). Bachao: internet result.
- Search result mein ghalat naam aa sakta hai (KP CM ke jawab mein purana naam): jawab ke neeche website kholkar check karein.

## v6 (add-on): Admin document list
NEW  GET /api/v1/rag/documents?collection=NAME (admin) => {"documents":[{doc_id, chunks, type, pages, chars}]}
EDIT app/api/routes/rag.py, app/rag/service.py, app/rag/store.py, app/rag/pgvector_store.py
NEW  tests/test_list_documents_v6.py (4 tests)
Frontend (frontend_nomode.zip ki Admin.jsx) is endpoint se har document ke saamne Delete button dikhata hai.
