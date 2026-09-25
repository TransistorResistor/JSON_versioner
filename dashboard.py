"""Category-first JSON history dashboard: python -m streamlit run dashboard.py."""
from __future__ import annotations

import hashlib
import inspect
import json
import math
import sqlite3
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

import dashboard_data as data

ROOT = Path(__file__).resolve().parent
VIEW_OVERVIEW = 'Where changes are happening'
VIEW_CONTENT = 'What changed'
VIEW_TIMING = 'When changes happened'
VIEW_EXPLORE = 'Explore records'
VIEW_HEALTH = 'History health'
VIEWS = [VIEW_OVERVIEW, VIEW_CONTENT, VIEW_TIMING, VIEW_EXPLORE, VIEW_HEALTH]
LEGACY_VIEWS = dict(zip(
    ['Category trends', 'Content changes', 'Timing & scope', 'Change explorer', 'Storage & health'], VIEWS))
FILTER_KEYS = {'system_group': 'filter_groups', 'system_type': 'filter_types', 'model_owner': 'filter_owners'}
st.set_page_config(page_title='Database change observatory', page_icon='◷', layout='wide')


def fingerprint(value):
    return hashlib.sha256(str(value).encode()).hexdigest()[:12]


def select_value(label, values, format_func, index=0, key=None):
    """Keep labels as widget values, including on older Streamlit runtimes."""
    values = list(values)
    labels = [format_func(value) for value in values]
    chosen = st.selectbox(label, labels, index=index, key=key)
    return values[labels.index(chosen)]


def database_stamp(path):
    result = []
    for item in (path, Path(str(path)+'-wal')):
        stat = item.stat() if item.exists() else None
        result.append((stat.st_mtime_ns, stat.st_size) if stat else None)
    return tuple(result)


@st.cache_data(show_spinner=False, max_entries=4)
def load_period(path, stamp, cfg_json, start, end):
    return data.period_data(path, json.loads(cfg_json), start, end)


@st.cache_data(show_spinner=False, max_entries=16)
def load_fields(path, stamp, cfg_json, event_keys):
    return data.field_scope(path, json.loads(cfg_json), event_keys)


@st.cache_data(show_spinner=False, max_entries=16)
def load_content_changes(path, stamp, cfg_json, events_json):
    return data.content_change_details(path, json.loads(cfg_json), json.loads(events_json))


@st.cache_data(show_spinner=False, max_entries=64)
def load_records(path, stamp, cfg_json, snapshot_id, filters_json, search, page):
    return data.record_page(path, json.loads(cfg_json), snapshot_id, json.loads(filters_json), search, page)


@st.cache_data(show_spinner=False, max_entries=64)
def load_comparison(path, stamp, record_id, before_id, after_id):
    return data.comparison(path, record_id, before_id, after_id)


def table(rows, name, limit=500):
    if not rows:
        st.info('No matching data for this selection.')
        return
    frame = pd.DataFrame(rows)
    st.dataframe(frame.head(limit), use_container_width=True, hide_index=True)
    if len(frame) > limit:
        st.caption(f'Showing {limit:,} of {len(frame):,} rows. The CSV contains all rows in this filtered result.')
    st.download_button('Download CSV', frame.to_csv(index=False).encode('utf-8-sig'),
                       f'{name}.csv', 'text/csv', key='csv_'+name)


def chart(rows, mark, x, y, color=None, tooltip=None, height=300):
    if not rows:
        st.info('No changes in this selection.')
        return
    plot = alt.Chart(alt.Data(values=rows))
    plot = plot.mark_line(point=True) if mark == 'line' else plot.mark_rect() if mark == 'heatmap' else plot.mark_bar()
    encodings = {'x': x, 'y': y, 'tooltip': tooltip or []}
    if color is not None:
        encodings['color'] = color
    st.altair_chart(plot.encode(**encodings).properties(height=height), use_container_width=True)


def select_filters(totals, events):
    rows = totals + events
    filters = {}
    for column, label in [('system_group', 'System groups'), ('system_type', 'System types'), ('model_owner', 'Model owners')]:
        options = sorted({r[column] for r in rows if all(r[k] in values for k, values in filters.items())})
        key = FILTER_KEYS[column]
        if key not in st.session_state and 'saved_'+key in st.session_state:
            st.session_state[key] = st.session_state['saved_'+key]
        if key in st.session_state:
            st.session_state[key] = [x for x in st.session_state[key] if x in options]
        filters[column] = st.sidebar.multiselect(label, options, default=options, key=key)
        st.session_state['saved_'+key] = filters[column]
    return filters


