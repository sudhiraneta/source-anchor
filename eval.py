"""Run the agent over a question set and summarize how well claims are anchored.

    uv run eval.py                      # eval_questions.txt
    uv run eval.py my_questions.txt

Writes one JSON record per question to runs/ and prints a table. These are
traceability metrics, not accuracy: they say how many claims are backed by a
passage and by the organization's own site. Accuracy needs the hand check
described in the README.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table

from research_agent import DEFAULT_MODEL, console, make_clients, run_research, to_record

app = typer.Typer(add_completion=False)
COLUMNS = ["claims_shown", "claims_dropped_no_valid_passage", "primary", "primary-partial",
           "reported", "synthesis", "flagged_unsupported_figure", "flagged_stale", "anchor_searches"]


@app.command()
def main(
    questions_file: Annotated[Path, typer.Argument()] = Path("eval_questions.txt"),
    model: Annotated[str, typer.Option()] = DEFAULT_MODEL,
) -> None:
    questions = [q.strip() for q in questions_file.read_text().splitlines() if q.strip()]
    chat_model, client = make_clients(model)
    out_dir = Path("runs") / f"eval-{int(time.time())}"
    out_dir.mkdir(parents=True)

    table = Table("question", *[c.replace("_", " ") for c in COLUMNS], "sec")
    totals = {c: 0 for c in COLUMNS}
    for i, question in enumerate(questions, 1):
        started = time.monotonic()
        try:
            answer, report, log = run_research(question, chat_model, client)
        except Exception as exc:
            console.print(f"[red]{question}: {exc}[/red]")
            continue
        record = to_record(question, model, answer, report, log, time.monotonic() - started)
        (out_dir / f"{i:02d}.json").write_text(json.dumps(record, indent=2))
        m = record["metrics"]
        for c in COLUMNS:
            totals[c] += m[c]
        table.add_row(question[:48], *[str(m[c]) for c in COLUMNS], str(record["seconds"]))

    table.add_row("TOTAL", *[str(totals[c]) for c in COLUMNS], "")
    console.print(table)
    shown = totals["claims_shown"] or 1
    console.print(
        f"Anchored to the organization's own site: {totals['primary'] + totals['primary-partial']}/{totals['claims_shown']} "
        f"({(totals['primary'] + totals['primary-partial']) / shown:.0%}). "
        f"Dropped for no valid passage: {totals['claims_dropped_no_valid_passage']}. Records in {out_dir}/"
    )


if __name__ == "__main__":
    app()
