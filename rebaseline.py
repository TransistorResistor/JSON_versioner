"""Explicit, destructive addition of ignore rules to an existing history."""
from __future__ import annotations

import collections
import hashlib
import json
import re
import sqlite3
import zlib
from contextlib import closing
from pathlib import Path

from comparison import (COMPARISON_KEYS, MISSING, canonical, changes, count_fields,
                        digest, leaves, normalize, path_matches, word_edits)
from configuration import configuration, load_config
from storage import PreviousRecords, record_text, unpack


def validate_rules(snapshots, cfg):
    """Only additions are supported; identities and other settings stay stable."""
    old = configuration(json.loads(snapshots[-1]['config_json']))
    additions = {}
    for key in ('ignore_fields', 'ignore_paths'):
        if not set(old[key]).issubset(cfg[key]):
            raise ValueError(f'Rebaseline cannot remove existing {key} rules')
        additions[key] = sorted(set(cfg[key]) - set(old[key]))
    for key in old.keys() | cfg.keys():
        if key not in additions and old.get(key) != cfg.get(key):
            raise ValueError(f'Rebaseline only supports adding ignore rules; {key} changed')
    for snapshot in snapshots:
        previous = configuration(json.loads(snapshot['config_json']))
        if any(previous[key] != old[key] for key in COMPARISON_KEYS):
            raise ValueError('History contains different comparison settings; cannot scrub safely')
    protected = ['$.' + field for field in cfg['id_fields']]
    for path, keys in cfg['array_keys'].items():
        keys = [keys] if isinstance(keys, str) else keys
        protected.extend(path + '[].' + key for key in keys)
    for path in protected:
        parts = re.sub(r'\[[^]]*\]', '', path).removeprefix('$.').split('.')
        if any(field in parts for field in additions['ignore_fields']):
            raise ValueError(f'New ignore rule would remove identity field or its parent: {path}')
        prefixes = [path[:i] for i, char in enumerate(path) if char == '.'] + [path]
        # Ignoring the array itself also removes its item identity.
        prefixes += [path[:i] for i, char in enumerate(path) if char == '[']
        if any(path_matches(prefix, additions['ignore_paths']) for prefix in prefixes):
            raise ValueError(f'New ignore path would remove identity field or its parent: {path}')
    return additions


def dropped_paths(value, cfg, path='$'):
    """Report removed JSON members, including an entire ignored subtree."""
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f'{path}.{key}'
            if key in cfg['ignore_fields'] or path_matches(child_path, cfg['ignore_paths']):
                yield child_path
            else:
                yield from dropped_paths(child, cfg, child_path)
    elif isinstance(value, list):
        for child in value:
            yield from dropped_paths(child, cfg, path + '[]')


def metadata_ignored(cfg, fields):
    return any(field in cfg['ignore_fields'] or path_matches('$.' + field, cfg['ignore_paths'])
               for field in fields)


def ignored_change_path(path, cfg):
    normalized = re.sub(r'\[[^]]*\]', '[]', path)
    parts = normalized.removeprefix('$.').split('.')
    if any(part.removesuffix('[]') in cfg['ignore_fields'] for part in parts):
        return True
    prefixes = [path[:i] for i, char in enumerate(path) if char == '.'] + [path]
    prefixes += [normalized[:i] for i, char in enumerate(normalized) if char == '[']
    return any(path_matches(prefix, cfg['ignore_paths']) for prefix in prefixes)


