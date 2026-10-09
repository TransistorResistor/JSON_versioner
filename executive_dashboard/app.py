"""Run with: python -m streamlit run executive_dashboard/app.py"""
from __future__ import annotations

import html
import json
import sys
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from executive_dashboard import data
from configuration import configuration


st.set_page_config(page_title='Change Brief', page_icon='∆', layout='wide')


@st.cache_data(show_spinner=False, max_entries=4)
def load(path, stamp, cfg_json, start_snapshot, end_snapshot):
    cfg = configuration(json.loads(cfg_json))
    snapshots, totals, events = data.load_history(path, cfg, start_snapshot, end_snapshot)
    return cfg, snapshots, totals, events


@st.cache_data(show_spinner=False, max_entries=4)
def load_snapshots(path, stamp):
    return data.history.snapshots(path)


@st.cache_data(show_spinner=False, max_entries=8)
def load_filter_inventory(path, stamp, cfg_json, start, end):
    return data.filter_inventory(path, configuration(json.loads(cfg_json)), start, end)


@st.cache_data(show_spinner=False, max_entries=16)
def load_sizes(path, stamp, cfg_json, events_json):
    return data.semantic_sizes(path, configuration(json.loads(cfg_json)), json.loads(events_json))


@st.cache_data(show_spinner=False, max_entries=32)
def load_event(path, stamp, cfg_json, snapshot_id, record_id):
    cfg = configuration(json.loads(cfg_json))
    with data.history.connect(path, cfg) as db:
        event = db.execute('SELECT * FROM events WHERE snapshot_id=? AND record_id=?',
                           (snapshot_id, record_id)).fetchone()
    if event is None:
        return None
    events = [dict(event)]
    rows = data.change_rows(events, cfg, semantic_sizes=data.semantic_sizes(path, cfg, events))
    return rows[0] if rows else None


@st.cache_data(show_spinner=False, max_entries=64)
def load_fields(path, stamp, snapshot_id, record_id, cfg_json):
    return data.change_fields(path, snapshot_id, record_id, cfg=configuration(json.loads(cfg_json)))


@st.cache_data(show_spinner=False, max_entries=32)
def load_structure(path, stamp, cfg_json, start_snapshot, end_snapshot, filters_json, start, end):
    return data.structure_changes(path, configuration(json.loads(cfg_json)), start_snapshot, end_snapshot,
                                  json.loads(filters_json), start, end)


@st.cache_data(show_spinner=False, max_entries=64)
def load_record_timeline(path, stamp, record_id):
    return data.record_timeline(path, record_id)


def database_stamp(path: Path):
    result = []
    for item in (path, Path(str(path) + '-wal')):
        stat = item.stat() if item.exists() else None
        result.append((stat.st_mtime_ns, stat.st_size) if stat else None)
    return tuple(result)


def remember_filter(name):
    st.session_state.setdefault('brief_filters', {})[name] = st.session_state['brief_' + name]


def reset_filters():
    st.session_state['brief_filters'] = {}
    for key in list(st.session_state):
        if key.startswith('brief_') and key not in ('brief_filters', 'brief_view'):
            del st.session_state[key]
    st.session_state['activity_feed_page'] = 1


def clear_dimension(name):
    for key in ('brief_categories', 'brief_category_drafts'):
        st.session_state.setdefault(key, {})[name] = {'mode': 'All', 'values': []}
    for key in ('brief_mode_' + name, 'brief_values_' + name):
        st.session_state.pop(key, None)


def remember_category(name):
    mapping = st.session_state.get('brief_category_option_maps', {}).get(name, {})
    previous = st.session_state.get('brief_category_drafts', {}).get(name, {})
    values = st.session_state.get('brief_values_' + name)
    st.session_state.setdefault('brief_category_drafts', {})[name] = {
        'mode': st.session_state.get('brief_mode_' + name, 'All'),
        'values': previous.get('values', []) if values is None else [mapping.get(value, value) for value in values]}


def apply_categories():
    st.session_state['brief_categories'] = json.loads(json.dumps(st.session_state.get('brief_category_drafts', {})))
    st.session_state['activity_feed_page'] = 1


def category_filter(label, name, counts):
    st.session_state.setdefault('brief_categories', {}).setdefault(name, {'mode': 'All', 'values': []})
    drafts = st.session_state.setdefault('brief_category_drafts', {})
    draft = drafts.setdefault(name, {'mode': 'All', 'values': []})
    # Existing sessions may still contain the retired None mode.
    for selection in (draft, st.session_state['brief_categories'][name]):
        if selection.get('mode') == 'None':
            selection.update(mode='All', values=[])
    st.session_state['brief_mode_' + name] = draft['mode']
    mode = st.selectbox(label + ' filter', ['All', 'Include', 'Exclude'],
        key='brief_mode_' + name, on_change=remember_category, args=(name,))
    if mode in ('Include', 'Exclude'):
        # Retain temporarily unavailable values with a zero count, so an upstream
        # filter never silently discards a saved selection or widens an Include.
        options = sorted(set(counts) | set(draft['values']))
        labels = {value: f'{value} ({counts.get(value, 0):,} records)' for value in options}
        st.session_state.setdefault('brief_category_option_maps', {})[name] = {text: value for value, text in labels.items()}
        st.session_state['brief_values_' + name] = [labels[value] for value in draft['values']]
        st.multiselect(label, list(labels.values()), key='brief_values_' + name,
            placeholder=f'Type to search {label.lower()}...',
            help='Click inside the field and type part of a name to filter the suggestions. '
                 'Select a match, then type again to select more. An empty selection means All.',
            on_change=remember_category, args=(name,))
    else:
        st.caption(f'All {len(counts):,} available {label.lower()}.')
    return data.category_values(counts, {'mode': mode, 'values': draft['values']})


