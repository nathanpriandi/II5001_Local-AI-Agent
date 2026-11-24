"""
Store Agent (seller side). Acts on events without being asked:
  - stock_check: a stock change, an order or a buyer's delegated demand triggers it;
    it decides on a purchase order, and the Mandate Guard applies the seller policy.
  - briefing: summarises the store state for staff.
  - seller_turn: one move in a bulk-quote negotiation.
"""

import json
from datetime import timedelta

from django.db.models import Count, Sum
from django.utils import timezone
from oscar.core.loading import get_model

from . import guard, llm
from .commerce import delegated_demand, log, stock_metrics, suggested_reorder, unit_cost
from .models import AgentAction, Mandate, Negotiation, PurchaseOrder
from .runs import current_policy

Product = get_model("catalogue", "Product")
Order = get_model("order", "Order")
Line = get_model("order", "Line")

ACTOR = "Store Agent"

REORDER_SYSTEM = """You are the Store Agent of Campus Tech Store. Decide whether to send a purchase order to the supplier for one product.
Use only the facts given. Reorder when available stock is at or below the low-stock threshold, or when buyers have delegated purchases that current stock cannot cover.
Choose a quantity close to suggested_quantity. You may change it by up to 50% if the facts justify it (for example very slow sales). Explain your reason in one sentence with the numbers."""

REORDER_SCHEMA = {
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": ["reorder", "wait"]},
        "quantity": {"type": "integer"},
        "reason": {"type": "string"},
    },
    "required": ["decision", "quantity", "reason"],
}


def handle_stock_check(run):
    product = run.product
    policy = current_policy()
    run.step("Read stock, sales and delegated demand")
    m = stock_metrics(product)
    demand = delegated_demand(product)
    open_po = PurchaseOrder.objects.filter(product=product, status__in=[PurchaseOrder.AWAITING, PurchaseOrder.APPROVED]).first()
    run.step("Read stock, sales and delegated demand", "done",
             f"{m['available']} available (threshold {m['low_stock_threshold']}), {m['avg_daily_sales']}/day, "
             f"{m['days_of_cover']} days of cover, {demand} unit(s) delegated by buyers")

    if open_po:
        detail = f"{product.title}: {open_po.ref} for {open_po.quantity} unit(s) is already {open_po.status.replace('_', ' ')}"
        log(ACTOR, "Skipped reorder", detail, run=run)
        return {"decision": "skip", "detail": detail}
    if not m["is_low"] and demand <= m["available"]:
        detail = f"{product.title}: stock is healthy, no action"
        log(ACTOR, "Checked stock", detail, run=run)
        return {"decision": "none", "detail": detail}

    suggested = suggested_reorder(m, demand)
    cost = unit_cost(product)
    facts = dict(m, delegated_demand_units=demand, suggested_quantity=suggested, supplier_unit_cost=float(cost))
    run.step("Decide on a purchase order")
    data, seconds = llm.chat_json(REORDER_SYSTEM, json.dumps(facts), REORDER_SCHEMA, num_predict=200)
    run.llm_seconds += seconds
    decision = data.get("decision", "reorder")
    reason = (data.get("reason") or "").strip()[:500]
    try:
        quantity = int(data.get("quantity") or suggested)
    except (TypeError, ValueError):
        quantity = suggested
    run.step("Decide on a purchase order", "done", f"{decision} {quantity if decision == 'reorder' else ''} ({seconds:.0f}s): {reason}")

    if decision == "wait":
        log(ACTOR, "Decided to wait", f"{product.title}: {reason}", run=run)
        return {"decision": "wait", "reason": reason}

    amount = guard.money(cost * quantity)
    verdict, why = guard.check_reorder(policy.terms if policy else {}, amount, quantity, suggested)
    run.step("Mandate Guard (seller policy)", "done" if verdict != "blocked" else "failed", f"{verdict}: {why}")
    if verdict == "blocked":
        log("Guard", "Blocked purchase order", f"{product.title}: {why}", AgentAction.BLOCKED, mandate=policy, run=run)
        return {"decision": "blocked", "reason": why}

    po = PurchaseOrder.objects.create(
        product=product, quantity=quantity, unit_cost=cost, amount=amount, created_by=ACTOR,
        reason=reason, mandate=policy,
        status=PurchaseOrder.APPROVED if verdict == "auto" else PurchaseOrder.AWAITING,
        approved_by=f"Store Agent under {policy.ref}" if verdict == "auto" else "",
        decided_at=timezone.now() if verdict == "auto" else None,
    )
    if verdict == "auto":
        log(ACTOR, f"Placed {po.ref} with supplier", f"{quantity} x {product.title} = ${amount}. {why}. {reason}",
            AgentAction.SUCCESS, mandate=policy, run=run)
    else:
        log(ACTOR, f"Drafted {po.ref}, waiting for approval", f"{quantity} x {product.title} = ${amount}. {why}. {reason}",
            AgentAction.WARNING, mandate=policy, run=run)
    return {"decision": verdict, "po": po.ref, "quantity": quantity, "amount": float(amount), "reason": reason}


