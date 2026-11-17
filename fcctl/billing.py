"""Billing reads.


DEPRECATED: thin shim over fcctl._sync.billing (generated from fcctl._async.billing); new code should use the async core / facade.
"""
from __future__ import annotations

from ._sync import billing as _impl

D = _impl.D
OPS = _impl.OPS
eligible_plans = _impl.eligible_plans
trust_tier = _impl.trust_tier
wallet_balance = _impl.wallet_balance
ledger = _impl.ledger
invoices_list = _impl.invoices_list
invoice_get = _impl.invoice_get
subscriptions = _impl.subscriptions
overview = _impl.overview
billing_health = _impl.billing_health
