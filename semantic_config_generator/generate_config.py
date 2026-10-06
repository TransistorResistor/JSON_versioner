"""Generate a semantic dashboard configuration from a JSON Versioner SQLite history.

The database is opened by its native filesystem path rather than a SQLite URI.
That is deliberate: native paths work for drive-letter paths and Windows UNC paths,
while file: URI handling of UNC authorities varies between SQLite builds.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
import zlib
from collections import Counter, defaultdict
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


GENERATOR_VERSION = 1
ROOT_LABELS = {
    "modelOwner": ("Owner", "owner", "review"),
    "systemType": ("System type", "category", "contextual"),
    "systemGroup": ("System group", "category", "contextual"),
    "nomenclature": ("Name", "name", "contextual"),
    "name": ("Name", "name", "contextual"),
    "status": ("Status", "status", "review"),
    "classification": ("Classification", "classification", "review"),
}
ID_NAMES = {"id", "modelid", "record_id", "recordid", "uuid", "guid"}
UNIT_NAMES = {"unit", "units", "uom", "measureunit"}
IDENTITY_HINTS = {"name", "code", "key", "type", "parameter", "component", "alias"}
NUMBER_RE = re.compile(r"^-?(?:\d+(?:\.\d*)?|\.\d+)$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[T ][0-9:.+-]+Z?)?$")


class GeneratorError(RuntimeError):
    pass


@dataclass
class FieldProfile:
    occurrences: int = 0
    records: set[str] = field(default_factory=set)
    types: Counter = field(default_factory=Counter)
    samples: list[Any] = field(default_factory=list)
    changes: int = 0
    schema_only_changes: int = 0

    def observe(self, record_id: str, value: Any) -> None:
        self.occurrences += 1
        self.records.add(record_id)
        self.types[value_type(value)] += 1
        rendered = compact_sample(value)
        if rendered not in self.samples and len(self.samples) < 4:
            self.samples.append(rendered)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a reviewable semantic configuration from a JSON Versioner SQLite snapshot."
    )
    parser.add_argument("database", help=r"History database path, including UNC paths such as \\server\share\history.sqlite3")
    parser.add_argument("--output", default="semantic-config", help="Output directory (default: semantic-config)")
    parser.add_argument("--snapshot-id", type=int, help="Snapshot to profile (default: latest)")
    parser.add_argument("--sample-size", type=int, default=10000, help="Maximum records to inspect (default: 10000)")
    return parser.parse_args(argv)


def native_path(value: str | Path) -> Path:
    """Return a native path without resolve(), which can damage unavailable UNC paths."""
    text = str(value).strip().strip('"')
    if not text:
        raise GeneratorError("Database path is empty")
    return Path(text).expanduser()


def open_readonly(path_value: str | Path) -> sqlite3.Connection:
    """Open an existing SQLite file read-only using its native path.

    Existence is checked before sqlite3.connect so a typo can never create a new
    database. PRAGMA query_only prevents writes while retaining normal WAL reads.
    """
    path = native_path(path_value)
    if not path.is_file():
        raise GeneratorError(f"Database does not exist or is not a file: {path}")
    db = sqlite3.connect(str(path))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    return db


def require_history_schema(db: sqlite3.Connection) -> None:
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    missing = {"snapshots", "records"} - tables
    if missing:
        raise GeneratorError("Not a JSON Versioner history database; missing: " + ", ".join(sorted(missing)))


def select_snapshot(db: sqlite3.Connection, requested: int | None) -> sqlite3.Row:
    if requested is None:
        row = db.execute("SELECT * FROM snapshots ORDER BY id DESC LIMIT 1").fetchone()
    else:
        row = db.execute("SELECT * FROM snapshots WHERE id=?", (requested,)).fetchone()
    if row is None:
        raise GeneratorError("The requested history snapshot was not found")
    return row


def has_table(db: sqlite3.Connection, name: str) -> bool:
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def record_payloads(db: sqlite3.Connection, snapshot_id: int, limit: int) -> Iterable[tuple[str, Any]]:
    blob_storage = has_table(db, "record_blobs")
    if blob_storage:
        query = """SELECT r.record_id,r.json_text,b.payload
                   FROM records r LEFT JOIN record_blobs b ON b.hash=r.hash
                   WHERE r.snapshot_id=? ORDER BY r.record_id LIMIT ?"""
        for row in db.execute(query, (snapshot_id, limit)):
            if row["json_text"] is not None:
                text = row["json_text"]
            elif row["payload"] is not None:
                text = zlib.decompress(row["payload"]).decode("utf-8")
            else:
                raise GeneratorError(f"Missing stored payload for record {row['record_id']}")
            yield str(row["record_id"]), json.loads(text)
    else:
        query = "SELECT record_id,json_text FROM records WHERE snapshot_id=? ORDER BY record_id LIMIT ?"
        for row in db.execute(query, (snapshot_id, limit)):
            yield str(row["record_id"]), json.loads(row["json_text"])


def key_name(value: str) -> str:
    return value.casefold().replace("-", "").replace("_", "")


def choose_array_key(items: list[Any]) -> str | list[str] | None:
    objects = [item for item in items if isinstance(item, dict)]
    if len(objects) < 2:
        return None
    common = set(objects[0])
    for item in objects[1:]:
        common.intersection_update(item)
    preferred = sorted(common, key=lambda key: (
        0 if key_name(key) in ID_NAMES else 1 if key_name(key) in IDENTITY_HINTS else 2,
        len(key), key.casefold()))
    for key in preferred:
        values = [item.get(key) for item in objects]
        if all(value not in (None, "", [], {}) for value in values) and len({json.dumps(v, sort_keys=True) for v in values}) == len(values):
            return key
    return None


def choose_grouped_array_key(items: list[tuple[str, dict]]) -> str | None:
    if not items:
        return None
    common = set(items[0][1])
    by_record: dict[str, list[dict]] = defaultdict(list)
    for record_id, item in items:
        common.intersection_update(item)
        by_record[record_id].append(item)
    preferred = sorted(common, key=lambda key: (
        0 if key_name(key) in ID_NAMES else 1 if key_name(key) in IDENTITY_HINTS else 2,
        len(key), key.casefold()))
    for key in preferred:
        valid = True
        for record_items in by_record.values():
            values = [item.get(key) for item in record_items]
            encoded = [json.dumps(value, sort_keys=True) for value in values]
            if any(value in (None, "", [], {}) for value in values) or len(set(encoded)) != len(encoded):
                valid = False
                break
        if valid:
            return key
    return None


def walk(value: Any, path: str, record_id: str, profiles: dict[str, FieldProfile], arrays: dict[str, list[tuple[str, dict]]]) -> None:
    if isinstance(value, dict):
        if not value:
            profiles[path].observe(record_id, value)
        for key, child in value.items():
            walk(child, f"{path}.{key}", record_id, profiles, arrays)
    elif isinstance(value, list):
        array_path = path + "[]"
        profiles[path].observe(record_id, value)
        remaining = max(0, 200 - len(arrays[path]))
        if remaining:
            arrays[path].extend((record_id, item) for item in value[:remaining] if isinstance(item, dict))
        for child in value:
            walk(child, array_path, record_id, profiles, arrays)
    else:
        profiles[path].observe(record_id, value)


def normalize_history_path(path: str) -> str:
    # Stored keyed-array paths include [...]; the semantic catalogue groups them as [].
    return re.sub(r"\[[^\]]*\]", "[]", path)


def attach_change_counts(db: sqlite3.Connection, profiles: dict[str, FieldProfile]) -> None:
    if not has_table(db, "field_changes"):
        return
    for row in db.execute("SELECT json_path,COUNT(*) AS n,SUM(schema_only) AS schema_n FROM field_changes GROUP BY json_path"):
        path = normalize_history_path(row["json_path"])
        profiles[path].changes += int(row["n"] or 0)
        profiles[path].schema_only_changes += int(row["schema_n"] or 0)


def value_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int) and not isinstance(value, bool):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    text = str(value).strip()
    if DATE_RE.match(text):
        return "date"
    if NUMBER_RE.match(text):
        return "numeric_text"
    return "text"


def compact_sample(value: Any) -> Any:
    if isinstance(value, list):
        return f"[{len(value)} items]"
    if isinstance(value, dict):
        return "{" + ", ".join(list(value)[:4]) + (", …" if len(value) > 4 else "") + "}"
    text = value if not isinstance(value, str) else value.strip()
    if isinstance(text, str) and len(text) > 100:
        return text[:97] + "…"
    return text


def words_from_key(key: str) -> str:
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", key).replace("_", " ").replace("-", " ")
    return " ".join(spaced.split()).capitalize() or "Value"


def field_key(path: str) -> str:
    return path.rsplit(".", 1)[-1].replace("[]", "")


def infer_semantics(path: str, profile: FieldProfile) -> tuple[str, str, str, float]:
    key = field_key(path)
    if path.count(".") == 1 and key in ROOT_LABELS:
        label, semantic, importance = ROOT_LABELS[key]
        return label, semantic, importance, 0.99
    folded = key_name(key)
    types = set(profile.types)
    if folded in ID_NAMES or folded.endswith("id"):
        return words_from_key(key), "identifier", "hidden", 0.94
    if folded in UNIT_NAMES:
        return words_from_key(key), "unit", "contextual", 0.93
    if "date" in types or folded.endswith("date") or folded.endswith("at"):
        return words_from_key(key), "date", "contextual", 0.86
    if types & {"integer", "number", "numeric_text"}:
        return words_from_key(key), "measurement", "contextual", 0.76
    if any(word in folded for word in ("description", "descr", "summary", "comment", "note")):
        return words_from_key(key), "content", "routine", 0.83
    if len(profile.samples) <= 4 and profile.occurrences > len(profile.samples) * 3:
        return words_from_key(key), "category", "contextual", 0.66
    return words_from_key(key), "metadata", "routine", 0.55


def schema_fingerprint(entries: dict[str, dict[str, Any]]) -> str:
    shape = [(path, tuple(entry["observed_types"])) for path, entry in sorted(entries.items())]
    return hashlib.sha256(json.dumps(shape, separators=(",", ":")).encode()).hexdigest()


def merge(base: Any, override: Any) -> Any:
    if isinstance(base, dict) and isinstance(override, dict):
        result = dict(base)
        for key, value in override.items():
            result[key] = merge(result.get(key), value) if key in result else value
        return result
    return override


def compare_schemas(previous: dict | None, current: dict) -> dict:
    if not previous or "fields" not in previous:
        return {"previous_fingerprint": None, "added_paths": [], "removed_paths": [], "type_changes": []}
    old_fields = previous.get("fields", {})
    new_fields = current.get("fields", {})
    type_changes = []
    for path in sorted(set(old_fields) & set(new_fields)):
        old_types = sorted(old_fields[path].get("observed_types", []))
        new_types = sorted(new_fields[path].get("observed_types", []))
        if old_types != new_types:
            type_changes.append({"path": path, "before": old_types, "after": new_types})
    return {
        "previous_fingerprint": previous.get("schema_fingerprint"),
        "added_paths": sorted(set(new_fields) - set(old_fields)),
        "removed_paths": sorted(set(old_fields) - set(new_fields)),
        "type_changes": type_changes,
    }


def build_configuration(db_path: str | Path, snapshot_id: int | None, sample_size: int) -> tuple[dict, dict]:
    if sample_size < 1:
        raise GeneratorError("--sample-size must be at least 1")
    with closing(open_readonly(db_path)) as db:
        require_history_schema(db)
        snapshot = select_snapshot(db, snapshot_id)
        profiles: dict[str, FieldProfile] = defaultdict(FieldProfile)
        arrays: dict[str, list[tuple[str, dict]]] = defaultdict(list)
        sampled = 0
        for record_id, payload in record_payloads(db, int(snapshot["id"]), sample_size):
            walk(payload, "$", record_id, profiles, arrays)
            sampled += 1
        attach_change_counts(db, profiles)
        total = db.execute("SELECT COUNT(*) FROM records WHERE snapshot_id=?", (snapshot["id"],)).fetchone()[0]
        entries: dict[str, dict[str, Any]] = {}
        for path, profile in sorted(profiles.items()):
            if path == "$":
                continue
            label, semantic, importance, confidence = infer_semantics(path, profile)
            entries[path] = {
                "label": label,
                "semantic_type": semantic,
                "importance": importance,
                "confidence": confidence,
                "observed_types": sorted(profile.types),
                "present_in_sample_percent": round(100 * len(profile.records) / sampled, 1) if sampled else 0,
                "historical_changes": profile.changes,
                "schema_only_changes": profile.schema_only_changes,
                "examples": profile.samples,
            }
        config = {
            "format_version": GENERATOR_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": {
                "database_name": native_path(db_path).name,
                "snapshot_id": int(snapshot["id"]),
                "records_in_snapshot": int(total),
                "records_sampled": sampled,
            },
            "schema_fingerprint": schema_fingerprint(entries),
            "defaults": {
                "unknown_field": {"semantic_type": "metadata", "importance": "routine"},
                "type_change": {"importance": "review", "automatic_label": False},
                "new_field": {"importance": "routine", "requires_review": True},
            },
            "array_keys": {path: candidate for path, values in sorted(arrays.items())
                           if (candidate := choose_grouped_array_key(values))},
            "fields": entries,
        }
        report = {
            "schema_fingerprint": config["schema_fingerprint"],
            "field_patterns": len(entries),
            "low_confidence_fields": [path for path, value in entries.items() if value["confidence"] < 0.7],
            "review_fields": [path for path, value in entries.items() if value["importance"] == "review"],
            "candidate_array_keys": config["array_keys"],
        }
        return config, report


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GeneratorError(f"Cannot read {path}: {exc}") from exc


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def adaptation_prompt(config: dict, report: dict) -> str:
    compact = {
        "schema_fingerprint": config["schema_fingerprint"],
        "field_patterns": report["field_patterns"],
        "review_fields": report["review_fields"][:30],
        "low_confidence_fields": report["low_confidence_fields"][:30],
        "candidate_array_keys": report["candidate_array_keys"],
    }
    return f"""# AI configuration adaptation prompt

