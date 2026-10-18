"""Research agent whose claims are anchored to the passages Tavily returned,
and, where a claim is about an organization, to that organization's own site.

    uv run research_agent.py "What changed in the AI search market this year?"

Same stack as the starter (LangChain create_agent + Nebius model + Tavily).
The trust logic lives in grounding.py and has no LangChain dependency.
"""

from __future__ import annotations

import json
import os
import time
from datetime import date
from pathlib import Path
from typing import Annotated, Any, Callable, Literal

import typer
from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from pydantic import BaseModel, Field
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from grounding import (
    DraftClaim,
    JudgeItem,
    Passage,
    PassageRegistry,
    Report,
    Status,
    Verdict,
    check_claims,
    render_for_model,
)

load_dotenv()
console = Console()
app = typer.Typer(add_completion=False)

DEFAULT_MODEL = "moonshotai/Kimi-K2.6"
MAX_CLAIMS = 5

SYSTEM_PROMPT = """Today is {today}. You are a research assistant. Every claim you make must be traceable.

1. Use web_search. If the question is time-bound ("this year", "latest", "recently"), pass start_date
   (YYYY-MM-DD) for the start of that period, worked out from today's date.
2. Search results are passages labelled [P1], [P2], ... State only facts found in passages you received.
3. Return at most {max_claims} claims, the most important first. Each claim is one plain sentence with:
   - passage_ids: the IDs of the passages that state it.
   - source_domain: if the claim is about one organization's own action (a launch, a deal, a price),
     the official website of that organization, e.g. "openai.com". Otherwise null.
4. Keep exact figures as the passage gives them. Do not put URLs in claims. Never cite an ID you did not receive.
5. Finish by calling the ResearchAnswer tool. Never end with a plain-text reply.
"""

RECOVERY_PROMPT = (
    "Return your final answer now in the ResearchAnswer format, using only the passage IDs "
    "from the search results above."
)


class Claim(BaseModel):
    text: str = Field(description="One factual sentence.")
    passage_ids: list[str] = Field(description="IDs like P3 of passages that state this claim.")
    source_domain: str | None = Field(
        default=None,
        description="Official website of the organization the claim is about, e.g. 'openai.com'; null for market-wide statements.",
    )


class ResearchAnswer(BaseModel):
    summary: str = Field(description="One-sentence direct answer to the question.")
    claims: list[Claim] = Field(description=f"At most {MAX_CLAIMS} supporting claims.")


# ------------------------------------------------------------------ Tavily


def parse_start_date(value: str | None, today: date) -> tuple[date | None, str | None]:
    """The model proposes a window; code decides whether it is usable."""
    if not value:
        return None, None
    try:
        start = date.fromisoformat(value.strip())
    except ValueError:
        return None, f"start_date '{value}' ignored: not YYYY-MM-DD"
    if start > today:
        return None, f"start_date {start} ignored: it is after today ({today})"
    return start, None


def build_search_tool(client: Any, registry: PassageRegistry, today: date, log: dict) -> Any:
    @tool
    def web_search(query: str, start_date: str | None = None) -> str:
        """Search the web with Tavily. start_date (YYYY-MM-DD) limits results to pages published since then."""
        start, warning = parse_start_date(start_date, today)
        params: dict[str, Any] = {
            "query": query,
            "search_depth": "advanced",  # needed for chunks_per_source
            "chunks_per_source": 3,
            "max_results": 5,
            "include_published_date": True,
        }
        if start:
            params["start_date"] = start.isoformat()
            log["window_starts"].append(start)
        log["searches"].append({"query": query, "start_date": params.get("start_date"), "kind": "research"})
        passages = registry.add_response(client.search(**params))
        body = render_for_model(passages)
        return f"{warning}\n\n{body}" if warning else body

    return web_search


def build_anchor_search(client: Any, registry: PassageRegistry, log: dict) -> Callable[[str, str], list[Passage]]:
    """One basic search per claim, restricted to the organization's own site."""

    def anchor(claim: str, site: str) -> list[Passage]:
        log["searches"].append({"query": claim, "site": site, "kind": "anchor"})
        response = client.search(
            query=claim,
            include_domains=[site],
            include_domains_mode="restrict",
            search_depth="basic",
            max_results=3,
            include_published_date=True,
        )
        return registry.add_response(response)

    return anchor


# ------------------------------------------------------------------- judge

JUDGE_PROMPT = """You check whether a passage states a claim. For each item, read ONLY that item's passage.
Use no outside knowledge, even if you believe the claim is true.

For each item return:
- label: "supports" if the passage states the claim; "contradicts" if the passage states something
  incompatible with it (a different number, date, or actor); "not_addressed" otherwise.
- span: the exact sentence copied from that passage that justifies your label. Copy it character for
  character. Leave it empty only for not_addressed.

Return one verdict per item, with the item's id.
"""


class JudgeVerdict(BaseModel):
    id: int = Field(description="The item's id.")
    label: Literal["supports", "contradicts", "not_addressed"]
    span: str = Field(default="", description="Exact sentence copied from the passage.")


