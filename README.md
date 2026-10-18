# Source Anchor

A Tavily research agent whose every claim is traceable: it cites a passage Tavily returned, a judge quotes the sentence that states it, and the label says whether the organization itself or only third parties said it.

```bash
cp .env.example .env        # add TAVILY_API_KEY and NEBIUS_API_KEY
uv run research_agent.py "What changed in the AI search market this year?" --model Qwen/Qwen3-235B-A22B-Instruct-2507
uv run pytest               # 38 offline tests, no keys needed
uv run eval.py              # traceability eval over eval_questions.txt (uses credits)
uv run planted.py runs/     # catch rate on deliberately broken claims (no credits)
```

## The problem, in one paragraph

Tavily is paid when agents built on it reach production. The wall between a demo and production isn't search quality; it's that nobody can approve an agent whose answers can't be audited. The starter shows why: it doesn't know today's date, so "this year" means nothing; its sources are a URL list at the end, so no one can tell which link backs which sentence; and ten sites repeating one rumor look like ten sources. The reader re-checks everything, and an answer you must re-check saves no time. That is the adoption problem for a solo builder (an agent they can't ship) and for an enterprise (an agent that fails review).

## What the agent guarantees

Every label is about the passages, never about the world. The agent cannot tell you a fact is true. It can guarantee four things:

1. **No invented sources.** A claim may only cite passage IDs (`[P1]`, `[P2]`...) from results Tavily actually returned. A claim with no valid ID is dropped and listed as dropped.
2. **No misquoted sources.** A judge reads each (claim, passage) pair and must copy the exact sentence that states the claim. Code checks that sentence is really in the passage; a quote that isn't there is discarded, not trusted.
3. **Who said it.** For a claim about one organization, one Tavily search restricted to that organization's own site decides whether the originator said it or only the press did.
4. **No invented numbers or stale windows.** Figures in the claim are checked against the passages; today's date goes in the prompt, and a claim whose sources all predate the window is flagged.

## Labels

| Label | Meaning |
|---|---|
| `PRIMARY` | The organization's own page states the claim, figures included. |
| `PRIMARY*` | The own page states the event; at least one figure comes only from other sources. |
| `REPORTED` | Third parties state it; the organization's own site does not (or the search there found nothing). |
| `SYNTHESIS` | A market-level statement with no single owner, stated by a cited passage. |
| `CONTESTED` | A cited passage states something incompatible (a different number, date, or actor). Both are shown. |
| `UNSUPPORTED` | No cited passage states the claim. |

Example output. **This is the offline test fixture, not a live run**; the sources are invented to show the format:

```
PRIMARY* 1. Nebius agreed to acquire Tavily for about $400 million.
  own site states the event, not every figure
  [P4] nebius.com · 2026-02-10 · Nebius Group today announced an agreement to acquire Tavily.
  [P1] technews.example · 2026-02-11 · Nebius agreed to acquire Tavily for about $400 million, sources said.
  stated in P4: "Nebius Group today announced an agreement to acquire Tavily."
  figures not on the organization's site: 400

REPORTED 2. OpenAI expanded web search in its Responses API.
  third-party sources state this; the own site does not
  [P3] blog.example · 2026-03-02 · OpenAI expanded web search in its Responses API to more models.
  stated in P3: "OpenAI expanded web search in its Responses API to more models."

3 claims shown · 1 dropped (no valid passage) · 2 anchor searches · window from 2026-01-01
  dropped: Google cut grounding prices by 50%.
```

Claim 1 is the case this is built for: the deal is confirmed by the buyer, the price only by the press, and the output says exactly that.

## Design decisions, including the rejected ones

