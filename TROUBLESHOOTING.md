# Dashboard troubleshooting

Use these checks to investigate snapshot/date filter failures and relationships that seem to move, disappear, or be re-added. They do not require changing the history database.

## Snapshot slider and date filters

### First identify the failing control

Record whether the failure occurs in **From / through snapshot**, **Time window**, **Changed from**, or **Changed through**. Copy the complete on-screen message and the Python traceback from the terminal running Streamlit. For a browser-only error without a Python traceback, copy the error from the browser developer console.

Also record the selected database/configuration paths, snapshot range, time window, browser version, and package versions from the Python environment serving the dashboard:

```powershell
python -c "import streamlit, pandas, sqlite3; print('Streamlit:', streamlit.__version__); print('pandas:', pandas.__version__); print('SQLite:', sqlite3.sqlite_version)"
```

### Separate session state from reproducible input problems

1. Use **Reset filters**, then **Refresh data**.
2. Stop and restart the actual Streamlit server. Restarting a browser computer does not restart a server hosted on another machine.
3. Open the dashboard in a private/incognito window using its base address, without `?record=...`, `snapshot=...`, or other query parameters.
4. Confirm the paths under **Data source**. Confirm that the browser is connected to the server process you restarted.
5. Try a known working small history with its matching configuration, leaving the original files intact.
6. If the controls work, select one snapshot, then widen the selection gradually. Record the smallest selection that reproduces the failure. Check both **All selected dates** and **Custom dates**.

