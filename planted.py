"""Planted-error test: does the support check catch claims we broke on purpose?

Takes claims the agent already produced (from runs/*.json), alters each one in a
way that makes it false about its own cited passage, and re-runs only the support
check. A caught error is one the checker labels contested or unsupported.

    uv run planted.py runs/eval-1760000000      # a folder of run records
    uv run planted.py runs/1760000000.json      # one record

No Tavily credits: it reuses the recorded passages.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Annotated, Callable

import typer
from rich.table import Table

from grounding import (
    CheckedClaim,
    Passage,
    Status,
    apply_verdicts,
    judge_items,
    parse_date,
)
from research_agent import DEFAULT_MODEL, build_judge, console, make_clients

app = typer.Typer(add_completion=False)

ORGS = {"openai": "Anthropic", "anthropic": "OpenAI", "google": "Microsoft", "nebius": "CoreWeave",
        "tavily": "Perplexity", "microsoft": "Google", "snowflake": "Databricks", "databricks": "Snowflake"}


@dataclass(frozen=True)
class Mutation:
    kind: str
    text: str


def plant(claim: str) -> list[Mutation]:
    """Each mutation keeps the sentence plausible but wrong about its passage."""
    out: list[Mutation] = []

    if m := re.search(r"\d[\d,]*(?:\.\d+)?", claim):
        original = m.group()
        digits = original.replace(",", "")
        try:
            scaled = float(digits) * 3
            replacement = str(int(scaled)) if scaled.is_integer() else f"{scaled:.1f}"
        except ValueError:
            replacement = "999"
        out.append(Mutation("number", claim[: m.start()] + replacement + claim[m.end() :]))

    for name, swap in ORGS.items():
        if re.search(rf"\b{name}\b", claim, re.I):
            out.append(Mutation("actor", re.sub(rf"\b{name}\b", swap, claim, count=1, flags=re.I)))
            break

    if re.search(r"\b20\d\d\b", claim):
        out.append(Mutation("year", re.sub(r"\b20(\d\d)\b", lambda m: f"20{int(m.group(1)) - 4:02d}", claim, count=1)))

    negated = re.sub(r"\b(has|have|is|are|was|were|will|did)\b", r"\1 not", claim, count=1)
    if negated != claim:
        out.append(Mutation("negation", negated))

    return out


def load_claims(path: Path) -> list[dict]:
    files = sorted(path.glob("*.json")) if path.is_dir() else [path]
    claims: list[dict] = []
    for f in files:
        record = json.loads(f.read_text())
        for claim in record.get("claims", []):
            if claim.get("evidence"):
                claims.append(claim)
    return claims


def rebuild(claim: dict, text: str, passages: dict[str, Passage]) -> CheckedClaim | None:
    evidence = [passages[e["id"]] for e in claim["evidence"] if e["id"] in passages]
    if not evidence:
        return None
    primary = next((p for p in evidence if p.url == claim.get("primary_url")), None)
    return CheckedClaim(text=text, status=Status.SYNTHESIS, evidence=evidence, primary=primary)


def passages_from(record_dir: Path) -> dict[str, Passage]:
    """Rebuild passages from the recorded evidence (id, url, published, span text)."""
    passages: dict[str, Passage] = {}
    files = sorted(record_dir.glob("*.json")) if record_dir.is_dir() else [record_dir]
    for f in files:
        for claim in json.loads(f.read_text()).get("claims", []):
            spans = {v["passage_id"]: v.get("span", "") for v in claim.get("verdicts", [])}
            for e in claim.get("evidence", []):
                text = spans.get(e["id"]) or e.get("text", "")
                if e["id"] not in passages and text:
                    passages[e["id"]] = Passage(
                        id=e["id"], url=e["url"], domain=e["url"].split("/")[2] if "//" in e["url"] else "",
                        title="", text=text, published=parse_date(e.get("published")),
                    )
    return passages


@app.command()
def main(
    records: Annotated[Path, typer.Argument(help="runs/ record file or folder")],
    model: Annotated[str, typer.Option()] = DEFAULT_MODEL,
) -> None:
    claims = load_claims(records)
    passages = passages_from(records)
    if not claims or not passages:
        console.print("[red]No claims with recorded passage text found. Run the agent first.[/red]")
        raise typer.Exit(code=1)

    chat_model, _ = make_clients(model)
    judge: Callable = build_judge(chat_model, {})

    rows: list[tuple[str, str, str, bool]] = []
    for claim in claims:
        for mutation in plant(claim["text"]):
            rebuilt = rebuild(claim, mutation.text, passages)
            if rebuilt is None:
                continue
            batch = [rebuilt]
            try:
                apply_verdicts(batch, judge(judge_items(batch)))
            except Exception as exc:
                console.print(f"[yellow]judge failed: {exc}[/yellow]")
                continue
            caught = batch[0].status in (Status.CONTESTED, Status.UNSUPPORTED)
            rows.append((mutation.kind, mutation.text, batch[0].status.value, caught))

    table = Table("planted error", "claim as altered", "label", "caught")
    by_kind: dict[str, list[bool]] = {}
    for kind, text, label, caught in rows:
        by_kind.setdefault(kind, []).append(caught)
        table.add_row(kind, text[:60], label, "yes" if caught else "NO")
    console.print(table)

    caught_total = sum(c for *_, c in rows)
    console.print(f"\nCaught {caught_total}/{len(rows)} planted errors ({caught_total / max(len(rows), 1):.0%})")
    for kind, results in sorted(by_kind.items()):
        console.print(f"  {kind}: {sum(results)}/{len(results)}")
    console.print(f"[dim]date: {date.today()} · model: {model}[/dim]")


if __name__ == "__main__":
    app()
