"""Shared permission for resource-intensive reporting refresh actions."""


def can_refresh_reporting(user):
    return user.is_authenticated and (user.is_staff or user.has_perm("reporting_mirror.refresh_reporting"))