def reset_filters():
    for key in FILTER_KEYS.values():
        st.session_state.pop(key, None)
        st.session_state.pop('saved_'+key, None)
    st.session_state.pop('event_search', None)


def drill_category(dimension, category):
    st.session_state[FILTER_KEYS[dimension]] = [category]
    st.session_state['view'] = VIEW_EXPLORE


def trends(totals, events, period, include_baseline):
    st.header('Where are changes happening?')
    controls = st.columns([1, 1, 1])
    dimension_label = controls[0].selectbox('Compare categories by', list(data.DIMENSIONS))
    dimension = data.DIMENSIONS[dimension_label]
    measure_label = controls[1].selectbox('Measure', ['Affected records (%)', 'Change events', 'Population'])
    measure = {'Affected records (%)': 'affected_percent', 'Change events': 'events', 'Population': 'population'}[measure_label]
    top_n = controls[2].selectbox('Categories shown', [10, 20, 50], index=0)
    series = data.category_series(totals, events, period, dimension, include_baseline)
    summary = data.category_summary(series, events, dimension, include_baseline)
    if not summary:
        st.info('No categories match these filters. Reset the filters to broaden the selection.')
        return
    rank_key = {'population': 'population_end', 'events': 'events', 'affected_percent': 'peak_affected_percent'}[measure]
    ranked = sorted(summary, key=lambda r: (-(r[rank_key] or 0), r['category']))
    categories = [r['category'] for r in ranked[:top_n]]
    shown = [r for r in series if r['category'] in categories]
    labels = {r['id']: f"#{r['id']} · {r['created_at'][:16].replace('T', ' ')}" for r in period}
    for row in shown:
        row['observation'] = labels[row['snapshot_id']]
    st.subheader('How is change distributed over time?')
    ranking = {'population': 'end population', 'events': 'event count', 'affected_percent': 'peak affected percentage'}[measure]
    st.caption(f'Categories are ranked by {ranking}. Every observation is compared with its own predecessor, including the first observation in the selected range.')
    chart(shown, 'heatmap', alt.X('observation:N', sort=list(labels.values()), title='Observation (UTC)', axis=alt.Axis(labelAngle=-40)),
          alt.Y('category:N', sort=categories, scale=alt.Scale(domain=categories), title=dimension_label),
          alt.Color(f'{measure}:Q', title=measure_label, scale=alt.Scale(scheme='blues', domain=[0, 100]) if measure == 'affected_percent' else alt.Scale(scheme='blues', zero=True)),
          ['category:N', 'observation:N', 'population:Q', 'added:Q', 'removed:Q', 'modified:Q',
           alt.Tooltip('affected_percent:Q', format='.1f'), 'baseline_excluded:N'], height=max(280, min(900, len(categories)*36+100)))
    st.caption('Affected % = (added + removed + modified) / (current population + removed). Blank rates mean no denominator or an excluded initial baseline. Rates describe each observation, not an additive period total.')
    focus = st.multiselect('Trend lines', categories, default=categories[:5])
    line_rows = [r for r in shown if r['category'] in focus]
    chart(line_rows, 'line', alt.X('observation:N', sort=list(labels.values()), title='Observation sequence (UTC)', axis=alt.Axis(labelAngle=-40)),
          alt.Y(f'{measure}:Q', title=measure_label), alt.Color('category:N', title=dimension_label),
          ['category:N', 'snapshot_id:O', 'observed_at:T', alt.Tooltip(f'{measure}:Q', format='.1f')])
    st.subheader('How much did each category change?')
    st.caption('Unique records count each ID once per category during the period; events count repeated changes. Population delta compares the first and last selected observations and includes category/owner transfers.')
    table(summary, 'category_scope')
    category = st.selectbox('Inspect category', [r['category'] for r in summary])
    st.button('Explore changes in this category', on_click=drill_category, args=(dimension, category))
    st.subheader('Which owners and system types changed?')
    active = data.meaningful(events, include_baseline)
    owners = {}
    for r in active:
        key = r['system_type'], r['model_owner']
        item = owners.setdefault(key, {'system_type': key[0], 'model_owner': key[1], 'events': 0, 'ids': set()})
        item['events'] += 1
        item['ids'].add(r['record_id'])
    owner_rows = sorted([{'system_type': r['system_type'], 'model_owner': r['model_owner'], 'events': r['events'],
                          'unique_records': len(r['ids'])} for r in owners.values()], key=lambda r: -r['events'])
    table(owner_rows, 'type_owner_scope')


