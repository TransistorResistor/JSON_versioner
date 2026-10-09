"""Build 500 synthetic records and exactly 50 record updates per month."""
from __future__ import annotations

import argparse
import calendar
import contextlib
import copy
import datetime as dt
import json
import random
import re
import sqlite3
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from configuration import load_config
from executive_dashboard import data
import version_json as versioner

OUTPUT = HERE / 'generated_synthetic'
CHANGE_DAYS = (5, 11, 17, 23, 28)


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str) + '\n', encoding='utf-8')


def month_date(first, offset):
    index = first.year * 12 + first.month - 1 + offset
    return dt.date(index // 12, index % 12 + 1, 1)


def templates(source):
    result = []
    for path in sorted(source.glob('*.json')):
        record = json.loads(path.read_text(encoding='utf-8-sig'))
        # The schema-shape exemplar is a placeholder, rather than a system.
        if path.stem == 'exemplar_schema_shape':
            continue
        if not isinstance(record, dict) or 'modelID' not in record:
            raise ValueError(f'Template needs a modelID: {path}')
        result.append((path.stem, record))
    if not result:
        raise ValueError(f'No record templates at {source}')
    return result


def clone_records(source, destination, count, baseline):
    originals = templates(source)
    destination.mkdir()
    provenance = []
    for index in range(count):
        cohort, template_index = divmod(index, len(originals))
        stem, original = originals[template_index]
        record = copy.deepcopy(original)
        model_id = 900001 + index
        suffix = f'Synthetic {cohort + 1:02d}'
        name = str(original.get('nomenclature') or original.get('name') or stem)
        record['modelID'] = model_id
        record['nomenclature'] = f'{name} · {suffix}'
        record['modelOwner'] = ('Team North', 'Team Central', 'Team South', 'Team West', 'Team East')[index % 5]
        record['updatedDate'] = baseline.isoformat()
        record['syntheticReviewNote'] = ''
        record['syntheticNotice'] = 'Synthetic demonstration record; revised values are not real system specifications.'
        mapping = {str(raw['modelID']): (900001 + cohort * len(originals) + n,
                   f"{raw.get('nomenclature') or raw.get('name') or key} · {suffix}")
                   for n, (key, raw) in enumerate(originals)
                   if cohort * len(originals) + n < count}
        for relation in record.get('relations') or []:
            for side in ('parent', 'child'):
                target = mapping.get(str(relation.get(side + 'ModelID')))
                if target:
                    relation[side + 'ModelID'], relation[side + 'Model'] = target
        for media_index, item in enumerate(record.get('media') or [], 1):
            item['mediaID'] = model_id * 100 + media_index
        write_json(destination / f'{model_id}.json', record)
        provenance.append({'modelID': model_id, 'template': stem, 'original_modelID': original['modelID']})
    return provenance


def revised_value(value, revision):
    text = str(value if value is not None else '')
    if re.fullmatch(r'[+-]?\d+(?:\.\d+)?', text):
        return f'{float(text) + revision * 0.5:g}'
    return re.sub(r'\s+\[synthetic revision \d+\]$', '', text) + f' [synthetic revision {revision}]'


def edit_record(record, kind, revision):
    parameters = record.get('parametrics') or []
    if kind in ('parameter', 'parameter_bundle') and parameters:
        count = 1 if kind == 'parameter' else min(5, len(parameters))
        offset = revision % len(parameters)
        for index in range(count):
            item = parameters[(offset + index) % len(parameters)]
            value = revised_value(item.get('parameterValue'), revision)
            item['parameterValue'] = value
            if 'parameterUomValue' in item or item.get('uom'):
                item['parameterUomValue'] = f"{value} {item.get('uom') or ''}".strip()
            item['comments'] = f'Synthetic review {revision}: parameter estimate revised.'
        return
    descriptions = record.get('descriptions') or []
    if kind in ('description', 'large_description') and descriptions:
        item = descriptions[revision % len(descriptions)]
        if kind == 'description':
            item['shortDescription'] = f'Synthetic review {revision}: updated operational summary for this demonstration variant.'
        else:
            # More than 250 edited words demonstrates the Large description band.
            item['description'] = ' '.join(
                f'Synthetic review {revision} section {number}: capabilities, assumptions, operating context, '
                'support arrangements, and limitations were reassessed for this demonstration variant.'
                for number in range(1, 21))
        return
    # Guaranteed meaningful edit even for a sparse template.
    record['syntheticReviewNote'] = f'Synthetic review {revision}: metadata assessment revised.'


def capture(records, history, config, note, day):
    args = argparse.Namespace(source=records, history=history, config=config, note=note,
        dry_run=False, url_template=None, column=None, timeout=20, url_prefix=None,
        insecure_skip_tls_verify=False)
    versioner.run(args)
    stamp = day.isoformat() + 'T12:00:00+00:00'
    # Only this generated history is backdated, to simulate observed monthly captures.
    with contextlib.closing(sqlite3.connect(history / 'history.sqlite3')) as db:
        sid = db.execute('SELECT MAX(id) FROM snapshots').fetchone()[0]
        db.execute('UPDATE snapshots SET created_at=? WHERE id=?', (stamp, sid))
        db.execute('UPDATE record_changes SET event_at=? WHERE snapshot_id=?', (stamp, sid))
        db.commit()
    return sid


def validate(history, cfg, expected_records, months, updates):
    path = history / 'history.sqlite3'
    observations = data.history.snapshots(path)
    _, _, events = data.load_history(path, cfg, observations[0]['id'], observations[-1]['id'])
    sizes = data.semantic_sizes(path, cfg, events)
    rows = data.change_rows(events, cfg, semantic_sizes=sizes)
    monthly = {}
    for row in rows:
        month = row['estimated_day'].strftime('%Y-%m')
        monthly.setdefault(month, {'events': 0, 'record_ids': set(), 'sizes': {}})
        item = monthly[month]
        item['events'] += 1
        item['record_ids'].add(row['record_id'])
        item['sizes'][row['size']] = item['sizes'].get(row['size'], 0) + 1
    if len(monthly) != months or any(item['events'] != updates or len(item['record_ids']) != updates
                                    for item in monthly.values()):
        raise ValueError('Monthly update counts do not match the requested dataset')
    if any(item['total'] != expected_records for item in observations):
        raise ValueError('Record population changed during generation')
    if any(row['kind'] != 'modified' for row in rows):
        raise ValueError('Expected only modifications after the baseline')
    with data.history.connect(path) as db:
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Generated database failed its integrity check')
    return {'records': expected_records, 'snapshots': len(observations), 'months': months,
            'updates_per_month': updates, 'record_updates': len(rows),
            'size_counts': {size: sum(row['size'] == size for row in rows) for size in ('Small', 'Medium', 'Large')},
            'monthly': {month: {**item, 'record_ids': sorted(item['record_ids'])}
                        for month, item in sorted(monthly.items())}}


def build(args):
    first = dt.date.fromisoformat(args.start_month + '-01')
    if args.records < 1 or not 1 <= args.changes_per_month <= args.records or args.months < 1:
        raise ValueError('Records/months must be positive, with 1..records changes per month')
    output = args.output.resolve()
    # Output replacement is restricted to generated demo folders, never source data/history.
    if output.parent != HERE.resolve() or not output.name.startswith('generated_'):
        raise ValueError('Output must be a generated_* directory inside executive_dashboard/demo')
    if output.exists() and not args.replace:
        raise ValueError(f'{output} already exists; use --replace to archive it and rebuild')
    cfg = load_config(ROOT / 'config.json')
    rng = random.Random(args.seed)
    baseline = first - dt.timedelta(days=1)
    with tempfile.TemporaryDirectory(prefix='synthetic-build-', dir=HERE) as temporary:
        staging = Path(temporary) / 'dataset'
        staging.mkdir()
        records, history, config = staging / 'records', staging / 'history', staging / 'demo_config.json'
        provenance = clone_records(ROOT / 'test_records', records, args.records, baseline)
        write_json(config, cfg)
        capture(records, history, config, 'Synthetic baseline: 500-system template inventory' if args.records == 500
                else 'Synthetic baseline', baseline)
        paths = sorted(records.glob('*.json'))
        plan = []
        for month_index in range(args.months):
            month = month_date(first, month_index)
            selected = rng.sample(paths, args.changes_per_month)
            # Default 50: 20 single-parameter, 10 bundles, 10 descriptions,
            # 5 large descriptions, and 5 metadata updates.
            for cycle, day_number in enumerate(CHANGE_DAYS):
                day = month.replace(day=min(day_number, calendar.monthrange(month.year, month.month)[1]))
                batch = selected[cycle * len(selected) // 5:(cycle + 1) * len(selected) // 5]
                if not batch:
                    continue
                for offset, path in enumerate(batch):
                    index = cycle * len(selected) // 5 + offset
                    band = index / len(selected)
                    kind = ('parameter' if band < .4 else 'parameter_bundle' if band < .6
                            else 'description' if band < .8 else 'large_description' if band < .9 else 'metadata')
                    revision = month_index * args.changes_per_month + index + 1
                    record = json.loads(path.read_text(encoding='utf-8'))
                    edit_record(record, kind, revision)
                    record['updatedDate'] = day.isoformat()
                    write_json(path, record)
                    plan.append({'month': month.strftime('%Y-%m'), 'day': day.isoformat(),
                                 'modelID': record['modelID'], 'kind': kind})
                sid = capture(records, history, config, f'Synthetic review: {day.isoformat()} ({len(batch)} updates)', day)
                for item in plan[-len(batch):]:
                    item['snapshot_id'] = sid
        summary = validate(history, cfg, args.records, args.months, args.changes_per_month)
        summary.update({'seed': args.seed, 'start_month': args.start_month,
                        'template_count': len(templates(ROOT / 'test_records')), 'baseline': baseline.isoformat()})
        # The temporary build is published only after counts and database integrity pass.
        with contextlib.closing(sqlite3.connect(history / 'history.sqlite3')) as db:
            db.execute('UPDATE snapshots SET source=?', (str(output / 'records'),))
            db.commit()
        write_json(staging / 'summary.json', summary)
        write_json(staging / 'provenance.json', provenance)
        write_json(staging / 'change_plan.json', plan)
        if output.exists():
            backup = output.with_name(output.name + '-backup-' + dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
            if backup.parent.resolve() != HERE.resolve():
                raise ValueError('Backup must stay inside the demo directory')
            output.rename(backup)
            print(f'Previous generated dataset archived at {backup}')
        staging.rename(output)
    print(f"Built {summary['records']} records, {summary['record_updates']} updates, {summary['months']} months at {output}")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--records', type=int, default=500)
    parser.add_argument('--changes-per-month', type=int, default=50)
    parser.add_argument('--months', type=int, default=6)
    parser.add_argument('--start-month', default='2026-04', help='First review month, YYYY-MM')
    parser.add_argument('--seed', type=int, default=20261009)
    parser.add_argument('--output', type=Path, default=OUTPUT)
    parser.add_argument('--replace', action='store_true', help='Archive existing generated output before replacing it')
    try:
        build(parser.parse_args())
    except (ValueError, OSError, sqlite3.Error) as exc:
        parser.exit(1, f'Error: {exc}\n')


if __name__ == '__main__':
    main()