Filter selections are kept in Streamlit session state between reruns, not in SQLite or `config.json`. Session state normally resets when the browser WebSocket session resets. If the failure returns after a fresh server and browser session, the same database values, configuration, package version, or widget interaction may be recreating it. See [Streamlit session-state behavior](https://docs.streamlit.io/develop/api-reference/caching-and-state/st.session_state).

Repeated records across snapshots are normal. Repeated snapshot dates and notes are also valid: slider labels contain the snapshot ID, making the options distinct. Snapshot IDs are SQLite primary keys. Repeated dates/notes were tested without a server-side exception.

**Known date-state weakness:** a local Streamlit AppTest reproduced custom date inputs retaining a January-to-April selection after the snapshot range was narrowed to a single March observation. The code clamps the inputs' defaults but does not explicitly reconcile their existing widget state. The reproduction did not produce a server-side crash; it is evidence of inconsistent state, not proof of the cause of a reported browser crash. Resetting filters is the immediate workaround.

### Check SQLite and snapshot metadata read-only

Open the selected database read-only in a SQLite tool and run:

```sql
PRAGMA quick_check;
PRAGMA integrity_check;
```

Both should return `ok`. The full integrity check can take longer on a large database. These checks validate SQLite structures; they do not establish that stored dates, JSON, compressed payloads, or application settings are valid. See [SQLite integrity checks](https://www.sqlite.org/pragma.html#pragma_integrity_check).

Inspect snapshot dates and predecessor links:

```sql
SELECT id, created_at, previous_id, note
FROM snapshots
ORDER BY id;

SELECT s.id, s.previous_id
FROM snapshots s
LEFT JOIN snapshots p ON p.id = s.previous_id
WHERE s.previous_id IS NOT NULL AND p.id IS NULL;
```

The second query should return no rows. A null predecessor on the initial baseline is expected. Look for malformed dates, implausible years, or unexpected timezone formats in the first query. Do not assume that snapshot IDs must be contiguous or that timestamps must be unique.

Check for missing compressed payloads in the current storage schema:

```sql
SELECT r.snapshot_id, r.record_id, r.hash
FROM records r
LEFT JOIN record_blobs b ON b.hash = r.hash
WHERE r.json_text IS NULL AND b.hash IS NULL;
```

This should return no rows. Legacy inline JSON records are deliberately excluded by the `json_text IS NULL` condition. A missing table in a much older schema is a schema-version difference, not by itself proof of corruption.

### Windows network shares

Both dashboards accept full file paths such as:

```text
\\server\share\history.sqlite3
//server/share/history.sqlite3
file://server/share/history.sqlite3
\\192.168.1.20\reports$\history.sqlite3
```

A server name alone is insufficient: include the share and database filename. The account running Streamlit must have share permissions. Browser-computer permissions do not grant access to the Streamlit host. HTTP URLs and database-server connection strings are not SQLite filenames. On non-Windows hosts, use a mounted share's local path.

An `invalid uri authority` error with an older dashboard version can be caused by converting UNC filenames into `file://server/...` directly for SQLite. The updated shared loader keeps the authority empty and puts the UNC server in the URI path, while retaining `mode=ro`. Spaces, administrative-share dollar signs, and filename punctuation are preserved. In file addresses, encode literal filename `#`, `%`, and `?` as `%23`, `%25`, and `%3F`.

Path-format tests cover UNC hosts, IP addresses, administrative shares, and punctuation; remote connection arguments were checked with mocks. They do not establish access to a particular live share. Successful local read-only connections and both dashboard source widgets were also tested.

SQLite still depends on the network filesystem's locking and reliability. WAL is not supported for a live database accessed across hosts over a network filesystem. For that setup, run the dashboard on the database host or use a properly produced consistent local copy. Do not copy only a live `.sqlite3` file while ignoring its WAL. The dashboards do not bypass locks, mark mutable databases immutable, or change journal mode. See [SQLite URI handling](https://www.sqlite.org/uri.html), [network filesystem considerations](https://www.sqlite.org/useovernet.html), and [WAL limitations](https://www.sqlite.org/wal.html).

## Relationships that appear reordered or re-added

### Verify the capture-time identity rule

For the default root-level `relations` array, the configuration includes:

```json
"array_keys": {
  "$.relations": ["childModelID", "relationType", "parentComponent"]
}
```

Keep other existing `array_keys` entries alongside this rule. Identity matching requires every entry in both compared arrays to be an object containing all three fields, with each combination unique within its array. A present `null` value is allowed; a missing field is not. Names are case-sensitive, and this root-level rule does not cover an array with a different name or location.

**One missing field or duplicate identity anywhere in either array causes the entire array to fall back to positional comparison.** An insertion, deletion, or reorder can then look like several edits because subsequent entries occupy different positions.

The current `config.json` is not necessarily the config used to capture an older snapshot. Inspect the capture-time settings, replacing the example snapshot ID:

```sql
SELECT id, created_at, config_json
FROM snapshots
WHERE id = 12;
```

Inspect both endpoint configurations if necessary. A change to the dashboard's current config does not reconstruct historical field-change rows.

### Use technical paths to distinguish identity matching from position matching

Choose one affected record and event, and inspect **Technical field paths** in the executive change detail view.

| Example path | Interpretation |
| --- | --- |
| `$.relations[childModelID=123,relationType=%22Parent%22,parentComponent=null].comments` | Composite identity matching was used. Selector values may be percent-encoded JSON. |
| `$.relations[0].comments` | Positional comparison was used for this array. Check missing keys, duplicate identities, and the capture-time rule. |

For the current schema, inspect the comparison endpoint and stored field rows directly if needed. Replace the example snapshot and record IDs:

```sql
SELECT snapshot_id, record_id, comparison_snapshot_id, change_type,
       filename, previous_filename, fields_changed
FROM record_changes
WHERE snapshot_id = 12 AND record_id = 'modelID:1004';

SELECT json_path, old_value, new_value, schema_only
FROM field_changes
WHERE snapshot_id = 12 AND record_id = 'modelID:1004'
  AND json_path LIKE '$.relations%'
ORDER BY json_path;
```

Older schemas may lack `comparison_snapshot_id`. Use the dashboard's before/after view in that case: after a capture gap it infers the actual last-seen predecessor, which may not be the immediately preceding observation. Compare the stored versions for the specific event, not today's source JSON or arbitrary adjacent snapshots.

### Check both complete arrays, including otherwise unrelated entries

Download the stored before/after JSON for the affected record and save them as `before.json` and `after.json`. This optional local diagnostic reports malformed items, missing keys, and duplicate identities without editing either file:

```python
import json
from collections import defaultdict
from pathlib import Path

keys = ("childModelID", "relationType", "parentComponent")
for filename in ("before.json", "after.json"):
    record = json.loads(Path(filename).read_text(encoding="utf-8-sig"))
    relations = record.get("relations", [])
    print(filename)
    if not isinstance(relations, list):
        print("  relations is not an array")
        continue
    identities = defaultdict(list)
    for index, entry in enumerate(relations):
        if not isinstance(entry, dict):
            print("  non-object entry at index", index)
            continue
        missing = [key for key in keys if key not in entry]
        if missing:
            print("  missing identity fields at index", index, missing)
            continue
        identity = tuple(json.dumps(entry[key], sort_keys=True) for key in keys)
        identities[identity].append(index)
    for identity, indexes in identities.items():
        if len(indexes) > 1:
            print("  duplicate identity", identity, "at indexes", indexes)
```

JSON serialization in the diagnostic retains distinctions such as integer `123` versus string `"123"`, boolean `true` versus integer `1`, and `null` versus `""`. Also compare case and whitespace: `"Parent"`, `"parent"`, and `"Parent "` are different identities. A genuine change to an identity field is recorded as removing the old relationship and adding the new one.

### Interpret remaining changes

- With valid unique identities, reordering alone produces no field changes. One relationship addition/removal can generate several leaf-level history rows, but the dashboard groups these into one relationship item.
- Changing a non-identity field is still a modification. A changed `seq` field is a value change even if the exporter changed it only to reflect a new order.
- Object field order is already ignored. `unordered_arrays` controls arrays whose order has no meaning; it is not required for keyed relationship matching.
- Keyed arrays currently retain their array order in stored payloads unless also configured as unordered. Reordering alone can therefore produce a different stored hash without a meaningful modification event; that is not evidence of a relationship removal/re-addition.
- **Source moved** describes a record's source filename changing, not a relationship moving inside its array.
- If stored paths use identities and the identity values are unchanged, compare stored field changes with dashboard grouping. A discrepancy at that point may be a presentation/classification issue rather than a matching issue. Record the path, operation label, and before/after values.

New exclusions under `dashboard_display.exclude_other_paths`, such as `$.relations[].seq`, can hide relationship bookkeeping changes and remove their contribution to dashboard metrics without altering captured history. Unlike parameter exclusions, `exclude_parameter_fields` does not apply to relationships. Do not hide identity fields as a substitute for diagnosing matching failures.

Changing `array_keys` or `unordered_arrays` requires a new history baseline under the current capture guard. The destructive `--rebaseline` command only accepts additional ignore rules; it cannot migrate array identities or array-order policies. Do not change these settings or scrub history merely to troubleshoot a display anomaly.

## Evidence to include in a bug report

For a filter failure, include the exact control/error, smallest failing snapshot range, database location type (local/UNC), config path, Streamlit/pandas versions, and whether a clean browser/server session or another history reproduces it.

For relationship anomalies, include the record ID, event snapshot ID, actual comparison snapshot ID, capture-time `array_keys` rule, technical field paths, and the relevant stored before/after entries. Include results for missing/duplicate identities across the complete arrays. Remove sensitive values before sharing examples, while preserving the types, missing fields, and duplicate patterns needed to reproduce the behavior.