def content_changes_view(path, stamp, cfg, snapshots, events, include_baseline):
    st.header('What changed inside the records?')
    active = data.meaningful(events, include_baseline)
    # Item-level history exists for modified records. Whole-record additions
    # and removals remain separate inventory events by design.
    modified = [row for row in active if row['kind'] == 'modified']
    cfg_json = json.dumps(cfg, sort_keys=True)
    events_json = json.dumps(modified, sort_keys=True)
    with st.spinner('Classifying changed JSON fields…'):
        details = load_content_changes(path, stamp, cfg_json, events_json)
    summary = data.content_change_summary(details)
    additions = sum(row['kind'] == 'added' for row in active)
    removals = sum(row['kind'] == 'removed' for row in active)
    record_category_events = {(row['snapshot_id'], row['record_id'], row['content_key']) for row in details}
    description_large = sum(row['content_key'] == 'descriptions' and row['magnitude'] == 'Large' for row in details)
    a, b, c, d = st.columns(4)
    a.metric('Records with categorised edits', f"{len({row['record_id'] for row in details}):,}")
    b.metric('Record-category events', f'{len(record_category_events):,}')
    c.metric('Item / metadata changes', f'{len(details):,}')
    d.metric('Large description edits', f'{description_large:,}')
    st.caption(f'{additions:,} whole records added and {removals:,} removed in this selection. Their initial/final contents are excluded from item-operation totals, so record inventory changes do not swamp editing activity.')
    if not details:
        st.info('No field-level content changes match this selection. Added and removed whole records are shown above; schema-only changes are excluded.')
        return

    st.subheader('Which content changed in each system type?')
    heat = {}
    for row in details:
        key = row['system_type'], row['content_category']
        item = heat.setdefault(key, {'system_type': key[0], 'content_category': key[1], 'events': set(), 'records': set(), 'item_changes': 0})
        item['events'].add((row['snapshot_id'], row['record_id']))
        item['records'].add(row['record_id'])
        item['item_changes'] += 1
    heat_rows = [{'system_type': item['system_type'], 'content_category': item['content_category'],
                  'record_events': len(item['events']), 'unique_records': len(item['records']),
                  'item_changes': item['item_changes']} for item in heat.values()]
    heat_measure_label = st.selectbox('Heatmap measure', ['Record-category events', 'Unique records', 'Item changes'])
    heat_measure = {'Record-category events': 'record_events', 'Unique records': 'unique_records', 'Item changes': 'item_changes'}[heat_measure_label]
    types = [name for name, _ in sorted(((name, sum(row[heat_measure] for row in heat_rows if row['system_type'] == name))
                                         for name in {row['system_type'] for row in heat_rows}), key=lambda pair: (-pair[1], pair[0]))]
    labels = [category['label'] for category in data.content_categories(cfg)]
    chart(heat_rows, 'heatmap', alt.X('content_category:N', sort=labels, title='Content category'),
          alt.Y('system_type:N', sort=types, scale=alt.Scale(domain=types), title='System type'),
          alt.Color(f'{heat_measure}:Q', title=heat_measure_label, scale=alt.Scale(scheme='blues', zero=True)),
          ['system_type:N', 'content_category:N', 'record_events:Q', 'unique_records:Q', 'item_changes:Q'],
          height=max(280, min(900, len(types)*34+100)))
    st.caption('A record changed in several content categories appears once in each relevant cell. Item changes count description, parameter, relationship, and media entries; Metadata items are changed fields.')

    st.subheader('When did each kind of content change?')
    time_measure_label = st.selectbox('Timeline measure', ['Record-category events', 'Item changes'], key='content_time_measure')
    grouped = {}
    for row in details:
        key = row['observed_at'], row['content_category']
        item = grouped.setdefault(key, {'observed_at': key[0], 'content_category': key[1], 'events': set(), 'item_changes': 0})
        item['events'].add((row['snapshot_id'], row['record_id']))
        item['item_changes'] += 1
    timeline = [{'observed_at': item['observed_at'], 'content_category': item['content_category'],
                 'record_events': len(item['events']), 'item_changes': item['item_changes']} for item in grouped.values()]
    time_measure = 'record_events' if time_measure_label == 'Record-category events' else 'item_changes'
    chart(timeline, 'bar', alt.X('observed_at:T', title='Observation (UTC)'),
          alt.Y(f'{time_measure}:Q', title=time_measure_label, stack='zero'),
          alt.Color('content_category:N', sort=labels, title='Content category'),
          ['observed_at:T', 'content_category:N', 'record_events:Q', 'item_changes:Q'])

    st.subheader('How substantial were the description edits?')
    description_rows = [row for row in details if row['content_key'] == 'descriptions']
    description_counts = []
    for label, predicate in [
        ('Added', lambda row: row['operation'] == 'added'),
        ('Removed', lambda row: row['operation'] == 'removed'),
        ('Small', lambda row: row['magnitude'] == 'Small'),
        ('Medium', lambda row: row['magnitude'] == 'Medium'),
        ('Large', lambda row: row['magnitude'] == 'Large'),
        ('Other', lambda row: row['magnitude'] == 'Other'),
    ]:
        description_counts.append({'description_change': label, 'items': sum(predicate(row) for row in description_rows)})
    chart(description_counts, 'bar', alt.X('items:Q', title='Description entries'),
          alt.Y('description_change:N', sort=['Added', 'Removed', 'Small', 'Medium', 'Large', 'Other'], title=None),
          tooltip=['description_change:N', 'items:Q'], height=230)
    thresholds = cfg.get('description_edit_thresholds', {'small': 50, 'medium': 250})
    st.caption(f"Modified description entries are Small below {thresholds['small']} edited words, Medium from {thresholds['small']} through {thresholds['medium']}, and Large above {thresholds['medium']}. “Other” means a description entry changed outside its text fields. Added and removed entries are shown separately.")

    st.subheader('Were items added, removed or modified?')
    operation_rows = []
    for row in summary:
        for operation in ('added', 'removed', 'modified'):
            operation_rows.append({'content_category': row['content_category'], 'operation': operation.title(), 'items': row[operation]})
    chart(operation_rows, 'bar', alt.X('items:Q', title='Items / metadata fields'),
          alt.Y('content_category:N', sort=labels, title='Content category'),
          alt.Color('operation:N', sort=['Added', 'Removed', 'Modified'], title='Operation'),
          ['content_category:N', 'operation:N', 'items:Q'], height=260)
    table(summary, 'content_change_summary')

    st.subheader('Explore the underlying content changes')
    left, middle, right = st.columns(3)
    category_options = sorted({row['content_category'] for row in details})
    selected_categories = left.multiselect('Content categories', category_options, default=category_options)
    operation_options = ['added', 'removed', 'modified']
    selected_operations = middle.multiselect('Operations', operation_options, default=operation_options)
    magnitude_options = ['Small', 'Medium', 'Large', 'Other', '(Not applicable)']
    selected_magnitudes = right.multiselect('Description sizes', magnitude_options, default=magnitude_options)
    search = st.text_input('Search record, item, field, system type or owner', key='content_search').casefold()
    visible = [row for row in details if row['content_category'] in selected_categories
               and row['operation'] in selected_operations
               and (row['magnitude'] or '(Not applicable)') in selected_magnitudes
               and search in ' '.join(str(row.get(key) or '') for key in
                   ('record_id', 'record_name', 'item', 'changed_fields', 'system_type', 'model_owner')).casefold()]
    columns = ['snapshot_id', 'observed_at', 'record_id', 'record_name', 'system_type', 'model_owner',
               'content_category', 'item', 'operation', 'magnitude', 'word_edits', 'field_edits', 'changed_fields']
    table([{key: row[key] for key in columns} for row in visible], 'content_change_details')
    if visible:
        selected = select_value('Inspect record event', range(len(visible)),
            lambda index: f"#{visible[index]['snapshot_id']} · {visible[index]['record_name']} · {visible[index]['content_category']} · {visible[index]['item']}",
            key='content_detail_'+fingerprint([(row['snapshot_id'], row['record_id'], row['item_path']) for row in visible]))
        row = visible[selected]
        event = next(item for item in modified if item['snapshot_id'] == row['snapshot_id'] and item['record_id'] == row['record_id'])
        record_detail(path, stamp, cfg, snapshots, row['record_id'], event['previous_id'], event['snapshot_id'])


