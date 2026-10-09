# Executive change dashboard

A read-only Streamlit dashboard for reviewing substantial record changes without treating structure-only changes as edits.

## Run

From the repository root:

```powershell
python -m streamlit run executive_dashboard/app.py
```

The sidebar accepts an existing JSON Versioner `history.sqlite3` and `config.json`. The shared dashboard data layer opens the selected history read-only.

## Views

- **Activity feed** leads with a summary of affected records, update sizes, additions/removals, and the most affected system types.
- **Largest changes** sorts record updates by weighted change size and provides exact before/after field values and complete stored records.
- **Changes over time** uses the best available event date, preferring a valid record timestamp and falling back to observation time.
- **Changes by system type** shows both `systemGroup` and `systemType`, plus owner filtering.
- **Structure changes** separately groups fields added, removed, or structurally altered. These never enter edit counts or size rankings.
- **Current inventory** browses and searches captured records at the end of the observation range, including unchanged records, with database-backed pagination.

All views share observation-range, system-group, system-type, owner, and estimated-date filters, including Structure changes. The default range is the latest 30 observations; choose a wider or earlier range in the sidebar. History queries are bounded to that range, and semantic change sizes are calculated after filtering. The activity feed and largest-changes table paginate their rendered results. Baseline-only, unchanged, and structure-only histories remain navigable.

Change details compare with the actual previous stored version, including across capture gaps. Preview choices include snapshot IDs so several versions captured on the same day remain individually selectable. Direct change links remain usable even outside the selected observation range.

The collapsed **Capture details** panel shows the latest stored observation time in UTC, captured record count, unavailable-record count, and predecessor. The collapsed **Change summary** panel contains update totals, size counts, and most affected system types, keeping the record views prominent. Capture gaps are explained separately from removals. These values describe stored history; CSV download times and failed collection attempts require separate downloader/run-status integration.

Filters persist when changing views and opening records. The collapsed **Category filters** panel defaults each dimension to **All**, displaying no selected tags. Choose **Include** or **Exclude** to search and select a small number of groups, types, or owners. An empty Include/Exclude list means All. Option counts show unique records across the observation range, with dependent type/owner lists narrowed by draft selections. Temporarily unavailable selected values are retained with zero counts rather than silently discarded.

Category edits remain pending until **Apply filters** is pressed; compact chips above the view summarize the applied selections and can clear a dimension immediately. Date and observation controls update directly. **Reset filters** clears applied and pending categories and restores the default observation range without changing the view. **Refresh data** reloads cached reads. Date shortcuts offer **Last capture**, **Last 7 days**, **Last 30 days**, or custom bounds. Day windows end at the last selected observation; Last capture selects that observation's events even when their estimated dates are earlier.

Hover over size badges for their change-unit and content-category explanation; it also appears in the change detail. Record buttons open details within the same session, with **Previous change**, **Next change**, and **Back to changes** controls. Baseline-only and empty selections have distinct messages and an action to browse captured inventory.

## Test-record demo

See [`demo/README.md`](demo/README.md) for a repeatable four-snapshot dataset based on the repository's `test_records`. It includes parameter-value, combined value/UOM, comment, description-noise, and structure-only cases.

## Controlling visible fields

Comparison and presentation exclusions are deliberately separate:

- `ignore_fields` and `ignore_paths` are opt-out rules applied before comparison. Matching values do not create stored changes.
- `dashboard_display.exclude_parameter_fields` hides named leaves such as `uom` or `comments` only in the executive dashboard.
- `dashboard_display.exclude_description_fields` hides named leaves within description items.
- `dashboard_display.exclude_other_paths` hides exact or normalized paths elsewhere; array selectors normalize to `[]`.

Dashboard exclusions are also opt-out: new fields remain visible until deliberately excluded. Parameter identity and context fields can still supply the component, parameter name, and subtitle used in natural labels and the complete-record view.

When a changed `parameterUomValue` is visible, duplicate `parameterValue` and `uom` changes are collapsed into it. Other non-excluded parameter fields, including newly introduced fields, retain their exact before/after values. Encoded selector values are split before decoding, preserving commas, quotes, dots, and brackets in parameter identities.

Run all regression tests from the repository root with `python -m unittest discover -v`.

After building it, launch directly into the demo with:

```powershell
python -m streamlit run executive_dashboard/app.py -- --demo
```
