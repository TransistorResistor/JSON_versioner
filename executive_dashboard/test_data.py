import unittest
from datetime import date

import pandas as pd

import data


class ExecutiveDataTests(unittest.TestCase):
    def event(self, **values):
        base = {'kind': 'modified', 'previous_id': 1, 'snapshot_id': 2, 'record_id': 'A',
                'record_name': 'Alpha', 'system_group': 'Sensors', 'system_type': 'Radar',
                'model_owner': 'North', 'weighted_ratio': .5, 'fields_changed': 3,
                'estimated_at': '2026-09-10T00:00:00Z', 'created_at': '2026-09-11T00:00:00Z'}
        base.update(values)
        return base

    def test_size_sort_and_date_filter(self):
        rows = data.change_rows([self.event(), self.event(record_id='B', weighted_ratio=.05,
            estimated_at='2026-09-20T00:00:00Z')], {'change_thresholds': {'small': .1, 'medium': .4}})
        self.assertEqual([row['size'] for row in data.sort_changes(rows, 'Largest first')], ['Large', 'Small'])
        filtered = data.filter_changes(rows, ['Sensors'], ['Radar'], ['North'], date(2026, 9, 15), date(2026, 9, 30))
        self.assertEqual([row['record_id'] for row in filtered], ['B'])

    def test_system_summary_contains_both_system_fields(self):
        rows = data.change_rows([self.event(), self.event(record_id='B', system_group='Weapons',
            system_type='Radar')], {})
        summary = data.system_summary(rows)
        self.assertEqual({(row['system_group'], row['system_type']) for row in summary},
                         {('Sensors', 'Radar'), ('Weapons', 'Radar')})

    def test_schema_only_events_are_not_record_changes(self):
        events = [self.event(kind='schema only')]
        self.assertEqual(data.change_rows(events, {}), [])


if __name__ == '__main__':
    unittest.main()
