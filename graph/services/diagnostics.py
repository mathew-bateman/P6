"""Safe service diagnostics: intentionally exclude raw bodies, URLs and secrets."""
from __future__ import annotations

import re
from uuid import UUID

import requests


class ServiceOperationError(RuntimeError):
    pass


_CODES = {
    "invalid_client", "invalid_request", "invalid_scope", "invalid_grant",
    "unauthorized_client", "access_denied", "temporarily_unavailable", "server_error",
    "accessDenied", "InvalidAuthenticationToken", "invalidRequest", "itemNotFound",
    "generalException", "notAllowed", "serviceNotAvailable", "activityLimitReached",
    "quotaLimitReached", "resourceModified", "nameAlreadyExists",
}
_AUTH_REASONS = {
    "AADSTS7000215": "invalid client secret; check credential value and application pairing",
    "AADSTS7000222": "client secret has expired",
    "AADSTS700016": "application was not found in the configured tenant",
    "AADSTS7000218": "required client credential is missing",
    "AADSTS700027": "client certificate or signature validation failed",
}


def response_details(response) -> str:
    details = []
    status = getattr(response, "status_code", None)
    if isinstance(status, int) and 100 <= status <= 599:
        details.append(f"HTTP {status}")
    try:
        payload = response.json()
    except (ValueError, TypeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    provider_error = payload.get("error")
    code = provider_error.get("code") if isinstance(provider_error, dict) else provider_error
    if isinstance(code, str) and code in _CODES:
        details.append(code)
    codes = payload.get("error_codes", [])
    if isinstance(codes, list):
        details.extend(f"AADSTS{code}" for code in codes[:3] if type(code) is int and 1000 <= code <= 999999999)
    if not any(value.startswith("AADSTS") for value in details):
        description = payload.get("error_description")
        if isinstance(description, str):
            match = re.search(r"\bAADSTS[0-9]{4,9}\b", description[:20000])
            if match:
                details.append(match.group(0))
    for code, reason in _AUTH_REASONS.items():
        if code in details:
            details.append(reason)
    inner = provider_error.get("innerError", {}) if isinstance(provider_error, dict) else {}
    if not isinstance(inner, dict):
        inner = {}
    headers = getattr(response, "headers", {}) or {}
    for label, value in (
        ("trace", payload.get("trace_id")),
        ("correlation", payload.get("correlation_id")),
        ("request", inner.get("request-id") or headers.get("request-id")),
    ):
        if isinstance(value, str) and len(value) == 36:
            try:
                details.append(f"{label} {UUID(value)}")
            except ValueError:
                pass
    return "; ".join(details)


def checked_request(stage, method, *args, accepted_statuses=(), **kwargs):
    try:
        response = method(*args, **kwargs)
        if getattr(response, "status_code", None) in accepted_statuses:
            return response
        response.raise_for_status()
    except requests.RequestException as error:
        details = response_details(error.response) if error.response is not None else ""
        raise ServiceOperationError(
            f"{stage} failed; {type(error).__name__}" + (f"; {details}" if details else "")
        ) from None
    return response


def safe_failure(error, stage):
    if isinstance(error, ServiceOperationError):
        return str(error)
    message = f"{stage} failed; {type(error).__name__}"
    args = getattr(error, "args", ())
    if args and isinstance(args[0], str) and re.fullmatch(r"[A-Z0-9]{5}", args[0]):
        message += f"; SQLSTATE {args[0]}"
    return message
