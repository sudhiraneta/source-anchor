"""End-to-end run of the real LangChain agent loop with no network:
a scripted chat model stands in for Nebius and a recording fake stands in
for TavilyClient. This proves the wiring, not model quality."""

from datetime import date
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from grounding import Status
from research_agent import parse_start_date, run_research

TODAY = date(2026, 10, 16)


class ScriptedModel(BaseChatModel):
    script: list[AIMessage]

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "ScriptedModel":
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=self.script.pop(0))])


class FakeTavily:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def search(self, **params) -> dict:
        self.calls.append(params)
        if params.get("include_domains") == ["nebius.com"]:
            return {"results": [{
                "url": "https://nebius.com/newsroom/tavily",
                "published_date": "2026-02-10",
                "content": "Nebius Group today announced an agreement to acquire Tavily.",
            }]}
        if params.get("include_domains"):
            return {"results": []}  # e.g. openai.com has nothing on this claim
        return {"results": [
            {
                "url": "https://technews.example/deal",
                "published_date": "2026-02-11",
                "content": "Nebius agreed to acquire Tavily for about $400 million, sources said. "
                "[...] Analysts expect more consolidation among agent search providers this year.",
            },
            {
                "url": "https://blog.example/openai",
                "published_date": "2026-03-02",
                "content": "OpenAI expanded web search in its Responses API to more models.",
            },
        ]}


def script(start_date: str) -> list[AIMessage]:
    answer = {
        "summary": "Consolidation and model vendors bundling search defined the year.",
        "claims": [
            {"text": "Nebius agreed to acquire Tavily for about $400 million.", "passage_ids": ["P1"], "source_domain": "nebius.com"},
            {"text": "OpenAI expanded web search in its Responses API.", "passage_ids": ["P3"], "source_domain": "openai.com"},
            {"text": "Analysts expect more consolidation this year.", "passage_ids": ["P2"], "source_domain": None},
            {"text": "Google cut grounding prices by 50%.", "passage_ids": ["P42"], "source_domain": "google.com"},
        ],
    }
    return [
        AIMessage(content="", tool_calls=[{"name": "web_search", "args": {"query": "AI search market 2026", "start_date": start_date}, "id": "c1"}]),
        AIMessage(content="", tool_calls=[{"name": "ResearchAnswer", "args": answer, "id": "c2"}]),
    ]


def test_full_run_anchors_claims_and_drops_uncited_one():
    tavily = FakeTavily()
    answer, report, log = run_research(
        "What changed in the AI search market this year?",
        ScriptedModel(script=script("2026-01-01")),
        tavily,
        today=TODAY,
    )

    research_call = tavily.calls[0]
    assert research_call["start_date"] == "2026-01-01"
    assert research_call["chunks_per_source"] == 3 and research_call["search_depth"] == "advanced"

    by_text = {c.text.split()[0]: c for c in report.claims}
    assert by_text["Nebius"].status is Status.PRIMARY_PARTIAL  # deal confirmed, price only from press
    assert by_text["Nebius"].figures_not_in_primary == ["400"]
    assert by_text["OpenAI"].status is Status.REPORTED  # own site returned nothing
    assert by_text["Analysts"].status is Status.SYNTHESIS
    assert [d.text for d in report.dropped] == ["Google cut grounding prices by 50%."]

    anchor_calls = [c for c in tavily.calls if c.get("include_domains")]
    assert {c["include_domains"][0] for c in anchor_calls} == {"nebius.com", "openai.com"}
    assert all(c["include_domains_mode"] == "restrict" and c["search_depth"] == "basic" for c in anchor_calls)
    assert report.window_start == date(2026, 1, 1) and report.anchor_searches == 2


def test_future_start_date_is_ignored_not_forwarded():
    tavily = FakeTavily()
    _, report, _ = run_research("q", ScriptedModel(script=script("2027-01-01")), tavily, today=TODAY)
    assert "start_date" not in tavily.calls[0]
    assert report.window_start is None


