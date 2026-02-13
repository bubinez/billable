"""Tests for Product admin CSV export and import."""

from __future__ import annotations

import csv
from io import StringIO
from unittest.mock import patch

import pytest
from django.contrib.admin.sites import AdminSite
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import HttpRequest
from django.test import RequestFactory

from billable.admin import PRODUCT_IMPORT_EXPORT_FIELDS, ProductAdmin
from billable.models import Offer, OfferItem, Product


def _make_csv_row(
    product_key: str = "",
    name: str = "",
    description: str = "",
    product_type: str = "quantity",
    is_active: str = "True",
    is_currency: str = "False",
    created_at: str = "",
    metadata: str = "{}",
) -> dict[str, str]:
    """Build a dict for one CSV row (keys = PRODUCT_IMPORT_EXPORT_FIELDS)."""
    return {
        "product_key": product_key,
        "name": name,
        "description": description,
        "product_type": product_type,
        "is_active": is_active,
        "is_currency": is_currency,
        "created_at": created_at,
        "metadata": metadata,
    }


def _csv_bytes(rows: list[dict[str, str]]) -> bytes:
    """Turn list of row dicts into CSV bytes (UTF-8) with correct headers."""
    buf = StringIO()
    writer = csv.DictWriter(buf, fieldnames=PRODUCT_IMPORT_EXPORT_FIELDS)
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8")


@pytest.mark.django_db
class TestProductExportCsv:
    """Tests for Product admin export_products_csv action."""

    def test_export_selected_products_contains_headers_and_data(self) -> None:
        """Export returns CSV with expected headers and one row per product."""
        p1 = Product.objects.create(
            product_key="KEY_A",
            name="Product A",
            description="Desc A",
            product_type=Product.ProductType.QUANTITY,
            is_active=True,
            is_currency=False,
            metadata={"x": 1},
        )
        site = AdminSite()
        admin = ProductAdmin(Product, site)
        request = RequestFactory().get("/")
        admin.message_user = lambda req, msg, level=None, extra_tags=None, fail_silently=False: None

        response = admin.export_products_csv(request, Product.objects.filter(pk=p1.pk))

        assert response["Content-Type"] == "text/csv"
        assert "products_export.csv" in response["Content-Disposition"]
        content = response.content.decode("utf-8")
        reader = csv.DictReader(StringIO(content))
        assert list(reader.fieldnames) == list(PRODUCT_IMPORT_EXPORT_FIELDS)
        rows = list(reader)
        assert len(rows) == 1
        assert rows[0]["product_key"] == "KEY_A"
        assert rows[0]["name"] == "Product A"
        assert rows[0]["description"] == "Desc A"
        assert rows[0]["product_type"] == "quantity"
        assert rows[0]["is_active"] == "True"
        assert rows[0]["is_currency"] == "False"
        assert rows[0]["metadata"] == '{"x": 1}'
        assert rows[0]["created_at"]  # ISO format

    def test_export_empty_queryset_exports_all_products(self) -> None:
        """When no selection, export exports all products."""
        Product.objects.create(
            product_key="ALL_ONE",
            name="One",
            product_type=Product.ProductType.QUANTITY,
        )
        Product.objects.create(
            product_key="ALL_TWO",
            name="Two",
            product_type=Product.ProductType.PERIOD,
        )
        site = AdminSite()
        admin = ProductAdmin(Product, site)
        request = RequestFactory().get("/")
        admin.message_user = lambda req, msg, level=None, extra_tags=None, fail_silently=False: None

        response = admin.export_products_csv(request, Product.objects.none())

        content = response.content.decode("utf-8")
        reader = csv.DictReader(StringIO(content))
        rows = list(reader)
        assert len(rows) == 2
        keys = {r["product_key"] for r in rows}
        assert keys == {"ALL_ONE", "ALL_TWO"}

    def test_export_created_at_iso_metadata_json(self) -> None:
        """Exported created_at is ISO format; metadata is JSON string."""
        p = Product.objects.create(
            product_key="META_KEY",
            name="Meta",
            product_type=Product.ProductType.QUANTITY,
            metadata={"a": "b", "n": 42},
        )
        site = AdminSite()
        admin = ProductAdmin(Product, site)
        request = RequestFactory().get("/")
        admin.message_user = lambda req, msg, level=None, extra_tags=None, fail_silently=False: None

        response = admin.export_products_csv(request, Product.objects.filter(pk=p.pk))
        content = response.content.decode("utf-8")
        reader = csv.DictReader(StringIO(content))
        rows = list(reader)
        assert len(rows) == 1
        assert "T" in rows[0]["created_at"]  # ISO has 'T'
        assert rows[0]["metadata"] == '{"a": "b", "n": 42}' or "42" in rows[0]["metadata"]


