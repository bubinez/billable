"""Tests for Offer admin CSV export and import, including OfferItem/Product relations."""

from __future__ import annotations

import csv
from io import StringIO
from unittest.mock import patch

import pytest
from django.contrib.admin.sites import AdminSite
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import HttpRequest
from django.test import RequestFactory

from billable.admin import OFFER_CSV_HEADERS, OfferAdmin
from billable.models import Offer, OfferItem, Product


def _make_offer_row(
    sku: str = "",
    name: str = "",
    price: str = "",
    currency: str = "USD",
    image: str = "",
    description: str = "",
    is_active: str = "True",
    created_at: str = "",
    metadata: str = "{}",
    product_key: str = "",
    quantity: str = "",
    period_unit: str = "",
    period_value: str = "",
) -> dict[str, str]:
    """Build a dict for one CSV row (keys = OFFER_CSV_HEADERS)."""
    return {
        "sku": sku,
        "name": name,
        "price": price,
        "currency": currency,
        "image": image,
        "description": description,
        "is_active": is_active,
        "created_at": created_at,
        "metadata": metadata,
        "product_key": product_key,
        "quantity": quantity,
        "period_unit": period_unit,
        "period_value": period_value,
    }


def _offer_csv_bytes(rows: list[dict[str, str]]) -> bytes:
    """Turn list of row dicts into CSV bytes (UTF-8) with OFFER_CSV_HEADERS."""
    buf = StringIO()
    writer = csv.DictWriter(buf, fieldnames=OFFER_CSV_HEADERS, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8")


@pytest.mark.django_db
class TestOfferExportCsv:
    """Tests for Offer admin export_offers_csv action."""

    def test_export_headers_and_one_offer_no_items(self) -> None:
        """Export returns CSV with expected headers; offer without items yields one row with empty product columns."""
        offer = Offer.objects.create(
            sku="EXP_SOLO",
            name="Solo Offer",
            price=99,
            currency="EUR",
            description="No products",
            is_active=True,
            metadata={"tag": "solo"},
        )
        site = AdminSite()
        admin = OfferAdmin(Offer, site)
        request = RequestFactory().get("/")
        admin.message_user = lambda req, msg, level=None, extra_tags=None, fail_silently=False: None

        response = admin.export_offers_csv(request, Offer.objects.filter(pk=offer.pk))

        assert response["Content-Type"] == "text/csv"
        assert "offers_export.csv" in response["Content-Disposition"]
        content = response.content.decode("utf-8")
        reader = csv.DictReader(StringIO(content))
        assert list(reader.fieldnames) == list(OFFER_CSV_HEADERS)
        rows = list(reader)
        assert len(rows) == 1
        assert rows[0]["sku"] == "EXP_SOLO"
        assert rows[0]["name"] == "Solo Offer"
        assert float(rows[0]["price"]) == 99
        assert rows[0]["currency"] == "EUR"
        assert rows[0]["description"] == "No products"
        assert rows[0]["metadata"] == '{"tag": "solo"}'
        assert rows[0]["product_key"] == ""
        assert rows[0]["quantity"] == ""
        assert rows[0]["period_unit"] == ""
        assert rows[0]["period_value"] == ""

    def test_export_offer_with_items_multiple_rows_per_offer(self) -> None:
        """One offer with two products yields two CSV rows; product_key and item fields match OfferItem."""
        p1 = Product.objects.create(
            product_key="PROD_ALPHA",
            name="Alpha",
            product_type=Product.ProductType.QUANTITY,
        )
        p2 = Product.objects.create(
            product_key="PROD_BETA",
            name="Beta",
            product_type=Product.ProductType.PERIOD,
        )
        offer = Offer.objects.create(
            sku="EXP_MULTI",
            name="Multi",
            price=50,
            currency="USD",
            is_active=True,
        )
        OfferItem.objects.create(offer=offer, product=p1, quantity=2, period_unit=OfferItem.PeriodUnit.FOREVER)
        OfferItem.objects.create(
            offer=offer,
            product=p2,
            quantity=1,
            period_unit=OfferItem.PeriodUnit.MONTHS,
            period_value=12,
        )

        site = AdminSite()
        admin = OfferAdmin(Offer, site)
        request = RequestFactory().get("/")
        admin.message_user = lambda req, msg, level=None, extra_tags=None, fail_silently=False: None

        response = admin.export_offers_csv(request, Offer.objects.filter(pk=offer.pk))
        content = response.content.decode("utf-8")
        reader = csv.DictReader(StringIO(content))
        rows = list(reader)
        assert len(rows) == 2
        assert all(r["sku"] == "EXP_MULTI" and r["name"] == "Multi" for r in rows)
        by_pk = {r["product_key"]: r for r in rows}
        assert "PROD_ALPHA" in by_pk
        assert "PROD_BETA" in by_pk
        assert by_pk["PROD_ALPHA"]["quantity"] == "2"
        assert by_pk["PROD_ALPHA"]["period_unit"] == "forever"
        assert by_pk["PROD_BETA"]["quantity"] == "1"
        assert by_pk["PROD_BETA"]["period_unit"] == "months"
        assert by_pk["PROD_BETA"]["period_value"] == "12"

    def test_export_empty_queryset_exports_all_offers(self) -> None:
        """When no selection, export exports all offers."""
        Offer.objects.create(sku="ALL_A", name="A", price=1, currency="USD")
        Offer.objects.create(sku="ALL_B", name="B", price=2, currency="EUR")
        site = AdminSite()
        admin = OfferAdmin(Offer, site)
        request = RequestFactory().get("/")
        admin.message_user = lambda req, msg, level=None, extra_tags=None, fail_silently=False: None

        response = admin.export_offers_csv(request, Offer.objects.none())
        content = response.content.decode("utf-8")
        reader = csv.DictReader(StringIO(content))
        rows = list(reader)
        assert len(rows) == 2
        assert {r["sku"] for r in rows} == {"ALL_A", "ALL_B"}

    def test_export_metadata_created_at_iso_image_path(self) -> None:
        """Exported metadata is JSON string; created_at is ISO; image is path or empty."""
        offer = Offer.objects.create(
            sku="META_OFF",
            name="Meta",
            price=10,
            currency="USD",
            metadata={"k": "v", "n": 1},
        )
        site = AdminSite()
        admin = OfferAdmin(Offer, site)
        request = RequestFactory().get("/")
        admin.message_user = lambda req, msg, level=None, extra_tags=None, fail_silently=False: None

        response = admin.export_offers_csv(request, Offer.objects.filter(pk=offer.pk))
        content = response.content.decode("utf-8")
        reader = csv.DictReader(StringIO(content))
        rows = list(reader)
        assert len(rows) == 1
        assert "T" in rows[0]["created_at"]
        assert "k" in rows[0]["metadata"] and "v" in rows[0]["metadata"]
        assert rows[0]["image"] == ""


@pytest.mark.django_db
class TestOfferImportCsv:
    """Tests for Offer admin import_offers_view."""

    def _import_request(self, csv_bytes: bytes) -> HttpRequest:
        request = RequestFactory().post("/import/", data={}, format="multipart")
        request.FILES["csv_file"] = SimpleUploadedFile(
            "offers.csv",
            csv_bytes,
            content_type="text/csv",
        )
        return request

    def test_import_creates_new_offer(self) -> None:
        """Valid CSV creates new offer; fields match."""
        rows = [
            _make_offer_row(
                sku="IMP_NEW",
                name="New Offer",
                price="19.99",
                currency="XTR",
                description="Desc",
                is_active="true",
                metadata='{"a": 1}',
            ),
        ]
        site = AdminSite()
        admin = OfferAdmin(Offer, site)
        messages: list[tuple[str, str | None]] = []
        admin.message_user = lambda req, msg, level=None, extra_tags=None, fail_silently=False: messages.append(
            (msg, level)
        )

        request = self._import_request(_offer_csv_bytes(rows))
        with patch("billable.admin.reverse", return_value="/admin/billable/offer/"):
            response = admin.import_offers_view(request)

        assert response.status_code == 302
        offer = Offer.objects.get(sku="IMP_NEW")
        assert offer.name == "New Offer"
        assert float(offer.price) == 19.99
        assert offer.currency == "XTR"
        assert offer.description == "Desc"
        assert offer.is_active is True
        assert offer.metadata == {"a": 1}
        assert any("1 created" in (m[0] or "") and "0 updated" in (m[0] or "") for m in messages)

    def test_import_updates_existing_by_sku(self) -> None:
        """Row with existing sku updates that offer (upsert)."""
        Offer.objects.create(
            sku="UPD_SKU",
            name="Old",
            price=5,
            currency="USD",
            is_active=True,
        )
        rows = [
            _make_offer_row(
                sku="UPD_SKU",
                name="Updated Name",
                price="15.50",
                currency="EUR",
                is_active="false",
                metadata="{}",
            ),
        ]
        site = AdminSite()
        admin = OfferAdmin(Offer, site)
        admin.message_user = lambda req, msg, level=None, extra_tags=None, fail_silently=False: None

        request = self._import_request(_offer_csv_bytes(rows))
        with patch("billable.admin.reverse", return_value="/admin/billable/offer/"):
            admin.import_offers_view(request)

        assert Offer.objects.filter(sku="UPD_SKU").count() == 1
        offer = Offer.objects.get(sku="UPD_SKU")
        assert offer.name == "Updated Name"
        assert offer.price == 15.5
        assert offer.currency == "EUR"
        assert offer.is_active is False

    def test_import_rejects_wrong_headers(self) -> None:
        """CSV with wrong headers redirects to import with error."""
        wrong = b"sku,name\nX,Y"
        site = AdminSite()
        admin = OfferAdmin(Offer, site)
        messages: list[tuple[str, str | None]] = []
        admin.message_user = lambda req, msg, level=None, extra_tags=None, fail_silently=False: messages.append(
            (msg, level)
        )

        request = self._import_request(wrong)
        with patch(
            "billable.admin.reverse",
            side_effect=lambda x, **kw: "/import/" if "import" in x else "/admin/billable/offer/",
        ):
            response = admin.import_offers_view(request)

        assert response.status_code == 302
        assert "import" in response.url
        assert any("CSV must have headers" in (m[0] or "") for m in messages)

    def test_import_invalid_metadata_skips_offer_and_reports_error(self) -> None:
        """Offer with invalid metadata JSON is skipped and error reported."""
        rows = [
            _make_offer_row(sku="BAD_META", name="X", price="1", metadata="not json"),
        ]
        site = AdminSite()
        admin = OfferAdmin(Offer, site)
        messages: list[tuple[str, str | None]] = []
        admin.message_user = lambda req, msg, level=None, extra_tags=None, fail_silently=False: messages.append(
            (msg, level)
        )

        request = self._import_request(_offer_csv_bytes(rows))
        with patch("billable.admin.reverse", return_value="/admin/billable/offer/"):
            admin.import_offers_view(request)

        assert not Offer.objects.filter(sku="BAD_META").exists()
        assert any("invalid metadata" in (m[0] or "").lower() for m in messages)

    def test_import_missing_price_reports_error(self) -> None:
        """Offer with empty price is skipped and error reported."""
        rows = [
            _make_offer_row(sku="NO_PRICE", name="X", price="", metadata="{}"),
        ]
        site = AdminSite()
        admin = OfferAdmin(Offer, site)
        messages: list[tuple[str, str | None]] = []
        admin.message_user = lambda req, msg, level=None, extra_tags=None, fail_silently=False: messages.append(
            (msg, level)
        )

        request = self._import_request(_offer_csv_bytes(rows))
        with patch("billable.admin.reverse", return_value="/admin/billable/offer/"):
            admin.import_offers_view(request)

        assert not Offer.objects.filter(sku="NO_PRICE").exists()
        assert any("price" in (m[0] or "").lower() for m in messages)

    def test_import_sku_normalized_to_uppercase(self) -> None:
        """Imported sku is stored uppercase."""
        rows = [
            _make_offer_row(sku="lower_sku", name="X", price="1", metadata="{}"),
        ]
        site = AdminSite()
        admin = OfferAdmin(Offer, site)
        admin.message_user = lambda req, msg, level=None, extra_tags=None, fail_silently=False: None

        request = self._import_request(_offer_csv_bytes(rows))
        with patch("billable.admin.reverse", return_value="/admin/billable/offer/"):
            admin.import_offers_view(request)

        assert Offer.objects.filter(sku="LOWER_SKU").exists()
        assert not Offer.objects.filter(sku="lower_sku").exists()


@pytest.mark.django_db
class TestOfferImportExportRelations:
    """Tests for Offer/OfferItem/Product table relations in export and import."""

    def test_import_one_offer_two_product_keys_creates_two_offer_items(self) -> None:
        """Import one offer with two rows (different product_key) creates one Offer and two OfferItems with correct Product FKs."""
        Product.objects.create(product_key="P1", name="Product 1", product_type=Product.ProductType.QUANTITY)
        Product.objects.create(product_key="P2", name="Product 2", product_type=Product.ProductType.QUANTITY)
        rows = [
            _make_offer_row(
                sku="REL_OFF",
                name="Rel Offer",
                price="10",
                product_key="P1",
                quantity="2",
                period_unit="forever",
            ),
            _make_offer_row(
                sku="REL_OFF",
                name="Rel Offer",
                price="10",
                product_key="P2",
                quantity="1",
                period_unit="months",
                period_value="6",
            ),
        ]
        site = AdminSite()
        admin = OfferAdmin(Offer, site)
        admin.message_user = lambda req, msg, level=None, extra_tags=None, fail_silently=False: None

        request = RequestFactory().post("/import/", data={}, format="multipart")
        request.FILES["csv_file"] = SimpleUploadedFile("offers.csv", _offer_csv_bytes(rows), content_type="text/csv")
        with patch("billable.admin.reverse", return_value="/admin/billable/offer/"):
            admin.import_offers_view(request)

        assert Offer.objects.filter(sku="REL_OFF").count() == 1
        offer = Offer.objects.get(sku="REL_OFF")
        items = list(offer.items.select_related("product").order_by("product__product_key"))
        assert len(items) == 2
        assert items[0].product.product_key == "P1"
        assert items[0].quantity == 2
        assert items[0].period_unit == OfferItem.PeriodUnit.FOREVER
        assert items[0].period_value is None
        assert items[1].product.product_key == "P2"
        assert items[1].quantity == 1
        assert items[1].period_unit == OfferItem.PeriodUnit.MONTHS
        assert items[1].period_value == 6

    def test_import_with_product_key_overwrites_existing_offer_items(self) -> None:
        """When file has at least one product_key for an offer, all existing OfferItems for that offer are replaced."""
        p_old = Product.objects.create(
            product_key="OLD_PROD",
            name="Old",
            product_type=Product.ProductType.QUANTITY,
        )
        p_new = Product.objects.create(
            product_key="NEW_PROD",
            name="New",
            product_type=Product.ProductType.QUANTITY,
        )
        offer = Offer.objects.create(sku="OVERWRITE", name="Offer", price=10, currency="USD")
        OfferItem.objects.create(offer=offer, product=p_old, quantity=99, period_unit=OfferItem.PeriodUnit.DAYS, period_value=7)
        assert offer.items.count() == 1

        rows = [
            _make_offer_row(
                sku="OVERWRITE",
                name="Offer",
                price="10",
                product_key="NEW_PROD",
                quantity="1",
                period_unit="forever",
            ),
        ]
        site = AdminSite()
        admin = OfferAdmin(Offer, site)
        admin.message_user = lambda req, msg, level=None, extra_tags=None, fail_silently=False: None

        request = RequestFactory().post("/import/", data={}, format="multipart")
        request.FILES["csv_file"] = SimpleUploadedFile("offers.csv", _offer_csv_bytes(rows), content_type="text/csv")
        with patch("billable.admin.reverse", return_value="/admin/billable/offer/"):
            admin.import_offers_view(request)

        offer.refresh_from_db()
        assert offer.items.count() == 1
        item = offer.items.get()
        assert item.product_id == p_new.pk
        assert item.quantity == 1
        assert not OfferItem.objects.filter(offer=offer, product=p_old).exists()

    def test_import_without_product_key_does_not_touch_offer_items(self) -> None:
        """When file has no product_key for an offer, only offer fields are updated; OfferItems remain unchanged."""
        p = Product.objects.create(
            product_key="KEEP_PROD",
            name="Keep",
            product_type=Product.ProductType.QUANTITY,
        )
        offer = Offer.objects.create(sku="KEEP_LINKS", name="Old Name", price=5, currency="USD")
        OfferItem.objects.create(offer=offer, product=p, quantity=3, period_unit=OfferItem.PeriodUnit.YEARS, period_value=1)
        initial_count = offer.items.count()
        assert initial_count == 1

        rows = [
            _make_offer_row(
                sku="KEEP_LINKS",
                name="New Name",
                price="20",
                currency="EUR",
                product_key="",
                quantity="",
                period_unit="",
                period_value="",
            ),
        ]
        site = AdminSite()
        admin = OfferAdmin(Offer, site)
        admin.message_user = lambda req, msg, level=None, extra_tags=None, fail_silently=False: None

        request = RequestFactory().post("/import/", data={}, format="multipart")
        request.FILES["csv_file"] = SimpleUploadedFile("offers.csv", _offer_csv_bytes(rows), content_type="text/csv")
        with patch("billable.admin.reverse", return_value="/admin/billable/offer/"):
            admin.import_offers_view(request)

        offer.refresh_from_db()
        assert offer.name == "New Name"
        assert offer.price == 20
        assert offer.currency == "EUR"
        assert offer.items.count() == initial_count
        item = offer.items.get()
        assert item.product.product_key == "KEEP_PROD"
        assert item.quantity == 3
        assert item.period_unit == OfferItem.PeriodUnit.YEARS
        assert item.period_value == 1

    def test_import_skipped_product_key_reported_and_no_item_created(self) -> None:
        """When product_key is not found in DB, that row is skipped and user is notified; no OfferItem for it."""
        Product.objects.create(product_key="EXISTS", name="Exists", product_type=Product.ProductType.QUANTITY)
        rows = [
            _make_offer_row(sku="SKIP_OFF", name="Offer", price="1", product_key="EXISTS", quantity="1"),
            _make_offer_row(sku="SKIP_OFF", name="Offer", price="1", product_key="MISSING_KEY", quantity="2"),
        ]
        site = AdminSite()
        admin = OfferAdmin(Offer, site)
        messages: list[tuple[str, str | None]] = []
        admin.message_user = lambda req, msg, level=None, extra_tags=None, fail_silently=False: messages.append(
            (msg, level)
        )

        request = RequestFactory().post("/import/", data={}, format="multipart")
        request.FILES["csv_file"] = SimpleUploadedFile("offers.csv", _offer_csv_bytes(rows), content_type="text/csv")
        with patch("billable.admin.reverse", return_value="/admin/billable/offer/"):
            admin.import_offers_view(request)

        offer = Offer.objects.get(sku="SKIP_OFF")
        assert offer.items.count() == 1
        assert offer.items.get().product.product_key == "EXISTS"
        assert any("MISSING_KEY" in (m[0] or "") and "SKIP_OFF" in (m[0] or "") for m in messages)
        assert any("Skipped" in (m[0] or "") or "not found" in (m[0] or "").lower() for m in messages)

    def test_import_links_overwritten_message_contains_sku(self) -> None:
        """When links are overwritten, success message lists the offer sku."""
        Product.objects.create(product_key="LINK_P", name="P", product_type=Product.ProductType.QUANTITY)
        rows = [
            _make_offer_row(sku="MSG_OFF", name="N", price="1", product_key="LINK_P", quantity="1"),
        ]
        site = AdminSite()
        admin = OfferAdmin(Offer, site)
        messages: list[tuple[str, str | None]] = []
        admin.message_user = lambda req, msg, level=None, extra_tags=None, fail_silently=False: messages.append(
            (msg, level)
        )

        request = RequestFactory().post("/import/", data={}, format="multipart")
        request.FILES["csv_file"] = SimpleUploadedFile("offers.csv", _offer_csv_bytes(rows), content_type="text/csv")
        with patch("billable.admin.reverse", return_value="/admin/billable/offer/"):
            admin.import_offers_view(request)

        assert any("MSG_OFF" in (m[0] or "") and ("overwritten" in (m[0] or "").lower() or "Links" in (m[0] or "")) for m in messages)

    def test_export_import_roundtrip_preserves_offer_and_items(self) -> None:
        """Export then re-import preserves offer fields and product links (by product_key)."""
        p1 = Product.objects.create(
            product_key="RTA",
            name="RTA Product",
            product_type=Product.ProductType.QUANTITY,
        )
        p2 = Product.objects.create(
            product_key="RTB",
            name="RTB Product",
            product_type=Product.ProductType.PERIOD,
        )
        offer = Offer.objects.create(
            sku="ROUNDTRIP",
            name="Roundtrip Offer",
            price=33.33,
            currency="USD",
            description="Roundtrip desc",
            is_active=True,
            metadata={"r": 1},
        )
        OfferItem.objects.create(offer=offer, product=p1, quantity=5, period_unit=OfferItem.PeriodUnit.MONTHS, period_value=3)
        OfferItem.objects.create(offer=offer, product=p2, quantity=1, period_unit=OfferItem.PeriodUnit.FOREVER)

        site = AdminSite()
        admin = OfferAdmin(Offer, site)
        request = RequestFactory().get("/")
        admin.message_user = lambda req, msg, level=None, extra_tags=None, fail_silently=False: None

        response = admin.export_offers_csv(request, Offer.objects.filter(pk=offer.pk))
        csv_bytes = response.content

        Offer.objects.filter(sku="ROUNDTRIP").delete()
        assert not Offer.objects.filter(sku="ROUNDTRIP").exists()

        admin.message_user = lambda req, msg, level=None, extra_tags=None, fail_silently=False: None
        imp_request = RequestFactory().post("/import/", data={}, format="multipart")
        imp_request.FILES["csv_file"] = SimpleUploadedFile("offers.csv", csv_bytes, content_type="text/csv")
        with patch("billable.admin.reverse", return_value="/admin/billable/offer/"):
            admin.import_offers_view(imp_request)

        restored = Offer.objects.get(sku="ROUNDTRIP")
        assert restored.name == "Roundtrip Offer"
        assert float(restored.price) == 33.33
        assert restored.currency == "USD"
        assert restored.description == "Roundtrip desc"
        assert restored.is_active is True
        assert restored.metadata == {"r": 1}
        items = list(restored.items.select_related("product").order_by("product__product_key"))
        assert len(items) == 2
        by_pk = {it.product.product_key: it for it in items}
        assert by_pk["RTA"].quantity == 5
        assert by_pk["RTA"].period_unit == OfferItem.PeriodUnit.MONTHS
        assert by_pk["RTA"].period_value == 3
        assert by_pk["RTB"].quantity == 1
        assert by_pk["RTB"].period_unit == OfferItem.PeriodUnit.FOREVER
        assert by_pk["RTB"].period_value is None
