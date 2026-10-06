"""Run with: python -m streamlit run executive_dashboard/app.py"""
from __future__ import annotations

import json
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

import data


ROOT = Path(__file__).resolve().parents[1]
st.set_page_config(page_title='Change Brief', page_icon='∆', layout='wide')


@st.cache_data(show_spinner=False, max_entries=4)
def load(path, stamp, cfg_json):
    cfg = json.loads(cfg_json)
    snapshots, totals, events = data.load_history(path, cfg)
    return cfg, snapshots, totals, data.change_rows(events, cfg)


@st.cache_data(show_spinner=False, max_entries=64)
def load_fields(path, stamp, snapshot_id, record_id):
    return data.change_fields(path, snapshot_id, record_id)


@st.cache_data(show_spinner=False, max_entries=32)
def load_structure(path, stamp, cfg_json, start_snapshot, end_snapshot):
    return data.structure_changes(path, json.loads(cfg_json), start_snapshot, end_snapshot)


def database_stamp(path: Path):
    result = []
    for item in (path, Path(str(path) + '-wal')):
        stat = item.stat() if item.exists() else None
        result.append((stat.st_mtime_ns, stat.st_size) if stat else None)
    return tuple(result)


def display_value(value):
    if value is None:
        return '(not present)'
    try:
        decoded = json.loads(value)
        return json.dumps(decoded, ensure_ascii=False, indent=2) if isinstance(decoded, (dict, list)) else str(decoded)
    except (json.JSONDecodeError, TypeError):
        return str(value)


def change_detail(path, stamp, selected):
    st.subheader(selected['title'])
    day_label = selected['estimated_day'].strftime('%d %b %Y').lstrip('0')
    st.caption(f"{selected['system_group']} · {selected['system_type']} · {selected['model_owner']} · "
               f"estimated {day_label} · {selected.get('timing_basis') or 'observation fallback'}")
    fields = load_fields(str(path), stamp, selected['snapshot_id'], selected['record_id'])
    if fields:
        table = pd.DataFrame([{'Field': row['json_path'], 'Before': display_value(row['old_value']),
                               'After': display_value(row['new_value'])} for row in fields])
        st.dataframe(table, use_container_width=True, hide_index=True)
    else:
        st.info('This is a whole-record addition or removal. Open the stored records below for the complete content.')
    with st.expander('Complete stored records'):
        before, after = data.record_versions(path, selected)
        left, right = st.columns(2)
        left.markdown('**Before**')
        left.json(None if before is data.history.ABSENT else before, expanded=False)
        right.markdown('**After**')
        right.json(None if after is data.history.ABSENT else after, expanded=False)


def main():
    st.title('Change Brief')
    database_text = st.sidebar.text_input('History database', str(ROOT / 'json_history' / 'history.sqlite3'))
    config_text = st.sidebar.text_input('Configuration', str(ROOT / 'config.json'))
    path, config_path = Path(database_text), Path(config_text)
    if not path.is_file() or not config_path.is_file():
        st.info('Select an existing history database and configuration file.')
        return
    stamp = database_stamp(path)
    cfg_json = config_path.read_text(encoding='utf-8')
    cfg, snapshots, totals, rows = load(str(path), stamp, cfg_json)
    if not snapshots:
        st.info('No history snapshots are available.')
        return

    groups = sorted({row['system_group'] for row in rows})
    selected_groups = st.sidebar.multiselect('System groups', groups, default=groups)
    group_rows = [row for row in rows if row['system_group'] in selected_groups]
    types = sorted({row['system_type'] for row in group_rows})
    selected_types = st.sidebar.multiselect('System types', types, default=types)
    type_rows = [row for row in group_rows if row['system_type'] in selected_types]
    owners = sorted({row['model_owner'] for row in type_rows})
    selected_owners = st.sidebar.multiselect('Owners', owners, default=owners)

    dates = [row['estimated_day'].date() for row in rows]
    start = st.sidebar.date_input('Changed from', min(dates), min_value=min(dates), max_value=max(dates))
    end = st.sidebar.date_input('Changed through', max(dates), min_value=min(dates), max_value=max(dates))
    filtered = data.filter_changes(rows, selected_groups, selected_types, selected_owners, start, end)

    page = st.sidebar.radio('View', ['Largest changes', 'Changes over time', 'Changes by system type', 'Structure changes'])
    if page == 'Largest changes':
        st.caption(f'{len(filtered):,} record updates across {len({row["record_id"] for row in filtered}):,} records. '
                   'Structure-only changes are excluded.')
        order = st.selectbox('Sort', ['Largest first', 'Most recent first', 'Smallest first'])
        ordered = data.sort_changes(filtered, order)
        size_counts = pd.Series([row['size'] for row in ordered]).value_counts() if ordered else {}
        metrics = st.columns(4)
        for column, label in zip(metrics, ['Large', 'Medium', 'Small', 'Added / removed']):
            value = (size_counts.get('Added', 0) + size_counts.get('Removed', 0)) if label == 'Added / removed' else size_counts.get(label, 0)
            column.metric(label, int(value))
        shown = ordered[:500]
        frame = pd.DataFrame([{'Size': row['size'], 'Record': row['record_name'] or row['record_id'],
                               'Description': row['description'], 'System group': row['system_group'],
                               'System type': row['system_type'], 'Owner': row['model_owner'],
                               'Estimated change date': row['estimated_day'].date(),
                               'Date basis': row.get('timing_basis')} for row in shown])
        st.dataframe(frame, use_container_width=True, hide_index=True)
        if len(ordered) > 500:
            st.caption(f'Showing 500 of {len(ordered):,} matching updates.')
        if ordered:
            options = {f"{row['size']} · {row['record_name'] or row['record_id']} · {row['estimated_day'].date()}": row for row in ordered}
            choice = st.selectbox('Inspect an update', options)
            change_detail(path, stamp, options[choice])
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
        structure = load_structure(str(path), stamp, cfg_json, snapshots[0]['id'], snapshots[-1]['id'])
        st.dataframe(pd.DataFrame(structure), use_container_width=True, hide_index=True)


if __name__ == '__main__':
    main()
