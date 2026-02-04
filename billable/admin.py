"""Django admin registrations for billable models.

Provides admin interfaces for Product, Offer, OfferItem, QuotaBatch, Transaction, 
Order, OrderItem, Referral, TrialHistory and ExternalIdentity.
"""

from __future__ import annotations

from django.contrib import admin
from django.contrib.contenttypes.admin import GenericTabularInline
from django.urls import reverse
from django.utils.safestring import mark_safe

from .models import (
    ExternalIdentity, 
    Offer, 
    OfferItem, 
    Order, 
    OrderItem, 
    Product, 
    QuotaBatch, 
    Referral, 
    Transaction, 
    TrialHistory,
    Customer
)
from django.db.models import Exists, OuterRef, Q, Subquery
from django.utils.translation import gettext_lazy as _


class OfferItemInline(admin.TabularInline):
    """Inline for displaying products in an offer."""
    model = OfferItem
    extra = 1
    raw_id_fields = ("product",)


from django import forms
from django.utils.translation import gettext_lazy as _

class ProductAdminForm(forms.ModelForm):
    """Custom form for ProductAdmin with a Wizard-style Offer Builder."""
    
    offer_price = forms.DecimalField(
        required=False, 
        decimal_places=2, 
        max_digits=10, 
        label=_("New Sales Price"),
        help_text=_("Enter price here to CREATE A NEW SALES OFFER for this product. Leave empty if you just want to edit the product."),
        widget=forms.NumberInput(attrs={'placeholder': '0.00', 'style': 'width: 15rem; border: 2px solid #79aec8;'})
    )
    offer_currency = forms.CharField(
        max_length=10,
        required=False,
        label=_("Currency"),
        help_text=_("e.g. USD, EUR, XTR. History-enabled field."),
        widget=forms.TextInput(attrs={'placeholder': 'USD'})
    )
    offer_quantity = forms.IntegerField(
        required=False, 
        initial=1, 
        label=_("Units to Grant"),
        help_text=_("How many units of this product the user gets per purchase.")
    )
    offer_period_unit = forms.ChoiceField(
        choices=OfferItem.PeriodUnit.choices,
        initial=OfferItem.PeriodUnit.FOREVER,
        required=False,
        label=_("Access Duration")
    )
    offer_period_value = forms.IntegerField(
        required=False, 
        label=_("Period Value"),
        help_text=_("Required if Access Duration is not 'Forever'.")
    )

    class Meta:
        model = Product
        fields = "__all__"

    def clean(self):
        cleaned_data = super().clean()
        price = cleaned_data.get("offer_price")
        unit = cleaned_data.get("offer_period_unit")
        value = cleaned_data.get("offer_period_value")

        if price and price > 0:
            if unit != OfferItem.PeriodUnit.FOREVER and not value:
                self.add_error("offer_period_value", _("Please specify a value for the selected time period."))
        return cleaned_data


