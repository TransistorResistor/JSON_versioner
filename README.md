# JSON record history

Licensed under the [MIT License](LICENSE).

Python scripts keep semantic snapshots of a JSON directory or a list of JSON URLs and show changes in a Streamlit dashboard. Python 3.11+ works on Windows 10/11. SQLite and zlib compression are bundled with Python. Streamlit powers the dashboard; `openpyxl` reads Excel lists; Requests provides pooled HTTPS connections. No service, Git, or administrator rights are needed. URL mode needs access to the specified URLs.

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

The dashboard focuses on **scale, scope, and timing across aggregate categories**, with system types as the default comparison. Choose an observation range and filter by `systemGroup`, `systemType`, and root-level `modelOwner`. Missing values appear as `(Unspecified)`. The first snapshot is excluded from change counts by default because it is a baseline inventory; enable **Include initial baseline as additions** when needed.

### Dashboard views

- **Category trends:** a category-by-observation heatmap, selected trend lines, population and event summaries, and system-type/owner breakdowns. Switch between affected percentage, change events, and population; group by system type, group, or owner. **Explore changes in this category** opens the filtered event list.
- **Content changes:** classifies changed JSON into Descriptions, Parameters, Relationships, Media, and Metadata, then compares those categories across system types and time. It reports item additions/removals/modifications and sizes modified descriptions by edited-word count. Proliferations, aliases, codes, and uncategorised root fields are Metadata by default.
- **Timing & scope:** observed or estimated timing by UTC day/week/month, composition of additions/removals/modifications, most affected fields, edited-word totals, and category/owner transfers. Timing fallback and record-timestamp series are mutually exclusive.
- **Change explorer:** search names, IDs, and sources; filter event kinds and minimum modification magnitude; browse 50 events per page. Browse all records at an observation, including unchanged records, with database-backed pagination. Open a record's complete observation timeline, compare any two stored versions directly, highlight before/after text, and download JSON or CSV. Streamlit 1.35+ supports row selection; older runtimes have a selection control below the table.
- **Storage & health:** current storage size, compression, legacy rows, snapshot inventory, and observation gaps. Collection failures and historical database-size samples are not recorded, so those are not inferred.

**Metric definitions:** a change event is one added/removed/meaningfully modified record at one observation. Unique affected records count each ID once across the selected period; repeated modifications are separate events. Per-observation affected percentage is `(added + removed + modified) / (current population + removed)`. It is not summed across observations. Population delta compares the first and last selected observations and includes owner/category transfers. Events at the first selected observation still compare against that observation's actual predecessor, which can lie outside the selected range.

Events use the record's group/type/owner at that event; removals use the previous version and transfers are attributed to their destination. Thus one ID may contribute to multiple owner/category rows over time while counting once in the overall unique-record total. The transfer table exposes the old and new values. A missing percentage means an excluded baseline or no population denominator, not zero change.

Content item totals use field history for modified records. A parameter entry with three changed fields is one modified parameter; added and removed keyed-array entries are one item each. Whole-record additions and removals are displayed separately and their contents are excluded from these item totals, so loading a new record with many parameters does not overwhelm editing trends. Description sizes default to Small below 50 edited words, Medium from 50 through 250, and Large above 250. Non-text changes within a description entry appear as Other.

Display fields are configurable using `group_fields`, `type_fields`, `owner_fields`, and `name_fields`. New compressed payload metadata includes owner and name values; older history falls back to its stored JSON without rewriting the database. Filters survive view navigation and reset via **Reset filters**. **Refresh data** clears cached results. See [DASHBOARD.md](DASHBOARD.md) for architecture and validation details.

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

The list may also be JSON: `[1004, 1005]`, `{"modelIDs": [1004, 1005]}`, or an array of objects with ID or URL keys. Full HTTPS URLs are used directly, even when mixed with IDs in one list. Plain HTTP URLs and redirects to HTTP are rejected. `--url-prefix` overrides the configured prefix for one run. For endpoints needing text after the ID, use `--url-template "https://example.org/api/models/{modelID}.json"`; `{id}` is also accepted. Example configuration: `"url_prefix": "https://example.org/api/models/"` makes ID `1004` resolve to `https://example.org/api/models/1004`.

If the returned JSON has a stable ID field, that field remains the record identity and the URL is retained as its source reference. A response without an ID uses the requested modelID; a full-URL entry without an ID falls back to the URL. The program fetches the complete list before committing a snapshot. Any failed request, invalid JSON, duplicate ID, or ID mismatch aborts that snapshot, so a temporary retrieval failure cannot be mistaken for a removed record. A successful later snapshot in which an ID is absent from the list records a removal. `--dry-run` fetches and validates without saving. Text lists can contain comments beginning with `#`. There are no application-specific authentication options; Requests' normal environment/proxy/netrc behavior applies.

