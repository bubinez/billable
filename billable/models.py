"""Models for the custom billing system.

Supports multiple active products for a single user,
detailed usage tracking (e.g., "30 of 100 applications"), and a flexible pricing system.
"""

from __future__ import annotations

import hashlib
import uuid
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from .conf import billable_settings


class ProductQuerySet(models.QuerySet):
    """
    Custom QuerySet for Product model that normalizes product_key to uppercase.
    """

    def update(self, **kwargs) -> int:
        """
        Normalize product_key to uppercase before updating.
        """
        if 'product_key' in kwargs and kwargs['product_key']:
            kwargs['product_key'] = kwargs['product_key'].upper()
        return super().update(**kwargs)

    def bulk_create(self, objs, batch_size=None, ignore_conflicts=False) -> list[Product]:
        """
        Normalize product_key to uppercase for all objects before bulk creation.
        """
        for obj in objs:
            if obj.product_key:
                obj.product_key = obj.product_key.upper()
        return super().bulk_create(objs, batch_size=batch_size, ignore_conflicts=ignore_conflicts)


class OfferQuerySet(models.QuerySet):
    """
    Custom QuerySet for Offer model that normalizes SKU to uppercase.
    """

    def update(self, **kwargs) -> int:
        """
        Normalize SKU to uppercase before updating.
        """
        if 'sku' in kwargs and kwargs['sku']:
            kwargs['sku'] = kwargs['sku'].upper()
        return super().update(**kwargs)

    def bulk_create(self, objs, batch_size=None, ignore_conflicts=False) -> list[Offer]:
        """
        Normalize SKU to uppercase for all objects before bulk creation.
        """
        for obj in objs:
            if obj.sku:
                obj.sku = obj.sku.upper()
        return super().bulk_create(objs, batch_size=batch_size, ignore_conflicts=ignore_conflicts)


class Product(models.Model):
    """
    Fundamental entity, technical resource or access right.
    """

    class ProductType(models.TextChoices):
        PERIOD = "period", "By period"
        QUANTITY = "quantity", "By quantity"
        UNLIMITED = "unlimited", "Unlimited"

    product_key = models.CharField(
        max_length=50,
        unique=True,
        null=True,
        blank=True,
        verbose_name="Product Key",
        help_text="Unique product key (e.g., 'DIAMONDS', 'VIP_ACCESS'). Automatically normalized to uppercase (CAPS) when saved.",
    )
    name = models.CharField(max_length=100, verbose_name="Product Name")
    description = models.TextField(blank=True, verbose_name="Product Description")
    product_type = models.CharField(
        max_length=20,
        choices=ProductType.choices,
        verbose_name="Product Type",
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name="Active",
    )
    is_currency = models.BooleanField(
        default=False,
        verbose_name="Is Currency",
        help_text="If True, this product can be used as a currency in the exchange engine.",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="Creation Date",
    )
    metadata = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="Additional Parameters",
    )

    objects = ProductQuerySet.as_manager()

    class Meta:
        db_table = "billable_products"
        verbose_name = "Product"
        verbose_name_plural = "Products"
        ordering = ["name"]
        indexes = [
            models.Index(fields=["is_active"], name="billable_prod_is_active_idx"),
            models.Index(fields=["product_type"], name="billable_prod_type_idx"),
        ]

    def clean(self) -> None:
        """
        Validate shared namespace: product_key must not exist as an Offer SKU.
        """
        if self.product_key:
            if Offer.objects.filter(sku=self.product_key).exists():
                raise ValidationError(
                    {"product_key": f"Conflict: '{self.product_key}' is already used as an Offer SKU."}
                )
        super().clean()

    def save(self, *args, **kwargs) -> None:
        """
        Normalize product_key to uppercase before saving.
        """
        if self.product_key:
            self.product_key = self.product_key.upper()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.name} ({self.get_product_type_display()})"