def timing_scope(path, stamp, cfg_json, events, include_baseline):
    st.header('When did changes happen?')
    a, b = st.columns(2)
    clock = a.radio('Time basis', ['Observed', 'Estimated'], horizontal=True)
    bucket = b.selectbox('Group timing by', ['Day', 'Week', 'Month'])
    rows = data.timing(events, clock, bucket, include_baseline)
    chart(rows, 'bar', alt.X('period:T', title=f'{bucket} starting (UTC)'), alt.Y('events:Q', title='Change events'),
          alt.Color('basis:N', title='Timing basis'), ['period:T', 'basis:N', 'events:Q'])
    st.caption('Estimated time uses a valid record timestamp for modifications; other events use the observation time. The series are mutually exclusive. Estimated dates can precede the selected observation range. Weeks start Monday.')
    with st.expander('Timing data'):
        table(rows, 'timing')
    active = data.meaningful(events, include_baseline)
    counts = [{'change': kind.title(), 'events': sum(r['kind'] == kind for r in active)} for kind in data.MEANINGFUL]
    st.subheader('What kinds of change occurred?')
    chart(counts, 'bar', alt.X('events:Q', title='Change events'), alt.Y('change:N', title=None), height=160)
    st.subheader('Which fields changed most?')
    keys = tuple((r['snapshot_id'], r['record_id']) for r in active)
    with st.spinner('Aggregating changed fields…'):
        fields = load_fields(path, stamp, cfg_json, keys)
    st.caption('Unique records and record events are counted separately. Array element paths are grouped under []. A record can affect several fields, so field totals overlap. Added/removed records have no leaf-level change log.')
    chart(fields[:20], 'bar', alt.X('unique_records:Q', title='Unique records'),
          alt.Y('field:N', sort='-x', title='Field (top 20)'),
          tooltip=['field:N', 'unique_records:Q', 'record_events:Q', 'field_edits:Q', 'word_edits:Q'], height=max(180, min(600, len(fields)*25)))
    table(fields, 'field_scope')
    st.subheader('Which records moved between categories or owners?')
    transfers = []
    for row in active:
        if row['kind'] != 'modified':
            continue
        for field in data.DIMENSIONS.values():
            old, new = row.get('previous_'+field), row[field]
            if old is not None and old != new:
                transfers.append({'snapshot': row['snapshot_id'], 'record_id': row['record_id'], 'name': row['record_name'],
                    'dimension': field, 'from': old, 'to': new, 'observed_at': row['created_at']})
    table(transfers, 'transfers')
    st.caption('Modification events are attributed to the new category/owner; removals use the previous values. Transfers explain population changes that are not additions or removals. Filters use the event attribution above.')


