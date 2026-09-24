# Category dashboard architecture

The dashboard is organised around comparing **system types**, with system group and `modelOwner` as cross-cutting dimensions. Record-level inspection explains an aggregate result rather than being the starting page.

## Views and mockups

`mockups/category-trends.html` contains three interactive mockup states: category trends, timing/scope, and category drill-down. They use clearly labelled illustrative data, with local owner/group filtering. The design choices carry into the Streamlit implementation, which uses native controls and charts rather than copying the mockup's exact CSS.

The implemented views are Category trends, Timing & scope, Change explorer, and Storage & health. Only the active view executes its detailed queries. Root-level `modelOwner` is filtered and displayed alongside system group/type. Missing owner values remain visible as `(Unspecified)`; no owners were invented or injected into existing user records.

## Separation of responsibilities

- `dashboard.py`: navigation, filters, cache boundaries, charts, tables, CSV/JSON downloads, and comparison presentation.
- `dashboard_data.py`: read-only connection lifecycle, classification/event views, aggregation definitions, timing buckets, record pagination, endpoint comparison, and escaped word highlighting. It can be tested without a browser or Streamlit runtime.
- `storage.py`: compressed content lookup and small classification metadata, now including configurable owner/name fields. Old payloads with incomplete metadata fall back to stored JSON.
- `test_dashboard.py`: independent analytical fixtures and application navigation tests, alongside the existing versioning suite.
- `verify_dashboard_ui.py`: optional Playwright browser smoke checks and screenshots. Run a local dashboard on port 8517 first. Pass `--renderer <visualize-skill>/scripts/render.py` to additionally check the inline mockup in the skill's preview wrapper. This optional QA dependency is not needed to run the dashboard.

All application connections use SQLite `mode=ro`. Temporary classification views and selected-event tables live on the connection, and connections close in `finally`. The app does not migrate or compact the selected history database.

## Aggregation contract

1. **Population:** records present at the observation under the selected group/type/owner filters.
2. **Change events:** added, removed, or meaningfully modified record at an observation. One record edited at three observations produces three events.
3. **Unique records affected:** distinct IDs across meaningful events in the selected observation range. Category rows are not additive when a record changes category or owner.
4. **Affected percentage:** events divided by current population plus removed records, calculated independently for each observation/category. Missing denominators and an excluded initial baseline produce null rates. Zero is reserved for a comparable population with no meaningful events.
5. **Event attribution:** current category/owner for additions and modifications; previous category/owner for removals. Transfers belong to the destination for event counts; the scope view separately shows old/new dimensions. Category populations can change without record additions/removals.
6. **Range semantics:** every observation compares to its actual predecessor, even when that predecessor is outside the selected range. The initial database baseline is excluded by default, with an explicit opt-in. Population delta instead compares the first and last selected populations.
7. **Timing:** UTC day, Monday-start week, or month. Observed timing uses the snapshot timestamp. Estimated timing uses a valid stored record timestamp or a mutually exclusive observation fallback. Estimated dates can lie before the first selected observation.
8. **Field scope:** group array paths under `[]`; distinguish unique IDs, unique record/observation pairs, individual field edits, and edited-word counts. Different fields overlap, so summing their unique counts is not a dataset total. Added/removed records have no precomputed leaf diff.
9. **Record comparison:** compute differences between selected normalised endpoint payloads, not a sum of historical field changes. Warn when comparison settings differ. Historical category totals and scores are not rewritten.

## Performance boundaries

SQLite groups snapshot populations before returning them. Normal views operate on small classification values and change-event headers rather than full record payloads. Period results are cached with a four-entry bound and keyed by path, database/WAL modification state, display configuration, and observation range. Manual refresh clears caches. Field analysis runs only on its view and reads paths/counts rather than old/new field values. Payloads are loaded for the inspected record only.

All-record browsing uses `LIMIT/OFFSET` and a filtered SQL count. Event browsing displays 50 headers at a time but **the selected range's event headers are still materialised and filtered in Python**. This is appropriate for sparse updates over the default 30 observations; a long range with 10,000 changes every observation can consume substantial memory. The next scaling step is persistent category summaries plus fully SQL-filtered event pagination, not silently truncating metrics. CSV exports contain the complete filtered event result; displayed aggregate tables cap previews at 500 rows.

Changing owner/name settings or reading older payload metadata can trigger decompression on the first load. The cache and small per-connection metadata cache limit repeated work; no persistent metadata backfill is performed by the dashboard.

## Validation

Automated tests cover baseline exclusion, population denominators, removed-only categories, repeated edits versus unique IDs, owner moves, missing owners, old metadata fallback, exclusive timing series, record pagination/name search, HTML escaping, unchanged observation histories, endpoint reversions, and navigation/filter persistence. The original snapshot/HTTPS/compaction tests remain in the suite.

The synthetic `python benchmark_dashboard.py` run used 10,000 records across 30 observations (300,000 membership rows), with 1% changing per later observation. A cold read returned 180 population aggregates and 12,900 event headers, including 10,000 baseline additions, in **5.665 seconds**. Excluding the baseline produced the expected 2,900 change events. This measures aggregate-query scale with small metadata-bearing payloads; it is not a 2.5 GB payload benchmark or an older-metadata decompression benchmark. Repeated dashboard interactions reuse cached period results.

Browser smoke checks exercise the actual legacy history in read-only mode and the three mockup views. Mockup owner selection changes its displayed measures, and a narrow-screen preview is checked. Tests use the installed Streamlit 1.30 compatibility selector; native row selection uses the documented API available from Streamlit 1.35 onward. [Streamlit row-selection documentation](https://docs.streamlit.io/develop/tutorials/elements/dataframe-row-selections)

The heatmap uses Altair's rectangle mark with categorical axes and numeric colour encoding. [Altair rectangle marks](https://altair-viz.github.io/user_guide/marks/rect.html)

## Deliberate limits

- No write controls or automatic compaction in the dashboard.
- No inferred failed collections, exact edit times, or storage-growth history where the source database has not recorded them.
- Old ignored fields cannot be recovered from normalised JSON. Owner fields previously excluded from snapshots remain unspecified there.
- Very large changed values have a 20,000-character comparison preview; full values remain in downloads. Existing ambiguous JSON path escaping is unchanged.
