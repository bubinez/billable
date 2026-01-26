"""Django admin registrations for billable models.

Provides admin interfaces for Product, UserProduct, Order, OrderItem, ProductUsage, Referral and TrialHistory.
"""

from __future__ import annotations

from django.contrib import admin

from .models import Order, OrderItem, Product, ProductUsage, Referral, TrialHistory, UserProduct


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    """Admin configuration for Product."""

    list_display = ("id", "sku", "name", "product_type", "price", "currency", "is_active", "created_at")
    list_filter = ("product_type", "is_active", "created_at")
    search_fields = ("sku", "name", "description")
    readonly_fields = ("created_at",)


class OrderItemInline(admin.TabularInline):
    """Inline for displaying order items."""

    model = OrderItem
    extra = 0
    fields = ("product", "quantity", "price", "total_quantity", "period_days")
    readonly_fields = ()
    raw_id_fields = ("product",)


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    """Admin configuration for Order."""

    list_display = ("id", "user", "total_amount", "currency", "status", "payment_method", "created_at", "paid_at")
    list_filter = ("status", "payment_method", "created_at")
    search_fields = ("id", "user__chat_id", "payment_id")
    readonly_fields = ("created_at",)
    date_hierarchy = "created_at"
    inlines = (OrderItemInline,)


@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    """Admin configuration for OrderItem."""

    list_display = ("id", "order", "product", "quantity", "price", "total_quantity", "period_days")
    list_filter = ("order__status",)
    search_fields = ("order__id", "product__name")
    raw_id_fields = ("order", "product")


@admin.register(UserProduct)
class UserProductAdmin(admin.ModelAdmin):
    """Admin configuration for UserProduct."""

    list_display = (
        "id",
        "user",
        "product",
        "is_active",
        "purchased_at",
        "expires_at",
        "total_quantity",
        "used_quantity",
    )
    list_filter = ("is_active", "product__product_type", "purchased_at")
    search_fields = ("user__chat_id", "product__name")
    readonly_fields = ("purchased_at",)
    raw_id_fields = ("user", "product", "order_item")
    date_hierarchy = "purchased_at"


@admin.register(ProductUsage)
class ProductUsageAdmin(admin.ModelAdmin):
    """Admin configuration for ProductUsage."""

    list_display = ("id", "user", "user_product", "action_type", "action_id", "used_at")
    list_filter = ("action_type", "used_at")
    search_fields = ("user__chat_id", "action_type", "action_id")
    readonly_fields = ("used_at",)
    raw_id_fields = ("user", "user_product")
    date_hierarchy = "used_at"


@admin.register(TrialHistory)
class TrialHistoryAdmin(admin.ModelAdmin):
    """Admin configuration for TrialHistory."""

    list_display = ("id", "identity_type", "identity_hash_display", "trial_plan_name", "used_at", "created_at")
    list_filter = ("identity_type", "trial_plan_name", "used_at", "created_at")
    search_fields = ("identity_type", "identity_hash", "trial_plan_name")
    readonly_fields = ("identity_hash", "used_at", "created_at")
    date_hierarchy = "used_at"

    def identity_hash_display(self, obj):
        """Truncated hash for display."""
        return f"{obj.identity_hash[:8]}..."
    identity_hash_display.short_description = "Hash"


@admin.register(Referral)
class ReferralAdmin(admin.ModelAdmin):
    """Admin configuration for Referral."""

    list_display = ("id", "referrer", "referee", "bonus_granted", "bonus_granted_at", "created_at")
    list_filter = ("bonus_granted", "created_at", "bonus_granted_at")
    search_fields = ("referrer__chat_id", "referee__chat_id")
    readonly_fields = ("created_at", "bonus_granted_at")
    raw_id_fields = ("referrer", "referee")
    date_hierarchy = "created_at"
    actions = ["grant_bonuses"]

    def grant_bonuses(self, request, queryset):
        """Mass granting of bonuses for selected referrals."""
        count = 0
        for referral in queryset.filter(bonus_granted=False):
            referral.grant_bonus()
            count += 1
        self.message_user(request, f"Granted bonuses: {count}")

    grant_bonuses.short_description = "Grant bonuses to selected referrals"
