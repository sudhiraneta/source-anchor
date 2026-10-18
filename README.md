# Source Anchor

A Tavily research agent whose every claim is traceable: it cites a passage Tavily returned, a judge quotes the sentence that states it, and a label says whether the organization itself or only third parties said it.

```bash
cp .env.example .env     # add TAVILY_API_KEY and NEBIUS_API_KEY (lowercase also works)
uv run research_agent.py "What did OpenAI announce for its API this year?" --model Qwen/Qwen3-235B-A22B-Instruct-2507
uv run pytest            # 38 offline tests, no keys needed
uv run planted.py runs/  # catch rate on deliberately broken claims (no credits)
```

## Before and after

Same question, same model. The starter returns a paragraph with links at the end; you can't tell which link backs which sentence, or whether a sentence is backed at all.

**Starter** (`starter_agent.py`, *"What did OpenAI announce for its API this year?"*):

```
<paste the starter's real output here>
```

**Source Anchor**, same question (real run, `Qwen/Qwen3-235B-A22B-Instruct-2507`, abridged):

```
PRIMARY 1. OpenAI announced the public beta of its Agents API on September 10, 2026 ...
  the organization's own site states this
  [P15] openai.com · 2026-10-07 · ... # Introducing the Agents API. Build and run cloud agents ...
  stated in P15: "September 10, 2026 # Introducing the Agents API. Build and run cloud agents with the Codex harness, fully managed by OpenAI."

REPORTED 2. OpenAI introduced GPT-6 Sol and Luna on September 22, 2026 ...
  third-party sources state this; the own site does not
  [P3] powerdrill.ai · 2026-09-24 · OpenAI published the announcement on Tuesday, September 22, 2026 ...

5 claims shown · 0 dropped · 5 anchor searches · window from 2026-01-01
```

The difference: every sentence sits next to the passage it came from, and the label says whether OpenAI's own site confirmed it (claim 1) or only a third party did (claim 2). The reader checks the flagged ones, not all twenty sources.

## The problem, and why it matters to Tavily

Tavily is paid when agents built on it reach production. The wall between a demo and production is not search quality; it is that a team can't approve an agent whose answers nobody can audit. The starter shows why: no date in the prompt (so "this year" is meaningless), sources as a trailing URL list, and ten reprints of one rumor looking like ten sources. The reader re-checks everything, which means the agent saved no time, which means it doesn't ship. Trust is the gate to production, and production is what consumes credits.

## What the agent guarantees

Every label is about the passages, never about the world. The agent cannot tell you a fact is true. It guarantees four things, each checked in code:

1. **No invented sources.** A claim may only cite passage IDs (`[P1]`...) from results Tavily returned. A claim with no valid ID is dropped and listed.
2. **No misquoted sources.** A judge reads each (claim, passage) pair and must copy the exact sentence that states the claim; code checks that sentence is really in the passage, and discards a quote that isn't.
3. **Who said it.** For a claim about one organization, one Tavily search restricted to that organization's own site decides whether the originator said it or only the press did.
4. **No invented numbers or stale windows.** Figures are checked against the passages; today's date goes in the prompt, and a claim whose sources all predate the asked-for window is flagged.

| Label | Meaning |
|---|---|
| `PRIMARY` | The organization's own page states the claim, figures included. |
| `PRIMARY*` | The own page states the event; a figure comes only from elsewhere. |
| `REPORTED` | Third parties state it; the own site does not. |
| `SYNTHESIS` | A market-level statement with no single owner, stated by a cited passage. |
| `CONTESTED` | A cited passage states something incompatible. Both are shown. |
| `UNSUPPORTED` | No cited passage states the claim. |

## Design decisions, including the rejected ones

| Option | Verdict |
|---|---|
| String-match a model-produced quote | Rejected. Brittle; flags correct paraphrases. |
| Re-search each claim, trust the relevance score | Rejected. Relevance is not support; a wrong date on a real event still ranks high. |
| Rank sources by a curated trusted-domain list | Rejected. Subjective, never complete, needs hand curation. |
| Count how many domains agree | Rejected. Forty reprints of one press release look like forty sources. |
| Google's Check Grounding API | Gold standard, but locked to Vertex AI, where Tavily's builders aren't. |
| **Cite by ID, anchor to the originator, judge with a quoted span** | Built. Every check runs on text in memory, except one restricted search and one batched judge call. |

The judge is what turns "traceable" into "checked": without it, a topical hit on the org's site earned `PRIMARY` even if the page said something else.

## Live results

