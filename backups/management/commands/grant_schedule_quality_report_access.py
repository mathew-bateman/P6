from __future__ import annotations

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Grant Django users access to Schedule Quality reports."

    def add_arguments(self, parser):
        parser.add_argument("usernames", nargs="+")

    def handle(self, *args, **options):
        usernames = [str(username).strip() for username in options["usernames"]]
        User = get_user_model()
        users = []
        missing = []
        for username in usernames:
            try:
                users.append(User.objects.get_by_natural_key(username))
            except User.DoesNotExist:
                missing.append(username)
        if missing:
            raise CommandError(
                "Users do not exist: " + ", ".join(repr(username) for username in missing)
            )

        group, _ = Group.objects.get_or_create(
            name=settings.P6_SCHEDULE_QUALITY_REPORT_GROUP
        )
        for user in users:
            user.groups.add(group)
            self.stdout.write(
                self.style.SUCCESS(
                    f"Granted Schedule Quality report access to {user.get_username()}."
                )
            )
