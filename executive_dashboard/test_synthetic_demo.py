import copy
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

from comparison import digest, normalize
from configuration import load_config
from executive_dashboard.demo import build_synthetic_demo as generator


class SyntheticDemoTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.source = Path(temporary.name) / 'templates'
        self.source.mkdir()
        for model_id, related_id in ((10, 11), (11, 10)):
            record = {
                'modelID': model_id,
                'nomenclature': f'Template {model_id}',
                'parametrics': [{'parameter': 'Range', 'parameterValue': '10', 'uom': 'km'}],
                'descriptions': [{'shortDescription': 'Summary', 'description': 'Details'}],
                'media': [{'mediaID': model_id}],
                'relations': [{'parentModelID': model_id, 'childModelID': related_id}],
            }
            (self.source / f'{model_id}.json').write_text(json.dumps(record), encoding='utf-8')

    def test_clones_have_unique_ids_and_internal_relationships(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / 'records'
            provenance = generator.clone_records(self.source, destination,
                                                 50, dt.date(2026, 3, 31))
            records = [json.loads(path.read_text(encoding='utf-8')) for path in destination.glob('*.json')]
            ids = {record['modelID'] for record in records}
            self.assertEqual(len(ids), 50)
            self.assertEqual(len(provenance), 50)
            self.assertEqual(len({record['nomenclature'] for record in records}), 50)
            media_ids = [item['mediaID'] for record in records for item in record.get('media', [])]
            self.assertEqual(len(media_ids), len(set(media_ids)))
            for record in records:
                for relation in record.get('relations', []):
                    for field in ('parentModelID', 'childModelID'):
                        if field in relation:
                            self.assertIn(relation[field], ids)

    def test_every_edit_kind_is_a_meaningful_change(self):
        cfg = load_config(generator.ROOT / 'config.json')
        templates = generator.templates(self.source)
        for _, template in templates:
            for kind in ('parameter', 'parameter_bundle', 'description', 'large_description', 'metadata'):
                record = copy.deepcopy(template)
                before = digest(normalize(record, cfg))
                generator.edit_record(record, kind, 1)
                self.assertNotEqual(before, digest(normalize(record, cfg)), (template['modelID'], kind))


if __name__ == '__main__':
    unittest.main()