def record_detail(path, stamp, cfg, snapshots, record_id, default_before=None, default_after=None):
    st.subheader(f'Record · {record_id}')
    timeline = data.record_timeline(path, cfg, record_id)
    with st.expander('Observation history, including unchanged versions'):
        table(timeline, 'record_timeline')
    by_id = {r['id']: r for r in snapshots}
    ids = list(by_id)
    options = [None]+ids
    label = lambda sid: '(Absent baseline)' if sid is None else f"#{sid} · {by_id[sid]['created_at'][:19]} · {by_id[sid]['note'] or 'untitled'}"
    before_col, after_col = st.columns(2)
    with before_col:
        before_id = select_value('Before snapshot', options, label, index=options.index(default_before) if default_before in options else 0,
                                 key=f'before_{record_id}_{default_before}_{default_after}')
    with after_col:
        after_id = select_value('After snapshot', ids, label, index=ids.index(default_after) if default_after in ids else len(ids)-1,
                                key=f'after_{record_id}_{default_before}_{default_after}')
    if before_id is not None and before_id > after_id:
        st.info('Choose a Before snapshot no later than the After snapshot.')
        return
    before, after, diffs, compatible = load_comparison(path, stamp, record_id, before_id, after_id)
    if not compatible:
        st.warning('These snapshots used different comparison settings. Differences may reflect changed rules, not source changes.')
    st.caption('Direct comparison of the stored endpoint versions. Intermediate edits that were reverted are absent here. This view includes schema/empty-value differences and does not recalculate historical scores.')
    downloads = st.columns(2)
    for col, value, name, sid in [(downloads[0], before, 'Before', before_id), (downloads[1], after, 'After', after_id)]:
        if value is not data.ABSENT:
            col.download_button(f'Download {name} JSON', json.dumps(value, ensure_ascii=False, indent=2),
                                f'record_{fingerprint(record_id)}_{sid}.json', 'application/json', key='download_'+name)
        else:
            col.info(f'{name}: record absent.')
    if not diffs:
        st.success('No differences between these stored versions.')
    else:
        st.write(f'{len(diffs):,} changed fields')
        table(diffs, 'endpoint_differences')
        selected = select_value('Inspect changed field', range(len(diffs)), lambda i: diffs[i]['field'])
        row = diffs[selected]
        left, right = st.columns(2)
        left.markdown('**Before**')
        right.markdown('**After**')
        if max(len(row['before']), len(row['after'])) <= 20_000:
            old, new = data.word_diff(row['before'], row['after'])
            left.markdown('<div style="white-space:pre-wrap;overflow-wrap:anywhere">'+old+'</div>', unsafe_allow_html=True)
            right.markdown('<div style="white-space:pre-wrap;overflow-wrap:anywhere">'+new+'</div>', unsafe_allow_html=True)
        else:
            left.code(row['before'][:20_000], language='json')
            right.code(row['after'][:20_000], language='json')
            st.caption('Preview limited to 20,000 characters per value; complete values are in the CSV and JSON downloads.')
    with st.expander('Complete stored versions'):
        a, b = st.columns(2)
        if before is not data.ABSENT:
            a.json(before, expanded=False)
        if after is not data.ABSENT:
            b.json(after, expanded=False)


