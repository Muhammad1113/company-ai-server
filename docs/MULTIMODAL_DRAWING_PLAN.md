# Phase 2 plan: VLM, engineering drawings, tolerance verification, CV (YOLO) API

Status: **design only, nothing here is implemented in the application yet.** Written 2026-11-19 against the current repo
(FastAPI + llama.cpp + PostgreSQL/pgvector, text-only RAG, API-key / session auth, per-company collections).
Facts about models were checked online on 2026-11-19; everything marked *(estimate)* or *(unverified)* must be measured on your hardware.

## Khulasa (Roman Urdu)
1. **Model:** Qwen2.5-VL se shuru mat karo, wo do generation purana hai. Aap ka maujooda **Qwen3.5-9B khud multimodal hai** (alag `mmproj` file
   chahiye), aur **Qwen3.5-27B** bhi. 24 GB GPU par **Qwen3.5-27B Q4_K_M** pehli pasand; Qwen3-VL-32B se 50 asli drawings par muqabla karwao.
   8 GB GTX 1070 sirf prototype ke liye.
2. **Drawings:** VLM ko faisla karne mat do. Pehle CAD/PDF se **seedha text/dimension** nikalo (DXF, vector PDF). VLM sirf scanned/image
   drawings ke liye, **JSON schema** ke saath. Phir QA engineer **approve** kare (jaise regulatory approval). Pass/fail sirf **Python (Decimal)** karta hai,
   approved values par.
3. **YOLO API:** edge par decision local; server ko image + metadata **multipart POST** (idempotent, hash-checked, model version ke saath).
   Server evidence store karta hai aur audit report banata hai. VLM sirf advisory second opinion, YOLO ka reject kabhi override nahi.
4. **Image detection accuracy:** detection ke liye YOLO hi rakho (VLM localisation mein kamzor aur slow). Accuracy data, lighting, threshold aur
   feedback loop se aati hai, bare model se nahi (section 5).

---

## 0. Decisions at a glance

| Question | Recommendation | Why |
|---|---|---|
| VLM family | **Qwen3.5** (9B now, 27B after GPU upgrade); benchmark **Qwen3-VL-32B** against it | Qwen3.5 is natively multimodal (released 2026-03-02) and is already the chat model, so one `llama-server` can serve chat and vision. Qwen3-VL (2B/4B/8B/32B dense, 30B-A3B and 235B MoE) has had llama.cpp GGUF support since 2025-10-30 |
| Size | **27B/32B class on a 24 GB card**; 9B as the fast tier | 7B/9B-class models are the ones that misread small digits; larger is not automatically better, so decide on your own drawings |
| Server | Stay on **llama.cpp** until you have more than ~3 concurrent heavy users; then vLLM | The repo already has a provider switch (`INFERENCE_PROVIDER`); no code change to move |
| Who decides pass/fail | **Python on approved, normalised numbers** | An LLM must never be the judge of a tolerance |
| Where numbers come from | 1) native CAD/PDF text, 2) VLM only as fallback, 3) human approval | Zero-shot general VLMs hallucinate on GD&T (see 2.1) |
| CV reject flow | Edge decides; server stores evidence + builds the report | A slow or busy server must never stop the line |

---

## 1. VLM model and VRAM

### 1.1 What is available (checked 2026-11-19)
- **Qwen3.5** (released 2026-03-02): natively multimodal (text, image, video), hybrid Gated-DeltaNet + attention layers in a 3:1 pattern, 262K context.
  Sizes seen: 4B, 9B, 27B (dense) and 35B-A3B (MoE). Vision needs a separate `mmproj-*.gguf` passed with `--mmproj`.
  The 9B Q4_K_M file is **5.3 GB** and its `mmproj` about **0.9 GB** (both read from the model page).
