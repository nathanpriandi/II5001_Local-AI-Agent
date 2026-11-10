"""
Agent loop for the Store Ops Assistant.

Same pattern as the Ollama chapter of the module (message roles + conversation
history), extended with tool calling:
    1. send history + tool schemas to the local model
    2. if it asks for tools, run them on Oscar's data and append the results
    3. repeat until the model answers in plain text (max MAX_TOOL_ROUNDS)
"""

import json
import re

import ollama
from django.conf import settings

from .tools import TOOLS

MAX_TOOL_ROUNDS = 5
MAX_HISTORY = 24  # messages kept besides the system prompt

SYSTEM_PROMPT = """You are the Store Ops Assistant for "Campus Tech Store", an online shop run on Django Oscar.
You help shop staff with stock, products, orders and sales.

Rules:
- Always use the tools to get data. Never invent prices, stock levels, order numbers or sales figures; quote the numbers the tools return.
- Prices are in USD.
- To act on a product you need its product_id: get it from search_products or list_low_stock first.
- Restocking: only call propose_restock when the user asks to restock or reorder. It creates a request that staff confirm with a button; tell the user to click Confirm. Never claim stock has already changed.
- When asked what to restock, combine list_low_stock with sales data (get_product_details or get_sales_summary) and suggest quantities with a short reason.
- Answer briefly: findings first, then the recommended action. Use short lists for several products.
- If a request is unrelated to running this shop, say you only handle store operations."""

client = ollama.Client(host=settings.OLLAMA_HOST)


def new_history():
    return [{"role": "system", "content": SYSTEM_PROMPT}]


def _trim(history):
    """Keep the system prompt and the latest turns, cutting only at a user message
    so a tool result is never separated from the call that produced it."""
    if len(history) - 1 <= MAX_HISTORY:
        return history
    body = history[1:]
    for i, m in enumerate(body):
        if m["role"] == "user" and len(body) - i <= MAX_HISTORY:
            return [history[0]] + body[i:]
    return [history[0]]


def _call_model(history, use_tools=True):
    kwargs = {
        "model": settings.OLLAMA_MODEL,
        "messages": history,
        "options": {"temperature": 0, "num_ctx": 8192},  # deterministic, bounded memory
    }
    if use_tools:
        kwargs["tools"] = list(TOOLS.values())
    if settings.OLLAMA_MODEL.startswith("qwen3"):
        kwargs["think"] = False  # thinking mode is slow on CPU and not needed here
    return client.chat(**kwargs).message


def _as_dict(message):
    """Convert an Ollama Message into a plain dict so it can live in the session."""
    data = {"role": "assistant", "content": message.content or ""}
    if message.tool_calls:
        data["tool_calls"] = [
            {"function": {"name": c.function.name, "arguments": dict(c.function.arguments or {})}}
            for c in message.tool_calls
        ]
    return data


def _clean(text):
    """Remove any leftover <think> block from the visible answer."""
    return re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL).strip()


def run_agent(history, user_message):
    """Run one user turn. Returns (reply, trace, pending_actions, new_history)."""
    history = _trim(history)
    history.append({"role": "user", "content": user_message})
    trace, pending = [], []

    for _ in range(MAX_TOOL_ROUNDS):
        message = _call_model(history)
        history.append(_as_dict(message))
        if not message.tool_calls:  # no tool requested = final answer
            return _clean(message.content), trace, pending, history

        for call in message.tool_calls:
            name = call.function.name
            args = dict(call.function.arguments or {})
            func = TOOLS.get(name)
            if func is None:
                result = json.dumps({"error": f"Unknown tool '{name}'", "available": list(TOOLS)})
            else:
                try:
                    result = func(**args)
                except TypeError as err:  # wrong or missing arguments: let the model retry
                    result = json.dumps({"error": f"Bad arguments for {name}: {err}"})
            # Write actions are not executed here; they wait for a staff click
            if name == "propose_restock":
                parsed = json.loads(result)
                if parsed.get("status") == "awaiting_staff_confirmation":
                    pending.append(parsed)
            trace.append({"tool": name, "args": args, "result": result})
            history.append({"role": "tool", "content": result, "tool_name": name})

    # Too many rounds: ask for a text answer from what has been gathered
    message = _call_model(history, use_tools=False)
    history.append(_as_dict(message))
    return _clean(message.content), trace, pending, history