def explorer(path, stamp, cfg, snapshots, period, events, filters, include_baseline):
    st.header('Explore changes and individual records')
    mode = st.radio('Explore', ['Change events', 'All records at an observation'], horizontal=True)
    search = st.text_input('Search record ID, name or source', key='event_search').casefold()
    cfg_json = json.dumps(cfg, sort_keys=True)
    selected_event = None
    if mode == 'Change events':
        kinds = st.multiselect('Change kinds', ['added', 'removed', 'modified', 'schema only', 'source moved'], default=['added', 'removed', 'modified'])
        minimum = st.slider('Minimum weighted change (%) for modifications', 0, 100, 0)
        rows = [r for r in events if r['kind'] in kinds and (include_baseline or r['previous_id'] is not None)
                and (r['kind'] != 'modified' or 100*(r['weighted_ratio'] or 0) >= minimum)
                and search in (r['record_id']+' '+r['record_name']+' '+(r['filename'] or '')).casefold()]
        rows.sort(key=lambda r: (-r['snapshot_id'], r['record_id']))
        st.caption(f'{len(rows):,} events match. Event classifications and owner values reflect the record at that event.')
        if not rows:
            st.info('No matching events. Try another filter or browse all records.')
            return
        page = st.number_input('Page', min_value=1, max_value=max(1, math.ceil(len(rows)/50)), value=1,
                               key='events_page_'+fingerprint((filters, search, kinds, minimum, period[0]['id'], period[-1]['id'], include_baseline)))
        page_rows = rows[(page-1)*50:page*50]
        shown = [{k: r[k] for k in ['snapshot_id', 'record_id', 'record_name', 'kind', 'system_group', 'system_type', 'model_owner',
                                   'fields_changed', 'weighted_ratio', 'created_at']} for r in page_rows]
        selectable = 'on_select' in inspect.signature(st.dataframe).parameters
        if selectable:
            selection = st.dataframe(shown, use_container_width=True, hide_index=True, on_select='rerun',
                selection_mode='single-row', key='event_table_'+fingerprint(shown))
            selected_rows = selection.selection.rows
            selected_event = page_rows[selected_rows[0]] if selected_rows else None
        else:
            st.dataframe(shown, use_container_width=True, hide_index=True)
        if selected_event is None:
            index = select_value('Record event on this page', range(len(page_rows)),
                lambda i: f"#{page_rows[i]['snapshot_id']} · {page_rows[i]['record_name']} · {page_rows[i]['record_id']} · {page_rows[i]['kind']}",
                key='event_pick_'+fingerprint(shown))
            selected_event = page_rows[index]
        st.download_button('Download filtered events CSV', pd.DataFrame(rows).to_csv(index=False).encode('utf-8-sig'),
                           'filtered_events.csv', 'text/csv')
        rid, previous_id, sid = selected_event['record_id'], selected_event['previous_id'], selected_event['snapshot_id']
    else:
        by_id = {r['id']: r for r in period}
        sid = select_value('Observation', list(by_id),
                           lambda i: f"#{i} · {by_id[i]['created_at'][:19]} · {by_id[i]['note'] or 'untitled'}", index=len(period)-1)
        filter_json = json.dumps(filters, sort_keys=True)
        total, first = load_records(path, stamp, cfg_json, sid, filter_json, search, 0)
        st.caption(f'{total:,} records match, including unchanged records.')
        if not total:
            st.info('No matching records at this observation.')
            return
        page = st.number_input('Page', min_value=1, max_value=max(1, math.ceil(total/50)), value=1,
                               key='records_page_'+fingerprint((filter_json, sid, search)))
        _, page_rows = (total, first) if page == 1 else load_records(path, stamp, cfg_json, sid, filter_json, search, page-1)
        st.dataframe([{k: r[k] for k in ['record_id', 'record_name', 'system_group', 'system_type', 'model_owner']} for r in page_rows],
                     use_container_width=True, hide_index=True)
        index = select_value('Record on this page', range(len(page_rows)),
            lambda i: f"{page_rows[i]['record_name']} · {page_rows[i]['record_id']}", key='record_pick_'+fingerprint(page_rows))
        rid, previous_id = page_rows[index]['record_id'], by_id[sid]['previous_id']
    record_detail(path, stamp, cfg, snapshots, rid, previous_id, sid)


