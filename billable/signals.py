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
# Arguments: usage (ProductUsage)
quota_consumed = Signal()

# Sent after trial period activation
# Arguments: user_id, telegram_id, products (List[str])
trial_activated = Signal()

# Sent when a product is deactivated (expired or exhausted)
# Arguments: user_product (UserProduct), reason (str)
product_deactivated = Signal()
