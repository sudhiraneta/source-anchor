# Source Anchor

A Tavily research agent whose every claim is traceable: it cites a passage Tavily returned, a judge quotes the sentence that states it, and a label says whether the organization itself or only third parties said it.

```bash
cp .env.example .env     # add TAVILY_API_KEY and NEBIUS_API_KEY (lowercase also works)
uv run research_agent.py "What did OpenAI announce for its API this year?" --model Qwen/Qwen3-235B-A22B-Instruct-2507
uv run pytest            # 51 offline tests, no keys needed
uv run planted.py runs/  # catch rate on deliberately broken claims (no credits)
# add --strict to research_agent.py to also show CONTESTED (off by default; see Live results)
```

## Before and after

Same question, same model. The starter returns a paragraph with links at the end; you can't tell which link backs which sentence, or whether a sentence is backed at all.

**Starter** (`starter_agent.py`, excluded from this repo per the brief): a single prose paragraph that ends with a flat list of source URLs. There is no mapping from any sentence to any source, no indication of which claims the organization itself stated versus which a third party reported, and no flag when a sentence has no backing at all.

**Source Anchor**, same question — a real run of the shipped code in default mode (`Qwen/Qwen3-235B-A22B-Instruct-2507`, abridged):

```
PRIMARY 1. OpenAI announced the public beta of its Agents API on September 10, 2026 ...
  the organization's own site states this
  [P12] openai.com · 2026-10-07 · September 10, 2026 # Introducing the Agents API ...
  stated in P12: "September 10, 2026 # Introducing the Agents API Build and run cloud agents with the Codex harness, fully managed by OpenAI."

PRIMARY* 2. On September 22, 2026, OpenAI released GPT-6 Sol and Luna ... for Plus, Pro, Business, Enterprise, and Edu users ...
  own site states the event, not every figure
  [P19] community.openai.com · 2026-09-22 · ... GPT-6 Sol and Luna build on Astra's ...
  stated in P19: "GPT-6 Sol and Luna roll out today in ChatGPT Work and Codex for Plus, Pro, Business, Enterprise, and Edu users."
  figures not on the organization's site: 22

REPORTED 3. GPT-6.1 Sol was released on September 29, 2026, priced at $2 per million input tokens ...
  third-party sources state this; the own site does not
  [P5] tech-insider.org · 2026-10-10 · ... GPT-6.1 Sol shipped on September 29, 2026, priced at $2 ...

SYNTHESIS 5. The Responses API has become the preferred endpoint for developers ...
  market-level statement, stated by a cited passage
```

The difference: every sentence sits next to the passage it came from, and the label says whether OpenAI's own site confirmed it (claims 1–2), only a third party did (claim 3), or it's a market-level statement with no single owner (claim 5). The reader checks the flagged ones, not all twenty sources. Six labels exist; this one run exercised four of them with no `CONTESTED` (off by default — see below).

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
| `CONTESTED` | **Opt-in (`--strict`).** A cited passage appears to conflict. Off by default because the judge over-fires it (see Live results); when shown, both passages appear so a person decides. |
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

