# Dashboard and versioning review

Reviewed and updated 24 September 2026. The existing `json_history/history.sqlite3` was read for compatibility checks, but was not changed or compacted.

The subsequent category-first dashboard redesign is documented in [DASHBOARD.md](DASHBOARD.md). It adds owner filtering, aggregate category trends, exclusive timing series, cached reads, all-record pagination, and direct endpoint inspection. Findings and benchmark measurements below describe the earlier storage/versioning review unless noted otherwise.

## Findings addressed

| Priority | Original issue | Change |
| --- | --- | --- |
| High | Every snapshot duplicated the full normalised dataset, including unchanged records. At 10,000 × 10–250 KB, this is about 100 MB–2.5 GB per snapshot before overhead. | SHA-256 addressed, zlib-compressed payloads in `record_blobs`; `records` retains per-snapshot identity, source and hash references. Exact payloads are shared across snapshots and reversions. |
| High | Incoming and previous snapshots, plus pending diffs, were all held as parsed Python objects. RAM demand can greatly exceed JSON byte size. | Incoming payloads spool to temporary SQLite storage. Previous records load on demand. Schema detection streams comparisons in a first pass; changed records are compared again when writing, trading CPU for bounded dataset memory. |
| High | Python equality treats `True == 1` and `1 == 1.0`; the diff could miss changes even though the stored hash changed. Empty object/array additions and removals produced no leaves. | Compare canonical JSON values; retain empty containers as leaves. Regression coverage includes nested type changes and empty schema fields. |
| High | The predecessor was selected before acquiring a write transaction, allowing overlapping writers to select the same predecessor. | Acquire `BEGIN IMMEDIATE` before reading the latest snapshot. A competing writer waits or fails with SQLite's normal timeout instead of silently creating a branch. |
| High | Changed ignore/identity/normalisation rules could be compared against incompatible historical payloads; ignored original data is no longer available. | Reject changed comparison settings and require a separate history baseline. Stored metrics remain historical; changing weights does not retroactively recalculate them. |
| Medium | URL mode opened independent urllib requests and accepted plaintext HTTP. | One reusable Requests session; HTTPS required even after redirects, normal TLS verification, GET retries, pooled connections, streamed decompressed response limit. |
| Medium | Dashboard filters extracted classifications from large record JSON for each snapshot and rerun. | Store small group/type metadata with each compressed payload. Dashboard queries use that metadata; legacy rows and changed classification settings have a payload-reading fallback. |
| Medium | The dashboard initially queried all history and plotted percentages on the same scale as counts. | Default to the latest 30 snapshots, separate percentage chart, expose database size/unique payload statistics, add ID search and a 500-option cap. |
| Medium | Record field detail lacked a record-first index; description event lookup scanned records for every field. | Add `(record_id, snapshot_id)` index and a dictionary for description event lookups. |
| Medium | Weighted ratios used only the new record's denominator, inflating scores after deletions. | Use the larger old/new weighted leaf count, consistent with the unweighted ratio. |

## Storage design and expected growth