class Offer(models.Model):
    """
    Marketing packaging for products.
    """

    sku = models.CharField(
        max_length=50,
        unique=True,
        verbose_name="SKU",
        help_text="Commercial deal identifier (e.g., 'OFF_DIAMONDS_100'). Automatically normalized to uppercase (CAPS) when saved. Used for grants and purchases.",
    )
    name = models.CharField(max_length=255, verbose_name="Offer Name")
    price = models.DecimalField(
        max_digits=20,
        decimal_places=2,
        verbose_name="Price",
    )
    currency = models.CharField(
        max_length=10,
        verbose_name="Currency",
        help_text="EUR, USD, XTR, INTERNAL",
    )
    image = models.ImageField(
        upload_to="billable/offers/",
        null=True,
        blank=True,
        verbose_name="Image",
    )
    description = models.TextField(blank=True, verbose_name="Description")
    is_active = models.BooleanField(default=True, verbose_name="Is Active")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Created At")
    metadata = models.JSONField(default=dict, blank=True, verbose_name="Metadata")

    objects = OfferQuerySet.as_manager()

    class Meta:
        db_table = "billable_offers"
        verbose_name = "Offer"
        verbose_name_plural = "Offers"
        ordering = ["-created_at"]

    def clean(self) -> None:
        """
        Validate shared namespace: SKU must not exist as a Product Key.
        """
        if self.sku:
            if Product.objects.filter(product_key=self.sku).exists():
                raise ValidationError(
                    {"sku": f"Conflict: '{self.sku}' is already used as a Product Key."}
                )
        super().clean()

    def save(self, *args, **kwargs) -> None:
        """
        Normalize SKU to uppercase before saving.
        """
        if self.sku:
            self.sku = self.sku.upper()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.name} ({self.price} {self.currency})"


class OfferItem(models.Model):
    """
    Connects Offer with Products.
    """

    class PeriodUnit(models.TextChoices):
        HOURS = "hours", "Hours"
        DAYS = "days", "Days"
        MONTHS = "months", "Months"
        YEARS = "years", "Years"
        FOREVER = "forever", "Forever"

    offer = models.ForeignKey(
        Offer,
        on_delete=models.CASCADE,
        related_name="items",
        verbose_name="Offer",
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="offer_items",
        verbose_name="Product",
    )
    quantity = models.PositiveIntegerField(
        default=1,
        verbose_name="Quantity",
        help_text="Units to grant",
    )

    # Timing fields
    period_unit = models.CharField(
        max_length=10,
        choices=PeriodUnit.choices,
        default=PeriodUnit.FOREVER,
        verbose_name="Period Unit",
    )
    period_value = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name="Period Value",
    )

    class Meta:
        db_table = "billable_offer_items"
        verbose_name = "Offer Item"
        verbose_name_plural = "Offer Items"

    def __str__(self) -> str:
        return f"{self.offer.name} -> {self.product.name} x{self.quantity}"

class Order(models.Model):
    """
    User order for purchasing products.
    """

    class Status(models.TextChoices):
        """Order statuses."""

        PENDING = "pending", "Waiting for payment"
        PAID = "paid", "Paid"
        CANCELLED = "cancelled", "Cancelled"
        REFUNDED = "refunded", "Refunded"

    # User relationship
    user = models.ForeignKey(
        billable_settings.USER_MODEL,
        on_delete=models.CASCADE,
        verbose_name="User",
    )

    # Amount and currency fields
    total_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        verbose_name="Total Amount",
    )
    currency = models.CharField(
        max_length=3,
        default="RUB",
        verbose_name="Currency",
    )

    # Order status fields
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        verbose_name="Status",
    )

    # Payment info fields
    payment_method = models.CharField(
        max_length=20,
        default="yoomoney",
        verbose_name="Payment Method",
    )
    payment_id = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        verbose_name="Payment ID",
    )

    # Additional metadata (JSON)
    metadata = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="Metadata",
        help_text="Additional technical information"
    )

    # Date fields
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="Creation Date",
    )
    paid_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Payment Date",
    )

    class Meta:
        db_table = "billable_orders"
        verbose_name = "Order"
        verbose_name_plural = "Orders"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "status"], name="billable_ord_user_status_idx"),
            models.Index(fields=["status"], name="billable_ord_status_idx"),
            models.Index(fields=["created_at"], name="billable_ord_created_at_idx"),
        ]

    def __str__(self) -> str:
        """Return human-readable representation."""
        return f"Order #{self.id} - user_id={self.user_id} ({self.total_amount} {self.currency})"

    def is_paid(self) -> bool:
        """
        Checks if the order is paid.
        
        Returns:
            bool: True if the order is paid.
        """
        return self.status == Order.Status.PAID

    def can_be_cancelled(self) -> bool:
        """
        Checks if the order can be cancelled.
        
        Returns:
            bool: True if the order can be cancelled.
        """
        return self.status == Order.Status.PENDING

    def get_order_items(self):
        """
        Gets order items with offers.
        
        Returns:
            QuerySet[OrderItem]: Order items with loaded offers.
        """
        return self.items.select_related("offer").all()

    def get_offers(self) -> list[Offer]:
        """
        Gets all offers in the order.
        
        Returns:
            list[Offer]: List of all offers in the order.
        """
        return [item.offer for item in self.items.all() if item.offer]

    def get_first_offer(self) -> Offer | None:
        """
        Gets the first offer in the order.
        
        Returns:
            Offer|None: First offer or None if the order is empty.
        """
        order_item = self.items.select_related("offer").first()
        return order_item.offer if order_item else None