class QuickOfferPlacementInline(admin.TabularInline):
    """List of existing offers where this product is included."""
    model = OfferItem
    extra = 0
    verbose_name = _("Existing Placement")
    verbose_name_plural = _("🔗 CURRENT SALES CHANNELS (Where this product is sold)")
    raw_id_fields = ("offer",)
    fields = ("offer", "quantity", "period_unit", "period_value")
    readonly_fields = ("offer", "quantity", "period_unit", "period_value")


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    """Comprehensive Admin for Product and its Market Presence."""
    form = ProductAdminForm

    list_display = ("id", "product_key", "name", "product_type", "is_currency", "is_active", "created_at")
    list_filter = ("product_type", "is_currency", "is_active", "created_at")
    search_fields = ("product_key", "name", "description")
    readonly_fields = ("created_at", "active_offers", "product_report")

    def active_offers(self, obj):
        """Renders a clean table of existing offers for this product."""
        if not obj or not obj.id:
            return _("Save the product first to see its sales presence.")

        items = obj.offer_items.select_related('offer').all()
        if not items:
            return mark_safe(f"<span style='color: var(--error-fg);'>⚠️ {_('This product is not listed in any active offers yet.')}</span>")

        html = (
            '<table style="width:100%; border: 1px solid var(--border-color); border-collapse: collapse; background: var(--body-bg);">'
            '<thead style="background: var(--darkened-bg);">'
            '<tr>'
            '<th style="padding: 8px; text-align: left; border-bottom: 1px solid var(--border-color);">Offer Name</th>'
            '<th style="padding: 8px; text-align: center; border-bottom: 1px solid var(--border-color);">Price</th>'
            '<th style="padding: 8px; text-align: center; border-bottom: 1px solid var(--border-color);">Value to Grant</th>'
            '<th style="padding: 8px; text-align: right; border-bottom: 1px solid var(--border-color);">Actions</th>'
            '</tr></thead><tbody>'
        )
        for item in items:
            offer_url = f"../../offer/{item.offer.id}/change/"
            html += (
                f'<tr style="border-bottom: 1px solid var(--border-color);">'
                f'<td style="padding: 8px;"><b><a href="{offer_url}">{item.offer.name}</a></b></td>'
                f'<td style="padding: 8px; text-align: center;">{item.offer.price} {item.offer.currency}</td>'
                f'<td style="padding: 8px; text-align: center;">x{item.quantity} ({item.get_period_unit_display()})</td>'
                f'<td style="padding: 8px; text-align: right;"><a href="{offer_url}" class="button" style="padding: 2px 10px; font-size: 11px;">Edit Offer</a></td>'
                f'</tr>'
            )
        html += '</tbody></table>'
        return mark_safe(html)

    active_offers.short_description = _("Active Market Placements")

    def product_report(self, obj):
        """
        Renders two tables: how the product appeared (quota batches and sources)
        and how it was spent (DEBIT transactions).
        """
        if not obj or not obj.pk:
            return _("Save the product first to see the report.")

        batches = (
            QuotaBatch.objects.filter(product=obj)
            .select_related("user", "source_offer", "order_item", "order_item__order")
            .prefetch_related("transactions")
            .order_by("-created_at")
        )
        debits = (
            Transaction.objects.filter(quota_batch__product=obj, direction=Transaction.Direction.DEBIT)
            .select_related("user", "quota_batch")
            .order_by("-created_at")[:200]
        )

        rows_sources = []
        for qb in batches:
            credit_txs = [t for t in qb.transactions.all() if t.direction == Transaction.Direction.CREDIT]
            first_credit = min(credit_txs, key=lambda t: t.created_at) if credit_txs else None
            action_label = first_credit.action_type if first_credit else "—"

            if qb.order_item and qb.order_item.order_id:
                order_url = reverse("admin:billable_order_change", args=[qb.order_item.order_id])
                source_label = f'<a href="{order_url}">Заказ #{qb.order_item.order_id}</a>'
            elif qb.source_offer_id:
                offer_url = reverse("admin:billable_offer_change", args=[qb.source_offer_id])
                source_label = f'<a href="{offer_url}">{qb.source_offer.name}</a>'
            else:
                source_label = _("Ручное начисление")

            user_url = reverse("admin:billable_customer_change", args=[qb.user_id]) if qb.user_id else "#"
            user_link = f'<a href="{user_url}">{qb.user_id}</a>'
            qb_url = reverse("admin:billable_quotabatch_change", args=[qb.pk])
            rows_sources.append(
                f"<tr><td style='padding:6px;border-bottom:1px solid var(--border-color);'>"
                f"<a href='{qb_url}'>{str(qb.pk)[:8]}…</a></td>"
                f"<td style='padding:6px;border-bottom:1px solid var(--border-color);'>{user_link}</td>"
                f"<td style='padding:6px;border-bottom:1px solid var(--border-color);'>{source_label}</td>"
                f"<td style='padding:6px;border-bottom:1px solid var(--border-color);'>{action_label}</td>"
                f"<td style='padding:6px;border-bottom:1px solid var(--border-color);text-align:right;'>{qb.initial_quantity}</td>"
                f"<td style='padding:6px;border-bottom:1px solid var(--border-color);'>{qb.created_at.strftime('%Y-%m-%d %H:%M')}</td></tr>"
            )

        sources_table = (
            "<table style='width:100%;border:1px solid var(--border-color);border-collapse:collapse;margin-bottom:1.5rem;'>"
            "<thead style='background:var(--darkened-bg);'><tr>"
            "<th style='padding:8px;text-align:left;'>" + _("Батч") + "</th>"
            "<th style='padding:8px;text-align:left;'>" + _("Пользователь") + "</th>"
            "<th style='padding:8px;text-align:left;'>" + _("Источник (как появился)") + "</th>"
            "<th style='padding:8px;text-align:left;'>" + _("Тип операции") + "</th>"
            "<th style='padding:8px;text-align:right;'>" + _("Кол-во") + "</th>"
            "<th style='padding:8px;text-align:left;'>" + _("Дата") + "</th></tr></thead><tbody>"
            + "".join(rows_sources) if rows_sources else "<tr><td colspan='6' style='padding:8px;'>" + _("Нет данных") + "</td></tr>"
            + "</tbody></table>"
        )

        rows_debit = []
        for tx in debits:
            user_url = reverse("admin:billable_customer_change", args=[tx.user_id]) if tx.user_id else "#"
            user_link = f'<a href="{user_url}">{tx.user_id}</a>'
            tx_url = reverse("admin:billable_transaction_change", args=[tx.pk])
            rows_debit.append(
                f"<tr><td style='padding:6px;border-bottom:1px solid var(--border-color);'>"
                f"<a href='{tx_url}'>{str(tx.pk)[:8]}…</a></td>"
                f"<td style='padding:6px;border-bottom:1px solid var(--border-color);'>{user_link}</td>"
                f"<td style='padding:6px;border-bottom:1px solid var(--border-color);text-align:right;'>−{tx.amount}</td>"
                f"<td style='padding:6px;border-bottom:1px solid var(--border-color);'>{tx.action_type}</td>"
                f"<td style='padding:6px;border-bottom:1px solid var(--border-color);'>{tx.created_at.strftime('%Y-%m-%d %H:%M')}</td></tr>"
            )

        debits_table = (
            "<p><strong>" + _("Расход (списания)") + "</strong></p>"
            "<table style='width:100%;border:1px solid var(--border-color);border-collapse:collapse;'>"
            "<thead style='background:var(--darkened-bg);'><tr>"
            "<th style='padding:8px;text-align:left;'>" + _("Транзакция") + "</th>"
            "<th style='padding:8px;text-align:left;'>" + _("Пользователь") + "</th>"
            "<th style='padding:8px;text-align:right;'>" + _("Списано") + "</th>"
            "<th style='padding:8px;text-align:left;'>" + _("Тип операции") + "</th>"
            "<th style='padding:8px;text-align:left;'>" + _("Дата") + "</th></tr></thead><tbody>"
            + "".join(rows_debit) if rows_debit else "<tr><td colspan='5' style='padding:8px;'>" + _("Нет списаний") + "</td></tr>"
            + "</tbody></table>"
        )

        report_html = (
            "<p><strong>" + _("Появление (зачисления)") + "</strong></p>"
            + sources_table
            + debits_table
        )
        return mark_safe(report_html)

    product_report.short_description = _("Отчёт по продукту")

    fieldsets = (
        (_("📦 Technical DNA (The Product)"), {
            "fields": ("product_key", "name", "description", "product_type", "is_active"),
            "description": mark_safe("""
                <p style="color: #417690; font-weight: bold; margin-bottom: 10px;">
                    ℹ️ Product Key is automatically normalized to uppercase (CAPS) when saved.
                </p>
                <script>
                (function($) {
                    $(function() {
                        var $type = $('#id_product_type');
                        var $periodRows = $('.field-offer_period_unit, .field-offer_period_value');
                        function toggle() {
                            if ($type.val() === 'quantity') { $periodRows.hide(); } else { $periodRows.show(); }
                        }
                        $type.on('change', toggle);
                        toggle();
                    });
                })(django.jQuery);
                </script>
            """)
        }),
        (_("🔗 CURRENT SALE (Existing Placements)"), {
            "fields": ("active_offers",),
        }),
        (_("📊 Отчёт по продукту: как появился и как потратился"), {
            "fields": ("product_report",),
            "description": _(
                "Появление — батчи квот и источник (заказ, оффер, ручное начисление). "
                "Расход — списания по транзакциям."
            ),
        }),
        (_("🚀 DEPLOYMENT: Create a NEW Price Tag (Offer)"), {
            "description": _(
                "If you want to start selling this product at a specific price, fill the fields below. "
                "The system will generate a new Offer automatically upon saving."
            ),
            "fields": (
                ("offer_price", "offer_currency"),
                ("offer_quantity", "offer_period_unit", "offer_period_value"),
            )
        }),
        (_("⚙️ System Metadata"), {
            "classes": ("collapse",),
            "fields": ("metadata", "created_at"),
        }),
    )

    inlines = ()

    def save_model(self, request, obj, form, change):
        """Handle product save and optional automatic offer creation."""
        # Save product first
        super().save_model(request, obj, form, change)
        
        # Trigger Offer creation if price is provided
        price = form.cleaned_data.get("offer_price")
        if price and price > 0:
            currency = form.cleaned_data.get("offer_currency") or "USD"
            quantity = form.cleaned_data.get("offer_quantity") or 1
            period_unit = form.cleaned_data.get("offer_period_unit")
            period_value = form.cleaned_data.get("offer_period_value")

            # Create the Offer
            sku = f"GET_{obj.product_key}" if obj.product_key else f"GET_{obj.id}"
            
            # If an offer with this SKU already exists, we might want to update it 
            # or create one with a suffix. Given the PRD, system offers should have get_ prefix.
            base_sku = sku
            counter = 1
            while Offer.objects.filter(sku=sku).exists():
                sku = f"{base_sku}_{counter}"
                counter += 1

            offer = Offer.objects.create(
                sku=sku,
                name=obj.name,
                price=price,
                currency=currency,
                description=obj.description,
                is_active=True
            )
            
            # Link Product to Offer via OfferItem
            OfferItem.objects.create(
                offer=offer,
                product=obj,
                quantity=quantity,
                period_unit=period_unit,
                period_value=period_value
            )
            
            self.message_user(request, f"🚀 SUCCESS: New Offer '{offer.name}' has been deployed to the market.")


