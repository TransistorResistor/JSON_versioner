"""Read-only data preparation for the executive change dashboard."""
from __future__ import annotations

import json
import difflib
import html
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import dashboard_data as history  # noqa: E402
from comparison import selector_values
from configuration import load_config as read_config


SIZE_ORDER = {'Large': 3, 'Medium': 2, 'Small': 1, 'Added': 4, 'Removed': 4}


def category_values(options, selection):
    """Empty Include/Exclude selections mean All."""
    mode = selection.get('mode', 'All')
    values = set(selection.get('values', []))
    if mode == 'Include' and values:
        return [value for value in options if value in values]
    if mode == 'Exclude' and values:
        return [value for value in options if value not in values]
    return list(options)


def category_counts(rows, dimension):
    records = defaultdict(set)
    for row in rows:
        records[row[dimension]].add(row['record_id'])
    return {value: len(ids) for value, ids in sorted(records.items())}


def filter_inventory(path, cfg, start, end):
    with history.connect(path, cfg) as db:
        return [dict(row) for row in db.execute('''SELECT DISTINCT record_id,system_group,system_type,model_owner
            FROM dimensions WHERE snapshot_id BETWEEN ? AND ?''', (start, end))]


def load_config(path: str | Path) -> dict:
    return read_config(path)


def load_history(path: str | Path, cfg: dict, start_snapshot=None, end_snapshot=None) -> tuple[list[dict], list[dict], list[dict]]:
    snapshots = history.snapshots(path)
    if not snapshots:
        return [], [], []
    start_snapshot = snapshots[max(0, len(snapshots) - 30)]['id'] if start_snapshot is None else start_snapshot
    end_snapshot = snapshots[-1]['id'] if end_snapshot is None else end_snapshot
    totals, events = history.period_data(path, cfg, start_snapshot, end_snapshot)
    return snapshots, totals, events


def size_label(event: dict, cfg: dict, semantic: dict | None = None) -> str:
    if event['kind'] == 'added':
        return 'Added'
    if event['kind'] == 'removed':
        return 'Removed'
    if semantic:
        return semantic['overall_update_size'].title()
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


def change_rows(events: list[dict], cfg: dict, include_baseline: bool = False,
                semantic_sizes: dict[tuple[int, str], dict] | None = None) -> list[dict]:
    rows = []
    for event in history.meaningful(events, include_baseline):
        semantic = (semantic_sizes or {}).get((event['snapshot_id'], event['record_id']))
        size = size_label(event, cfg, semantic)
        verb = {'added': 'added', 'removed': 'removed', 'modified': 'edited'}[event['kind']]
        fields = int(semantic['item_changes'] if semantic else event.get('fields_changed') or 0)
        noun = 'item' if semantic else 'field'
        description = f'{fields:,} {noun}{"s" if fields != 1 else ""} edited' if event['kind'] == 'modified' else f'Record {verb}'
        rows.append({**event, 'size': size, 'size_rank': SIZE_ORDER[size],
                     'estimated_day': estimated_day(event),
                     'title': f"{event.get('record_name') or event['record_id']} {verb}",
                     'description': description, 'display_change_count': fields,
                     'size_explanation': size_explanation(event, cfg, semantic)})
    return rows


def size_explanation(event, cfg, semantic=None):
    if event['kind'] in ('added', 'removed'):
        return 'Whole-record addition' if event['kind'] == 'added' else 'Whole-record removal'
    if not semantic:
        return f"{100 * float(event.get('weighted_ratio') or 0):.1f}% weighted field change (stored capture score)."
    if 'change_units' not in semantic:
        return f"{semantic['item_changes']} logical items changed."
    categories = history.content_categories(cfg)
    parts = [f"{semantic.get(category['key'] + '_changes', 0)} {category['label'].lower()}"
             for category in categories if semantic.get(category['key'] + '_changes', 0)]
    thresholds = cfg.get('combined_update_size', {'small_max': 4, 'medium_max': 15})
    return (f"{semantic['change_units']} change units: {', '.join(parts)}. "
            f"Small through {thresholds['small_max']}; Medium through {thresholds['medium_max']}; "
            'Large above that. Each non-description item is 1 unit; description units depend on edited words.')


