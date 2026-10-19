# Source Anchor

## The moment

You ask a research agent what changed this year. It gives you five confident sentences and a list of links.

Now you have to check all five. Which link backs which sentence? Did the company say that, or did a blog say the company said that? Is that number real?

An answer you have to re-check is not an answer. It is homework with a confident tone.

That is why agents stall in pilot. Not because search is weak, but because no one can approve an answer they can't audit. For Tavily, that wall is the business: it is paid when agents reach production, and trust is the gate.

## The idea

Make every sentence show its work.

Not a link at the bottom. The actual passage, next to the claim, with a label that says who said it.

```
PRIMARY   OpenAI announced the public beta of its Agents API on September 10, 2026.
          openai.com: "Introducing the Agents API. Build and run cloud agents with the
          Codex harness, fully managed by OpenAI."

REPORTED  GPT-6.1 Sol launched September 29, priced at $2 per million input tokens.
          tech-insider.org states this. OpenAI's own site does not.
```

The reader stops re-checking everything. They check the ones that say `REPORTED`.

## Four promises, kept by code

**No invented sources.** A claim may only cite passages Tavily actually returned. Cite one that doesn't exist, and the claim is dropped.

**No misquotes.** A judge must copy the exact sentence that states the claim. Code confirms that sentence is in the passage. If it isn't, the verdict is discarded.

**Who said it.** For a claim about an organization, one search restricted to that organization's own site. The label says whether the originator said it or only the press did.

**No invented numbers.** Every figure in a claim is checked against the passages. A number no source contains is flagged.

These hold no matter how good the model is. They are checked in code, on text already in memory.

## What we rejected, and why

We tried the obvious things first.

String-match the model's quotes: brittle, flags correct paraphrases. Trust Tavily's relevance score as proof: relevance is not support. Count how many sites agree: forty reprints of one press release are still one source. A list of trusted domains: nobody adopts a pattern that needs hand-curation.

Google ships a real grounding check. It is locked to Vertex AI, where Tavily's builders are not.

The move that made the rest unnecessary: stop asking how many sites repeated it. Ask whether the source itself said it.

## Measured, not asserted

- **12 of 12** quoted spans, across every live run, found on their actual source pages.
- **88%** of claims we deliberately broke — wrong number, wrong company, wrong year — caught. Wrong numbers: 10 of 10.
- The `PRIMARY` label, checked against the live openai.com page: fully stated.

## The thing we turned off

The judge can also say a passage *contradicts* a claim. On the live run it did so three times. All three were wrong. None of the passages conflicted with its claim; two stated the claim outright, with extra detail the judge mistook for a conflict.

We verified that against the real pages. Then we turned the label off by default.

A red flag you have to disprove is worse than no flag. The default output shows only labels the code can stand behind. `--strict` turns contradictions back on. The fix that would make them reliable is specified, and it will ship when it is measured, not before.

## What it costs

About seven Tavily credits and three to five model calls per answer. Roughly double the starter's latency. One file of trust logic, `grounding.py`, with no LangChain dependency. It drops into any agent that calls Tavily.

## What it does not do

It does not know if the source is right. `PRIMARY` means the organization said it. It does not know what it didn't find. And the same question on two days can return different pages, so two claims moved from `PRIMARY` to `REPORTED` between runs. Always in the safe direction. Never a false `PRIMARY`.

## Why this matters to Tavily

Every label the agent produces is also a signal Tavily's Feedback endpoint accepts: which result was used, which was quoted, which was the originator. Most agents can't send that because they don't know. This one does, per claim.

The user's trust and Tavily's ranking signal are the same data. Computed once.

---

*51 tests, four live runs, one correctness check against the live pages. The full evidence chain is in `VERIFICATION.md`. How it was built, decision by decision, is in `BUILD_LOG.md`.*
