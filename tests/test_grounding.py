"""Tests for the framework-free core. Fixtures mimic Tavily's /search response
shape (results[].url/title/content/published_date); the text is invented."""

from datetime import date

from grounding import (
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
