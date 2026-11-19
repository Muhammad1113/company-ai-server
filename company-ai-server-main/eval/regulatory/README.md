# Regulatory accuracy eval (starter set, 31 questions)

**Ye starter draft hai, final benchmark nahi.** `doc_id` aur `expect_section` meri jaankari se likhe gaye hain: apna
regulatory lead har line verify kare, aur company ke asli sawal (30-50) jod kar apni list banaye. Product code `GAD` ke
sawal tab chalte hain jab GAD tracked ho aur uska sync approve ho chuka ho.

Zaroori: pehle admin portal mein eCFR (parts 801, 807, 820, 878) aur openFDA sync **approve** karo, warna documents index mein nahi.

```powershell
$env:COMPANY_AI_KEY = "<admin key ya session token>"
python scripts\rag_eval.py --questions eval\regulatory\questions.jsonl --collection reg__fda
```
Ye sirf **retrieval** naapta hai (sahi document mila ya nahi: hit@1, hit@3, MRR). `expect_section` ko script nahi
parakhti: misses dekh kar hath se check karo ke jawab ka citation (`21 CFR 807.87`) sahi section dikha raha hai.

**Answer-level accuracy** ke liye: har sawal chat mein poochho, jawab + citation lead se verify karao, aur
pass/fail ka table likho. Target likhit tay karo (jaise hit@3 >= 90% aur citation sahi >= 90%) **pehle**, nateeja baad mein.

Note: 2 Feb 2026 se Part 820 sirf 6 sections (820.1, .3, .7, .10, .35, .45) hai; purane `820.30` (design controls) jaise sections
ab CFR mein nahi: unke sawal ISO 13485 clause se jawab hote hain, is liye aise sawal ka jawab "Part 820 ISO 13485 ko incorporate karta hai" hona chahiye.
