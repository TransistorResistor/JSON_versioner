# Semantic configuration generator

This utility turns a JSON Versioner SQLite history snapshot into a reviewable field catalogue for natural-language change labels.

It produces:

- `generated_config.json` — regenerated machine observations and safe defaults;
- `curated_overrides.json` — durable human or AI-approved decisions;
- `effective_config.json` — the generated catalogue with overrides applied;
- `schema_report.json` — fingerprint, uncertain fields, review fields, and candidate array keys;
- `adaptation_prompt.md` — a ready-to-use prompt for executive, operational, analyst, audit, or minimal adaptations.

## Run it

From the repository root:

```powershell
python semantic_config_generator/generate_config.py `
  json_history/history.sqlite3 `
  --output semantic-config
```

UNC database and output paths are accepted directly:

```powershell
python semantic_config_generator/generate_config.py `
  "\\server\share\history\history.sqlite3" `
  --output "\\server\share\history\semantic-config"
```

The database must already exist. It is opened using its native filesystem path with SQLite query-only mode enabled. The generator never constructs a `file:` URI, calls `Path.resolve()`, or writes to the history database, avoiding common UNC authority and path-normalisation problems.

Use `--snapshot-id 42` to inspect a particular snapshot and `--sample-size 50000` to inspect more record payloads. Historical field-change counts are aggregated across the complete `field_changes` table when that table is available; the sample limit only applies to payload inspection.

## Review and adapt

1. Inspect `schema_report.json` and the important or low-confidence entries in `generated_config.json`.
2. Give `adaptation_prompt.md` and `generated_config.json` to an AI, filling in the target audience and decisions.
3. Review the returned JSON and merge its `fields` entries into `curated_overrides.json`.
4. Run the generator again. It refreshes the generated catalogue and rebuilds `effective_config.json` without overwriting the overrides.

If the schema changes, the fingerprint changes. On every rerun, `schema_report.json` records paths added or removed since the preceding generation and any observed type changes. New fields receive conservative defaults; type changes are highlighted for review rather than automatically labelled. Keep generated outputs in source control or an artifact store if you also want a longer schema history.

## Safety model

- Existing database check prevents SQLite from creating a database for a mistyped path.
- `PRAGMA query_only=ON` blocks database writes.
- Unknown fields default to routine metadata and require review.
- Type changes default to review and should not receive an automatic natural-language label.
- Generated observations and curated decisions remain separate.