class JudgeVerdicts(BaseModel):
    verdicts: list[JudgeVerdict]


def build_judge(model: Any, log: dict) -> Callable[[list[JudgeItem]], list[Verdict]]:
    """One batched call: every (claim, passage) pair judged in a single request."""

    def judge(items: list[JudgeItem]) -> list[Verdict]:
        if not items:
            return []
        body = "\n\n".join(
            f"[item {n}]\nCLAIM: {item.claim}\nPASSAGE ({item.passage.domain}): {item.passage.text}"
            for n, item in enumerate(items)
        )
        structured = model.with_structured_output(JudgeVerdicts, method="function_calling")
        result = structured.invoke([SystemMessage(JUDGE_PROMPT), HumanMessage(body)])
        log["judge_items"] = len(items)
        return [
            Verdict(
                claim_index=items[v.id].claim_index,
                passage_id=items[v.id].passage.id,
                label=v.label,
                span=v.span,
            )
            for v in result.verdicts
            if 0 <= v.id < len(items)
        ]

    return judge


# ------------------------------------------------------------------- agent


def _last_ai_text(messages: list) -> str:
    for m in reversed(messages):
        if getattr(m, "type", None) == "ai":
            content = m.content
            if isinstance(content, list):
                content = "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
            return str(content or "")
    return ""


def recover_answer(model: Any, messages: list) -> ResearchAnswer:
    """Some models end with plain text instead of calling the answer tool.
    Try, in order: JSON in that text, then one forced structured call over the
    same conversation. Search results are already in the registry, so no new
    searches are needed."""
    text = _last_ai_text(messages)
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            return ResearchAnswer.model_validate_json(text[start : end + 1])
        except ValueError:
            pass
    try:
        structured = model.with_structured_output(ResearchAnswer, method="function_calling")
        result = structured.invoke([*messages, HumanMessage(RECOVERY_PROMPT)])
        if isinstance(result, ResearchAnswer):
            return result
    except Exception as exc:  # report what the model said, not a bare failure
        raise RuntimeError(f"No structured answer ({exc}). Model's last reply: {text[:400]!r}") from exc
    raise RuntimeError(f"No structured answer. Model's last reply: {text[:400]!r}")


def run_research(
    question: str,
    model: Any,
    client: Any,
    today: date | None = None,
    on_search: Callable[[str, str | None], None] | None = None,
    check_support: bool = True,
) -> tuple[ResearchAnswer, Report, dict]:
    today = today or date.today()
    registry = PassageRegistry()
    log: dict = {"searches": [], "window_starts": []}

    agent = create_agent(
        model=model,
        tools=[build_search_tool(client, registry, today, log)],
        system_prompt=SYSTEM_PROMPT.format(today=today.isoformat(), max_claims=MAX_CLAIMS),
        response_format=ToolStrategy(ResearchAnswer),
    )

    state: dict = {}
    reported = 0
    for state in agent.stream({"messages": [{"role": "user", "content": question}]}, stream_mode="values"):
        if on_search:
            for s in log["searches"][reported:]:
                on_search(s["query"], s.get("start_date"))
            reported = len(log["searches"])

    answer = state.get("structured_response")
    if not isinstance(answer, ResearchAnswer):
        answer = recover_answer(model, state.get("messages", []))
        log["recovered"] = True

    drafts = [DraftClaim(c.text, c.passage_ids, c.source_domain) for c in answer.claims[:MAX_CLAIMS]]
    window = min(log["window_starts"], default=None)
    report = check_claims(
        drafts,
        registry,
        build_anchor_search(client, registry, log),
        window,
        judge=build_judge(model, log) if check_support else None,
    )
    return answer, report, log


# ----------------------------------------------------------------- display

BADGE = {
    Status.PRIMARY: ("PRIMARY", "bold green", "the organization's own site states this"),
    Status.PRIMARY_PARTIAL: ("PRIMARY*", "bold yellow", "own site states the event, not every figure"),
    Status.REPORTED: ("REPORTED", "bold magenta", "third-party sources state this; the own site does not"),
    Status.SYNTHESIS: ("SYNTHESIS", "bold blue", "market-level statement, stated by a cited passage"),
    Status.CONTESTED: ("CONTESTED", "bold red", "a cited passage states something incompatible"),
    Status.UNSUPPORTED: ("UNSUPPORTED", "bold red", "no cited passage states this"),
}


def _excerpt(text: str, limit: int = 180) -> str:
    return text if len(text) <= limit else text[:limit].rstrip() + "..."


def _source_line(p: Passage) -> str:
    when = p.published.isoformat() if p.published else "undated"
    return f"  [{p.id}] {p.domain} · {when} · {_excerpt(p.text)}"


