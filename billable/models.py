"""Models for the custom billing system.

Supports multiple active products for a single user,
detailed usage tracking (e.g., "30 of 100 applications"), and a flexible pricing system.
"""

from __future__ import annotations

import hashlib
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class Product(models.Model):
    """
    Product model in the billing system.
    
    Supports different product types:
    - period: products by period (e.g., resume boost for 30 days)
    - quantity: products by quantity (e.g., 100 job applications)
    - unlimited: unlimited products
    """

    class ProductType(models.TextChoices):
        """Product types."""

        PERIOD = "period", "By period"
        QUANTITY = "quantity", "By quantity"
        UNLIMITED = "unlimited", "Unlimited"

    # Main product fields
    name = models.CharField(max_length=100, verbose_name="Product Name")
    sku = models.CharField(
        max_length=50,
        unique=True,
        null=True,
        blank=True,
        verbose_name="SKU",
        help_text="Unique product SKU for n8n integration",
    )
    description = models.TextField(verbose_name="Product Description")
    product_type = models.CharField(
        max_length=20,
        choices=ProductType.choices,
        verbose_name="Product Type",
    )
    price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        verbose_name="Product Price",
    )
    currency = models.CharField(
        max_length=3,
        default="RUB",
        verbose_name="Currency",
    )

    # Fields for different product types
    # For products by period
    period_days = models.IntegerField(
        null=True,
        blank=True,
        verbose_name="Validity period in days",
    )

    # For products by quantity
    quantity = models.IntegerField(
        null=True,
        blank=True,
        verbose_name="Number of units",
    )

    # Activity management fields
    is_active = models.BooleanField(
        default=True,
        verbose_name="Active",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="Creation Date",
    )

    # Additional product parameters
    metadata = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="Additional Parameters",
    )

    class Meta:
        db_table = "billable_products"
        verbose_name = "Product"
        verbose_name_plural = "Products"
        ordering = ["name"]
        indexes = [
            models.Index(fields=["is_active"], name="billable_prod_is_active_idx"),
            models.Index(fields=["product_type"], name="billable_prod_type_idx"),
        ]

    def __str__(self) -> str:
        """Return human-readable representation."""
        return f"{self.name} ({self.get_product_type_display()})"

    def clean(self) -> None:
        """Product fields validation."""
        if self.product_type == Product.ProductType.PERIOD and not self.period_days:
            raise ValidationError("For period-based products, you must specify the period in days")

        if self.product_type == Product.ProductType.QUANTITY and not self.quantity:
            raise ValidationError("For quantity-based products, you must specify the number of units")

        # Validation of metadata.features
        if self.metadata:
            features = self.metadata.get("features")
            if features is not None:
                if not isinstance(features, list):
                    raise ValidationError("metadata.features must be a list")
                if not all(isinstance(f, str) for f in features):
                    raise ValidationError("All elements of metadata.features must be strings")

    def get_display_name(self) -> str:
        """
        Returns the display name of the product.
        
        Returns:
            str: Display name with parameters.
        """
        if self.product_type == Product.ProductType.PERIOD:
            return f"{self.name} ({self.period_days} days)"
        elif self.product_type == Product.ProductType.QUANTITY:
            return f"{self.name} ({self.quantity} units)"
        else:
            return self.name

    def get_price_display(self) -> str:
        """
        Returns the display price of the product.
        
        Returns:
            str: Price with currency.
        """
        return f"{self.price} {self.currency}"

    def is_trial(self) -> bool:
        """
        Checks if the product is a trial.
        
        Returns:
            bool: True if the product is a trial.
        """
        return "trial" in self.name.lower() or "starter" in self.name.lower()

    def has_feature(self, feature_name: str) -> bool:
        """
        Checks if the product has a specific feature.
        
        Args:
            feature_name: Feature name to check
            
        Returns:
            bool: True if the feature exists in metadata.features
        """
        features = self.metadata.get("features", [])
        return feature_name in features

    def is_resume_lift_service(self) -> bool:
        """
        Checks if the service is a resume boost.
        
        Returns:
            bool: True if the product includes the resume_lift feature
        """
        return self.has_feature("resume_lift")


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
        settings.AUTH_USER_MODEL,
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
        return f"Order #{self.id} - {self.user.chat_id} ({self.total_amount} {self.currency})"

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

    def get_user_products(self):
        """
        Gets user products associated with the order.
        
        Returns:
            QuerySet[UserProduct]: User products associated with the order.
        """
        return UserProduct.objects.filter(order_item__order=self).select_related("user", "product", "order_item")

    def get_order_items(self):
        """
        Gets order items with products.
        
        Returns:
            QuerySet[OrderItem]: Order items with loaded products.
        """
        return self.items.select_related("product").all()

    def get_products(self) -> list[Product]:
        """
        Gets all products in the order.
        
        Returns:
            list[Product]: List of all products in the order.
        """
        return [item.product for item in self.items.all()]

    def get_first_product(self) -> Product | None:
        """
        Gets the first product in the order (for single product orders).
        
        Returns:
            Product|None: First product or None if the order is empty.
        """
        order_item = self.items.select_related("product").first()
        return order_item.product if order_item else None


