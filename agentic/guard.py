"""
Mandate Guard: deterministic checks that run before any agent action has an effect.

Design rule: limits that protect money (budgets, price floors, spending caps) are
never decided by the language model. Zhu et al. (2025) found small models in
buyer/seller negotiations accept deals above budget and reveal their budget;
Microsoft's Magentic Marketplace (2025) found Qwen3-4B open to manipulation by
seller-written text. The guard closes those gaps in code.
"""

import hashlib
import hmac
import json
import re
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.utils import timezone

CENT = Decimal("0.01")


def money(value):
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------
# Mandate signatures (simplified version of AP2's signed mandates)
# ---------------------------------------------------------------------------
def _payload(mandate):
    return json.dumps({
        "kind": mandate.kind, "side": mandate.side, "owner": mandate.owner_id,
        "product": mandate.product_id, "terms": mandate.terms,
        "expires": mandate.expires_at.isoformat() if mandate.expires_at else None,
    }, sort_keys=True, default=str)


def sign(mandate):
    """HMAC-SHA256 over the mandate's content, keyed with the project secret."""
    return hmac.new(settings.SECRET_KEY.encode(), _payload(mandate).encode(), hashlib.sha256).hexdigest()


def verify(mandate):
    return bool(mandate.signature) and hmac.compare_digest(mandate.signature, sign(mandate))


class Verdict:
    def __init__(self, ok, reason=""):
        self.ok, self.reason = ok, reason

    def __bool__(self):
        return self.ok


def mandate_usable(mandate):
    if not verify(mandate):
        return Verdict(False, f"{mandate.ref} signature does not match its terms (edited after signing)")
    if mandate.status != mandate.ACTIVE:
        return Verdict(False, f"{mandate.ref} is {mandate.status}")
    if mandate.is_expired:
        return Verdict(False, f"{mandate.ref} expired on {timezone.localtime(mandate.expires_at):%Y-%m-%d %H:%M}")
    return Verdict(True)


# ---------------------------------------------------------------------------
# Buyer-side checks
# ---------------------------------------------------------------------------
def check_delegated_purchase(mandate, unit_price, available, held):
    """Can the Shopping Agent buy now under this intent mandate (buyer not present)?"""
    usable = mandate_usable(mandate)
    if not usable:
        return usable
    terms = mandate.terms
    qty, max_price = int(terms["quantity"]), money(terms["max_unit_price"])
    if money(unit_price) > max_price:
        return Verdict(False, f"price ${money(unit_price)} is above the mandate limit ${max_price}")
    if available < qty:
        return Verdict(False, f"only {available} in stock, mandate needs {qty}")
    if money(unit_price) * qty > held:
        return Verdict(False, f"held funds ${held} do not cover ${money(unit_price) * qty}")
    return Verdict(True, f"${money(unit_price)} <= ${max_price}, {qty} in stock, funds held")


def check_cart_payment(total, budget, wallet_balance):
    if budget is not None and money(total) > money(budget):
        return Verdict(False, f"cart total ${money(total)} exceeds budget ${money(budget)}")
    if money(total) > wallet_balance:
        return Verdict(False, f"wallet balance ${wallet_balance} is below ${money(total)}")
    return Verdict(True)


# ---------------------------------------------------------------------------
# Seller-side checks
# ---------------------------------------------------------------------------
def seller_floor(policy_terms, list_price, quantity, available, avg_daily):
    """Lowest unit price the Store Agent may accept, and why."""
    list_price = money(list_price)
    if policy_terms.get("negotiation_mode") == "off":
        return list_price, "negotiation disabled by policy: list price only"
    if quantity > available:
        return None, f"only {available} in stock, cannot sell {quantity}"
    if quantity < int(policy_terms.get("min_qty_for_discount", 5)):
        return list_price, f"discounts start at {policy_terms.get('min_qty_for_discount')} units"
    cover_after = (available - quantity) / avg_daily if avg_daily > 0 else 999
    min_cover = float(policy_terms.get("min_days_cover_after_sale", 14))
    if cover_after < min_cover:
        return list_price, f"stock after sale covers {cover_after:.0f} days (< {min_cover:.0f}), no discount on scarce items"
    pct = Decimal(str(policy_terms.get("max_discount_pct", 10)))
    floor = money(list_price * (1 - pct / 100))
    return floor, f"max {pct}% discount, stock after sale covers {min(cover_after, 999):.0f} days"


