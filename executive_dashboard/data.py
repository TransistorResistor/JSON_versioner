"""Read-only data preparation for the executive change dashboard."""
from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import dashboard_data as history  # noqa: E402


SIZE_ORDER = {'Large': 3, 'Medium': 2, 'Small': 1, 'Added': 4, 'Removed': 4}


def load_config(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding='utf-8'))


def load_history(path: str | Path, cfg: dict) -> tuple[list[dict], list[dict], list[dict]]:
    snapshots = history.snapshots(path)
    if not snapshots:
        return [], [], []
    totals, events = history.period_data(path, cfg, snapshots[0]['id'], snapshots[-1]['id'])
    return snapshots, totals, events


def size_label(event: dict, cfg: dict) -> str:
    if event['kind'] == 'added':
        return 'Added'
    if event['kind'] == 'removed':
        return 'Removed'
    ratio = float(event.get('weighted_ratio') or 0)
    thresholds = cfg.get('change_thresholds', {'small': 0.10, 'medium': 0.40})
    if ratio >= float(thresholds.get('medium', 0.40)):
        return 'Large'
    if ratio >= float(thresholds.get('small', 0.10)):
        return 'Medium'
    return 'Small'


def estimated_day(event: dict) -> pd.Timestamp:
    value = event.get('estimated_at') or event.get('created_at')
    parsed = pd.to_datetime(value, utc=True, errors='coerce')
    if pd.isna(parsed):
        parsed = pd.to_datetime(event['created_at'], utc=True)
    return parsed


def change_rows(events: list[dict], cfg: dict, include_baseline: bool = False) -> list[dict]:
    rows = []
    for event in history.meaningful(events, include_baseline):
        size = size_label(event, cfg)
        verb = {'added': 'added', 'removed': 'removed', 'modified': 'edited'}[event['kind']]
        fields = int(event.get('fields_changed') or 0)
        description = f'{fields:,} field{"s" if fields != 1 else ""} edited' if event['kind'] == 'modified' else f'Record {verb}'
        rows.append({**event, 'size': size, 'size_rank': SIZE_ORDER[size],
                     'estimated_day': estimated_day(event),
                     'title': f"{event.get('record_name') or event['record_id']} {verb}",
                     'description': description})
    return rows


def filter_changes(rows: list[dict], groups: list[str], types: list[str], owners: list[str],
                   start, end) -> list[dict]:
    start = pd.Timestamp(start, tz='UTC') if start else None
    end = pd.Timestamp(end, tz='UTC') + pd.Timedelta(days=1) if end else None
    return [row for row in rows
            if row['system_group'] in groups and row['system_type'] in types and row['model_owner'] in owners
            and (start is None or row['estimated_day'] >= start)
            and (end is None or row['estimated_day'] < end)]


def sort_changes(rows: list[dict], order: str) -> list[dict]:
    if order == 'Most recent first':
        return sorted(rows, key=lambda row: (row['estimated_day'], row['size_rank']), reverse=True)
    if order == 'Smallest first':
        return sorted(rows, key=lambda row: (row['size_rank'], row.get('weighted_ratio') or 0, row['estimated_day']))
    return sorted(rows, key=lambda row: (row['size_rank'], row.get('weighted_ratio') or 0,
                                          row.get('fields_changed') or 0, row['estimated_day']), reverse=True)


def change_fields(path: str | Path, snapshot_id: int, record_id: str, schema_only: int = 0) -> list[dict]:
    with history.connect(path) as db:
        return [dict(row) for row in db.execute('''SELECT json_path,old_value,new_value,word_edits
            FROM field_changes WHERE snapshot_id=? AND record_id=? AND schema_only=? ORDER BY json_path''',
            (snapshot_id, record_id, schema_only))]


def record_versions(path: str | Path, event: dict):
    before = history.record_version(path, event.get('previous_id'), event['record_id'])
    after = history.record_version(path, event['snapshot_id'], event['record_id'])
    return before, after


def system_summary(rows: list[dict]) -> list[dict]:
    grouped = defaultdict(lambda: {'events': 0, 'records': set(), 'Added': 0, 'Removed': 0,
                                    'Large': 0, 'Medium': 0, 'Small': 0})
    for row in rows:
        item = grouped[(row['system_group'], row['system_type'])]
        item['events'] += 1
        item['records'].add(row['record_id'])
        item[row['size']] += 1
    return sorted([{'system_group': group, 'system_type': type_, 'record_updates': item['events'],
                    'unique_records': len(item['records']), 'added': item['Added'], 'removed': item['Removed'],
                    'large': item['Large'], 'medium': item['Medium'], 'small': item['Small']}
                   for (group, type_), item in grouped.items()],
                  key=lambda row: (-row['record_updates'], row['system_group'], row['system_type']))


def time_summary(rows: list[dict]) -> list[dict]:
    counts = Counter((row['estimated_day'].floor('D'), row['size']) for row in rows)
    return [{'date': day, 'size': size, 'updates': count}
            for (day, size), count in sorted(counts.items())]


def structure_changes(path: str | Path, cfg: dict, start_snapshot: int, end_snapshot: int) -> list[dict]:
    with history.connect(path, cfg) as db:
        raw = db.execute('''SELECT f.json_path,f.old_value,f.new_value,e.system_group,e.system_type,
                e.model_owner,e.estimated_at,e.created_at,e.record_id
            FROM field_changes f JOIN events e USING(snapshot_id,record_id)
            WHERE f.schema_only=1 AND f.snapshot_id BETWEEN ? AND ?''', (start_snapshot, end_snapshot))
        grouped = {}
        for row in raw:
            normalized = re.sub(r'\[[^]]*\]', '[]', row['json_path'])
            item = grouped.setdefault(normalized, {'field': normalized, 'records': set(), 'groups': set(),
                'types': set(), 'owners': set(), 'first_observed': None, 'last_observed': None,
                'added': 0, 'removed': 0, 'type_or_shape_changed': 0})
            item['records'].add(row['record_id'])
            item['groups'].add(row['system_group'])
            item['types'].add(row['system_type'])
            item['owners'].add(row['model_owner'])
            stamp = pd.to_datetime(row['estimated_at'] or row['created_at'], utc=True)
            item['first_observed'] = stamp if item['first_observed'] is None else min(item['first_observed'], stamp)
            item['last_observed'] = stamp if item['last_observed'] is None else max(item['last_observed'], stamp)
            if row['old_value'] is None:
                item['added'] += 1
            elif row['new_value'] is None:
                item['removed'] += 1
            else:
                item['type_or_shape_changed'] += 1
    result = []
    for item in grouped.values():
        kind = 'Field added' if item['added'] and not item['removed'] else 'Field removed' if item['removed'] and not item['added'] else 'Structure changed'
        result.append({'kind': kind, 'field': item['field'], 'affected_records': len(item['records']),
                       'system_groups': ', '.join(sorted(item['groups'])),
                       'system_types': ', '.join(sorted(item['types'])),
                       'owners': ', '.join(sorted(item['owners'])),
                       'first_observed': item['first_observed'], 'last_observed': item['last_observed']})
    return sorted(result, key=lambda row: (-row['affected_records'], row['field']))
