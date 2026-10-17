# Ingest module (`app/ingest/`)

**Purpose:** turn an image-only fax PDF into per-page markdown (`PacketText`) with page numbers, doc types,
orientation and legibility.

**Public API**
- `render.validate_pdf(path) -> int`: raises `InvalidPDF` on non-PDF, empty or encrypted files.
- `render.render_page(pdf, page, rotate=0, dpi=150) -> (bytes, media_type)`: `rotate` is clockwise degrees.
- `render.cached_page_image(pdf, hash, page, cache_root, rotate=0)`: renders once into `data/cache/{hash}/pages/`.
- `ocr.transcribe_packet(llm, pdf, cache_root, concurrency=8, on_progress=None) -> PacketText`.

**Behavior**
- One Claude vision call per page (effort `low`), concurrency 8. A page reported as rotated is re-rendered upright and
  transcribed again; the better legibility wins and `orientation` keeps the original value, so the image endpoint can
  correct it with `CORRECTION`.
- A page that fails twice becomes `[transcription failed]` (legibility `poor`), and the packet is then **not** cached,
  so a retry re-attempts it.
- Cache key: sha256 of the PDF bytes → `data/cache/{hash}/packet.json`. Delete it to force a re-OCR (e.g. after a prompt change).

**Testing:** `pytest tests/test_render.py tests/test_ocr.py` (fake LLM, no network).

**Known limits:** ~1 call per page (34 pages ≈ 1–2 min). Handwriting quality depends on the scan.
