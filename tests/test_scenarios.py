"""Research-concise question shapes, end to end through the real agent loop.

Each scenario is a question shape a user of a research assistant would ask,
run with a scripted model and a configurable fake Tavily, and checked against
the label the design says it should get. Live runs can only cover a few
questions; these cover the shapes.
"""

from datetime import date
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from grounding import Status
from research_agent import run_research

TODAY = date(2026, 10, 16)


class Model(BaseChatModel):
    """Scripted writer. If `judge_label` is set, also acts as the judge and
    quotes the first sentence of every passage with that label."""

    script: list[AIMessage]
    judge_label: str | None = None

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "Model":
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=self.script.pop(0))])

    def with_structured_output(self, schema, **kwargs):
        label = self.judge_label or "supports"

        class _R:
            def invoke(self, messages, **_):
                body = messages[-1].content
                verdicts = []
                for n, block in enumerate(body.split("[item ")[1:]):
                    passage = block.split("): ", 1)[1].strip()
                    # A prefix of the passage is always a real span in it.
                    span = passage[:50] if label != "not_addressed" else ""
                    verdicts.append({"id": n, "label": label, "span": span})
                return schema.model_validate({"verdicts": verdicts})

        return _R()


class Tavily:
    """Fake Tavily: `web` is the open-search result set; `sites` maps a domain
    to what a restricted search on it returns."""

    def __init__(self, web: list[dict], sites: dict[str, list[dict]] | None = None) -> None:
        self.web, self.sites, self.calls = web, sites or {}, []

    def search(self, **p) -> dict:
        self.calls.append(p)
        if p.get("include_domains"):
            return {"results": self.sites.get(p["include_domains"][0], [])}
        return {"results": self.web}


def passage(url: str, text: str, published: str = "2026-06-01") -> dict:
    return {"url": url, "published_date": published, "content": text}


def script(answer: dict, query: str = "q", start: str | None = "2026-01-01") -> list[AIMessage]:
    args = {"query": query, **({"start_date": start} if start else {})}
    return [
        AIMessage(content="", tool_calls=[{"name": "web_search", "args": args, "id": "s"}]),
        AIMessage(content="", tool_calls=[{"name": "ResearchAnswer", "args": answer, "id": "a"}]),
    ]


def run(answer, web, sites=None, judge="supports", strict=False, start="2026-01-01"):
    tav = Tavily(web, sites)
    _, report, log = run_research("q", Model(script=script(answer, start=start), judge_label=judge),
                                  tav, today=TODAY, strict=strict)
    return report, tav, log


# ---------------------------------------------------------------- shapes


def test_market_trend_question_is_all_synthesis_and_no_anchor_searches():
    """'What changed in the AI search market this year?' — no originator."""
    web = [passage("https://a.example/t", "AI search tools captured 12% share in 2026, up from 5%.")]
    ans = {"summary": "s", "claims": [
        {"text": "AI search tools captured 12% share, up from 5%.", "passage_ids": ["P1"], "source_domain": None}]}
    report, tav, _ = run(ans, web)
    assert [c.status for c in report.claims] == [Status.SYNTHESIS]
    assert report.anchor_searches == 0 and not any(c.get("include_domains") for c in tav.calls)


def test_single_org_announcement_confirmed_on_own_site_is_primary():
    """'What did OpenAI announce?' — the own page states it."""
    web = [passage("https://news.example/x", "OpenAI launched the Agents API beta on September 10.")]
    sites = {"openai.com": [passage("https://openai.com/agents", "Introducing the Agents API, launched September 10.")]}
    ans = {"summary": "s", "claims": [
        {"text": "OpenAI launched the Agents API beta on September 10.", "passage_ids": ["P1"], "source_domain": "openai.com"}]}
    report, tav, _ = run(ans, web, sites)
    claim = report.claims[0]
    assert claim.status is Status.PRIMARY and claim.primary.domain == "openai.com"
    assert report.anchor_searches == 1


def test_org_claim_own_site_silent_is_reported():
    web = [passage("https://news.example/x", "OpenAI improved prompt caching for GPT-6.")]
    ans = {"summary": "s", "claims": [
        {"text": "OpenAI improved prompt caching for GPT-6.", "passage_ids": ["P1"], "source_domain": "openai.com"}]}
    report, _, _ = run(ans, web, sites={"openai.com": []})
    assert report.claims[0].status is Status.REPORTED and report.claims[0].primary is None


def test_own_site_on_topic_but_not_stating_claim_is_reported_not_primary():
    """Relevance is not support: a topical own-site hit must not earn PRIMARY."""
    web = [passage("https://news.example/x", "OpenAI cut API prices by 50% this quarter.")]
    sites = {"openai.com": [passage("https://openai.com/pricing", "Our pricing page lists current rates.")]}
    ans = {"summary": "s", "claims": [
        {"text": "OpenAI cut API prices by 50% this quarter.", "passage_ids": ["P1"], "source_domain": "openai.com"}]}

    class JudgeByDomain(Model):
        def with_structured_output(self, schema, **kwargs):
            class _R:
                def invoke(self, messages, **_):
                    body = messages[-1].content
                    out = []
                    for n, block in enumerate(body.split("[item ")[1:]):
                        is_own = "(openai.com)" in block
                        txt = block.split("): ", 1)[1].strip()
                        out.append({"id": n, "label": "not_addressed" if is_own else "supports",
                                    "span": "" if is_own else txt[:50]})
                    return schema.model_validate({"verdicts": out})
            return _R()

    tav = Tavily(web, sites)
    _, report, _ = run_research("q", JudgeByDomain(script=script(ans)), tav, today=TODAY)
    assert report.claims[0].status is Status.REPORTED and report.claims[0].primary is None