def check_reorder(policy_terms, amount, quantity, suggested):
    """Decide whether a Store Agent purchase order is auto-approved, needs approval or is blocked."""
    mode = policy_terms.get("reorder_mode", "ask")
    limit = money(policy_terms.get("reorder_auto_limit", 0))
    if quantity <= 0:
        return "blocked", "quantity must be positive"
    if suggested and quantity > 3 * suggested:
        return "blocked", f"quantity {quantity} is more than 3x the computed need ({suggested})"
    if mode == "suggest":
        return "suggest", "policy: Store Agent may only suggest purchase orders"
    if mode == "auto" and money(amount) <= limit:
        return "auto", f"${money(amount)} <= auto-approve limit ${limit}"
    if mode == "auto":
        return "ask", f"${money(amount)} > auto-approve limit ${limit}, needs staff approval"
    return "ask", "policy: every purchase order needs staff approval"


# ---------------------------------------------------------------------------
# Negotiation guard
# ---------------------------------------------------------------------------
def guard_offer(side, action, price, limit, own_last, other_last):
    """
    Correct one negotiation move so it stays inside the mandate.
      buyer:  never above `limit` (private max), never below its own last offer
      seller: never below `limit` (private floor), never above its own last offer
    Returns (action, price, note). Note is empty when nothing was changed.
    """
    notes = []
    try:
        price = money(price) if price is not None else None
    except Exception:  # noqa: BLE001  non-numeric price from the model
        price = None
    if action == "accept":
        if other_last is None:
            action, notes = "offer", ["nothing to accept yet, turned into an offer"]
        elif (side == "buyer" and other_last > limit) or (side == "seller" and other_last < limit):
            notes.append(f"BLOCKED accept at ${other_last}: outside mandate")
            action, price = "offer", limit
        else:
            return "accept", other_last, ""
    if action == "reject":
        return "reject", None, ""
    if price is None:
        price = own_last if own_last is not None else limit
        notes.append("missing price, kept previous offer")
    if side == "buyer":
        if price > limit:
            notes.append(f"BLOCKED ${price}: above buyer limit, capped")
            price = limit
        if own_last is not None and price < own_last:
            price = own_last
            notes.append("offers may not go backwards")
    else:
        if price < limit:
            notes.append(f"BLOCKED ${price}: below seller floor, raised")
            price = limit
        if own_last is not None and price > own_last:
            price = own_last
            notes.append("offers may not go backwards")
    return "offer", price, "; ".join(notes)


def redact_secret(message, secrets):
    """Remove a private limit from a negotiation message (agents must not reveal it)."""
    redacted = False
    for value in secrets:
        if value is None:
            continue
        for text in {f"{money(value)}", f"{money(value):.2f}".rstrip("0").rstrip(".")}:
            # Whole number only: "26" must not match inside "126" or "26.5"
            pattern = r"\$?(?<![\d.])" + re.escape(text) + r"(?!\.?\d)"
            if re.search(pattern, message):
                message = re.sub(pattern, "[private]", message)
                redacted = True
    return message, redacted


# ---------------------------------------------------------------------------
# Untrusted text (seller-written product descriptions)
# ---------------------------------------------------------------------------
INJECTION = re.compile(
    r"(ignore (all|any|previous|the above)|system (note|prompt|message)|you must|as an ai|"
    r"ai (agent|assistant)s?|instruction|always add|add \d+ (units|of)|override)", re.I)


def sanitize_untrusted(text, limit=160):
    """Drop sentences that try to instruct an agent. Returns (clean_text, flagged)."""
    sentences = re.split(r"(?<=[.!?])\s+", text or "")
    kept = [s for s in sentences if not INJECTION.search(s)]
    flagged = len(kept) != len(sentences)
    clean = " ".join(kept)
    return (clean[:limit] + ("..." if len(clean) > limit else "")), flagged
