"""Quick check of how a Nebius model behaves. Not part of the agent.

    uv run diag_model.py                                        # Kimi (default)
    uv run diag_model.py Qwen/Qwen3-235B-A22B-Instruct-2507

Used to find out why the starter's default model returned empty replies.
Prints, for a plain prompt and a tool-call prompt: the content, any tool
calls, the finish reason and token usage.
"""

import os
import sys

from dotenv import load_dotenv
from langchain_core.tools import tool
from langchain_nebius import ChatNebius

load_dotenv()
for k in ("NEBIUS_API_KEY",):
    if not os.getenv(k) and os.getenv(k.lower()):
        os.environ[k] = os.environ[k.lower()]

model_name = sys.argv[1] if len(sys.argv) > 1 else "moonshotai/Kimi-K2.6"
model = ChatNebius(model=model_name)


def show(label: str, msg) -> None:
    meta = getattr(msg, "response_metadata", {}) or {}
    usage = getattr(msg, "usage_metadata", None)
    print(f"\n== {label}")
    print("content:", repr(msg.content)[:300])
    print("tool_calls:", msg.tool_calls)
    print("finish_reason:", meta.get("finish_reason"), "| usage:", usage)


@tool
def web_search(query: str) -> str:
    """Search the web."""
    return "result"


print("model:", model_name)
show("1. plain reply", model.invoke("Reply with the single word: ok"))
show("2. tool call", model.bind_tools([web_search]).invoke("Search the web for 'AI search news'."))
