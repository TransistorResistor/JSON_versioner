# Demo outcomes

The generated September history uses all 26 records from `test_records` across four snapshots.

## What the dashboard shows

- Six record-update events across five system types and four system groups.
- One grouped structure-only change affecting 24 unchanged records; it does not enter edit counts or size rankings.
- The 9M96 update changes six logical parameter items and is classified as Medium.
- Single-parameter updates are classified as Small even when several physical JSON leaves change.
- `Changes by system type` keeps `systemGroup` and `systemType` together, which distinguishes the Aircraft, Missiles, Sensors, and Weapon portfolios.

## Parameter filtering outcome

The source records often repeat one parameter fact across `parameterValue`, `uom`, and `parameterUomValue`. They also carry `parameterOnly`, `parameterDescr`, `seq`, `dataType`, and `component` metadata.

The executive view now:

1. counts one changed parameter object as one logical change for sizing;
2. displays `parameterUomValue` when available instead of repeating value and unit separately;
3. falls back to `parameterValue` and a changed `uom` when no combined value exists;
4. retains `comments` and fields containing `subtitle`;
5. suppresses parameter descriptions, duplicate labels, sequence numbers, data types, and component metadata from the executive diff;
6. leaves the complete stored before/after JSON available for audit.

This produced concise details such as `parameterUomValue + comments`, rather than six or seven rows for a single parameter update. A comment-only change correctly remains visible without inventing a value change.