class OrderItem(models.Model):
    """
    Position in the order.
    """

    # Relationship with order and offer
    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="items",
        verbose_name="Order",
    )
    offer = models.ForeignKey(
        Offer,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        verbose_name="Offer",
    )

    # Quantity and price fields
    quantity = models.IntegerField(
        default=1,
        verbose_name="Quantity",
    )
    price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        verbose_name="Price per unit",
    )

    class Meta:
        db_table = "billable_order_items"
        verbose_name = "Order Item"
        verbose_name_plural = "Order Items"
        indexes = [
            models.Index(fields=["order"], name="billable_ord_item_order_idx"),
        ]

    def __str__(self) -> str:
        """Return human-readable representation."""
        return f"{self.offer.name} x{self.quantity} - {self.price} {self.order.currency}"

    def get_quota_batches(self):
        """
        Gets quota batches associated with an order item.
        """
        return QuotaBatch.objects.filter(order_item=self).select_related("user", "product")


class QuotaBatch(models.Model):
    """
    What is actually on the balance after purchase/grant.
    """

    class State(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        EXHAUSTED = "EXHAUSTED", "Exhausted"
        EXPIRED = "EXPIRED", "Expired"
        REVOKED = "REVOKED", "Revoked"

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    user = models.ForeignKey(
        billable_settings.USER_MODEL,
        on_delete=models.CASCADE,
        related_name="quota_batches",
        verbose_name="User",
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="quota_batches",
        verbose_name="Product",
    )
    source_offer = models.ForeignKey(
        Offer,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Source Offer",
    )
    order_item = models.ForeignKey(
        OrderItem,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Order Item",
    )

    initial_quantity = models.IntegerField(verbose_name="Initial Quantity")
    remaining_quantity = models.IntegerField(verbose_name="Remaining Quantity")

    valid_from = models.DateTimeField(default=timezone.now, verbose_name="Valid From")
    expires_at = models.DateTimeField(null=True, blank=True, verbose_name="Expires At")

    state = models.CharField(
        max_length=20,
        choices=State.choices,
        default=State.ACTIVE,
        verbose_name="State",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Created At")

    class Meta:
        db_table = "billable_quota_batches"
        verbose_name = "Quota Batch"
        verbose_name_plural = "Quota Batches"
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["user", "product", "state"], name="billable_qb_user_prod_state"),
            models.Index(fields=["expires_at"], name="billable_qb_expires_at_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.user_id} - {self.product.name} ({self.remaining_quantity}/{self.initial_quantity})"


class Transaction(models.Model):
    """
    Every balance change is fixed by an immutable transaction.
    """

    class Direction(models.TextChoices):
        CREDIT = "CREDIT", "Credit"
        DEBIT = "DEBIT", "Debit"

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    user = models.ForeignKey(
        billable_settings.USER_MODEL,
        on_delete=models.CASCADE,
        related_name="billable_transactions",
        verbose_name="User",
    )
    quota_batch = models.ForeignKey(
        QuotaBatch,
        on_delete=models.CASCADE,
        related_name="transactions",
        verbose_name="Quota Batch",
    )

    amount = models.IntegerField(verbose_name="Amount")
    direction = models.CharField(
        max_length=10,
        choices=Direction.choices,
        verbose_name="Direction",
    )
    action_type = models.CharField(
        max_length=50,
        verbose_name="Action Type",
        help_text="purchase, referral_bonus, usage, refund",
    )

    # Generic FK for related objects
    content_type = models.ForeignKey(
        ContentType,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
    )
    object_id = models.CharField(max_length=255, null=True, blank=True)
    related_object = GenericForeignKey("content_type", "object_id")

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Created At")
    metadata = models.JSONField(default=dict, blank=True, verbose_name="Metadata")

    class Meta:
        db_table = "billable_transactions"
        verbose_name = "Transaction"
        verbose_name_plural = "Transactions"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "created_at"], name="billable_tx_user_created_idx"),
            models.Index(fields=["action_type"], name="billable_tx_action_type_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.direction} {self.amount} - {self.action_type} ({self.user_id})"

class TrialHistory(models.Model):
    """
    Model for tracking users who have already used a free trial.
    
    Used to prevent reuse of trial periods
    by a single user through different identifiers (Abstract Identity Model).
    """

    identity_type = models.CharField(
        max_length=50,
        db_index=True,
        verbose_name="Identity Type",
        help_text="Type of identifier (e.g., 'external_id', 'hh', 'email', 'fingerprint')",
    )
    identity_hash = models.CharField(
        max_length=64,
        db_index=True,
        verbose_name="Identity Hash",
        help_text="SHA-256 hash of the normalized identifier value for privacy",
    )
    trial_plan_name = models.CharField(
        max_length=100,
        verbose_name="Trial Plan Name",
        help_text="Name of the trial plan used",
    )
    used_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="Trial Usage Date",
        help_text="Date and time when the free trial was used",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="Record Creation Date",
        help_text="Date the record was created in the database",
    )

    class Meta:
        db_table = "billable_trial_history"
        verbose_name = "Trial history"
        verbose_name_plural = "Trial histories"
        ordering = ["-used_at"]
        unique_together = ["identity_type", "identity_hash"]
        indexes = [
            models.Index(fields=["identity_type", "identity_hash"], name="billable_trial_identity_idx"),
        ]

    def __str__(self) -> str:
        """
        Return human-readable representation.
        
        Returns:
            str: Identity type and first 8 characters of the hash.
        """
        return f"Trial: {self.identity_type}:{self.identity_hash[:8]}... ({self.trial_plan_name})"

    @staticmethod
    def generate_identity_hash(value: str | int | None) -> str:
        """
        Generates a stable SHA-256 hash for an identity value.
        
        Args:
            value: The identity value to hash.
            
        Returns:
            str: SHA-256 hash string or empty string if value is None.
        """
        if value is None:
            return ""
        normalized = str(value).strip().lower()
        return hashlib.sha256(normalized.encode()).hexdigest()

    @classmethod
    def has_used_trial(cls, identities: dict[str, str | int | None] | None = None, **kwargs) -> bool:
        """
        Checks if the user has used a trial before.

        Args:
            identities: Dictionary of {identity_type: identity_value}.
            **kwargs: Backward compatibility for telegram_id, hh_id.

        Returns:
            bool: True if any identity matches a record in TrialHistory, False otherwise.
        """
        ids_to_check = identities.copy() if identities else {}
        for key in ["telegram_id", "hh_id"]:
            if key in kwargs and kwargs[key]:
                type_name = key.replace("_id", "")
                ids_to_check[type_name] = kwargs[key]

        if not ids_to_check:
            return False

        lookups = models.Q()
        for id_type, id_value in ids_to_check.items():
            if id_value:
                id_hash = cls.generate_identity_hash(id_value)
                lookups |= models.Q(identity_type=id_type, identity_hash=id_hash)

        if not lookups:
            return False

        return cls.objects.filter(lookups).exists()

    @classmethod
    async def ahas_used_trial(cls, identities: dict[str, str | int | None] | None = None, **kwargs) -> bool:
        """
        Asynchronously checks if the user has used a trial before.

        Args:
            identities: Dictionary of {identity_type: identity_value}.
            **kwargs: Backward compatibility for telegram_id, hh_id.

        Returns:
            bool: True if any identity matches a record in TrialHistory, False otherwise.
        """
        ids_to_check = identities.copy() if identities else {}
        for key in ["telegram_id", "hh_id"]:
            if key in kwargs and kwargs[key]:
                type_name = key.replace("_id", "")
                ids_to_check[type_name] = kwargs[key]

        if not ids_to_check:
            return False

        lookups = models.Q()
        for id_type, id_value in ids_to_check.items():
            if id_value:
                id_hash = cls.generate_identity_hash(id_value)
                lookups |= models.Q(identity_type=id_type, identity_hash=id_hash)

        if not lookups:
            return False

        return await cls.objects.filter(lookups).aexists()


