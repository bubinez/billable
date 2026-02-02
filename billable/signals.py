"""Signals for the billable module.

Allow other applications to react to billing events 
(order payment, quota consumption, etc.) without direct dependency.
"""

from __future__ import annotations

from django.dispatch import Signal

# Sent after successful order payment confirmation
# Arguments: order (Order)
order_confirmed = Signal()

# Sent after successful quota consumption
# Arguments: usage (Transaction)
quota_consumed = Signal()

# Sent after trial period activation
# Arguments: user_id (int), products (List[str])
trial_activated = Signal()

# Sent after any transaction (grant or consume) is created
# Arguments: transaction (Transaction)
transaction_created = Signal()

# Sent after a referral link is established
# Arguments: referral (Referral)
referral_attached = Signal()

# Sent after successful customer merge
# Arguments: target_user_id (int), source_user_id (int)
customers_merged = Signal()