- **Qwen3-VL**: 2B, 4B, 8B, 32B dense; 30B-A3B and 235B-A22B MoE; GGUF in llama.cpp since 2025-10-30.
- **Qwen2.5-VL** (the model in your question) still works but is two generations old. Do not start a new project on it.
- Because the hybrid layout keeps a KV cache for only about one layer in four, long contexts should cost less VRAM than a plain-attention model of the same
  size *(estimate; check the `llama-server` log for the real KV size)*.

### 1.2 VRAM budget
Rule of thumb for GGUF: Q4_K_M is about 0.6 GB per billion parameters, Q8_0 about 1 GB per billion *(estimate, consistent with the 9B figures above)*.
Add `mmproj` (about 1 GB), the KV cache, and the image tokens. A drawing is image-heavy: a 2048x1448 render is roughly 2.9K image tokens at a 32 px patch
size *(estimate)*, and a full A3 sheet at 300 dpi must be **tiled**, not shrunk (see 2.4).

| GPU class | Fits | Verdict |
|---|---|---|
| 8 GB (today, GTX 1070) | Qwen3.5-9B Q4_K_M (5.3 GB) + mmproj (0.9 GB) + small context | **Prototype only.** Pascal has no tensor cores, so expect slow image encoding, and it leaves almost no room for tiles or concurrency |
| 16 GB | 9B at Q8_0 (about 9 GB) with 16K+ context | Good for the 9B tier; the 27B does not fit |
| **24 GB** (RTX 3090/4090, A5000) | **27B Q4_K_M (about 16-17 GB)** + mmproj + 16-32K context | **Recommended minimum for production drawing work.** Qwen3-VL-32B Q4_K_M (about 19-20 GB) fits but leaves little room for image tokens |
| 48 GB (A6000, L40S, 2x24 GB) | 27B/32B at Q8_0 (about 29-35 GB) or two parallel slots | Use when accuracy matters more than cost, or for 3+ concurrent users |

Plan the budget so the **VLM uses at most about 80% of VRAM**. The YOLO project needs its own few GB (training is better done elsewhere), and
the embedding server currently runs on CPU, which is fine.

### 1.3 Serving
One `llama-server -m <model>.gguf --mmproj <mmproj>.gguf --jinja -ngl 99 -c 16384 -np 1` serves both chat and images through the OpenAI-compatible
`/v1/chat/completions` endpoint, so no second model has to be loaded. Required repo change: `messages.py` and `ChatRequest.content` are plain `str`;
they must accept OpenAI-style content parts (`[{"type":"text",...},{"type":"image_url",...}]`). Image requests should go through the same FIFO queue
(`INFERENCE_MAX_CONCURRENT`) but in a **separate low-priority lane** so a batch of drawings or CV events cannot starve interactive chat (README: queue is plain FIFO today).

### 1.4 How to choose, not guess
Run a one-day bake-off on **50 of your real drawings** (mix of clean PDF, scanned, hand-annotated): Qwen3.5-9B vs Qwen3.5-27B vs Qwen3-VL-32B, same prompt and schema.
Measure the metrics in section 2.7. Pick the smallest model that meets the acceptance bar. I could not verify any published number for these models on
engineering drawings, so do not trust a leaderboard for this decision.

---

## 2. Multimodal drawing RAG and tolerance framework

### 2.1 Design principle: deterministic first, VLM second, Python decides
A published study (Khan et al., arXiv 2411.03707, 2024) found zero-shot GPT-4o and Claude 3.5 Sonnet weak at extracting GD&T from 2D drawings,
while a small Florence-2 model fine-tuned on only 400 annotated drawings clearly beat them on precision, recall and hallucination rate.
Two lessons: **(a)** do not expect a general VLM to read tolerances reliably out of the box, **(b)** a small amount of your own labelled drawings is the most valuable asset.
So every number goes through a pipeline where the VLM is the *last* resort and a human approves the result.

### 2.2 Pipeline

