"""Tests for the framework-free core. Fixtures mimic Tavily's /search response
shape (results[].url/title/content/published_date); the text is invented."""

from datetime import date

from grounding import (
    Verdict,
    DraftClaim,
    PassageRegistry,
    Status,
    check_claims,
    domain_of,
    figures,
    is_same_site,
    parse_date,
)

NEWS = {
    "results": [
        {
            "url": "https://www.technews.example/tavily-deal",
            "title": "Nebius buys Tavily",
            "published_date": "2026-02-11",
            "content": "Nebius agreed to acquire Tavily for about $400 million, sources said. "
            "[...] The deal gives Nebius an agent search layer for its Token Factory platform.",
        },
        {
            # Verbatim reprint of the first chunk on another site: syndication, not a second source.
            "url": "https://aggregator.example/copy",
            "title": "Nebius buys Tavily (reprint)",
            "published_date": "2026-02-12",
            "content": "Nebius agreed to acquire Tavily for about $400 million, sources said.",
        },
    ]
}

PRIMARY = {
    "results": [
        {
            "url": "https://nebius.com/newsroom/tavily",
            "title": "Nebius to acquire Tavily",
            "published_date": "Tue, 10 Feb 2026 13:00:00 GMT",
            "content": "Nebius Group today announced an agreement to acquire Tavily, the search layer for AI agents.",
        }
    ]
}


def make_registry(*responses):
    registry = PassageRegistry()
    for r in responses:
        registry.add_response(r)
    return registry


# ---- passages


def test_chunks_get_own_ids_and_reprints_are_stored_once():
    registry = make_registry(NEWS)
    assert len(registry) == 2  # two chunks from page 1; the reprint is skipped
    assert registry.get("P1").domain == "technews.example"
    assert "Token Factory" in registry.get("[p2]").text  # tolerant of the model's formatting


def test_registry_caps_total_passages():
    registry = PassageRegistry(max_passages=1)
    registry.add_response(NEWS)
    assert len(registry) == 1


def test_domain_helpers():
    assert domain_of("https://www.Nebius.com/x") == "nebius.com"
    assert is_same_site("docs.aws.amazon.com", "aws.amazon.com")
    assert not is_same_site("fakeaws.amazon.com.evil.io", "aws.amazon.com")
    assert not is_same_site("notnebius.com", "nebius.com")


def test_dates_and_figures():
    assert parse_date("2026-02-11T08:00:00Z") == date(2026, 2, 11)
    assert parse_date("Tue, 10 Feb 2026 13:00:00 GMT") == date(2026, 2, 10)
    assert parse_date("sometime") is None
    assert figures("raised $1,200M in 2026, up 3.5x") == {"1200", "2026", "3.5"}


# ---- claim checks


def test_claim_citing_unknown_passage_is_dropped():
    report = check_claims([DraftClaim("Tavily raised $1B.", ["P99"], "tavily.com")], make_registry(NEWS))
    assert report.claims == [] and len(report.dropped) == 1


def test_primary_from_anchor_search_and_figure_only_in_press():
    """The useful case: the company confirms the deal, but the price comes only from press."""
    registry = make_registry(NEWS)
    calls = []

    def anchor(claim, site):
        calls.append(site)
        return registry.add_response(PRIMARY)

    report = check_claims(
        [DraftClaim("Nebius agreed to acquire Tavily for about $400 million.", ["P1"], "nebius.com")],
        registry,
        anchor_search=anchor,
    )
    claim = report.claims[0]
    assert calls == ["nebius.com"] and report.anchor_searches == 1
    assert claim.status is Status.PRIMARY_PARTIAL
    assert claim.primary.domain == "nebius.com"
    assert claim.figures_not_in_primary == ["400"]
    assert claim.figures_not_in_any_source == []  # the press passage does carry 400


def test_primary_already_cited_needs_no_extra_search():
    registry = make_registry(NEWS, PRIMARY)
    nebius_id = next(p.id for p in (registry.get(f"P{i}") for i in range(1, 4)) if p.domain == "nebius.com")

    def anchor(claim, site):
        raise AssertionError("should not search when the primary is already cited")

    report = check_claims(
        [DraftClaim("Nebius announced it will acquire Tavily.", [nebius_id], "nebius.com")],
        registry,
        anchor_search=anchor,
    )
    assert report.claims[0].status is Status.PRIMARY and report.anchor_searches == 0


def test_no_primary_found_is_reported():
    registry = make_registry(NEWS)
    report = check_claims(
        [DraftClaim("Nebius agreed to acquire Tavily.", ["P1"], "nebius.com")],
        registry,
        anchor_search=lambda claim, site: [],
    )
    assert report.claims[0].status is Status.REPORTED


def test_anchor_results_from_other_sites_are_ignored():
    registry = make_registry(NEWS)
    report = check_claims(
        [DraftClaim("Nebius agreed to acquire Tavily.", ["P1"], "nebius.com")],
        registry,
        anchor_search=lambda claim, site: registry.add_response(
            {"results": [{"url": "https://nebius-fans.example/post", "content": "x" * 50}]}
        ),
    )
    assert report.claims[0].status is Status.REPORTED


def test_invented_figure_is_flagged_even_with_valid_citation():
    registry = make_registry(NEWS)
    report = check_claims([DraftClaim("Nebius paid $900 million for Tavily.", ["P1"], None)], registry)
    claim = report.claims[0]
    assert claim.status is Status.SYNTHESIS
    assert claim.figures_not_in_any_source == ["900"] and claim.flagged


