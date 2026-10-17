from pathlib import Path

import pymupdf
import pytest

from app.ingest.ocr import CORRECTION, FAILED_MARKER, transcribe_packet
from app.llm import LLMError
from app.schemas import PageMarkdown
from tests.fakes import FakeLLM


@pytest.fixture
def pdf(tmp_path: Path) -> Path:
    doc = pymupdf.open()
    for i in range(3):
        doc.new_page(width=200, height=200).insert_text((20, 50), f"invented page {i + 1}")
    path = tmp_path / "p.pdf"
    doc.save(path)
    return path


def upright(output, system, content):
    return PageMarkdown(page=0, doc_type="other", orientation="upright", legibility="good", markdown="text")


async def test_transcribes_all_pages_in_order_with_page_numbers(pdf, tmp_path):
    llm = FakeLLM(upright)
    progress = []
    packet = await transcribe_packet(llm, pdf, tmp_path / "c", concurrency=2,
                                     on_progress=lambda d, n: progress.append((d, n)))
    assert [p.page for p in packet.pages] == [1, 2, 3]
    assert packet.page_count == 3 and len(llm.calls) == 3
    assert progress[-1] == (3, 3)
    first = llm.calls[0]["content"]
    assert first[0]["type"] == "image" and first[0]["source"]["media_type"] == "image/png"
    assert llm.calls[0]["effort"] == "low"


async def test_second_run_uses_cache(pdf, tmp_path):
    await transcribe_packet(FakeLLM(upright), pdf, tmp_path / "c")
    llm2 = FakeLLM(upright)
    packet = await transcribe_packet(llm2, pdf, tmp_path / "c")
    assert llm2.calls == [] and packet.page_count == 3


async def test_rotated_page_is_retried_upright(pdf, tmp_path):
    state = {"n": 0}

    def handler(output, system, content):
        state["n"] += 1
        if state["n"] == 1:
            return PageMarkdown(page=0, doc_type="labs", orientation="rotated_90", legibility="poor", markdown="garbled")
        return PageMarkdown(page=0, doc_type="labs", orientation="upright", legibility="good", markdown="clean")

    llm = FakeLLM(handler)
    packet = await transcribe_packet(llm, pdf, tmp_path / "c", concurrency=1)
    p1 = packet.pages[0]
    assert p1.markdown == "clean" and p1.orientation == "rotated_90"  # original orientation kept for the image endpoint
    assert CORRECTION["rotated_90"] == 270
    assert llm.calls[1]["content"][0]["source"]["data"] != llm.calls[0]["content"][0]["source"]["data"]


def _first_call_rotated(second):
    state = {"n": 0}

    def handler(output, system, content):
        state["n"] += 1
        if state["n"] == 1:
            return PageMarkdown(page=0, doc_type="labs", orientation="rotated_90", legibility="partial", markdown="first")
        if state["n"] == 2:
            return second()
        return upright(output, system, content)

    return handler


async def test_worse_retry_keeps_first_transcription(pdf, tmp_path):
    handler = _first_call_rotated(lambda: PageMarkdown(
        page=0, doc_type="labs", orientation="upright", legibility="poor", markdown="worse"))
    packet = await transcribe_packet(FakeLLM(handler), pdf, tmp_path / "c", concurrency=1)
    assert packet.pages[0].markdown == "first"


async def test_failing_retry_keeps_first_transcription(pdf, tmp_path):
    handler = _first_call_rotated(lambda: LLMError("retry boom"))
    packet = await transcribe_packet(FakeLLM(handler), pdf, tmp_path / "c", concurrency=1)
    assert packet.pages[0].markdown == "first" and packet.pages[0].markdown != FAILED_MARKER


async def test_failed_page_does_not_stop_run_and_is_not_cached(pdf, tmp_path):
    def handler(output, system, content):
        if "page 2" in content[-1]["text"]:
            return LLMError("boom")
        return upright(output, system, content)

    packet = await transcribe_packet(FakeLLM(handler), pdf, tmp_path / "c")
    assert packet.pages[1].markdown == FAILED_MARKER and packet.pages[1].legibility == "poor"
    assert not (tmp_path / "c" / packet.packet_hash / "packet.json").exists()