def semantic_sizes(path: str | Path, cfg: dict, events: list[dict]) -> dict[tuple[int, str], dict]:
    details = history.content_change_details(path, cfg, events)
    return {(row['snapshot_id'], row['record_id']): row
            for row in history.combined_update_sizes(details, cfg)}


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
        return sorted(rows, key=lambda row: (row['estimated_day'], row['snapshot_id'], row['size_rank'], row['record_id']), reverse=True)
    if order == 'Smallest first':
        return sorted(rows, key=lambda row: (row['size_rank'], row.get('weighted_ratio') or 0, row['estimated_day'], row['snapshot_id'], row['record_id']))
    return sorted(rows, key=lambda row: (row['size_rank'], row.get('weighted_ratio') or 0,
                                          row.get('fields_changed') or 0, row['estimated_day'], row['snapshot_id'], row['record_id']), reverse=True)


def change_fields(path: str | Path, snapshot_id: int, record_id: str, schema_only: int = 0,
                  cfg: dict | None = None) -> list[dict]:
    with history.connect(path) as db:
        rows = [dict(row) for row in db.execute('''SELECT json_path,old_value,new_value,word_edits
            FROM field_changes WHERE snapshot_id=? AND record_id=? AND schema_only=? ORDER BY json_path''',
            (snapshot_id, record_id, schema_only))]
    return minimise_parameter_fields(rows, cfg)


def _words(value: str) -> str:
    value = re.sub(r'([a-z0-9])([A-Z])', r'\1 \2', value).replace('_', ' ')
    return value[:1].upper() + value[1:].lower()


def field_display(row: dict) -> dict:
    """Add concise labels and parameter identity to a stored field change."""
    path = row['json_path']
    leaf = path.rsplit('.', 1)[-1]
    result = {**row, 'label': _words(leaf), 'technical_path': path,
              'is_parameter': path.startswith('$.parametrics[')}
    if not result['is_parameter']:
        parts = re.findall(r'[^.\[\]]+(?:\[[^\]]*\])?', path.removeprefix('$.'))
        labels = []
        for part in parts:
            name = _words(re.sub(r'\[.*', '', part))
            selector = re.search(r'\[(.*)\]', part)
            if selector:
                selected = [str(value) for value in selector_values(selector.group(1)).values()
                            if value not in (None, '')]
                if selected:
                    name = ' · '.join(selected)
            labels.append(name)
        result['label'] = ' · '.join(labels)
        return result
    selector = re.search(r'\[(.*)\]', path)
    values = selector_values(selector.group(1)) if selector else {}
    component = values.get('component')
    result['component'] = component if component not in (None, '') else None
    result['parameter'] = values.get('parameter') or 'Parameter'
    result['detail'] = {'parameterUomValue': 'Value', 'parameterValue': 'Value', 'uom': 'Unit',
                        'comments': 'Comment'}.get(leaf, _words(leaf))
    result['label'] = f"{str(result['component']) + ' · ' if result['component'] is not None else ''}{result['parameter']} — {result['detail']}"
    return result


def display_fields(rows: list[dict]) -> list[dict]:
    return [field_display(row) for row in rows]


def inline_word_diff(before: str, after: str) -> str:
    """Return safe, inline track-changes markup for prose."""
    old_tokens = re.findall(r'\s+|\S+', before)
    new_tokens = re.findall(r'\s+|\S+', after)
    result = []
    for tag, old_start, old_end, new_start, new_end in difflib.SequenceMatcher(
            None, old_tokens, new_tokens, autojunk=False).get_opcodes():
        old_text = html.escape(''.join(old_tokens[old_start:old_end]))
        new_text = html.escape(''.join(new_tokens[new_start:new_end]))
        if tag == 'equal':
            result.append(old_text)
        elif tag == 'delete':
            result.append(f'<del>{old_text}</del>')
        elif tag == 'insert':
            result.append(f'<ins>{new_text}</ins>')
        else:
            result.extend((f'<del>{old_text}</del>', f'<ins>{new_text}</ins>'))
    return ''.join(result)


