# Build Log

How this was built: I worked with Claude (Opus) in claude.ai as a coding and research partner. This log summarizes the session in order: what I asked for, what came back, and what I decided. The full chat is linked at the end.

My approach was to make the AI argue against its own proposals before writing any code, and to check facts against the docs and the installed packages instead of trusting what the model remembered.

## 1. Scoping: who is the user, what hurts

- I chose Option 1 and defined the persona: someone using a research assistant in a B2B setting, or a solo builder shipping one.
- The pain I settled on: answers that mix time periods and claims you can't trace, so the user re-checks everything and stops using the product.
- Verified against the code: the starter has no current date, one default `TavilySearch()` call, citations described only as "include URLs when available", and no eval or tracing.
- Verified against Tavily's `/search` docs: `start_date`, `include_domains` with `include_domains_mode`, `chunks_per_source`, `include_published_date`, `published_date`, `score`.

## 2. First plan, then I made the AI attack it

**First plan:** inject the date, enforce a time window, return structured claims with quotes, and string-match the quotes.

**I asked it to challenge the plan.** It found these problems:
- "What changed this year" needs before-and-after reasoning, not just a date filter.
- `published_date` is Tavily's estimate.
- Snippets are too short to quote-check.
- Regex on "this year" is brittle.

## 3. Competitors: what already exists

I asked how competitors handle grounding, with sources required.
- OpenAI, Anthropic and Perplexity: the model cites its own sources, and nothing independently checks them.
- Exa: returns highlights and leaves verification to the builder.
- Google: a dedicated Check Grounding API with per-claim support scores, but tied to Vertex AI.
- Academic work: NLI entailment models, not offered as a product.

**My push:** Tavily's growth comes from small teams and weekend builders, not from teams on Vertex AI. So the fix had to take one file and add no new vendor.

## 4. Three rounds of "is there something better"

1. **Re-search each claim and trust the relevance score.** When challenged, the AI rejected its own idea: relevance is not support, and it would pass claims that are almost right.
2. **Cite by passage ID (evidence first).** The model can only cite passages that exist, so there's nothing to check afterward. Kept.
3. **Count supporting domains for confidence.** My objection: *a viral claim repeated by many sites can still be wrong.* A curated list of trusted domains was proposed next. I asked for a better approach, which led to:
4. **Go to the primary source.** For a claim about an organization, search that organization's own site. This replaced domain counting, echo detection and the curated list.

I also asked whether each piece had evidence behind it. That separated what we had verified (LangChain's `ToolStrategy` falls back to tool calling, Tavily's parameters) from my own estimates (time, the eval baseline).

## 5. Build

- `grounding.py` came first and imports nothing from LangChain, because many B2B customers don't use LangChain.
- Checked the installed `tavily-python` source before relying on it. `start_date`, `include_domains` and `include_domains_mode` are named parameters; other keys such as `chunks_per_source` are passed into the request body unchanged.
- Used `ToolStrategy` on purpose instead of relying on Kimi's native JSON mode. It only needs tool calling, which the starter already uses, and it retries on schema errors.
- 14 core tests passed before the agent was written.
- 3 offline end-to-end tests run the real `create_agent` loop with a scripted model and a fake Tavily client, and check the exact parameters sent to Tavily.

## 6. Bugs and corrections caught by checking

- **Display bug:** reviewing the rendered output showed raw `[green]` markup on the primary-source line. Fixed.
- **README claim:** the README said the eval included a TPRM-style question. It didn't, so I removed the claim.
- **Credit costs:** checked against Tavily's pricing page (basic 1, advanced 2) before stating them.
- **Example output:** the README labels it as test-fixture output, not a live run.

## 7. What I ran live

*(fill in after running with keys)*
- Starter on the assignment question: output saved as the "before".
- `research_agent.py` on the same question: does Kimi follow the cite-by-ID format? Is `published_date` returned?
- `eval.py` table and a hand check of 10 `PRIMARY` claims.

## Full chat

Link to the shared claude.ai conversation: *(add share link)*
