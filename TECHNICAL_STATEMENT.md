# Technical Statement

## The problem, tied to Tavily's business

Tavily is paid when agents built on it run in production. The wall between a demo and production is not search quality; it is that a team cannot approve an agent whose answers nobody can audit. The starter shows why an answer can't be trusted: it doesn't know today's date, so "this year" is meaningless; its sources are a URL list at the end, so no one can tell which link backs which sentence, or whether a sentence is backed at all; and for "what changed" questions, ten sites repeating one rumor look like ten sources.

So the reader re-checks every claim by hand, and an answer you must re-check saves no time. That is the adoption problem: a solo builder can't ship an agent their users trust, and an enterprise can't put one past review. Trust is the gate to production, and production is what consumes credits.

## The approach

Every claim is traceable, and the agent guarantees four things. Each is checked in code against text already in memory, so the guarantees hold regardless of model quality.

1. **No invented sources.** A claim may only cite passage IDs (`[P1]`, `[P2]`...) from results Tavily actually returned. A claim with no valid ID is dropped and listed as dropped.
2. **No misquoted sources.** A judge reads each (claim, passage) pair and must copy the exact sentence that states the claim. Code checks that sentence is really in the passage; a quote that isn't there is discarded, not trusted. This is what turns "traceable" into "checked": a topical hit on the organization's site no longer earns a primary label unless the page actually states the claim.
3. **Who said it.** For a claim about one organization, one Tavily search restricted to that organization's own site (`include_domains`, `restrict`) decides whether the originator said it or only the press did.
4. **No invented numbers or stale windows.** Figures in the claim are checked against the passages; today's date goes in the prompt, the proposed `start_date` is validated in code, and a claim whose sources all predate the window is flagged.

Each claim gets one label: `PRIMARY`, `PRIMARY*` (event stated, a figure only elsewhere), `REPORTED`, `SYNTHESIS` (market-level, no single owner), `CONTESTED` (a cited passage appears to conflict — a flag to inspect, not a verdict; see the reliability note in Verification), or `UNSUPPORTED` (no cited passage states it). The answer stays short: one summary sentence and at most five claims.

## Thought process

I worked through and rejected several designs, each for a concrete reason:

- **String-match a model-produced quote.** Brittle; flags correct paraphrases.
- **Re-search each claim and trust the relevance score.** Relevance is not support; a wrong date on a real event still ranks high.
- **Rank sources by a curated list of trusted domains.** Subjective, never complete, and builders won't adopt a pattern that needs curation.
- **Count how many domains agree.** Forty reprints of one press release look like forty sources.
- **Google's Check Grounding API.** The gold standard, but locked to Vertex AI, where Tavily's builders aren't.

The key move was replacing "how many sites repeated it" with "did the source itself say it", verified by a quoted span. `PRIMARY` means the organization said it, not that it is true: a boundary the write-up and the output both state.

## The value

- **Technical:** unsupported claims cannot reach the reader unnoticed, and every claim carries an audit record (passage, URL, date, label, quoted span) saved as JSON in `runs/`. The trust logic (`grounding.py`) imports nothing from LangChain, so the same functions work with the Vercel AI SDK, OpenAI Agents, or a hand-written loop.
- **For builders:** a solo developer gets a review layer without building one; an enterprise gets the audit trail review processes demand.
- **For Tavily:** a reusable recipe on features most builders never touch (`include_domains` + `restrict`, `chunks_per_source`, `start_date`), and an ecosystem loop: the per-claim labels map one-to-one onto Tavily's [Feedback endpoint](https://docs.tavily.com/documentation/api-reference/endpoint/feedback) (`used_ids`, `used_citations`, per-result scores), which its docs call the signal it uses to improve ranking. Better labels for the user and better training signal for Tavily are the same data.

## Verification

- **38 offline tests** (`uv run pytest`), including the real `create_agent` loop with a scripted model and a fake Tavily client, which asserts the exact parameters sent to Tavily (`start_date`, `chunks_per_source`, `include_domains` + `restrict`), the judge (span verification, contradiction over support, invented spans discarded, judge failure reported not hidden), and the planted-error harness.
- **Tavily client read directly** to confirm `start_date`, `include_domains`, `include_domains_mode` are named parameters and other keys pass through.
- **Live finding:** the starter's default model, Kimi-K2.6, returned empty replies through `create_agent` on Nebius (`diag_model.py` reproduces it). `Qwen/Qwen3-235B-A22B-Instruct-2507` answered and tool-called correctly, so it is the recommended model, and a recovery path handles a model that ends without calling the answer tool. On the assignment's market-level question, all five claims came back `SYNTHESIS` (no single originator to anchor to) with every span verified; an organization-specific question exercises the `PRIMARY`/`REPORTED` path. On an organization question ("What did OpenAI announce for its API this year?") the labels split `PRIMARY` / `REPORTED` / `CONTESTED`, with `openai.com` confirming the Agents API claim by its own headline. `planted.py` then broke 24 claims from both runs on purpose; the judge caught **21 of 24 (88%)**: 10/10 changed numbers, 2/2 negations, 4/5 swapped actors, 5/7 shifted years. The honest split: the judge is reliable at *absence* (unsupported/dropped) and at *changed magnitudes*, and unreliable at *conflict* — on the OpenAI re-run it marked three of five claims `CONTESTED`, at least two wrongly (a passage that states the claim, and a passage that merely lists extra pricing tiers). A prompt instruction reduced but did not remove this, so `CONTESTED` is presented as "inspect this", not a verdict. The code-checkable contradiction gate that would fix it, and the planted-actor trade-off that kept me from shipping it before the deadline, are in the README. I would rather ship a measured limitation than an untested heuristic.

## Limits

Every label is about the passages, never about the world. The agent does not check truth (a press release can be wrong) or currency (a price can change after the crawl); it does not check completeness (a missing change isn't flagged); the judge is itself a model, made checkable by the quoted span but not independent; "the organization's own site" is the wrong primary source for funding, regulation and compliance questions (the claim-kind table in the README is the planned fix); a wrong domain guess becomes `REPORTED`, never a false `PRIMARY`; and `published_date` is Tavily's estimate.

## Observability

`grounding.py` emits a structured `Report`; the agent writes a full JSON record per run to `runs/`. With `LANGSMITH_TRACING=true` and `LANGSMITH_API_KEY` set, LangChain traces the agent loop, and the anchor search and judge (which run outside that loop) are decorated with LangSmith's `@traceable`, so they appear in the same trace. The decorator is a no-op when langsmith is not installed, so it is never a hard dependency.