@admin.register(Offer)
class OfferAdmin(admin.ModelAdmin):
    """Admin configuration for Offer."""

    list_display = ("id", "sku", "name", "price", "currency", "is_active", "created_at")
    list_filter = ("currency", "is_active", "created_at")
    search_fields = ("sku", "name", "description")
    readonly_fields = ("id", "created_at")
    inlines = (OfferItemInline,)
    
    def get_form(self, request, obj=None, **kwargs):
        """Add help text about SKU normalization."""
        form = super().get_form(request, obj, **kwargs)
        if 'sku' in form.base_fields:
            form.base_fields['sku'].help_text = _(
                "SKU is automatically normalized to uppercase (CAPS) when saved. "
                "Example: 'test_offer' becomes 'TEST_OFFER'."
            )
        return form


@admin.register(QuotaBatch)
class QuotaBatchAdmin(admin.ModelAdmin):
    """Admin configuration for QuotaBatch."""

    list_display = (
        "id",
        "user",
        "product",
        "utilization",
        "state",
        "valid_from",
        "expires_at",
    )
    list_filter = ("state", "product", "created_at")
    search_fields = ("user_id", "product__name", "id")
    readonly_fields = ("id", "created_at")
    raw_id_fields = ("user", "product", "source_offer", "order_item")
    date_hierarchy = "created_at"

    def utilization(self, obj):
        """Displays remaining / initial quantity."""
        return f"{obj.remaining_quantity} / {obj.initial_quantity}"
    utilization.short_description = "Remaining / Total"


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    """Admin configuration for Transaction."""

    list_display = ("id", "user", "quota_batch", "amount", "direction", "action_type", "created_at")
    list_filter = ("direction", "action_type", "created_at")
    search_fields = ("user_id", "quota_batch__id", "id")
    readonly_fields = ("id", "created_at")
    raw_id_fields = ("user", "quota_batch")
    date_hierarchy = "created_at"