Runs on `Qwen/Qwen3-235B-A22B-Instruct-2507` (the starter's default Kimi-K2.6 returned empty replies through `create_agent`; `diag_model.py` reproduces it). Records in `runs/`.

**What is solid, and proven live:**

- **Traceability.** Across every run, no claim cited a source that wasn't returned, no claim carried a figure absent from its passages, and every shown span was verified present in its passage. Claims the model couldn't ground were dropped and listed.
- **The primary-source anchor works, in the shipped default.** A run of the final code on *"What did OpenAI announce for its API this year?"* returned 1 `PRIMARY`, 1 `PRIMARY*` (`community.openai.com` states the Sol/Luna rollout; one date digit wasn't in the quoted text), 2 `REPORTED`, 1 `SYNTHESIS`, and **0 `CONTESTED`** — four labels exercised, no wrong flag. Two claims that reached `PRIMARY` on an earlier run came back `REPORTED` here because Tavily returned different own-site pages: search variance, failing in the safe direction (never a false `PRIMARY`).
- **Changed-number detection.** `planted.py` broke 24 claims on purpose: **21/24 caught (88%)** — changed numbers **10/10**, negations 2/2, swapped actors 4/5, shifted years 5/7.

**Correctness, checked against the live source pages.** Every number above measures the *checker*. This measures whether the labels are *right*: I fetched each cited page directly (the live page, not Tavily's snippet) and checked the claim against it, for all 12 distinct claims across the 4 recorded runs.

| What was checked | Result |
|---|---|
| Quoted span actually on the cited page | **12/12.** No fabricated source, no misquote, in any run. |
| `PRIMARY` (Agents API, openai.com) | Fully stated by the page: public beta, Sept 10 2026, Codex harness, managed by OpenAI. |
| `PRIMARY*` (Sol/Luna, community.openai.com) | Rollout date, tiers, and lower cost are on the page. One sub-clause, "trained with similar methods as Astra," is only on the third-party source — the writer fused a third-party detail into a primary-labelled sentence. The quoted span is still on the page; the label is defensible; the sentence over-reaches by one clause. |
| `REPORTED` (×4) | Every one is stated by its third-party source. In two cases the organization's site *also* stated it but that run's search didn't surface the page: conservative, never a false `PRIMARY`. |
| `SYNTHESIS` (×6) | Every one is stated by its cited source. |
| `CONTESTED` (×3, strict mode only) | **All three confirmed wrong by the live pages.** The changelog *supports* the $2/$10 pricing; the Astra page carries the Sept 22 Sol/Luna update and contradicts nothing; the DevDay page states 300 tok/s for Astra in Codex. None of the three passages conflicted with its claim. This is the direct evidence behind gating `CONTESTED` off. |

Two things the check surfaced that the agent couldn't have: the Ultrafast "October 8" date exists only in a third-party article (the OpenAI page is dated Sept 29), and the market-size source gives 14.2% CAGR in its body and 14.10% in its own FAQ. Both are source-side facts the labels correctly route to the reader rather than resolve.
- **Market questions behave correctly.** *"What changed in the AI search market this year?"* returned five `SYNTHESIS` claims: a market-wide question has no originator, so the primary path correctly never fires.

**What is noisy, and what I did about it: the `CONTESTED` label over-fires.** On the OpenAI re-run, three of five claims were marked `CONTESTED` and at least two were wrong:

- Claim 2 (Sol/Luna released): the `openai.com` passage actually *states* it ("expanding our GPT-6 family with GPT-6 Sol and GPT-6 Luna"); the judge flagged an unrelated sentence about GPT-6 Astra as the conflict.
- Claim 3 (pricing): the claim's "$2 in / $10 out" is *contained in* a passage that merely lists more tiers ("$2 input, $0.10 cached, $2.50 cache write, $10 output"). Elaboration, not conflict.

A prompt instruction ("extra detail is not a contradiction") reduced but did not eliminate this. An LLM judge still calls compatible-but-different a contradiction, and code cannot yet confirm a *conflict* the way it confirms a *span*.

**So `CONTESTED` is off by default.** A "contradicts" verdict is dropped and the claim is labelled from its supporting evidence only; on that run the three claims fall to `PRIMARY`/`REPORTED` instead of showing a red flag the reader would have to disprove. `--strict` turns it back on. `planted.py` always runs strict, because it measures the judge's raw ability to spot a planted conflict, not the user-facing default; that is how the 88% is computed. The honest split: the judge is reliable at *absence* and at *changed magnitudes*, unreliable at *conflict*, and the default output only shows labels code can stand behind.

**The real fix, specified under Next:** make a contradiction code-checkable by requiring the judge to name a value the passage genuinely lacks. It would let `CONTESTED` back on by default. I prototyped it far enough to see it also downgrades a swapped-actor conflict whose numbers match, so it needs live iteration before it ships.

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

The full evidence chain, guarantee by guarantee, is in [`VERIFICATION.md`](VERIFICATION.md). In brief:

- **51 offline tests** (`uv run pytest`): core logic, the judge (span verification, contradiction over support, invented spans discarded, failure reported not hidden), the real `create_agent` loop with a scripted model and a fake Tavily client asserting the exact Tavily parameters, the planted-error harness, and **12 research-concise question shapes** (`tests/test_scenarios.py`: market trend, single-org announcement, own-site silent, topical-but-not-stating, false premise, invented figure, multi-org, stale window, >5 claims, judge finds nothing, contradiction default vs `--strict`, syndicated reprints) each checked against its designed label.
- **Tavily client read directly** to confirm parameter pass-through; **credit rates and the feedback endpoint** checked against the docs.
- **Live:** the two runs above, plus `diag_model.py` reproducing the Kimi failure that motivated the recovery path.

## Tracing

Set `LANGSMITH_TRACING=true` and `LANGSMITH_API_KEY` in `.env`; LangChain traces the agent loop with no code changes. The anchor search and the judge run outside that loop, so they carry LangSmith's `@traceable` and appear in the same trace. The decorator is a no-op when langsmith is absent, so it is never a hard dependency.

## Next

1. **Make `CONTESTED` code-checkable, then turn it on by default.** Have the judge name the claim value and the conflicting passage value; in code, require a value the passage genuinely lacks. Measure against the planted-actor case first, since it can also downgrade a real actor/date swap whose numbers match.
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
| `tests/` | 51 offline tests. |
| `TECHNICAL_STATEMENT.md`, `BUILD_LOG.md` | Approach and value; how it was built, decision by decision. |
| `VERIFICATION.md` | Each guarantee traced to the code that produces it, the test that proves it, and the live run that shows it. |
