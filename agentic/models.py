"""
Data model for the agentic layer.

Concepts (adapted from Google's Agent Payments Protocol, AP2):
  - Mandate: limits a human gives an agent, signed so later edits are detected.
      intent      buyer: "buy X when available, max price P, before date D"
      cart        buyer: approval of one exact cart and total (pay now)
      negotiation buyer: private price limit for a bulk-quote negotiation
      policy      seller: autonomy levels and limits for the Store Agent
  - Wallet: simulated Campus Wallet so every order is paid before it is placed.
  - AgentRun: one unit of agent work (queued, run by the agent worker, polled by the UI).
  - AgentAction: audit log of everything agents and the guard did.
"""

from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

MONEY = dict(max_digits=10, decimal_places=2)


class Wallet(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="campus_wallet")
    balance = models.DecimalField(default=Decimal("0.00"), **MONEY)  # spendable now
    held = models.DecimalField(default=Decimal("0.00"), **MONEY)     # reserved by active delegations
    updated = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Wallet of {self.user}"


class WalletEntry(models.Model):
    """Ledger line: top-up, hold, release or charge."""
    wallet = models.ForeignKey(Wallet, on_delete=models.CASCADE, related_name="entries")
    kind = models.CharField(max_length=20)
    amount = models.DecimalField(**MONEY)
    note = models.CharField(max_length=255, blank=True)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created", "-id"]


class Mandate(models.Model):
    INTENT, CART, NEGOTIATION, POLICY = "intent", "cart", "negotiation", "policy"
    KINDS = [(INTENT, "Intent (delegated purchase)"), (CART, "Cart approval"),
             (NEGOTIATION, "Negotiation limit"), (POLICY, "Seller policy")]
    ACTIVE, FULFILLED, EXPIRED, CANCELLED, SUPERSEDED = "active", "fulfilled", "expired", "cancelled", "superseded"

    kind = models.CharField(max_length=20, choices=KINDS)
    side = models.CharField(max_length=10)  # buyer / seller
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="agent_mandates")
    product = models.ForeignKey("catalogue.Product", null=True, blank=True, on_delete=models.SET_NULL)
    terms = models.JSONField(default=dict)
    signature = models.CharField(max_length=64, blank=True)
    status = models.CharField(max_length=20, default=ACTIVE)
    expires_at = models.DateTimeField(null=True, blank=True)
    outcome = models.CharField(max_length=255, blank=True)
    order_number = models.CharField(max_length=128, blank=True)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created", "-id"]

    @property
    def ref(self):
        return f"M-{self.pk}"

    @property
    def is_expired(self):
        return self.expires_at is not None and self.expires_at < timezone.now()

    def __str__(self):
        return f"{self.ref} {self.kind} ({self.status})"


class SupplierPrice(models.Model):
    """What the store pays its supplier per unit (not shown to buyers)."""
    product = models.OneToOneField("catalogue.Product", on_delete=models.CASCADE, related_name="supplier_price")
    unit_cost = models.DecimalField(**MONEY)
    supplier = models.CharField(max_length=100, default="Campus Tech Distributor")


class PurchaseOrder(models.Model):
    AWAITING, APPROVED, RECEIVED, REJECTED = "awaiting_approval", "approved", "received", "rejected"

    product = models.ForeignKey("catalogue.Product", on_delete=models.CASCADE, related_name="purchase_orders")
    quantity = models.PositiveIntegerField()
    unit_cost = models.DecimalField(**MONEY)
    amount = models.DecimalField(**MONEY)
    status = models.CharField(max_length=20, default=AWAITING)
    created_by = models.CharField(max_length=50)  # "Store Agent" / "Store Agent (chat)"
    approved_by = models.CharField(max_length=100, blank=True)
    reason = models.TextField(blank=True)
    mandate = models.ForeignKey(Mandate, null=True, blank=True, on_delete=models.SET_NULL)
    created = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    received_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created", "-id"]

    @property
    def ref(self):
        return f"PO-{self.pk}"


