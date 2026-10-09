"""Regression coverage for empty histories, display rules, and configuration."""
import argparse
import contextlib
import io
import json
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import configuration
import dashboard_data as history
import version_json as versioner
from executive_dashboard import data
from streamlit.testing.v1 import AppTest


class ReviewFixTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'records'
        self.source.mkdir()
        self.cfg = configuration.configuration({})
        self.config = self.root / 'config.json'
        self.config.write_text(json.dumps(self.cfg), encoding='utf-8')
        self.args = argparse.Namespace(source=self.source, history=self.root / 'json_history',
            config=self.config, note='', dry_run=False, url_template=None, column=None,
            timeout=1, url_prefix=None)
        self.path = self.args.history / 'history.sqlite3'

    def capture(self, records):
        for record in records:
            (self.source / f"{record['modelID']}.json").write_text(json.dumps(record), encoding='utf-8')
        with contextlib.redirect_stdout(io.StringIO()):
            versioner.run(self.args)

    def app(self):
        code = (versioner.ROOT / 'executive_dashboard' / 'app.py').read_text(encoding='utf-8')
        code = code.replace('ROOT = Path(__file__).resolve().parents[1]', f'ROOT = Path({str(self.root)!r})')
        script = self.root / 'app_test.py'
        script.write_text(code, encoding='utf-8')
        return AppTest.from_file(str(script)).run(timeout=30)

    def choose_category(self, app, label, values, mode='Include'):
        next(w for w in app.sidebar.selectbox if w.label == label + ' filter').set_value(mode).run(timeout=30)
        widget = next(w for w in app.sidebar.multiselect if w.label == label)
        widget.set_value([next(option for option in widget.options if option.startswith(value + ' ('))
                          for value in values]).run(timeout=30)
        next(w for w in app.sidebar.button if w.label == 'Apply filters').click().run(timeout=30)

    def test_defaults_do_not_depend_on_user_config_and_create_missing_config(self):
        missing = self.root / 'nested' / 'config.json'
        with patch.object(versioner, 'ROOT', self.root), contextlib.redirect_stdout(io.StringIO()):
            cfg = versioner.config(missing)
        self.assertTrue(missing.is_file())
        self.assertEqual(cfg, self.cfg)
        self.config.write_text('{"ignore_fields": []}', encoding='utf-8')
        self.assertEqual(configuration.load_config(self.config)['ignore_fields'], [])

    def test_invalid_configuration_is_rejected_before_capture(self):
        for overrides in ({'id_fields': []}, {'ignore_paths': 'not a list'},
                          {'array_keys': {'$.items': []}}, {'important_fields': {'$.x': -1}},
                          {'schema_field_fraction': 2}, {'content_categories': [None]},
                          {'combined_update_size': {'small_max': 16, 'medium_max': 4}}):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                configuration.configuration(overrides)
        self.config.write_text('{"id_fields": []}', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.capture([{'modelID': 1}])
        self.assertFalse(self.path.exists())

    def test_new_parameter_fields_are_visible_with_opt_out_exclusions(self):
        prefix = '$.parametrics[component=null,parameter=%22Range%22].'
        rows = [{'json_path': prefix + field, 'old_value': '1', 'new_value': '2', 'word_edits': None}
                for field in ('parameterValue', 'uom', 'parameterUomValue', 'confidence', 'comments')]
        shown = data.minimise_parameter_fields(rows, {'dashboard_display': {'exclude_parameter_fields': []}})
        self.assertEqual({row['json_path'].rsplit('.', 1)[-1] for row in shown},
                         {'parameterUomValue', 'confidence', 'comments'})
        excluded = data.minimise_parameter_fields(rows,
            {'dashboard_display': {'exclude_parameter_fields': ['confidence', 'parameterUomValue']}})
        self.assertEqual({row['json_path'].rsplit('.', 1)[-1] for row in excluded},
                         {'parameterValue', 'uom', 'comments'})

    def test_encoded_selectors_preserve_punctuation_quotes_and_null_strings(self):
        from comparison import array_identity, array_path
        item = {'component': 'A,B.[C] "D"', 'parameter': 'null'}
        path = array_path('$.parametrics', list(item), array_identity(item, list(item))) + '.comments'
        shown = data.field_display({'json_path': path})
        self.assertEqual(shown['component'], item['component'])
        self.assertEqual(shown['parameter'], 'null')
        description = array_path('$.descriptions', ['descrType'], ('"Overview.v2, extra"',))
        label = data.field_display({'json_path': description + '.shortDescription'})['label']
        self.assertEqual(label, 'Overview.v2, extra · Short description')
        self.assertIn(item['component'], history.content_item_label(path.rsplit('.', 1)[0]))

    def test_baseline_and_unchanged_histories_allow_every_executive_view(self):
        record = {'modelID': 1, 'systemGroup': 'Air', 'systemType': 'Fighter'}
        self.capture([record])
        app = self.app()
        self.assertFalse(app.exception)
        self.assertFalse(app.error)
        self.capture([record])
        app.run(timeout=30)
        for view in ('Change brief', 'Changes over time', 'Changes by system type', 'Structure changes'):
            app.sidebar.radio[0].set_value(view).run(timeout=30)
            self.assertFalse(app.exception, view)
            self.assertFalse(app.error, view)

    def test_structure_changes_apply_filters_before_aggregation(self):
        records = [{'modelID': i, 'systemGroup': 'Air' if i < 3 else 'Sea',
                    'systemType': 'Type', 'modelOwner': 'Owner'} for i in range(1, 5)]
        self.capture(records)
        self.capture([{**record, 'empty': None} for record in records])
        with contextlib.closing(sqlite3.connect(self.path)) as db:
            db.execute("UPDATE snapshots SET created_at='2026-09-10T00:00:00Z' WHERE id=1")
            db.execute("UPDATE snapshots SET created_at='2026-09-20T00:00:00Z' WHERE id=2")
            db.execute("UPDATE record_changes SET event_at='2026-09-20T00:00:00Z' WHERE snapshot_id=2")
            db.commit()
        filters = {'system_group': ['Air'], 'system_type': ['Type'], 'model_owner': ['Owner']}
        rows = data.structure_changes(self.path, self.cfg, 1, 2, filters,
                                      date(2026, 9, 20), date(2026, 9, 20))
        self.assertEqual(rows[0]['affected_records'], 2)
        self.assertEqual(rows[0]['system_groups'], 'Air')
        self.assertEqual(data.structure_changes(self.path, self.cfg, 1, 2, filters,
                         date(2026, 9, 10), date(2026, 9, 10)), [])
        self.assertEqual(data.structure_changes(self.path, self.cfg, 1, 1, filters), [])
        app = self.app()
        app.sidebar.radio[0].set_value('Structure changes').run(timeout=30)
        self.choose_category(app, 'System groups', ['Air'])
        self.assertFalse(app.exception)
        self.assertEqual(app.dataframe[0].value.iloc[0]['affected_records'], 2)
        self.choose_category(app, 'System groups', ['Air', 'Sea'], mode='Exclude')
        self.assertFalse(app.exception)
        self.assertTrue(app.dataframe[0].value.empty)

    def test_unknown_parameter_edits_render_and_preview_preserves_same_day_versions(self):
        record = {'modelID': 1, 'systemGroup': 'Air', 'parametrics': [
            {'component': 'A,B', 'parameter': 'Range', 'parameterValue': 5, 'confidence': 1}]}
        self.capture([record])
        record['parametrics'][0]['confidence'] = 2
        self.capture([record])
        app = self.app()
        self.assertFalse(app.exception)
        app.query_params['record'] = 'modelID:1'
        app.query_params['snapshot'] = '2'
        app.run(timeout=30)
        self.assertFalse(app.exception)
        self.assertTrue(any('Confidence' in widget.value for widget in app.markdown))
        preview = next(w for w in app.selectbox if w.label == 'Version')
        self.assertEqual(len(preview.options), 2)
        self.assertTrue(all(label.startswith('#') for label in preview.options))

    def test_history_loading_defaults_to_latest_thirty_observations(self):
        for value in range(32):
            self.capture([{'modelID': 1, 'value': value}])
        snapshots, totals, events = data.load_history(self.path, self.cfg)
        self.assertEqual(len(snapshots), 32)
        self.assertEqual({row['snapshot_id'] for row in totals}, set(range(3, 33)))
        self.assertEqual({row['snapshot_id'] for row in events}, set(range(3, 33)))
        _, totals, events = data.load_history(self.path, self.cfg, 1, 2)
        self.assertEqual({row['snapshot_id'] for row in totals}, {1, 2})
        self.assertEqual(len(events), 2)
        app = self.app()
        self.assertFalse(app.exception)
        app.query_params['record'] = 'modelID:1'
        app.query_params['snapshot'] = '2'
        app.run(timeout=30)
        self.assertFalse(app.exception)
        self.assertTrue(any('edited' in widget.value for widget in app.subheader))

    def test_filters_persist_through_detail_navigation_and_can_be_cleared(self):
        records = [{'modelID': 1, 'systemGroup': 'Air', 'systemType': 'Fighter', 'value': 1},
                   {'modelID': 2, 'systemGroup': 'Sea', 'systemType': 'Ship', 'value': 1}]
        for value in (1, 2, 3):
            self.capture([{**record, 'value': value} for record in records])
        app = self.app()
        self.choose_category(app, 'System groups', ['Air'])
        app.sidebar.radio[0].set_value('Change brief').run(timeout=30)
        next(w for w in app.button if w.label == 'Open selected change').click().run(timeout=30)
        self.assertFalse(app.exception)
        self.assertEqual(app.query_params['snapshot'], ['3'])
        next(w for w in app.button if w.label == 'Next change →').click().run(timeout=30)
        self.assertFalse(app.exception)
        self.assertEqual(app.query_params['snapshot'], ['2'])
        next(w for w in app.button if w.label == 'Back to changes').click().run(timeout=30)
        self.assertFalse(app.exception)
        self.assertEqual(app.sidebar.radio[0].value, 'Change brief')
        self.assertTrue(next(w for w in app.sidebar.multiselect if w.label == 'System groups').value[0].startswith('Air ('))
        next(w for w in app.button if w.label.startswith('Groups:')).click().run(timeout=30)
        self.assertFalse(app.exception)
        self.assertEqual(next(w for w in app.sidebar.selectbox if w.label == 'System groups filter').value, 'All')
        next(w for w in app.sidebar.button if w.label == 'Reset filters').click().run(timeout=30)
        self.assertFalse(app.exception)
        self.assertEqual(app.sidebar.radio[0].value, 'Change brief')

    def test_baseline_inventory_action_and_capture_status(self):
        self.capture([{'modelID': 1, 'nomenclature': 'Alpha', 'systemGroup': 'Air'}])
        app = self.app()
        self.assertTrue(any('initial baseline' in widget.value for widget in app.info))
        self.assertEqual(next(w for w in app.metric if w.label == 'Records captured').value, '1')
        next(w for w in app.button if w.label == 'View captured inventory').click().run(timeout=30)
        self.assertFalse(app.exception)
        self.assertFalse(app.error)
        self.assertEqual(app.sidebar.radio[0].value, 'Current inventory')
        self.assertEqual(app.dataframe[0].value.iloc[0]['record_name'], 'Alpha')

    def test_date_shortcuts_and_custom_dates(self):
        for value in (1, 2, 3):
            self.capture([{'modelID': 1, 'systemGroup': 'Air', 'value': value}])
        with contextlib.closing(sqlite3.connect(self.path)) as db:
            for sid, day in ((1, '2026-09-01'), (2, '2026-09-10'), (3, '2026-09-20')):
                stamp = day + 'T12:00:00Z'
                db.execute('UPDATE snapshots SET created_at=? WHERE id=?', (stamp, sid))
                db.execute('UPDATE record_changes SET event_at=? WHERE snapshot_id=?', (stamp, sid))
            db.commit()
        app = self.app()
        window = next(w for w in app.sidebar.selectbox if w.label == 'Time window')
        for choice in ('Last capture', 'Last 7 days'):
            window.set_value(choice).run(timeout=30)
            self.assertFalse(app.exception)
            self.assertEqual(next(w for w in app.metric if w.label == 'Record updates').value, '1')
            window = next(w for w in app.sidebar.selectbox if w.label == 'Time window')
        window.set_value('Custom dates').run(timeout=30)
        next(w for w in app.sidebar.date_input if w.label == 'Changed from').set_value(date(2026, 9, 10)).run(timeout=30)
        next(w for w in app.sidebar.date_input if w.label == 'Changed through').set_value(date(2026, 9, 10)).run(timeout=30)
        self.assertFalse(app.exception)
        self.assertEqual(next(w for w in app.metric if w.label == 'Record updates').value, '1')
        app.sidebar.radio[0].set_value('Changes over time').run(timeout=30)
        self.assertEqual(next(w for w in app.sidebar.date_input if w.label == 'Changed from').value, date(2026, 9, 10))

    def test_category_modes_and_unique_record_counts(self):
        options = ['A', 'B', 'C']
        self.assertEqual(data.category_values(options, {}), options)
        self.assertEqual(data.category_values(options, {'mode': 'Include', 'values': []}), options)
        self.assertEqual(data.category_values(options, {'mode': 'Exclude', 'values': ['B']}), ['A', 'C'])
        self.assertEqual(data.category_values(options, {'mode': 'Include', 'values': ['unavailable']}), [])
        self.assertEqual(data.category_values(options, {'mode': 'Exclude', 'values': options}), [])
        self.assertEqual(data.category_counts([{'record_id': '1', 'group': 'A'},
            {'record_id': '1', 'group': 'A'}, {'record_id': '2', 'group': 'A'}], 'group'), {'A': 2})

    def test_large_category_lists_apply_only_when_submitted(self):
        records = [{'modelID': i, 'systemGroup': f'Group {i % 20:02d}',
                    'systemType': f'Type {i % 80:02d}', 'modelOwner': f'Owner {i:03d}', 'value': 1}
                   for i in range(100)]
        self.capture(records)
        self.capture([{**record, 'value': 2} for record in records])
        app = self.app()
        self.assertEqual(len(app.sidebar.multiselect), 0)
        next(w for w in app.sidebar.selectbox if w.label == 'System groups filter').set_value('Include').run(timeout=30)
        self.assertEqual(len(next(w for w in app.sidebar.multiselect if w.label == 'System groups').options), 20)
        next(w for w in app.sidebar.selectbox if w.label == 'System types filter').set_value('Include').run(timeout=30)
        self.assertEqual(len(next(w for w in app.sidebar.multiselect if w.label == 'System types').options), 80)
        next(w for w in app.sidebar.selectbox if w.label == 'Owners filter').set_value('Exclude').run(timeout=30)
        owner = next(w for w in app.sidebar.multiselect if w.label == 'Owners')
        self.assertEqual(len(owner.options), 100)
        self.assertEqual(owner.value, [])
        owner.set_value(['Owner 099 (1 records)']).run(timeout=30)
        self.assertEqual(next(w for w in app.metric if w.label == 'Record updates').value, '100')
        next(w for w in app.sidebar.button if w.label == 'Apply filters').click().run(timeout=30)
        self.assertFalse(app.exception)
        self.assertEqual(next(w for w in app.metric if w.label == 'Record updates').value, '99')
        self.assertTrue(any(w.label.startswith('Owners: 1 excluded') for w in app.button))
        next(w for w in app.button if w.label.startswith('Owners:')).click().run(timeout=30)
        self.assertEqual(next(w for w in app.metric if w.label == 'Record updates').value, '100')


if __name__ == '__main__':
    unittest.main()
