"""Category analytics and navigation regression tests."""
import json
import unittest

import dashboard_data as data
import test_versioner as fixtures
import version_json as v


class DashboardTests(unittest.TestCase):
    setUp = fixtures.VersionerTests.setUp
    write = fixtures.VersionerTests.write
    run_snapshot = fixtures.VersionerTests.run_snapshot
    connect = fixtures.VersionerTests.connect

    def history(self):
        original = {'id': 1, 'nomenclature': 'Example One', 'systemGroup': 'Sensors',
                    'systemType': 'Radar', 'modelOwner': 'North', 'status': 'original'}
        self.write('1.json', original)
        self.write('2.json', {**original, 'id': 2, 'nomenclature': 'Example Two'})
        self.write('3.json', {'id': 3, 'systemGroup': 'Weapons', 'systemType': 'Missile', 'modelOwner': 'South'})
        self.write('4.json', {'id': 4, 'systemGroup': 'Sensors', 'systemType': 'Radar'})
        self.run_snapshot()
        self.write('1.json', {**original, 'modelOwner': 'South', 'status': 'first change'})
        (self.source / '2.json').unlink()
        self.write('5.json', {'id': 5, 'systemGroup': 'Aircraft', 'systemType': 'Fighter', 'modelOwner': 'North'})
        self.run_snapshot()
        self.write('1.json', {**original, 'modelOwner': 'South', 'status': 'second change'})
        (self.source / '3.json').unlink()
        self.run_snapshot()
        self.write('1.json', original)
        self.run_snapshot()
        db = self.connect()
        for sid in range(1, 5):
            stamp = f'2026-09-{sid*7:02d}T12:00:00+00:00'
            db.execute('UPDATE snapshots SET created_at=? WHERE id=?', (stamp, sid))
            db.execute("UPDATE record_changes SET event_at=?,event_basis='snapshot observation' WHERE snapshot_id=?", (stamp, sid))
        db.execute("UPDATE record_changes SET event_at='2026-09-10T10:00:00+00:00',event_basis='record timestamp' WHERE snapshot_id=2 AND record_id='id:1'")
        db.commit()
        self.path = self.args.history / 'history.sqlite3'
        self.period = data.snapshots(self.path)
        self.totals, self.events = data.period_data(self.path, self.cfg, 1, 4)

    def test_rates_unique_records_baseline_and_removed_categories(self):
        self.history()
        active = data.meaningful(self.events)
        self.assertEqual(len(active), 6)
        self.assertEqual(len({e['record_id'] for e in active}), 4)
        self.assertEqual(len(data.meaningful(self.events, True)), 10)
        series = data.category_series(self.totals, self.events, self.period, 'system_type')
        cell = lambda sid, cat: next(r for r in series if r['snapshot_id'] == sid and r['category'] == cat)
        self.assertAlmostEqual(cell(2, 'Radar')['affected_percent'], 200/3)
        self.assertEqual(cell(3, 'Radar')['affected_percent'], 50)
        self.assertEqual(cell(3, 'Missile')['affected_percent'], 100)
        self.assertIsNone(cell(1, 'Radar')['affected_percent'])
        self.assertIsNone(cell(4, 'Missile')['affected_percent'])
        summary = data.category_summary(series, self.events, 'system_type')
        radar = next(r for r in summary if r['category'] == 'Radar')
        self.assertEqual((radar['events'], radar['unique_records']), (4, 2))

    def test_owner_attribution_missing_owner_and_legacy_metadata(self):
        self.history()
        event = next(r for r in self.events if r['snapshot_id'] == 2 and r['record_id'] == 'id:1')
        self.assertEqual((event['previous_model_owner'], event['model_owner']), ('North', 'South'))
        removed = next(r for r in self.events if r['kind'] == 'removed' and r['record_id'] == 'id:2')
        self.assertEqual(removed['model_owner'], 'North')
        self.assertIn(data.UNKNOWN, {r['model_owner'] for r in self.totals})
        db = self.connect()
        # Simulate blobs written before owner/name metadata existed.
        db.execute("UPDATE record_blobs SET classification_json='{}'")
        db.commit()
        totals, events = data.period_data(self.path, self.cfg, 1, 4)
        self.assertEqual(totals, self.totals)
        self.assertEqual(events, self.events)

    def test_timing_has_exclusive_bases_and_correct_dates(self):
        self.history()
        estimated = data.timing(self.events, 'Estimated')
        observed = data.timing(self.events, 'Observed')
        self.assertEqual(sum(r['events'] for r in estimated), 6)
        self.assertEqual(sum(r['events'] for r in observed), 6)
        reported = [r for r in estimated if r['basis'] == 'Record timestamp']
        self.assertEqual(reported, [{'period': '2026-09-10T00:00:00+00:00', 'basis': 'Record timestamp', 'events': 1}])
        self.assertEqual(sum(r['events'] for r in data.timing(self.events, 'Estimated', 'Month')), 6)

    def test_fields_distinguish_unique_records_from_repeated_events(self):
        self.history()
        active = data.meaningful(self.events)
        fields = data.field_scope(self.path, self.cfg, [(r['snapshot_id'], r['record_id']) for r in active])
        status = next(r for r in fields if r['field'] == '$.status')
        self.assertEqual((status['unique_records'], status['record_events'], status['field_edits']), (1, 3, 3))
        self.assertEqual(data.field_scope(self.path, self.cfg, []), [])

    def test_content_categories_item_operations_and_description_size(self):
        categories = data.content_categories(self.cfg)
        self.assertEqual(data.content_category('$.descriptions', categories)['key'], 'descriptions')
        self.assertEqual(data.content_category('$.proliferations[]', categories)['key'], 'metadata')
        original = {
            'id': 10, 'nomenclature': 'Categorised', 'systemGroup': 'Sensors', 'systemType': 'Radar', 'modelOwner': 'North',
            'status': 'draft',
            'descriptions': [{'descrType': 'Overview', 'description': 'original words', 'classification': 'U'}],
            'parametrics': [
                {'component': None, 'parameter': 'Range', 'parameterValue': '10', 'uom': 'km'},
                {'component': None, 'parameter': 'Weight', 'parameterValue': '20', 'uom': 'kg'},
            ],
            'relations': [{'childModelID': 1, 'relationType': 'CHILD', 'parentComponent': 'Sensors', 'childModel': 'Old'}],
            'media': [{'mediaID': 1, 'title': 'Old title', 'url': 'https://example.test/1'}],
            'proliferations': [{'country': 'AU', 'proliferation': 'Using', 'organization': 'Unit', 'region': 'Old'}],
        }
        self.write('10.json', original)
        self.run_snapshot()
        changed = {**original, 'status': 'published'}
        changed['descriptions'] = [{**original['descriptions'][0], 'description': 'a short revised description'}]
        changed['parametrics'] = [
            {**original['parametrics'][0], 'parameterValue': '15'},
            {'component': None, 'parameter': 'Speed', 'parameterValue': '30', 'uom': 'km/h'},
        ]
        changed['relations'] = original['relations'] + [
            {'childModelID': 2, 'relationType': 'CHILD', 'parentComponent': 'Sensors', 'childModel': 'New'}]
        changed['media'] = [{**original['media'][0], 'title': 'New title'}]
        changed['proliferations'] = [{**original['proliferations'][0], 'region': 'New'}]
        self.write('10.json', changed)
        self.run_snapshot()
        path = self.args.history / 'history.sqlite3'
        _, events = data.period_data(path, self.cfg, 1, 2)
        details = data.content_change_details(path, self.cfg, data.meaningful(events))
        descriptions = [row for row in details if row['content_key'] == 'descriptions']
        self.assertEqual([(row['operation'], row['magnitude']) for row in descriptions], [('modified', 'Small')])
        parameters = [row['operation'] for row in details if row['content_key'] == 'parameters']
        self.assertEqual(sorted(parameters), ['added', 'modified', 'removed'])
        self.assertEqual([row['operation'] for row in details if row['content_key'] == 'relationships'], ['added'])
        self.assertEqual([row['operation'] for row in details if row['content_key'] == 'media'], ['modified'])
        metadata_paths = {row['item_path'] for row in details if row['content_key'] == 'metadata'}
        self.assertIn('$.status', metadata_paths)
        self.assertTrue(any(path.startswith('$.proliferations[') for path in metadata_paths))
        summary = {row['content_category']: row for row in data.content_change_summary(details)}
        self.assertEqual((summary['Parameters']['added'], summary['Parameters']['removed'], summary['Parameters']['modified']), (1, 1, 1))
        self.assertEqual(summary['Descriptions']['small_description_edits'], 1)
        combined = data.combined_update_sizes(details, self.cfg)
        self.assertEqual(len(combined), 1)
        self.assertEqual((combined[0]['overall_update_size'], combined[0]['change_units']), ('Medium', 8))
        self.assertEqual((combined[0]['parameters_changes'], combined[0]['relationships_changes'],
                          combined[0]['media_changes']), (3, 1, 1))
        self.assertEqual(data.combined_update_sizes([next(row for row in details if row['content_key'] == 'media')], self.cfg)[0]['overall_update_size'], 'Small')
        large_description = {**descriptions[0], 'word_edits': 251}
        self.assertEqual(data.combined_update_sizes([large_description], self.cfg)[0]['overall_update_size'], 'Large')

    def test_direct_comparison_reversion_removal_and_unchanged_timeline(self):
        self.history()
        before, after, diffs, compatible = data.comparison(self.path, 'id:1', 1, 4)
        self.assertEqual(before, after)
        self.assertEqual(diffs, [])
        self.assertTrue(compatible)
        _, absent, diffs, _ = data.comparison(self.path, 'id:2', 1, 2)
        self.assertIs(absent, data.ABSENT)
        self.assertTrue(all(r['kind'] == 'removed' for r in diffs))
        self.assertFalse(data.comparison(self.path, 'id:missing', 1, 4)[2])
        timeline = data.record_timeline(self.path, self.cfg, 'id:4')
        self.assertEqual([r['kind'] for r in timeline], ['unchanged', 'unchanged', 'unchanged', 'added'])

    def test_record_search_pagination_owner_and_safe_highlighting(self):
        self.history()
        filters = {key: sorted({r[key] for r in self.totals}) for key in data.DIMENSIONS.values()}
        total, first = data.record_page(self.path, self.cfg, 4, filters, size=2)
        _, second = data.record_page(self.path, self.cfg, 4, filters, page=1, size=2)
        self.assertEqual(total, 3)
        self.assertEqual(len(first), 2)
        self.assertEqual(len(second), 1)
        self.assertEqual(data.record_page(self.path, self.cfg, 4, filters, 'Example One')[0], 1)
        filters['model_owner'] = ['South']
        self.assertEqual(data.record_page(self.path, self.cfg, 4, filters)[0], 0)
        left, right = data.word_diff('<script>alert(1)</script>', '<b>safe</b>')
        self.assertNotIn('<script>', left)
        self.assertNotIn('<b>', right)
        self.assertIn('&lt;', right)

    def test_root_null_is_distinct_from_absent_record(self):
        self.write('null.json', None)
        self.run_snapshot()
        path = self.args.history / 'history.sqlite3'
        before, after, diffs, _ = data.comparison(path, 'file:null.json', None, 1)
        self.assertIs(before, data.ABSENT)
        self.assertIsNone(after)
        self.assertEqual(diffs, [{'field': '$', 'before': '(absent)', 'after': 'null', 'kind': 'added'}])

    def app(self):
        from streamlit.testing.v1 import AppTest
        code = (v.ROOT / 'dashboard.py').read_text(encoding='utf-8')
        code = code.replace('ROOT = Path(__file__).resolve().parent', f'ROOT = Path({str(self.root)!r})')
        code = code.replace("ROOT / 'json_history'", "ROOT / 'history'")
        script = self.root / 'dashboard_test.py'
        script.write_text(code, encoding='utf-8')
        return AppTest.from_file(str(script)).run(timeout=30)

    def check_app(self, app):
        self.assertFalse(app.exception, [e.message for e in app.exception])
        self.assertFalse(app.error, [e.value for e in app.error])

    def test_navigation_owner_filters_drilldown_and_record_comparison(self):
        self.history()
        app = self.app()
        self.check_app(app)
        self.assertEqual([m.value for m in app.metric], ['3', '4', '6', '3'])
        owners = next(w for w in app.sidebar.multiselect if w.label == 'Model owners')
        owners.set_value(['South']).run(timeout=30)
        self.check_app(app)
        self.assertEqual([m.value for m in app.metric], ['0', '2', '3', '2'])
        next(b for b in app.sidebar.button if b.label == 'Reset filters').click().run(timeout=30)
        self.assertEqual(app.metric[0].value, '3')
        next(w for w in app.selectbox if w.label == 'Inspect category').set_value('Radar')
        next(b for b in app.button if b.label == 'Explore changes in this category').click().run(timeout=30)
        self.check_app(app)
        self.assertEqual(app.sidebar.radio[0].value, 'Explore records')
        self.assertEqual(next(w for w in app.sidebar.multiselect if w.label == 'System types').value, ['Radar'])
        self.assertTrue(any(s.label == 'Inspect changed field' for s in app.selectbox))
        next(w for w in app.radio if w.label == 'Explore').set_value('All records at an observation').run(timeout=30)
        self.check_app(app)
        app.sidebar.radio[0].set_value('When changes happened').run(timeout=30)
        self.check_app(app)
        next(w for w in app.radio if w.label == 'Time basis').set_value('Estimated').run(timeout=30)
        self.check_app(app)
        app.sidebar.radio[0].set_value('History health').run(timeout=30)
        self.check_app(app)
        app.sidebar.radio[0].set_value('Where changes are happening').run(timeout=30)
        self.check_app(app)
        self.assertEqual(next(w for w in app.sidebar.multiselect if w.label == 'System types').value, ['Radar'])


if __name__ == '__main__':
    unittest.main()