```
upload (PDF / DXF / DWG / PNG / TIFF)
  S0 classify file ──► vector PDF | scanned PDF | DXF | DWG | raster image
  S1 native extraction (no AI)
        DXF  : ezdxf  -> DIMENSION / TOLERANCE entities, exact values and tolerances
        DWG  : convert to DXF first (ODA File Converter or LibreDWG; check licence terms)
        PDF  : embedded text with coordinates (pdfplumber / pypdfium2) -> candidate dimension strings
  S2 render at 300 dpi, crop the title block, cut overlapping tiles (keep tile origin so bboxes map back to the sheet)
  S3 VLM -> JSON per tile (schema-constrained, section 2.3) ; only for what S1 could not read
  S4 reconcile + verify
        merge S1 and S3, dedupe by (value, bbox IoU)
        if S1 has text for that spot, the VLM value must equal it, else flag
        re-read pass: crop each dimension bbox, ask the VLM again, two reads must agree, else needs_review
  S5 normalise -> Decimal values, units (mm / inch), tolerance form, angle (deg-min-sec), hardness (HRC range)
  S6 human approval (QA engineer sees an overlay of every value on the drawing) -> versioned "approved spec"
  S7 compare with measurements in Python -> PASS / FAIL / INDETERMINATE
```

The approval step deliberately reuses the pattern you already have for regulatory text (`pending -> admin approves -> live`): extracted specs are **pending**
until a QA user approves them, and only an **approved** spec revision can be used for pass/fail. Phase 2a should require approval for every feature;
automatic approval of high-confidence native values can be considered later, only after measured accuracy (2.7).

### 2.3 Schema (pydantic v2; pydantic is already a dependency)
The VLM is called with a JSON schema so the output cannot drift from the format. llama.cpp's server accepts a JSON schema or a GBNF grammar
(the repo already sends a `grammar` field for llama_cpp only; the same switch applies). Tolerance-critical numbers stay **strings in JSON** and become `Decimal` in Python, never `float`.

```jsonc
// DrawingSpec (one per drawing revision)
{
  "schema_version": "1.0",
  "drawing": {
    "drawing_number": "FRC-1234", "revision": "C", "title": "Mosquito forceps 125 mm",
    "part_number": "SF-0125", "material": "AISI 420 stainless", "standard": "ISO 2768-m",
    "units": "mm", "projection": "third-angle", "scale": "1:1", "sheet_count": 2
  },
  "features": [
    {
      "id": "F-014",
      "kind": "diameter",                       // linear | diameter | radius | angle | thread | hardness | roughness | gdt | note
      "label": "pivot hole",
      "view": "SECTION A-A",
      "nominal": "3.00",                        // string -> Decimal
      "unit": "mm",                             // mm | in | deg | HRC | HV | um
      "tolerance": {
        "type": "bilateral",                    // symmetric | bilateral | unilateral | limits | fit | general
        "plus": "0.02", "minus": "-0.01",       // deviations, as printed
        "lower": null, "upper": null,           // for type=limits
        "fit_code": null,                       // e.g. "H7"; resolved via an ISO 286 table, unsupported codes -> needs_review
        "general_class": null                   // e.g. "ISO 2768-m" when no explicit tolerance is printed
      },
      "gdt": null,                              // {"symbol":"position","value":"0.05","datums":["A","B"]} when kind=gdt
      "quantity": 1,
      "source": {
        "method": "native",                     // native | vlm | manual
        "page": 1, "bbox": [412, 910, 538, 944],  // sheet pixels at 300 dpi
        "evidence_text": "Ø3.00 +0.02/-0.01",  // exact string that was read
        "confidence": 0.97                      // native = 1.0; VLM = agreement of the two reads, NOT the model's own score
      },
      "review": {"status": "pending", "by": null, "at": null, "note": null}   // pending | approved | rejected | edited
    }
  ]
}
```

