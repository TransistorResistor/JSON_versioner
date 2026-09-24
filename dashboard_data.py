"""Read-only dashboard queries and independently testable category analytics."""
from __future__ import annotations

import difflib
import html
import json
import re
import sqlite3
from collections import Counter, defaultdict
from contextlib import contextmanager
from enum import Enum
from pathlib import Path

import pandas as pd

from storage import dashboard_records, record_text

UNKNOWN = '(Unspecified)'
DIMENSIONS = {'System type': 'system_type', 'System group': 'system_group', 'Model owner': 'model_owner'}
MEANINGFUL = ('added', 'removed', 'modified')


class MissingRecord(Enum):
    ABSENT = 'absent'


ABSENT = MissingRecord.ABSENT


def value_expr(column, fields):
    parts = []
    for field in fields:
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', field):
            raise ValueError(f'Classification field must be a simple top-level name: {field!r}')
        parts.append(f"NULLIF(TRIM(CAST(json_extract({column}, '$.{field}') AS TEXT)), '')")
    return f"COALESCE({', '.join(parts + [repr(UNKNOWN)])})" if parts else repr(UNKNOWN)


@contextmanager
def connect(path, cfg=None):
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    try:
        # A consistent read across the several SELECTs used to build one view.
        db.execute('BEGIN')
        if cfg is not None:
            field_sets = {
                'system_group': cfg.get('group_fields', ['systemGroup']),
                'system_type': cfg.get('type_fields', ['systemType']),
                'model_owner': cfg.get('owner_fields', ['modelOwner']),
                'record_name': cfg.get('name_fields', ['nomenclature', 'name']),
            }
            dashboard_records(db, sorted({f for fields in field_sets.values() for f in fields}))
            expressions = ','.join(f'{value_expr("r.json_text", fields)} AS {name}' for name, fields in field_sets.items())
            db.execute(f'''CREATE TEMP VIEW dimensions AS SELECT r.snapshot_id,r.record_id,r.filename,r.hash,
                {expressions} FROM display_records r''')
            columns = {r[1] for r in db.execute('PRAGMA table_info(record_changes)')}
            event_at = 'COALESCE(rc.event_at,s.created_at)' if 'event_at' in columns else 's.created_at'
            basis = "COALESCE(rc.event_basis,'snapshot observation (legacy)')" if 'event_basis' in columns else "'snapshot observation (legacy)'"
            fields = ','.join(f"COALESCE(r.{name},old.{name},{repr(UNKNOWN)}) AS {name},old.{name} AS previous_{name}" for name in field_sets)
            db.execute(f'''CREATE TEMP VIEW events AS SELECT rc.*,s.created_at,s.previous_id,
                {event_at} AS estimated_at,{basis} AS timing_basis,
                {fields},
                CASE WHEN rc.change_type!='unchanged' THEN rc.change_type
                     WHEN EXISTS(SELECT 1 FROM field_changes f WHERE f.snapshot_id=rc.snapshot_id
                         AND f.record_id=rc.record_id AND f.schema_only=1) THEN 'schema only'
                     ELSE 'source moved' END AS kind
                FROM record_changes rc JOIN snapshots s ON s.id=rc.snapshot_id
                LEFT JOIN dimensions r ON r.snapshot_id=rc.snapshot_id AND r.record_id=rc.record_id
                LEFT JOIN dimensions old ON old.snapshot_id=s.previous_id AND old.record_id=rc.record_id''')
        yield db
    finally:
        db.close()


def snapshots(path):
    with connect(path) as db:
        return [dict(r) for r in db.execute('SELECT * FROM snapshots ORDER BY id')]


def period_data(path, cfg, start, end):
    with connect(path, cfg) as db:
        totals = [dict(r) for r in db.execute('''SELECT snapshot_id,system_group,system_type,model_owner,COUNT(*) AS population
            FROM dimensions WHERE snapshot_id BETWEEN ? AND ?
            GROUP BY snapshot_id,system_group,system_type,model_owner''', (start, end))]
        events = [dict(r) for r in db.execute('SELECT * FROM events WHERE snapshot_id BETWEEN ? AND ? ORDER BY snapshot_id,record_id', (start, end))]
    return totals, events


def scoped(rows, filters):
    allowed = {key: set(values) for key, values in filters.items()}
    return [r for r in rows if all(r[key] in values for key, values in allowed.items())]


def meaningful(events, include_baseline=False):
    return [e for e in events if e['kind'] in MEANINGFUL and (include_baseline or e['previous_id'] is not None)]


