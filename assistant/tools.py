"""
Agent tools for the Store Ops Assistant.

Each function queries Django Oscar's own models (catalogue, partner, order),
so the agent works on the shop's live data: the same products, stock and
orders you see in the storefront and in the Oscar dashboard.

The docstrings are part of the interface: the Ollama Python library turns each
function's type hints and "Args:" section into the JSON schema the model sees.
Every tool returns a JSON string so the model reads exact values.
"""

import json
import uuid
from datetime import timedelta

from django.conf import settings
from django.db.models import Count, F, Q, Sum
from django.utils import timezone
from oscar.core.loading import get_model

Product = get_model("catalogue", "Product")
StockRecord = get_model("partner", "StockRecord")
Order = get_model("order", "Order")
Line = get_model("order", "Line")


def _money(value):
    """Decimal -> float rounded to cents (JSON cannot hold Decimal)."""
    return round(float(value or 0), 2)


def _to_int(value, default, low, high):
    """Small models sometimes send numbers as strings or out of range; clamp them."""
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default


def _stock_info(sr):
    """Common stock fields for one stock record."""
    available = (sr.num_in_stock or 0) - (sr.num_allocated or 0)
    threshold = sr.low_stock_threshold
    return {
        "price_usd": _money(sr.price),
        "num_in_stock": sr.num_in_stock,
        "num_available": available,
        "low_stock_threshold": threshold,
        "is_low_stock": threshold is not None and available <= threshold,
    }


def _product_card(product):
    """Short product summary used by several tools."""
    sr = product.stockrecords.first()
    card = {
        "product_id": product.id,
        "title": product.title,
        "upc": product.upc,
        "category": ", ".join(c.name for c in product.get_categories().all()),
    }
    if sr:
        card.update(_stock_info(sr))
    return card


def _find_product(product_id):
    """Look up by numeric id or UPC; return None if not found."""
    query = Product.objects.filter(upc__iexact=str(product_id).strip())
    if str(product_id).strip().isdigit():
        query = Product.objects.filter(Q(pk=int(product_id)) | Q(upc__iexact=str(product_id)))
    return query.first()


def _not_found(product_id):
    return json.dumps({
        "error": f"No product with id or UPC '{product_id}'.",
        "hint": "Call search_products first to get the product_id.",
    })


# ---------------------------------------------------------------------------
# Read tools
# ---------------------------------------------------------------------------
def search_products(query: str) -> str:
    """
    Search the shop catalogue by product name, UPC or category name.

    Args:
        query: Words to search for, for example "raspberry", "sensor" or "ESP32"

    Returns:
        str: JSON list of up to 10 matching products with id, price and stock
    """
    words = [w for w in str(query).split() if w]
    products = Product.objects.all()
    for word in words:
        products = products.filter(
            Q(title__icontains=word) | Q(upc__icontains=word) | Q(categories__name__icontains=word)
        )
    products = products.distinct()[:10]
    results = [_product_card(p) for p in products]
    return json.dumps(results if results else {"result": f"No products match '{query}'."})


def get_product_details(product_id: int) -> str:
    """
    Get full details for one product: description, price, stock and units sold
    in the last 30 days.

    Args:
        product_id: The product_id from search_products (a UPC also works)

    Returns:
        str: JSON with product details, stock levels and recent sales
    """
    product = _find_product(product_id)
    if product is None:
        return _not_found(product_id)
    since = timezone.now() - timedelta(days=30)
    sold = Line.objects.filter(product=product, order__date_placed__gte=since).aggregate(
        units=Sum("quantity"), orders=Count("order", distinct=True)
    )
    details = _product_card(product)
    details.update({
        "description": product.description,
        "units_sold_last_30_days": sold["units"] or 0,
        "orders_last_30_days": sold["orders"] or 0,
    })
    return json.dumps(details)