def test_parse_start_date():
    assert parse_start_date("2026-01-01", TODAY) == (date(2026, 1, 1), None)
    assert parse_start_date(None, TODAY) == (None, None)
    assert parse_start_date("Jan 2026", TODAY)[0] is None


def _search_then(final: AIMessage, *extra: AIMessage) -> list[AIMessage]:
    return [
        AIMessage(content="", tool_calls=[{"name": "web_search", "args": {"query": "AI search 2026"}, "id": "s1"}]),
        final,
        *extra,
    ]


ANSWER_JSON = '{"summary": "Deals.", "claims": [{"text": "Nebius agreed to acquire Tavily.", "passage_ids": ["P1"], "source_domain": null}]}'


def test_recovers_when_model_ends_with_json_text():
    """Seen live with Kimi: the model answers in plain text instead of calling the answer tool."""
    model = ScriptedModel(script=_search_then(AIMessage(content=f"Here you go:\n```json\n{ANSWER_JSON}\n```")))
    answer, report, log = run_research("q", model, FakeTavily(), today=TODAY)
    assert log["recovered"] and answer.summary == "Deals."
    assert report.claims[0].evidence[0].id == "P1"


def test_recovers_with_forced_structured_call_when_text_is_prose():
    forced = AIMessage(content="", tool_calls=[{"name": "ResearchAnswer", "args": {
        "summary": "Deals.", "claims": [{"text": "Nebius agreed to acquire Tavily.", "passage_ids": ["P1"]}]}, "id": "f1"}])
    model = ScriptedModel(script=_search_then(AIMessage(content="The market consolidated this year."), forced))
    answer, report, _ = run_research("q", model, FakeTavily(), today=TODAY)
    assert answer.claims[0].passage_ids == ["P1"] and len(report.claims) == 1


def test_clear_error_when_recovery_fails():
    import pytest
    model = ScriptedModel(script=_search_then(AIMessage(content="I cannot help."), AIMessage(content="still no")))
    with pytest.raises(RuntimeError, match="I cannot help"):
        run_research("q", model, FakeTavily(), today=TODAY)


class JudgingModel(ScriptedModel):
    """Scripted agent run, plus a judge that quotes the passage it is given."""

    verdict_label: str = "supports"

    def with_structured_output(self, schema, **kwargs):
        label = self.verdict_label

        class _Runnable:
            def invoke(self, messages, **_):
                body = messages[-1].content
                verdicts = []
                for n, block in enumerate(body.split("[item ")[1:]):
                    passage = block.split("PASSAGE (", 1)[1].split("): ", 1)[1].strip()
                    span = passage.split(".")[0] + "." if label != "not_addressed" else ""
                    verdicts.append({"id": n, "label": label, "span": span})
                return schema.model_validate({"verdicts": verdicts})

        return _Runnable()


def test_judge_runs_and_labels_claims():
    model = JudgingModel(script=script("2026-01-01"))
    _, report, log = run_research("q", model, FakeTavily(), today=TODAY)
    assert log["judge_items"] >= 3
    assert all(c.judged for c in report.claims)
    nebius = next(c for c in report.claims if c.text.startswith("Nebius"))
    assert nebius.status in (Status.PRIMARY, Status.PRIMARY_PARTIAL)
    assert nebius.supporting and nebius.supporting[0].span


def test_judge_finding_nothing_marks_claims_unsupported():
    model = JudgingModel(script=script("2026-01-01"), verdict_label="not_addressed")
    _, report, _ = run_research("q", model, FakeTavily(), today=TODAY)
    assert {c.status for c in report.claims} == {Status.UNSUPPORTED}
    assert report.summary()["unsupported"] == 3


def test_check_support_false_skips_judge():
    model = JudgingModel(script=script("2026-01-01"))
    _, report, log = run_research("q", model, FakeTavily(), today=TODAY, check_support=False)
    assert "judge_items" not in log and not any(c.judged for c in report.claims)