```jsonc
// MeasurementSet (from CMM / gauge / manual entry)
{ "drawing_number": "FRC-1234", "revision": "C", "lot": "L2611-07", "serial": "0042",
  "instrument": {"id": "CMM-02", "calibration_due": "2027-03-01"},
  "measurements": [ {"feature_id": "F-014", "value": "3.012", "unit": "mm", "expanded_uncertainty": "0.004"} ] }

// FeatureResult (output of the checker, stored; the VLM is not involved)
{ "feature_id": "F-014", "measured": "3.012", "limits": ["2.99", "3.02"], "verdict": "PASS",
  "rule": "ISO14253-1:default", "spec_revision": 7, "checked_at": "2026-11-19T10:00:00Z" }
```

Mismatched revision, unit mismatch, missing feature, or an unapproved spec must produce `NEEDS_REVIEW` / `NOT_MEASURED`, never a silent PASS.

### 2.4 Reading the drawing (S2-S4) in practice
- **Tiles, not one downscaled page.** Tiny digits (`0.02`) disappear when an A3 sheet is shrunk to fit the model's image budget. Use roughly 1.5-2K px tiles with about 15% overlap, send the title block separately.
- **Ask for evidence.** Every item carries `bbox` and `evidence_text`; items without them are discarded.
- **Verify by re-reading** a tight crop around each dimension. Two independent reads that disagree go to review. This catches most `0.08` vs `0.03` style errors cheaply.
- **Symbols.** The VLM may misread `Ø`, `±`, `∠`, GD&T frames. Map them to named fields in the schema (as the paper above did with Unicode) and validate (a `diameter` must have a nominal, a `position` GD&T needs at least one datum).
- **Dependencies and licences.** `PyMuPDF` is AGPL (a problem for closed-source commercial use); prefer `pypdfium2` + `pdfplumber` for PDF and `ezdxf` (MIT) for DXF. Check each licence before adding it.

### 2.5 Multimodal RAG on top of the current text RAG
Do not embed drawing images with a CLIP-style model and hope; a question like "what is the bore tolerance of part SF-0125?" is an **exact-match question**.
1. **Structured store (source of truth):** PostgreSQL tables `drawings`, `drawing_revisions`, `drawing_features`, `inspection_runs`, `inspection_results`
   (company-scoped, same isolation rules as `app/core/tenancy.py`).
2. **Text chunks for retrieval:** one chunk per approved feature, in the same tagged style as the law text,
   e.g. `[DWG FRC-1234 rev C, SECTION A-A] Ø3.00 +0.02/-0.01 mm (pivot hole)`, into a company collection such as `co_<slug>__drawings`. Citations then show drawing, revision and view,
   and the UI can highlight the `bbox` on the stored page image (it already highlights passages for documents).
3. **Deterministic tool route:** add a drawing lookup to the existing `tools.py` / router `tool` path (part number or drawing number detected in the question -> SQL lookup),
   so spec questions never depend on embedding similarity.
4. **VLM at question time** only for "look at this drawing and tell me ..." requests, with the image attached to the chat turn (needs the content-parts change in 1.3). Answers must say they come from an image read and are not an approved spec.

### 2.6 Reference tolerance checker (tested)
This is the core of "exact pass/fail in Python". It was run against the cases listed in the comments before being put here.

```python
from dataclasses import dataclass
from decimal import Decimal as D
from enum import Enum

class Verdict(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    INDETERMINATE = "INDETERMINATE"      # inside the uncertainty band: cannot prove pass or fail -> re-measure / human

@dataclass(frozen=True)
class Tolerance:
    kind: str                            # symmetric | bilateral | unilateral | limits
    nominal: D | None = None
    plus: D | None = None                # symmetric: +/- plus; bilateral/unilateral: upper deviation
    minus: D | None = None               # bilateral/unilateral: lower deviation (negative or 0)
    lower: D | None = None               # limits: absolute limits
    upper: D | None = None

    def limits(self) -> tuple[D, D]:
        if self.kind == "limits":
            lo, hi = self.lower, self.upper
        elif self.kind == "symmetric":
            lo, hi = self.nominal - self.plus, self.nominal + self.plus
        elif self.kind in ("bilateral", "unilateral"):
            lo, hi = self.nominal + self.minus, self.nominal + self.plus
        else:
            raise ValueError(f"unsupported tolerance kind: {self.kind}")
        if lo > hi:
            raise ValueError("lower limit above upper limit (bad extraction?)")
        return lo, hi

def judge(measured: D, tol: Tolerance, U: D = D(0)) -> Verdict:
    """ISO 14253-1 default rule: PASS only inside the limits shrunk by U; FAIL only outside the limits widened by U."""
    lo, hi = tol.limits()
    if lo + U <= measured <= hi - U:
        return Verdict.PASS
    if measured < lo - U or measured > hi + U:
        return Verdict.FAIL
    return Verdict.INDETERMINATE
```