def test_false_premise_claim_citing_nonexistent_passage_is_dropped():
    """'Did OpenAI acquire Perplexity?' — the model invents a citation."""
    web = [passage("https://a.example/p", "Perplexity raised a new funding round.")]
    ans = {"summary": "s", "claims": [
        {"text": "OpenAI acquired Perplexity this year.", "passage_ids": ["P9"], "source_domain": "openai.com"}]}
    report, tav, _ = run(ans, web)
    assert report.claims == [] and len(report.dropped) == 1
    assert report.anchor_searches == 0  # no search spent on an uncited claim


def test_invented_figure_is_flagged_even_with_real_citation():
    web = [passage("https://a.example/r", "The latest funding round was led by two well-known venture investors.")]
    ans = {"summary": "s", "claims": [
        {"text": "The round raised $900 million.", "passage_ids": ["P1"], "source_domain": None}]}
    report, _, _ = run(ans, web)
    assert report.claims[0].figures_not_in_any_source == ["900"] and report.claims[0].flagged


def test_multi_org_question_anchors_each_org_separately():
    """'Compare what OpenAI and Anthropic shipped' — two anchors, mixed labels."""
    web = [passage("https://news.example/a", "OpenAI shipped the Agents API to developers this quarter. [...] Anthropic shipped Claude Code hooks for enterprise teams.")]
    sites = {"openai.com": [passage("https://openai.com/x", "OpenAI shipped the Agents API to developers this quarter, the company said.")], "anthropic.com": []}
    ans = {"summary": "s", "claims": [
        {"text": "OpenAI shipped the Agents API to developers this quarter.", "passage_ids": ["P1"], "source_domain": "openai.com"},
        {"text": "Anthropic shipped Claude Code hooks for enterprise teams.", "passage_ids": ["P2"], "source_domain": "anthropic.com"}]}
    report, tav, _ = run(ans, web, sites)
    by = {c.text.split()[0]: c.status for c in report.claims}
    assert by["OpenAI"] is Status.PRIMARY and by["Anthropic"] is Status.REPORTED
    assert {c["include_domains"][0] for c in tav.calls if c.get("include_domains")} == {"openai.com", "anthropic.com"}


def test_time_scoped_question_flags_sources_older_than_window():
    """'What changed this year?' answered only from last year's articles."""
    web = [passage("https://old.example/a", "The agent search market consolidated sharply over the past year.", published="2025-03-01")]
    ans = {"summary": "s", "claims": [
        {"text": "The agent search market consolidated sharply over the past year.", "passage_ids": ["P1"], "source_domain": None}]}
    report, _, _ = run(ans, web, start="2026-01-01")
    assert report.claims[0].stale and report.claims[0].flagged


def test_writer_exceeding_five_claims_is_capped():
    web = [passage("https://a.example/a", "Fact one. " * 10)]
    ans = {"summary": "s", "claims": [
        {"text": f"Claim {i}.", "passage_ids": ["P1"], "source_domain": None} for i in range(8)]}
    report, _, _ = run(ans, web)
    assert len(report.claims) + len(report.dropped) == 5


def test_judge_finding_nothing_marks_unsupported():
    web = [passage("https://a.example/a", "Unrelated text about regional weather patterns and seasonal rainfall.")]
    ans = {"summary": "s", "claims": [
        {"text": "Rates rose 2% in May.", "passage_ids": ["P1"], "source_domain": None}]}
    report, _, _ = run(ans, web, judge="not_addressed")
    assert report.claims[0].status is Status.UNSUPPORTED


def test_contradiction_hidden_by_default_shown_with_strict():
    """The live finding: a 'contradicts' verdict is not surfaced unless --strict."""
    web = [passage("https://a.example/a", "Price is $2 input and $10 output, plus $0.10 cached.")]
    ans = {"summary": "s", "claims": [
        {"text": "Price is $2 input and $10 output.", "passage_ids": ["P1"], "source_domain": None}]}
    default, _, _ = run(ans, web, judge="contradicts")
    strict, _, _ = run(ans, web, judge="contradicts", strict=True)
    assert default.claims[0].status is Status.UNSUPPORTED  # verdict dropped, nothing else supports
    assert strict.claims[0].status is Status.CONTESTED


def test_syndicated_reprints_count_once():
    """Ten sites repeating one rumor are one source."""
    text = "Nebius agreed to acquire Tavily for about $400 million."
    web = [passage(f"https://site{i}.example/x", text) for i in range(10)]
    ans = {"summary": "s", "claims": [
        {"text": text, "passage_ids": ["P1"], "source_domain": None}]}
    report, _, _ = run(ans, web)
    assert len(report.claims[0].evidence) == 1  # only P1 exists; reprints were deduped at ingest
