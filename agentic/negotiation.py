"""
Agent-to-agent bulk-quote negotiation.

The buyer's Shopping Agent and the seller's Store Agent alternate moves. Each move
is a structured model call; the Mandate Guard then corrects any move outside the
owner's limits and redacts private limits from the message, as recommended by the
failure modes in Zhu et al. (2025). Deals become single-use Oscar vouchers.
"""

from decimal import Decimal

from django.db import transaction
from oscar.core.loading import get_model

from . import guard
from .commerce import (available, checkout_with_wallet, create_deal_voucher, log, quote, stock_metrics,
                       stockrecord, wallet_for)
from .models import AgentAction, Mandate, Negotiation, NegotiationMessage
from .runs import current_policy, enqueue_now
from .shopping_agent import buyer_turn
from .store_agent import seller_turn

Voucher = get_model("voucher", "Voucher")


@transaction.atomic
def start_negotiation(user, product, quantity, max_unit_price):
    policy = current_policy()
    sr = stockrecord(product)
    metrics = stock_metrics(product)
    floor, reason = guard.seller_floor(policy.terms, sr.price, int(quantity), available(sr), metrics["avg_daily_sales"])
    buyer_mandate = Mandate(kind=Mandate.NEGOTIATION, side="buyer", owner=user, product=product,
                            terms={"quantity": int(quantity), "max_unit_price": str(guard.money(max_unit_price))})
    buyer_mandate.signature = guard.sign(buyer_mandate)
    buyer_mandate.save()
    negotiation = Negotiation.objects.create(
        buyer=user, product=product, quantity=int(quantity), list_price=sr.price,
        buyer_mandate=buyer_mandate, seller_mandate=policy, floor_price=floor, floor_reason=reason)
    log("Buyer", f"Requested bulk quote {negotiation.ref}",
        f"{quantity} x {product.title}; buyer limit is private ({buyer_mandate.ref})", mandate=buyer_mandate, user=user)
    run = enqueue_now("negotiate", user=user, product=product, payload={"negotiation_id": negotiation.pk})
    return negotiation, run


def _say(negotiation, side, action, price, proposed, message, note, seconds):
    return NegotiationMessage.objects.create(
        negotiation=negotiation, side=side, action=action, unit_price=price, proposed_price=proposed,
        message=message[:500], guard_note=note[:255], seconds=seconds)


def _proposed(data):
    try:
        return guard.money(data.get("unit_price"))
    except Exception:  # noqa: BLE001
        return None


