"""
Buyer-facing Shopping Agent.

Every request here only queues work or approves it: the agent worker runs the
model, and the page polls the run to show each step live. Money moves only
through the Mandate Guard (agentic.guard) and the Campus Wallet.
"""

import json
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.template.loader import render_to_string
from django.views.decorators.http import require_POST
from oscar.core.loading import get_model

from agentic import guard
from agentic.commerce import available, stockrecord, wallet_for, wallet_topup
from agentic.models import AgentRun, Mandate, Negotiation, WalletEntry, WorkerHeartbeat
from agentic.negotiation import pay_negotiation, start_negotiation
from agentic.runs import current_policy, enqueue_now
from agentic.shopping_agent import cancel_mandate, create_intent_mandate, pay_plan

Product = get_model("catalogue", "Product")
Order = get_model("order", "Order")

EXAMPLES = {
    "plan": [
        ("Air-quality monitor", "An air-quality monitor for my dorm room that I can check from my phone", "60"),
        ("Motion alarm", "A motion-triggered alarm with a small screen for my desk", "45"),
        ("Weather logger", "Log temperature and humidity on my balcony", "40"),
    ],
    "delegate": [
        "Buy 2 MQ-135 sensors when they are back in stock, but no more than $6 each, within a week",
        "Get me one Raspberry Pi 5 if the price stays under $85 this month",
    ],
}


def _data(request):
    try:
        return json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return {}


def _money(value, default=None):
    try:
        return guard.money(Decimal(str(value)))
    except (InvalidOperation, TypeError, ValueError):
        return default


def _side_context(user):
    wallet = wallet_for(user)
    delegations = list(Mandate.objects.filter(owner=user, kind=Mandate.INTENT).select_related("product").order_by("-created")[:6])
    for d in delegations:
        d.valid = guard.verify(d)
    return {
        "wallet": wallet,
        "entries": WalletEntry.objects.filter(wallet=wallet).order_by("-created", "-id")[:6],
        "delegations": delegations,
        "orders": Order.objects.filter(user=user).order_by("-date_placed")[:4],
    }


def _side_html(request):
    return render_to_string("shopper/_side.html", _side_context(request.user), request=request)


@login_required
def index(request):
    policy = current_policy()
    products = []
    for p in Product.objects.prefetch_related("stockrecords").order_by("title"):
        sr = stockrecord(p)
        if sr:
            products.append({"id": p.pk, "title": p.title, "price": sr.price, "available": available(sr)})
    context = _side_context(request.user)
    context.update({
        "products": products, "examples": EXAMPLES, "beat": WorkerHeartbeat.get(),
        "min_qty": (policy.terms.get("min_qty_for_discount", 5) if policy else 5),
        "negotiation_mode": (policy.terms.get("negotiation_mode", "auto") if policy else "auto"),
        "resume": AgentRun.objects.filter(user=request.user, kind__in=["plan", "parse_delegation", "negotiate"]).order_by("-created").first(),
    })
    return render(request, "shopper/agent.html", context)


def _queued(run):
    return JsonResponse({"run_id": run.pk})


@login_required
@require_POST
def plan_start(request):
    data = _data(request)
    goal = (data.get("goal") or "").strip()[:400]
    if len(goal) < 8:
        return JsonResponse({"error": "Describe the project in a sentence."}, status=400)
    budget = _money(data.get("budget"))
    run = enqueue_now("plan", user=request.user, payload={"goal": goal, "budget": float(budget) if budget else None})
    return _queued(run)


@login_required
@require_POST
def delegate_parse(request):
    text = (_data(request).get("text") or "").strip()[:300]
    if len(text) < 8:
        return JsonResponse({"error": "Write an instruction, for example the product and your maximum price."}, status=400)
    return _queued(enqueue_now("parse_delegation", user=request.user, payload={"text": text}))


