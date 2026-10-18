"""Offline tests for the planted-error harness."""
import json
from pathlib import Path

from planted import load_claims, passages_from, plant, rebuild


def test_plant_makes_each_kind_of_error():
    kinds = {m.kind for m in plant("Nebius agreed to acquire Tavily for about $400 million in 2026.")}
    assert {"number", "actor", "year"} <= kinds
    numbers = next(m.text for m in plant("raised $400 million") if m.kind == "number")
    assert "400" not in numbers


def test_plant_swaps_the_actor():
    actor = next(m.text for m in plant("OpenAI expanded web search.") if m.kind == "actor")
    assert actor.startswith("Anthropic") and "OpenAI" not in actor


def test_plant_returns_nothing_for_a_claim_with_no_handles():
    assert plant("Consolidation continued.") == []


def test_round_trip_from_a_run_record(tmp_path: Path):
    record = {"claims": [{
        "text": "Nebius agreed to acquire Tavily for about $400 million.",
        "primary_url": "https://nebius.com/newsroom/tavily",
        "evidence": [
            {"id": "P4", "url": "https://nebius.com/newsroom/tavily", "published": "2026-02-10",
             "text": "Nebius Group today announced an agreement to acquire Tavily."},
            {"id": "P1", "url": "https://technews.example/deal", "published": "2026-02-11",
             "text": "Nebius agreed to acquire Tavily for about $400 million."},
        ],
        "verdicts": [],
    }]}
    (tmp_path / "01.json").write_text(json.dumps(record))

    claims = load_claims(tmp_path)
    passages = passages_from(tmp_path)
    assert len(claims) == 1 and set(passages) == {"P1", "P4"}
    assert passages["P4"].domain == "nebius.com" and passages["P4"].published is not None

    mutated = next(m for m in plant(claims[0]["text"]) if m.kind == "number")
    rebuilt = rebuild(claims[0], mutated.text, passages)
    assert rebuilt is not None
    assert rebuilt.primary is not None and rebuilt.primary.id == "P4"
    assert len(rebuilt.evidence) == 2 and "400" not in rebuilt.text
