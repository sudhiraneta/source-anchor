<div align="center">

<br/>

# Source Anchor

### Every claim shows its work.

<br/>

</div>

<br/>

Research agents don't fail in demos. They fail in review.

Five confident sentences, a list of links, and no way to tell which link backs which sentence — or whether the company said it, or a blog said the company said it. So the reader re-checks everything. An answer you must re-check has saved nothing.

That is why agents stall in pilot. Not weak search. **Unauditable output.** A team cannot approve what it cannot trace — and Tavily is paid only when agents reach production.

<br/>

<div align="center">

### The metric

**How much of the answer can the reader trust without re-checking.**

<br/>

| **12 / 12** | **88%** | **0** |
|:---:|:---:|:---:|
| quoted sentences found on their real source pages | deliberately broken claims caught | wrong flags in the default output |

<br/>

</div>

<br/>

### The answer

The passage, next to the claim, labeled by who said it.

```
PRIMARY    OpenAI announced the public beta of its Agents API on September 10, 2026.
           openai.com: "Introducing the Agents API. Build and run cloud agents with
           the Codex harness, fully managed by OpenAI."

REPORTED   GPT-6.1 Sol launched September 29, priced at $2 per million input tokens.
           tech-insider.org states this. OpenAI's own site does not.
```

The reader stops checking everything. They check the ones marked `REPORTED`.

<br/>

### How

A claim may only cite a passage Tavily actually returned. A judge copies the exact sentence that states it, and code confirms the sentence is there. For a claim about a company, one search of the company's own site decides whether they said it or the press did. Every number is checked against the sources.

Four promises. Kept by code, not by the model. One file, no framework dependency.

<br/>

<div align="center">

```bash
uv run research_agent.py "What did OpenAI announce for its API this year?"
```

<br/>

[**Technical statement**](TECHNICAL_STATEMENT.md) · [**Details**](DETAILS.md) · [**Verification**](VERIFICATION.md) · [**Build log**](BUILD_LOG.md)

<br/>

*`PRIMARY` means the company said it. Not that it's true.*

</div>
