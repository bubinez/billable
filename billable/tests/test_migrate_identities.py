"""Autotests for the migrate_identities management command."""

from io import StringIO

import pytest
from django.core.management import call_command
from django.contrib.auth import get_user_model

from billable.models import ExternalIdentity


@pytest.mark.django_db
class TestMigrateIdentitiesCommand:
    """Tests for migrate_identities management command."""

    def test_creates_identities_from_user_field(self) -> None:
        """Command creates ExternalIdentity for each user with non-empty field value."""
        User = get_user_model()
        User.objects.create_user(username="u1", password="x")
        User.objects.create_user(username="u2", password="x")

        out = StringIO()
        err = StringIO()
        call_command("migrate_identities", "username", "telegram", stdout=out, stderr=err)

        assert ExternalIdentity.objects.filter(provider="telegram").count() == 2
        assert ExternalIdentity.objects.get(external_id="u1").user_id == User.objects.get(username="u1").pk
        assert ExternalIdentity.objects.get(external_id="u2").user_id == User.objects.get(username="u2").pk
        assert "Done" in out.getvalue()
        assert "Created: 2" in out.getvalue()

    def test_idempotent_second_run_skips_existing(self) -> None:
        """Second run does not create duplicates; existing identities are skipped."""
        User = get_user_model()
        User.objects.create_user(username="u1", password="x")

        call_command("migrate_identities", "username", "telegram", stdout=StringIO(), stderr=StringIO())
        assert ExternalIdentity.objects.filter(provider="telegram", external_id="u1").count() == 1

        out = StringIO()
        call_command("migrate_identities", "username", "telegram", stdout=out, stderr=StringIO())

        assert ExternalIdentity.objects.filter(provider="telegram", external_id="u1").count() == 1
        assert "Created: 0" in out.getvalue()
        assert "skipped (already exist): 1" in out.getvalue()

    def test_dry_run_creates_nothing(self) -> None:
        """With --dry-run no ExternalIdentity records are created."""
        User = get_user_model()
        User.objects.create_user(username="u1", password="x")

        out = StringIO()
        call_command(
            "migrate_identities", "username", "telegram", "--dry-run",
            stdout=out, stderr=StringIO(),
        )

        assert ExternalIdentity.objects.filter(provider="telegram").count() == 0
        assert "[DRY]" in out.getvalue()
        assert "Created: 1" in out.getvalue()

    def test_limit_respects_user_count(self) -> None:
        """With --limit only that many users are processed."""
        User = get_user_model()
        User.objects.create_user(username="u1", password="x")
        User.objects.create_user(username="u2", password="x")

        out = StringIO()
        call_command(
            "migrate_identities", "username", "telegram", "--limit=1",
            stdout=out, stderr=StringIO(),
        )

        assert ExternalIdentity.objects.filter(provider="telegram").count() == 1
        assert "Created: 1" in out.getvalue()

    def test_invalid_field_writes_error_and_creates_nothing(self) -> None:
        """When User model has no such field, command writes to stderr and creates no identities."""
        User = get_user_model()
        User.objects.create_user(username="u1", password="x")

        out = StringIO()
        err = StringIO()
        call_command(
            "migrate_identities", "nonexistent_field", "telegram",
            stdout=out, stderr=err,
        )

        assert "has no field 'nonexistent_field'" in err.getvalue()
        assert ExternalIdentity.objects.filter(provider="telegram").count() == 0

    def test_two_runs_different_fields_create_two_identities_per_user(self) -> None:
        """Run command twice with different field and provider: one user gets two ExternalIdentity records."""
        User = get_user_model()
        user = User.objects.create_user(username="alice", password="x")
        user.chat_id = 123456789
        user.stripe_id = "cus_StripeAlice"
        user.save()

        out1 = StringIO()
        call_command("migrate_identities", "chat_id", "telegram", stdout=out1, stderr=StringIO())
        out2 = StringIO()
        call_command("migrate_identities", "stripe_id", "stripe", stdout=out2, stderr=StringIO())

        identities = list(ExternalIdentity.objects.filter(user=user).order_by("provider"))
        assert len(identities) == 2
        by_provider = {i.provider: i.external_id for i in identities}
        assert by_provider["telegram"] == "123456789"
        assert by_provider["stripe"] == "cus_StripeAlice"
        assert "Created: 1" in out1.getvalue()
        assert "Created: 1" in out2.getvalue()