You are adapting a generated semantic field catalogue for a JSON change dashboard.

## Desired direction

Choose one or combine several:

- **Executive:** surface additions, removals, responsibility changes, and material capability changes; hide routine metadata.
- **Operational:** emphasize ownership, status, freshness, completeness, and workflow changes.
- **Analyst:** retain more technical context, measurements, relationships, and source changes.
- **Audit:** preserve neutral wording, identifiers, provenance, and traceability; avoid inferred importance.
- **Minimal:** show only additions, removals, and explicitly approved high-importance fields.

Target direction: `<describe the audience and decisions here>`

## Rules

1. Return JSON only: a small override object, not a rewritten generated catalogue.
2. Put field changes under `fields` using the exact normalized paths supplied in `generated_config.json`.
3. You may change `label`, `semantic_type`, `importance`, `unit`, `group`, and `description`.
4. Allowed importance values are `hidden`, `routine`, `contextual`, and `review`.
5. Never invent a path, unit, business meaning, or array key. Mark uncertainty with `needs_human_review: true`.
6. Do not copy examples containing sensitive values into labels or descriptions.
7. Preserve the generated file. Your output will be merged into `curated_overrides.json`.

## Catalogue summary

```json
{json.dumps(compact, indent=2, ensure_ascii=False)}
```