def test_year_supported_by_publish_date():
    registry = make_registry(NEWS)
    report = check_claims([DraftClaim("In 2026 Nebius moved into agent search.", ["P2"], None)], registry)
    assert report.claims[0].figures_not_in_any_source == []


def test_stale_only_when_every_cited_passage_predates_window():
    registry = make_registry(NEWS)
    draft = DraftClaim("Nebius agreed to acquire Tavily.", ["P1"], None)
    assert check_claims([draft], registry, window_start=date(2026, 6, 1)).claims[0].stale
    assert not check_claims([draft], registry, window_start=date(2026, 1, 1)).claims[0].stale


def test_undated_passage_is_never_called_stale():
    registry = make_registry({"results": [{"url": "https://a.example", "content": "y" * 60}]})
    draft = DraftClaim("Something happened.", ["P1"], None)
    assert not check_claims([draft], registry, window_start=date(2026, 1, 1)).claims[0].stale


def test_summary_counts():
    registry = make_registry(NEWS)
    report = check_claims(
        [DraftClaim("Nebius agreed to acquire Tavily.", ["P1"], None), DraftClaim("Made up.", [], None)],
        registry,
    )
    s = report.summary()
    assert s["claims_shown"] == 1 and s["claims_dropped_no_valid_passage"] == 1 and s["synthesis"] == 1


# ---- judge


def judged(verdicts, *, drafts=None, anchor=None, registry=None):
    """Run check_claims with a fake judge returning the given verdicts."""
    registry = registry or make_registry(NEWS)
    drafts = drafts or [DraftClaim("Nebius agreed to acquire Tavily for about $400 million.", ["P1"], "nebius.com")]
    seen: dict = {}

    def judge(items):
        seen["items"] = items
        return verdicts(items) if callable(verdicts) else verdicts

    report = check_claims(drafts, registry, anchor_search=anchor, judge=judge)
    return report, seen.get("items", [])


def test_judge_sees_primary_passage_first():
    registry = make_registry(NEWS)
    report, items = judged(
        [],
        anchor=lambda c, s: registry.add_response(PRIMARY),
        registry=registry,
    )
    assert items[0].passage.domain == "nebius.com"  # primary ranked ahead of press
    assert items[0].claim_index == 0 and len(items) <= 3


def test_supporting_span_from_primary_gives_primary_partial():
    registry = make_registry(NEWS)
    report, items = judged(
        lambda its: [
            Verdict(0, p.id, "supports", "agreement to acquire Tavily")
            if p.domain == "nebius.com"
            else Verdict(0, p.id, "supports", "about $400 million")
            for p in (i.passage for i in its)
        ],
        anchor=lambda c, s: registry.add_response(PRIMARY),
        registry=registry,
    )
    claim = report.claims[0]
    assert claim.judged and claim.status is Status.PRIMARY_PARTIAL
    assert claim.figures_not_in_primary == ["400"]


def test_primary_downgraded_when_own_site_does_not_state_claim():
    """The fix for relevance-is-not-support: a topical hit alone no longer earns PRIMARY."""
    registry = make_registry(NEWS)
    report, _ = judged(
        lambda its: [
            Verdict(0, i.passage.id, "not_addressed" if i.passage.domain == "nebius.com" else "supports",
                    "" if i.passage.domain == "nebius.com" else "about $400 million")
            for i in its
        ],
        anchor=lambda c, s: registry.add_response(PRIMARY),
        registry=registry,
    )
    assert report.claims[0].status is Status.REPORTED and report.claims[0].primary is None
    # Seen live: a rejected primary must not leave a stale "figures not on own site" line.
    assert report.claims[0].figures_not_in_primary == []


def test_contradiction_wins_over_support():
    report, _ = judged(
        lambda its: [
            Verdict(0, its[0].passage.id, "supports", "about $400 million"),
            Verdict(0, its[1].passage.id, "contradicts", "Token Factory platform"),
        ],
        drafts=[DraftClaim("Nebius agreed to acquire Tavily for about $400 million.", ["P1", "P2"], None)],
    )
    claim = report.claims[0]
    assert claim.status is Status.CONTESTED and claim.flagged
    assert len(claim.contradicting) == 1


def test_claim_with_no_support_is_unsupported():
    report, _ = judged(lambda its: [Verdict(0, i.passage.id, "not_addressed") for i in its])
    assert report.claims[0].status is Status.UNSUPPORTED and report.claims[0].flagged


def test_invented_span_is_discarded():
    """A judge quoting text that isn't in the passage is not trusted."""
    report, _ = judged(lambda its: [Verdict(0, i.passage.id, "supports", "a sentence never published") for i in its])
    assert report.claims[0].status is Status.UNSUPPORTED and report.claims[0].support == []


def test_verdict_for_unknown_passage_or_claim_is_ignored():
    report, _ = judged([Verdict(0, "P99", "supports", "x"), Verdict(7, "P1", "supports", "about $400 million")])
    assert report.claims[0].support == [] and report.claims[0].status is Status.UNSUPPORTED


def test_judge_failure_keeps_provenance_labels():
    def boom(items):
        raise RuntimeError("judge offline")

    report = check_claims(
        [DraftClaim("Nebius agreed to acquire Tavily.", ["P1"], None)], make_registry(NEWS), judge=boom
    )
    assert report.claims[0].status is Status.SYNTHESIS and not report.claims[0].judged
    assert report.judge_error and "judge offline" in report.judge_error


def test_no_judge_leaves_claims_unjudged():
    report = check_claims([DraftClaim("Nebius agreed to acquire Tavily.", ["P1"], None)], make_registry(NEWS))
    assert not report.claims[0].judged and report.summary()["judged"] == 0
