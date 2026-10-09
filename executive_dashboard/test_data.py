import unittest
from datetime import date

import pandas as pd

from executive_dashboard import data


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

    def test_parameter_detail_prefers_combined_value_and_keeps_annotations(self):
        prefix = '$.parametrics[component=null,parameter=Range].'
        rows = [{'json_path': prefix + leaf, 'old_value': 'a', 'new_value': 'b', 'word_edits': None}
                for leaf in ['parameter', 'parameterDescr', 'parameterValue', 'uom',
                             'parameterUomValue', 'comments', 'valueSubtitle']]
        shown = data.minimise_parameter_fields(rows)
        leaves = [row['json_path'].rsplit('.', 1)[-1] for row in shown]
        self.assertEqual(leaves, ['comments', 'parameterUomValue', 'valueSubtitle'])

    def test_semantic_size_uses_logical_item_count(self):
        semantic = {(2, 'A'): {'overall_update_size': 'Medium', 'item_changes': 3}}
        row = data.change_rows([self.event(fields_changed=12)], {}, semantic_sizes=semantic)[0]
        self.assertEqual((row['size'], row['description']), ('Medium', '3 items edited'))

    def test_parameter_path_has_natural_display_label(self):
        row = {'json_path': '$.parametrics[component=%22Guidance%22,parameter=%22Guidance%20system%22].comments',
               'old_value': 'a', 'new_value': 'b', 'word_edits': None}
        shown = data.field_display(row)
        self.assertEqual(shown['label'], 'Guidance · Guidance system — Comment')
        self.assertEqual((shown['component'], shown['parameter'], shown['detail']),
                         ('Guidance', 'Guidance system', 'Comment'))

    def test_description_path_uses_selector_as_label(self):
        row = {'json_path': '$.descriptions[descrType=%22Overview%22].shortDescription',
               'old_value': 'a', 'new_value': 'b', 'word_edits': None}
        self.assertEqual(data.field_display(row)['label'], 'Overview · Short description')

    def test_inline_word_diff_is_safe_and_tracks_changes(self):
        shown = data.inline_word_diff('A <short> description', 'A clearer description')
        self.assertIn('<del>&lt;short&gt;</del>', shown)
        self.assertIn('<ins>clearer</ins>', shown)
        self.assertNotIn('<short>', shown)

    def test_dashboard_display_exclusions_are_opt_out_by_category(self):
        rows = [
            {'json_path': '$.parametrics[component=null,parameter=Range].parameterValue', 'old_value': '1', 'new_value': '2', 'word_edits': None},
            {'json_path': '$.parametrics[component=null,parameter=Range].comments', 'old_value': 'a', 'new_value': 'b', 'word_edits': None},
            {'json_path': '$.descriptions[descrType=Overview].classification', 'old_value': 'U', 'new_value': 'S', 'word_edits': None},
            {'json_path': '$.status', 'old_value': 'A', 'new_value': 'B', 'word_edits': None},
        ]
        cfg = {'dashboard_display': {'exclude_parameter_fields': ['comments'],
                                     'exclude_description_fields': ['classification'],
                                     'exclude_other_paths': ['$.status']}}
        shown = data.minimise_parameter_fields(rows, cfg)
        self.assertEqual([row['json_path'] for row in shown],
                         ['$.parametrics[component=null,parameter=Range].parameterValue'])


if __name__ == '__main__':
    unittest.main()