def preview(db, cfg):
    required = {'event_at', 'event_basis', 'reported_updated_at', 'comparison_snapshot_id'}
    if not required.issubset({r[1] for r in db.execute('PRAGMA table_info(record_changes)')}):
        raise ValueError('Legacy history schema: run a capture with the original config before rebaselining')
    snapshots = [dict(row) for row in db.execute('SELECT * FROM snapshots ORDER BY id')]
    if not snapshots:
        raise ValueError('History has no snapshots to rebaseline')
    additions = validate_rules(snapshots, cfg)
    fingerprint = hashlib.sha256(canonical(snapshots).encode('utf-8'))
    counts = collections.Counter()
    affected_hashes = set()
    for row in db.execute('SELECT hash,payload FROM record_blobs'):
        fingerprint.update(row['hash'].encode('utf-8'))
        fingerprint.update(row['payload'])
        paths = list(dropped_paths(json.loads(unpack(row['payload'])), cfg))
        if paths:
            affected_hashes.add(row['hash'])
            counts.update(paths)
    affected_snapshots, affected_records = set(), set()
    memberships = 0
    for row in db.execute('SELECT snapshot_id,record_id,hash,json_text FROM records'):
        fingerprint.update(canonical(list(row)).encode('utf-8'))
        legacy_paths = list(dropped_paths(json.loads(row['json_text']), cfg)) if row['json_text'] is not None else []
        if row['json_text'] is not None:
            counts.update(legacy_paths)
        if legacy_paths or (row['json_text'] is None and row['hash'] in affected_hashes):
            memberships += 1
            affected_snapshots.add(row['snapshot_id'])
            affected_records.add(row['record_id'])
    change_paths = collections.Counter()
    for row in db.execute('SELECT json_path FROM field_changes'):
        if ignored_change_path(row['json_path'], cfg):
            change_paths[re.sub(r'\[[^]]*\]', '[]', row['json_path'])] += 1
    return {'additions': additions, 'paths': dict(sorted(counts.items())),
            'change_paths': dict(sorted(change_paths.items())),
            'snapshots': len(affected_snapshots), 'records': len(affected_records),
            'memberships': memberships, 'history_snapshots': len(snapshots),
            'latest_id': snapshots[-1]['id'],
            'fingerprint': fingerprint.hexdigest(),
            'drop_owner_metadata': metadata_ignored(cfg, cfg['owner_fields']),
            'drop_timestamp_metadata': metadata_ignored(
                additions, cfg['updated_at_fields'])}


def print_warning(path, report):
    print(f'WARNING: IRREVERSIBLE DATA LOSS in {path}')
    print('This scrubs ALL earlier snapshots and resets the latest stored snapshot as the capture baseline.')
    for key, rules in report['additions'].items():
        print(f'Added {key}: {canonical(rules)}')
    print(f"Affected: {report['snapshots']} snapshots, {report['records']} records, "
          f"{report['memberships']} stored record versions.")
    print('JSON members/subtrees to drop (occurrences in distinct payloads and legacy records):')
    for path, count in report['paths'].items():
        print(f'  {path}: {count}')
    if not report['paths']:
        print('  No matching JSON members currently stored; the new rules still affect future captures.')
    print('Field-change entries whose before/after values will be dropped:')
    for path, count in report['change_paths'].items():
        print(f'  {path}: {count}')
    if not report['change_paths']:
        print('  None currently stored.')
    if report['drop_owner_metadata']:
        print('  Capture-gap owner metadata will also be cleared.')
    if report['drop_timestamp_metadata']:
        print('  Stored reported timestamps will be cleared; affected event times use snapshot observation time.')
    print('Removed values and their field-change before/after history will be permanently dropped.')
    print('Hashes, classification metadata, historical counts and change scores will be rebuilt.')
    print('Edits containing only ignored fields will become unchanged. Snapshot dates and record IDs remain.')
    print('There is no undo or automatic backup. Make a separate backup first if you may need these values.')
    print('Original source files and external backups are not scrubbed. This is not a forensic erasure guarantee.')


def scrub_payloads(db, cfg):
    """Stage only sanitized payloads; replace all legacy and redundant storage."""
    db.execute('CREATE TEMP TABLE scrub_blobs AS SELECT * FROM record_blobs WHERE 0')
    db.execute('CREATE UNIQUE INDEX scrub_blob_hash ON scrub_blobs(hash)')
    db.execute('CREATE TEMP TABLE scrub_records AS SELECT * FROM records WHERE 0')
    mapping = {}
    fields = set()
    for row in db.execute('SELECT config_json FROM snapshots'):
        old = configuration(json.loads(row['config_json']))
        for key in ('group_fields', 'type_fields', 'owner_fields', 'name_fields'):
            fields.update(old[key])

    def stage(value):
        value = normalize(value, cfg)
        text = canonical(value)
        hash_ = digest(value)
        raw = text.encode('utf-8')
        metadata = {field: value.get(field) if isinstance(value, dict) else None for field in fields}
        db.execute('INSERT OR IGNORE INTO scrub_blobs VALUES(?,?,?,?)',
                   (hash_, zlib.compress(raw), len(raw), canonical(metadata)))
        return hash_

    for row in db.execute('SELECT hash,payload FROM record_blobs'):
        mapping[row['hash']] = stage(json.loads(unpack(row['payload'])))
    for row in db.execute('SELECT * FROM records'):
        hash_ = stage(json.loads(row['json_text'])) if row['json_text'] is not None else mapping[row['hash']]
        db.execute('INSERT INTO scrub_records VALUES(?,?,?,?,NULL)',
                   (row['snapshot_id'], row['record_id'], row['filename'], hash_))
    db.execute('DELETE FROM records')
    db.execute('INSERT INTO records SELECT * FROM scrub_records')
    db.execute('DELETE FROM record_blobs')
    db.execute('INSERT INTO record_blobs SELECT * FROM scrub_blobs WHERE hash IN (SELECT hash FROM records)')
    db.execute('DROP TABLE scrub_records')
    db.execute('DROP TABLE scrub_blobs')


