from __future__ import annotations

import re
import json
from decimal import Decimal
from typing import Any

from django import forms

from backups.services.schedule_quality import ScheduleQualitySettingsSnapshot


SETTING_CODE_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
SCOPE_FIELDS = (
    "include_loe",
    "include_wbs_summary",
    "include_milestones",
    "exclude_complete",
)
DETAIL_FIELD_CATEGORIES = {
    "activity_code", "udf", "task_column", "project_column",
    "relationship_column", "wbs_column", "resource_column",
}
P6_TABLE_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,127}$")
NUMERIC_OPTION_BOUNDS = {
    "negative_float_days": (Decimal("-100000"), Decimal("100000")),
    "riding_days_after_data_date": (Decimal("0"), Decimal("3650")),
    "excessive_ss_percent": (Decimal("0"), Decimal("100")),
    "excessive_ff_percent": (Decimal("0"), Decimal("100")),
}
SCOPE_OPTION_CODES = {"exclude_deleted_activities"}
LEGACY_OPEN_END_EVIDENCE_FIELD = ("relationship_column", "relationship_summary")
OPEN_END_EVIDENCE_DEFAULTS = {
    "open_start": (
        ("TASKPRED", "pred_type", "Relationship Type"),
        ("TASK", "task_code", "Predecessor Activity ID"),
        ("TASK", "task_name", "Predecessor Activity Name"),
    ),
    "open_finish": (
        ("TASKPRED", "pred_type", "Relationship Type"),
        ("TASK", "task_code", "Successor Activity ID"),
        ("TASK", "task_name", "Successor Activity Name"),
    ),
}


def _field_name(*parts: str) -> str:
    for part in parts:
        if not SETTING_CODE_PATTERN.fullmatch(part):
            raise RuntimeError(f"Unsafe schedule quality setting code: {part!r}")
    return "__".join(parts)


def _initial_detail_fields(
    settings_snapshot: ScheduleQualitySettingsSnapshot,
) -> list[dict[str, object]]:
    fields = [
        {
            "check_code": field.check_code,
            "source_category": field.source_category,
            "source_identifier": field.source_identifier,
            "display_label": field.display_label,
            "display_format": field.display_format,
            "sort_order": field.sort_order,
        }
        for field in settings_snapshot.detail_fields
    ]

    for check_code, defaults in OPEN_END_EVIDENCE_DEFAULTS.items():
        check_fields = [field for field in fields if field["check_code"] == check_code]
        if not any(
            (field["source_category"], field["source_identifier"])
            == LEGACY_OPEN_END_EVIDENCE_FIELD
            for field in check_fields
        ):
            continue

        existing = {
            (str(field["source_category"]), str(field["source_identifier"])): field
            for field in check_fields
        }
        default_keys = {(category, identifier) for category, identifier, _ in defaults}
        replacement = []
        for category, identifier, label in defaults:
            replacement.append(
                existing.get(
                    (category, identifier),
                    {
                        "check_code": check_code,
                        "source_category": category,
                        "source_identifier": identifier,
                        "display_label": label,
                        "display_format": "native",
                        "sort_order": 0,
                    },
                )
            )
        replacement.extend(
            field
            for field in sorted(check_fields, key=lambda item: int(item["sort_order"]))
            if (
                field["source_category"],
                field["source_identifier"],
            )
            not in default_keys | {LEGACY_OPEN_END_EVIDENCE_FIELD}
        )
        for sort_order, field in enumerate(replacement, start=1):
            field["sort_order"] = sort_order

        fields = [field for field in fields if field["check_code"] != check_code]
        fields.extend(replacement)

    return fields