History is stored in `json_history/history.sqlite3` beside the scripts. Each distinct normalised JSON payload is stored once, addressed by its SHA-256 hash and compressed with zlib. Snapshots store small references to those payloads plus precomputed record and field changes. Unchanged records and reversions reuse existing payloads. Keep a backup of this file. `--history` and `--config` accept alternative paths. `--dry-run` validates and counts files without writing a snapshot (temporary staging files are still used). `--note` adds a label. Running again creates another timestamped snapshot, even if nothing changed.

## HTTPS sessions

One Requests session is reused for each URL-list run, with connection pooling, normal certificate/hostname verification, and up to three retries for GET requests on connection failures and HTTP 429/500/502/503/504. Retry backoff and `Retry-After` are respected. `--timeout` is the read inactivity timeout; connection timeout is capped at 10 seconds. It is not a total time budget across the list or retries. Responses are streamed and limited to 20 MB **after** HTTP decompression.

Use HTTPS in your URL list, prefix, and template. For an internal certificate authority, set `REQUESTS_CA_BUNDLE` to a trusted PEM CA bundle; certificate verification is never disabled by the CLI. This secures outgoing data requests; it does not configure TLS for the Streamlit dashboard server.

## Existing databases and storage size

Existing history stays readable. New snapshots use compressed payloads immediately, while old full-text rows remain until explicitly converted:

```powershell
python version_json.py --compact-storage
# Or: python version_json.py --history C:\Example\history --compact-storage
```

The command first creates `history.sqlite3.pre-compression.bak`, converts legacy rows in a transaction, and runs SQLite `VACUUM` to reclaim free pages. It refuses to overwrite an existing backup. Stop other writers and close the dashboard during compaction; allow disk space for the backup and SQLite's temporary database copy. Keep the backup until you have checked the converted history. If VACUUM fails, the converted data can still be valid but the free pages have not been reclaimed. Older versions of these scripts cannot read compressed rows; restore the backup to return to those versions.

At 10,000 records of 10–250 KB, the old layout repeated roughly 100 MB–2.5 GB per snapshot, excluding indexes and field history. The new payload cost is approximately **unique record versions × average compressed record size**, plus one small reference per record per snapshot. Compression depends on your data. Field-change old/new values are still stored as text and can become significant with frequent large edits. Whole-record deduplication does not share matching subsections across records with different IDs.

Incoming records are staged on temporary disk, and previous payloads are read only when needed. RAM no longer needs to hold both complete datasets. Allow temporary space for a compressed incoming dataset; unusually large individual records and their diffs still need memory. The dashboard reads stored classification metadata instead of decompressing payloads for the usual filters, defaults to 30 snapshots, exposes storage statistics, and provides an ID search before selecting among large change histories. Changing classification field names may require reading payloads again.

The development workspace used `python -m unittest -v` for regression checks and synthetic benchmark scripts; those local test and mock files are intentionally excluded from the distribution. See [REVIEW.md](REVIEW.md) for findings and remaining tradeoffs.

## Comparison rules

Identity uses the first populated field in `id_fields` (the demos use `modelID`), then falls back to relative filename. Duplicate IDs abort by default. Filename moves with a stable ID retain identity and appear in record history. Object key order and JSON formatting do not matter. Arrays are ordered by default; list paths in `unordered_arrays` to ignore order. `array_keys` matches rows in selected arrays by a stable field or a list of fields, avoiding shifts when rows are inserted. The default uses composite identities for parameters, relationships, and proliferations.

Edit `config.json` to change ignored fields or paths, IDs, array rules, field weights, and magnitude thresholds. Changes to identity, ignore, timestamp-exclusion, or array-comparison settings require a new `--history` baseline: discarded values cannot be recovered from an old normalised payload. Display thresholds and classification fields can change without resetting history. Field paths start at `$`. For array elements, `[]` matches any element in ignore and unordered path rules. `parameterDescr` is ignored by default because it is a definition rather than a record value. Other newly added or removed fields containing only null or empty values across at least 90% of comparable records are marked as schema only; value-bearing additions remain data changes. For small datasets, at least two records must show the schema pattern. Schema events remain in field history but do not enter modified counts or the field matrix. Empty objects/arrays are counted as leaves, and booleans, integers, and floating-point JSON values are compared distinctly.

The default array identities now use composite keys for Parameters, Relationships, and Proliferations so the dashboard can distinguish their item operations reliably. If your existing history was created with the earlier single-field defaults, either keep its original `array_keys` configuration when appending snapshots or start a new history directory for these comparison rules.

