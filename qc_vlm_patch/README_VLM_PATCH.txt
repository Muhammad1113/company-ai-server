VLM PATCH (company-ai-server): scanned / figure PDFs ko Qwen3.5 vision se padhna + PDF page-number fix
===========================================================================================

Copy karo (same paths, backend project root mein):
  app/rag/extractors.py        <- poori file replace
  app/rag/vision.py            <- NAYI file
  app/api/routes/rag.py        <- poori file replace   (sirf ingest_file badla + `from app.rag import vision`)
  tests/test_vision.py         <- NAYI file
  config: app/core/config.py mein `RAG_MAX_FILE_CHARS` ke neeche config_additions.txt ki lines paste karo

Install:   pip install pypdfium2 pillow        (requirements.txt mein bhi add kar do)

llama-server (chat wala, :8081) ko mmproj ke saath chalao. start_all.ps1 ki line:
  & '$LlamaServer' -m '$ChatModel' --mmproj 'C:\path\mmproj-F16.gguf' -ngl 99 -c 4096 -np 1 --host $LlamaHost --port 8081
mmproj usi repo se lo jahan se Qwen3.5-9B GGUF li hai (mmproj-*.gguf, F16 ~0.9GB). VRAM kam pade to
  --no-mmproj-offload   (vision encoder CPU par chalega, slow lekin VRAM bachta hai)

.env:
  VLM_MODE=figures        # off | scanned | figures | all

Test: 2-3 page ki scanned/figure wali PDF admin panel se upload karo; response mein "vision": {"vlm_pages": N, ...} aayega.
Bug fix (zaroori): purane extractors.py mein PDF ka page break \f -> \n\n ban jata tha, isliye PDF ke saare pages
ek chunk mein mil jate the aur "Page N" citation nahi ban sakti thi. Is patch ke baad dobara upload karo.
Regulatory upload (app/services/regulatory.py) bhi isi extract_text se guzarta hai, to usay bhi page numbers mil jayenge.
