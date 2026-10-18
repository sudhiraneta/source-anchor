# Technical Statement

## The problem

People use a research agent so they don't have to read twenty pages. That only works if they can trust the answer without checking it. The starter agent can't earn that trust. It doesn't know today's date, so "this year" means nothing. Its sources come as a URL list at the end, so you can't tell which sentence a link supports, or whether a sentence is supported at all. And for "what changed" questions, most results are news coverage that repeats itself. Ten sites repeating one rumor look like ten sources.

The result is that the reader checks every claim by hand. An answer you have to check yourself saves no time, and that is where a research product loses its users. For a solo builder, it means an agent they can't ship. For an enterprise team, it means an agent that fails review because nobody can audit it.

## The approach

Each claim in the answer must show the passage it came from, and claims about an organization are checked against that organization's own website.

1. **Cite by ID.** Every passage Tavily returns gets a number (`[P1]`, `[P2]`...), and the model may only cite those numbers. A claim with no valid ID is dropped and listed. The model never has a chance to cite anything else, so there's nothing to check after the fact.
2. **Go to the source.** When a claim is about one organization's own action (a launch, a deal, a price), the model names that organization's site. If no cited passage comes from it, the code runs one Tavily search restricted to that site (`include_domains`, `restrict`).
3. **Check figures and dates.** Numbers in the claim are compared with the primary passage and with all cited passages. Today's date goes into the prompt. The model proposes a `start_date`, and code rejects it if it's malformed or in the future.

Each claim gets one label: `PRIMARY`, `PRIMARY*` (the event is confirmed, a figure isn't), `REPORTED`, or `SYNTHESIS`. The answer stays short: one summary sentence and at most five claims.

## Thought process

I chose this over three other approaches I worked through:

- **Ask the model to quote, then string-match the quote.** Breaks on reformatting and flags correct paraphrases.
- **Re-search each claim and trust the relevance score.** Relevance is not support. A claim with a wrong date about a real event still ranks high.
- **Rank sources by a list of "reputable" domains.** Subjective, never complete, and builders won't adopt a pattern that needs a hand-curated list.

The key change was replacing "how many sites repeated it" with "did the source itself say it". Forty reprints of a press release don't outweigh the press release, so echo detection and domain lists aren't needed. Primary sources can still spin, so `PRIMARY` means "the organization said it", not "it is true". The README says this directly.

## The value

- **Technical:** unsupported claims can't reach the reader unnoticed, and every claim carries an audit record (passage, URL, date, label), saved as JSON. The trust logic (`grounding.py`) imports nothing from LangChain, so it works with any agent framework that calls Tavily.
- **For builders:** a solo developer gets a review layer without building one, and an enterprise team gets the audit trail that review processes ask for.
- **For Tavily:** it's a reusable recipe built on features most builders never use (`include_domains` with `restrict`, `chunks_per_source`, `start_date`, `published_date`). Answers people trust keep builders calling search. It costs at most 5 extra basic-depth credits per answer.

## Verification and limits

There are 17 offline tests, including the real `create_agent` loop driven by a scripted model and a fake Tavily client, which check the exact parameters sent to Tavily. Live behavior with Kimi-K2.6 and the eval results are reported separately in the README. Known limits: the model can guess an organization's site wrong (which causes a false miss, never a false confirmation), the anchor search checks topic rather than truth, and `published_date` is Tavily's estimate.