def list_low_stock() -> str:
    """
    List products whose available stock is at or below their low-stock
    threshold, including products that are sold out.

    Returns:
        str: JSON list of low-stock products, most urgent first
    """
    default = settings.ASSISTANT_LOW_STOCK_THRESHOLD
    rows = []
    for sr in StockRecord.objects.select_related("product"):
        available = (sr.num_in_stock or 0) - (sr.num_allocated or 0)
        threshold = sr.low_stock_threshold if sr.low_stock_threshold is not None else default
        if available <= threshold:
            rows.append({
                "product_id": sr.product.id,
                "title": sr.product.title,
                "num_available": available,
                "low_stock_threshold": threshold,
                "sold_out": available <= 0,
            })
    rows.sort(key=lambda r: r["num_available"] - r["low_stock_threshold"])
    return json.dumps(rows if rows else {"result": "No products are low on stock."})


def get_sales_summary(days: int = 7) -> str:
    """
    Summarise sales over the last N days: number of orders, revenue,
    and the best-selling products by units.

    Args:
        days: How many days back to look, between 1 and 90 (default 7)

    Returns:
        str: JSON with order count, revenue in USD and top 5 products
    """
    days = _to_int(days, 7, 1, 90)
    since = timezone.now() - timedelta(days=days)
    orders = Order.objects.filter(date_placed__gte=since)
    totals = orders.aggregate(count=Count("id"), revenue=Sum("total_incl_tax"))
    top = (
        Line.objects.filter(order__in=orders)
        .values("product_id", "title")
        .annotate(units=Sum("quantity"), revenue=Sum("line_price_incl_tax"))
        .order_by("-units")[:5]
    )
    return json.dumps({
        "period_days": days,
        "orders": totals["count"] or 0,
        "revenue_usd": _money(totals["revenue"]),
        "top_products": [
            {"product_id": t["product_id"], "title": t["title"], "units": t["units"],
             "revenue_usd": _money(t["revenue"])}
            for t in top
        ],
    })


def get_recent_orders(limit: int = 5) -> str:
    """
    List the most recent orders with status, total and items.

    Args:
        limit: How many orders to return, between 1 and 20 (default 5)

    Returns:
        str: JSON list of orders, newest first
    """
    limit = _to_int(limit, 5, 1, 20)
    orders = Order.objects.order_by("-date_placed").prefetch_related("lines")[:limit]
    return json.dumps([
        {
            "order_number": o.number,
            "placed": timezone.localtime(o.date_placed).strftime("%Y-%m-%d %H:%M"),
            "status": o.status,
            "total_usd": _money(o.total_incl_tax),
            "items": [f"{line.quantity} x {line.title}" for line in o.lines.all()],
        }
        for o in orders
    ])


# ---------------------------------------------------------------------------
# Write tool (human in the loop)
# ---------------------------------------------------------------------------
def propose_restock(product_id: int, quantity: int, reason: str) -> str:
    """
    Propose adding stock to a product. This does NOT change stock yet: it
    creates a request that a staff member must confirm in the interface.
    Only use this when the user asks to restock or reorder.

    Args:
        product_id: The product_id from search_products or list_low_stock
        quantity: Number of units to add, between 1 and 500
        reason: Short justification based on the data, for example "sold out, 8 units sold this week"

    Returns:
        str: JSON describing the pending restock request
    """
    product = _find_product(product_id)
    if product is None:
        return _not_found(product_id)
    sr = product.stockrecords.first()
    if sr is None:
        return json.dumps({"error": f"'{product.title}' has no stock record to update."})
    qty = _to_int(quantity, 0, 0, 500)
    if qty <= 0:
        return json.dumps({"error": "quantity must be a whole number between 1 and 500."})
    return json.dumps({
        "status": "awaiting_staff_confirmation",
        "action_id": uuid.uuid4().hex[:8],
        "type": "restock",
        "product_id": product.id,
        "stockrecord_id": sr.id,
        "title": product.title,
        "quantity": qty,
        "current_stock": sr.num_in_stock,
        "stock_after": (sr.num_in_stock or 0) + qty,
        "reason": str(reason),
    })


def apply_restock(action):
    """Executed by the view only after a staff member clicks Confirm."""
    updated = StockRecord.objects.filter(pk=action["stockrecord_id"]).update(
        num_in_stock=F("num_in_stock") + action["quantity"]
    )
    if not updated:
        return None
    return StockRecord.objects.get(pk=action["stockrecord_id"]).num_in_stock


# Registry passed to the model (name -> function)
TOOLS = {
    f.__name__: f
    for f in (search_products, get_product_details, list_low_stock,
              get_sales_summary, get_recent_orders, propose_restock)
}