Checked: `12.00 +0.02/-0.01` gives limits 11.99-12.02; `12.02` PASS (exactly on the limit, no float error: `0.1+0.2 != 0.3` in float but is equal in Decimal);
`12.021` FAIL; `12.015` with U=0.01 INDETERMINATE; angle `45 ±0.5` and hardness limits `40-48 HRC` behave correctly; an inverted limit raises.
Two decisions are for **QA, not engineering**: whether to apply the uncertainty guard band (the default above is conservative), and which general tolerance class (for example ISO 2768-m) applies when a dimension has no explicit tolerance.
Fits such as `H7/g6` need an ISO 286 lookup table; until it exists they are `needs_review`.

### 2.7 Accuracy gate (how you know it works)
Build a **gold set of 50-100 drawings**, labelled once by a QA engineer, never used for tuning prompts. Report per field type:
exact-match rate of nominal, of each tolerance value, of unit; **hallucination rate** (values that are not on the drawing); and review rate.
Suggested rule: while exact-match on numeric fields is below your chosen bar (agree it with QA, for example 99%), approval stays **100% human**.
Every human correction is stored and becomes fine-tuning / few-shot data; the paper above suggests this is where the biggest gain comes from.

---

## 3. CV (YOLO) to server API contract

### 3.1 Principles
- **The edge decides, the server records.** The YOLO project rejects locally in real time. The server is for evidence, audit report and analytics; if it is down or slow the line keeps running and the edge **buffers and retries**.
- **Idempotent.** A retry must never create a second event or a second report.
- **Tamper-evident.** SHA-256 of every image, model version and weights hash, so an audit can prove which model made which call on which image.
- **No cross-company trust.** The company is taken from the API key, never from the request body.

### 3.2 Endpoints

| Method + path | Purpose | Success |
|---|---|---|
| `POST /v1/qc/events` | Submit one inspection result (reject, or uncertain; optionally pass for sampling) | `202` + `{event_id, status:"received"}`; `200` on idempotent repeat |
| `GET /v1/qc/events/{event_id}` | Status and result (`received -> stored -> analysed -> reported`) | `200` |
| `POST /v1/qc/events/{event_id}/review` | Human verdict (confirm / false-reject / missed-defect) and notes | `200` |
| `GET /v1/qc/reports/{report_id}` | QC audit report (PDF + JSON); batch reports by lot/shift via `POST /v1/qc/reports` | `200` |
| `GET /v1/qc/health` | Edge heartbeat / queue depth | `200` |

Auth: `X-API-Key` (the existing key mechanism) with a **new narrow permission** (for example `qc:write`) so the CV key cannot read chats or other companies' data.
Required headers: `Idempotency-Key: <event_id>`, `Content-Type: multipart/form-data`. Errors use the repo's current error shape; `413` for oversize, `422` for schema errors,
`409` if the same `event_id` arrives with different content, `429` with `Retry-After` for backpressure (the edge must honour it).

### 3.3 Request: multipart with a `metadata` JSON part and one or more `image` parts

