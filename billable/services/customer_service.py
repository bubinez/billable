"""Service for customer management operations.

Includes logic for merging customers, handling identities, and other user-centric tasks.
"""

from __future__ import annotations

import logging
from django.db import transaction
from django.contrib.auth import get_user_model
from asgiref.sync import sync_to_async

from billable.models import (
    Order, QuotaBatch, Transaction, ExternalIdentity, Referral
)
from billable.signals import customers_merged

logger = logging.getLogger(__name__)
User = get_user_model()


class CustomerService:
    """
    Service for managing customer-related operations like merging.
    """

    @classmethod
    def merge_customers(cls, target_user_id: int, source_user_id: int) -> dict:
        """
        Merges source customer into target customer.

        Moves all orders, quota batches, transactions, identities, and referrals.
        Source user is not deleted here, only its data is moved.

        Args:
            target_user_id: ID of the user who will receive the data.
            source_user_id: ID of the user whose data will be moved.

        Returns:
            dict: Statistics of moved items.

        Raises:
            ValueError: If users are the same or don't exist.
        """
        if target_user_id == source_user_id:
            raise ValueError("Target and source users must be different.")

        if not User.objects.filter(pk=target_user_id).exists():
            raise ValueError(f"Target user {target_user_id} does not exist.")
        
        if not User.objects.filter(pk=source_user_id).exists():
            raise ValueError(f"Source user {source_user_id} does not exist.")

        stats = {
            "moved_orders": 0,
            "moved_batches": 0,
            "moved_transactions": 0,
            "moved_identities": 0,
            "moved_referrals": 0,
        }

        with transaction.atomic():
            # 1. Handle ExternalIdentities
            # If both have same provider, we might have a conflict.
            source_identities = ExternalIdentity.objects.filter(user_id=source_user_id)
            for identity in source_identities:
                # Check if target already has this provider
                if ExternalIdentity.objects.filter(user_id=target_user_id, provider=identity.provider).exists():
                    logger.warning(
                        f"Conflict: target user {target_user_id} already has identity for provider {identity.provider}. "
                        f"Skipping identity {identity.id} from source user {source_user_id}."
                    )
                    # Optionally we could delete it or re-link if external_id is same, 
                    # but for now we follow 6.4: if external_id different, it's an error/conflict.
                    # If external_id is same, we just delete the source one.
                    target_identity = ExternalIdentity.objects.get(user_id=target_user_id, provider=identity.provider)
                    if target_identity.external_id == identity.external_id:
                        identity.delete()
                    else:
                        # Different external_id for same provider - this is a real conflict
                        raise ValueError(
                            f"Identity conflict: both users have different external_ids for provider {identity.provider}."
                        )
                else:
                    identity.user_id = target_user_id
                    identity.save(update_fields=["user_id"])
                    stats["moved_identities"] += 1

            # 2. Move Orders
            stats["moved_orders"] = Order.objects.filter(user_id=source_user_id).update(user_id=target_user_id)

            # 3. Move QuotaBatches
            stats["moved_batches"] = QuotaBatch.objects.filter(user_id=source_user_id).update(user_id=target_user_id)

            # 4. Move Transactions
            stats["moved_transactions"] = Transaction.objects.filter(user_id=source_user_id).update(user_id=target_user_id)

            # 5. Move Referrals
            # Case 1: source_user was a referrer
            stats["moved_referrals"] += Referral.objects.filter(referrer_id=source_user_id).update(referrer_id=target_user_id)
            # Case 2: source_user was a referee
            stats["moved_referrals"] += Referral.objects.filter(referee_id=source_user_id).update(referee_id=target_user_id)

            # Cleanup: remove self-referral if it was created by merge
            Referral.objects.filter(referrer_id=target_user_id, referee_id=target_user_id).delete()

            # 6. Signal
            customers_merged.send(
                sender=cls,
                target_user_id=target_user_id,
                source_user_id=source_user_id
            )

            logger.info(
                f"Merged customer {source_user_id} into {target_user_id}. Stats: {stats}"
            )

        return stats

    @classmethod
    async def amerge_customers(cls, target_user_id: int, source_user_id: int) -> dict:
        """
        Asynchronous version of merge_customers.

        Args:
            target_user_id: ID of the user who will receive the data.
            source_user_id: ID of the user whose data will be moved.

        Returns:
            dict: Statistics of moved items.
        """
        return await sync_to_async(cls.merge_customers, thread_sensitive=True)(
            target_user_id=target_user_id,
            source_user_id=source_user_id
        )
