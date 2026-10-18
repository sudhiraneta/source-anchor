"""Source anchoring for research agents built on Tavily.

Framework-agnostic: this module never imports LangChain. It takes Tavily
search responses and a model's draft claims, and decides how far each claim
can be trusted. Any agent runtime that can call Tavily can use it.

The idea in one line: a claim is only shown if it cites a passage Tavily
actually returned, and claims about an organization are checked against that
organization's own website rather than against whoever repeated the news.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date
from email.utils import parsedate_to_datetime
from enum import Enum
from urllib.parse import urlparse

# Tavily joins multiple chunks from one page with this separator when
# chunks_per_source > 1. Splitting on it gives each chunk its own ID.
CHUNK_SEPARATOR = "[...]"
MIN_PASSAGE_CHARS = 40
FIGURE_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


# ---------------------------------------------------------------- passages


@dataclass(frozen=True)
class Passage:
    id: str
    url: str
    domain: str
    title: str
    text: str
    published: date | None


def domain_of(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host.removeprefix("www.")


def is_same_site(domain: str, site: str) -> bool:
    """docs.aws.amazon.com belongs to aws.amazon.com; aws-news.com does not."""
    site = site.lower().removeprefix("www.").strip("/")
    return domain == site or domain.endswith("." + site)


def parse_date(value: object) -> date | None:
    """Tavily returns ISO dates or RFC 2822 strings; anything else is unknown."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        pass
    try:
        return parsedate_to_datetime(value).date()
    except (TypeError, ValueError):
        return None


def _normalize(text: str) -> str:
    return " ".join(text.lower().split())


class PassageRegistry:
    """Numbers every passage Tavily returns, across all searches in a run.

    The model sees passages as [P1], [P2]... and may only cite those IDs.
    Verbatim duplicates (syndicated reprints) are stored once, so a story
    copied by ten sites does not look like ten sources.
    """

    def __init__(self, max_passages: int = 60) -> None:
        self.max_passages = max_passages
        self._by_id: dict[str, Passage] = {}
        self._seen_text: set[str] = set()

    def __len__(self) -> int:
        return len(self._by_id)

    def get(self, passage_id: str) -> Passage | None:
        return self._by_id.get(passage_id.strip().strip("[]").upper())

    def add_response(self, response: dict) -> list[Passage]:
        added: list[Passage] = []
        for result in response.get("results", []):
            url = result.get("url", "")
            published = parse_date(result.get("published_date"))
            chunks = str(result.get("content", "")).split(CHUNK_SEPARATOR)
            for chunk in chunks:
                text = " ".join(chunk.split())
                key = _normalize(text)
                if len(text) < MIN_PASSAGE_CHARS or key in self._seen_text:
                    continue
                if len(self._by_id) >= self.max_passages:
                    return added
                self._seen_text.add(key)
                passage = Passage(
                    id=f"P{len(self._by_id) + 1}",
                    url=url,
                    domain=domain_of(url),
                    title=result.get("title", ""),
                    text=text,
                    published=published,
                )
                self._by_id[passage.id] = passage
                added.append(passage)
        return added


def render_for_model(passages: Iterable[Passage]) -> str:
    """Compact text the model reads. IDs first, so citing them is natural."""
    lines = []
    for p in passages:
        when = p.published.isoformat() if p.published else "undated"
        lines.append(f"[{p.id}] {p.domain} ({when}) {p.title}\n{p.text}")
    return "\n\n".join(lines) or "No results."


# ------------------------------------------------------------------ checks


class Status(str, Enum):
    PRIMARY = "primary"  # the organization's own site backs the claim and its figures
    PRIMARY_PARTIAL = "primary-partial"  # own site found, but some figures only from others
    REPORTED = "reported"  # only third-party sources
    SYNTHESIS = "synthesis"  # not about one organization's own action


