"""Page-by-page transcription of image-only faxes with Claude vision."""
import asyncio
import base64
import logging
from collections.abc import Callable
from pathlib import Path

from app.ingest.render import cached_page_image, file_sha256, page_count
from app.schemas import PacketText, PageMarkdown

log = logging.getLogger(__name__)

FAILED_MARKER = "[transcription failed]"
# Orientation as reported -> clockwise degrees that make the content upright.
CORRECTION = {"rotated_90": 270, "rotated_180": 180, "rotated_270": 90}
LEGIBILITY_RANK = {"good": 2, "partial": 1, "poor": 0}

OCR_SYSTEM = """You transcribe one page of a faxed hospital referral packet for a recuperative-care intake team.
- Transcribe exactly what is on the page, in reading order, as markdown. Keep tables as markdown tables and checkboxes as [x] / [ ].
- Black boxes are privacy redactions: write [REDACTED] in their place. Never guess what was under them.
- Write [illegible] for anything you cannot read. Never complete, infer or correct clinical content.
- Mark handwritten text with "(handwritten)" after it.
- orientation describes the page as shown to you: upright; rotated_90 (content turned 90 degrees clockwise, text runs top to bottom); rotated_180 (upside down); rotated_270 (content turned 90 degrees counter-clockwise).
- doc_type: one of fax cover, IHH referral form, health plan authorization, face sheet, H&P, progress note, nursing note, PT evaluation, OT evaluation, social work note, psychiatry consult, labs, vitals, imaging, ECG, medication record, physician orders, wound note, near-blank, other.
- legibility: good, partial or poor."""


async def _ocr(llm, image: bytes, media_type: str, page: int) -> PageMarkdown:
    content = [
        {"type": "image", "source": {"type": "base64", "media_type": media_type,
                                     "data": base64.standard_b64encode(image).decode("ascii")}},
        {"type": "text", "text": f"This is page {page} of the packet. Transcribe it."},
    ]
    return await llm.parse(system=OCR_SYSTEM, content=content, output=PageMarkdown, effort="low", max_tokens=8000)


async def _transcribe_page(llm, pdf_path: Path, packet_hash: str, page: int, cache_root: Path) -> PageMarkdown:
    for attempt in range(2):
        try:
            image, media = await asyncio.to_thread(cached_page_image, pdf_path, packet_hash, page, cache_root)
            result = await _ocr(llm, image, media, page)
            fix = CORRECTION.get(result.orientation)
            if fix:
                image2, media2 = await asyncio.to_thread(cached_page_image, pdf_path, packet_hash, page, cache_root, fix)
                retry = await _ocr(llm, image2, media2, page)
                if LEGIBILITY_RANK[retry.legibility] >= LEGIBILITY_RANK[result.legibility]:
                    result = retry.model_copy(update={"orientation": result.orientation})
            return result.model_copy(update={"page": page})
        except Exception as e:
            log.warning("Page %s transcription attempt %s failed: %s", page, attempt + 1, e)
    return PageMarkdown(page=page, doc_type="other", orientation="unknown", legibility="poor", markdown=FAILED_MARKER)


async def transcribe_packet(llm, pdf_path: Path, cache_root: Path, concurrency: int = 8,
                            on_progress: Callable[[int, int], None] | None = None) -> PacketText:
    packet_hash = await asyncio.to_thread(file_sha256, pdf_path)
    cache_file = cache_root / packet_hash / "packet.json"
    if cache_file.exists():
        return PacketText.model_validate_json(cache_file.read_text("utf-8"))

    total = await asyncio.to_thread(page_count, pdf_path)
    sem = asyncio.Semaphore(concurrency)
    done = 0

    async def one(page: int) -> PageMarkdown:
        nonlocal done
        async with sem:
            result = await _transcribe_page(llm, pdf_path, packet_hash, page, cache_root)
        done += 1
        if on_progress:
            on_progress(done, total)
        return result

    pages = await asyncio.gather(*(one(p) for p in range(1, total + 1)))
    packet = PacketText(packet_hash=packet_hash, page_count=total, pages=sorted(pages, key=lambda p: p.page))
    if all(p.markdown != FAILED_MARKER for p in packet.pages):
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(packet.model_dump_json(), "utf-8")
    return packet
