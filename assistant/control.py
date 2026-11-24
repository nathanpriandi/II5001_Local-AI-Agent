"""
Agent Control Center (staff side).

One page shows what both agents are doing on their own: the worker status, the
seller policy mandate (the autonomy dial), an inbox of decisions that need a
human, purchase orders, buyer delegations, negotiations, the briefing and the
audit log. The page polls a partial every few seconds, so actions taken by the
background worker appear without a reload.
"""

from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from oscar.core.loading import get_model

from agentic import guard
from agentic.commerce import delegated_demand, log, stock_metrics
from agentic.models import AgentAction, AgentRun, Mandate, Negotiation, PurchaseOrder, WorkerHeartbeat
from agentic.negotiation import issue_voucher
from agentic.runs import DEFAULT_POLICY, current_policy, enqueue, enqueue_now, sign_policy

Product = get_model("catalogue", "Product")
StockRecord = get_model("partner", "StockRecord")

# Autonomy levels from Feng, McDonald and Zhang (2025), "Levels of Autonomy for AI Agents"
REORDER_LEVELS = {
    "suggest": ("L2 Collaborator", "The Store Agent drafts purchase orders; staff decide every one."),
    "ask": ("L4 Approver", "The Store Agent prepares purchase orders and waits for staff approval."),
    "auto": ("L5 Observer (within limit)", "The Store Agent places purchase orders up to the limit; staff watch the log."),
}
NEGOTIATION_LEVELS = {
    "off": ("L1 Operator", "No negotiation: buyers get the list price."),
    "ask": ("L4 Approver", "The Store Agent negotiates; staff approve each deal before a voucher is issued."),
    "auto": ("L5 Observer (within floor)", "The Store Agent negotiates and closes deals above the policy floor."),
}


def _policy_view(policy):
    if policy is None:
        return None
    terms = dict(DEFAULT_POLICY, **policy.terms)
    return {
        "mandate": policy, "terms": terms, "valid": guard.verify(policy),
        "reorder_level": REORDER_LEVELS.get(terms["reorder_mode"]),
        "negotiation_level": NEGOTIATION_LEVELS.get(terms["negotiation_mode"]),
    }


def _context():
    policy = current_policy()
    products = list(Product.objects.prefetch_related("stockrecords"))
    low = []
    for p in products:
        m = stock_metrics(p)
        demand = delegated_demand(p)
        if m["is_low"] or demand:
            m["product_id"], m["delegated"] = p.pk, demand
            m["open_po"] = p.purchase_orders.filter(status__in=[PurchaseOrder.AWAITING, PurchaseOrder.APPROVED]).first()
            low.append(m)
    delegations = list(Mandate.objects.filter(kind=Mandate.INTENT).select_related("owner", "product").order_by("-created")[:8])
    for d in delegations:
        d.valid = guard.verify(d)
    briefing = AgentRun.objects.filter(kind="briefing", status=AgentRun.DONE).order_by("-finished").first()
    beat = WorkerHeartbeat.get()
    return {
        "beat": beat,
        "policy": _policy_view(policy),
        "low": low,
        "inbox_pos": PurchaseOrder.objects.filter(status=PurchaseOrder.AWAITING).select_related("product"),
        "inbox_deals": Negotiation.objects.filter(status=Negotiation.AWAITING_SELLER).select_related("product", "buyer"),
        "on_the_way": PurchaseOrder.objects.filter(status=PurchaseOrder.APPROVED).select_related("product"),
        "recent_pos": PurchaseOrder.objects.exclude(status__in=[PurchaseOrder.AWAITING, PurchaseOrder.APPROVED])
                                          .select_related("product").order_by("-created")[:5],
        "delegations": delegations,
        "negotiations": Negotiation.objects.select_related("product", "buyer").prefetch_related("messages").order_by("-created")[:5],
        "briefing": briefing,
        "briefing_running": AgentRun.objects.filter(kind="briefing", status__in=[AgentRun.QUEUED, AgentRun.RUNNING]).exists(),
        "actions": AgentAction.objects.select_related("mandate").order_by("-created")[:30],
        "runs": AgentRun.objects.select_related("product").order_by("-created")[:10],
        "stats": {
            "autonomous_pos": PurchaseOrder.objects.filter(created_by="Store Agent", status__in=[PurchaseOrder.APPROVED, PurchaseOrder.RECEIVED],
                                                           approved_by__startswith="Store Agent").count(),
            "delegated_buys": Mandate.objects.filter(kind=Mandate.INTENT, status=Mandate.FULFILLED).count(),
            "deals": Negotiation.objects.filter(status__in=[Negotiation.DEAL, Negotiation.PAID]).count(),
            "blocked": AgentAction.objects.filter(level=AgentAction.BLOCKED).count(),
        },
        "now": timezone.now(),
    }


def control(request):
    return render(request, "assistant/control.html", _context())


def control_panel(request):
    """Partial re-rendered by the page every few seconds."""
    return render(request, "assistant/_control_panel.html", _context())


