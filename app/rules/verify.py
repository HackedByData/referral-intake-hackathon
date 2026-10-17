"""Deterministic check that every quote the model cites really appears on (or next to) the cited page."""
import re

from rapidfuzz import fuzz

from app.schemas import CaseSummary, Evidence, PacketText, RuleResult

FUZZY_THRESHOLD = 90
MIN_FUZZY_LEN = 12
_DROP = re.compile(r"[^a-z0-9.%\s]")
_SPACE = re.compile(r"\s+")
_NUM = re.compile(r"\d+(?:\.\d+)?")
NEGATIONS = {"no", "not", "denies", "denied", "without", "negative", "never", "none"}


def normalize(s: str) -> str:
    s = _DROP.sub(" ", s.lower())
    return _SPACE.sub(" ", s).strip()


def quote_on_page(quote: str, page_md: str) -> bool:
    q, text = normalize(quote), normalize(page_md)
    if len(q) < 3:
        return False
    if q in text:
        return True
    if len(q) < MIN_FUZZY_LEN:
        return False
    # Fuzzy path only: numbers and negations must survive exactly, or the meaning may have flipped.
    page_tokens = set(text.split())
    for tok in q.split():
        if (_NUM.fullmatch(tok) or tok in NEGATIONS) and tok not in page_tokens:
            return False
    return fuzz.partial_ratio(q, text) >= FUZZY_THRESHOLD


def verify_evidence(evidence: list[Evidence], packet: PacketText) -> list[Evidence]:
    pages = {p.page: p.markdown for p in packet.pages}
    out = []
    for e in evidence:
        found = next((c for c in (e.page, e.page - 1, e.page + 1)
                      if c in pages and quote_on_page(e.quote, pages[c])), None)
        out.append(e.model_copy(update={"page": found if found is not None else e.page,
                                        "verified": found is not None}))
    return out


def verify_results(results: list[RuleResult], packet: PacketText) -> list[RuleResult]:
    return [r.model_copy(update={"evidence": verify_evidence(r.evidence, packet)}) for r in results]


def verify_summary(summary: CaseSummary, packet: PacketText) -> CaseSummary:
    conflicts = [c.model_copy(update={"statements": verify_evidence(c.statements, packet)})
                 for c in summary.conflicts]
    return summary.model_copy(update={"conflicts": conflicts})
