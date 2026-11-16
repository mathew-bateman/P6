"""Keep report links and forms tied to the source the user is viewing."""
from django import template
from django.http import QueryDict
from django.utils.html import format_html

register = template.Library()
SOURCES = {"combined", "p6_22", "p6_23"}
COMPATIBLE_SOURCE_FILTERS = (
    "portfolio", "lead_planner", "project_status", "project_state",
    "exclude_blanks", "updated_date", "check", "discipline", "project_phase",
)


def selected_source(context):
    request = context.get("request")
    snapshot = context.get("reporting_snapshot") or {}
    source = snapshot.get("source") or getattr(request, "p6_report_source", None)
    if source not in SOURCES and request is not None:
        source = request.session.get("p6_report_source")
    return source if source in SOURCES else None


@register.simple_tag(takes_context=True)
def report_query(context, reset=False, **updates):
    """Build an escaped query string, always retaining the selected source."""
    request = context.get("request")
    query = request.GET.copy() if request is not None and not reset else QueryDict(mutable=True)
    source = selected_source(context)
    if source:
        query["report_source"] = source
    for key, value in updates.items():
        if value is None:
            query.pop(key, None)
        else:
            query[key] = value
    return "?" + query.urlencode() if query else ""


@register.simple_tag(takes_context=True)
def report_source_input(context):
    source = selected_source(context)
    return format_html('<input type="hidden" name="report_source" value="{}">', source) if source else ""


@register.simple_tag(takes_context=True)
def report_source_filters(context):
    """Keep cross-source filters, but never carry source-specific project IDs/pages."""
    request = context.get("request")
    if request is None:
        return []
    return [(key, request.GET[key]) for key in COMPATIBLE_SOURCE_FILTERS if key in request.GET]