class OrderItemInline(admin.TabularInline):
    """Inline for displaying order items (offers)."""

    model = OrderItem
    extra = 0
    fields = ("offer", "quantity", "price")
    raw_id_fields = ("offer",)


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    """Admin configuration for Order."""

    list_display = ("id", "user", "total_amount", "currency", "status", "payment_method", "created_at", "paid_at")
    list_filter = ("status", "payment_method", "created_at")
    search_fields = ("id", "user_id", "payment_id")
    readonly_fields = ("created_at",)
    date_hierarchy = "created_at"
    raw_id_fields = ("user",)
    inlines = (OrderItemInline,)


@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    """Admin configuration for OrderItem."""

    list_display = ("id", "order", "offer", "quantity", "price")
    list_filter = ("order__status",)
    search_fields = ("order__id", "offer__name")
    raw_id_fields = ("order", "offer")


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


@admin.register(ExternalIdentity)
class ExternalIdentityAdmin(admin.ModelAdmin):
    """Admin configuration for ExternalIdentity."""

    list_display = ("id", "provider", "external_id", "user", "created_at", "updated_at")
    list_filter = ("provider", "created_at", "updated_at")
    search_fields = ("provider", "external_id", "user_id")
    readonly_fields = ("created_at", "updated_at")
    raw_id_fields = ("user",)
    date_hierarchy = "created_at"