class AgentRun(models.Model):
    QUEUED, RUNNING, DONE, FAILED = "queued", "running", "done", "failed"

    kind = models.CharField(max_length=30)
    status = models.CharField(max_length=10, default=QUEUED)
    priority = models.IntegerField(default=5)  # lower runs first
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    product = models.ForeignKey("catalogue.Product", null=True, blank=True, on_delete=models.SET_NULL)
    payload = models.JSONField(default=dict)
    steps = models.JSONField(default=list)
    result = models.JSONField(default=dict)
    error = models.TextField(blank=True)
    llm_seconds = models.FloatField(default=0)
    created = models.DateTimeField(auto_now_add=True)
    started = models.DateTimeField(null=True, blank=True)
    finished = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created", "-id"]

    def step(self, label, status="running", detail=""):
        """Add or update a visible plan step (shown live in the UI)."""
        for s in self.steps:
            if s["label"] == label:
                s.update(status=status, detail=detail)
                break
        else:
            self.steps.append({"label": label, "status": status, "detail": detail})
        self.save(update_fields=["steps"])

    @property
    def is_active(self):
        return self.status in (self.QUEUED, self.RUNNING)


class AgentAction(models.Model):
    """Audit log entry. Every autonomous action and every guard decision lands here."""
    INFO, SUCCESS, WARNING, BLOCKED = "info", "success", "warning", "blocked"

    actor = models.CharField(max_length=30)  # Store Agent, Shopping Agent, Guard, Staff, Buyer
    verb = models.CharField(max_length=80)
    detail = models.TextField(blank=True)
    level = models.CharField(max_length=10, default=INFO)
    mandate = models.ForeignKey(Mandate, null=True, blank=True, on_delete=models.SET_NULL)
    run = models.ForeignKey(AgentRun, null=True, blank=True, on_delete=models.SET_NULL)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created", "-id"]


class Negotiation(models.Model):
    RUNNING, DEAL, NO_DEAL, AWAITING_SELLER, PAID, REJECTED = (
        "running", "deal", "no_deal", "awaiting_seller", "paid", "rejected_by_seller")

    buyer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="negotiations")
    product = models.ForeignKey("catalogue.Product", on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField()
    list_price = models.DecimalField(**MONEY)
    buyer_mandate = models.ForeignKey(Mandate, null=True, on_delete=models.SET_NULL, related_name="+")
    seller_mandate = models.ForeignKey(Mandate, null=True, on_delete=models.SET_NULL, related_name="+")
    floor_price = models.DecimalField(null=True, blank=True, **MONEY)  # seller private
    floor_reason = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=20, default=RUNNING)
    deal_price = models.DecimalField(null=True, blank=True, **MONEY)
    voucher_code = models.CharField(max_length=128, blank=True)
    order_number = models.CharField(max_length=128, blank=True)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created", "-id"]

    @property
    def ref(self):
        return f"N-{self.pk}"

    @property
    def deal_total(self):
        return (self.deal_price or 0) * self.quantity

    @property
    def list_total(self):
        return self.list_price * self.quantity


class NegotiationMessage(models.Model):
    negotiation = models.ForeignKey(Negotiation, on_delete=models.CASCADE, related_name="messages")
    side = models.CharField(max_length=10)  # buyer / seller
    action = models.CharField(max_length=10)  # offer / accept / reject
    unit_price = models.DecimalField(null=True, blank=True, **MONEY)
    proposed_price = models.DecimalField(null=True, blank=True, **MONEY)  # what the model asked for
    message = models.TextField(blank=True)
    guard_note = models.CharField(max_length=255, blank=True)
    seconds = models.FloatField(default=0)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created", "id"]


class WorkerHeartbeat(models.Model):
    """Single row updated by the agent worker so the UI can show if it is online."""
    last_seen = models.DateTimeField(null=True)
    last_scan = models.DateTimeField(null=True)
    current = models.CharField(max_length=120, blank=True)

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    @property
    def online(self):
        # While a model call runs the loop does not beat, so allow longer when busy
        limit = 300 if self.current else 15
        return self.last_seen is not None and (timezone.now() - self.last_seen).total_seconds() < limit
