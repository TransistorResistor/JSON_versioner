import json
import sqlite3
import tempfile
import unittest
import zlib
from pathlib import Path

from semantic_config_generator import generate_config as generator


class GeneratorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db_path = self.root / "history.sqlite3"
        db = sqlite3.connect(self.db_path)
        db.executescript("""
            CREATE TABLE snapshots (id INTEGER PRIMARY KEY, created_at TEXT);
            CREATE TABLE records (
                snapshot_id INTEGER, record_id TEXT, filename TEXT,
                hash TEXT, json_text TEXT, PRIMARY KEY(snapshot_id,record_id));
            CREATE TABLE record_blobs (
                hash TEXT PRIMARY KEY, payload BLOB, raw_bytes INTEGER, classification_json TEXT);
            CREATE TABLE field_changes (
                snapshot_id INTEGER, record_id TEXT, json_path TEXT,
                old_value TEXT, new_value TEXT, schema_only INTEGER, word_edits INTEGER);
        """)
        db.execute("INSERT INTO snapshots VALUES(1,'2026-01-01T00:00:00Z')")
        records = [
            {"modelID": "A", "modelOwner": "Team North", "systemType": "Radar", "rangeKm": 120,
             "parametrics": [{"parameter": "Range", "value": 120, "unit": "km"}]},
            {"modelID": "B", "modelOwner": "Team South", "systemType": "Radar", "rangeKm": 180,
             "parametrics": [{"parameter": "Range", "value": 180, "unit": "km"}]},
        ]
        for item in records:
            text = json.dumps(item, separators=(",", ":"))
            hash_ = item["modelID"]
            db.execute("INSERT INTO record_blobs VALUES(?,?,?,?)", (hash_, zlib.compress(text.encode()), len(text), "{}"))
            db.execute("INSERT INTO records VALUES(?,?,?,?,NULL)", (1, hash_, hash_ + ".json", hash_))
        db.execute("INSERT INTO field_changes VALUES(1,'A','$.rangeKm','100','120',0,0)")
        db.commit()
        db.close()

    def tearDown(self):
        self.temp.cleanup()

    def test_generates_catalogue_and_array_key(self):
        config, report = generator.build_configuration(self.db_path, None, 100)
        self.assertEqual(config["source"]["records_sampled"], 2)
        self.assertEqual(config["fields"]["$.modelOwner"]["semantic_type"], "owner")
        self.assertEqual(config["fields"]["$.rangeKm"]["historical_changes"], 1)
        self.assertEqual(config["array_keys"]["$.parametrics"], "parameter")
        self.assertEqual(report["schema_fingerprint"], config["schema_fingerprint"])

    def test_run_preserves_curated_overrides(self):
        output = self.root / "output"
        output.mkdir()
        (output / "curated_overrides.json").write_text(
            json.dumps({"fields": {"$.rangeKm": {"label": "Operating range", "importance": "review"}}}),
            encoding="utf-8",
        )
        args = generator.parse_args([str(self.db_path), "--output", str(output)])
        generator.run(args)
        effective = json.loads((output / "effective_config.json").read_text(encoding="utf-8"))
        self.assertEqual(effective["fields"]["$.rangeKm"]["label"], "Operating range")
        self.assertTrue((output / "adaptation_prompt.md").exists())

    def test_schema_comparison_reports_add_remove_and_type_change(self):
        previous = {"schema_fingerprint": "old", "fields": {
            "$.same": {"observed_types": ["text"]},
            "$.removed": {"observed_types": ["text"]},
        }}
        current = {"schema_fingerprint": "new", "fields": {
            "$.same": {"observed_types": ["number"]},
            "$.added": {"observed_types": ["boolean"]},
        }}
        changes = generator.compare_schemas(previous, current)
        self.assertEqual(changes["added_paths"], ["$.added"])
        self.assertEqual(changes["removed_paths"], ["$.removed"])
        self.assertEqual(changes["type_changes"][0]["path"], "$.same")

    def test_missing_database_is_not_created(self):
        missing = self.root / "missing.sqlite3"
        with self.assertRaises(generator.GeneratorError):
            generator.open_readonly(missing)
        self.assertFalse(missing.exists())

    def test_unc_text_is_not_converted_to_uri(self):
        unc = r"\\server\share\history\history.sqlite3"
        self.assertEqual(str(generator.native_path(unc)), unc)
        self.assertFalse(str(generator.native_path(unc)).startswith("file:"))


if __name__ == "__main__":
    unittest.main()