def storage_health(path, snapshots):
    st.header('Is the history complete and compact?')
    stats = data.storage_stats(path)
    a, b, c = st.columns(3)
    a.metric('Database size', f"{stats['database_bytes']/1_000_000:,.1f} MB")
    b.metric('Compressed payloads', f"{stats.get('compressed_bytes',0)/1_000_000:,.1f} MB")
    c.metric('Legacy record copies', f"{stats['legacy_records']:,}")
    raw, compressed = stats.get('unique_raw_bytes', 0), stats.get('compressed_bytes', 0)
    st.write(f"{stats.get('unique_payloads',0):,} unique payloads · {100*(1-compressed/raw) if raw else 0:.1f}% compression saving on unique JSON · {stats['free_bytes']/1_000_000:,.1f} MB reusable free space")
    st.caption('Database size includes indexes, snapshot references, and field values. Payload savings exclude legacy rows. Existing metadata missing owner/name fields is read from stored JSON without modifying history.')
    st.write('Latest observation (UTC):', snapshots[-1]['created_at'])
    gaps = []
    for previous, current in zip(snapshots, snapshots[1:]):
        hours = (pd.Timestamp(current['created_at'])-pd.Timestamp(previous['created_at'])).total_seconds()/3600
        gaps.append({'snapshot': current['id'], 'observed_at': current['created_at'], 'hours_since_previous': round(hours, 2)})
    chart(gaps, 'bar', alt.X('observed_at:T', title='Observation (UTC)'), alt.Y('hours_since_previous:Q', title='Hours since previous observation'))
    st.caption('Gaps show collection cadence, not confirmed outages. Failed collection attempts and historical file-size samples are not stored, so this page does not infer them.')
    table([{k: r[k] for k in ['id', 'created_at', 'note', 'total', 'added', 'removed', 'modified', 'schema_only']} for r in snapshots], 'observations')
    with st.expander('Legacy storage maintenance'):
        st.code('python version_json.py --compact-storage', language='powershell')
        st.write('Run from the terminal with writers stopped and this dashboard closed. The command creates a backup before conversion and compaction.')