class ExternalIdentity(models.Model):
    """
    External identity mapping for integrations.

    Stores a stable external identifier for a given provider (telegram, max, n8n, etc.)
    with optional linkage to the local Django user model.

    Uniqueness is enforced on (provider, external_id) to avoid collisions across
    different identity sources.
    """

    provider = models.CharField(
        max_length=50,
        default="default",
        db_index=True,
        blank=True,
        verbose_name="Provider",
        help_text="Identity source/provider name (e.g., 'telegram', 'max', 'n8n')",
    )
    external_id = models.CharField(
        max_length=255,
        db_index=True,
        verbose_name="External ID",
        help_text="Stable external identifier within the provider scope",
    )
    user = models.ForeignKey(
        billable_settings.USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="billable_external_identities",
        verbose_name="User",
        help_text="Optional link to the local Django user model (if applicable)",
    )
    metadata = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="Metadata",
        help_text="Additional identity data (e.g., workspace, username, raw payload)",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="Creation Date",
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name="Update Date",
    )

    class Meta:
        db_table = "billable_external_identities"
        verbose_name = "External Identity"
        verbose_name_plural = "External Identities"
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "external_id"],
                name="billable_extid_provider_external_id_uniq",
            ),
        ]
        indexes = [
            models.Index(fields=["provider"], name="billable_extid_provider_idx"),
            models.Index(fields=["external_id"], name="billable_extid_external_id_idx"),
            models.Index(fields=["user"], name="billable_extid_user_idx"),
        ]

    def __str__(self) -> str:
        """Return human-readable representation."""
        return f"{self.provider}:{self.external_id}"

    @classmethod
    def get_user_by_identity(
        cls, external_id: str | int, provider: str = "default"
    ) -> billable_settings.USER_MODEL | None:
        """
        Retrieves a user by their external identity.

        Args:
            external_id: The unique identifier from the external provider.
            provider: The name of the identity provider (e.g., 'telegram', 'n8n').

        Returns:
            The User instance if found and linked, otherwise None.

        Example:
            user = ExternalIdentity.get_user_by_identity("12345", provider="telegram")
        """
        identity = (
            cls.objects.filter(external_id=str(external_id), provider=provider)
            .select_related("user")
            .first()
        )
        return identity.user if identity else None

    @classmethod
    async def aget_user_by_identity(
        cls, external_id: str | int, provider: str = "default"
    ) -> billable_settings.USER_MODEL | None:
        """
        Asynchronously retrieves a user by their external identity.

        Args:
            external_id: The unique identifier from the external provider.
            provider: The name of the identity provider (e.g., 'telegram', 'n8n').

        Returns:
            The User instance if found and linked, otherwise None.

        Example:
            user = await ExternalIdentity.aget_user_by_identity("12345", provider="telegram")
        """
        identity = (
            await cls.objects.filter(external_id=str(external_id), provider=provider)
            .select_related("user")
            .afirst()
        )
        return identity.user if identity else None


