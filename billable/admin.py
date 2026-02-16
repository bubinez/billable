"""Django admin registrations for billable models.

Provides admin interfaces for Product, Offer, OfferItem, QuotaBatch, Transaction, 
Order, OrderItem, Referral, TrialHistory and ExternalIdentity.
"""

from __future__ import annotations

import csv
import json
from decimal import Decimal
from io import TextIOWrapper

from django.contrib import admin
from django.contrib.contenttypes.admin import GenericTabularInline
from django.contrib.messages import constants as message_constants
from django.http import HttpResponse, HttpResponseRedirect
from django.urls import path, reverse
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


# Product export/import: all non-related fields (no offers link).
PRODUCT_IMPORT_EXPORT_FIELDS = (
    "product_key",
    "name",
    "description",
    "product_type",
    "is_active",
    "is_currency",
    "created_at",
    "metadata",
)

# Offer export/import: all non-related fields + product link columns.
OFFER_IMPORT_EXPORT_FIELDS = (
    "sku",
    "name",
    "price",
    "currency",
    "image",
    "description",
    "is_active",
    "created_at",
    "metadata",
)
OFFER_ITEMS_COLUMNS = ("product_key", "quantity", "period_unit", "period_value")
OFFER_CSV_HEADERS = OFFER_IMPORT_EXPORT_FIELDS + OFFER_ITEMS_COLUMNS


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    """Comprehensive Admin for Product and its Market Presence."""
    form = ProductAdminForm

    list_display = ("id", "product_key", "name", "product_type", "is_currency", "is_active", "created_at")
    actions = ["export_products_csv"]
    change_list_template = "admin/billable/product/change_list.html"
    list_filter = ("product_type", "is_currency", "is_active", "created_at")
    search_fields = ("product_key", "name", "description")
    readonly_fields = ("created_at", "active_offers", "product_report")

    def export_products_csv(self, request, queryset) -> HttpResponse:
        """Export selected products to CSV (all non-related fields, no offers)."""
        if not queryset.exists():
            queryset = Product.objects.all()
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="products_export.csv"'
        writer = csv.writer(response)
        writer.writerow(PRODUCT_IMPORT_EXPORT_FIELDS)
        for obj in queryset.order_by("id"):
            row = []
            for f in PRODUCT_IMPORT_EXPORT_FIELDS:
                val = getattr(obj, f)
                if f == "metadata":
                    val = json.dumps(val, ensure_ascii=False) if val else "{}"
                elif f == "created_at" and val is not None:
                    val = val.isoformat()
                elif val is None:
                    val = ""
                else:
                    val = str(val)
                row.append(val)
            writer.writerow(row)
        return response

    export_products_csv.short_description = _("Export selected products (CSV)")

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
                source_label = f'<a href="{order_url}">Order #{qb.order_item.order_id}</a>'
            elif qb.source_offer_id:
                offer_url = reverse("admin:billable_offer_change", args=[qb.source_offer_id])
                source_label = f'<a href="{offer_url}">{qb.source_offer.name}</a>'
            else:
                source_label = "Manual grant"

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
            "<th style='padding:8px;text-align:left;'>" + _("Batch") + "</th>"
            "<th style='padding:8px;text-align:left;'>" + _("User") + "</th>"
            "<th style='padding:8px;text-align:left;'>" + _("Source (how it appeared)") + "</th>"
            "<th style='padding:8px;text-align:left;'>" + _("Operation type") + "</th>"
            "<th style='padding:8px;text-align:right;'>" + _("Qty") + "</th>"
            "<th style='padding:8px;text-align:left;'>" + _("Date") + "</th></tr></thead><tbody>"
            + "".join(rows_sources) if rows_sources else "<tr><td colspan='6' style='padding:8px;'>" + _("No data") + "</td></tr>"
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
            "<p><strong>" + _("Spending (debits)") + "</strong></p>"
            "<table style='width:100%;border:1px solid var(--border-color);border-collapse:collapse;'>"
            "<thead style='background:var(--darkened-bg);'><tr>"
            "<th style='padding:8px;text-align:left;'>" + _("Transaction") + "</th>"
            "<th style='padding:8px;text-align:left;'>" + _("User") + "</th>"
            "<th style='padding:8px;text-align:right;'>" + _("Debited") + "</th>"
            "<th style='padding:8px;text-align:left;'>" + _("Operation type") + "</th>"
            "<th style='padding:8px;text-align:left;'>" + _("Date") + "</th></tr></thead><tbody>"
            + "".join(rows_debit) if rows_debit else "<tr><td colspan='5' style='padding:8px;'>" + _("No debits") + "</td></tr>"
            + "</tbody></table>"
        )

        report_html = (
            "<p><strong>" + _("Inflows (credits)") + "</strong></p>"
            + sources_table
            + debits_table
        )
        return mark_safe(report_html)

    product_report.short_description = _("Product report")

    def get_urls(self) -> list:
        """Add import and export URLs for Product admin."""
        urls = super().get_urls()
        return [
            path("import/", self.admin_site.admin_view(self.import_products_view), name="billable_product_import"),
            path("export/", self.admin_site.admin_view(self.export_products_all_view), name="billable_product_export"),
        ] + urls

    def export_products_all_view(self, request) -> HttpResponse:
        """Export all products as CSV (used by the 'Export CSV' link in list view)."""
        return self.export_products_csv(request, Product.objects.all())

    def import_products_view(self, request) -> HttpResponse | HttpResponseRedirect:
        """Import products from CSV: upsert by product_key; all non-related fields; offers not imported."""
        if request.method == "POST" and request.FILES.get("csv_file"):
            created = 0
            updated = 0
            errors = []
            f = TextIOWrapper(request.FILES["csv_file"].file, encoding="utf-8-sig")
            reader = csv.DictReader(f)
            if not reader.fieldnames or set(reader.fieldnames) != set(PRODUCT_IMPORT_EXPORT_FIELDS):
                self.message_user(
                    request,
                    _("CSV must have headers: %(headers)s") % {"headers": ", ".join(PRODUCT_IMPORT_EXPORT_FIELDS)},
                    level=message_constants.ERROR,
                )
                return HttpResponseRedirect(reverse("admin:billable_product_import"))
            for i, row in enumerate(reader, start=2):
                key_raw = (row.get("product_key") or "").strip()
                product_key = key_raw.upper() if key_raw else None
                try:
                    metadata_val = row.get("metadata", "{}").strip() or "{}"
                    metadata = json.loads(metadata_val)
                except json.JSONDecodeError:
                    errors.append(_("Row %(row)s: invalid metadata JSON") % {"row": i})
                    continue
                created_at_val = (row.get("created_at") or "").strip()
                created_at = None
                if created_at_val:
                    from django.utils.dateparse import parse_datetime
                    created_at = parse_datetime(created_at_val)
                product_type = (row.get("product_type") or "").strip()
                if product_type not in dict(Product.ProductType.choices):
                    errors.append(_("Row %(row)s: invalid product_type") % {"row": i})
                    continue
                is_active = (row.get("is_active") or "").strip().lower() in ("1", "true", "yes")
                is_currency = (row.get("is_currency") or "").strip().lower() in ("1", "true", "yes")
                product, was_created = Product.objects.get_or_create(
                    product_key=product_key,
                    defaults={
                        "name": (row.get("name") or "").strip() or "—",
                        "description": (row.get("description") or "").strip(),
                        "product_type": product_type,
                        "is_active": is_active,
                        "is_currency": is_currency,
                        "metadata": metadata,
                    },
                )
                if was_created:
                    created += 1
                else:
                    updated += 1
                product.name = (row.get("name") or "").strip() or product.name
                product.description = (row.get("description") or "").strip()
                product.product_type = product_type
                product.is_active = is_active
                product.is_currency = is_currency
                product.metadata = metadata
                product.save(update_fields=["name", "description", "product_type", "is_active", "is_currency", "metadata"])
                if created_at:
                    Product.objects.filter(pk=product.pk).update(created_at=created_at)
            for err in errors[:10]:
                self.message_user(request, err, level=message_constants.ERROR)
            if len(errors) > 10:
                self.message_user(
                    request,
                    _("%(count)s more errors.") % {"count": len(errors) - 10},
                    level=message_constants.ERROR,
                )
            self.message_user(
                request,
                _("Import finished: %(created)s created, %(updated)s updated.") % {"created": created, "updated": updated},
            )
            return HttpResponseRedirect(reverse("admin:billable_product_changelist"))
        context = {
            **self.admin_site.each_context(request),
            "title": _("Import products (CSV)"),
            "opts": self.model._meta,
        }
        from django.template.loader import render_to_string
        html = render_to_string(
            "admin/billable/product/import_form.html",
            context,
            request=request,
        )
        return HttpResponse(html)

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
        (_("📊 Product report: inflows and spending"), {
            "fields": ("product_report",),
            "description": _(
                "Inflows — quota batches and source (order, offer, manual grant). "
                "Spending — debits via transactions."
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
    actions = ["export_offers_csv"]
    change_list_template = "admin/billable/offer/change_list.html"

    def export_offers_csv(self, request, queryset) -> HttpResponse:
        """Export selected offers to CSV (all non-related fields + product links by product_key)."""
        if not queryset.exists():
            queryset = Offer.objects.all()
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="offers_export.csv"'
        writer = csv.writer(response)
        writer.writerow(OFFER_CSV_HEADERS)
        for offer in queryset.prefetch_related("items__product").order_by("id"):
            row_base = []
            for f in OFFER_IMPORT_EXPORT_FIELDS:
                val = getattr(offer, f)
                if f == "metadata":
                    val = json.dumps(val, ensure_ascii=False) if val else "{}"
                elif f == "created_at" and val is not None:
                    val = val.isoformat()
                elif f == "image":
                    val = (offer.image.name or "").strip()
                elif val is None:
                    val = ""
                else:
                    val = str(val)
                row_base.append(val)
            items = list(offer.items.select_related("product").all())
            if not items:
                writer.writerow(row_base + ["", "", "", ""])
            else:
                for item in items:
                    pk_val = (item.product.product_key or "").strip()
                    qty = item.quantity
                    pu = (item.period_unit or "").strip()
                    pv = item.period_value if item.period_value is not None else ""
                    writer.writerow(row_base + [pk_val, qty, pu, pv])
        return response

    export_offers_csv.short_description = _("Export selected offers (CSV)")

    def get_urls(self) -> list:
        """Add import and export URLs for Offer admin."""
        urls = super().get_urls()
        return [
            path("import/", self.admin_site.admin_view(self.import_offers_view), name="billable_offer_import"),
            path("export/", self.admin_site.admin_view(self.export_offers_all_view), name="billable_offer_export"),
        ] + urls

    def export_offers_all_view(self, request) -> HttpResponse:
        """Export all offers as CSV (used by the 'Export CSV' link in list view)."""
        return self.export_offers_csv(request, Offer.objects.all())

    def import_offers_view(self, request) -> HttpResponse | HttpResponseRedirect:
        """Import offers from CSV: upsert by sku; overwrite product links if any product_key in file; skip missing products with notification."""
        if request.method == "POST" and request.FILES.get("csv_file"):
            created = 0
            updated = 0
            errors = []
            skipped_products: list[str] = []
            links_overwritten: list[str] = []

            f = TextIOWrapper(request.FILES["csv_file"].file, encoding="utf-8-sig")
            reader = csv.DictReader(f)
            if not reader.fieldnames or set(reader.fieldnames) != set(OFFER_CSV_HEADERS):
                self.message_user(
                    request,
                    _("CSV must have headers: %(headers)s") % {"headers": ", ".join(OFFER_CSV_HEADERS)},
                    level=message_constants.ERROR,
                )
                return HttpResponseRedirect(reverse("admin:billable_offer_import"))

            from django.utils.dateparse import parse_datetime

            rows = list(reader)
            by_sku: dict[str, list[dict]] = {}
            for row in rows:
                sku_raw = (row.get("sku") or "").strip()
                sku = sku_raw.upper() if sku_raw else None
                if not sku:
                    errors.append(_("Row with empty sku skipped."))
                    continue
                if sku not in by_sku:
                    by_sku[sku] = []
                by_sku[sku].append(row)

            for sku, group in by_sku.items():
                first = group[0]
                try:
                    metadata_val = (first.get("metadata") or "{}").strip() or "{}"
                    metadata = json.loads(metadata_val)
                except json.JSONDecodeError:
                    errors.append(_("Offer %(sku)s: invalid metadata JSON") % {"sku": sku})
                    continue
                created_at_val = (first.get("created_at") or "").strip()
                created_at = parse_datetime(created_at_val) if created_at_val else None
                price_val = (first.get("price") or "").strip()
                if not price_val:
                    errors.append(_("Offer %(sku)s: price is required") % {"sku": sku})
                    continue
                try:
                    price = Decimal(price_val)
                except (ValueError, TypeError):
                    errors.append(_("Offer %(sku)s: invalid price") % {"sku": sku})
                    continue

                offer, was_created = Offer.objects.get_or_create(
                    sku=sku,
                    defaults={
                        "name": (first.get("name") or "").strip() or "—",
                        "price": price,
                        "currency": (first.get("currency") or "").strip() or "USD",
                        "description": (first.get("description") or "").strip(),
                        "is_active": (first.get("is_active") or "").strip().lower() in ("1", "true", "yes"),
                        "metadata": metadata,
                    },
                )
                if was_created:
                    created += 1
                else:
                    updated += 1

                offer.name = (first.get("name") or "").strip() or offer.name
                offer.price = price
                offer.currency = (first.get("currency") or "").strip() or offer.currency
                offer.description = (first.get("description") or "").strip()
                offer.is_active = (first.get("is_active") or "").strip().lower() in ("1", "true", "yes")
                offer.metadata = metadata
                offer.save(update_fields=["name", "price", "currency", "description", "is_active", "metadata"])
                if created_at:
                    Offer.objects.filter(pk=offer.pk).update(created_at=created_at)

                has_any_product_key = any((r.get("product_key") or "").strip() for r in group)
                if has_any_product_key:
                    OfferItem.objects.filter(offer=offer).delete()
                    links_overwritten.append(sku)
                    for r in group:
                        pk_raw = (r.get("product_key") or "").strip()
                        product_key = pk_raw.upper() if pk_raw else None
                        if not product_key:
                            continue
                        product = Product.objects.filter(product_key=product_key).first()
                        if not product:
                            skipped_products.append(f"{product_key} (offer {sku})")
                            continue
                        qty_val = (r.get("quantity") or "").strip()
                        quantity = 1
                        if qty_val:
                            try:
                                quantity = max(1, int(qty_val))
                            except ValueError:
                                pass
                        period_unit = (r.get("period_unit") or "").strip()
                        if period_unit not in dict(OfferItem.PeriodUnit.choices):
                            period_unit = OfferItem.PeriodUnit.FOREVER
                        period_value = None
                        pv = (r.get("period_value") or "").strip()
                        if pv:
                            try:
                                period_value = max(0, int(pv))
                            except ValueError:
                                pass
                        OfferItem.objects.create(
                            offer=offer,
                            product=product,
                            quantity=quantity,
                            period_unit=period_unit,
                            period_value=period_value,
                        )

            for err in errors[:10]:
                self.message_user(request, err, level=message_constants.ERROR)
            if len(errors) > 10:
                self.message_user(
                    request,
                    _("%(count)s more errors.") % {"count": len(errors) - 10},
                    level=message_constants.ERROR,
                )
            self.message_user(
                request,
                _("Import finished: %(created)s created, %(updated)s updated.") % {"created": created, "updated": updated},
            )
            if links_overwritten:
                self.message_user(
                    request,
                    _("Links overwritten for offers: %(skus)s.") % {"skus": ", ".join(links_overwritten)},
                    level=message_constants.SUCCESS,
                )
            if skipped_products:
                self.message_user(
                    request,
                    _("Skipped (product not found in DB): %(keys)s.") % {"keys": ", ".join(skipped_products[:20])},
                    level=message_constants.WARNING,
                )
                if len(skipped_products) > 20:
                    self.message_user(
                        request,
                        _("%(count)s more skipped product keys.") % {"count": len(skipped_products) - 20},
                        level=message_constants.WARNING,
                    )
            return HttpResponseRedirect(reverse("admin:billable_offer_changelist"))

        from django.template.loader import render_to_string
        context = {
            **self.admin_site.each_context(request),
            "title": _("Import offers (CSV)"),
            "opts": self.model._meta,
        }
        html = render_to_string("admin/billable/offer/import_form.html", context, request=request)
        return HttpResponse(html)

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

    list_display = ("id", "user", "quota_batch", "amount_display", "direction", "action_type", "document_link", "created_at")
    list_filter = ("direction", "action_type", "created_at", "quota_batch__product")
    search_fields = ("user_id", "quota_batch__id", "id")
    readonly_fields = ("id", "created_at", "document_link", "balance_after")
    raw_id_fields = ("user", "quota_batch")
    date_hierarchy = "created_at"

    def amount_display(self, obj):
        color = "green" if obj.direction == Transaction.Direction.CREDIT else "red"
        prefix = "+" if obj.direction == Transaction.Direction.CREDIT else "-"
        return mark_safe(f'<span style="color: {color}; font-weight: bold;">{prefix}{obj.amount}</span>')
    amount_display.short_description = _("Amount")

    def balance_after(self, obj):
        """
        Placeholder for balance calculation. 
        In a real scenario, this would compute the running balance for the product.
        """
        return "-"
    balance_after.short_description = _("Balance")

    def document_link(self, obj):
        """Generates readable anchors for related documents."""
        if obj.direction == Transaction.Direction.CREDIT:
            # Try to find Order
            if obj.quota_batch and obj.quota_batch.order_item and obj.quota_batch.order_item.order:
                order = obj.quota_batch.order_item.order
                url = reverse("admin:billable_order_change", args=[order.pk])
                return mark_safe(f'<a href="{url}">Order #{order.pk} from {order.created_at.strftime("%d.%m.%Y")}</a>')
            
            if obj.action_type == "referral_bonus":
                return "Bonus: Referral"
            
            if obj.quota_batch:
                url = reverse("admin:billable_quotabatch_change", args=[obj.quota_batch.pk])
                return mark_safe(f'<a href="{url}">Batch #{str(obj.quota_batch.pk)[-12:]}</a>')
            
            # No batch, no order - just return empty (transaction link will be shown separately)
            return ""
        else:
            # Debit - check related_object
            rel_obj = obj.related_object
            
            if rel_obj:
                try:
                    # Try to get a nice name
                    name = str(rel_obj)
                    if hasattr(rel_obj, 'name'): name = rel_obj.name
                    elif hasattr(rel_obj, 'title'): name = rel_obj.title
                    
                    # Try to get admin URL
                    content_type = obj.content_type
                    url = reverse(f"admin:{content_type.app_label}_{content_type.model}_change", args=[obj.object_id])
                    
                    prefix = ""
                    if obj.action_type == "usage": prefix = "Usage"
                    elif obj.action_type == "refund": prefix = "Refund"
                    
                    return mark_safe(f'<a href="{url}">{prefix}: {name}</a>')
                except:
                    pass
            
            # Fallback: no related_object - return empty (batch link shown in batch header row)
            return ""
    
    document_link.short_description = _("Document")


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
    readonly_fields = ("technical_profile_link", "history_link", "products_list_view")
    inlines = ()

    def products_list_view(self, obj):
        """
        Renders Level 1 view: List of unique products (Active and History).
        """
        if not obj or not obj.pk:
            return _("Save customer first.")

        from django.utils import timezone
        now = timezone.now()

        # Get all products ever associated with the user
        all_batches = obj.quota_batches.select_related("product").all()
        
        products_data = {}
        for qb in all_batches:
            p_id = qb.product_id
            if p_id not in products_data:
                products_data[p_id] = {
                    "obj": qb.product,
                    "is_active": False,
                    "remaining": 0,
                    "expires": None
                }
            
            # Logic for active status
            is_qb_active = (
                qb.state == QuotaBatch.State.ACTIVE and 
                (qb.expires_at is None or qb.expires_at > now) and
                qb.remaining_quantity > 0
            )
            
            if is_qb_active:
                products_data[p_id]["is_active"] = True
                products_data[p_id]["remaining"] += qb.remaining_quantity
                if qb.expires_at:
                    if not products_data[p_id]["expires"] or qb.expires_at > products_data[p_id]["expires"]:
                        products_data[p_id]["expires"] = qb.expires_at

        active_rows = []
        history_rows = []

        for p_id, data in products_data.items():
            product = data["obj"]
            # Level 2 link: Custom report view
            report_url = reverse("admin:billable_customer_product_report", args=[obj.id, p_id])
            
            row = (
                f'<tr style="border-bottom: 1px solid var(--border-color);">'
                f'<td style="padding: 8px;">{product.name}</td>'
                f'<td style="padding: 8px; text-align: center;">{data["remaining"] if data["is_active"] else "-"}</td>'
                f'<td style="padding: 8px; text-align: center;">{data["expires"].strftime("%d.%m.%Y") if data["expires"] else "-"}</td>'
                f'<td style="padding: 8px; text-align: right;"><a href="{report_url}" class="button" style="padding: 2px 10px; font-size: 11px;">{_("Details")}</a></td>'
                f'</tr>'
            )
            
            if data["is_active"]:
                active_rows.append(row)
            else:
                history_rows.append(row)

        def build_table(rows, title):
            if not rows:
                return f"<p style='color: #666;'>{_('No data')}</p>"
            return (
                f'<h4>{title}</h4>'
                f'<table style="width:100%; border: 1px solid var(--border-color); border-collapse: collapse; margin-bottom: 20px;">'
                f'<thead style="background: var(--darkened-bg);">'
                f'<tr>'
                f'<th style="padding: 8px; text-align: left;">{_("Product")}</th>'
                f'<th style="padding: 8px; text-align: center;">{_("Balance")}</th>'
                f'<th style="padding: 8px; text-align: center;">{_("Expiry")}</th>'
                f'<th style="padding: 8px; text-align: right;">{_("Action")}</th>'
                f'</tr></thead><tbody>'
                f'{"".join(rows)}'
                f'</tbody></table>'
            )

        html = build_table(active_rows, _("Active products"))
        
        if history_rows:
            html += (
                f'<details style="margin-top: 10px; border: 1px solid var(--border-color); padding: 10px; border-radius: 4px;">'
                f'<summary style="cursor: pointer; font-weight: bold;">{_("History (Past products)")}</summary>'
                f'{build_table(history_rows, "")}'
                f'</details>'
            )
        
        return mark_safe(html)
    products_list_view.short_description = _("Products and subscriptions")

    fieldsets = (
        (None, {
            "fields": ("user_info_compact", "external_identities_links")
        }),
        (_("Products and subscriptions (Read-only)"), {
            "fields": ("products_list_view",),
        }),
        (_("🔗 Quick Links"), {
            "fields": ("technical_profile_link", "history_link"),
        }),
    )

    def user_info_compact(self, obj):
        """Displays user info in a compact, read-only format."""
        return mark_safe(
            f'<div style="font-size: 1.1em; font-weight: bold;">'
            f'{obj.get_full_name() or obj.username} '
            f'<span style="font-weight: normal; color: #666;">({obj.email})</span>'
            f'</div>'
        )
    user_info_compact.short_description = _("User Profile")

    def external_identities_links(self, obj):
        """Displays external identities as clickable tags."""
        identities = obj.billable_external_identities.all()
        if not identities:
            return "-"
        
        links = []
        for identity in identities:
            url = reverse("admin:billable_externalidentity_change", args=[identity.pk])
            links.append(
                f'<a href="{url}" style="display: inline-block; padding: 2px 8px; margin: 2px; '
                f'background: #79aec8; color: white; border-radius: 10px; text-decoration: none; font-size: 0.9em;">'
                f'{identity.provider}:{identity.external_id}</a>'
            )
        return mark_safe(" ".join(links))
    external_identities_links.short_description = _("Identities")

    def get_urls(self):
        """Add custom report URL."""
        from django.urls import path
        urls = super().get_urls()
        custom_urls = [
            path(
                '<path:object_id>/product-report/<int:product_id>/',
                self.admin_site.admin_view(self.product_usage_report_view),
                name='billable_customer_product_report',
            ),
        ]
        return custom_urls + urls

    def product_usage_report_view(self, request, object_id, product_id):
        """
        Level 2: Detailed transaction report for a specific product.
        Hierarchical by QuotaBatch groups.
        """
        from django.shortcuts import get_object_or_404
        from django.template.response import TemplateResponse
        from django.db.models import Sum
        from django.urls import reverse
        from collections import defaultdict
        
        customer = get_object_or_404(Customer, pk=object_id)
        product = get_object_or_404(Product, pk=product_id)
        
        # Get all transactions for this product and user
        transactions = Transaction.objects.filter(
            user=customer,
            quota_batch__product=product
        ).select_related(
            "quota_batch",
            "content_type",
            "quota_batch__order_item",
            "quota_batch__order_item__order",
            "quota_batch__source_offer"
        )
        
        # Group transactions by quota_batch
        transactions_by_batch = defaultdict(list)
        for tx in transactions:
            batch_id = tx.quota_batch_id if tx.quota_batch else None
            transactions_by_batch[batch_id].append(tx)
        
        # Get all unique batches with their metadata
        batch_ids = [bid for bid in transactions_by_batch.keys() if bid is not None]
        batches = QuotaBatch.objects.filter(
            id__in=batch_ids
        ).select_related(
            "order_item__order",
            "source_offer"
        )
        
        batches_dict = {batch.id: batch for batch in batches}
        
        # Prepare all rows with batch_id for grouping
        all_rows = []
        total_credit_qty = 0
        total_debit_qty = 0
        total_credit_cost = 0
        total_debit_cost = 0
        
        # Process all transactions and prepare rows
        for batch_id, batch_txs in transactions_by_batch.items():
            if batch_id is None:
                # Transactions without batch - skip for now or handle separately
                continue
                
            batch = batches_dict.get(batch_id)
            if not batch:
                continue
            
            batch_credit_qty = 0
            batch_debit_qty = 0
            batch_credit_cost = 0
            batch_debit_cost = 0
            
            for tx in batch_txs:
                # Determine the actual business date for the transaction
                tx_date = tx.created_at
                if batch.order_item and batch.order_item.order:
                    order = batch.order_item.order
                    if order.paid_at:
                        tx_date = order.paid_at

                # Calculate cost if it's an order
                cost = 0
                if batch.order_item:
                    item = batch.order_item
                    qb_initial = batch.initial_quantity
                    if qb_initial > 0:
                        cost = (item.price / qb_initial) * tx.amount

                if tx.direction == Transaction.Direction.CREDIT:
                    batch_credit_qty += tx.amount
                    batch_credit_cost += cost
                    credit = {"qty": tx.amount, "cost": cost}
                    debit = None
                else:
                    batch_debit_qty += tx.amount
                    batch_debit_cost += cost
                    credit = None
                    debit = {"qty": tx.amount, "cost": cost}
                
                doc_link = self.admin_site._registry[Transaction].document_link(tx)
                
                # Create transaction link (last 12 chars of GUID)
                tx_url = reverse("admin:billable_transaction_change", args=[tx.pk])
                transaction_link = mark_safe(f'<a href="{tx_url}">#{str(tx.pk)[-12:]}</a>')
                
                all_rows.append({
                    "batch_id": batch_id,
                    "date": tx_date,
                    "credit": credit,
                    "debit": debit,
                    "doc_link": doc_link,
                    "transaction_link": transaction_link,
                    "batch_totals": {
                        "credit_qty": batch_credit_qty,
                        "debit_qty": batch_debit_qty,
                        "credit_cost": batch_credit_cost,
                        "debit_cost": batch_debit_cost,
                    }
                })
            
            # Add to totals
            total_credit_qty += batch_credit_qty
            total_debit_qty += batch_debit_qty
            total_credit_cost += batch_credit_cost
            total_debit_cost += batch_debit_cost
        
        # Sort all rows by business date/time ascending (oldest first)
        all_rows.sort(key=lambda r: (r["date"], 0 if r["debit"] else 1))
        
        # Calculate running balance in chronological order (across all batches)
        running_balance = 0
        for row in all_rows:
            if row["credit"]:
                running_balance += row["credit"]["qty"]
            else:
                running_balance -= row["debit"]["qty"]
            row["balance"] = running_balance
        
        # Group rows back by batch BEFORE reversing
        rows_by_batch = defaultdict(list)
        for row in all_rows:
            batch_id = row.pop("batch_id")
            batch_totals = row.pop("batch_totals")
            rows_by_batch[batch_id].append(row)
        
        # Prepare batch groups
        batch_groups = []
        for batch_id, batch_rows in rows_by_batch.items():
            batch = batches_dict.get(batch_id)
            if not batch:
                continue
            
            # Sort rows within batch: first DEBIT (by date/time desc), then CREDIT (by date/time desc)
            # Debits appear first (above), credits appear after (below debits)
            # Both groups sorted by date/time descending (newest first within each group)
            # This matches the display order: newest transactions appear first
            from datetime import datetime
            batch_rows_sorted = sorted(
                batch_rows,
                key=lambda r: (
                    1 if r["credit"] else 0,  # Debits first (0), credits second (1)
                    r["date"] if isinstance(r["date"], datetime) else r["date"]  # For datetime comparison
                ),
                reverse=True  # Reverse to get descending order (newest first)
            )
            # But we need DEBIT first, so reverse only the date part, not the type part
            # So we sort by (type, -date) instead
            batch_rows_sorted = sorted(
                batch_rows,
                key=lambda r: (
                    1 if r["credit"] else 0,  # Debits first (0), credits second (1)
                    r["date"]  # Date for comparison
                ),
                reverse=False  # Normal order for type (DEBIT=0 first), but we'll handle date separately
            )
            # Now reverse date order within each type group
            # Group by type and sort each group by date descending
            debits = [r for r in batch_rows_sorted if r["debit"]]
            credits = [r for r in batch_rows_sorted if r["credit"]]
            debits.sort(key=lambda r: r["date"], reverse=True)  # Newest first
            credits.sort(key=lambda r: r["date"], reverse=True)  # Newest first
            batch_rows_sorted = debits + credits  # DEBIT first, then CREDIT
            
            # Recalculate balance within batch from bottom to top (matching display order)
            # Start from the last row (bottom) and go up
            batch_balance = 0
            for row in reversed(batch_rows_sorted):
                if row["credit"]:
                    batch_balance += row["credit"]["qty"]
                else:
                    batch_balance -= row["debit"]["qty"]
                row["balance"] = batch_balance
            
            # Calculate batch totals from rows
            batch_credit_qty = sum(r["credit"]["qty"] for r in batch_rows_sorted if r["credit"])
            batch_debit_qty = sum(r["debit"]["qty"] for r in batch_rows_sorted if r["debit"])
            batch_credit_cost = sum(r["credit"]["cost"] for r in batch_rows_sorted if r["credit"])
            batch_debit_cost = sum(r["debit"]["cost"] for r in batch_rows_sorted if r["debit"])
            
            # Balance is already calculated in the loop above, use the final value
            # This is the balance after all transactions in the batch (shown in the header)
            
            # Form batch header text with link to batch
            batch_date = batch.created_at.strftime("%d.%m.%Y")
            source_text = ""
            if batch.order_item and batch.order_item.order:
                order = batch.order_item.order
                source_text = f"Order #{order.id}"
            elif batch.source_offer:
                source_text = batch.source_offer.name
            else:
                source_text = "Manual grant"
            
            batch_url = reverse("admin:billable_quotabatch_change", args=[batch.id])
            batch_header = mark_safe(
                f'<a href="{batch_url}">{batch_date} {source_text}</a> — {batch.state}'
            )
            
            batch_groups.append({
                "batch": batch,
                "batch_header": batch_header,
                "rows": batch_rows_sorted,
                "batch_totals": {
                    "credit_qty": batch_credit_qty,
                    "debit_qty": batch_debit_qty,
                    "credit_cost": batch_credit_cost,
                    "debit_cost": batch_debit_cost,
                    "balance": batch_balance,
                }
            })
        
        # Sort batches by created_at desc (newest first)
        batch_groups.sort(key=lambda bg: bg["batch"].created_at, reverse=True)
        
        # Final balance is the running balance from the last transaction
        final_balance = running_balance
        
        totals = {
            "credit_qty": total_credit_qty,
            "debit_qty": total_debit_qty,
            "credit_cost": total_credit_cost,
            "debit_cost": total_debit_cost,
            "balance": final_balance,
        }
        
        context = {
            **self.admin_site.each_context(request),
            "title": f'{_("Product report")} "{product.name}"',
            "customer": customer,
            "product": product,
            "batch_groups": batch_groups,
            "totals": totals,
            "opts": self.model._meta,
        }
        
        return TemplateResponse(request, "admin/billable/customer/product_report.html", context)

    def get_readonly_fields(self, request, obj=None):
        """Make all base user fields readonly in this view."""
        return self.readonly_fields + ("username", "email", "first_name", "last_name", "user_info_compact", "external_identities_links")

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
        """Sum of remaining quantity across active, non-expired quota batches."""
        from django.utils import timezone
        from django.db.models import Sum
        now = timezone.now()
        result = (
            obj.quota_batches.filter(
                state=QuotaBatch.State.ACTIVE,
                remaining_quantity__gt=0
            )
            .filter(
                Q(expires_at__isnull=True) | Q(expires_at__gt=now)
            )
            .aggregate(total=Sum("remaining_quantity"))
        )
        total = result["total"] or 0
        return total if total else "-"
    active_quotas_count.short_description = _("Active Products (remaining)")

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
