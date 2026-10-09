"""Regression tests: python -m unittest -v"""
import argparse
import contextlib
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

import storage
import version_json as v


class VersionerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        self.cfg = json.loads((v.ROOT / 'config.json').read_text())
        self.config = self.root / 'config.json'
        self.config.write_text(json.dumps(self.cfg))
        self.args = argparse.Namespace(source=self.source, history=self.root / 'history',
            config=self.config, note='', dry_run=False, url_template=None, column=None, timeout=1, url_prefix=None)

    def write(self, name, value):
        (self.source / name).write_text(json.dumps(value), encoding='utf-8')

    def run_snapshot(self):
        with contextlib.redirect_stdout(io.StringIO()):
            v.run(self.args)

    def connect(self):
        db = sqlite3.connect(self.args.history / 'history.sqlite3')
        self.addCleanup(db.close)
        return db

    def test_dedupe_revert_removal_and_classification(self):
        original = {'modelID': 1, 'systemGroup': 'Air', 'description': 'repeat ' * 3000}
        self.write('a.json', original)
        self.run_snapshot()
        self.run_snapshot()
        self.write('a.json', {**original, 'description': 'changed'})
        self.run_snapshot()
        self.write('a.json', original)
        self.run_snapshot()
        (self.source / 'a.json').unlink()
        self.run_snapshot()
        db = self.connect()
        self.assertEqual(db.execute('SELECT count(*) FROM record_blobs').fetchone()[0], 2)
        self.assertEqual(db.execute('SELECT count(*) FROM records WHERE json_text IS NOT NULL').fetchone()[0], 0)
        self.assertEqual(db.execute('SELECT added,modified,removed,unchanged FROM snapshots ORDER BY id').fetchall(),
                         [(1, 0, 0, 0), (0, 0, 0, 1), (0, 1, 0, 0), (0, 1, 0, 0), (0, 0, 1, 0)])
        raw, packed = db.execute('SELECT SUM(raw_bytes),SUM(length(payload)) FROM record_blobs').fetchone()
        self.assertLess(packed, raw / 5)
        storage.dashboard_records(db, ['systemGroup', 'description'])
        rows = db.execute('SELECT json_text FROM display_records WHERE snapshot_id=1').fetchall()
        self.assertEqual(json.loads(rows[0][0]), {'systemGroup': 'Air', 'description': original['description']})

    def test_empty_containers_and_json_types(self):
        for old, new in [(True, 1), (1, 1.0), ({'x': True}, {'x': 1}), ({}, {'x': []}), ({'x': {}}, {})]:
            self.assertTrue(list(v.changes(old, new, self.cfg)), (old, new))
        self.assertEqual(list(v.leaves({'x': []})), [('$.x', [])])

    def test_composite_array_keys_match_items_and_encode_paths(self):
        cfg = {**self.cfg, 'array_keys': {'$.items': ['component', 'name']}}
        old = {'items': [
            {'component': None, 'name': 'A] one', 'value': 1},
            {'component': 'B', 'name': 'Two', 'value': 2},
        ]}
        reordered = {'items': list(reversed(old['items']))}
        self.assertEqual(list(v.changes(old, reordered, cfg)), [])
        changed = {'items': [old['items'][0], {**old['items'][1], 'value': 3}]}
        diffs = list(v.changes(old, changed, cfg))
        self.assertEqual(len(diffs), 1)
        self.assertIn('component=%22B%22,name=%22Two%22', diffs[0][0])
        self.assertNotIn('A] one', diffs[0][0])

    def test_schema_empty_field_and_settings_guard(self):
        for i in range(2):
            self.write(f'{i}.json', {'modelID': i})
        self.run_snapshot()
        for i in range(2):
            self.write(f'{i}.json', {'modelID': i, 'empty': {}})
        self.run_snapshot()
        db = self.connect()
        self.assertEqual(db.execute('SELECT modified,schema_only FROM snapshots WHERE id=2').fetchone(), (0, 2))
        self.cfg['ignore_fields'].append('empty')
        self.config.write_text(json.dumps(self.cfg))
        with self.assertRaisesRegex(ValueError, 'Comparison settings changed'):
            self.run_snapshot()
        self.assertEqual(db.execute('SELECT count(*) FROM snapshots').fetchone()[0], 2)

    def test_invalid_input_does_not_commit(self):
        self.write('a.json', {'id': 1})
        self.run_snapshot()
        (self.source / 'a.json').write_text('{invalid')
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(ValueError):
            self.run_snapshot()
        self.assertEqual(self.connect().execute('SELECT count(*) FROM snapshots').fetchone()[0], 1)

    def test_compaction_and_legacy_dashboard(self):
        self.write('a.json', {'id': 1, 'systemGroup': 'Air'})
        self.run_snapshot()
        self.run_snapshot()
        db = self.connect()
        # Recreate the legacy representation, including repeated full payloads.
        hash_, payload = db.execute('SELECT hash,payload FROM record_blobs').fetchone()
        text = storage.unpack(payload)
        db.execute('UPDATE records SET json_text=?', (text,))
        db.execute('DROP TABLE record_blobs')
        db.commit()
        storage.dashboard_records(db, ['systemGroup'])
        self.assertEqual(json.loads(db.execute('SELECT json_text FROM display_records LIMIT 1').fetchone()[0])['systemGroup'], 'Air')
        db.execute('DROP VIEW display_records')
        migrated = v.database(self.args.history / 'history.sqlite3')
        try:
            backup = storage.compact_storage(migrated, self.args.history / 'history.sqlite3')
            self.assertTrue(backup.exists())
            self.assertEqual(migrated.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(migrated.execute('SELECT count(*) FROM record_blobs').fetchone()[0], 1)
            self.assertEqual(storage.record_text(migrated, hash_), text)
            with contextlib.closing(sqlite3.connect(backup)) as original:
                self.assertEqual(original.execute('SELECT count(*) FROM records WHERE json_text IS NOT NULL').fetchone()[0], 2)
        finally:
            migrated.close()
        self.run_snapshot()

    def test_compaction_failure_rolls_back(self):
        self.write('a.json', {'id': 1})
        self.run_snapshot()
        db = self.connect()
        payload = db.execute('SELECT payload FROM record_blobs').fetchone()[0]
        db.execute('UPDATE records SET json_text=?', (storage.unpack(payload),))
        db.commit()
        with patch('storage.put_blob', side_effect=ValueError('injected failure')):
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(ValueError, 'injected'):
                storage.compact_storage(db, self.args.history / 'history.sqlite3')
        self.assertIsNotNone(db.execute('SELECT json_text FROM records').fetchone()[0])

    def test_https_enforced_and_tls_verification(self):
        with v.https_session() as session:
            with self.assertRaisesRegex(ValueError, 'HTTPS'):
                session.get('http://example.test/data')
            response = requests.Response()
            response.status_code = 200
            response._content = b'{}'
            with patch.object(requests.Session, 'send', return_value=response) as send:
                session.get('https://example.test/data')
                self.assertTrue(send.call_args.kwargs['verify'])
            adapter = session.get_adapter('https://example.test')
            self.assertEqual(adapter.max_retries.total, 3)
            self.assertEqual(adapter.max_retries.allowed_methods, {'GET'})

    def test_https_downgrade_redirect_blocked(self):
        with v.https_session() as session:
            response = requests.Response()
            response.status_code = 302
            response.headers['Location'] = 'http://example.test/plaintext'
            response.url = 'https://example.test/start'
            response._content = b''
            response.request = requests.Request('GET', response.url).prepare()
            adapter = session.get_adapter(response.url)
            with patch.object(adapter, 'send', return_value=response) as send:
                with self.assertRaisesRegex(ValueError, 'HTTPS'):
                    session.get(response.url)
                self.assertEqual(send.call_count, 1)

    def test_url_session_reuse_and_errors(self):
        source = self.root / 'urls.txt'
        source.write_text('https://example.test/1\nhttps://example.test/2')
        session = Mock()
        responses = []
        for i in [1, 2]:
            response = Mock()
            response.iter_content.return_value = [json.dumps({'id': i}).encode()]
            manager = Mock()
            manager.__enter__ = Mock(return_value=response)
            manager.__exit__ = Mock(return_value=False)
            responses.append(manager)
        session.get.side_effect = responses
        result, errors = v.scan_urls(source, self.cfg, None, None, 1, session=session)
        self.assertEqual(len(result), 2)
        self.assertFalse(errors)
        self.assertEqual(session.get.call_count, 2)
        session.get.side_effect = requests.exceptions.SSLError('untrusted certificate')
        result, errors = v.scan_urls(source, self.cfg, None, None, 1, session=session)
        self.assertEqual(len(errors), 2)
        self.assertFalse(result)

    def test_source_owner_from_text_and_excel_overrides_response(self):
        text = self.root / 'models.txt'
        text.write_text('1004 North Team\n1005,South Team', encoding='utf-8')
        self.assertEqual(v.source_entries(text, None),
                         [('1', '1004', 'North Team'), ('2', '1005', 'South Team')])

        from openpyxl import Workbook
        book = Workbook()
        sheet = book.active
        sheet.append(['modelID', 'Model Owner'])
        sheet.append([1004, 'North Team'])
        excel = self.root / 'models.xlsx'
        book.save(excel)
        book.close()
        self.assertEqual(v.source_entries(excel, None), [('2', '1004', 'North Team')])

        response = Mock(status_code=200)
        response.iter_content.return_value = [json.dumps(
            {'modelID': 1004, 'modelOwner': 'Owner from JSON'}).encode()]
        manager = Mock()
        manager.__enter__ = Mock(return_value=response)
        manager.__exit__ = Mock(return_value=False)
        session = Mock()
        session.get.return_value = manager
        result, errors = v.scan_urls(excel, self.cfg, None, None, 1,
                                     prefix='https://example.test/models/', session=session)
        self.assertFalse(errors)
        self.assertEqual(result['modelID:1004'][1]['modelOwner'], 'North Team')

    def test_unavailable_response_is_recorded_as_second_capture_gap(self):
        source = self.root / 'models.txt'
        source.write_text('1004\tNorth Team', encoding='utf-8')
        self.args.source = source
        self.cfg['url_prefix'] = 'https://example.test/models/'
        self.cfg['unavailable_phrases'] = ['Not available now']
        self.config.write_text(json.dumps(self.cfg), encoding='utf-8')

        def capture(payload):
            response = Mock(status_code=200)
            response.raw.retries.history = ()
            response.iter_content.return_value = [json.dumps(payload).encode()]
            request = Mock()
            request.__enter__ = Mock(return_value=response)
            request.__exit__ = Mock(return_value=False)
            session = Mock()
            session.get.return_value = request
            context = Mock()
            context.__enter__ = Mock(return_value=session)
            context.__exit__ = Mock(return_value=False)
            with patch('version_json.https_session', return_value=context):
                self.run_snapshot()

        capture({'modelID': 1004, 'modelOwner': 'Wrong JSON owner', 'value': 1})
        capture({'anyKey': 'NOT AVAILABLE NOW'})
        capture({'NOT AVAILABLE NOW': True})
        capture({'modelID': 1004, 'modelOwner': 'Still wrong', 'value': 2})

        db = self.connect()
        self.assertEqual(db.execute(
            'SELECT total,added,removed,modified FROM snapshots ORDER BY id').fetchall(),
            [(1, 1, 0, 0), (0, 0, 0, 0), (0, 0, 0, 0), (1, 0, 0, 1)])
        self.assertEqual(db.execute(
            'SELECT record_id,model_owner,reason FROM snapshot_gaps ORDER BY snapshot_id').fetchall(),
            [('modelID:1004', 'North Team', 'Not available now'),
             ('modelID:1004', 'North Team', 'Not available now')])
        self.assertEqual(db.execute('''SELECT old_value,new_value FROM field_changes
            WHERE snapshot_id=4 AND record_id='modelID:1004' AND json_path='$.value' ''').fetchone(),
            ('1', '2'))
        classification = json.loads(db.execute('SELECT classification_json FROM record_blobs').fetchone()[0])
        self.assertEqual(classification['modelOwner'], 'North Team')

        import dashboard_data as dashboard
        self.assertEqual([row['unavailable'] for row in dashboard.snapshots(
            self.args.history / 'history.sqlite3')], [0, 1, 1, 0])
        totals, events = dashboard.period_data(self.args.history / 'history.sqlite3', self.cfg, 1, 4)
        self.assertEqual(totals[0]['model_owner'], 'North Team')
        self.assertFalse(any(event['kind'] == 'removed' for event in events))
        modified = next(event for event in events if event['kind'] == 'modified')
        self.assertEqual(modified['model_owner'], 'North Team')
        self.assertEqual(modified['previous_id'], 3)
        self.assertEqual(modified['comparison_snapshot_id'], 1)
        self.assertEqual(modified['previous_model_owner'], 'North Team')
        from executive_dashboard import data as executive
        before, after = executive.record_versions(self.args.history / 'history.sqlite3', modified)
        self.assertEqual((before['value'], after['value']), (1, 2))
        self.assertEqual(db.execute('SELECT comparison_snapshot_id FROM record_changes WHERE snapshot_id=4').fetchone()[0], 1)
        # Existing histories lack the new column. Read-only readers infer the
        # predecessor from gaps without migrating or changing the database.
        db.execute('ALTER TABLE record_changes DROP COLUMN comparison_snapshot_id')
        db.commit()
        _, legacy_events = dashboard.period_data(self.args.history / 'history.sqlite3', self.cfg, 4, 4)
        legacy = legacy_events[0]
        self.assertEqual(legacy['comparison_snapshot_id'], 1)
        self.assertEqual(legacy['previous_model_owner'], 'North Team')
        before, after = executive.record_versions(self.args.history / 'history.sqlite3', legacy)
        self.assertEqual((before['value'], after['value']), (1, 2))

    def test_unavailable_phrase_matches_root_string_list_key_and_value(self):
        cfg = {**self.cfg, 'unavailable_phrases': 'Gone'}
        for value in ('GONE', ['gone'], {'Gone': None}, {'anyKey': 'gone'}):
            self.assertEqual(v.unavailable_reason(value, cfg), 'Gone')
        self.assertIsNone(v.unavailable_reason({'nested': {'value': 'gone'}}, cfg))

    def test_url_progress_and_benchmark_timings(self):
        source = self.root / 'urls.txt'
        source.write_text('\n'.join(f'https://example.test/{i}' for i in range(12)))
        session = Mock()
        responses = []
        for i in range(12):
            response = Mock(status_code=200)
            response.raw.retries.history = ()
            response.iter_content.return_value = [json.dumps({'id': i}).encode()]
            manager = Mock()
            manager.__enter__ = Mock(return_value=response)
            manager.__exit__ = Mock(return_value=False)
            responses.append(manager)
        session.get.side_effect = responses
        timings = []
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result, errors = v.scan_urls(source, self.cfg, None, None, 1, session=session, timings=timings)
        self.assertFalse(errors)
        self.assertEqual((len(result), len(timings)), (12, 12))
        self.assertTrue(all(row['status'] == 200 and row['retries'] == 0 for row in timings))
        self.assertIn('Fetched 10/12 URLs', output.getvalue())
        self.assertIn('Fetched 12/12 URLs', output.getvalue())

    def test_benchmark_mode_is_sampled_and_read_only(self):
        source = self.root / 'urls.txt'
        source.write_text('https://example.test/1\nhttps://example.test/2')
        response = Mock(status_code=200)
        response.raw.retries.history = ()
        response.iter_content.return_value = [json.dumps({'id': 1}).encode()]
        request_manager = Mock()
        request_manager.__enter__ = Mock(return_value=response)
        request_manager.__exit__ = Mock(return_value=False)
        session = Mock()
        session.get.return_value = request_manager
        session_manager = Mock()
        session_manager.__enter__ = Mock(return_value=session)
        session_manager.__exit__ = Mock(return_value=False)
        args = argparse.Namespace(source=source, config=self.config, column=None, benchmark=1,
                                  url_template=None, url_prefix=None, timeout=1)
        output = io.StringIO()
        with patch('version_json.https_session', return_value=session_manager), contextlib.redirect_stdout(output):
            v.benchmark_urls(args)
        self.assertFalse((self.root / 'history').exists())
        self.assertIn('"sampled_records": 1', output.getvalue())
        self.assertIn('"https_sessions": 1', output.getvalue())

    def test_dashboard_compressed_single_snapshot_and_empty_filters(self):
        from streamlit.testing.v1 import AppTest
        self.write('a.json', {'id': 1, 'systemGroup': 'Air', 'systemType': 'Fighter'})
        self.run_snapshot()
        code = (v.ROOT / 'dashboard.py').read_text(encoding='utf-8')
        code = code.replace('ROOT = Path(__file__).resolve().parent', f'ROOT = Path({str(self.root)!r})')
        # The dashboard defaults to ROOT/json_history.
        code = code.replace("ROOT / 'json_history'", "ROOT / 'history'")
        script = self.root / 'dashboard_test.py'
        script.write_text(code, encoding='utf-8')
        app = AppTest.from_file(str(script)).run(timeout=30)
        self.assertFalse(app.exception)
        self.assertFalse(app.error, [e.value for e in app.error])
        self.assertTrue(app.metric, str(app))
        self.assertEqual(app.metric[0].value, '1')
        groups = next(widget for widget in app.sidebar.multiselect if widget.label == 'System groups')
        groups.set_value([]).run(timeout=30)
        self.assertFalse(app.exception)
        self.assertEqual(app.metric[0].value, '0')

    def test_snapshot_write_failure_rolls_back(self):
        self.write('a.json', {'id': 1})
        with patch('version_json.put_blob', side_effect=OSError('disk full')):
            with self.assertRaisesRegex(OSError, 'disk full'):
                self.run_snapshot()
        self.assertEqual(self.connect().execute('SELECT count(*) FROM snapshots').fetchone()[0], 0)

    def test_decompressed_response_limit(self):
        source = self.root / 'urls.txt'
        source.write_text('https://example.test/1')
        response = Mock()
        response.iter_content.return_value = (b'x' * 65536 for _ in range(306))
        session = Mock()
        manager = Mock()
        manager.__enter__ = Mock(return_value=response)
        manager.__exit__ = Mock(return_value=False)
        session.get.return_value = manager
        result, errors = v.scan_urls(source, self.cfg, None, None, 1, session=session)
        self.assertFalse(result)
        self.assertIn('after decompression', errors[0])


if __name__ == '__main__':
    unittest.main()