def print_report(question: str, answer: ResearchAnswer, report: Report) -> None:
    console.print(Panel(Text(answer.summary), title=question, border_style="cyan"))
    for i, claim in enumerate(report.claims, 1):
        label, style, meaning = BADGE[claim.status]
        console.print(f"\n[{style}]{label}[/{style}] {i}. {claim.text}", highlight=False)
        console.print(f"  [dim]{meaning}[/dim]")
        if claim.primary and claim.primary not in claim.evidence:
            console.print(_source_line(claim.primary), highlight=False, markup=False, style="green")
        for p in claim.evidence:
            console.print(_source_line(p), highlight=False, markup=False, style="dim")
        for verdict in claim.contradicting:
            console.print(f"  [red]contradicted by {verdict.passage_id}:[/red] “{_excerpt(verdict.span)}”", highlight=False)
        for verdict in claim.supporting[:1]:
            console.print(f"  [green]stated in {verdict.passage_id}:[/green] “{_excerpt(verdict.span)}”", highlight=False)
        if claim.figures_not_in_primary:
            console.print(f"  [yellow]figures not on the organization's site: {', '.join(claim.figures_not_in_primary)}[/yellow]")
        if claim.figures_not_in_any_source:
            console.print(f"  [red]figures in no cited passage: {', '.join(claim.figures_not_in_any_source)}[/red]")
        if claim.stale:
            console.print(f"  [red]every cited source predates {report.window_start}[/red]")

    s = report.summary()
    footer = (
        f"{s['claims_shown']} claims shown · {s['claims_dropped_no_valid_passage']} dropped (no valid passage) · "
        f"{s['anchor_searches']} anchor searches"
    )
    if report.window_start:
        footer += f" · window from {report.window_start}"
    console.print(f"\n[dim]{footer}[/dim]")
    if report.judge_error:
        console.print(f"[yellow]support check did not run ({report.judge_error}); labels show provenance only[/yellow]")
    for d in report.dropped:
        console.print(f"[dim]  dropped: {d.text}[/dim]", highlight=False)


def to_record(question: str, model_name: str, answer: ResearchAnswer, report: Report, log: dict, seconds: float) -> dict:
    return {
        "question": question,
        "date": date.today().isoformat(),
        "model": model_name,
        "seconds": round(seconds, 1),
        "summary": answer.summary,
        "metrics": report.summary(),
        "judge_error": report.judge_error,
        "window_start": report.window_start.isoformat() if report.window_start else None,
        "searches": log["searches"],
        "claims": [
            {
                "text": c.text,
                "status": c.status.value,
                # text is kept so planted.py can re-judge without new searches
                "evidence": [
                    {"id": p.id, "url": p.url, "published": str(p.published), "text": p.text}
                    for p in ([c.primary] if c.primary else []) + c.evidence
                ],
                "primary_url": c.primary.url if c.primary else None,
                "figures_not_in_primary": c.figures_not_in_primary,
                "figures_not_in_any_source": c.figures_not_in_any_source,
                "stale": c.stale,
                "judged": c.judged,
                "verdicts": [
                    {"passage_id": v.passage_id, "label": v.label, "span": v.span} for v in c.support
                ],
            }
            for c in report.claims
        ],
        "dropped": [d.text for d in report.dropped],
    }


def make_clients(model_name: str) -> tuple[Any, Any]:
    from langchain_nebius import ChatNebius
    from tavily import TavilyClient

    for key, where in (("TAVILY_API_KEY", "https://app.tavily.com"), ("NEBIUS_API_KEY", "https://tokenfactory.nebius.com")):
        if not os.getenv(key) and os.getenv(key.lower()):  # accept tavily_api_key / nebius_api_key too
            os.environ[key] = os.environ[key.lower()]
        value = (os.getenv(key) or "").strip()
        if not value or value.endswith("..."):  # empty, or still the .env.example placeholder
            console.print(f"[bold red]Missing {key}[/bold red] (create one at {where}, then add it to .env)")
            raise typer.Exit(code=1)
    return ChatNebius(model=model_name), TavilyClient()


@app.command()
def main(
    question: Annotated[list[str], typer.Argument(help="Question")],
    model: Annotated[str, typer.Option(help="Nebius model name")] = DEFAULT_MODEL,
    save: Annotated[bool, typer.Option(help="Write a JSON record to runs/")] = True,
    check_support: Annotated[bool, typer.Option(help="Judge whether each cited passage states the claim")] = True,
) -> None:
    """Answer a question with claims anchored to Tavily passages and primary sources."""
    question_text = " ".join(question)
    chat_model, client = make_clients(model)

    started = time.monotonic()
    try:
        answer, report, log = run_research(
            question_text,
            chat_model,
            client,
            on_search=lambda q, since: console.print(
                f"[yellow]search[/yellow] {q}" + (f" [dim](since {since})[/dim]" if since else ""), highlight=False
            ),
            check_support=check_support,
        )
    except Exception as exc:  # surface provider errors plainly
        console.print(f"[bold red]Run failed:[/bold red] {exc}")
        raise typer.Exit(code=1) from None

    print_report(question_text, answer, report)
    if save:
        Path("runs").mkdir(exist_ok=True)
        path = Path("runs") / f"{int(time.time())}.json"
        record = to_record(question_text, model, answer, report, log, time.monotonic() - started)
        path.write_text(json.dumps(record, indent=2))
        console.print(f"[dim]saved {path}[/dim]")


if __name__ == "__main__":
    app()