```jsonc
// part "metadata" (application/json)
{
  "schema_version": "1.0",
  "event_id": "0194f3c2-7b1e-7c3a-9a55-3d1d2f0c9a10",       // UUIDv7 generated on the edge (idempotency key)
  "captured_at": "2026-11-19T09:41:07.312Z",                  // UTC, from the camera trigger, not the upload time
  "station": {"line_id": "L2", "station_id": "ST-04", "camera_id": "CAM-A"},
  "part": {"part_number": "SF-0125", "drawing_revision": "C", "lot": "L2611-07", "serial": "0042"},
  "decision": {"verdict": "REJECT",                           // REJECT | UNCERTAIN | PASS
               "reason_codes": ["SCRATCH", "BURR"], "decided_by": "yolo", "latency_ms": 38},
  "model": {"name": "forceps-defect", "version": "2.3.1", "weights_sha256": "9c1f...e0",
            "task": "detect",                                  // detect | segment
            "imgsz": 1280, "conf_threshold": 0.35, "nms_iou": 0.5, "classes_version": "2026-10"},
  "detections": [
    {"class": "scratch", "class_id": 2, "confidence": 0.91,
     "bbox_xyxy_px": [812, 440, 1033, 468],                   // image pixels, origin top-left
     "mask_polygon_px": null,                                 // when task = segment
     "area_px": 6188, "length_mm": 1.9}                       // *_mm only if the camera is calibrated
  ],
  "images": [
    {"part": "image_0", "role": "original", "width": 4096, "height": 3000, "format": "png",
     "sha256": "ab12...ff", "px_per_mm": 52.4, "calibration_id": "CAL-2026-10-30"},
    {"part": "image_1", "role": "annotated", "width": 1920, "height": 1406, "format": "jpeg", "sha256": "77de...01"}
  ],
  "operator_action": {"diverted_to": "REJECT_BIN_2", "at": "2026-11-19T09:41:07.401Z"}
}
// parts "image_0", "image_1": binary. "original" must be lossless (PNG/TIFF) and unmodified; "annotated" is for humans only.
```

Server-side checks: sizes within limit (suggest 25 MB per image, configurable), SHA-256 of each part equals the declared one, `bbox` inside the image, `confidence` in 0-1, known
`schema_version`, timestamp not in the future by more than a few minutes, `part_number + drawing_revision` exists (if not, still **accept and flag** `unknown_part`; never drop evidence).

### 3.4 What the server does with it
1. **Store** images immutably (content-addressed by hash, write-once; outside the web root), metadata in PostgreSQL, one audit-log row.
2. **Link to the drawing**: look up the approved spec for that part/revision. If a defect has a calibrated size (`length_mm`) and the spec has an applicable limit, the report states the comparison; otherwise it says "not comparable".
3. **Optional VLM second opinion** (background, low priority): a text description of the defect and an "agrees / disagrees with YOLO" note. It is **advisory only**; it never changes the stored YOLO verdict. A disagreement raises the event's review priority.
4. **Report**: PDF + JSON with part, lot, station, model name + version + weights hash, thresholds, original and annotated images with hashes, detections, drawing reference, human review result, timestamps. Generated from stored data by code, not written by the VLM.
5. **Metrics**: reject rate per lot / station / defect class, override rate (false rejects and missed defects), drift of confidence distribution.

### 3.5 Operational rules
- CV traffic must **not use the chat inference slot.** The API only stores and enqueues (cheap); VLM and report work runs in a separate worker lane, so a burst of 200 rejects cannot block a salesperson's chat.
- Edge buffering: on `5xx`, timeout or `429`, keep the event on disk and retry with exponential backoff and the same `event_id`; delete only after a `200/202`.
- Retention and access: set a retention period with QA (evidence for a regulated product is normally kept for years); only QA roles may download originals.

---

## 4. Accuracy of the image detection itself: is there a better plan?

Short answer: **keep YOLO as the detector; improve it through data, imaging and thresholds, and use the VLM only as a second stage.**

1. **Do not replace YOLO with a VLM for detection.** VLMs are weak at precise localisation of small defects, slow (seconds vs milliseconds), and non-deterministic.
   Use them to *explain* or *double-check* uncertain cases.
