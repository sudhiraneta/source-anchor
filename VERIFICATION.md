# Verification: from code to the experience

Every behavior the user sees is traced here to the code that produces it, the offline test that proves the logic, and the live run that shows it holding on real data. Four layers, each catching what the one before it can't.

| Layer | What it proves | What it can't prove |
|---|---|---|
| 1. Unit tests (`tests/test_grounding.py`, 24) | Each check is correct in isolation | That the pieces are wired together |
| 2. Agent-loop tests (`tests/test_agent_offline.py`, 9) | The real `create_agent` loop sends the right parameters to Tavily and routes results through the checks | How a real model behaves |
| 3. Scenario tests (`tests/test_scenarios.py`, 12) + harness tests (`tests/test_planted.py`, 6) | Each research-concise question *shape* lands on its designed label; the planted-error harness mutates and reloads claims correctly | What the live web returns |
| 4. Live runs + page check (`runs/*.json`, README "Live results") | Real model, real Tavily, real pages: labels are right | Any specific *future* run (search varies) |

Run layers 1–3: `uv run pytest` (51 tests, no keys). Layer 4 is recorded in `runs/`.

## Guarantee by guarantee

### "A claim cannot cite a source that doesn't exist"

- **Code:** `grounding.PassageRegistry` numbers every passage Tavily returns across all searches; `check_claims` looks each cited ID up and **drops** the claim if any ID is missing (`Report.dropped`). Passages under 40 characters are never registered, so fragments can't be cited either.
- **Logic proven:** `test_claim_citing_unknown_passage_is_dropped`; `test_false_premise_claim_citing_nonexistent_passage_is_dropped` (also asserts no anchor search is spent on it); `test_registry_caps_total_passages`.
- **Live:** 0 dropped across 4 runs — the model never invented an ID — and the page check found all 12/12 cited spans on their pages.

### "A quoted span is really in the passage"

- **Code:** `apply_verdicts` normalizes whitespace/case and requires `span ⊂ passage.text`; a verdict whose span isn't found is **discarded, not trusted**.
- **Logic proven:** `test_invented_span_is_discarded`; the scenario harness itself tripped this twice when my fake judge produced a span not in the passage — the code correctly rejected it (see BUILD_LOG §7).
- **Live:** every "stated in Pn" line in every run was independently found on the live page.

### "PRIMARY means the organization's own page states it, not just mentions the topic"

- **Code:** anchor search = one Tavily call with `include_domains=[site]`, `include_domains_mode="restrict"`, `search_depth="basic"`, `max_results=3`. `is_same_site` accepts subdomains (`community.openai.com` → openai.com) and rejects look-alikes. Then the **judge must mark the own-site passage `supports` with a verified span**; a topical hit without that is downgraded to `REPORTED` and `primary` is cleared.
- **Logic proven:** `test_primary_from_anchor_search_and_figure_only_in_press`; `test_primary_downgraded_when_own_site_does_not_state_claim` (the relevance-is-not-support fix); `test_anchor_results_from_other_sites_are_ignored`; `test_domain_helpers`; scenario `test_own_site_on_topic_but_not_stating_claim_is_reported_not_primary`.
- **Live:** Agents API → `PRIMARY`, and the openai.com page fully states it. Sol/Luna → `PRIMARY*` via `community.openai.com` (subdomain rule working), page states the rollout.

### "Figures the sources don't contain are flagged"

- **Code:** `figures()` extracts digits from the claim; `_supported()` checks each against passage text (a year also counts if a passage was published that year). Missing from all passages → `figures_not_in_any_source`; missing only from the primary → `figures_not_in_primary` → `PRIMARY*`.
- **Logic proven:** `test_invented_figure_is_flagged_even_with_valid_citation`; `test_year_supported_by_publish_date`; scenario `test_invented_figure_is_flagged_even_with_real_citation`.
- **Live:** planted number changes caught **10/10**. Sol/Luna's "22" (date digit) correctly flagged as not in the own-site text → `PRIMARY*`.

