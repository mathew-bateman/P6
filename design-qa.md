# Schedule Quality Reports design QA

## Evidence

- Source visual truth: `validation-source.png`, captured from `SQL Test Dashboard v3.pbix` in Power BI Desktop.
- Browser-rendered implementation: `validation-implementation.png`.
- Narrow implementation: `validation-implementation-narrow.png`.
- Full-view comparison: `validation-comparison.png`.
- Focused comparison: `validation-comparison-focused.png`.
- Programme Check source visual truth: `overview-source.png`, captured from the first page of `SQL Test Dashboard v3.pbix` in Power BI Desktop.
- Programme Check browser renders: `overview-implementation.png`, `overview-implementation-scorecards.png`, and `overview-implementation-narrow.png`.
- Programme Check combined comparison: `overview-comparison.png`, containing the source, the top of the Django page, and the scrolled scorecard state in one review input.
- Source pixels: 1376 x 752, including Power BI Desktop chrome and authoring sidebars.
- Implementation pixels: 1362 x 744 at a 1362 x 744 CSS viewport and device scale factor 1.
- Narrow pixels: 700 x 900 at a 700 x 900 CSS viewport and device scale factor 1.
- Normalization: the full comparison preserves both captures at native density. The focused comparison crops the source report canvas and the Django content region, then scales the source crop to the implementation width for layout comparison.
- State: authenticated staff user, all filters cleared, twenty checks shown on both pages, representative score and evidence data loaded.

## Findings

No actionable P0, P1, or P2 findings remain.

- Typography: the implementation uses the application's established Segoe UI stack, weights, and hierarchy. It deliberately does not reproduce Power BI Desktop's authoring chrome. Table text remains readable at the desktop and narrow viewports.
- Spacing and layout: both PBIX page structures are preserved. Programme Check includes the filter rail, twenty-row scoring matrix, latest programme date, five score cards, and PASS/FAIL outcome. Validation & Evidence includes its filter rail, check-result matrix, last-refresh information, and full-width evidence table. Django navigation, hero, KPI cards, panel spacing, borders, and radii follow the existing Operations Hub system. The 700 px layouts collapse to one column without horizontal page overflow; dense report tables retain their own scroll containers.
- Colors and tokens: the implementation maps the source's qualifying-result emphasis to the Hub's existing teal, danger, success, background, border, and muted-text tokens. Contrast and status meaning remain clear without importing Power BI's purple report chrome.
- Image and asset fidelity: the source Amey mark belongs to the Power BI report canvas. The Django view intentionally uses the application's existing P6 brand/navigation rather than introducing a second product brand. No source imagery has been replaced with placeholder or CSS-drawn artwork.
- Copy and content: the Programme Check filters, eleven scoring columns, twenty PBIX checks, limits, available points, scored points, pass percentage, and pass-rate threshold are retained. The Validation & Evidence filters and core metric/evidence columns are retained. Check labels come from the active schedule-quality configuration, so saved Django labels remain the source of truth rather than copying static PBIX wording.
- Interactions and accessibility: Project, Portfolio, Updated date, Lead planner, and Check type controls are labelled native selects. Apply performs a server-side GET filter; Clear resets the report. The two report tabs navigate between Programme Overview and Validation & Evidence. A project selection remained selected after filtering; a Check type interaction reduced the validation summary to one row and evidence to three rows. No browser console warnings or errors were present.

## Comparison history

1. Initial browser capture found a P1 layout defect: the evidence section used a semantic `header` element, and the shared base stylesheet fixes all `header` elements to the viewport. This caused the evidence heading to cover the application navigation and report content.
2. Replaced both report-section `header` wrappers with neutral `div` wrappers while preserving their accessible headings.
3. Restarted the preview, recaptured at 1362 x 744, and confirmed that the only fixed header is the Operations Hub application header. The evidence heading now remains in document flow.
4. Captured and checked the 700 x 900 layout. No page-level horizontal overflow, overlap, clipping, or unusable controls remained.
5. The first Programme Check capture exposed a P2 table-width defect: the final Points scored column was hidden behind an unnecessary 1160 px minimum width inside the available desktop panel.
6. Reduced the scoring table minimum width to 1010 px and tightened only the table cell padding. Recapture confirmed all eleven columns are visible at 1362 x 744 while retaining an intentional table-level horizontal scroller at 700 px.
7. Reviewed `overview-comparison.png` as a single source-plus-implementation input. The source row ordering, limits, points, totals, threshold, and PASS state match, while the implementation correctly substitutes the established Django application chrome and responsive layout for Power BI Desktop authoring chrome.

## Primary interactions tested

- Staff sign-in and staff-only navigation visibility.
- Opening the Validation Report from the Operations Hub landing page.
- Applying the High Total Float check filter and preserving the query-string state.
- Server-rendered filtered summary and evidence results.
- Desktop and narrow responsive layouts.
- Browser console warnings and errors.
- Opening Programme Overview from the shared Quality Reports navigation.
- Switching between Programme Overview and Validation & Evidence.
- Applying a Project filter and preserving its query-string and selected state.
- Desktop scoring table visibility, scrolled scorecard visibility, and 700 px responsive behaviour.

## Follow-up polish

- P3: verify the page against the live reporting database once that SQL Server is reachable from this workstation. The current visual run used representative data because the configured local SQL endpoint did not respond.

## Annotation update QA - 14 August 2026

**Evidence**

