"""Build a deterministic month-long demo with enough volume to test the feed."""
from __future__ import annotations

import datetime as dt
import json
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
GENERATED = HERE / 'generated_large'
SOURCE = GENERATED / 'records'
HISTORY = GENERATED / 'history'
CONFIG = GENERATED / 'demo_config.json'
RECORD_COUNT = 72
CHANGE_DAYS = [1, 5, 9, 13, 17, 21, 25, 29]


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str) + '\n', encoding='utf-8')


def clone_records() -> None:
    sources = sorted((ROOT / 'test_records').glob('*.json'))
    SOURCE.mkdir(parents=True)
    for index in range(RECORD_COUNT):
        record = json.loads(sources[index % len(sources)].read_text(encoding='utf-8-sig'))
        original_name = str(record.get('nomenclature') or record.get('name') or sources[index % len(sources)].stem)
        record['modelID'] = 900001 + index
        record['nomenclature'] = f'{original_name} · Demo {index + 1:02d}'
        record['modelOwner'] = ['Team North', 'Team Central', 'Team South'][index % 3]
        record['updatedDate'] = '2026-08-31'
        write_json(SOURCE / f'{index + 1:03d}.json', record)


def make_config() -> None:
    cfg = json.loads((ROOT / 'config.json').read_text(encoding='utf-8'))
    cfg['ignore_fields'] = sorted(set(cfg.get('ignore_fields', [])) | {'updatedDate', 'updated_at'})
    write_json(CONFIG, cfg)


def set_snapshot_time(day: dt.date) -> None:
    stamp = f'{day.isoformat()}T12:00:00+00:00'
    with sqlite3.connect(HISTORY / 'history.sqlite3') as db:
        snapshot_id = db.execute('SELECT MAX(id) FROM snapshots').fetchone()[0]
        db.execute('UPDATE snapshots SET created_at=? WHERE id=?', (stamp, snapshot_id))
        db.execute('UPDATE record_changes SET event_at=? WHERE snapshot_id=?', (stamp, snapshot_id))


def snapshot(note: str, day: dt.date) -> None:
    subprocess.run([sys.executable, str(ROOT / 'version_json.py'), str(SOURCE),
                    '--history', str(HISTORY), '--config', str(CONFIG), '--note', note],
                   cwd=ROOT, check=True)
    set_snapshot_time(day)


def revised_value(value, step: int, index: int) -> str:
    text = str(value or '')
    if re.fullmatch(r'[+-]?\d+(?:\.\d+)?', text):
        number = float(text) + ((index % 5) + 1) * step
        return str(int(number)) if number.is_integer() else f'{number:.1f}'
    return re.sub(r'\s+· revision \d+$', '', text) + f' · revision {step}'


def update_records(step: int, day: dt.date) -> None:
    for index, path in enumerate(sorted(SOURCE.glob('*.json'))):
        record = json.loads(path.read_text(encoding='utf-8'))
        parameters = record.get('parametrics') or []
        if parameters:
            item = parameters[(step + index) % len(parameters)]
            value = revised_value(item.get('parameterValue'), step, index)
            item['parameterValue'] = value
            if item.get('uom'):
                item['parameterUomValue'] = f"{value} {item['uom']}"
            item['comments'] = f'Review cycle {step}; synthetic demonstration value'
        if step % 3 == 0 and record.get('descriptions'):
            description = record['descriptions'][0]
            key = 'shortDescription' if 'shortDescription' in description else 'description'
            description[key] = re.sub(r'\s+Review cycle \d+\.$', '', str(description.get(key) or '')) + f' Review cycle {step}.'
        record['updatedDate'] = day.isoformat()
        write_json(path, record)


def report() -> None:
    sys.path.insert(0, str(ROOT / 'executive_dashboard'))
    import data
    cfg = data.load_config(CONFIG)
    snapshots, _, events = data.load_history(HISTORY / 'history.sqlite3', cfg)
    sizes = data.semantic_sizes(HISTORY / 'history.sqlite3', cfg, events)
    rows = data.change_rows(events, cfg, semantic_sizes=sizes)
    result = {'snapshots': len(snapshots), 'records': RECORD_COUNT, 'record_updates': len(rows),
              'first_change': min(row['estimated_day'] for row in rows),
              'last_change': max(row['estimated_day'] for row in rows)}
    write_json(GENERATED / 'summary.json', result)
    print(json.dumps(result, indent=2, default=str))


def main() -> None:
    if GENERATED.exists():
        shutil.rmtree(GENERATED)
    GENERATED.mkdir(parents=True)
    clone_records()
    make_config()
    snapshot('Large demo baseline', dt.date(2026, 8, 31))
    for step, day_number in enumerate(CHANGE_DAYS, 1):
        day = dt.date(2026, 9, day_number)
        update_records(step, day)
        snapshot(f'Large demo review cycle {step}', day)
    report()


if __name__ == '__main__':
    main()