class OrderItem(models.Model):
    """
    Position in the order.
    """

    # Relationship with order and product
    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="items",
        verbose_name="Order",
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        verbose_name="Product",
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

    # Fields for quantity-based products
    total_quantity = models.IntegerField(
        null=True,
        blank=True,
        verbose_name="Total Quantity",
    )

    # Fields for period-based products
    period_days = models.IntegerField(
        null=True,
        blank=True,
        verbose_name="Period in days",
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
        return f"{self.product.name} x{self.quantity} - {self.price} {self.order.currency}"

    def get_user_products(self):
        """
        Gets user products associated with an order item.
        
        Returns:
            QuerySet[UserProduct]: User products associated with the position.
        """
        return UserProduct.objects.filter(order_item=self).select_related("user", "product")


class UserProduct(models.Model):
    """
    Active user product.
    
    Stores information about products purchased by the user
    and their current usage status.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        verbose_name="User",
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        verbose_name="Product",
    )
    # Relationship with order item
    order_item = models.ForeignKey(
        OrderItem,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        verbose_name="Order Item",
    )

    # General fields for all product types
    purchased_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="Purchase Date",
    )
    expires_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Expiration Date",
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name="Active",
    )

    # Fields for quantity-based products
    total_quantity = models.IntegerField(
        null=True,
        blank=True,
        verbose_name="Total Quantity",
    )
    used_quantity = models.IntegerField(
        default=0,
        verbose_name="Used quantity",
    )

    # Fields for period-based products
    period_start = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Period Start",
    )
    period_end = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Period End",
    )

    class Meta:
        db_table = "billable_user_products"
        verbose_name = "User Product"
        verbose_name_plural = "User Products"
        unique_together = ["user", "product", "order_item"]
        ordering = ["-purchased_at"]
        indexes = [
            models.Index(fields=["user", "product", "is_active"], name="billable_up_prod_active_idx"),
            models.Index(fields=["user", "is_active"], name="billable_up_user_active_idx"),
            models.Index(fields=["is_active"], name="billable_up_is_active_idx"),
        ]

    def __str__(self) -> str:
        """Return human-readable representation."""
        return f"{self.user.chat_id} - {self.product.name}"

    def clean(self) -> None:
        """User product fields validation."""
        if self.product.product_type == Product.ProductType.QUANTITY and not self.total_quantity:
            raise ValidationError("For quantity-based products, you must specify the total quantity")

    def is_expired(self) -> bool:
        """
        Checks if the product has expired.
        
        Returns:
            bool: True if the product has expired.
        """
        if self.product.product_type == Product.ProductType.PERIOD:
            expired = bool(self.expires_at and timezone.now() > self.expires_at)
            # Lazy deactivation on access, to sync flag with actual status
            if expired and self.is_active:
                try:
                    self.is_active = False
                    self.save(update_fields=["is_active"])
                except Exception:
                    # Do not escalate errors in model
                    pass
            return expired
        return False

    def can_use(self) -> bool:
        """
        Checks if the product can be used.
        
        Returns:
            bool: True if the product can be used.
        """
        if not self.is_active:
            return False

        if self.product.product_type == Product.ProductType.QUANTITY:
            return self.used_quantity < self.total_quantity
        elif self.product.product_type == Product.ProductType.PERIOD:
            return not self.is_expired()
        else:  # unlimited
            return True

    def get_remaining_quantity(self) -> int | None:
        """
        Returns the remaining quantity for quantity-based products.
        
        Returns:
            int|None: Remaining quantity or None for other product types.
        """
        if self.product.product_type == Product.ProductType.QUANTITY:
            return max(0, self.total_quantity - self.used_quantity)
        return None

    def get_days_left(self) -> int | None:
        """
        Returns the number of days until expiration for period-based products.
        
        Returns:
            int|None: Number of days until expiration or None for other product types.
        """
        if self.product.product_type == Product.ProductType.PERIOD and self.expires_at:
            delta = self.expires_at - timezone.now()
            return max(0, delta.days)
        return None


class ProductUsage(models.Model):
    """
    Record of a product usage by the user.
    
    Stores detailed information about each usage of a product
    for analytics and history tracking.
    """

    # User and user product relationships
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        verbose_name="User",
    )
    user_product = models.ForeignKey(
        UserProduct,
        on_delete=models.CASCADE,
        verbose_name="User Product",
    )

    # Action type and identifier fields
    action_type = models.CharField(
        max_length=50,
        verbose_name="Action Type",
    )
    action_id = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        verbose_name="Action ID",
    )

    # Usage date field
    used_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="Usage Date",
    )

    # Metadata field
    # Application IDs (report_id, vacancy_response_id, etc.) are stored here
    metadata = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="Additional Data",
        help_text="Stores application IDs (report_id, vacancy_response_id) and other data",
    )

    class Meta:
        db_table = "billable_product_usages"
        verbose_name = "Product Usage"
        verbose_name_plural = "Product Usages"
        ordering = ["-used_at"]
        indexes = [
            models.Index(fields=["user", "used_at"], name="billable_pu_user_used_at_idx"),
            models.Index(fields=["user_product", "used_at"], name="billable_pu_prod_used_at_idx"),
            models.Index(fields=["used_at"], name="billable_pu_used_at_idx"),
            models.Index(fields=["action_type"], name="billable_pu_action_type_idx"),
        ]

    def __str__(self) -> str:
        """Return human-readable representation."""
        return f"{self.user.chat_id} - {self.user_product.product.name} - {self.action_type}"


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
    async def has_used_trial_async(cls, identities: dict[str, str | int | None] | None = None, **kwargs) -> bool:
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


class Referral(models.Model):
    """
    Referral program model.
    
    Tracks links between the inviter (referrer) and the invitee (referee)
    users, as well as the bonus accrual status.
    """

    referrer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="referrals_made",
        verbose_name="Inviter",
        help_text="User who invited another user",
    )
    referee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
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
        return f"{self.referrer.chat_id} → {self.referee.chat_id} (bonus: {bonus_status})"

    def grant_bonus(self) -> None:
        """
        Marks bonus as granted.
        
        Sets bonus_granted=True and bonus_granted_at=now().
        """
        if not self.bonus_granted:
            self.bonus_granted = True
            self.bonus_granted_at = timezone.now()
            self.save(update_fields=["bonus_granted", "bonus_granted_at"])
