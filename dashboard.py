"""Explore JSON snapshot changes. Run with: python -m streamlit run dashboard.py"""
from __future__ import annotations

import csv
import io
import json
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent
UNKNOWN = '(Unspecified)'
st.set_page_config(page_title='JSON change history', layout='wide')
st.title('JSON change history')


def table(rows: list[dict], key: str) -> None:
    st.dataframe(rows, use_container_width=True, hide_index=True)
    if rows:
        stream = io.StringIO()
        columns = list(dict.fromkeys(column for row in rows for column in row))
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
        st.download_button('Download CSV', stream.getvalue().encode('utf-8-sig'),
                           f'{key}.csv', 'text/csv', key=key)


def value_expr(json_column: str, fields: list[str]) -> str:
    """Read the first populated top-level classification field from JSON."""
    expressions = []
    for field in fields:
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', field):
            raise ValueError(f'Classification field must be a simple top-level name: {field!r}')
        expressions.append(f"NULLIF(CAST(json_extract({json_column}, '$.{field}') AS TEXT), '')")
    return f"COALESCE({', '.join(expressions + [repr(UNKNOWN)])})"


def category(ratio: float, tiny: float, small: float, medium: float) -> str:
    return 'Tiny' if ratio <= tiny else 'Small' if ratio <= small else 'Medium' if ratio <= medium else 'Large'


db_path = Path(st.sidebar.text_input('History database', str(ROOT / 'json_history' / 'history.sqlite3')))
if not db_path.is_file():
    st.info('Create a snapshot first with: python version_json.py "C:\\path\\to\\records"')
    st.stop()