def category_series(totals, events, period, dimension, include_baseline=False):
    if dimension not in DIMENSIONS.values():
        raise ValueError('Unknown dimension')
    changes = meaningful(events, include_baseline)
    categories = sorted({r[dimension] for r in totals + events})
    populations = Counter()
    counts = defaultdict(Counter)
    for row in totals:
        populations[row['snapshot_id'], row[dimension]] += row['population']
    for row in changes:
        counts[row['snapshot_id'], row[dimension]][row['kind']] += 1
    rows = []
    for snapshot in period:
        for category in categories:
            key = snapshot['id'], category
            count = counts[key]
            population = populations[key]
            denominator = population + count['removed']
            events_count = sum(count.values())
            excluded = snapshot['previous_id'] is None and not include_baseline
            rows.append({'snapshot_id': snapshot['id'], 'observed_at': snapshot['created_at'], 'category': category,
                'population': population, 'added': count['added'], 'removed': count['removed'],
                'modified': count['modified'], 'events': events_count,
                'affected_percent': 100 * events_count / denominator if denominator and not excluded else None,
                'baseline_excluded': excluded})
    return rows


def category_summary(series, events, dimension, include_baseline=False):
    changes = meaningful(events, include_baseline)
    unique = defaultdict(set)
    for row in changes:
        unique[row[dimension]].add(row['record_id'])
    by_category = defaultdict(list)
    for row in series:
        by_category[row['category']].append(row)
    result = []
    for category, values in by_category.items():
        rates = [r['affected_percent'] for r in values if r['affected_percent'] is not None]
        result.append({'category': category, 'population_start': values[0]['population'],
            'population_end': values[-1]['population'], 'population_delta': values[-1]['population']-values[0]['population'],
            'unique_records': len(unique[category]), 'events': sum(r['events'] for r in values),
            **{kind: sum(r[kind] for r in values) for kind in MEANINGFUL},
            'peak_affected_percent': max(rates) if rates else None})
    return sorted(result, key=lambda r: (-r['events'], r['category']))


def timing(events, clock='Observed', bucket='Day', include_baseline=False):
    counts = Counter()
    for event in meaningful(events, include_baseline):
        is_reported = event.get('timing_basis') == 'record timestamp'
        stamp = event['created_at'] if clock == 'Observed' else event.get('estimated_at') or event['created_at']
        parsed = pd.to_datetime(stamp, utc=True, errors='coerce')
        if pd.isna(parsed):
            parsed = pd.to_datetime(event['created_at'], utc=True)
            is_reported = False
        day = parsed.floor('D')
        if bucket == 'Week':
            day -= pd.Timedelta(days=day.weekday())
        elif bucket == 'Month':
            day = day.replace(day=1)
        basis = 'Observed' if clock == 'Observed' else 'Record timestamp' if is_reported else 'Observation fallback'
        counts[(day.isoformat(), basis)] += 1
    return [{'period': period, 'basis': basis, 'events': count} for (period, basis), count in sorted(counts.items())]


def field_scope(path, cfg, event_keys):
    """Aggregate only selected record events; no field values leave SQLite."""
    if not event_keys:
        return []
    stats = defaultdict(lambda: {'records': set(), 'snapshots': set(), 'events': set(), 'edits': 0, 'words': 0})
    with connect(path) as db:
        db.execute('CREATE TEMP TABLE selected_events(snapshot_id INTEGER,record_id TEXT,PRIMARY KEY(snapshot_id,record_id))')
        db.executemany('INSERT OR IGNORE INTO selected_events VALUES(?,?)', event_keys)
        for r in db.execute('''SELECT f.snapshot_id,f.record_id,f.json_path,f.word_edits FROM field_changes f
            JOIN selected_events e USING(snapshot_id,record_id) WHERE f.schema_only=0'''):
            field = re.sub(r'\[[^]]*\]', '[]', r['json_path'])
            item = stats[field]
            item['records'].add(r['record_id'])
            item['snapshots'].add(r['snapshot_id'])
            item['events'].add((r['snapshot_id'], r['record_id']))
            item['edits'] += 1
            item['words'] += r['word_edits'] or 0
    return sorted([{'field': field, 'section': field.removeprefix('$.').split('.')[0].split('[')[0],
                    'unique_records': len(s['records']), 'record_events': len(s['events']),
                    'field_edits': s['edits'], 'snapshots': len(s['snapshots']), 'word_edits': s['words']}
                   for field, s in stats.items()], key=lambda r: (-r['unique_records'], r['field']))