def run_negotiation(run):
    negotiation = Negotiation.objects.get(pk=run.payload["negotiation_id"])
    product, qty = negotiation.product, negotiation.quantity
    buyer_m, seller_m = negotiation.buyer_mandate, negotiation.seller_mandate
    run.step("Check both mandates")
    for m in (buyer_m, seller_m):
        if not guard.verify(m):
            run.step("Check both mandates", "failed", f"{m.ref} signature invalid")
            negotiation.status = Negotiation.NO_DEAL
            negotiation.save()
            log("Guard", f"Stopped {negotiation.ref}", f"{m.ref} was edited after signing", AgentAction.BLOCKED, mandate=m, run=run)
            return {"status": negotiation.status}
    limit, floor = Decimal(buyer_m.terms["max_unit_price"]), negotiation.floor_price
    max_rounds = int(seller_m.terms.get("max_rounds", 3))
    run.step("Check both mandates", "done", f"buyer {buyer_m.ref} and seller policy {seller_m.ref} verified; max {max_rounds} rounds")

    if floor is None:
        _say(negotiation, "seller", "reject", None, None, f"Sorry, we cannot supply {qty} units right now.", negotiation.floor_reason, 0)
        negotiation.status = Negotiation.NO_DEAL
        negotiation.save()
        log("Store Agent", f"Declined {negotiation.ref}", negotiation.floor_reason, AgentAction.WARNING, run=run)
        return {"status": negotiation.status}

    base = {"product": product.title, "quantity": qty, "list_price": float(negotiation.list_price), "max_rounds": max_rounds}
    transcript = []
    buyer_last = seller_last = None
    deal_price, outcome = None, "no deal: maximum rounds reached"

    for rnd in range(1, max_rounds + 1):
        # ---- buyer move
        run.step(f"Round {rnd}: Shopping Agent")
        data, secs = buyer_turn(dict(base, round=rnd, your_last_offer=_f(buyer_last), seller_last_offer=_f(seller_last),
                                     transcript=transcript[-4:]), limit)
        run.llm_seconds += secs
        action, price, note = guard.guard_offer("buyer", data.get("action", "offer"), data.get("unit_price"), limit, buyer_last, seller_last)
        message, redacted = guard.redact_secret(data.get("message", ""), [limit])
        if redacted:
            note = "; ".join(filter(None, [note, "redacted private limit from message"]))
        _say(negotiation, "buyer", action, price, _proposed(data), message, note, secs)
        if note:
            log("Guard", f"Corrected Shopping Agent in {negotiation.ref}", note, AgentAction.BLOCKED, mandate=buyer_m, run=run)
        transcript.append({"from": "buyer", "action": action, "unit_price": _f(price), "message": message})
        run.step(f"Round {rnd}: Shopping Agent", "done", f"{action} {'$' + str(price) if price else ''} ({secs:.0f}s)")
        if action == "accept":
            deal_price, outcome = seller_last, "buyer accepted the seller's offer"
            break
        if action == "reject":
            outcome = "buyer walked away"
            break
        buyer_last = price
        if seller_last is not None and buyer_last >= seller_last:
            deal_price, outcome = seller_last, "offers met"
            break

        # ---- seller move
        run.step(f"Round {rnd}: Store Agent")
        data, secs = seller_turn(dict(base, round=rnd, your_last_offer=_f(seller_last), buyer_last_offer=_f(buyer_last),
                                      transcript=transcript[-4:]), floor)
        run.llm_seconds += secs
        action, price, note = guard.guard_offer("seller", data.get("action", "offer"), data.get("unit_price"), floor, seller_last, buyer_last)
        message, redacted = guard.redact_secret(data.get("message", ""), [floor])
        if redacted:
            note = "; ".join(filter(None, [note, "redacted private floor from message"]))
        _say(negotiation, "seller", action, price, _proposed(data), message, note, secs)
        if note:
            log("Guard", f"Corrected Store Agent in {negotiation.ref}", note, AgentAction.BLOCKED, mandate=seller_m, run=run)
        transcript.append({"from": "seller", "action": action, "unit_price": _f(price), "message": message})
        run.step(f"Round {rnd}: Store Agent", "done", f"{action} {'$' + str(price) if price else ''} ({secs:.0f}s)")
        if action == "accept":
            deal_price, outcome = buyer_last, "seller accepted the buyer's offer"
            break
        if action == "reject":
            outcome = "seller ended the negotiation"
            break
        seller_last = price
        if seller_last <= buyer_last:
            deal_price, outcome = buyer_last, "offers met"
            break
    else:
        # Closing rule: after the last round the buyer takes the seller's standing offer if its mandate allows it
        if seller_last is not None and seller_last <= limit:
            deal_price, outcome = seller_last, "closing rule: seller's final offer is within the buyer mandate"
            _say(negotiation, "buyer", "accept", seller_last, None, "We accept your final offer.",
                 "closing rule applied by the guard", 0)

    run.step("Close", "done", outcome)
    if deal_price is None:
        negotiation.status = Negotiation.NO_DEAL
        negotiation.save()
        log("Store Agent", f"No deal in {negotiation.ref}", outcome, run=run)
        return {"status": negotiation.status, "outcome": outcome}

    negotiation.deal_price = deal_price
    if seller_m.terms.get("negotiation_mode") == "ask":
        negotiation.status = Negotiation.AWAITING_SELLER
        negotiation.save()
        log("Store Agent", f"Deal {negotiation.ref} needs staff approval",
            f"{qty} x {product.title} at ${deal_price} (list ${negotiation.list_price})", AgentAction.WARNING, mandate=seller_m, run=run)
    else:
        issue_voucher(negotiation, approved_by=f"Store Agent under {seller_m.ref}")
    return {"status": negotiation.status, "deal_price": float(deal_price), "outcome": outcome}


def issue_voucher(negotiation, approved_by):
    voucher = create_deal_voucher(negotiation)
    negotiation.voucher_code, negotiation.status = voucher.code, Negotiation.DEAL
    negotiation.save()
    saving = (negotiation.list_price - negotiation.deal_price) * negotiation.quantity
    log("Store Agent", f"Deal {negotiation.ref} agreed",
        f"{negotiation.quantity} x {negotiation.product.title} at ${negotiation.deal_price} (saves ${saving:.2f}); "
        f"single-use voucher {voucher.code}; approved by {approved_by}", AgentAction.SUCCESS, mandate=negotiation.seller_mandate)


def pay_negotiation(negotiation, user):
    if negotiation.status != Negotiation.DEAL:
        raise ValueError("There is no open deal to pay for.")
    voucher = Voucher.objects.get(code=negotiation.voucher_code)
    lines = [(negotiation.product, negotiation.quantity)]
    total = quote(user, lines, voucher)
    limit_total = Decimal(negotiation.buyer_mandate.terms["max_unit_price"]) * negotiation.quantity
    verdict = guard.check_cart_payment(total, limit_total, wallet_for(user).balance)
    if not verdict:
        log("Guard", "Blocked deal payment", verdict.reason, AgentAction.BLOCKED, user=user)
        raise ValueError(verdict.reason)
    order, amount = checkout_with_wallet(user, lines, voucher=voucher, note=f"(deal {negotiation.ref})")
    negotiation.order_number, negotiation.status = order.number, Negotiation.PAID
    negotiation.save()
    log("Buyer", f"Paid deal {negotiation.ref}", f"${amount} from Campus Wallet, order {order.number}",
        AgentAction.SUCCESS, user=user)
    return order


def _f(value):
    return float(value) if value is not None else None
