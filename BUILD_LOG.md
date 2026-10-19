# Build Log

How this was built, written for a reviewer who wants to know one thing: when the AI was wrong, did I notice, and what did I do about it.

I built this with Claude in claude.ai as a coding and research partner. Two rules governed the whole session:

1. **Make the AI argue against its own proposal before writing code.** Every design below was challenged at least once; four of them died that way.
2. **Verify against the source, never against the model's memory.** Tavily's parameters came from reading the installed `tavily-python` client. Credit costs and the Feedback endpoint came from Tavily's docs. Model behavior came from running it.

Everything below points at an artifact you can check: a commit, a test, or a file in `runs/`.

## The decision arc

### 1. What problem, and for whom

I rejected the obvious reading of the starter ("make the search better") in favor of a user problem: a reader who gets an answer they must re-check by hand has saved no time, so the agent never reaches production. For Tavily, that is the revenue gate — it is paid on production usage. The persona was deliberately broad (solo builder, enterprise analyst, marketplace admin) because the fix had to be one pattern, not three features.

Verified against the code before designing: the starter has no date in its prompt, one default `TavilySearch()` call, citations described only as "include URLs when available," and no eval or tracing.

### 2. Four designs I killed, and who killed them

| Design | Killed by | Why |
|---|---|---|
| Model quotes a sentence, code string-matches it | The AI, when I asked it to attack the plan | Brittle on reformatting; flags correct paraphrases |
| Re-search each claim, trust Tavily's relevance score | The AI, on its own proposal, after I pushed | Relevance is not support; a wrong date on a real event still ranks high |
| Count how many domains agree | **Me** | A viral rumor on ten sites is still one rumor |
| Curated list of "trusted" domains | **Me** | Subjective, never complete, nobody adopts a pattern that needs hand curation |

The survivor replaced "how many sites repeated it" with "did the source itself say it": cite only passages Tavily returned, and for a claim about an organization, search that organization's own site. That one move made echo detection and domain lists unnecessary.

I also checked what competitors ship. OpenAI, Anthropic and Perplexity self-cite with no independent check. Google has a real Check Grounding API, but it is locked to Vertex AI, where Tavily's builders are not. That told me the gap was real and the fix had to add no new vendor.

### 3. The build, and what I made the AI prove first

- `grounding.py` was written first and imports nothing from LangChain, so the trust logic works in any framework that calls Tavily. 14 core tests passed before any agent code existed.
- 3 end-to-end tests drive the *real* `create_agent` loop with a scripted model and a fake Tavily client and assert the exact parameters sent to Tavily. This caught that a future `start_date` would otherwise be forwarded.
- I chose LangChain's `ToolStrategy` over the model's native JSON mode because it needs only tool calling, which the starter already depends on, and retries on schema errors.

### 4. The challenge that produced the judge

Halfway through I asked how deep the "verification" actually went. The honest answer: the anchor search only proved the organization's site had a page *on the topic*. That is relevance, not support — the exact flaw I had rejected in design #2. I had rebuilt the thing I killed.

The fix: a judge reads each (claim, passage) pair and must copy the exact sentence that states the claim, and code verifies that sentence is really in the passage. An invented quote is discarded, not trusted. `PRIMARY` now means the organization's own page *states* the claim.

A test immediately exposed that my error handling silently swallowed a judge bug. I replaced it with a reported `judge_error`, so failures surface instead of hiding. (`tests/test_grounding.py::test_judge_failure_keeps_provenance_labels`)

### 5. What I measured, and what the measurement said back

- **`diag_model.py`**: the starter's default model, Kimi-K2.6, returned empty replies through `create_agent`. Qwen3-235B answered and tool-called correctly. I switched the recommended model and added a recovery path. (commit `7b976f9`)
- **The assignment question** returned five `SYNTHESIS` claims — correct, because a market-wide question has no originator. I added an organization question so the primary path gets exercised. (`runs/1792362444.json`)
- **The organization question**: `openai.com` confirmed the Agents API claim by its own headline → `PRIMARY`. The anchor works live. (`runs/1792365221.json`)
- **`planted.py`** broke 24 real claims on purpose and re-judged them: **21/24 caught (88%)** — numbers 10/10, negations 2/2, actors 4/5, years 5/7. The misses were a year buried in a citation and an actor swap the judge read past while the magnitudes still matched.

### 6. The finding I chose not to hide

Reading the live output against its passages showed the judge **over-fires `CONTESTED`**. On the OpenAI re-run, three of five claims were flagged, and at least two were wrong: one passage actually *stated* the claim, another merely listed extra pricing tiers around the same numbers. I tightened the judge prompt; the re-run showed it reduced but did not eliminate this. A prompt nudge cannot reliably stop an LLM from calling "compatible but different" a contradiction.

I then designed the real fix — make a contradiction code-checkable by requiring the judge to name a value the passage genuinely lacks — and worked through it far enough to see the cost: on this run it clears all three false positives, but it also downgrades a swapped-actor contradiction whose numbers match, which would lower the planted-actor catch rate. That trade-off needs live iteration I could not finish before the deadline.

**The call:** ship the measured limitation, not the untested patch. `CONTESTED` is now presented as "inspect this," with both passages shown; the gate and its trade-off are specified under Next in the README. I would rather a reviewer see a known, bounded weakness than an unverified heuristic that moves the error somewhere I didn't measure. (commit `c8984d0`)

### 7. Small things the checking caught

- Raw markup leaking into the rendered output. Fixed on sight.
- A README claim about a TPRM question in the eval set that wasn't there. Removed.
- A stale "figures not on own site" line surviving on a `REPORTED` claim after its primary was rejected. Fixed with a regression test.
- The technical statement drifting out of sync with the code after the judge was added (wrong test count, missing labels). Rewritten to match. (commit `409e44e`)

## What I'd want a reviewer to take from this

- The code's guarantees are structural and hold regardless of model quality: no invented sources, no invented numbers, every shown span verified in code.
- The judge is a model, and I treated it like one — measured it, found exactly where it breaks, and bounded the claim rather than overselling it.
- Every number in the README comes from a file in `runs/` or a test you can run.

## Evidence

- `git log` — the commits above, in order.
- `runs/*.json` — every live run, with per-claim verdicts and spans.
- `uv run pytest` — 38 offline tests, no keys needed.
- `uv run planted.py runs/` — reproduces the catch rate from the recorded runs.