def inventory_view():
    st.session_state['brief_view'] = 'Current inventory'
    st.query_params.clear()


def open_change(row):
    st.query_params['record'] = row['record_id']
    st.query_params['snapshot'] = str(row['snapshot_id'])


def back_to_changes():
    st.query_params.clear()


def size_badge(row):
    explanation = html.escape(row.get('size_explanation', ''), quote=True)
    return (f'<span class="size-badge {row["size"].lower()}" title="{explanation}">'
            f'{html.escape(row["size"])}</span>')


def capture_status(snapshots):
    latest = snapshots[-1]
    a, b, c, d = st.columns([2, 1, 1, 1])
    stamp = pd.to_datetime(latest['created_at'], utc=True)
    a.metric('Last captured (UTC)', stamp.strftime('%d %b %Y'))
    a.caption(stamp.strftime('%H:%M:%S UTC'))
    b.metric('Records captured', f"{latest['total'] or 0:,}")
    c.metric('Unavailable records', f"{latest.get('unavailable', 0):,}")
    predecessor = latest.get('previous_id')
    d.metric('Compared with', f'#{predecessor}' if predecessor else 'Baseline')
    st.caption(f"Latest observation #{latest['id']} · {latest.get('note') or 'No capture note'}. "
               'Returning records compare with their last seen version across capture gaps.')
    if latest.get('unavailable'):
        st.warning('The latest capture has unavailable records. These are capture gaps; they are not counted as removals.')


def change_summary(rows):
    counts = pd.Series([row['size'] for row in rows], dtype='object').value_counts()
    a, b, c, d = st.columns(4)
    a.metric('Records affected', f"{len({row['record_id'] for row in rows}):,}")
    b.metric('Record updates', f'{len(rows):,}', help='Repeated changes to one record count as separate updates.')
    c.metric('Large updates', int(counts.get('Large', 0)), help='Hover over a size badge to see its scoring explanation.')
    d.metric('Added / removed', f"{counts.get('Added', 0)} / {counts.get('Removed', 0)}")
    st.caption(f"{counts.get('Large', 0)} large · {counts.get('Medium', 0)} medium · "
               f"{counts.get('Small', 0)} small modifications in the current selection.")
    summary = data.system_summary(rows)
    if summary:
        st.caption('Most affected: ' + '; '.join(
            f"{row['system_group']} / {row['system_type']} ({row['record_updates']} updates)"
            for row in summary[:3]))


def empty_changes(snapshots, rows, filtered):
    if filtered:
        return
    if len(snapshots) == 1:
        st.info('Only the initial baseline has been captured. Capture another observation to see changes.')
    elif not rows:
        st.info('No record edits, additions, or removals were observed in this observation range. '
                'Structure-only changes are available in Structure changes.')
    else:
        st.info('No changes match your filters. Clear filters or choose a wider date window.')
        st.button('Clear filters', on_click=reset_filters)
    st.button('View captured inventory', on_click=inventory_view)


def change_navigation(rows, selected):
    ordered = data.sort_changes(rows, 'Most recent first')
    keys = [(row['snapshot_id'], row['record_id']) for row in ordered]
    key = selected['snapshot_id'], selected['record_id']
    if key not in keys:
        st.caption('This linked change is outside the current filters.')
        return
    index = keys.index(key)
    previous, position, following = st.columns([1, 2, 1])
    previous.button('← Previous change', disabled=index == 0,
                    on_click=open_change, args=(ordered[max(0, index - 1)],))
    position.caption(f'Change {index + 1} of {len(ordered)} · most recent first')
    following.button('Next change →', disabled=index + 1 == len(ordered),
                     on_click=open_change, args=(ordered[min(len(ordered) - 1, index + 1)],))


