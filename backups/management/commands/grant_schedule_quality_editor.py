from __future__ import annotations

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Grant a Django user access to edit and publish schedule-quality settings."

    def add_arguments(self, parser):
        parser.add_argument("username")

    def handle(self, *args, **options):
        username = str(options["username"]).strip()
        try:
            user = get_user_model().objects.get_by_natural_key(username)
        except get_user_model().DoesNotExist as error:
            raise CommandError(f"User {username!r} does not exist.") from error

        group, _ = Group.objects.get_or_create(
            name=settings.P6_SCHEDULE_QUALITY_EDITOR_GROUP
        )
        user.groups.add(group)
        self.stdout.write(
            self.style.SUCCESS(
                f"Granted schedule-quality editor access to {user.get_username()}."
            )
        )