| Option | Verdict |
|---|---|
| Ask the writer to quote, then string-match | Rejected. Brittle, and it flags correct paraphrases. |
| Re-search each claim and trust the relevance score | Rejected. Relevance is not support; a wrong date on a real event still ranks high. |
| Rank sources by a curated list of trusted domains | Rejected. Subjective, never complete, and nobody adopts a pattern that needs curation. |
| Count how many domains agree | Rejected. Forty reprints of one press release look like forty sources. |
| Google's Check Grounding API | The gold standard, but locked to Vertex AI. Tavily's builders aren't there. |
| **Cite by ID, anchor to the originator, judge with a quoted span** | Built. Every check runs on text already in memory, except one restricted search and one batched judge call. |

The judge is the piece that turns "traceable" into "checked". Without it, a topical hit on the organization's site earned `PRIMARY` even if the page said something else. With it, `PRIMARY` means the page states the claim.

## What this does not do

- **It doesn't check truth or currency.** A press release can be wrong; a price can change an hour after the crawl. `PRIMARY` means the originator said it, nothing more.
- **It doesn't check completeness.** If the most important change this year is missing, nothing flags it.
- **The judge is a model.** The quoted span makes it checkable, not independent. `planted.py` measures how often it catches errors we plant on purpose.
- **"The organization's own site" is the wrong primary source for some questions.** Funding (investor or filing), regulation (the regulator), compliance (a trust portal that blocks crawlers). The claim-kind table below is the fix, not yet built.
- **A wrong domain guess becomes `REPORTED`.** Safe (never a false `PRIMARY`), but it hides a lookup failure under a source judgment.
- **`published_date` is Tavily's estimate**, so the stale flag is a hint.

## Who it serves, and what each gets

| Segment | What they get |
|---|---|
| Startups / B2B2C | A review layer they didn't have to build. The agent says which statements are confirmed and which are reported, so the product is shippable. |
| Enterprise (B2B) | An audit record per claim (passage, URL, date, label, quoted span) in `runs/*.json`. This is what passes a platform team's review. |
| Marketplaces (Snowflake, Databricks, AWS) | One answer to "how do we know this is right?", identical across platforms. |
| Tavily | A reusable recipe using features most builders never touch (`include_domains` + `restrict`, `chunks_per_source`, `start_date`), and a feedback signal (below). |

## The ecosystem loop: feedback to Tavily

Tavily's [Feedback endpoint](https://docs.tavily.com/documentation/api-reference/endpoint/feedback) accepts per-result scores, `used_ids`, `used_citations` and `response_delivered`, and its docs say this is "the signal we use to improve ranking and result quality". Most agents can't send it because they don't know which results they used. This one knows, per claim: which passage stated it, which contradicted it, and which was the originator's own page. That maps one-to-one onto the endpoint. Better labels for the user and better training signal for Tavily are the same data, computed once. Sending it is free and planned (see Next); text fields should be opt-in, since an enterprise may not want the question leaving the building.

## Cost

| Step | Tavily credits | Model calls |
|---|---|---|
| Research search, advanced depth | 2 per call | the agent loop |
| Anchor search, basic, only when the own site isn't already cited | 1 each, ≤5 | 0 |
| Judge, one batched call for all (claim, passage) pairs | 0 | 1 |
| Recovery, only if the writer skips the answer format | 0 | ≤1 |

Rates from Tavily's [credits page](https://docs.tavily.com/documentation/api-credits). Worst case: about 7 credits and 3 to 5 model calls per answer, roughly double the starter's latency. `--no-check-support` drops the judge.

## How it was verified