@dataclass(frozen=True)
class DraftClaim:
    """What the model returns. source_domain is the official site of the
    organization the claim is about, or None for market-level synthesis."""

    text: str
    passage_ids: list[str]
    source_domain: str | None = None


@dataclass
class CheckedClaim:
    text: str
    status: Status
    evidence: list[Passage]
    primary: Passage | None = None
    figures_not_in_primary: list[str] = field(default_factory=list)
    figures_not_in_any_source: list[str] = field(default_factory=list)
    stale: bool = False

    @property
    def flagged(self) -> bool:
        return bool(self.figures_not_in_any_source) or self.stale


@dataclass
class Report:
    claims: list[CheckedClaim]
    dropped: list[DraftClaim]
    window_start: date | None
    anchor_searches: int

    def summary(self) -> dict:
        counts = {s.value: 0 for s in Status}
        for c in self.claims:
            counts[c.status.value] += 1
        return {
            "claims_shown": len(self.claims),
            "claims_dropped_no_valid_passage": len(self.dropped),
            **counts,
            "flagged_unsupported_figure": sum(bool(c.figures_not_in_any_source) for c in self.claims),
            "flagged_stale": sum(c.stale for c in self.claims),
            "anchor_searches": self.anchor_searches,
        }


AnchorSearch = Callable[[str, str], list[Passage]]
"""(claim_text, site) -> passages from that site only. Injected so the core
stays free of any HTTP client."""


def figures(text: str) -> set[str]:
    """Numbers in a sentence, normalized: '$1,200M' -> '1200'."""
    return {m.replace(",", "") for m in FIGURE_RE.findall(text)}


def _supported(figs: set[str], passages: list[Passage]) -> set[str]:
    """Figures that appear in a passage. A year also counts as supported when
    a passage was published that year (articles rarely restate their own year)."""
    in_text = set().union(*(figures(p.text) for p in passages)) if passages else set()
    years = {str(p.published.year) for p in passages if p.published}
    return {f for f in figs if f in in_text or f in years}


def check_claims(
    drafts: Iterable[DraftClaim],
    registry: PassageRegistry,
    anchor_search: AnchorSearch | None = None,
    window_start: date | None = None,
) -> Report:
    claims: list[CheckedClaim] = []
    dropped: list[DraftClaim] = []
    searches = 0

    for draft in drafts:
        evidence = [p for pid in draft.passage_ids if (p := registry.get(pid))]
        if not evidence:
            dropped.append(draft)
            continue

        claim_figs = figures(draft.text)
        status, primary, primary_candidates = Status.SYNTHESIS, None, []

        if draft.source_domain:
            site = draft.source_domain
            primary_candidates = [p for p in evidence if is_same_site(p.domain, site)]
            if not primary_candidates and anchor_search:
                searches += 1
                primary_candidates = [
                    p for p in anchor_search(draft.text, site) if is_same_site(p.domain, site)
                ]
            if primary_candidates:
                # Prefer the primary passage that confirms the most figures.
                primary = max(primary_candidates, key=lambda p: len(_supported(claim_figs, [p])))
                missing = claim_figs - _supported(claim_figs, [primary])
                status = Status.PRIMARY_PARTIAL if missing else Status.PRIMARY
            else:
                status = Status.REPORTED

        all_sources = evidence + ([primary] if primary else [])
        dated = [p.published for p in evidence if p.published]
        claims.append(
            CheckedClaim(
                text=draft.text,
                status=status,
                evidence=evidence,
                primary=primary,
                figures_not_in_primary=sorted(claim_figs - _supported(claim_figs, [primary]))
                if primary
                else [],
                figures_not_in_any_source=sorted(claim_figs - _supported(claim_figs, all_sources)),
                # Stale only when every cited passage is dated and all predate the window.
                stale=bool(window_start and dated and len(dated) == len(evidence)
                           and max(dated) < window_start),
            )
        )

    return Report(claims=claims, dropped=dropped, window_start=window_start, anchor_searches=searches)