def record_page(path, cfg, snapshot_id, filters, search='', page=0, size=50):
    clauses = ['snapshot_id=?']
    parameters = [snapshot_id]
    for column in DIMENSIONS.values():
        values = filters.get(column, [])
        if not values:
            return 0, []
        clauses.append(f'{column} IN ({",".join("?" for _ in values)})')
        parameters.extend(values)
    if search:
        clauses.append('(instr(lower(record_id),lower(?))>0 OR instr(lower(record_name),lower(?))>0 OR instr(lower(filename),lower(?))>0)')
        parameters.extend([search]*3)
    where = ' AND '.join(clauses)
    with connect(path, cfg) as db:
        count = db.execute('SELECT COUNT(*) FROM dimensions WHERE '+where, parameters).fetchone()[0]
        rows = [dict(r) for r in db.execute('SELECT * FROM dimensions WHERE '+where+' ORDER BY record_id LIMIT ? OFFSET ?', parameters+[size, page*size])]
    return count, rows


def record_version(path, snapshot_id, record_id):
    if snapshot_id is None:
        return ABSENT
    with connect(path) as db:
        row = db.execute('SELECT hash,json_text FROM records WHERE snapshot_id=? AND record_id=?', (snapshot_id, record_id)).fetchone()
        return json.loads(record_text(db, row['hash'], row['json_text'])) if row else ABSENT


def record_timeline(path, cfg, record_id):
    with connect(path, cfg) as db:
        return [dict(r) for r in db.execute('''SELECT s.id AS snapshot_id,s.created_at,s.note,
            COALESCE(e.kind,'unchanged') AS kind,COALESCE(d.system_group,e.system_group) AS system_group,
            COALESCE(d.system_type,e.system_type) AS system_type,COALESCE(d.model_owner,e.model_owner) AS model_owner,
            COALESCE(d.record_name,e.record_name) AS record_name
            FROM snapshots s LEFT JOIN dimensions d ON d.snapshot_id=s.id AND d.record_id=?
            LEFT JOIN events e ON e.snapshot_id=s.id AND e.record_id=?
            WHERE d.record_id IS NOT NULL OR e.record_id IS NOT NULL ORDER BY s.id DESC''', (record_id, record_id))]


def comparison(path, record_id, before_id, after_id):
    """Compare actual endpoint versions, preserving missing vs JSON null."""
    from version_json import MISSING, canonical, changes
    with connect(path) as db:
        settings = {}
        for sid in (before_id, after_id):
            row = db.execute('SELECT config_json FROM snapshots WHERE id=?', (sid,)).fetchone() if sid else None
            settings[sid] = json.loads(row[0]) if row else {}
        a_cfg, b_cfg = settings[before_id], settings[after_id]
        keys = ('id_fields', 'ignore_fields', 'ignore_paths', 'updated_at_fields', 'unordered_arrays', 'array_keys')
        compatible = not before_id or all(a_cfg.get(k) == b_cfg.get(k) for k in keys)
    before = record_version(path, before_id, record_id)
    after = record_version(path, after_id, record_id)
    result = []
    if before is ABSENT and after is ABSENT:
        return before, after, result, compatible
    for field, a, b in changes(MISSING if before is ABSENT else before, MISSING if after is ABSENT else after, b_cfg):
        result.append({'field': field, 'before': '(absent)' if a is MISSING else canonical(a),
                       'after': '(absent)' if b is MISSING else canonical(b),
                       'kind': 'added' if a is MISSING else 'removed' if b is MISSING else 'modified'})
    return before, after, result, compatible


def word_diff(before, after):
    """Escaped text-only markup; record content cannot inject HTML."""
    a, b = re.findall(r'\s+|\S+', before), re.findall(r'\s+|\S+', after)
    left, right = [], []
    for tag, i, j, k, l in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        x, y = html.escape(''.join(a[i:j])), html.escape(''.join(b[k:l]))
        left.append(f'<del>{x}</del>' if tag in ('replace', 'delete') else x)
        right.append(f'<ins>{y}</ins>' if tag in ('replace', 'insert') else y)
    return ''.join(left), ''.join(right)


def storage_stats(path):
    with connect(path) as db:
        page_size = db.execute('PRAGMA page_size').fetchone()[0]
        result = {'database_bytes': page_size*db.execute('PRAGMA page_count').fetchone()[0],
                  'free_bytes': page_size*db.execute('PRAGMA freelist_count').fetchone()[0]}
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='record_blobs'").fetchone():
            result.update(dict(db.execute('''SELECT COUNT(*) AS unique_payloads,COALESCE(SUM(raw_bytes),0) AS unique_raw_bytes,
                COALESCE(SUM(length(payload)),0) AS compressed_bytes FROM record_blobs''').fetchone()))
            result['referenced_raw_bytes'] = db.execute('''SELECT COALESCE(SUM(b.raw_bytes),0)
                FROM records r JOIN record_blobs b ON b.hash=r.hash WHERE r.json_text IS NULL''').fetchone()[0]
        result['legacy_records'] = db.execute('SELECT COUNT(*) FROM records WHERE json_text IS NOT NULL').fetchone()[0]
        return result
