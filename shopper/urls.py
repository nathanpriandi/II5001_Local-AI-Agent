from django.urls import path

from . import views

app_name = "shopper"

urlpatterns = [
    path("", views.index, name="index"),
    path("plan/", views.plan_start, name="plan"),
    path("delegate/parse/", views.delegate_parse, name="delegate-parse"),
    path("delegate/create/", views.delegate_create, name="delegate-create"),
    path("delegate/<int:pk>/cancel/", views.delegate_cancel, name="delegate-cancel"),
    path("negotiate/", views.negotiate_start, name="negotiate"),
    path("run/<int:pk>/", views.run_status, name="run"),
    path("run/<int:pk>/pay/", views.run_pay, name="run-pay"),
    path("deal/<int:pk>/pay/", views.negotiation_pay, name="deal-pay"),
    path("wallet/topup/", views.wallet_topup_view, name="topup"),
    path("side/", views.side, name="side"),
]