def minimise_parameter_fields(rows: list[dict], cfg: dict | None = None) -> list[dict]:
    """Keep non-excluded fields, collapsing duplicate value/unit representations."""
    display = (cfg or {}).get('dashboard_display', {})
    excluded_params = {str(value).casefold() for value in display.get(
        'exclude_parameter_fields', ['parameterDescr', 'parameterOnly', 'seq', 'dataType', 'parameter', 'component'])}
    excluded_descriptions = {str(value).casefold() for value in display.get('exclude_description_fields', [])}
    excluded_other = display.get('exclude_other_paths', [])
    result = []
    for row in rows:
        path = row['json_path']
        leaf = path.rsplit('.', 1)[-1].casefold()
        if path.startswith('$.parametrics['):
            continue
        if path.startswith('$.descriptions[') and leaf in excluded_descriptions:
            continue
        normalized = re.sub(r'\[[^]]*\]', '[]', path)
        if path in excluded_other or normalized in excluded_other:
            continue
        result.append(row)
    grouped = defaultdict(list)
    for row in rows:
        if row['json_path'].startswith('$.parametrics['):
            grouped[row['json_path'].rsplit('.', 1)[0]].append(row)
    for item_rows in grouped.values():
        by_leaf = {row['json_path'].rsplit('.', 1)[-1].casefold(): row for row in item_rows
                   if row['json_path'].rsplit('.', 1)[-1].casefold() not in excluded_params}
        if 'parameteruomvalue' in by_leaf:
            by_leaf.pop('parametervalue', None)
            by_leaf.pop('uom', None)
        result.extend(by_leaf.values())
    return sorted(result, key=lambda row: row['json_path'])


def record_versions(path: str | Path, event: dict):
    return history.event_versions(path, event)



def record_timeline(path: str | Path, record_id: str) -> list[dict]:
    """List snapshots containing a record, newest first."""
    with history.connect(path) as db:
        return [dict(row) for row in db.execute('''SELECT s.id AS snapshot_id,s.created_at,s.note,r.filename
            FROM records r JOIN snapshots s ON s.id=r.snapshot_id
            WHERE r.record_id=? ORDER BY s.id DESC''', (record_id,))]


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


def structure_changes(path: str | Path, cfg: dict, start_snapshot: int, end_snapshot: int,
                      filters=None, start=None, end=None) -> list[dict]:
    start = pd.Timestamp(start, tz='UTC') if start else None
    end = pd.Timestamp(end, tz='UTC') + pd.Timedelta(days=1) if end else None
    with history.connect(path, cfg) as db:
        raw = db.execute('''SELECT f.json_path,f.old_value,f.new_value,e.system_group,e.system_type,
                e.model_owner,e.estimated_at,e.created_at,e.record_id
            FROM field_changes f JOIN events e USING(snapshot_id,record_id)
            WHERE f.schema_only=1 AND f.snapshot_id BETWEEN ? AND ?''', (start_snapshot, end_snapshot))
        grouped = {}
        for row in raw:
            if filters is not None and any(row[key] not in values for key, values in filters.items()):
                continue
            stamp = pd.to_datetime(row['estimated_at'] or row['created_at'], utc=True)
            if (start is not None and stamp < start) or (end is not None and stamp >= end):
                continue
            normalized = re.sub(r'\[[^]]*\]', '[]', row['json_path'])
            item = grouped.setdefault(normalized, {'field': normalized, 'records': set(), 'groups': set(),
                'types': set(), 'owners': set(), 'first_observed': None, 'last_observed': None,
                'added': 0, 'removed': 0, 'type_or_shape_changed': 0})
            item['records'].add(row['record_id'])
            item['groups'].add(row['system_group'])
            item['types'].add(row['system_type'])
            item['owners'].add(row['model_owner'])
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