def rebuild_changes(db, cfg, drop_timestamps):
    # Import here so the capture CLI can load this module without a cycle.
    from version_json import change_time, parse_update, schema_paths, weight
    snapshots = [dict(row) for row in db.execute('SELECT * FROM snapshots ORDER BY id')]
    db.execute('CREATE TEMP TABLE old_changes AS SELECT * FROM record_changes')
    db.execute('CREATE UNIQUE INDEX old_change_key ON old_changes(snapshot_id,record_id)')
    db.execute('DELETE FROM record_changes')
    db.execute('DELETE FROM field_changes')
    for snapshot in snapshots:
        sid, previous_id = snapshot['id'], snapshot['previous_id']
        old_cfg = configuration(json.loads(snapshot['config_json']))
        old_cfg.update({key: cfg[key] for key in ('ignore_fields', 'ignore_paths')})
        current, previous = PreviousRecords(db, sid), PreviousRecords(db, previous_id)
        common = current.keys() & previous.keys()
        schema = schema_paths((changes(previous[rid][1], current[rid][1], old_cfg)
                               for rid in common if previous.metadata[rid][1] != current.metadata[rid][1]),
                              len(common), old_cfg)
        gaps = {r[0] for r in db.execute('SELECT record_id FROM snapshot_gaps WHERE snapshot_id=?', (sid,))}
        previous_gaps = {r[0] for r in db.execute('SELECT record_id FROM snapshot_gaps WHERE snapshot_id=?', (previous_id,))}
        counts = collections.Counter()
        now = parse_update(snapshot['created_at'])
        previous_time_row = db.execute('SELECT created_at FROM snapshots WHERE id=?', (previous_id,)).fetchone()
        previous_time = parse_update(previous_time_row[0]) if previous_time_row else None
        for rid in sorted(current.keys() | previous.keys()):
            if rid in gaps:
                continue
            old, new = previous.get(rid), current.get(rid)
            comparison_id, comparison_time = previous_id, previous_time
            if old is None and new is not None and rid in previous_gaps:
                last = db.execute('''SELECT r.snapshot_id,s.created_at FROM records r
                    JOIN snapshots s ON s.id=r.snapshot_id WHERE r.record_id=? AND r.snapshot_id<?
                    ORDER BY r.snapshot_id DESC LIMIT 1''', (rid, sid)).fetchone()
                if last:
                    stored = db.execute('SELECT filename,hash,json_text FROM records WHERE snapshot_id=? AND record_id=?',
                                        (last[0], rid)).fetchone()
                    old = (stored['filename'], json.loads(record_text(db, stored['hash'], stored['json_text'])), stored['hash'])
                    comparison_id, comparison_time = last[0], parse_update(last[1])
            diff = list(changes(old[1], new[1], old_cfg)) if old and new and old[2] != new[2] else []
            kind = 'added' if old is None else 'removed' if new is None else (
                'modified' if any(path not in schema for path, _, _ in diff) else 'unchanged')
            counts[kind] += 1
            if diff and kind == 'unchanged':
                counts['schema_only'] += 1
            if kind == 'unchanged' and not diff and old[0] == new[0]:
                continue
            meaningful = [(p, a, b) for p, a, b in diff if p not in schema]
            total = max(count_fields(old[1]) if old else 0, count_fields(new[1]) if new else 0, 1)
            weighted_total = max(sum(weight(p, old_cfg) for p, _ in leaves(old[1])) if old else 0,
                                 sum(weight(p, old_cfg) for p, _ in leaves(new[1])) if new else 0, 1)
            original = db.execute('SELECT * FROM old_changes WHERE snapshot_id=? AND record_id=?', (sid, rid)).fetchone()
            reported = original['reported_updated_at'] if original and kind == 'modified' and not drop_timestamps else None
            event_at, basis = change_time(kind, reported, comparison_time, now)
            if original and kind == 'modified' and not drop_timestamps:
                event_at, basis = original['event_at'], original['event_basis']
            db.execute('''INSERT INTO record_changes
                (snapshot_id,record_id,filename,previous_filename,change_type,fields_changed,fields_total,
                 change_ratio,weighted_ratio,event_at,event_basis,reported_updated_at,comparison_snapshot_id)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (sid, rid, new[0] if new else old[0], old[0] if old else None, kind, len(meaningful), total,
                 min(len(meaningful)/total, 1), min(sum(weight(p, old_cfg) for p, _, _ in meaningful)/weighted_total, 1),
                 event_at, basis, reported, comparison_id if old else None))
            for path, a, b in diff:
                words = (word_edits('' if a is MISSING else a, '' if b is MISSING else b)
                         if (a is MISSING or isinstance(a, str)) and (b is MISSING or isinstance(b, str))
                         and re.search(r'description', path, re.I) else None)
                db.execute('INSERT INTO field_changes VALUES(?,?,?,?,?,?,?)',
                           (sid, rid, path, None if a is MISSING else canonical(a),
                            None if b is MISSING else canonical(b), int(path in schema), words))
        # The latest config must match the provided file for the next capture.
        if sid == snapshots[-1]['id']:
            old_cfg = cfg
        db.execute('''UPDATE snapshots SET config_json=?,total=?,added=?,removed=?,modified=?,
            unchanged=?,schema_only=? WHERE id=?''', (canonical(old_cfg), len(current), counts['added'],
            counts['removed'], counts['modified'], counts['unchanged'], counts['schema_only'], sid))
    db.execute('DROP TABLE old_changes')


def rebaseline(history, config_path, *, dry_run=False, confirm=None):
    """Preview first; confirmation is mandatory and the rewrite is transactional."""
    path = Path(history).resolve() / 'history.sqlite3'
    if not path.is_file():
        raise ValueError(f'No database at {path}')
    cfg = load_config(config_path)
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute('BEGIN')
        report = preview(db, cfg)
    if not any(report['additions'].values()):
        print('No new ignore rules; history is unchanged.')
        return False
    print_warning(path, report)
    if dry_run:
        print('Dry run: no data was changed.')
        return False
    confirm = input if confirm is None else confirm
    try:
        answer = confirm('Type SCRUB HISTORY to permanently drop this data, or anything else to cancel: ')
    except (EOFError, KeyboardInterrupt):
        answer = ''
    if answer != 'SCRUB HISTORY':
        print('Cancelled; history is unchanged.')
        return False
    with closing(sqlite3.connect(path)) as db:
        db.row_factory = sqlite3.Row
        # Avoid retaining deleted values in WAL or freed database pages.
        db.execute('PRAGMA secure_delete=ON')
        mode = db.execute('PRAGMA journal_mode').fetchone()[0]
        if mode == 'wal':
            busy = db.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0]
            if busy:
                raise ValueError('History is in use; close readers/writers and retry rebaseline')
            if db.execute('PRAGMA journal_mode=DELETE').fetchone()[0] != 'delete':
                raise ValueError('Cannot leave WAL mode safely; close other database users and retry')
        db.execute('BEGIN EXCLUSIVE')
        try:
            if preview(db, cfg) != report:
                raise ValueError('History changed since the preview; run rebaseline again to review the new data')
            scrub_payloads(db, cfg)
            if report['drop_owner_metadata']:
                db.execute('UPDATE snapshot_gaps SET model_owner=NULL')
            rebuild_changes(db, cfg, report['drop_timestamp_metadata'])
            db.commit()
        except BaseException:
            db.rollback()
            raise
        try:
            db.execute('VACUUM')
        except sqlite3.Error as exc:
            raise ValueError('Scrub committed, but space reclamation failed; run VACUUM when the history is idle') from exc
    print(f"Rebaselined {report['history_snapshots']} snapshots; latest baseline is #{report['latest_id']}. "
          'New captures can use the supplied config. Removed values cannot be recovered from this history.')
    return True
