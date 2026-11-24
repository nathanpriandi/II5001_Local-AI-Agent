"""
Store operations used by the agents: Campus Wallet, stock metrics, product specs,
paid checkout through Oscar's own order pipeline, and negotiated-deal vouchers.
"""

import math
import secrets
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from oscar.apps.checkout.calculators import OrderTotalCalculator
from oscar.apps.offer.applicator import Applicator
from oscar.apps.order.utils import OrderCreator, OrderNumberGenerator
from oscar.apps.partner.strategy import Selector
from oscar.apps.shipping.methods import Free
from oscar.core.loading import get_model

from .guard import money
from .models import AgentAction, Mandate, SupplierPrice, Wallet, WalletEntry

Product = get_model("catalogue", "Product")
StockRecord = get_model("partner", "StockRecord")
Line = get_model("order", "Line")
Basket = get_model("basket", "Basket")
SourceType = get_model("payment", "SourceType")
Source = get_model("payment", "Source")
Range = get_model("offer", "Range")
Condition = get_model("offer", "Condition")
Benefit = get_model("offer", "Benefit")
ConditionalOffer = get_model("offer", "ConditionalOffer")
Voucher = get_model("voucher", "Voucher")


def log(actor, verb, detail="", level=AgentAction.INFO, mandate=None, run=None, user=None):
    return AgentAction.objects.create(actor=actor, verb=verb, detail=detail, level=level,
                                      mandate=mandate, run=run, user=user)


# ---------------------------------------------------------------------------
# Campus Wallet (simulated stored value; no real payment provider)
# ---------------------------------------------------------------------------
def wallet_for(user):
    wallet, _ = Wallet.objects.get_or_create(user=user)
    return wallet


def _entry(wallet, kind, amount, note):
    WalletEntry.objects.create(wallet=wallet, kind=kind, amount=money(amount), note=note[:255])


@transaction.atomic
def wallet_topup(user, amount, note="Demo top-up"):
    wallet = wallet_for(user)
    wallet.balance += money(amount)
    wallet.save()
    _entry(wallet, "top-up", amount, note)
    return wallet


@transaction.atomic
def wallet_hold(user, amount, note):
    """Reserve funds for a delegated purchase (like a payment token scoped to one mandate)."""
    wallet = wallet_for(user)
    amount = money(amount)
    if amount > wallet.balance:
        raise ValueError(f"Wallet balance ${wallet.balance} is below ${amount}")
    wallet.balance -= amount
    wallet.held += amount
    wallet.save()
    _entry(wallet, "hold", -amount, note)
    return wallet


@transaction.atomic
def wallet_release(user, amount, note):
    wallet = wallet_for(user)
    amount = min(money(amount), wallet.held)
    if amount > 0:
        wallet.held -= amount
        wallet.balance += amount
        wallet.save()
        _entry(wallet, "release", amount, note)
    return wallet


# ---------------------------------------------------------------------------
# Catalogue facts
# ---------------------------------------------------------------------------
def stockrecord(product):
    return product.stockrecords.first()


def available(sr):
    return (sr.num_in_stock or 0) - (sr.num_allocated or 0) if sr else 0


def stock_metrics(product, days=30):
    """Stock and sales facts computed in Python (the model never does this arithmetic)."""
    sr = stockrecord(product)
    since = timezone.now() - timedelta(days=days)
    units = Line.objects.filter(product=product, order__date_placed__gte=since).aggregate(u=Sum("quantity"))["u"] or 0
    avg_daily = units / days
    avail = available(sr)
    threshold = sr.low_stock_threshold if sr and sr.low_stock_threshold is not None else 5
    cover = avail / avg_daily if avg_daily > 0 else 999
    return {
        "product_id": product.id, "title": product.title,
        "price": float(sr.price) if sr else None,
        "available": avail, "low_stock_threshold": threshold,
        "units_sold_30d": units, "avg_daily_sales": round(avg_daily, 2),
        "days_of_cover": round(cover, 1) if cover < 999 else 999,
        "is_low": avail <= threshold,
    }


def delegated_demand(product):
    """Units buyers have delegated to their Shopping Agents for this product."""
    total = 0
    for m in Mandate.objects.filter(kind=Mandate.INTENT, status=Mandate.ACTIVE, product=product):
        total += int(m.terms.get("quantity", 0))
    return total


def suggested_reorder(metrics, demand):
    """Cover 30 days of sales plus delegated demand, back above the low-stock threshold."""
    need = math.ceil(metrics["avg_daily_sales"] * 30) + demand + metrics["low_stock_threshold"] - metrics["available"]
    return max(need, 5)


