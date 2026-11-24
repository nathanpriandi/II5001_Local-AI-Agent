"""
Local model client (Ollama). All agents call the model through this module.

Two call styles:
  chat()       free text or tool calling (used by the Store Agent chat)
  chat_json()  structured output: Ollama constrains the answer to a JSON schema,
               which keeps a 4B model's output parseable (see Martra, ch. 7, on
               small models and structured output).
"""

import json
import re
import time

import ollama
from django.conf import settings


class LLMError(Exception):
    pass


_client = None


def client():
    global _client
    if _client is None:
        _client = ollama.Client(host=settings.OLLAMA_HOST)
    return _client


def _kwargs(num_predict=None):
    options = {"temperature": 0, "num_ctx": settings.AGENT_NUM_CTX}
    if num_predict:
        options["num_predict"] = num_predict
    kwargs = {"model": settings.OLLAMA_MODEL, "options": options, "keep_alive": "30m"}
    if settings.OLLAMA_MODEL.startswith("qwen3"):
        kwargs["think"] = False  # reasoning mode is slow on CPU and not needed here
    return kwargs


def clean(text):
    """Remove a leftover <think> block, if any."""
    return re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL).strip()


def chat(messages, tools=None, num_predict=None):
    """One model call. Returns the Ollama message."""
    kwargs = _kwargs(num_predict)
    if tools:
        kwargs["tools"] = tools
    return client().chat(messages=messages, **kwargs).message


def _extract_json(text):
    text = clean(text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)  # tolerate text around the JSON
        if match:
            return json.loads(match.group(0))
        raise


def chat_json(system, user, schema, num_predict=500, retries=1):
    """Structured call. Returns (data, seconds). Retries once with the parse error."""
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    started = time.monotonic()
    last_error = None
    for _ in range(retries + 1):
        kwargs = _kwargs(num_predict)
        message = client().chat(messages=messages, format=schema, **kwargs).message
        try:
            return _extract_json(message.content), time.monotonic() - started
        except (json.JSONDecodeError, TypeError) as err:
            last_error = err
            messages += [{"role": "assistant", "content": message.content or ""},
                         {"role": "user", "content": f"That was not valid JSON ({err}). Reply with JSON only."}]
    raise LLMError(f"Model did not return valid JSON: {last_error}")


def warm_up():
    """Load the model into memory so the first real request is not slowed by loading."""
    client().chat(messages=[{"role": "user", "content": "ok"}], **_kwargs(num_predict=1))
