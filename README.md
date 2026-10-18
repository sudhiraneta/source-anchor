# Source Anchor

A Tavily research agent that shows how far to trust each claim: whether it cites a passage Tavily actually returned, and whether the organization the claim is about says so on its own website.

```bash
cp .env.example .env        # add TAVILY_API_KEY and NEBIUS_API_KEY
uv run research_agent.py "What changed in the AI search market this year?"
uv run pytest               # 17 offline tests, no keys needed
uv run eval.py              # 8-question traceability eval (uses credits)
```

## Premise

People use a research agent to avoid reading twenty pages themselves. The starter answers well enough, but it gives the reader no way to tell a sourced sentence from one the model filled in. It has three concrete gaps:

1. **No date.** The prompt doesn't tell the model today's date, so "this year" has no meaning and the search has no time window.
2. **Citations are a URL list.** "Include source URLs when available" puts links at the end. You can't tell which link backs which sentence, or whether a sentence is backed at all.
3. **Repetition looks like confirmation.** Ten sites repeating one rumor look like ten sources. For "what changed this year" questions, most results are news coverage, not the organization that made the change.

So the reader re-checks everything, and an answer you have to re-check saves no time. That is the adoption problem: a solo builder can't ship an agent their users trust, and an enterprise team can't put one in front of analysts without an audit trail.

## Goal

For every claim in the answer, the reader should see, without opening a browser:

