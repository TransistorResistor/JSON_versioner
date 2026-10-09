"""Build a deterministic multi-snapshot demo from the repository test records."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
GENERATED = HERE / 'generated'
SOURCE = GENERATED / 'records'
HISTORY = GENERATED / 'history'
CONFIG = GENERATED / 'demo_config.json'


def read(name: str) -> dict:
    return json.loads((SOURCE / name).read_text(encoding='utf-8-sig'))


def write(name: str, value: dict) -> None:
    (SOURCE / name).write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


def parameter(record: dict, name: str) -> dict:
    for item in record.get('parametrics', []):
        if item.get('parameter') == name:
            return item
    raise KeyError(f"{record.get('nomenclature')}: parameter {name!r} not found")


def set_parameter(filename: str, name: str, value: str, combined: str | None,
                  comment: str | None, updated: str, description_noise: bool = True) -> None:
    record = read(filename)
    item = parameter(record, name)
    item['parameterValue'] = value
    if combined is not None:
        item['parameterUomValue'] = combined
    if comment is not None:
        item['comments'] = comment
    if description_noise and item.get('parameterDescr'):
        item['parameterDescr'] += ' (editorial wording updated)'
    record['updatedDate'] = updated
    write(filename, record)


def add_description_noise() -> None:
    for path in SOURCE.glob('*.json'):
        record = json.loads(path.read_text(encoding='utf-8-sig'))
        changed = False
        for item in record.get('parametrics', []):
            if item.get('parameterDescr'):
                item['parameterDescr'] += ' '
                changed = True
        if changed:
            path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


def set_record_fields(filename: str, updated: str) -> None:
    """Add visible non-parameter edits so the drill-down demonstrates every section."""
    record = read(filename)
    overview = next(item for item in record['descriptions'] if item.get('descrType') == 'Overview')
    overview['shortDescription'] = ('Agile medium-range surface-to-air missile with active radar homing, '
                                    'terminal gas-dynamic control and quad-pack launcher compatibility.')
    record['aliases'].append({'alias': '9M96E2'})
    record['updatedDate'] = updated
    write(filename, record)


def snapshot(note: str) -> None:
    subprocess.run([sys.executable, str(ROOT / 'version_json.py'), str(SOURCE),
                    '--history', str(HISTORY), '--config', str(CONFIG), '--note', note],
                   cwd=ROOT, check=True)


def make_config() -> None:
    cfg = json.loads((ROOT / 'config.json').read_text(encoding='utf-8'))
    cfg['ignore_fields'] = sorted(set(cfg.get('ignore_fields', [])) | {'updatedDate', 'updated_at'})
    cfg['ignore_paths'] = sorted(set(cfg.get('ignore_paths', [])) | {
        '$.parametrics[].parameterDescr', '$.parametrics[].parameterOnly',
        '$.parametrics[].seq', '$.parametrics[].dataType'})
    CONFIG.write_text(json.dumps(cfg, indent=2) + '\n', encoding='utf-8')


def report() -> None:
    sys.path.insert(0, str(ROOT / 'executive_dashboard'))
    import data
    cfg = data.load_config(CONFIG)
    snapshots, _, events = data.load_history(HISTORY / 'history.sqlite3', cfg)
    sizes = data.semantic_sizes(HISTORY / 'history.sqlite3', cfg, events)
    rows = data.change_rows(events, cfg, semantic_sizes=sizes)
    details = []
    for row in rows:
        fields = data.change_fields(HISTORY / 'history.sqlite3', row['snapshot_id'], row['record_id'])
        details.append({'record': row['record_name'], 'size': row['size'],
                        'display_change_count': row['display_change_count'],
                        'shown_fields': [item['json_path'].rsplit('.', 1)[-1] for item in fields]})
    result = {'snapshots': len(snapshots), 'record_updates': len(rows),
              'structure_changes': len(data.structure_changes(HISTORY / 'history.sqlite3', cfg,
                                                               snapshots[0]['id'], snapshots[-1]['id'])),
              'updates': details, 'system_summary': data.system_summary(rows)}
    (GENERATED / 'summary.json').write_text(json.dumps(result, indent=2, ensure_ascii=False, default=str) + '\n', encoding='utf-8')


def main() -> None:
    if GENERATED.exists():
        shutil.rmtree(GENERATED)
    GENERATED.mkdir(parents=True)
    shutil.copytree(ROOT / 'test_records', SOURCE)
    make_config()
    snapshot('Baseline from test_records')

    add_description_noise()
    set_parameter('9M96.json', 'Maximum range', '150', '150 km',
                  'Expanded engagement envelope; source values vary by target profile', '2026-09-06')
    set_parameter('9M96.json', 'Maximum speed', '3.5', '3.5 Mach',
                  'Published maximum estimate', '2026-09-06')
    set_parameter('9M96.json', 'Maximum altitude', '35', '35 km',
                  'Upper engagement estimate', '2026-09-06')
    set_parameter('9M96.json', 'Weight', '185', '185 kg',
                  'Launch-weight estimate', '2026-09-06')
    set_parameter('9M96.json', 'Length', '3.9', '3.9 m',
                  'Overall length estimate', '2026-09-06')
    set_parameter('9M96.json', 'Guidance system',
                  'Inertial guidance with datalink update and active radar terminal homing', None,
                  'Flight-phase summary', '2026-09-06')
    set_record_fields('9M96.json', '2026-09-06')
    set_parameter('AN_APG-77.json', 'Detection range - 1', '220', '220 km',
                  'Estimated against a representative fighter-sized target', '2026-09-09')
    snapshot('Parameter value and comment updates')

    set_parameter('F-22_Raptor.json', 'Combat range - 1', '900', '900 km',
                  'Combat radius with internal fuel and representative mission load', '2026-09-17')
    set_parameter('S-400_Triumf.json', 'Deployment time', '5', '5 min',
                  'Crew-ready estimate under prepared-site conditions', '2026-09-19')
    for path in SOURCE.glob('*.json'):
        record = json.loads(path.read_text(encoding='utf-8-sig'))
        # Empty across almost every comparable record: a structural field
        # addition, not a record edit under JSON Versioner's schema contract.
        record['operationalRole'] = None
        path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    snapshot('Additional record updates and one structure addition')

    set_parameter('9M96DM.json', 'Maximum range', '250', '250 km',
                  'Extended-range variant estimate', '2026-09-26')
    set_parameter('F-35_Lightning_II.json', 'Unit cost - 1', '82.5', '82.5 USD million',
                  'Approximate flyaway cost; programme-average cost differs', '2026-09-28')
    snapshot('Late-month value and comment updates')
    report()
    print(f'Demo generated at {GENERATED}')


if __name__ == '__main__':
    main()