@admin.register(Referral)
class ReferralAdmin(admin.ModelAdmin):
    """Admin configuration for Referral."""

    list_display = ("id", "referrer", "referee", "bonus_granted", "bonus_granted_at", "created_at")
    list_filter = ("bonus_granted", "created_at", "bonus_granted_at")
    search_fields = ("referrer_id", "referee_id")
    readonly_fields = ("created_at", "bonus_granted_at")
    raw_id_fields = ("referrer", "referee")
    date_hierarchy = "created_at"


class ActiveQuotaBatchForm(forms.ModelForm):
    """Custom form for QuotaBatch inline to require a reason for manual changes."""
    manual_reason = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'rows': 2, 'placeholder': _('Enter reason for manual adjustment...')}),
        label=_("Manual Adjustment Reason"),
        help_text=_("Required when adding a new product batch manually.")
    )

    class Meta:
        model = QuotaBatch
        fields = "__all__"

    def clean(self):
        cleaned_data = super().clean()
        if not self.instance.pk and not cleaned_data.get("manual_reason"):
            self.add_error("manual_reason", _("Please specify a reason for this manual grant."))
        return cleaned_data


class ActiveQuotaBatchInline(admin.TabularInline):
    """Inline for active quotas with manual adjustment tracking."""
    model = QuotaBatch
    form = ActiveQuotaBatchForm
    extra = 0
    fields = ("product", "initial_quantity", "remaining_quantity", "valid_from", "expires_at", "state", "manual_reason")
    raw_id_fields = ("product", "source_offer", "order_item")
    
    def get_queryset(self, request):
        """Show only active and non-expired batches in the main view."""
        from django.utils import timezone
        now = timezone.now()
        return super().get_queryset(request).filter(
            state=QuotaBatch.State.ACTIVE
        ).filter(
            Q(expires_at__isnull=True) | Q(expires_at__gt=now)
        )

    def has_add_permission(self, request, obj) -> bool:
        return True