class ScheduleQualitySettingsForm(forms.Form):
    config_version_id = forms.IntegerField(widget=forms.HiddenInput)
    expected_settings_hash = forms.RegexField(
        regex=r"^[0-9A-Fa-f]{64}$",
        widget=forms.HiddenInput,
        error_messages={
            "invalid": "The settings version token is invalid. Reload and try again."
        },
    )
    change_note = forms.CharField(
        label="Change note",
        required=False,
        max_length=500,
        widget=forms.Textarea(
            attrs={
                "rows": 2,
                "placeholder": "Why are these settings changing?",
            }
        ),
    )
    detail_fields_json = forms.CharField(required=False, widget=forms.HiddenInput)

    def __init__(
        self,
        *args: Any,
        settings_snapshot: ScheduleQualitySettingsSnapshot,
        **kwargs: Any,
    ) -> None:
        self.settings_snapshot = settings_snapshot
        initial = dict(kwargs.pop("initial", {}) or {})
        initial.setdefault("config_version_id", settings_snapshot.config_version_id)
        initial.setdefault("expected_settings_hash", settings_snapshot.settings_hash)
        # This field is retained for the persisted audit note, but its content is
        # generated server-side from the submitted configuration delta.
        initial.setdefault("change_note", "")
        initial.setdefault(
            "detail_fields_json",
            json.dumps(_initial_detail_fields(settings_snapshot)),
        )
        kwargs["initial"] = initial
        super().__init__(*args, **kwargs)

        self.check_rows: list[dict[str, Any]] = []
        for check in settings_snapshot.checks:
            enabled_name = _field_name("check", check.check_code, "is_enabled")
            self.fields[enabled_name] = forms.BooleanField(
                required=False,
                initial=check.is_enabled,
                label=f"Enable {check.display_name}",
                widget=forms.CheckboxInput(
                    attrs={"aria-label": f"Enable {check.display_name}"}
                ),
            )
            scope_names: dict[str, str | None] = {}
            for scope_name in SCOPE_FIELDS:
                current_value = getattr(check, scope_name)
                if current_value is None:
                    scope_names[scope_name] = None
                    continue
                name = _field_name("check", check.check_code, scope_name)
                self.fields[name] = forms.BooleanField(
                    required=False,
                    initial=current_value,
                    label=f"{scope_name.replace('_', ' ').title()} for {check.display_name}",
                    widget=forms.CheckboxInput(
                        attrs={
                            "aria-label": (
                                f"{scope_name.replace('_', ' ')} for {check.display_name}"
                            )
                        }
                    ),
                )
                scope_names[scope_name] = name
            self.check_rows.append(
                {
                    "check": check,
                    "enabled": self[enabled_name],
                    **{
                        scope_name: self[field_name] if field_name else None
                        for scope_name, field_name in scope_names.items()
                    },
                }
            )

        self.option_rows: list[dict[str, Any]] = []
        self.scope_option_rows: list[dict[str, Any]] = []
        for option in settings_snapshot.options:
            name = _field_name("option", option.option_code)
            field = self._build_option_field(option)
            self.fields[name] = field
            row = {"option": option, "field": self[name]}
            if option.option_code in SCOPE_OPTION_CODES:
                self.scope_option_rows.append(row)
            else:
                self.option_rows.append(row)

        self.constraint_rows: list[dict[str, Any]] = []
        for constraint in settings_snapshot.constraint_types:
            name = _field_name("constraint", constraint.constraint_type_code)
            self.fields[name] = forms.BooleanField(
                required=False,
                initial=constraint.is_checked,
                label=f"Check {constraint.display_name}",
            )
            self.constraint_rows.append(
                {"constraint": constraint, "field": self[name]}
            )

    @staticmethod
    def _build_option_field(option):
        common = {"label": option.display_name, "required": True}
        if option.data_type == "bit":
            return forms.BooleanField(
                label=option.display_name,
                required=False,
                initial=bool(option.bit_value),
            )
        if option.data_type == "integer":
            minimum, maximum = NUMERIC_OPTION_BOUNDS.get(
                option.option_code,
                (Decimal("0"), None),
            )
            return forms.IntegerField(
                **common,
                initial=Decimal(option.numeric_value).quantize(Decimal("0.01")),
                min_value=int(minimum),
                max_value=None if maximum is None else int(maximum),
            )
        if option.data_type == "decimal":
            minimum, maximum = NUMERIC_OPTION_BOUNDS.get(
                option.option_code,
                (Decimal("0"), None),
            )
            return forms.DecimalField(
                **common,
                initial=Decimal(option.numeric_value).quantize(Decimal("0.01")),
                min_value=minimum,
                max_value=maximum,
                max_digits=18,
                decimal_places=4,
            )
        if option.data_type == "text":
            return forms.CharField(
                **common,
                initial=option.text_value or "",
                max_length=500,
            )
        raise RuntimeError(
            f"Unsupported data type {option.data_type!r} for {option.option_code}."
        )

    def clean_config_version_id(self) -> int:
        config_version_id = int(self.cleaned_data["config_version_id"])
        if config_version_id != self.settings_snapshot.config_version_id:
            raise forms.ValidationError(
                "These settings have changed since the page was loaded. Reload and try again."
            )
        return config_version_id

    def clean(self):
        cleaned_data = super().clean()
        expected_settings_hash = cleaned_data.get("expected_settings_hash")
        if (
            expected_settings_hash
            and expected_settings_hash.upper()
            != self.settings_snapshot.settings_hash.upper()
        ):
            raise forms.ValidationError(
                "This draft changed after the page was loaded. Reload before saving or publishing."
            )
        return cleaned_data

    def clean_detail_fields_json(self) -> list[dict[str, object]]:
        raw_value = self.cleaned_data.get("detail_fields_json") or "[]"
        try:
            fields = json.loads(raw_value)
        except json.JSONDecodeError as error:
            raise forms.ValidationError("Evidence fields could not be read. Reload and try again.") from error
        if not isinstance(fields, list):
            raise forms.ValidationError("Evidence fields must be a list.")
        allowed_checks = {check.check_code for check in self.settings_snapshot.checks}
        cleaned: list[dict[str, object]] = []
        seen: set[tuple[str, str, str]] = set()
        used_orders: set[tuple[str, int]] = set()
        for field in fields:
            if not isinstance(field, dict):
                raise forms.ValidationError("Each evidence field must be valid.")
            check_code = str(field.get("check_code") or "")
            category = str(field.get("source_category") or "")
            identifier = str(field.get("source_identifier") or "").strip()
            label = str(field.get("display_label") or "").strip()
            display_format = str(field.get("display_format") or "native").strip()
            try:
                sort_order = int(field.get("sort_order"))
            except (TypeError, ValueError) as error:
                raise forms.ValidationError("Each evidence field needs a valid send order.") from error
            if (
                check_code not in allowed_checks
                or (category not in DETAIL_FIELD_CATEGORIES and not P6_TABLE_NAME_PATTERN.fullmatch(category))
                or not identifier
                or not label
                or sort_order < 1
            ):
                raise forms.ValidationError("Each evidence field needs a check, source, field, and label.")
            if len(identifier) > 120 or len(label) > 120:
                raise forms.ValidationError("Evidence field identifiers and labels must be 120 characters or fewer.")
            if display_format not in {"native", "p6_days_only", "p6_hours_and_days"}:
                raise forms.ValidationError("Each evidence field needs a valid display format.")
            if (
                display_format in {"p6_days_only", "p6_hours_and_days"}
                and not identifier.lower().endswith("_hr_cnt")
            ):
                raise forms.ValidationError(
                    "Calculated days can only be selected for a P6 hour-count field."
                )
            key = (check_code, category, identifier)
            if key in seen:
                raise forms.ValidationError("The same evidence field cannot be added twice to one check.")
            seen.add(key)
            order_key = (check_code, sort_order)
            if order_key in used_orders:
                raise forms.ValidationError("Each evidence field needs a unique send order within its check.")
            used_orders.add(order_key)
            cleaned.append({"check_code": check_code, "source_category": category, "source_identifier": identifier, "display_label": label, "display_format": display_format, "sort_order": sort_order})
        return cleaned

    def build_payload(self) -> dict[str, list[dict[str, object]]]:
        if not self.is_valid():
            raise ValueError("Cannot build a settings payload from an invalid form.")

        checks: list[dict[str, object]] = []
        for check in self.settings_snapshot.checks:
            row: dict[str, object] = {
                "check_code": check.check_code,
                "is_enabled": bool(
                    self.cleaned_data[_field_name("check", check.check_code, "is_enabled")]
                ),
            }
            for scope_name in SCOPE_FIELDS:
                if getattr(check, scope_name) is None:
                    row[scope_name] = None
                else:
                    row[scope_name] = bool(
                        self.cleaned_data[
                            _field_name("check", check.check_code, scope_name)
                        ]
                    )
            checks.append(row)

        options: list[dict[str, object]] = []
        for option in self.settings_snapshot.options:
            value = self.cleaned_data[_field_name("option", option.option_code)]
            row = {
                "option_code": option.option_code,
                "bit_value": None,
                "numeric_value": None,
                "text_value": None,
            }
            if option.data_type == "bit":
                row["bit_value"] = bool(value)
            elif option.data_type in {"integer", "decimal"}:
                row["numeric_value"] = value
            else:
                row["text_value"] = str(value)
            options.append(row)

        constraint_types = [
            {
                "constraint_type_code": constraint.constraint_type_code,
                "is_checked": bool(
                    self.cleaned_data[
                        _field_name("constraint", constraint.constraint_type_code)
                    ]
                ),
            }
            for constraint in self.settings_snapshot.constraint_types
        ]
        return {
            "checks": checks,
            "options": options,
            "constraint_types": constraint_types,
            "detail_fields": self.cleaned_data["detail_fields_json"],
        }