## Requested output shape

```json
{{
  "profile": "executive",
  "notes": "Short explanation of the adaptation",
  "fields": {{
    "$.examplePath": {{
      "label": "Example label",
      "importance": "review"
    }}
  }}
}}
```

Review `generated_config.json` before answering. Prefer a short override containing only deliberate changes.
"""


def run(args: argparse.Namespace) -> Path:
    output = native_path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    generated_path = output / "generated_config.json"
    previous = load_json(generated_path, None)
    config, report = build_configuration(args.database, args.snapshot_id, args.sample_size)
    report["changes_since_previous_generation"] = compare_schemas(previous, config)
    overrides_path = output / "curated_overrides.json"
    effective_path = output / "effective_config.json"
    report_path = output / "schema_report.json"
    write_json(generated_path, config)
    if not overrides_path.exists():
        write_json(overrides_path, {"profile": "unreviewed", "notes": "Human and AI-approved overrides only.", "fields": {}})
    overrides = load_json(overrides_path, {})
    write_json(effective_path, merge(config, overrides))
    write_json(report_path, report)
    (output / "adaptation_prompt.md").write_text(adaptation_prompt(config, report), encoding="utf-8")
    return output


def main(argv: list[str] | None = None) -> int:
    try:
        output = run(parse_args(argv))
        print(f"Generated semantic configuration in {output}")
        return 0
    except (GeneratorError, OSError, sqlite3.Error, json.JSONDecodeError) as exc:
        print(f"Configuration generation failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