@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    """
    Dedicated Admin for Billable Customers (Proxy Users).
    Focuses on products, balances and external identities.
    """
    list_display = ("username", "email", "get_external_ids", "active_quotas_count")
    search_fields = ("username", "email", "first_name", "last_name")
    readonly_fields = ("technical_profile_link", "history_link")
    inlines = (ActiveQuotaBatchInline,)

    fieldsets = (
        (None, {
            "fields": ("username", "email", "first_name", "last_name", "is_active")
        }),
        (_("🔗 Quick Links"), {
            "fields": ("technical_profile_link", "history_link"),
        }),
    )

    def get_queryset(self, request):
        """
        Filter to show ONLY users who have at least one billable record.
        Optimized via Exists subquery (12a).
        """
        qs = super().get_queryset(request)
        
        # Subqueries for existence in billing tables
        has_quota = QuotaBatch.objects.filter(user=OuterRef("pk"))
        has_order = Order.objects.filter(user=OuterRef("pk"))
        has_tx = Transaction.objects.filter(user=OuterRef("pk"))
        
        return qs.filter(
            Exists(has_quota) | Exists(has_order) | Exists(has_tx)
        ).distinct()

    def get_external_ids(self, obj):
        """List all external identities (13b)."""
        identities = obj.billable_external_identities.all()
        if not identities:
            return "-"
        return ", ".join([f"{i.provider}:{i.external_id}" for i in identities])
    get_external_ids.short_description = _("External Identities")

    def active_quotas_count(self, obj):
        """Quick counter for the list view."""
        return obj.quota_batches.filter(state=QuotaBatch.State.ACTIVE).count()
    active_quotas_count.short_description = _("Active Products")

    def history_link(self, obj):
        """Link to all quota batches (active and archive) for this user (16a)."""
        if not obj or not obj.id:
            return "-"
        from django.urls import reverse
        from django.utils.html import format_html
        
        url = reverse("admin:billable_quotabatch_changelist")
        return format_html(
            '<a href="{}?user__id__exact={}" class="button">{}</a>',
            url, obj.id, _("View All Quota History")
        )
    history_link.short_description = _("Quota Archive")

    def technical_profile_link(self, obj):
        """Link to the standard Django User admin (2d)."""
        if not obj or not obj.id:
            return "-"
        from django.urls import reverse
        # Try to find the user change URL
        try:
            url = reverse("admin:auth_user_change", args=[obj.id])
            return mark_safe(f'<a href="{url}">{_("Open Technical User Profile")}</a>')
        except:
            return _("Standard user admin not available")
    technical_profile_link.short_description = _("System Profile")

    def save_formset(self, request, form, formset, change):
        """
        Handle manual QuotaBatch creation and log Transaction (17c).
        """
        # We need to save the instances and also handle the custom field from the form
        for f in formset.forms:
            if f.cleaned_data and not f.cleaned_data.get('DELETE', False):
                instance = f.save(commit=False)
                is_new = instance.pk is None
                
                if is_new:
                    reason = f.cleaned_data.get("manual_reason", "Manual grant via Customer Admin")
                    instance.save()
                    
                    Transaction.objects.create(
                        user=instance.user,
                        quota_batch=instance,
                        amount=instance.initial_quantity,
                        direction=Transaction.Direction.CREDIT,
                        action_type="manual_grant",
                        metadata={
                            "admin_id": request.user.id,
                            "reason": reason,
                            "note": "Created by administrator"
                        }
                    )
                else:
                    instance.save()
        formset.save_m2m()
