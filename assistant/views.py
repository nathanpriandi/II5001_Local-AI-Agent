"""
Views for the AI assistant page inside the Oscar dashboard.

Access control comes from Oscar: AssistantDashboardConfig.post_process_urls
wraps every view below with Oscar's staff-only permission decorator.
"""

import json

import ollama
from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST

from . import agent
from .tools import apply_restock, list_low_stock

HISTORY_KEY = "assistant_history"
PENDING_KEY = "assistant_pending"


def _history(request):
    return request.session.get(HISTORY_KEY) or agent.new_history()


def index(request):
    """The chat page, rendered inside Oscar's dashboard layout."""
    return render(request, "assistant/index.html", {"model_name": settings.OLLAMA_MODEL})


@require_POST
def chat(request):
    """Run one agent turn and return the reply, tool trace and pending actions."""
    try:
        message = (json.loads(request.body or "{}").get("message") or "").strip()
    except json.JSONDecodeError:
        message = ""
    if not message:
        return JsonResponse({"error": "Empty message."}, status=400)

    try:
        reply, trace, pending, history = agent.run_agent(_history(request), message)
    except ollama.ResponseError as err:  # e.g. model not pulled
        return JsonResponse({"error": f"Ollama error: {err.error}"}, status=500)
    except Exception as err:  # noqa: BLE001  e.g. Ollama not running
        return JsonResponse({"error": f"Could not reach the local model ({err}). Is Ollama running?"}, status=500)

    # Save conversation and any restock requests waiting for confirmation
    request.session[HISTORY_KEY] = history
    stored = request.session.get(PENDING_KEY, {})
    for action in pending:
        stored[action["action_id"]] = action
    request.session[PENDING_KEY] = stored

    return JsonResponse({"reply": reply or "(empty answer)", "trace": trace, "pending": pending})


@require_POST
def confirm_action(request, action_id):
    """Staff clicked Confirm: apply the restock to Oscar's stock record."""
    stored = request.session.get(PENDING_KEY, {})
    action = stored.pop(action_id, None)
    if action is None:
        return JsonResponse({"error": "This request was already handled or has expired."}, status=404)

    new_stock = apply_restock(action)
    request.session[PENDING_KEY] = stored
    if new_stock is None:
        return JsonResponse({"error": "Stock record no longer exists."}, status=404)

    # Tell the agent what happened, so follow-up questions use the new numbers
    note = f"Staff confirmed: added {action['quantity']} units to {action['title']}. Stock is now {new_stock}."
    history = _history(request)
    history.append({"role": "assistant", "content": note})
    request.session[HISTORY_KEY] = history
    return JsonResponse({"message": note, "new_stock": new_stock})


@require_POST
def cancel_action(request, action_id):
    """Staff clicked Cancel: drop the pending request without changing stock."""
    stored = request.session.get(PENDING_KEY, {})
    action = stored.pop(action_id, None)
    request.session[PENDING_KEY] = stored
    if action:
        history = _history(request)
        history.append({"role": "assistant", "content": f"Staff cancelled the restock request for {action['title']}."})
        request.session[HISTORY_KEY] = history
    return JsonResponse({"message": "Request cancelled."})


@require_POST
def reset(request):
    """Clear the conversation and pending requests."""
    request.session.pop(HISTORY_KEY, None)
    request.session.pop(PENDING_KEY, None)
    return JsonResponse({"ok": True})


def snapshot(request):
    """Live low-stock table for the side panel (no AI involved)."""
    data = json.loads(list_low_stock())
    return JsonResponse({"low_stock": data if isinstance(data, list) else []})