try:
    db = sqlite3.connect(f'file:{db_path.resolve().as_posix()}?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    snapshots = [dict(r) for r in db.execute('SELECT * FROM snapshots ORDER BY id')]
except sqlite3.Error as exc:
    st.error(f'Cannot read history: {exc}')
    st.stop()
if not snapshots:
    st.info('No snapshots yet.')
    st.stop()

snapshot_by_id = {s['id']: s for s in snapshots}
ids = list(snapshot_by_id)
st.sidebar.subheader('Time period')
start_id, end_id = st.sidebar.select_slider(
    'From / through snapshot', options=ids, value=(ids[0], ids[-1]))
st.sidebar.caption(f"{snapshot_by_id[start_id]['created_at']} through {snapshot_by_id[end_id]['created_at']}")
period = [s for s in snapshots if start_id <= s['id'] <= end_id]
sid = st.sidebar.selectbox('Inspect snapshot', [s['id'] for s in period], index=len(period)-1)
selected = snapshot_by_id[sid]
st.sidebar.caption(f"{selected['created_at']} | {selected['note'] or 'untitled'}")
cfg = json.loads(selected['config_json'])
local_config = ROOT / 'config.json'
if local_config.exists():
    cfg.update(json.loads(local_config.read_text(encoding='utf-8')))
try:
    group_expr = value_expr('r.json_text', cfg.get('group_fields', ['systemGroup']))
    family_expr = value_expr('r.json_text', cfg.get('type_fields', ['systemType']))
    group_change_expr = value_expr('COALESCE(r.json_text, old.json_text)', cfg.get('group_fields', ['systemGroup']))
    family_change_expr = value_expr('COALESCE(r.json_text, old.json_text)', cfg.get('type_fields', ['systemType']))
except ValueError as exc:
    st.error(str(exc))
    st.stop()

# Aggregate classification from the stored JSON. This also works with history
# created before group/family filters were added, without a database migration.
try:
    grouped_totals = [dict(r) for r in db.execute(f'''
        SELECT r.snapshot_id, {group_expr} AS system_group, {family_expr} AS family,
               COUNT(*) AS total
        FROM records r WHERE r.snapshot_id BETWEEN ? AND ?
        GROUP BY r.snapshot_id, system_group, family''', (start_id, end_id))]
    change_rows = [dict(r) for r in db.execute(f'''
        SELECT rc.*, s.created_at, s.previous_id,
               {group_change_expr} AS system_group, {family_change_expr} AS family,
               EXISTS(SELECT 1 FROM field_changes f WHERE f.snapshot_id=rc.snapshot_id
                   AND f.record_id=rc.record_id AND f.schema_only=1) AS has_schema_change
        FROM record_changes rc JOIN snapshots s ON s.id=rc.snapshot_id
        LEFT JOIN records r ON r.snapshot_id=rc.snapshot_id AND r.record_id=rc.record_id
        LEFT JOIN records old ON old.snapshot_id=s.previous_id AND old.record_id=rc.record_id
        WHERE rc.snapshot_id BETWEEN ? AND ?''', (start_id, end_id))]
except sqlite3.Error as exc:
    st.error(f'Cannot read classifications: {exc}')
    st.stop()

groups = sorted({r['system_group'] for r in grouped_totals} | {r['system_group'] for r in change_rows})
chosen_groups = st.sidebar.multiselect('System groups', groups, default=groups)
families = sorted({r['family'] for r in grouped_totals + change_rows if r['system_group'] in chosen_groups})
chosen_families = st.sidebar.multiselect('System types', families, default=families)


def in_scope(row: dict) -> bool:
    return row['system_group'] in chosen_groups and row['family'] in chosen_families


totals = [r for r in grouped_totals if in_scope(r)]
changes = [r for r in change_rows if in_scope(r)]
selected_changes = [r for r in changes if r['snapshot_id'] == sid]
selected_total = sum(r['total'] for r in totals if r['snapshot_id'] == sid)
selected_counts = Counter(r['change_type'] for r in selected_changes)
selected_schema = sum(r['change_type'] == 'unchanged' and r['has_schema_change'] for r in selected_changes)
selected_unchanged = selected_total - selected_counts['added'] - selected_counts['modified']
affected = selected_counts['added'] + selected_counts['removed'] + selected_counts['modified']
denominator = max(selected_total + selected_counts['removed'], 1)

st.subheader('Snapshot summary')
st.caption(f"Snapshot {sid} | {selected['created_at']} | {', '.join(chosen_groups) if chosen_groups else 'No groups selected'}")
metrics = [('Total', selected_total), ('Added', selected_counts['added']),
           ('Removed', selected_counts['removed']), ('Modified', selected_counts['modified']),
           ('Unchanged', selected_unchanged), ('Affected', f'{affected/denominator:.1%}'),
           ('Schema only', selected_schema)]
for col, (label, value) in zip(st.columns(7), metrics):
    col.metric(label, value)

st.subheader('Breakdown by system group and system type')
breakdown = []
for entry in [r for r in totals if r['snapshot_id'] == sid]:
    subset = [r for r in selected_changes if r['system_group'] == entry['system_group'] and r['family'] == entry['family']]
    counts = Counter(r['change_type'] for r in subset)
    breakdown.append({'system_group': entry['system_group'], 'system_type': entry['family'],
                      'total': entry['total'], 'added': counts['added'], 'removed': counts['removed'],
                      'modified': counts['modified'],
                      'unchanged': entry['total'] - counts['added'] - counts['modified']})
# Removed-only groups are absent from the current snapshot but still matter.
for group, family in sorted({(r['system_group'], r['family']) for r in selected_changes if r['change_type'] == 'removed'}):
    if not any(r['system_group'] == group and r['system_type'] == family for r in breakdown):
        breakdown.append({'system_group': group, 'system_type': family, 'total': 0, 'added': 0,
                          'removed': sum(r['change_type'] == 'removed' and r['system_group'] == group and r['family'] == family
                                         for r in selected_changes), 'modified': 0, 'unchanged': 0})
table(breakdown, 'group_family_breakdown')

thresholds = cfg.get('change_thresholds', {'tiny': .02, 'small': .10, 'medium': .40})
st.sidebar.subheader('Change ratio boundaries')
tiny = st.sidebar.number_input('Tiny up to', 0.0, 1.0, float(thresholds['tiny']), step=.01)
small = st.sidebar.number_input('Small up to', 0.0, 1.0, float(thresholds['small']), step=.01)
medium = st.sidebar.number_input('Medium up to', 0.0, 1.0, float(thresholds['medium']), step=.01)
if not tiny <= small <= medium:
    st.sidebar.error('Boundaries must increase.')
    st.stop()

modified = [r for r in selected_changes if r['change_type'] == 'modified']
for row in modified:
    row['magnitude'] = category(row['weighted_ratio'], tiny, small, medium)
st.subheader('Change magnitude')
st.bar_chart(pd.Series({name: sum(r['magnitude'] == name for r in modified)
                        for name in ('Tiny', 'Small', 'Medium', 'Large')}, name='records'))

st.subheader('Historical trends')
trend_rows = []
for snapshot in period:
    snapshot_id = snapshot['id']
    total = sum(r['total'] for r in totals if r['snapshot_id'] == snapshot_id)
    rows = [r for r in changes if r['snapshot_id'] == snapshot_id]
    counts = Counter(r['change_type'] for r in rows)
    impacted = counts['added'] + counts['removed'] + counts['modified']
    trend_rows.append({'snapshot': snapshot_id, 'time': snapshot['created_at'], 'total': total,
                       'added': counts['added'], 'removed': counts['removed'], 'modified': counts['modified'],
                       'schema_only': sum(r['change_type'] == 'unchanged' and r['has_schema_change'] for r in rows),
                       'affected_percent': round(100 * impacted / max(total + counts['removed'], 1), 2)})
table(trend_rows, 'historical_trends')
st.line_chart(pd.DataFrame(trend_rows).set_index('snapshot')[['added', 'removed', 'modified', 'affected_percent']])

st.subheader('Estimated change timing')
st.caption('Record timestamps are used for meaningful modifications only when they fall between the two snapshot times. Other events use the observation time.')
timed_changes = [r for r in changes if r['change_type'] in ('added', 'removed', 'modified') and r.get('event_at')]
by_day = Counter(r['event_at'][:10] for r in timed_changes)
timing_rows = [{'date': day, 'changes': count,
                'from_record_timestamp': sum(r['event_at'][:10] == day and r.get('event_basis') == 'record timestamp'
                                             for r in timed_changes)}
               for day, count in sorted(by_day.items())]
table(timing_rows, 'change_timing')
if timing_rows:
    st.bar_chart(pd.DataFrame(timing_rows).set_index('date')[['changes', 'from_record_timestamp']])

st.subheader('Modified records')
search = st.text_input('Search record ID or filename').lower()
allowed_magnitudes = st.multiselect('Magnitude', ['Tiny', 'Small', 'Medium', 'Large'],
                                    default=['Tiny', 'Small', 'Medium', 'Large'])
visible = [r for r in modified if r['magnitude'] in allowed_magnitudes
           and search in (r['record_id'] + ' ' + r['filename']).lower()]
table([{'record_id': r['record_id'], 'filename': r['filename'], 'system_group': r['system_group'],
        'system_type': r['family'], 'magnitude': r['magnitude'], 'weighted_ratio': r['weighted_ratio'],
        'fields_changed': r['fields_changed'], 'fields_total': r['fields_total'],
        'previous_snapshot': r['previous_id'], 'snapshot': r['snapshot_id'],
        'estimated_change_at': r.get('event_at'), 'time_basis': r.get('event_basis')}
       for r in visible], 'modified_records')

st.subheader('Record detail')
record_ids = sorted({r['record_id'] for r in changes})
if record_ids:
    chosen = st.selectbox('Record', record_ids)
    history = [{'snapshot': r['snapshot_id'], 'time': r['created_at'], 'change_type': r['change_type'],
                'filename': r['filename'], 'system_group': r['system_group'], 'system_type': r['family'],
                'fields_changed': r['fields_changed'], 'change_ratio': r['change_ratio'],
                'weighted_ratio': r['weighted_ratio'], 'estimated_change_at': r.get('event_at'),
                'time_basis': r.get('event_basis'), 'reported_updated_at': r.get('reported_updated_at')}
               for r in changes if r['record_id'] == chosen]
    table(sorted(history, key=lambda r: -r['snapshot']), 'record_history')
    details = [dict(r) for r in db.execute('''SELECT f.snapshot_id,s.created_at,rc.event_at AS estimated_change_at,
        f.json_path,f.old_value,f.new_value,f.schema_only,f.word_edits
        FROM field_changes f JOIN snapshots s ON s.id=f.snapshot_id
        LEFT JOIN record_changes rc ON rc.snapshot_id=f.snapshot_id AND rc.record_id=f.record_id
        WHERE f.record_id=? AND f.snapshot_id BETWEEN ? AND ?
        ORDER BY f.snapshot_id DESC,f.json_path''', (chosen, start_id, end_id))]
    for row in details:
        n = row['word_edits']
        row['description_size'] = ('Small' if n < 50 else 'Medium' if n <= 250 else 'Large') if n is not None else ''
        row['change_kind'] = 'schema only' if row.pop('schema_only') else 'value'
    table(details, 'record_fields')
else:
    st.info('No changed records in this selection.')

allowed_pairs = {(r['snapshot_id'], r['record_id']) for r in changes}
fields = [dict(r) for r in db.execute('''SELECT snapshot_id,record_id,json_path,word_edits
    FROM field_changes WHERE snapshot_id BETWEEN ? AND ? AND schema_only=0''', (start_id, end_id))
          if (r['snapshot_id'], r['record_id']) in allowed_pairs]

st.subheader('Description changes')
descriptions = [r for r in fields if r['snapshot_id'] == sid and r['word_edits'] is not None]
for row in descriptions:
    n = row['word_edits']
    row['size'] = 'Small' if n < 50 else 'Medium' if n <= 250 else 'Large'
    event = next((c for c in selected_changes if c['record_id'] == row['record_id']), None)
    row['estimated_change_at'] = event.get('event_at') if event else None
size_filter = st.multiselect('Description edit size', ['Small', 'Medium', 'Large'], default=['Medium', 'Large'])
st.caption('Word edits count additions, removals, and replacements. Medium: 50–250; Large: over 250.')
table(sorted((r for r in descriptions if r['size'] in size_filter), key=lambda r: -r['word_edits']),
      'description_changes')

st.subheader('Field analysis')
field_counts = Counter(r['json_path'] for r in fields)
field_snapshots = defaultdict(set)
for row in fields:
    field_snapshots[row['json_path']].add(row['snapshot_id'])
table([{'json_path': path, 'records_changed': count, 'snapshots_changed': len(field_snapshots[path])}
       for path, count in field_counts.most_common(500)], 'field_frequency')
selected_field_counts = Counter(r['json_path'] for r in fields if r['snapshot_id'] == sid)
comparable = max(selected_total - selected_counts['added'], 1)
st.caption('Bulk fields in the inspected snapshot: changed in at least 20% of comparable records.')
table([{'json_path': path, 'records_changed': count, 'percent_of_records': round(100*count/comparable, 1)}
       for path, count in selected_field_counts.most_common() if count/comparable >= .2], 'bulk_fields')

st.subheader('Change matrix')
matrix_counts = Counter((r['json_path'], r['snapshot_id']) for r in fields)
matrix = defaultdict(dict)
for (path, snapshot_id), count in matrix_counts.items():
    matrix[path][str(snapshot_id)] = count
matrix_rows = [{'field': path, **counts} for path, counts in matrix.items()]
matrix_rows.sort(key=lambda row: -sum(value for key, value in row.items() if key != 'field'))
table(matrix_rows[:100], 'change_matrix')
db.close()