# ---------------------------------------------------------------------------
# Actions (plain form posts that redirect back to the Control Center)
# ---------------------------------------------------------------------------
@require_POST
def po_action(request, pk, action):
    po = get_object_or_404(PurchaseOrder, pk=pk)
    who = request.user.get_username()
    if action == "approve" and po.status == PurchaseOrder.AWAITING:
        po.status, po.approved_by, po.decided_at = PurchaseOrder.APPROVED, who, timezone.now()
        po.save()
        log("Staff", f"Approved {po.ref}", f"{po.quantity} x {po.product.title} = ${po.amount}", AgentAction.SUCCESS, user=request.user)
    elif action == "reject" and po.status == PurchaseOrder.AWAITING:
        po.status, po.decided_at = PurchaseOrder.REJECTED, timezone.now()
        po.save()
        log("Staff", f"Rejected {po.ref}", f"{po.quantity} x {po.product.title}", AgentAction.WARNING, user=request.user)
    elif action == "receive" and po.status == PurchaseOrder.APPROVED:
        # save() (not a queryset update) so the post_save signal wakes both agents
        sr = po.product.stockrecords.first()
        sr.num_in_stock = (sr.num_in_stock or 0) + po.quantity
        po.status, po.received_at = PurchaseOrder.RECEIVED, timezone.now()
        po.save()
        sr.save()
        log("Staff", f"Received {po.ref}", f"+{po.quantity} x {po.product.title}; stock is now {sr.num_in_stock - (sr.num_allocated or 0)}",
            AgentAction.SUCCESS, user=request.user)
        messages.success(request, f"{po.ref} received. Stock updated; the agents were notified.")
    else:
        messages.warning(request, f"{po.ref} is {po.status.replace('_', ' ')}; nothing changed.")
    return redirect("assistant-control")


@require_POST
def deal_action(request, pk, action):
    negotiation = get_object_or_404(Negotiation, pk=pk)
    if negotiation.status != Negotiation.AWAITING_SELLER:
        messages.warning(request, f"{negotiation.ref} is no longer waiting for approval.")
    elif action == "approve":
        issue_voucher(negotiation, approved_by=request.user.get_username())
        messages.success(request, f"{negotiation.ref} approved; the buyer can now pay the deal price.")
    elif action == "reject":
        negotiation.status = Negotiation.REJECTED_BY_SELLER
        negotiation.save()
        log("Staff", f"Rejected deal {negotiation.ref}", f"{negotiation.quantity} x {negotiation.product.title} at ${negotiation.deal_price}",
            AgentAction.WARNING, user=request.user)
    return redirect("assistant-control")


@require_POST
def start_run(request, kind):
    if kind == "briefing":
        enqueue_now("briefing", user=request.user)
        messages.info(request, "Briefing queued. It appears here when the model finishes.")
    elif kind == "scan":
        enqueue_now("scan", user=request.user)
        messages.info(request, "Stock scan queued.")
    elif kind == "stock_check":
        product = get_object_or_404(Product, pk=request.POST.get("product_id"))
        enqueue_now("stock_check", user=request.user, product=product)
        messages.info(request, f"Store Agent asked to check {product.title}.")
    return redirect("assistant-control")


@require_POST
def tamper(request, pk):
    """Demo of the signature check: change a delegation's terms without re-signing it."""
    mandate = get_object_or_404(Mandate, pk=pk, kind=Mandate.INTENT, status=Mandate.ACTIVE)
    terms = dict(mandate.terms)
    terms["max_unit_price"] = str(guard.money(Decimal(terms["max_unit_price"]) * 3))
    Mandate.objects.filter(pk=mandate.pk).update(terms=terms)
    log("Demo", f"Edited {mandate.ref} without signing", f"max_unit_price changed to ${terms['max_unit_price']}",
        AgentAction.WARNING, mandate=mandate, user=request.user)
    enqueue("fulfil_delegations", product=mandate.product, dedupe=True)
    messages.warning(request, f"{mandate.ref} edited behind the buyer's back. Watch the Shopping Agent refuse it.")
    return redirect("assistant-control")


# ---------------------------------------------------------------------------
# Seller policy (the autonomy dial), signed as a mandate
# ---------------------------------------------------------------------------
def _number(value, default, low, high, cast=float):
    try:
        return max(low, min(high, cast(value)))
    except (TypeError, ValueError, InvalidOperation):
        return default


def policy(request):
    current = current_policy()
    if request.method == "POST":
        p = request.POST
        terms = {
            "reorder_mode": p.get("reorder_mode") if p.get("reorder_mode") in REORDER_LEVELS else "ask",
            "reorder_auto_limit": _number(p.get("reorder_auto_limit"), 150, 0, 5000, int),
            "negotiation_mode": p.get("negotiation_mode") if p.get("negotiation_mode") in NEGOTIATION_LEVELS else "ask",
            "max_discount_pct": _number(p.get("max_discount_pct"), 10, 0, 30, int),
            "min_qty_for_discount": _number(p.get("min_qty_for_discount"), 5, 1, 100, int),
            "min_days_cover_after_sale": _number(p.get("min_days_cover_after_sale"), 14, 0, 120, int),
            "max_rounds": _number(p.get("max_rounds"), 3, 1, 5, int),
        }
        mandate = sign_policy(request.user, terms)
        log("Staff", f"Signed seller policy {mandate.ref}",
            f"reorder {terms['reorder_mode']} (auto limit ${terms['reorder_auto_limit']}), negotiation {terms['negotiation_mode']} "
            f"(max {terms['max_discount_pct']}% off from {terms['min_qty_for_discount']} units)",
            AgentAction.SUCCESS, mandate=mandate, user=request.user)
        messages.success(request, f"Policy {mandate.ref} signed. Both agents use it from their next action.")
        return redirect("assistant-policy")
    history = Mandate.objects.filter(kind=Mandate.POLICY).select_related("owner").order_by("-created")[:8]
    for m in history:
        m.valid = guard.verify(m)
    return render(request, "assistant/policy.html", {
        "policy": _policy_view(current), "history": history,
        "reorder_levels": REORDER_LEVELS, "negotiation_levels": NEGOTIATION_LEVELS,
    })
