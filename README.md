# JSON record history

Licensed under the [MIT License](LICENSE).

Two local Python scripts keep semantic snapshots of a JSON directory or a list of JSON URLs and show changes in a Streamlit dashboard. Python 3.11+ works on Windows 10/11. SQLite is bundled with Python. Streamlit powers the dashboard; `openpyxl` reads Excel lists. No service, Git, or administrator rights are needed. URL mode needs access to the specified URLs.

## Windows setup

```powershell
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python version_json.py "C:\Example\JsonFiles"
streamlit run dashboard.py
```

If an existing Python installation has a broken `streamlit` command, use `python -m streamlit run dashboard.py` from the activated virtual environment.

To try the workflow without any supplied data, create a folder with two JSON records:

```powershell
New-Item -ItemType Directory -Force .\my_records
'{"modelID": 1, "systemGroup": "Aircraft", "systemType": "Fighter", "status": "active"}' | Set-Content .\my_records\1.json
'{"modelID": 2, "systemGroup": "Sensors", "systemType": "Radar", "status": "active"}' | Set-Content .\my_records\2.json
python version_json.py .\my_records --note "Baseline"
'{"modelID": 1, "systemGroup": "Aircraft", "systemType": "Fighter", "status": "retired"}' | Set-Content .\my_records\1.json
python version_json.py .\my_records --note "Status changed"
python version_json.py --list
streamlit run dashboard.py
```

The second snapshot should show one modified and one unchanged record. Store your actual JSON export outside this repository if you prefer; the snapshot database stays local in `json_history/`.

The dashboard sidebar lets you choose a **snapshot range** as the time period, inspect one snapshot within it, and filter by `systemGroup` and `systemType`. The filters apply to summary counts, historical trends, modified records, description changes, field analysis, and the change matrix. A group/type breakdown for the inspected snapshot includes removed records using their previous classification. Records missing either field appear as `(Unspecified)`. The classification field names can be changed with `group_fields` and `type_fields` in `config.json` if a later export uses different names; the defaults are `systemGroup` and `systemType`.

## URL and modelID lists

Use a `.txt` file with one full URL per line, or a `.csv`/`.xlsx` file with a `url` column:

```powershell
python version_json.py .\urls.txt
python version_json.py .\records.xlsx --column url
```

For a list of modelIDs, set `url_prefix` in `config.json` to the part of the URL before the ID. The default file leaves it empty because your endpoint is unknown. The ID is URL encoded and appended automatically. A text file contains one ID per line. CSV and Excel can use a `modelID`, `model_id`, `id`, or `record_id` column, detected automatically:

```text
modelID
1004
1005
```

```powershell
python version_json.py .\modelIDs.csv --note "September export"
python version_json.py .\modelIDs.xlsx --timeout 30
```

The list may also be JSON: `[1004, 1005]`, `{"modelIDs": [1004, 1005]}`, or an array of objects with ID or URL keys. Full HTTP(S) URLs are used directly, even when mixed with IDs in one list. `--url-prefix` overrides the configured prefix for one run. For endpoints needing text after the ID, use `--url-template "https://example.org/api/models/{modelID}.json"`; `{id}` is also accepted. Example configuration: `"url_prefix": "https://example.org/api/models/"` makes ID `1004` resolve to `https://example.org/api/models/1004`.

If the returned JSON has a stable ID field, that field remains the record identity and the URL is retained as its source reference. A response without an ID uses the requested modelID; a full-URL entry without an ID falls back to the URL. The program fetches the complete list before committing a snapshot. Any failed request, invalid JSON, duplicate ID, or ID mismatch aborts that snapshot, so a temporary retrieval failure cannot be mistaken for a removed record. A successful later snapshot in which an ID is absent from the list records a removal. `--dry-run` fetches and validates without saving. Text lists can contain comments beginning with `#`. Authentication is not built into this version; endpoints must be reachable with a normal HTTP(S) GET.

History is stored in `json_history/history.sqlite3` beside the scripts. The database stores each normalised JSON record for each snapshot plus precomputed record and field changes. Keep a backup of this file. `--history` and `--config` accept alternative paths. `--dry-run` validates and counts files without writing a snapshot. `--note` adds a label. Running again creates another timestamped snapshot, even if nothing changed.

## Comparison rules

Identity uses the first populated field in `id_fields` (the demos use `modelID`), then falls back to relative filename. Duplicate IDs abort by default. Filename moves with a stable ID retain identity and appear in record history. Object key order and JSON formatting do not matter. Arrays are ordered by default; list paths in `unordered_arrays` to ignore order. `array_keys` matches rows in selected arrays by a stable value such as `descrType`, avoiding shifts when rows are inserted.