- **38 offline tests.** Core logic (IDs, reprint dedupe, look-alike domains, dates, figures, stale windows), the judge (span verification, contradiction beats support, invented spans discarded, judge failure keeps provenance labels and is reported, not hidden), the real `create_agent` loop with a scripted model and a fake Tavily client (checks the exact parameters sent to Tavily), and the planted-error harness.
- **Tavily client read directly** to confirm `start_date`, `include_domains`, `include_domains_mode` are named parameters and other keys pass through.
- **Credit rates and the feedback endpoint** checked against Tavily's docs.
- **Live finding:** Kimi-K2.6 (the starter's default) returned empty replies through `create_agent` on Nebius. `Qwen/Qwen3-235B-A22B-Instruct-2507` answered and tool-called correctly in `diag_model.py`. The recovery path exists because of this.

**Live results.** Run on `Qwen/Qwen3-235B-A22B-Instruct-2507` (the starter's default Kimi-K2.6 returned empty replies through `create_agent`; see the recovery path).

On the assignment question, *"What changed in the AI search market this year?"* (16.9s, one search, 5 claims, 0 dropped, judge ran on all 5, `judge_error: null`):

- Every claim came back **`SYNTHESIS`**: the model attached no single organization, because a market-wide question has no originator to anchor to. This is the documented case, not a failure, and it means the assignment's own question does not exercise the `PRIMARY`/`REPORTED` path.
- **The judge verified every claim against a real span.** For example, "ChatGPT processes 2.5 billion queries per day" is backed by the exact sentence quoted from the source and confirmed present in the passage by code. No figure was invented; nothing was dropped or stale.

To exercise the primary-source anchoring, run an organization-specific question, which is why `eval_questions.txt` leads with one:

```
uv run research_agent.py "What did OpenAI announce for its API this year?" --model Qwen/Qwen3-235B-A22B-Instruct-2507
```

Then the catch-rate number, which does not need live search:

```
uv run planted.py runs/ --model Qwen/Qwen3-235B-A22B-Instruct-2507
```

`planted.py` result: _paste "Caught X/Y planted errors" here_ — the number-heavy synthesis claims above make good planted-error inputs, since a mutated figure no longer appears in its passage.

## Eval

- `eval.py`: traceability metrics per question (claims shown, dropped, each label, flagged figures, stale, anchor searches). The baseline has no claim structure, so the comparison is presence vs absence.
- `planted.py`: takes claims from a real run, breaks each one on purpose (number ×3, actor swapped, year shifted, negated), and re-judges against the recorded passages. The **catch rate** is a measured accuracy number, with no hand labels and no credits. Its blind spot: it only plants the error types I thought of. A hand-labeled sample of 30 (claim, passage) pairs is the honest complement.

## Tracing

Set `LANGSMITH_TRACING=true` and `LANGSMITH_API_KEY` in `.env`; LangChain traces every model and `web_search` call with no code changes. Anchor searches and the judge run outside the agent loop and are recorded in `runs/*.json`; wrapping them with `@traceable` would put them in the same trace.

## Next

1. **Claim kinds.** One field per claim (`announcement`, `funding`, `regulation`, `compliance`, `live_state`, `market`) and a policy table that picks the right primary source per kind, shows "as of" dates for live state, and requires two independent sources for market claims. Same four checks, right anchor per question.
2. **Feedback to Tavily** after each run: `used_ids`, per-result support verdicts, primary flag. Text fields opt-in.
3. **Show the searched domain** on every `REPORTED`, so the reader can tell "they didn't say it" from "we looked in the wrong place".
4. **False-premise pre-check**: if no passage establishes the premise, return zero claims and say so.
5. Plain-language label rendering for end users; concurrent anchor searches.

## Files

| File | Purpose |
|---|---|
| `grounding.py` | Framework-free core: passage registry, anchor and figure checks, judge application, labels. No LangChain import. |
| `research_agent.py` | The starter's stack (LangChain + Nebius + Tavily) with the date-aware search tool, structured claims, anchor pass, judge and recovery. |
| `planted.py` | Planted-error catch rate from recorded runs. |
| `eval.py`, `eval_questions.txt` | Traceability eval over a question set. |
| `diag_model.py` | Two-call check of how a Nebius model behaves. |
| `tests/` | 38 offline tests. |
| `TECHNICAL_STATEMENT.md` | One-page approach, reasoning and value. |
| `BUILD_LOG.md` | How this was built with an AI coding partner, decision by decision. |
