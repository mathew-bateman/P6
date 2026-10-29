# P6 workspace navigation

The shared Django shell follows the approved compact workspace design:

- A persistent icon rail opens section menus on hover or keyboard navigation.
- Clicking an area opens its default page; the secondary sidebar lists only that area's pages.
- Home shows the rail and available areas without a secondary sidebar or top bar.
- Search, breadcrumbs, the old brand row, desktop collapse control and preview footer are removed.
- Report filters use a right-hand drawer. Reset is an icon next to Close. Edits are applied explicitly; cancelling restores the applied values.
- Existing business screens, report calculations, operational actions and permission checks remain server-rendered.

`backups/navigation.py` derives visibility from the existing view permission helpers.
Staff status alone does not grant either report group. Report-source selection is
validated and preserved across area changes; filter selections carry between
pages in the same report area. The optional production Reporting Database module
is included only when installed and available to the user.

Fonts and icons are served locally; their licence files are included with the assets.
The earlier React design preview is reference material, not the production application.

## Verification

The release was checked with Python 3.12 and Django 5.0.9. All 148 tests pass after
`collectstatic`, including 15 navigation tests covering independent report groups,
staff/editor access, source preservation, active sections, Home and CSRF logout.
Production-dependency preflight also rendered nine safe authenticated GET routes
and checked navigation against existing users. The settings GET is excluded from
read-only smoke checks because it can create a configuration draft.

Visual QA uses local exports of real server-rendered pages, with current CSS and
JavaScript. These exports do not recalculate filters. Server-side report filtering
is covered by the existing report tests and authenticated GET checks.

## Release scope

Deploy only the base template, landing template, navigation include/helper/context
processor, and the `backups/static/backups/` assets. Preserve production-only
backend modules and report templates. No database migration or worker restart is
required for this presentation release. See the README for the verified live
deployment location and rollback procedure.