Edit `config.json` to change ignored fields or paths, IDs, array rules, field weights, and magnitude thresholds. Field paths start at `$`. For array elements, `[]` matches any element in ignore and unordered path rules. `parameterDescr` is ignored by default because it is a definition rather than a record value. Other newly added or removed fields containing only null or empty values across at least 90% of comparable records are marked as schema only; value-bearing additions remain data changes. For small datasets, at least two records must show the schema pattern. Schema events remain in field history but do not enter modified counts or the field matrix. Review config choices when changing export formats.

## Configuration reference

The default [config.json](config.json) is created if missing. Use `--config path\to\other.json` to snapshot with a different file. The dashboard uses its stored snapshot settings plus the `config.json` beside the scripts for current display settings.

| Setting | Use |
| --- | --- |
| `id_fields` | Top-level fields checked in order for stable record identity. `modelID` is first for the demo data. If none is usable, directory mode uses the relative filename; URL mode uses the requested ID or full URL. |
| `group_fields` | Top-level fields checked in order for dashboard system-group classification. Default: `systemGroup`. |
| `type_fields` | Top-level fields checked in order for dashboard system-type classification. Default: `systemType`. Missing values show as `(Unspecified)`. |
| `url_prefix` | Base HTTP(S) URL automatically prepended to an ID list entry, such as `https://example.org/api/models/`. Leave empty for lists of full URLs or when passing `--url-template` or `--url-prefix`. |
| `updated_at_fields` | Top-level timestamp fields checked in order. Their values are excluded from semantic comparison and used only to estimate when a **meaningful modification** occurred. Defaults: `updated_at`, `updatedDate`. |
| `ignore_fields` | Field names ignored wherever they occur in the JSON, such as export-only metadata. Do not put fields here if you want their values compared. |
| `ignore_paths` | Exact JSON paths to ignore, such as `$.internal_metadata` or `$.parametrics[].parameterDescr`. The latter suppresses definition changes in the demo schema. |
| `unordered_arrays` | Array paths whose order is not meaningful, such as `$.tags`. Arrays remain ordered unless listed here. |
| `array_keys` | Maps an array path to a key that identifies its object rows, such as `"$.descriptions": "descrType"`. Unique keys let insertions and reordering be compared by row identity; duplicates fall back to positional comparison. |
| `important_fields` | Maps JSON paths to optional positive weights used in the weighted change ratio. Unlisted fields have weight 1. |
| `change_thresholds` | Upper bounds for dashboard Tiny, Small, and Medium weighted-ratio labels; values above Medium are Large. The dashboard also lets you adjust them interactively. |
| `schema_field_fraction` | Fraction of comparable records that must gain or lose the same empty/null field before that field is considered a schema migration. At least two records are always required. |
| `invalid_json` | `abort` stops a directory snapshot on invalid files; `skip` omits them. URL snapshots always abort on retrieval or parsing failures. |

### Change timing from `updated_at`

Snapshots record **when the dataset was observed**. For a record whose meaningful values changed, the tool separately stores `estimated_change_at`, the timestamp reported by the new record if it lies after the previous snapshot and no later than the new snapshot. Otherwise it uses the new snapshot time and labels the basis as `snapshot observation`. This does not claim the exact field-change time; it is an estimate supplied by the record. ISO 8601 timestamps with offsets or `Z` are accepted; timestamps without offsets are treated as UTC. A date-only value is interpreted as noon UTC on that date. Timestamp-only edits, ignored fields, and schema-only changes do not create a data-change event. Added and removed records use observation time. The dashboard shows both snapshot trends and a separate chart by estimated event date. Earlier history without event times is labelled as legacy observation timing.

The primary magnitude is changed meaningful leaf values divided by the larger leaf count of the old and new record. A weighted ratio is also stored using `important_fields`; the dashboard classifies that ratio with editable thresholds. Description changes additionally store an edited-word count using word-sequence comparison, including added and removed descriptions: Small below 50, Medium 50 through 250, Large above 250. JSON values remain available in field history. The dashboard reads the SQLite change log directly and does not recompare old snapshots.

Invalid JSON in directory mode aborts a snapshot by default. Set `invalid_json` to `skip` if desired; a skipped previously known file can then appear removed, so use this carefully. URL mode always aborts on retrieval or parsing errors. Keep the history directory outside a source directory.