def main():
    st.title('How is the database changing?')
    st.caption('See where changes are happening, what changed, and when it happened across system types, groups and model owners.')
    path = Path(st.sidebar.text_input('History database', str(ROOT / 'json_history' / 'history.sqlite3'))).resolve()
    if not path.is_file():
        st.info('Create a snapshot first with version_json.py, or select an existing history database.')
        return
    if st.sidebar.button('Refresh data'):
        st.cache_data.clear()
    snapshots = data.snapshots(path)
    if not snapshots:
        st.info('No observations yet.')
        return
    signature = str(path)
    if st.session_state.get('active_database') != signature:
        reset_filters()
        st.session_state['active_database'] = signature
    if st.session_state.get('view') in LEGACY_VIEWS:
        st.session_state['view'] = LEGACY_VIEWS[st.session_state['view']]
    st.sidebar.radio('View', VIEWS, key='view')
    if st.session_state['view'] == VIEW_HEALTH:
        storage_health(str(path), snapshots)
        return
    by_id = {r['id']: r for r in snapshots}
    ids = list(by_id)
    st.sidebar.subheader('Observation range')
    labels = {f"#{i} · {by_id[i]['created_at'][:10]}": i for i in ids}
    options = list(labels)
    first, last = st.sidebar.select_slider('From / through snapshot', options=options,
                                          value=(options[max(0, len(options)-30)], options[-1]))
    start, end = labels[first], labels[last]
    period = [r for r in snapshots if start <= r['id'] <= end]
    st.sidebar.caption(f"{by_id[start]['created_at']} → {by_id[end]['created_at']} (UTC)")
    st.sidebar.caption(f"From: {by_id[start]['note'] or 'untitled'} · Through: {by_id[end]['note'] or 'untitled'}")
    include_baseline = st.sidebar.checkbox('Include initial baseline as additions', value=False)
    cfg = json.loads(by_id[end]['config_json'])
    local = ROOT / 'config.json'
    if local.exists():
        display = json.loads(local.read_text(encoding='utf-8'))
        for key in ('group_fields', 'type_fields', 'owner_fields', 'name_fields',
                    'content_categories', 'description_edit_thresholds', 'array_keys'):
            if key in display:
                cfg[key] = display[key]
    cfg_json = json.dumps(cfg, sort_keys=True)
    stamp = database_stamp(path)
    with st.spinner('Loading category history…'):
        totals, events = load_period(str(path), stamp, cfg_json, start, end)
    st.sidebar.subheader('Categories')
    filters = select_filters(totals, events)
    st.sidebar.button('Reset filters', on_click=reset_filters)
    totals, events = data.scoped(totals, filters), data.scoped(events, filters)
    active = data.meaningful(events, include_baseline)
    population = sum(r['population'] for r in totals if r['snapshot_id'] == end)
    a, b, c, d = st.columns(4)
    a.metric('Population at end', f'{population:,}')
    b.metric('Unique records affected', f"{len({r['record_id'] for r in active}):,}")
    c.metric('Change events', f'{len(active):,}')
    d.metric('System types affected', f"{len({r['system_type'] for r in active}):,}")
    st.caption(f"{len(period)} observations · population at #{end} · events compared with each observation's predecessor · {'initial baseline included' if include_baseline else 'initial baseline excluded'}")
    with st.expander('How to read these measures'):
        st.write('A change event is one added, removed, or meaningfully modified record at one observation. Repeated changes count as multiple events; unique records count each ID once across the selected period. Schema-only and source-move events are available in the explorer but excluded from these impact metrics.')
        st.write('Group, type, and owner are read from the version at the event; removals use the previous version. Owner/category moves are attributed to the destination. Missing values are shown as (Unspecified). Population is the number of records present at an observation.')
    if st.session_state['view'] == VIEW_OVERVIEW:
        trends(totals, events, period, include_baseline)
    elif st.session_state['view'] == VIEW_CONTENT:
        content_changes_view(str(path), stamp, cfg, snapshots, events, include_baseline)
    elif st.session_state['view'] == VIEW_TIMING:
        timing_scope(str(path), stamp, cfg_json, events, include_baseline)
    else:
        explorer(str(path), stamp, cfg, snapshots, period, events, filters, include_baseline)


try:
    main()
except (sqlite3.Error, ValueError, OSError) as exc:
    st.error(f'Cannot read this history: {exc}')