### "Time-scoped questions get a window, and stale sources are flagged"

- **Code:** today's date is injected into the prompt; the model proposes `start_date`; `parse_start_date` rejects malformed or future dates **before** anything reaches Tavily; `stale` = every cited passage dated and all before the window.
- **Logic proven:** `test_future_start_date_is_ignored_not_forwarded` (asserts the bad date never reaches the Tavily call); `test_stale_only_when_every_cited_passage_predates_window`; `test_undated_passage_is_never_called_stale`; scenario `test_time_scoped_question_flags_sources_older_than_window`.
- **Live:** `start_date=2026-01-01` sent on every run (recorded in `runs/*.json → searches`).

### "Ten sites repeating one rumor count as one source"

- **Code:** `PassageRegistry` stores verbatim-duplicate chunks once (normalized text set).
- **Logic proven:** `test_chunks_get_own_ids_and_reprints_are_stored_once`; scenario `test_syndicated_reprints_count_once`.

### "A market-wide question doesn't pretend to have a primary source"

- **Code:** claims with `source_domain=None` skip the anchor search entirely → `SYNTHESIS`.
- **Logic proven:** scenario `test_market_trend_question_is_all_synthesis_and_no_anchor_searches` (asserts 0 anchor searches).
- **Live:** the assignment question → 5/5 `SYNTHESIS`, 0 anchor searches.

### "CONTESTED only appears when asked for"

- **Code:** `apply_verdicts(..., surface_contradictions=False)` drops `contradicts` verdicts by default; `--strict` turns them on; `planted.py` always passes `True` so the catch rate measures the judge, not the default.
- **Logic proven:** `test_contradiction_is_dropped_by_default`; `test_contradiction_wins_over_support_in_strict_mode`; scenario `test_contradiction_hidden_by_default_shown_with_strict`.
- **Live, and why the default is what it is:** three `CONTESTED` labels on the OpenAI re-run; **all three confirmed wrong against the live pages** (README "Correctness"). Final-code default run: 0 `CONTESTED`, no wrong flag.

### "The run never breaks on a model that misbehaves"

- **Code:** if the writer ends without calling the answer tool, `recover_answer` parses JSON from its text, then forces one structured call; if the judge throws, `Report.judge_error` is set and labels fall back to provenance — the failure is **reported, not hidden**.
- **Logic proven:** `test_recovers_when_model_ends_with_json_text`; `test_recovers_with_forced_structured_call_when_text_is_prose`; `test_clear_error_when_recovery_fails`; `test_judge_failure_keeps_provenance_labels` (asserts `judge_error` is populated — this test is what exposed that my first version swallowed the error silently).
- **Live:** Kimi-K2.6 returned empty replies through `create_agent` (`diag_model.py` reproduces it); this is why the recovery path exists and Qwen3-235B is the recommended model.

## What is *not* validated, stated plainly

- **Truth.** `PRIMARY` means the originator said it. A press release can be wrong.
- **Completeness.** Nothing flags a change the agent didn't find.
- **Any specific future run.** Tavily returns different pages run to run; two claims moved `PRIMARY` → `REPORTED` between runs. Always in the safe direction, but the labels are not deterministic.
- **The judge as an oracle.** It is a model. The span check makes it *checkable*; `planted.py` and the page check make it *measured*; neither makes it independent.

## How to re-run the validation yourself

```bash
uv run pytest                                      # layers 1–3, ~1s, no keys
uv run research_agent.py "<question>" --model Qwen/Qwen3-235B-A22B-Instruct-2507
uv run planted.py runs/ --model Qwen/Qwen3-235B-A22B-Instruct-2507
```
Then open any `primary_url` in `runs/*.json` and check the quoted span against the page — that is the layer-4 check, and it takes about a minute per claim.