Full compressed versions allow direct reconstruction without traversing a chain of patches. An unchanged snapshot still records membership and filenames, so removals, renames, and historical enumeration remain straightforward. Legacy rows and new compressed rows can coexist; conversion is explicit and creates a backup before replacing legacy payloads. `VACUUM` reclaims the old pages afterward. SQLite documents that deleted/replaced data otherwise leaves reusable pages inside the file, and that VACUUM may require up to twice the original database size in free space; the retained backup needs additional space. [SQLite VACUUM](https://www.sqlite.org/lang_vacuum.html)

For `N` records, `S` snapshots, average normalised bytes `B`, compressed/raw ratio `R`, and fraction `C` changed per subsequent snapshot, payload storage is approximately:

```text
N × B × R × [1 + (S − 1) × C]
```

This assumes roughly constant record count and distinct changed versions. Add snapshot references, indexes, classifications, and field-change values. Reversions can reduce payload growth further. For illustration only, at 1% changes per run and a 30% compressed/raw ratio, 30 snapshots need about 38.7 MB–967.5 MB of record payloads across the stated size range, rather than 3–75 GB of repeated full records. Neither the change rate nor compression ratio has been measured on production data.

Whole-record hashing does **not** deduplicate a shared subsection inside records with different IDs. zlib compresses repetition within each record. If shared schema sections dominate production data, the next candidates are a trained compression dictionary (for example Zstandard) or independently addressed sections. Both add format/versioning and maintenance costs. Delta chains also make arbitrary-version reads and retention more complicated; measure full compressed storage before adding them.

## Validation

`python -m unittest -v`: 12 tests pass. They exercise unchanged/reverted payload reuse, removal, empty/schema/type changes, rejected comparison-setting changes, failed-input and failed-write rollback, legacy migration and backup contents, migration rollback, dashboard queries on legacy/compressed data, one-snapshot/empty-filter UI states, session reuse, certificate-verification defaults, downgrade rejection, and the decompressed response cap.

The existing legacy dashboard also ran through Streamlit's application test harness without exceptions. HTTP tests mock transport; no production HTTPS endpoint or private CA was supplied. These are functional dashboard checks, not a browser visual-layout audit.

Repeatable benchmark: `python benchmark_versioner.py --records 10000 --bytes 10000`. Each record has a different ID and a fixed pseudorandom hex payload. Values below are decimal MB, and include the full database file:

| Snapshot | Database size | Increment | Snapshot time |
| --- | ---: | ---: | ---: |
| Baseline | 65.77 MB | 65.77 MB | 104.80 s |
| Unchanged | 67.83 MB | 2.06 MB | 7.54 s |
| 1% changed | 70.55 MB | 2.72 MB | 9.37 s |

There were 10,100 unique payloads: 100.87 MB of canonical JSON compressed to 58.87 MB. Integrity check returned `ok`. Timings include local filesystem, compression, diffing, and database work, but exclude source-fixture generation and network latency. These measurements were taken before a further metadata-only membership lookup optimisation. This synthetic fixture is not a production compression forecast or a full 10,000 × 250 KB load test.

A second run with **1,000 × 250 KB** records exercised the upper per-record size. The baseline was 143.84 MB (41.08 s); an unchanged snapshot added 0.20 MB (15.34 s); a 1% update added 1.65 MB (15.90 s). Its 1,010 unique payloads compressed from 252.49 MB to 144.31 MB, and integrity check returned `ok`.

## Remaining priorities

1. **Field-history size:** `field_changes.old_value/new_value` still contain text. Frequently changing large descriptions can dominate growth after record deduplication. Measure this separately, then consider compressed/addressed field values and lazy detail downloads. A retention policy should preserve complete retained snapshots and garbage-collect only unreferenced payloads.
2. **Avoid repeated downloads:** conditional GET with ETag/Last-Modified can save bandwidth when supported by the endpoint. This needs persisted validators and source-to-record mappings so a 304 response can reuse the right record and timestamp metadata. HTTPS session pooling saves connection setup but does not avoid payload transfers. Requests documents connection reuse and the need to consume/close streamed responses. [Requests advanced usage](https://requests.readthedocs.io/en/stable/user/advanced/)
3. **Long history dashboard:** the redesign caches aggregate reads, adds SQL pagination for all-record browsing, and supports unchanged observations and endpoint comparisons. Event headers for the selected range remain materialised in Python. For years of frequent, dense changes, persist category summaries and move event filtering/pagination fully into SQL.
4. **Input policy:** `invalid_json=skip` still permits false removals in directory mode; keep the default `abort`. An empty directory also represents removal of every record. A future explicit “allow empty dataset” flag would help guard scheduled exports. Each history should represent one logical dataset, even if its source directory changes.
5. **Path representation:** field paths use dot/bracket strings without escaping arbitrary JSON keys. Literal dots/brackets or keyed-array identifiers containing delimiters can collide with nested paths. If such keys occur in your data, move to an escaped path format with a history migration. Keyed-array reorderings can also generate a new payload hash despite an unchanged semantic diff; canonicalising them requires a new comparison-format baseline.
6. **Historic results:** comparison bug fixes affect future snapshots. Compaction preserves old precomputed field changes and scores; it does not retroactively repair them. Keep the backup if you need to reproduce older behavior.

## Operational notes

- Install updated dependencies with `python -m pip install -r requirements.txt`.
- Existing history is compatible with the new reader. Run `python version_json.py --compact-storage` to convert old payloads; this was not run against the user's history. Close the dashboard and stop writers for this maintenance task.
- Payload staging needs temporary disk space for one incoming compressed dataset. Per-record parsing/diffing and large field changes still consume memory; there is no claim of constant memory independent of individual record complexity.
- TLS applies to outgoing requests. Serving the Streamlit dashboard over HTTPS is a separate deployment task.
- No WAL mode was enabled. Keeping the existing SQLite journal mode avoids changing backup/sidecar-file handling; long write transactions can still block or be blocked by readers, and the default lock timeout may need tuning for a scheduled deployment.