@login_required
@require_POST
def delegate_create(request):
    """The buyer signs an intent mandate; the agent may later buy without them (funds are held now)."""
    data = _data(request)
    product = get_object_or_404(Product, pk=data.get("product_id"))
    try:
        quantity = max(1, min(int(data.get("quantity") or 1), 10))
        days = max(1, min(int(data.get("days_valid") or 7), 30))
    except (TypeError, ValueError):
        return JsonResponse({"error": "Quantity and days must be whole numbers."}, status=400)
    max_price = _money(data.get("max_unit_price"))
    if max_price is None or max_price <= 0:
        return JsonResponse({"error": "Set a maximum price per unit."}, status=400)
    try:
        mandate = create_intent_mandate(request.user, product, quantity, max_price, days)
    except ValueError as err:
        return JsonResponse({"error": str(err)}, status=400)

    # Link the mandate to the run it came from, so the run card shows it
    run_id = data.get("run_id")
    if run_id:
        run = AgentRun.objects.filter(pk=run_id, user=request.user).first()
        if run and run.result:
            if run.kind == "plan":
                for item in run.result.get("items", []):
                    if item["product_id"] == product.pk:
                        item["mandate"] = mandate.ref
            else:
                run.result["mandate"] = mandate.ref
                run.step("Wait for you to sign the mandate", "done", f"signed {mandate.ref}; ${mandate.terms['hold']} held")
            run.save()
    return JsonResponse({"ok": True, "mandate": mandate.ref, "side": _side_html(request)})


@login_required
@require_POST
def delegate_cancel(request, pk):
    mandate = get_object_or_404(Mandate, pk=pk, owner=request.user, kind=Mandate.INTENT, status=Mandate.ACTIVE)
    cancel_mandate(mandate, "Cancelled by the buyer. Funds released.")
    return JsonResponse({"ok": True, "side": _side_html(request)})


@login_required
@require_POST
def negotiate_start(request):
    data = _data(request)
    product = get_object_or_404(Product, pk=data.get("product_id"))
    try:
        quantity = max(1, min(int(data.get("quantity") or 1), 100))
    except (TypeError, ValueError):
        return JsonResponse({"error": "Quantity must be a whole number."}, status=400)
    limit = _money(data.get("max_unit_price"))
    if limit is None or limit <= 0:
        return JsonResponse({"error": "Set the most you will pay per unit (kept private from the store)."}, status=400)
    if limit * quantity > wallet_for(request.user).balance:
        return JsonResponse({"error": f"Your wallet cannot cover {quantity} x ${limit}. Top up first."}, status=400)
    negotiation, run = start_negotiation(request.user, product, quantity, limit)
    return _queued(run)


@login_required
def run_status(request, pk):
    run = get_object_or_404(AgentRun, pk=pk, user=request.user)
    negotiation = None
    if run.kind == "negotiate":
        negotiation = Negotiation.objects.filter(pk=run.payload.get("negotiation_id")).prefetch_related("messages").first()
    html = render_to_string("shopper/_run.html", {
        "run": run, "negotiation": negotiation, "beat": WorkerHeartbeat.get(),
        "queue_ahead": AgentRun.objects.filter(status__in=[AgentRun.QUEUED, AgentRun.RUNNING], priority__lte=run.priority,
                                               created__lt=run.created).count() if run.status == AgentRun.QUEUED else 0,
    }, request=request)
    # Keep polling while the agent works, and while a deal waits for the store's approval
    waiting = run.is_active or (negotiation is not None and negotiation.status == Negotiation.AWAITING_SELLER)
    return JsonResponse({"status": run.status, "html": html, "poll": waiting})


@login_required
@require_POST
def run_pay(request, pk):
    run = get_object_or_404(AgentRun, pk=pk, user=request.user, kind="plan", status=AgentRun.DONE)
    if not run.result.get("verified"):
        return JsonResponse({"error": "This cart did not pass verification, so the agent may not pay for it."}, status=400)
    try:
        order = pay_plan(run, request.user)
    except ValueError as err:
        return JsonResponse({"error": str(err)}, status=400)
    return JsonResponse({"ok": True, "order": order.number, "side": _side_html(request)})


@login_required
@require_POST
def negotiation_pay(request, pk):
    negotiation = get_object_or_404(Negotiation, pk=pk, buyer=request.user)
    try:
        order = pay_negotiation(negotiation, request.user)
    except ValueError as err:
        return JsonResponse({"error": str(err)}, status=400)
    return JsonResponse({"ok": True, "order": order.number, "side": _side_html(request)})


@login_required
@require_POST
def wallet_topup_view(request):
    wallet_topup(request.user, Decimal("50"), "Demo top-up")
    return JsonResponse({"ok": True, "side": _side_html(request)})


@login_required
def side(request):
    return JsonResponse({"side": _side_html(request)})