@pytest.mark.django_db
class TestProductImportCsv:
    """Tests for Product admin import_products_view."""

    def _import_request(self, csv_bytes: bytes) -> HttpRequest:
        request = RequestFactory().post(
            "/import/",
            data={},
            format="multipart",
        )
        request.FILES["csv_file"] = SimpleUploadedFile(
            "products.csv",
            csv_bytes,
            content_type="text/csv",
        )
        return request

    def test_import_creates_new_products(self) -> None:
        """Valid CSV creates new products; count and fields match."""
        rows = [
            _make_csv_row(product_key="IMP_NEW1", name="New One", product_type="quantity", is_active="true"),
            _make_csv_row(product_key="IMP_NEW2", name="New Two", product_type="period", is_currency="1"),
        ]
        site = AdminSite()
        admin = ProductAdmin(Product, site)
        messages: list[tuple[str, str | None]] = []
        admin.message_user = lambda req, msg, level=None, extra_tags=None, fail_silently=False: messages.append((msg, level))

        request = self._import_request(_csv_bytes(rows))
        with patch("billable.admin.reverse", return_value="/admin/billable/product/"):
            response = admin.import_products_view(request)

        assert response.status_code == 302
        assert Product.objects.filter(product_key="IMP_NEW1").exists()
        assert Product.objects.filter(product_key="IMP_NEW2").exists()
        p1 = Product.objects.get(product_key="IMP_NEW1")
        assert p1.name == "New One"
        assert p1.product_type == "quantity"
        assert p1.is_active is True
        p2 = Product.objects.get(product_key="IMP_NEW2")
        assert p2.is_currency is True
        success_msg = next((m for m in messages if m[0] and "2 created" in m[0] and "0 updated" in m[0]), None)
        assert success_msg is not None

    def test_import_updates_existing_by_product_key(self) -> None:
        """Row with existing product_key updates that product."""
        Product.objects.create(
            product_key="UPD_KEY",
            name="Old Name",
            description="Old desc",
            product_type=Product.ProductType.QUANTITY,
            is_active=True,
            is_currency=False,
        )
        rows = [
            _make_csv_row(
                product_key="UPD_KEY",
                name="New Name",
                description="New desc",
                product_type="unlimited",
                is_active="false",
                is_currency="yes",
                metadata='{"k": "v"}',
            ),
        ]
        site = AdminSite()
        admin = ProductAdmin(Product, site)
        admin.message_user = lambda req, msg, level=None, extra_tags=None, fail_silently=False: None

        request = self._import_request(_csv_bytes(rows))
        with patch("billable.admin.reverse", return_value="/admin/billable/product/"):
            admin.import_products_view(request)

        assert Product.objects.filter(product_key="UPD_KEY").count() == 1
        p = Product.objects.get(product_key="UPD_KEY")
        assert p.name == "New Name"
        assert p.description == "New desc"
        assert p.product_type == "unlimited"
        assert p.is_active is False
        assert p.is_currency is True
        assert p.metadata == {"k": "v"}

    def test_import_rejects_wrong_headers(self) -> None:
        """CSV with wrong or missing headers redirects to import with error."""
        wrong_headers = b"product_key,name\nKEY,X"
        site = AdminSite()
        admin = ProductAdmin(Product, site)
        messages: list[tuple[str, str | None]] = []
        admin.message_user = lambda req, msg, level=None, extra_tags=None, fail_silently=False: messages.append((msg, level))

        request = self._import_request(wrong_headers)
        with patch("billable.admin.reverse", side_effect=lambda x, **kw: "/import/" if "import" in x else "/admin/billable/product/"):
            response = admin.import_products_view(request)

        assert response.status_code == 302
        assert "import" in response.url
        assert any("CSV must have headers" in (m[0] or "") for m in messages)

    def test_import_invalid_product_type_skips_row_and_reports_error(self) -> None:
        """Row with invalid product_type adds error message and skips row."""
        rows = [
            _make_csv_row(product_key="BAD_TYPE", name="X", product_type="invalid_type"),
        ]
        site = AdminSite()
        admin = ProductAdmin(Product, site)
        messages: list[tuple[str, str | None]] = []
        admin.message_user = lambda req, msg, level=None, extra_tags=None, fail_silently=False: messages.append((msg, level))

        request = self._import_request(_csv_bytes(rows))
        with patch("billable.admin.reverse", return_value="/admin/billable/product/"):
            admin.import_products_view(request)

        assert not Product.objects.filter(product_key="BAD_TYPE").exists()
        assert any("invalid product_type" in (m[0] or "") for m in messages)

    def test_import_invalid_metadata_json_skips_row_and_reports_error(self) -> None:
        """Row with invalid metadata JSON skips row and reports error."""
        rows = [
            _make_csv_row(product_key="BAD_META", name="X", metadata="not json"),
        ]
        site = AdminSite()
        admin = ProductAdmin(Product, site)
        messages: list[tuple[str, str | None]] = []
        admin.message_user = lambda req, msg, level=None, extra_tags=None, fail_silently=False: messages.append((msg, level))

        request = self._import_request(_csv_bytes(rows))
        with patch("billable.admin.reverse", return_value="/admin/billable/product/"):
            admin.import_products_view(request)

        assert not Product.objects.filter(product_key="BAD_META").exists()
        assert any("invalid metadata" in (m[0] or "").lower() for m in messages)

    def test_import_does_not_change_offer_items(self) -> None:
        """Import updates product fields but does not add/remove offer links."""
        prod = Product.objects.create(
            product_key="WITH_OFFER",
            name="Has Offer",
            product_type=Product.ProductType.QUANTITY,
        )
        offer = Offer.objects.create(sku="OFF_WITH", name="Offer", price=10, currency="USD")
        item = OfferItem.objects.create(offer=offer, product=prod, quantity=1)
        initial_offer_item_count = OfferItem.objects.filter(product=prod).count()
        assert initial_offer_item_count == 1

        rows = [
            _make_csv_row(
                product_key="WITH_OFFER",
                name="Has Offer Updated",
                product_type="quantity",
            ),
        ]
        site = AdminSite()
        admin = ProductAdmin(Product, site)
        admin.message_user = lambda req, msg, level=None, extra_tags=None, fail_silently=False: None

        request = self._import_request(_csv_bytes(rows))
        with patch("billable.admin.reverse", return_value="/admin/billable/product/"):
            admin.import_products_view(request)

        p = Product.objects.get(product_key="WITH_OFFER")
        assert p.name == "Has Offer Updated"
        assert OfferItem.objects.filter(product=p).count() == initial_offer_item_count
        OfferItem.objects.get(pk=item.pk)