## Configuration reference

The default [config.json](config.json) is created if missing. Use `--config path\to\other.json` to snapshot with a different file. The dashboard uses its stored snapshot settings plus the `config.json` beside the scripts for current display settings.

| Setting | Use |
| --- | --- |
| `id_fields` | Top-level fields checked in order for stable record identity. `modelID` is first for the demo data. If none is usable, directory mode uses the relative filename; URL mode uses the requested ID or full URL. |
| `group_fields` | Top-level fields checked in order for dashboard system-group classification. Default: `systemGroup`. |
| `type_fields` | Top-level fields checked in order for dashboard system-type classification. Default: `systemType`. Missing values show as `(Unspecified)`. |
| `owner_fields` | Top-level fields checked in order for owner filtering and grouping. Default: `modelOwner`. |
| `name_fields` | Top-level fields checked in order for record display/search names. Defaults: `nomenclature`, `name`. |
| `url_prefix` | Base HTTPS URL automatically prepended to an ID list entry, such as `https://example.org/api/models/`. Leave empty for lists of full URLs or when passing `--url-template` or `--url-prefix`. |
| `updated_at_fields` | Top-level timestamp fields checked in order. Their values are excluded from semantic comparison and used only to estimate when a **meaningful modification** occurred. Defaults: `updated_at`, `updatedDate`. |
| `ignore_fields` | Field names ignored wherever they occur in the JSON, such as export-only metadata. Do not put fields here if you want their values compared. |
| `ignore_paths` | Exact JSON paths to ignore, such as `$.internal_metadata` or `$.parametrics[].parameterDescr`. The latter suppresses definition changes in the demo schema. |
| `unordered_arrays` | Array paths whose order is not meaningful, such as `$.tags`. Arrays remain ordered unless listed here. |
| `array_keys` | Maps an array path to one field or a list of fields that identifies its object rows, such as `"$.descriptions": "descrType"` or `"$.parametrics": ["component", "parameter"]`. Unique identities let insertions and reordering be compared by item identity; duplicates fall back to positional comparison. Changing this setting requires a new history baseline. |
| `content_categories` | Dashboard mapping from normalised JSON array paths to content categories. Exactly one entry may use `"fallback": true`; the default fallback is Metadata, so proliferations and uncategorised fields appear there. This is a display setting and does not require a new baseline. |
| `description_edit_thresholds` | Edited-word boundaries for modified descriptions. Defaults: Small below 50, Medium from 50 through 250, Large above 250. This is a display setting. |
| `important_fields` | Maps JSON paths to optional positive weights used in the weighted change ratio. Unlisted fields have weight 1. |
| `change_thresholds` | Legacy magnitude-band configuration retained for older dashboards. The current explorer filters directly by minimum weighted change percentage. |
| `schema_field_fraction` | Fraction of comparable records that must gain or lose the same empty/null field before that field is considered a schema migration. At least two records are always required. |
| `invalid_json` | `abort` stops a directory snapshot on invalid files; `skip` omits them. URL snapshots always abort on retrieval or parsing failures. |

### Change timing from `updated_at`

Snapshots record **when the dataset was observed**. For a record whose meaningful values changed, the tool separately stores `estimated_change_at`, the timestamp reported by the new record if it lies after the previous snapshot and no later than the new snapshot. Otherwise it uses the new snapshot time and labels the basis as `snapshot observation`. This does not claim the exact field-change time; it is an estimate supplied by the record. ISO 8601 timestamps with offsets or `Z` are accepted; timestamps without offsets are treated as UTC. A date-only value is interpreted as noon UTC on that date. Timestamp-only edits, ignored fields, and schema-only changes do not create a data-change event. Added and removed records use observation time. The dashboard shows both snapshot trends and a separate chart by estimated event date. Earlier history without event times is labelled as legacy observation timing.

The primary magnitude is changed meaningful leaf values divided by the larger leaf count of the old and new record. A weighted ratio is also stored using `important_fields`; the explorer can filter modifications by this percentage. Description changes additionally store an edited-word count using word-sequence comparison, including added and removed descriptions. JSON values remain available in field history. Aggregate trends read the precomputed change log; the record inspector separately compares the actual selected endpoint versions, so intermediate reverted edits are not counted as endpoint differences.

Invalid JSON in directory mode aborts a snapshot by default. Set `invalid_json` to `skip` if desired; a skipped previously known file can then appear removed, so use this carefully. URL mode always aborts on retrieval or parsing errors. Keep the history directory outside a source directory.