- the exact passage it came from (or the claim isn't shown),
- whether the organization involved confirms it on its own site,
- which figures nobody confirms, and whether the sources fall in the asked-for period.

Constraint: keep the output short (one summary sentence, at most five claims) and use only what Tavily already provides.

## Decision

I considered and rejected three approaches before settling on this one:

| Option | Why not |
|---|---|
| Ask the model to quote, then string-match the quote | Brittle (whitespace, reformatted numbers), and it flags correct paraphrases. |
| Re-search each claim and trust a high relevance score | Relevance is not support. A wrong date on a real event still ranks high. |
| Rank sources with a list of "reputable" domains | Subjective, never complete, and nobody adopts a pattern that needs a list curated by hand. |

**What I built instead: cite by ID, then go to the source.**

1. Every passage Tavily returns gets an ID (`[P1]`, `[P2]`, ...) across all searches. Verbatim reprints get stored once.
2. The model may only cite IDs. A claim citing no valid ID is dropped and listed as dropped. Nothing needs verifying after the fact, because nothing else can be cited.
3. For a claim about one organization, the model names that organization's site (`nebius.com`). If no cited passage is from that site, code runs **one** Tavily search restricted to it (`include_domains`, `restrict`, basic depth).
4. Code compares the figures in the claim with the primary passage. Figures that only appear in press coverage are shown as such. Figures in no passage at all are flagged.
5. Today's date goes into the prompt. The model proposes `start_date`; code rejects malformed or future dates and passes valid ones to Tavily.

This replaces "who repeated it" with "did the source say it". Forty reprints of a press release don't outweigh the press release, so echo detection and domain lists become unnecessary.

## What the output looks like

Each claim gets one label:

| Label | Meaning |
|---|---|
| `PRIMARY` | The organization's own site backs the claim and its figures. |
| `PRIMARY*` | The own site confirms the event, but at least one figure comes only from other sources. |
| `REPORTED` | Only third-party sources; the restricted search on the own site found nothing. |
| `SYNTHESIS` | A market-level statement with no single owner, backed by cited passages. |

The example below is the rendered output of the offline test fixture, **not a live run**. The sources and text are invented to show the format:

```
PRIMARY* 1. Nebius agreed to acquire Tavily for about $400 million.
  own site confirms the event, not every figure
  [P4] nebius.com · 2026-02-10 · Nebius Group today announced an agreement to acquire Tavily.
  [P1] technews.example · 2026-02-11 · Nebius agreed to acquire Tavily for about $400 million, sources said.
  figures not on the organization's site: 400

REPORTED 2. OpenAI expanded web search in its Responses API.
  only third-party sources; own site did not confirm
  [P3] blog.example · 2026-03-02 · OpenAI expanded web search in its Responses API to more models.

3 claims shown · 1 dropped (no valid passage) · 2 anchor searches · window from 2026-01-01
  dropped: Google cut grounding prices by 50%.
```

Claim 1 is the case this is built for. The deal is real and confirmed by the buyer. The price is reported, not confirmed, and the output says exactly that.

## Value

- **For a solo builder or early-stage team:** a review layer they don't have to build. Their agent tells users which statements are confirmed and which are reported, which makes it shippable.
- **For an enterprise team:** an audit trail per claim (passage, URL, date, primary or not), saved as JSON in `runs/`. This is what gets an agent past "can we put this in front of customers".
- **For Tavily:** the pattern uses features most builders never touch (`include_domains` with `restrict`, `chunks_per_source`, `start_date`, `published_date`). It's a reusable recipe for a guide, and better-trusted answers are what keep a builder calling search.
- **Portable:** `grounding.py` has no LangChain import. The same functions work with the Vercel AI SDK, OpenAI Agents, or a hand-written loop. Only the "get structured output from the model" step is framework-specific.

## Cost

| Step | Tavily credits |
|---|---|
| Research search, advanced depth (the agent may call it more than once) | 2 per call |
| Anchor search, basic depth, only for organization claims not already backed by their own site | 1 each, at most 5 per answer |

Rates are from Tavily's [credits page](https://docs.tavily.com/documentation/api-credits) (basic 1, advanced 2). Worst case adds 5 credits and one round of searches per answer. Anchor searches run one after another; running them concurrently is an easy latency improvement.

## How I verified it

- **17 offline tests** (`uv run pytest`). 14 cover the core: passage IDs, reprint dedupe, site matching (including look-alike domains), date parsing, dropped claims, anchor search only when needed, figures confirmed only by press, invented figures, stale windows, undated sources. 3 drive the **real** `create_agent` loop with a scripted model and a fake Tavily client, and check what is actually sent to Tavily (`start_date`, `chunks_per_source`, `include_domains` + `restrict`) and that a future `start_date` is never forwarded.
- **Tavily client checked directly:** `tavily-python` exposes `start_date`, `include_domains` and `include_domains_mode` as named parameters and forwards other keys (`chunks_per_source`, `include_published_date`) into the request body as-is.
- **Rendering checked by eye**, which caught a display bug (raw markup tags on the primary-source line), now fixed.

**Not yet verified, needs one run with keys:**
- How Kimi-K2.6 on Nebius behaves with the structured-output tool (`ToolStrategy` uses ordinary tool calling and retries on schema errors, so it should work, but I haven't watched it).
- Whether Tavily returns `published_date` for general-topic results with `include_published_date` set (documented as beta).
- Real `chunks_per_source` separators. The code splits on `[...]`; if Tavily uses a different one, each page becomes one passage. Still correct, just coarser.
- The eval numbers. Run `uv run eval.py` and paste the table here.

## Eval

`eval.py` runs 8 questions (product launches, funding, partnerships, regulation; some about one organization, some market-wide) and reports per question: claims shown, claims dropped, each label, flagged figures, stale claims, anchor searches, seconds. The headline is **share of claims anchored to the organization's own site**.

The starter has no claim structure, so these metrics don't exist for it; the comparison is presence vs absence, not a score difference. These are traceability metrics. For accuracy, hand-check a sample: for 10 `PRIMARY` claims, open the primary URL and mark whether it actually states the claim. That number is the honest measure of the anchor check.

## Limits

- **The site guess can be wrong.** If the model names `meta.com` when the news is on `about.fb.com`, the claim shows as `REPORTED` when it shouldn't. That's a false miss, never a false confirmation.
- **The anchor search checks topic, not truth.** The figure check is the only automatic comparison of content. The primary passage is shown so a person can confirm in a second.
- **Primary sources spin.** `PRIMARY` means the organization said it, not that it is true.
- **`published_date` is Tavily's estimate** and can reflect a later update, so the stale flag is a hint.
- **Figures are matched as digits.** "$1.2 billion" vs "1,200 million" won't match and gets flagged. Over-flagging, not under-flagging.
- **Model compliance.** A claim citing the wrong passage ID passes the ID check. The figure check catches some of these; a small entailment check on the claim and its one passage is the next step.

## Tracing

Set `LANGSMITH_TRACING=true` and `LANGSMITH_API_KEY` in `.env`. LangChain sends every model call and `web_search` call to LangSmith with no code changes. The anchor searches run outside the agent loop, so they're recorded in the `runs/*.json` record rather than the trace. Wrapping them with LangSmith's `@traceable` would put them in the same trace.

## Next

1. A small entailment check on (claim, cited passage) for claims that pass the ID check.
2. Run anchor searches concurrently.
3. Let builders pass known sites (`{"Meta": "about.fb.com"}`) to fix wrong guesses.

## Files

| File | Purpose |
|---|---|
| `grounding.py` | Framework-free core: passage registry, claim checks, labels. |
| `research_agent.py` | The starter's stack (LangChain + Nebius + Tavily) with the date-aware search tool, structured claims and the anchor pass. |
| `eval.py`, `eval_questions.txt` | Traceability eval over a question set. |
| `tests/` | Offline tests: core logic and the full agent loop. |
| `TECHNICAL_STATEMENT.md` | One-page approach, reasoning and value. |
| `BUILD_LOG.md` | How this was built with an AI coding partner, decision by decision. |