class Referral(models.Model):
    """
    Referral program model.
    
    Tracks links between the inviter (referrer) and the invitee (referee)
    users, as well as the bonus accrual status.
    """

    referrer = models.ForeignKey(
        billable_settings.USER_MODEL,
        on_delete=models.CASCADE,
        related_name="referrals_made",
        verbose_name="Inviter",
        help_text="User who invited another user",
    )
    referee = models.ForeignKey(
        billable_settings.USER_MODEL,
        on_delete=models.CASCADE,
        related_name="referrals_received",
        verbose_name="Invitee",
        help_text="User who was invited",
    )
    bonus_granted = models.BooleanField(
        default=False,
        verbose_name="Bonus Accrued",
        help_text="Flag indicating whether a bonus was granted to the referrer",
    )
    bonus_granted_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Bonus Accrual Date",
        help_text="Date and time the bonus was accrued",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="Creation Date",
        help_text="Date the referral link was created",
    )
    metadata = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="Additional Data",
        help_text="Additional information about the referral (source, campaign, etc.)",
    )

    class Meta:
        db_table = "billable_referrals"
        verbose_name = "Referral"
        verbose_name_plural = "Referrals"
        ordering = ["-created_at"]
        unique_together = ["referrer", "referee"]
        indexes = [
            models.Index(fields=["referrer"], name="billable_ref_referrer_idx"),
            models.Index(fields=["referee"], name="billable_ref_referee_idx"),
            models.Index(fields=["bonus_granted"], name="billable_ref_bonus_granted_idx"),
            models.Index(fields=["created_at"], name="billable_ref_created_at_idx"),
        ]

    def __str__(self) -> str:
        """Return human-readable representation."""
        bonus_status = "✓" if self.bonus_granted else "✗"
        return f"{self.referrer_id} → {self.referee_id} (bonus: {bonus_status})"

    def claim_bonus(self) -> bool:
        """
        Atomically marks bonus as granted.
        
        Returns:
            bool: True if the bonus was successfully claimed (was not granted before),
                  False if it was already granted.
        """
        # Atomic update to prevent race conditions
        rows = Referral.objects.filter(pk=self.pk, bonus_granted=False).update(
            bonus_granted=True, 
            bonus_granted_at=timezone.now()
        )
        
        if rows > 0:
            # Update local instance to reflect DB change
            self.bonus_granted = True
            self.bonus_granted_at = timezone.now()
            return True
        return False


from .models_proxy import Customer