def unit_cost(product):
    try:
        return product.supplier_price.unit_cost
    except SupplierPrice.DoesNotExist:
        sr = stockrecord(product)
        return money(sr.price * Decimal("0.6")) if sr else Decimal("0")


def specs(product):
    """Technical attributes stored as Oscar product attributes (shown on the product page too)."""
    out = {}
    for value in product.attribute_values.select_related("attribute"):
        out[value.attribute.code] = value.value
    return out


def owned_products(user):
    """Products this buyer already bought (memory from Oscar's order history)."""
    ids = Line.objects.filter(order__user=user).values_list("product_id", flat=True).distinct()
    return list(Product.objects.filter(id__in=ids))


# ---------------------------------------------------------------------------
# Paid checkout through Oscar's order pipeline
# ---------------------------------------------------------------------------
def build_basket(user, lines, voucher=None):
    """A separate 'saved' basket so it never mixes with the buyer's own open basket."""
    basket = Basket.objects.create(owner=user, status=Basket.SAVED)
    basket.strategy = Selector().strategy(user=user)
    for product, qty in lines:
        basket.add_product(product, qty)
    if voucher is not None:
        basket.vouchers.add(voucher)
    Applicator().apply(basket, user)
    shipping = Free()
    charge = shipping.calculate(basket)
    total = OrderTotalCalculator().calculate(basket, charge)
    return basket, shipping, charge, total


def quote(user, lines, voucher=None):
    """Price a cart without placing an order."""
    with transaction.atomic():
        basket, _, _, total = build_basket(user, lines, voucher)
        amount = money(total.incl_tax)
        transaction.set_rollback(True)
    return amount


@transaction.atomic
def checkout_with_wallet(user, lines, voucher=None, held_amount=Decimal("0"), note="", actor="Shopping Agent"):
    """
    Pay first, then place the order: debit the Campus Wallet (held funds first),
    then create the order with Oscar's OrderCreator and record the payment source.
    """
    for product, qty in lines:
        if available(stockrecord(product)) < qty:
            raise ValueError(f"{product.title}: only {available(stockrecord(product))} in stock")
    basket, shipping, charge, total = build_basket(user, lines, voucher)
    amount = money(total.incl_tax)
    wallet = Wallet.objects.get(user=user)
    from_hold = min(money(held_amount), wallet.held, amount)
    from_balance = amount - from_hold
    if from_balance > wallet.balance:
        raise ValueError(f"Wallet balance ${wallet.balance} is below ${from_balance}")
    wallet.held -= from_hold
    wallet.balance -= from_balance
    wallet.save()
    number = OrderNumberGenerator().order_number(basket)
    order = OrderCreator().place_order(
        basket=basket, total=total, shipping_method=shipping, shipping_charge=charge,
        user=user, order_number=number, status="Paid",
    )
    source_type, _ = SourceType.objects.get_or_create(name="Campus Wallet")
    Source.objects.create(order=order, source_type=source_type, currency=order.currency,
                          amount_allocated=amount, amount_debited=amount, reference=f"wallet-{wallet.pk}")
    _entry(wallet, "charge", -amount, f"Order {order.number} {note}".strip())
    basket.submit()
    return order, amount


# ---------------------------------------------------------------------------
# Negotiated deal -> single-use Oscar voucher
# ---------------------------------------------------------------------------
@transaction.atomic
def create_deal_voucher(negotiation):
    product, qty = negotiation.product, negotiation.quantity
    discount = money((negotiation.list_price - negotiation.deal_price) * qty)
    code = f"DEAL-{negotiation.pk}-{secrets.token_hex(3).upper()}"
    rng = Range.objects.create(name=f"Negotiated deal {negotiation.ref} {code}")
    rng.add_product(product)
    condition = Condition.objects.create(range=rng, type=Condition.COUNT, value=qty)
    benefit = Benefit.objects.create(range=rng, type=Benefit.FIXED, value=discount)
    offer = ConditionalOffer.objects.create(
        name=f"Negotiated deal {negotiation.ref} ({code})", offer_type=ConditionalOffer.VOUCHER,
        condition=condition, benefit=benefit, exclusive=True,
    )
    now = timezone.now()
    voucher = Voucher.objects.create(name=f"Deal {negotiation.ref} {code}", code=code, usage=Voucher.SINGLE_USE,
                                     start_datetime=now, end_datetime=now + timedelta(days=7))
    voucher.offers.add(offer)
    return voucher