def current_inventory(path, cfg, snapshot_id, filters):
    st.header('Current inventory')
    st.caption(f'Inventory at the end of your observation range: #{snapshot_id}, including unchanged records. '
               'Date filters apply to change views.')
    search = st.text_input('Search inventory', placeholder='Record name, ID or source')
    count, first_rows = data.history.record_page(path, cfg, snapshot_id, filters, search, 0)
    if not count:
        st.info('No captured records match these category filters and search.')
        return
    pages = max(1, (count + 49) // 50)
    page_labels = [f'Page {value} of {pages}' for value in range(1, pages + 1)]
    page = page_labels.index(st.selectbox('Inventory page', page_labels)) + 1
    rows = first_rows if page == 1 else data.history.record_page(path, cfg, snapshot_id, filters, search, page - 1)[1]
    st.dataframe(pd.DataFrame(rows)[['record_name', 'record_id', 'system_group', 'system_type', 'model_owner']],
                 use_container_width=True, hide_index=True)
    st.caption(f'{count:,} matching records · 50 records per page')
    selected = st.selectbox('Open record', [row['record_id'] for row in rows])
    with st.expander('Complete stored record'):
        render_complete_record(data.history.record_version(path, snapshot_id, selected))


def display_value(value):
    if value is None:
        return '(not present)'
    try:
        decoded = json.loads(value)
        return json.dumps(decoded, ensure_ascii=False, indent=2) if isinstance(decoded, (dict, list)) else str(decoded)
    except (json.JSONDecodeError, TypeError):
        return str(value)


def scalar_value(value):
    if value is None:
        return '—'
    try:
        decoded = json.loads(value)
        return str(decoded) if not isinstance(decoded, (dict, list)) else json.dumps(decoded, ensure_ascii=False)
    except (json.JSONDecodeError, TypeError):
        return str(value)


def value_with_uom(value, uom=None):
    """Present a parameter value and its unit as one natural value."""
    text = scalar_value(value)
    unit = scalar_value(uom) if uom is not None else ''
    return f'{text} {unit}'.strip() if unit and unit != '—' else text


def parameter_item(record, component, parameter):
    if record is data.history.ABSENT or not isinstance(record, dict):
        return {}
    for item in record.get('parametrics', []):
        item_component = item.get('component')
        if item_component in (None, ''):
            item_component = None
        if item.get('parameter') == parameter and item_component == component:
            return item
    return {}


def item_value(item):
    if not item:
        return '—'
    if item.get('parameterUomValue') not in (None, ''):
        return str(item['parameterUomValue'])
    return value_with_uom(item.get('parameterValue'), item.get('uom'))


def item_subtitle(item):
    for key, value in item.items():
        if 'subtitle' in key.casefold() and value not in (None, ''):
            return str(value)
    return ''


def install_detail_styles():
    st.markdown('''<style>
        :root{--cb-accent:#183f6b;--cb-muted:#66717d;--cb-rule:rgba(73,88,105,.22);--cb-soft:rgba(73,88,105,.055)}
        [data-testid="stAppViewContainer"]>.main{background:var(--background-color)}
        [data-testid="stHeader"]{background:transparent}
        [data-testid="stDeployButton"],.stDeployButton{display:none!important}
        .block-container{max-width:1180px;padding-top:2.1rem;padding-bottom:4rem}
        [data-testid="stSidebar"]{background:var(--secondary-background-color);border-right:1px solid var(--cb-rule)}
        [data-testid="stSidebar"] [data-testid="stVerticalBlock"]{gap:.65rem}
        h1{font-size:2rem!important;letter-spacing:-.035em;font-weight:680!important;margin-bottom:.2rem!important}
        h2{font-size:1.42rem!important;letter-spacing:-.02em;font-weight:650!important;margin-top:1.8rem!important}
        h3{font-size:.78rem!important;letter-spacing:.075em;text-transform:uppercase;color:var(--cb-muted);margin-top:2rem!important}
        h4{font-size:1.03rem!important;letter-spacing:-.01em;margin-top:1.65rem!important}
        [data-testid="stMetric"]{border-top:2px solid var(--cb-rule);padding-top:.65rem}
        [data-testid="stMetricValue"]{font-size:1.55rem}
        div[role="radiogroup"]{gap:.2rem}.stRadio label{font-size:.9rem}
        .change-summary{display:flex;gap:0;flex-wrap:wrap;border-top:1px solid var(--cb-rule);border-bottom:1px solid var(--cb-rule);margin:.9rem 0 1.2rem;padding:.65rem 0}
        .change-pill{font-size:.82rem;color:var(--cb-muted);padding:.05rem .8rem .05rem 0;margin-right:.8rem;border-right:1px solid var(--cb-rule)}
        .change-pill:last-child{border-right:0}
        .redline-legend{font-size:.8rem;color:var(--cb-muted);margin:-.2rem 0 .8rem}
        .redline,.field-change{border-left:3px solid var(--cb-rule);padding:.4rem 0 .4rem 1rem;margin:.45rem 0 1.35rem;line-height:1.7}
        .record-prose{border-left:3px solid var(--cb-rule);padding:.25rem 0 .25rem 1rem;margin:.35rem 0 1.1rem;line-height:1.7;max-width:80ch}
        del{background:#f6dedb;color:#81342e;text-decoration-thickness:1.5px;padding:1px 2px}
        ins{background:#dfe8f3;color:#173f6b;text-decoration:none;border-bottom:1.5px solid #315f91;padding:1px 2px}
        .prose-version{border-top:1px solid var(--cb-rule);padding:.7rem .2rem;line-height:1.7;min-height:100%}
        .prose-label{font-size:.7rem;text-transform:uppercase;letter-spacing:.08em;color:var(--cb-muted);margin-bottom:.45rem}
        .change-table,.brief-table{width:100%;border-collapse:collapse;border-top:1px solid var(--cb-rule);margin-bottom:1rem;font-size:.89rem}
        .change-table th,.brief-table th{text-align:left;font-size:.69rem;text-transform:uppercase;letter-spacing:.065em;color:var(--cb-muted);font-weight:650}
        .change-table th,.change-table td,.brief-table th,.brief-table td{padding:.72rem .65rem;border-bottom:1px solid var(--cb-rule);vertical-align:top}
        .change-table tbody tr:hover,.brief-table tbody tr:hover{background:var(--cb-soft)}
        .change-table .context{color:var(--cb-muted);font-size:.79rem;margin-top:.15rem}
        .feed-date{font-size:.72rem;text-transform:uppercase;letter-spacing:.085em;color:var(--cb-muted);font-weight:680;border-bottom:1px solid var(--cb-rule);padding-bottom:.48rem;margin:2rem 0 0}
        .feed-range{font-size:.79rem;color:var(--cb-muted);text-align:right;margin:.3rem 0 .2rem}
        .feed-card{padding:1rem .15rem 1.05rem;border-bottom:1px solid var(--cb-rule)}
        .feed-top{display:flex;align-items:baseline;gap:.7rem}.record-link{font-size:1.03rem;font-weight:680;color:var(--text-color)!important;text-decoration:none}.record-link:hover{color:var(--cb-accent)!important;text-decoration:underline;text-underline-offset:3px}
        .feed-copy{margin:.35rem 0 .22rem;color:var(--text-color)}.feed-meta,.table-note{font-size:.79rem;color:var(--cb-muted)}
        .size-badge{display:inline-block;min-width:4.1rem;font-size:.68rem;font-weight:720;text-transform:uppercase;letter-spacing:.07em;color:var(--cb-accent)}
        .size-badge.large,.size-badge.added,.size-badge.removed{color:#963f37}.size-badge.medium{color:#94631f}
        .page-position{text-align:center;color:var(--cb-muted);font-size:.8rem;padding-top:.55rem}
        [data-testid="stButton"] button{border-radius:.25rem;border-color:var(--cb-rule);font-weight:600}
        div[data-testid="stExpander"]{border-color:var(--cb-rule);border-radius:.35rem}
        [data-testid="stDataFrame"]{border:1px solid var(--cb-rule);border-radius:.35rem;overflow:hidden}
        @media(max-width:760px){.block-container{padding:1.2rem 1rem}.feed-meta{line-height:1.55}.change-table,.brief-table{display:block;overflow-x:auto}.change-pill{width:50%;border:0;margin:0;padding:.18rem 0}}
    </style>''', unsafe_allow_html=True)


def parameter_edit_table(rows):
    body = []
    for row in rows:
        value_change = data.inline_word_diff(row['Before'], row['After'])
        comment_change = data.inline_word_diff(row['Before comment'], row['After comment'])
        body.append(f'<tr><td><strong>{html.escape(row["Parameter"])}</strong>'
                    f'<div class="context">{html.escape(row["Subtitle"])}</div></td>'
                    f'<td>{html.escape(row["Component"])}</td><td>{value_change}</td><td>{comment_change}</td></tr>')
    return ('<table class="change-table"><thead><tr><th>Parameter</th><th>Component</th>'
            '<th>Value change</th><th>Comment change</th></tr></thead><tbody>' + ''.join(body) + '</tbody></table>')


def render_complete_record(record):
    if record is data.history.ABSENT or not isinstance(record, dict):
        st.info('No record is stored for this version.')
        return
    reserved = {'descriptions', 'parametrics', 'relations', 'proliferations', 'media', 'aliases', 'codes'}
    identity = [{'Field': data._words(key), 'Value': display_value(json.dumps(value, ensure_ascii=False))}
                for key, value in record.items() if key not in reserved]
    if identity:
        st.markdown('##### Record details')
        st.table(pd.DataFrame(identity).style.hide(axis='index'))
    descriptions = record.get('descriptions') or []
    if descriptions:
        st.markdown('##### Descriptions')
        for description in descriptions:
            label = description.get('descrType') or 'Description'
            st.markdown(f'**{label}**')
            for key in ('shortDescription', 'description'):
                if description.get(key):
                    st.markdown(f'<div class="record-prose">{html.escape(str(description[key]))}</div>',
                                unsafe_allow_html=True)
    parameters = []
    for item in record.get('parametrics') or []:
        parameters.append({'Component': item.get('component') or '—', 'Parameter': item.get('parameter') or '—',
                           'Subtitle': item_subtitle(item) or '—', 'Value': item_value(item),
                           'Comments': item.get('comments') or '—'})
    if parameters:
        st.markdown('##### Complete parameter set')
        st.table(pd.DataFrame(parameters).style.hide(axis='index'))
    for key in ('aliases', 'codes', 'relations', 'proliferations', 'media'):
        values = record.get(key) or []
        if values:
            st.markdown(f'##### {data._words(key)}')
            frame = pd.DataFrame(values if isinstance(values, list) else [values])
            st.table(frame.style.hide(axis='index'))


def render_record_preview(path, stamp, selected):
    timeline = load_record_timeline(str(path), stamp, selected['record_id'])
    if not timeline:
        st.info('No stored versions are available for this record.')
        return
    labels = {}
    for item in timeline:
        date = pd.to_datetime(item['created_at'], utc=True).strftime('%d %b %Y').lstrip('0')
        note = item.get('note') or 'Snapshot'
        labels[f"#{item['snapshot_id']} · {date} · {note}"] = item
    current_label = next((label for label, item in labels.items()
                          if item['snapshot_id'] == selected['snapshot_id']), next(iter(labels)))
    choice = st.selectbox('Version', list(labels), index=list(labels).index(current_label),
                          key=f"record-preview-{selected['record_id']}-{selected['snapshot_id']}")
    version = labels[choice]
    st.caption(f"Stored as {version['filename']} · snapshot {version['snapshot_id']}")
    record = data.history.record_version(path, version['snapshot_id'], selected['record_id'])
    render_complete_record(record)


def render_activity_feed(rows, path, stamp, cfg_json):
    st.header('Activity feed')
    st.caption('A chronological view of substantive record updates. Select a record name for the full change review.')
    with st.expander('Search and display', expanded=False):
        search = st.text_input('Search activity', placeholder='Search records, systems, owners or changes')
        page_size = st.selectbox('Results per page', [10, 20, 30, 50, 100], index=2)
    query = search.strip().casefold()
    searched = rows
    if query:
        searched = [row for row in rows if query in ' '.join(str(row.get(field) or '') for field in (
            'record_name', 'record_id', 'system_group', 'system_type', 'model_owner', 'description', 'kind'
        )).casefold()]
    ordered = data.sort_changes(searched, 'Most recent first')
    page_count = max(1, (len(ordered) + page_size - 1) // page_size)
    page_key = 'activity_feed_page'
    settings_key = 'activity_feed_settings'
    settings = (query, page_size)
    if st.session_state.get(settings_key) != settings:
        st.session_state[page_key] = 1
        st.session_state[settings_key] = settings
    if page_key not in st.session_state:
        st.session_state[page_key] = 1
    st.session_state[page_key] = min(max(1, st.session_state[page_key]), page_count)
    page_number = st.session_state[page_key]
    if not ordered:
        st.info('No activity matches this search and the current filters.')
        return
    shown = ordered[(page_number - 1) * page_size:page_number * page_size]
    first_result = (page_number - 1) * page_size + 1 if ordered else 0
    last_result = min(page_number * page_size, len(ordered))
    st.markdown(f'<div class="feed-range">Showing {first_result}–{last_result} of {len(ordered):,}</div>',
                unsafe_allow_html=True)
    previous_day = None
    for row in shown:
        day = row['estimated_day'].date()
        if day != previous_day:
            date_label = (row['estimated_day'].strftime('%A, %-d %B %Y') if sys.platform != 'win32'
                          else row['estimated_day'].strftime('%A, %d %B %Y').replace(' 0', ' '))
            st.markdown(f'<div class="feed-date">{date_label}</div>', unsafe_allow_html=True)
            previous_day = day
        fields = data.display_fields(load_fields(str(path), stamp, row['snapshot_id'], row['record_id'], cfg_json))
        parameter_count = len({(field.get('component'), field.get('parameter')) for field in fields if field['is_parameter']})
        description_count = sum(field['technical_path'].startswith('$.descriptions[') for field in fields)
        other_count = sum(not field['is_parameter'] and not field['technical_path'].startswith('$.descriptions[') for field in fields)
        parts = []
        if parameter_count:
            parts.append(f'{parameter_count} parameter{"s" if parameter_count != 1 else ""}')
        if description_count:
            parts.append(f'{description_count} description{"s" if description_count != 1 else ""}')
        if other_count:
            parts.append(f'{other_count} other field{"s" if other_count != 1 else ""}')
        summary = f'{", ".join(parts)} edited' if parts else row['description']
        with st.container(border=True):
            record_column, size_column = st.columns([5, 1])
            record_column.button(str(row['record_name'] or row['record_id']),
                key=f"feed-open-{row['snapshot_id']}-{row['record_id']}",
                on_click=open_change, args=(row,), help=row['size_explanation'])
            size_column.markdown(size_badge(row), unsafe_allow_html=True)
            st.markdown(f'''<div class="feed-copy">{html.escape(summary)}</div>
                <div class="feed-meta">{html.escape(str(row['system_group']))} · {html.escape(str(row['system_type']))} · {html.escape(str(row['model_owner']))} · {html.escape(str(row.get('timing_basis') or 'observation fallback'))}</div>''',
                unsafe_allow_html=True)
    previous, position, following = st.columns([1, 3, 1])
    if previous.button('← Previous', disabled=page_number == 1, use_container_width=True):
        st.session_state[page_key] -= 1
        st.rerun()
    position.markdown(f'<div class="page-position">Page {page_number} of {page_count}</div>', unsafe_allow_html=True)
    if following.button('Next →', disabled=page_number >= page_count, use_container_width=True):
        st.session_state[page_key] += 1
        st.rerun()


def render_brief_table(rows):
    page_size = 50
    page_count = max(1, (len(rows) + page_size - 1) // page_size)
    page_labels = [f'Page {value} of {page_count}' for value in range(1, page_count + 1)]
    page_number = page_labels.index(st.selectbox('Results page', page_labels)) + 1
    shown = rows[(page_number - 1) * page_size:page_number * page_size]
    body = []
    for row in shown:
        date = row['estimated_day'].strftime('%d %b %Y').lstrip('0')
        body.append(f'<tr><td>{size_badge(row)}</td>'
                    f'<td>{html.escape(str(row["record_name"] or row["record_id"]))}'
                    f'<div class="table-note">{html.escape(row["description"])}</div></td><td>{html.escape(str(row["system_type"]))}</td>'
                    f'<td>{html.escape(str(row["model_owner"]))}</td><td>{date}</td></tr>')
    st.markdown('<table class="brief-table"><thead><tr><th>Size</th><th>Record and change</th><th>System type</th><th>Owner</th><th>Estimated date</th></tr></thead><tbody>'
                + ''.join(body) + '</tbody></table>', unsafe_allow_html=True)
    st.caption(f'Showing {len(shown)} of {len(rows):,} matching updates.')
    labels = [f"{row['record_name']} · {row['record_id']} · {row['estimated_day'].strftime('%d %b %Y')} · #{row['snapshot_id']}"
              for row in shown]
    selected_index = labels.index(st.selectbox('Review a change', labels))
    st.button('Open selected change', on_click=open_change, args=(shown[selected_index],))


def change_detail(path, stamp, selected, cfg_json):
    install_detail_styles()
    st.subheader(selected['title'])
    day_label = selected['estimated_day'].strftime('%d %b %Y').lstrip('0')
    st.caption(f"{selected['system_group']} · {selected['system_type']} · {selected['model_owner']} · "
               f"estimated {day_label} · {selected.get('timing_basis') or 'observation fallback'}")
    st.caption(selected.get('size_explanation', ''))
    before_record, after_record = data.record_versions(path, selected)
    fields = data.display_fields(load_fields(str(path), stamp, selected['snapshot_id'], selected['record_id'], cfg_json))
    if fields:
        parameters = [row for row in fields if row['is_parameter']]
        other = [row for row in fields if not row['is_parameter'] or
                 (row['detail'] not in ('Value', 'Unit', 'Comment') and 'subtitle' not in row['detail'].casefold())]
        descriptions = [row for row in other if row['technical_path'].startswith('$.descriptions[')]
        remaining = [row for row in other if row not in descriptions]
        parameter_count = len({(row.get('component'), row.get('parameter')) for row in parameters})
        st.markdown(f'''<div class="change-summary"><span class="change-pill">{selected['size']} change</span>
            <span class="change-pill">{parameter_count} parameters</span>
            <span class="change-pill">{len(descriptions)} description fields</span>
            <span class="change-pill">{len(remaining)} other fields</span></div>''', unsafe_allow_html=True)
        detail_view = st.radio('Change display', ['Edits', 'Before & after'], horizontal=True,
                               key=f"detail-view-{selected['snapshot_id']}-{selected['record_id']}")
        if detail_view == 'Edits':
            st.markdown('<div class="redline-legend"><del>Removed</del>&nbsp;&nbsp; <ins>Added</ins></div>',
                        unsafe_allow_html=True)
        if parameters:
            st.markdown('#### Parameter changes')
            parameter_rows = []
            groups = {}
            for row in parameters:
                groups.setdefault((row.get('component'), row['parameter']), {})[row['detail']] = row
            for (component, parameter), parts in groups.items():
                before_item = parameter_item(before_record, component, parameter)
                after_item = parameter_item(after_record, component, parameter)
                before_subtitle, after_subtitle = item_subtitle(before_item), item_subtitle(after_item)
                subtitle = after_subtitle or before_subtitle or '—'
                if before_subtitle and after_subtitle and before_subtitle != after_subtitle:
                    subtitle = f'{before_subtitle} → {after_subtitle}'
                parameter_rows.append({'Component': component or '—', 'Parameter': parameter,
                                       'Subtitle': subtitle,
                                       'Before': item_value(before_item), 'After': item_value(after_item),
                                       'Before comment': str(before_item.get('comments') or '—'),
                                       'After comment': str(after_item.get('comments') or '—')})
            if detail_view == 'Edits':
                st.markdown(parameter_edit_table(parameter_rows), unsafe_allow_html=True)
            else:
                st.table(pd.DataFrame(parameter_rows).style.hide(axis='index'))
        if other:
            if descriptions:
                st.markdown('#### Description changes')
                for row in descriptions:
                    before_text, after_text = scalar_value(row['old_value']), scalar_value(row['new_value'])
                    st.markdown(f"**{row['label']}**")
                    if detail_view == 'Edits':
                        redline = data.inline_word_diff(before_text, after_text)
                        st.markdown(f'<div class="redline">{redline}</div>', unsafe_allow_html=True)
                    else:
                        before_html = data.inline_word_diff(before_text, before_text)
                        after_html = data.inline_word_diff(after_text, after_text)
                        left, right = st.columns(2)
                        left.markdown(f'<div class="prose-version"><div class="prose-label">Before</div>{before_html}</div>',
                                      unsafe_allow_html=True)
                        right.markdown(f'<div class="prose-version"><div class="prose-label">After</div>{after_html}</div>',
                                       unsafe_allow_html=True)
            if remaining:
                st.markdown('#### Other field changes')
                if detail_view == 'Edits':
                    for row in remaining:
                        changed = data.inline_word_diff(scalar_value(row['old_value']), scalar_value(row['new_value']))
                        st.markdown(f'**{row["label"]}**<div class="field-change">{changed}</div>',
                                    unsafe_allow_html=True)
                else:
                    table = pd.DataFrame([{'Field': row['label'], 'Before': display_value(row['old_value']),
                                           'After': display_value(row['new_value'])} for row in remaining])
                    st.table(table.style.hide(axis='index'))
        with st.expander('Technical field paths'):
            st.dataframe(pd.DataFrame([{'Field': row['label'], 'JSON path': row['technical_path']}
                                       for row in fields]), use_container_width=True, hide_index=True)
    else:
        st.info('No individual edits are visible with the current display settings. Open the stored records below for the complete content.'
                if selected['kind'] == 'modified' else
                'This is a whole-record addition or removal. Open the stored records below for the complete content.')
    with st.expander('Preview full record at a point in time'):
        render_record_preview(path, stamp, selected)
    with st.expander('Raw record versions'):
        left, right = st.columns(2)
        left.markdown('**Before**')
        left.json(None if before_record is data.history.ABSENT else before_record, expanded=False)
        right.markdown('**After**')
        right.json(None if after_record is data.history.ABSENT else after_record, expanded=False)


def main():
    install_detail_styles()
    st.title('Change Brief')
    st.sidebar.markdown('### Explore')
    page = st.sidebar.radio('View', ['Activity feed', 'Change brief', 'Changes over time',
                                     'Changes by system type', 'Structure changes', 'Current inventory'],
                            label_visibility='collapsed', key='brief_view')
    filter_panel = st.sidebar.container()
    large_demo = '--large-demo' in sys.argv
    synthetic_demo = '--synthetic-demo' in sys.argv
    demo = '--demo' in sys.argv or large_demo or synthetic_demo
    demo_name = 'generated_synthetic' if synthetic_demo else 'generated_large' if large_demo else 'generated'
    demo_root = ROOT / 'executive_dashboard' / 'demo' / demo_name
    default_database = demo_root / 'history' / 'history.sqlite3' if demo else ROOT / 'json_history' / 'history.sqlite3'
    default_config = demo_root / 'demo_config.json' if demo else ROOT / 'config.json'
    with st.sidebar.expander('Data source'):
        database_text = st.text_input('History database', str(default_database))
        config_text = st.text_input('Configuration', str(default_config))
    path, config_path = Path(database_text), Path(config_text)
    if not path.is_file() or not config_path.is_file():
        st.info('Select an existing history database and configuration file.')
        return
    signature = (str(path.resolve()), str(config_path.resolve()))
    if st.session_state.get('active_brief_source') != signature:
        reset_filters()
        if st.session_state.get('active_brief_source') is not None:
            st.query_params.clear()
        st.session_state['active_brief_source'] = signature
    if st.sidebar.button('Refresh data'):
        st.cache_data.clear()
    stamp = database_stamp(path)
    cfg_json = config_path.read_text(encoding='utf-8')
    snapshots = load_snapshots(str(path), stamp)
    if not snapshots:
        st.info('No history snapshots are available.')
        return

    with st.expander('Capture details', expanded=False):
        capture_status(snapshots)
    ids = [item['id'] for item in snapshots]
    labels = {item['id']: f"#{item['id']} · {item['created_at'][:10]} · {item.get('note') or 'Snapshot'}"
              for item in snapshots}
    with filter_panel:
        st.markdown('### Observation range')
        if len(ids) == 1:
            first = last = ids[0]
            st.caption(labels[first])
        else:
            options = list(labels.values())
            default_range = (options[max(0, len(options) - 30)], options[-1])
            saved_range = st.session_state['brief_filters'].get('range', default_range)
            if not all(label in options for label in saved_range):
                saved_range = default_range
            st.session_state['brief_range'] = tuple(saved_range)
            first_label, last_label = st.select_slider('From / through snapshot', options=options,
                key='brief_range', on_change=remember_filter, args=('range',))
            selected_ids = {label: sid for sid, label in labels.items()}
            first, last = selected_ids[first_label], selected_ids[last_label]
    cfg, snapshots, totals, events = load(str(path), stamp, cfg_json, first, last)
    rows = data.change_rows(events, cfg)

    with filter_panel:
        option_rows = load_filter_inventory(str(path), stamp, cfg_json, first, last) + events
        groups = sorted(data.category_counts(option_rows, 'system_group'))
        with st.expander('Category filters', expanded=False):
            st.caption('Search to include or exclude a few values. Counts are unique records across the observation range.')
            draft_groups = category_filter('System groups', 'groups', data.category_counts(option_rows, 'system_group'))
            draft_group_rows = [row for row in option_rows if row['system_group'] in draft_groups]
            draft_types = category_filter('System types', 'types', data.category_counts(draft_group_rows, 'system_type'))
            draft_type_rows = [row for row in draft_group_rows if row['system_type'] in draft_types]
            category_filter('Owners', 'owners', data.category_counts(draft_type_rows, 'model_owner'))
            st.button('Apply filters', on_click=apply_categories, use_container_width=True)
            if st.session_state.get('brief_category_drafts', {}) != st.session_state.get('brief_categories', {}):
                st.caption('Pending selections. Apply filters to update the results.')
        applied = st.session_state.get('brief_categories', {})
        selected_groups = data.category_values(groups, applied.get('groups', {}))
        group_rows = [row for row in option_rows if row['system_group'] in selected_groups]
        types = sorted(data.category_counts(group_rows, 'system_type'))
        selected_types = data.category_values(types, applied.get('types', {}))
        type_rows = [row for row in group_rows if row['system_type'] in selected_types]
        owners = sorted(data.category_counts(type_rows, 'model_owner'))
        selected_owners = data.category_values(owners, applied.get('owners', {}))
        dates = [data.estimated_day(row).date() for row in events]
        observation_dates = [pd.to_datetime(item['created_at'], utc=True).date() for item in snapshots
                             if first <= item['id'] <= last]
        dates += observation_dates
        min_date, max_date = min(dates), max(dates)
        windows = ['All selected dates', 'Last capture', 'Last 7 days', 'Last 30 days', 'Custom dates']
        saved = st.session_state['brief_filters']
        st.session_state['brief_window'] = saved.get('window', windows[0])
        window = st.selectbox('Time window', windows, key='brief_window',
                              on_change=remember_filter, args=('window',))
        start, end = min_date, max_date
        if window in ('Last 7 days', 'Last 30 days'):
            days = 7 if window == 'Last 7 days' else 30
            start = max(min_date, max_date - pd.Timedelta(days=days - 1))
            st.caption('Date shortcuts end at the last selected capture (UTC).')
        date_values = {}
        for name, value in (('start', start), ('end', end)):
            chosen = saved.get(name, value) if window == 'Custom dates' else value
            date_values[name] = min(max(chosen, min_date), max_date)
        start = st.date_input('Changed from', value=date_values['start'], min_value=min_date, max_value=max_date,
            key='brief_start', on_change=remember_filter, args=('start',), disabled=window != 'Custom dates')
        end = st.date_input('Changed through', value=date_values['end'], min_value=min_date, max_value=max_date,
            key='brief_end', on_change=remember_filter, args=('end',), disabled=window != 'Custom dates')
        st.button('Reset filters', on_click=reset_filters, use_container_width=True)
    filters = {'system_group': selected_groups, 'system_type': selected_types, 'model_owner': selected_owners}
    if start > end:
        st.warning('Choose a start date no later than the end date.')
        return
    chips = st.columns(3)
    for column, name, label in zip(chips, ('groups', 'types', 'owners'), ('Groups', 'Types', 'Owners')):
        selection = applied.get(name, {})
        mode, values = selection.get('mode', 'All'), selection.get('values', [])
        if mode in ('Include', 'Exclude') and values:
            text = f'{label}: {len(values)} ' + ('included' if mode == 'Include' else 'excluded')
        else:
            text = f'{label}: All'
        if text.endswith(': All'):
            column.caption(text)
        else:
            column.button(text + ' [clear]', key='clear_' + name,
                          on_click=clear_dimension, args=(name,))
    capture_rows = [row for row in rows if row['snapshot_id'] == last] if window == 'Last capture' else rows
    filtered = data.filter_changes(capture_rows, selected_groups, selected_types, selected_owners, start, end)
    if filtered:
        selected_keys = {(row['snapshot_id'], row['record_id']) for row in filtered}
        selected_events = [row for row in events if (row['snapshot_id'], row['record_id']) in selected_keys]
        sizes = load_sizes(str(path), stamp, cfg_json, json.dumps(selected_events))
        filtered = data.change_rows(selected_events, cfg, semantic_sizes=sizes)

    requested_record = st.query_params.get('record')
    requested_snapshot = st.query_params.get('snapshot')
    if requested_record and requested_snapshot:
        try:
            selected = load_event(str(path), stamp, cfg_json, int(requested_snapshot), requested_record)
        except ValueError:
            selected = None
        if selected:
            st.button('Back to changes', on_click=back_to_changes)
            change_navigation(filtered, selected)
            change_detail(path, stamp, selected, cfg_json)
            return
        st.warning('That change is no longer available in the selected history database.')

    if page == 'Current inventory':
        current_inventory(path, cfg, last, filters)
        return
    if page != 'Structure changes':
        with st.expander('Change summary', expanded=False):
            change_summary(filtered)
        if not filtered:
            empty_changes(snapshots, capture_rows, filtered)
            return
    if page == 'Activity feed':
        render_activity_feed(filtered, path, stamp, cfg_json)
    elif page == 'Change brief':
        st.header('Largest changes')
        st.caption(f'{len(filtered):,} record updates across {len({row["record_id"] for row in filtered}):,} records. '
                   'Structure-only changes are excluded.')
        order = st.selectbox('Sort', ['Largest first', 'Most recent first', 'Smallest first'])
        ordered = data.sort_changes(filtered, order)
        if ordered:
            render_brief_table(ordered)
        else:
            st.info('No changes match these filters.')
    elif page == 'Changes over time':
        st.header('Changes over time')
        summary = data.time_summary(filtered)
        if summary:
            st.altair_chart(alt.Chart(alt.Data(values=summary)).mark_bar().encode(
                x=alt.X('date:T', title='Best estimated change date'), y=alt.Y('updates:Q', title='Record updates'),
                color=alt.Color('size:N', title='Change size'), tooltip=['date:T', 'size:N', 'updates:Q']
            ).properties(height=380), use_container_width=True)
        else:
            st.info('No changes match these filters.')
    elif page == 'Changes by system type':
        st.header('Changes by system type')
        st.caption('System group and system type are shown together so similarly named types remain in context.')
        summary = data.system_summary(filtered)
        st.dataframe(pd.DataFrame(summary), use_container_width=True, hide_index=True)
        if summary:
            st.altair_chart(alt.Chart(alt.Data(values=summary)).mark_bar().encode(
                y=alt.Y('system_type:N', title='System type', sort='-x'),
                x=alt.X('record_updates:Q', title='Record updates'),
                color=alt.Color('system_group:N', title='System group'),
                tooltip=['system_group:N', 'system_type:N', 'record_updates:Q', 'unique_records:Q']
            ).properties(height=max(320, len(summary) * 30)), use_container_width=True)
    else:
        st.header('Structure changes')
        st.caption('Field additions, removals, and shape changes are kept separate from record edits and size rankings.')
        filters = {'system_group': selected_groups, 'system_type': selected_types, 'model_owner': selected_owners}
        structure = load_structure(str(path), stamp, cfg_json, last if window == 'Last capture' else first,
                                   last, json.dumps(filters), start, end)
        st.dataframe(pd.DataFrame(structure), use_container_width=True, hide_index=True)
        if not structure:
            st.info('No structure changes match this observation range and filters.')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, data.history.sqlite3.Error) as exc:
        st.error(f'Cannot read this history: {exc}')
