# Executive change dashboard

A read-only Streamlit dashboard for reviewing substantial record changes without treating structure-only changes as edits.

## Run

From the repository root:

```powershell
python -m streamlit run executive_dashboard/app.py
```

The sidebar accepts an existing JSON Versioner `history.sqlite3` and `config.json`. The shared dashboard data layer opens the selected history read-only.

## Views

- **Largest changes** sorts record updates by weighted change size and provides exact before/after field values and complete stored records.
- **Changes over time** uses the best available event date, preferring a valid record timestamp and falling back to observation time.
- **Changes by system type** shows both `systemGroup` and `systemType`, plus owner filtering.
- **Structure changes** separately groups fields added, removed, or structurally altered. These never enter edit counts or size rankings.

All views share system-group, system-type, owner, and estimated-date filters. The largest-changes table caps its rendered preview at 500 rows while preserving the full filtered result in memory for drill-down.
