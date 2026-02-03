"""Management command to migrate user field values into ExternalIdentity records."""

from __future__ import annotations

from typing import Any

from django.apps import apps
from django.core.management.base import BaseCommand, CommandParser
from django.db import transaction

from billable.conf import billable_settings
from billable.models import ExternalIdentity


class Command(BaseCommand):
    """
    Create ExternalIdentity records from a User model field.

    Reads the given field (e.g. chat_id, telegram_id) from each user and creates
    an ExternalIdentity with that value as external_id and the given provider,
    if one does not already exist (idempotent).
    """

    help = (
        "Create ExternalIdentity records from a User model field (e.g. chat_id). "
        "For each user with a non-empty field value, creates an identity with the "
        "given provider if one does not already exist (idempotent)."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "field",
            type=str,
            help="Name of the field on User/CustomUser holding the identifier (e.g. chat_id, telegram_id).",
        )
        parser.add_argument(
            "provider",
            type=str,
            help="Provider name for ExternalIdentity (e.g. telegram).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Only show the plan without writing to the database.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="Limit the number of users to process (0 = no limit).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        field: str = options["field"]
        provider: str = options["provider"]
        dry_run: bool = options["dry_run"]
        limit: int = options["limit"]

        user_model_label: str = billable_settings.USER_MODEL
        try:
            app_label, model_name = user_model_label.split(".", 1)
            UserModel = apps.get_model(app_label, model_name)
        except (ValueError, LookupError) as e:
            self.stderr.write(
                self.style.ERROR(f"Failed to load user model '{user_model_label}': {e}")
            )
            return

        if not hasattr(UserModel, field):
            self.stderr.write(
                self.style.ERROR(
                    f"Model {user_model_label} has no field '{field}'."
                )
            )
            return

        qs = UserModel.objects.all().order_by("pk")
        if limit > 0:
            qs = qs[:limit]

        created = 0
        skipped = 0

        with transaction.atomic():
            for user in qs:
                value = getattr(user, field, None)
                if value is None or (isinstance(value, str) and not value.strip()):
                    continue
                external_id = str(value).strip()

                exists = ExternalIdentity.objects.filter(
                    provider=provider,
                    external_id=external_id,
                ).exists()
                if exists:
                    skipped += 1
                    continue

                if dry_run:
                    self.stdout.write(
                        f"[DRY] Create identity provider={provider!r} external_id={external_id!r} user_id={user.pk}"
                    )
                    created += 1
                else:
                    ExternalIdentity.objects.create(
                        provider=provider,
                        external_id=external_id,
                        user=user,
                    )
                    created += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Done. Created: {created}, skipped (already exist): {skipped}."
            )
        )