Both runs on `Qwen/Qwen3-235B-A22B-Instruct-2507` (the starter's default Kimi-K2.6 returned empty replies through `create_agent`; `diag_model.py` reproduces it). Records in `runs/`.

- **Market question** (*"What changed in the AI search market this year?"*): 5 claims, all `SYNTHESIS`. A market-wide question has no originator, so the primary path correctly never fires. Every claim's span was verified; no figure invented.
- **Organization question** (*"What did OpenAI announce..."*): 1 `PRIMARY` (openai.com, confirmed by its own headline), 3 `REPORTED`, 1 `CONTESTED`. This exercises the full label set.
- **Catch rate** (`planted.py`, 24 claims broken on purpose across both runs): **21/24 (88%)** — changed numbers 10/10, negations 2/2, swapped actors 4/5, shifted years 5/7.

**Findings from reading the live output (the point of the labels):**

1. **A false `CONTESTED`.** Claim 4 said "$2 input, $10 output"; the openai.com passage said "$2 input, $0.10 cached, $2.50 cache write, $10 output" — support with extra detail, not a contradiction. The judge got it wrong. Fixed: the judge prompt now states that extra detail is never a contradiction, only a directly incompatible value for the same quantity. Re-run the organization question to confirm claim 4 becomes `PRIMARY`.
2. **Numbers beat years and names.** The judge catches changed magnitudes (10/10) better than a year buried in a citation or a swapped actor (5/7, 4/5). Numbers also get a code-level check; years and actors don't yet. That code check is the next fix.

## What it does not do

- **Not truth or currency.** A press release can be wrong; a price can change after the crawl. `PRIMARY` means the originator said it, nothing more.
- **Not completeness.** A missing change isn't flagged.
- **The judge is a model.** The quoted span makes it checkable, not independent; `planted.py` is how its accuracy is measured.
- **The own site is the wrong primary for some questions** (funding → filing, regulation → regulator). The claim-kind table under Next is the fix.
- **A wrong domain guess becomes `REPORTED`** — safe (never a false `PRIMARY`), but it hides a lookup miss under a source judgment.
- **`published_date` is Tavily's estimate**, so the stale flag is a hint.

## Who it serves, and the ecosystem loop

| Segment | What they get |
|---|---|
| Startups / B2B2C | A review layer they didn't build; the product can say which statements are confirmed, so it ships. |
| Enterprise | A per-claim audit record (passage, URL, date, label, span) in `runs/*.json` — what passes a platform team's review. |
| Marketplaces | One answer to "how do we know this is right?", identical across platforms. |

The per-claim labels map one-to-one onto Tavily's [Feedback endpoint](https://docs.tavily.com/documentation/api-reference/endpoint/feedback) (`used_ids`, `used_citations`, per-result scores), which its docs call the signal Tavily uses to improve ranking. Most agents can't send it because they don't track which results they used; this one does. User trust and Tavily's ranking signal are the same data, computed once. (Planned — see Next.)

## Cost

| Step | Tavily credits | Model calls |
|---|---|---|
| Research search (advanced) | 2 per call | agent loop |
| Anchor search (basic), only when the own site isn't already cited | 1 each, ≤5 | 0 |
| Judge, one batched call for all pairs | 0 | 1 |
| Recovery, only if the writer skips the format | 0 | ≤1 |

Rates from Tavily's [credits page](https://docs.tavily.com/documentation/api-credits). Worst case ≈7 credits and 3–5 model calls per answer. `--no-check-support` drops the judge.

## Verification

- **38 offline tests** (`uv run pytest`): core logic, the judge (span verification, contradiction over support, invented spans discarded, failure reported not hidden), the real `create_agent` loop with a scripted model and a fake Tavily client asserting the exact Tavily parameters, and the planted-error harness.
- **Tavily client read directly** to confirm parameter pass-through; **credit rates and the feedback endpoint** checked against the docs.
- **Live:** the two runs above, plus `diag_model.py` reproducing the Kimi failure that motivated the recovery path.

## Tracing

Set `LANGSMITH_TRACING=true` and `LANGSMITH_API_KEY` in `.env`; LangChain traces the agent loop with no code changes. The anchor search and the judge run outside that loop, so they carry LangSmith's `@traceable` and appear in the same trace. The decorator is a no-op when langsmith is absent, so it is never a hard dependency.

## Next

1. **The two fixes the live run surfaced:** the judge prompt change (done — re-run to confirm) and a code-level check for the named organization and stated year, the way figures are already checked.
2. **Claim kinds:** one field per claim (`announcement`, `funding`, `regulation`, `compliance`, `live_state`, `market`) and a policy table that picks the right primary source per kind.
3. **Feedback to Tavily** after each run (`used_ids`, per-result verdicts); text fields opt-in.
4. **Show the searched domain** on `REPORTED`, so "they didn't say it" is distinct from "we looked in the wrong place".
5. Concurrent anchor searches; plain-language labels for end users.

## Files

| File | Purpose |
|---|---|
| `grounding.py` | Framework-free core: passage registry, anchor/figure checks, judge application, labels. No LangChain import. |
| `research_agent.py` | The starter's stack (LangChain + Nebius + Tavily) plus the date-aware search tool, structured claims, anchor pass, judge, recovery. |
| `planted.py` | Planted-error catch rate from recorded runs. |
| `eval.py`, `eval_questions.txt` | Traceability eval over a question set. |
| `diag_model.py` | Two-call check of how a Nebius model behaves. |
| `tests/` | 38 offline tests. |
| `TECHNICAL_STATEMENT.md`, `BUILD_LOG.md` | Approach and value; how it was built, decision by decision. |