2. **Three-band decision instead of one threshold.** Missing a defect usually costs far more than a false reject. Choose thresholds from the precision-recall curve of a **held-out production test set**:
   `score >= T_high` auto-reject, `score <= T_low` auto-pass, in between send to a human (and optionally the VLM). Log everything in the band.
3. **Imaging beats model size.** Fixed camera, fixed distance, controlled and diffused lighting (use several light angles for scratches vs reflections on polished steel), no auto-exposure, lossless storage.
   Check that your smallest defect covers enough pixels to be learnable; if not, increase resolution or use tiled inference (SAHI-style) rather than a bigger network.
4. **Same preprocessing in training and production** (resize, colour, no JPEG re-compression of training images that production never sees).
5. **Hard negatives.** Collect the things that look like defects but are not (dust, water spots, reflections, laser marking) and label them as background. This usually reduces false rejects more than extra epochs.
6. **Fixed golden test set** that is never trained on, re-scored on every new model and monthly for drift. Release a new model only if per-class **recall at fixed precision** does not drop for any critical defect class.
7. **Feedback loop.** Every human override from `/review` is a new labelled sample. Retrain on a schedule (for example monthly), version every model, and keep the old one for rollback.
8. **If you have very few defect samples**, consider adding an anomaly-detection model (trained only on good parts) as a second signal next to YOLO. Treat this as an experiment to measure, not a guaranteed gain.
9. **If you need defect size against a tolerance**, use segmentation (YOLO-seg) plus a calibrated `px_per_mm`, so the length or area in mm can be compared with the drawing.

I cannot promise an accuracy figure without your data. Agree a **measurable target with QA first** (for example "recall >= X% for critical defects at precision >= Y%"),
then use the golden set to show whether you meet it. To tune the advice further I need: defect classes, smallest defect size, part finish (polished / matte), camera resolution, line speed, and how many labelled images exist.

---

## 5. Phased roadmap and repo impact

| Phase | Deliverable | Main repo changes |
|---|---|---|
| 2.0 | GPU upgrade, `llama-server --mmproj`, bake-off on 50 drawings | `deploy/ubuntu/companyai-chat.service`, `.env`; `scripts/` eval script |
| 2.1 | Image input in chat | `messages.py`, `ChatRequest`, provider payload; separate image lane in `app/core/concurrency.py` |
| 2.2 | Drawing ingest + native extraction + approval flow | new `app/services/drawing_extract.py`, `drawing_spec.py` (pydantic), repos with SQLite and PostgreSQL parity, admin routes (same pending/approve pattern as `regulatory`) |
| 2.3 | VLM fallback + re-read verification + gold-set eval | `app/services/drawing_vlm.py`, `scripts/drawing_eval.py` |
| 2.4 | Tolerance checker + inspection data import | `app/services/tolerance.py` (section 2.6), `/inspection` routes, tests |
| 2.5 | CV API + evidence store + reports | `app/api/routes/qc.py`, `qc` permission in `app/core/security.py`, report worker, retention job |
| 2.6 | Feedback loop and drift dashboard | `/review` data export for YOLO retraining, Prometheus metrics |

## 6. Open questions for the senior engineer / QA
1. Drawing sources: native DXF/DWG available, or only PDF/scans? (This decides how much of the pipeline can avoid the VLM.)
2. Drawing standard: ISO (2768, 286, first/third angle) or ASME Y14.5? Is GD&T in scope for phase 2 or only plus/minus dimensions, angles and hardness?
3. Where do measurements come from (CMM export, calipers, manual entry), and who owns instrument uncertainty and the decision rule?
4. Retention period and who may view original images and reports.
5. CV project: defect classes, camera setup, line speed, and number of events per hour (sizes the report worker).
6. Approved budget for the GPU (24 GB vs 48 GB) and whether training for the YOLO project will run on this server.
