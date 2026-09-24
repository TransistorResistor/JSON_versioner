"""Content-addressed JSON storage and bounded-memory snapshot staging."""
from __future__ import annotations

import json
import sqlite3
import tempfile
import zlib
from collections.abc import Mapping
from contextlib import closing
from functools import lru_cache
from pathlib import Path


def unpack(payload):
    return zlib.decompress(payload).decode('utf-8')


def put_blob(db, hash_, text, cfg):
    if db.execute('SELECT 1 FROM record_blobs WHERE hash=?', (hash_,)).fetchone():
        return
    value = json.loads(text)
    fields = sorted(set(cfg.get('group_fields', ['systemGroup']) + cfg.get('type_fields', ['systemType'])
                        + cfg.get('owner_fields', ['modelOwner']) + cfg.get('name_fields', ['nomenclature', 'name'])))
    classification = {field: value.get(field) if isinstance(value, dict) else None for field in fields}
    raw = text.encode('utf-8')
    db.execute('INSERT INTO record_blobs VALUES(?,?,?,?)',
               (hash_, zlib.compress(raw), len(raw), json.dumps(classification)))


def record_text(db, hash_, legacy=None):
    if legacy is not None:
        return legacy
    row = db.execute('SELECT payload FROM record_blobs WHERE hash=?', (hash_,)).fetchone()
    if row is None:
        raise ValueError(f'Missing record payload: {hash_}')
    return unpack(row[0])


def dashboard_records(db, fields):
    """Expose small classification JSON, with a legacy/config-change fallback."""
    has_blobs = db.execute("SELECT 1 FROM sqlite_master WHERE name='record_blobs'").fetchone()
    if not has_blobs:
        db.execute('CREATE TEMP VIEW display_records AS SELECT * FROM records')
        return

    @lru_cache(maxsize=20000)
    def classification(hash_, metadata):
        values = json.loads(metadata) if metadata else {}
        if not set(fields).issubset(values):
            full = json.loads(record_text(db, hash_))
            values = {field: full.get(field) if isinstance(full, dict) else None for field in fields}
        return json.dumps({field: values.get(field) for field in fields})

    db.create_function('classification', 2, classification)
    db.execute('''CREATE TEMP VIEW display_records AS
        SELECT r.snapshot_id,r.record_id,r.filename,r.hash,
            CASE WHEN r.json_text IS NOT NULL THEN r.json_text
                 ELSE classification(r.hash,b.classification_json) END AS json_text
        FROM records r LEFT JOIN record_blobs b ON b.hash=r.hash''')


class StagedRecords(Mapping):
    """Spool incoming payloads to disk; keep only IDs and hashes in RAM."""

    def __init__(self):
        self.directory = tempfile.TemporaryDirectory(prefix='json-versioner-')
        self.db = sqlite3.connect(Path(self.directory.name) / 'staging.sqlite3')
        self.db.execute('CREATE TABLE staged (id TEXT PRIMARY KEY, filename TEXT, payload BLOB, hash TEXT, updated TEXT)')
        self.hashes = {}
        self.filenames = {}

    def __setitem__(self, key, value):
        filename, data, hash_, updated = value
        payload = zlib.compress(json.dumps(data, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
        self.db.execute('INSERT INTO staged VALUES(?,?,?,?,?)', (key, filename, payload, hash_, updated))
        self.hashes[key] = hash_
        self.filenames[key] = filename

    def __getitem__(self, key):
        row = self.db.execute('SELECT filename,payload,hash,updated FROM staged WHERE id=?', (key,)).fetchone()
        if row is None:
            raise KeyError(key)
        return row[0], json.loads(unpack(row[1])), row[2], row[3]

    def __iter__(self):
        return iter(self.hashes)

    def __len__(self):
        return len(self.hashes)

    def __contains__(self, key):
        return key in self.hashes

    def close(self):
        self.db.close()
        self.directory.cleanup()


class PreviousRecords(Mapping):
    def __init__(self, db, snapshot_id):
        self.db = db
        self.snapshot_id = snapshot_id
        self.metadata = {rid: (filename, hash_) for rid, filename, hash_ in db.execute(
            'SELECT record_id,filename,hash FROM records WHERE snapshot_id=?', (snapshot_id,))}

    def __getitem__(self, key):
        filename, hash_ = self.metadata[key]
        legacy = self.db.execute('SELECT json_text FROM records WHERE snapshot_id=? AND record_id=?',
                                 (self.snapshot_id, key)).fetchone()[0]
        return filename, json.loads(record_text(self.db, hash_, legacy)), hash_

    def __iter__(self):
        return iter(self.metadata)

    def __len__(self):
        return len(self.metadata)

    def __contains__(self, key):
        return key in self.metadata


def compact_storage(db, path):
    """Back up first, convert legacy payloads atomically, then reclaim pages."""
    backup = Path(str(path) + '.pre-compression.bak')
    if backup.exists():
        raise ValueError(f'Backup already exists: {backup}; move it before running compaction again')
    with closing(sqlite3.connect(backup)) as target:
        db.backup(target)
    try:
        with db:
            for sid, cfg_text in db.execute('SELECT id,config_json FROM snapshots'):
                cfg = json.loads(cfg_text)
                for hash_, value in db.execute('SELECT hash,json_text FROM records WHERE snapshot_id=? AND json_text IS NOT NULL', (sid,)):
                    put_blob(db, hash_, value, cfg)
                db.execute('UPDATE records SET json_text=NULL WHERE snapshot_id=?', (sid,))
        db.execute('VACUUM')
    except Exception:
        print(f'Original database backup: {backup}')
        raise
    return backup