- Source visual truth: `validation-implementation-narrow.png`, the pre-annotation report state showing Refresh & Settings, report tabs below the hero, and filter actions extending beyond their declared grid width.
- Browser-rendered implementation: `annotation-validation-after-700.png` at a 700 x 900 CSS viewport and device scale factor 1.
- Filter-action focused state: `annotation-validation-buttons-700.png` at the same viewport and density.
- Combined source and implementation input: `annotation-comparison.png`, normalized to two 700 x 900 panels.
- Additional annotated-size verification: `annotation-validation-after.png` at 767 x 794 CSS pixels.
- State: authenticated staff user on Validation & Evidence with deterministic local QA data. The hero, tabs, filter form, buttons, responsive CSS, and route navigation all rendered without a reporting-database dependency; production data was verified separately after deployment.

**Findings**

No actionable P0, P1, or P2 findings remain.

- Fonts and typography: the existing application Segoe UI hierarchy is unchanged. Tab labels remain readable and do not truncate at 700 or 767 px.
- Spacing and layout: the two report tabs now sit inside the hero in the space vacated by Refresh & Settings. At 700 px they share a full-width row; at 767 px they wrap naturally inside the hero. The filter actions remain fully inside the filter panel.
- Colors and tokens: active and inactive tabs continue to use the existing accent, white, border, and text tokens.
- Image quality and assets: no imagery or icons are involved in these annotated controls, and no substitute artwork was introduced.
- Copy and content: Refresh & Settings is absent. Programme Overview and Validation & Evidence retain the established two-page wording and routes.
- Responsiveness and accessibility: browser geometry checks confirmed no page-level horizontal overflow, both action buttons inside their panel bounds, the tabs nested semantically inside the hero, and native labelled filters unchanged.

**Comparison history**

1. P2: Refresh & Settings occupied the hero despite the annotation requesting removal. Removed it from both report templates.
2. P2: report tabs formed a detached row below the hero. Moved each page's report navigation into its hero and preserved `aria-current` on the active page.
3. P2: Apply/Clear used content-box sizing, so 100% widths plus padding could exceed the two-column action grid. Added `box-sizing: border-box` to both pages' filter action controls.
4. Recaptured at 767 x 794 and 700 x 900. Geometry confirmed zero Refresh & Settings links, tabs inside the hero, action bounds inside the filter panel, and no horizontal page overflow.

**Primary interactions tested**

- Validation & Evidence route reload after the template change.
- Programme Overview tab navigation.
- Active-page state and tab placement.
- Filter control and action layout at 767 x 794 and 700 x 900.
- Browser console contained no new frontend errors.

## Schedule Quality annotation update QA - 14 August 2026

**Evidence**

- Source visual truth: the two browser annotation captures supplied with this update at 767 x 794, showing the former Open Quality Reports hero action and the former header order.
- Browser-rendered implementation: `annotation-schedule-quality-after.png`, captured at the same 767 x 794 CSS viewport and device scale factor 1.
- State: authenticated staff user on Schedule Quality. The same live-page chrome, hero, action cards, and refresh-history state were retained.

**Findings**

No actionable P0, P1, or P2 findings remain.

- Typography, colors, image assets, and application copy are unchanged outside the two annotated controls.
- Spacing and layout: removing the hero button leaves the existing hero compact and balanced. The refreshed header has no overlap or horizontal overflow at 767 px.
- Navigation and accessibility: the header link order is now Backup Targets, Database Maintenance, Schedule Quality, and Quality Reports; all remain ordinary navigable links.

**Comparison history**

1. P2: Open Quality Reports duplicated the report navigation and was specifically marked for removal. Removed its staff-only hero action from `schedule_quality.html`.
2. P2: Database Maintenance appeared after the schedule-quality links. Moved its existing conditional header link immediately after Backup Targets in `base.html`.
3. Recaptured at 767 x 794. DOM and layout checks confirm zero Open Quality Reports links, the required header order, and zero page-level horizontal overflow.

**Primary interactions tested**

- Authenticated Schedule Quality page render.
- Header navigation order and preserved target URLs.
- Hero action removal.
- 767 x 794 responsive layout and overflow geometry.

## Database Maintenance annotation update QA - 14 August 2026

**Evidence**

- Source visual truth: the supplied browser annotation at 870 x 794, showing the Sign Out control clipped at the right edge of the authenticated header.
- Browser-rendered implementation: authenticated local QA capture at 870 x 794 CSS pixels and device scale factor 1.
- State: staff user on Database Maintenance with the existing maintenance metrics and audit history content.

**Findings**

No actionable P0, P1, or P2 findings remain.

- The shared base template now applies an intermediate narrow-width header rule through 940 px: reduced navigation spacing, hidden non-essential username badge, and a fixed-size non-wrapping Sign Out control.
- Geometry checks confirm zero page-level horizontal overflow, a 32 px Sign Out button fully inside the viewport, and all four navigation links visible without clipping.
- Typography, colours, copy, and existing navigation destinations remain unchanged. Browser console contained no warnings or errors.

**Comparison history**

1. P2: at the annotated 870 px viewport, the user badge and full-width navigation consumed the available header space, causing Sign Out to wrap and clip.
2. Added a bounded shared responsive rule in `base.html`, preserving the existing desktop and 720 px mobile rules while making the header fit at the supplied width.
3. Recaptured at 870 x 794. The header, Sign Out control, and maintenance page now fit with zero horizontal overflow.

**Primary interactions tested**

- Authenticated Database Maintenance route render.
- Sign Out visibility and non-wrapping layout.
- Header navigation links at the annotated width.
- Browser console warnings and errors.

## Final result

passed