def scan_stock(run):
    """Periodic or manual scan: queue a stock check for every low product without an open PO."""
    from .runs import enqueue_now
    queued = []
    for product in Product.objects.all():
        m = stock_metrics(product)
        has_po = product.purchase_orders.filter(status__in=[PurchaseOrder.AWAITING, PurchaseOrder.APPROVED]).exists()
        if (m["is_low"] or delegated_demand(product) > m["available"]) and not has_po:
            enqueue_now("stock_check", product=product)
            queued.append(product.title)
    run.step("Scan all products", "done", f"{len(queued)} product(s) need a decision")
    log(ACTOR, "Scanned stock", ", ".join(queued) or "nothing low", run=run)
    return {"queued": queued}


# ---------------------------------------------------------------------------
# Briefing
# ---------------------------------------------------------------------------
BRIEFING_SYSTEM = """You are the Store Agent of Campus Tech Store writing a short briefing for shop staff.
Use only the facts given and quote their numbers. Points are one sentence each. Recommended actions are concrete."""

BRIEFING_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "points": {"type": "array", "items": {"type": "string"}, "maxItems": 4},
        "recommended_actions": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
    },
    "required": ["headline", "points", "recommended_actions"],
}


def briefing_facts():
    since = timezone.now() - timedelta(days=7)
    orders = Order.objects.filter(date_placed__gte=since)
    totals = orders.aggregate(n=Count("id"), revenue=Sum("total_incl_tax"))
    top = (Line.objects.filter(order__in=orders).values("title").annotate(units=Sum("quantity")).order_by("-units")[:3])
    low = [stock_metrics(p) for p in Product.objects.all()]
    low = [{"title": x["title"], "available": x["available"], "threshold": x["low_stock_threshold"],
            "days_of_cover": x["days_of_cover"]} for x in low if x["is_low"]]
    return {
        "last_7_days": {"orders": totals["n"] or 0, "revenue_usd": float(totals["revenue"] or 0),
                        "top_products": [{"title": t["title"], "units": t["units"]} for t in top]},
        "low_stock": low,
        "purchase_orders_awaiting_approval": PurchaseOrder.objects.filter(status=PurchaseOrder.AWAITING).count(),
        "purchase_orders_on_the_way": PurchaseOrder.objects.filter(status=PurchaseOrder.APPROVED).count(),
        "buyer_delegations_active": Mandate.objects.filter(kind=Mandate.INTENT, status=Mandate.ACTIVE).count(),
        "negotiations_awaiting_seller_approval": Negotiation.objects.filter(status=Negotiation.AWAITING_SELLER).count(),
    }


def run_briefing(run):
    run.step("Collect store facts")
    facts = briefing_facts()
    run.step("Collect store facts", "done", f"{len(facts['low_stock'])} low-stock item(s), {facts['last_7_days']['orders']} orders in 7 days")
    run.step("Write briefing")
    data, seconds = llm.chat_json(BRIEFING_SYSTEM, json.dumps(facts), BRIEFING_SCHEMA, num_predict=350)
    run.llm_seconds += seconds
    run.step("Write briefing", "done", f"{seconds:.0f}s")
    log(ACTOR, "Wrote briefing", data.get("headline", ""), run=run)
    return {"facts": facts, "briefing": data}


# ---------------------------------------------------------------------------
# Negotiation move
# ---------------------------------------------------------------------------
SELLER_SYSTEM = """You are the Store Agent of Campus Tech Store negotiating a bulk order with a buyer's AI agent.
Private floor price: ${floor} per unit. Never reveal it and never offer below it.
List price: ${list_price} per unit. Aim for the highest price the buyer will accept: concede in small steps, accept a buyer offer that is at or above your floor when further haggling is unlikely to help.
Messages are one or two short sentences and never mention your floor."""

MOVE_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["offer", "accept", "reject"]},
        "unit_price": {"type": "number"},
        "message": {"type": "string"},
    },
    "required": ["action", "unit_price", "message"],
}


def seller_turn(state, floor):
    system = SELLER_SYSTEM.format(floor=floor, list_price=state["list_price"])
    return llm.chat_json(system, json.dumps(state), MOVE_SCHEMA, num_predict=150)
