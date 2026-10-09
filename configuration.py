"""Independent defaults and validation for capture and dashboard configuration."""
from __future__ import annotations

import json
import math
from pathlib import Path

from comparison import array_key_fields

DEFAULT_PATH = Path(__file__).with_name('default_config.json')


DEFAULT_CONTENT_CATEGORIES = [
    {'key': 'descriptions', 'label': 'Descriptions', 'paths': ['$.descriptions[]']},
    {'key': 'parameters', 'label': 'Parameters', 'paths': ['$.parametrics[]']},
    {'key': 'relationships', 'label': 'Relationships', 'paths': ['$.relations[]']},
    {'key': 'media', 'label': 'Media', 'paths': ['$.media[]']},
    {'key': 'metadata', 'label': 'Metadata', 'fallback': True},
]


def content_categories(cfg):
    categories = cfg.get('content_categories', DEFAULT_CONTENT_CATEGORIES)
    if not isinstance(categories, list) or not categories:
        raise ValueError('content_categories must be a non-empty list')
    result = []
    seen = set()
    fallback = None
    for category in categories:
        if not isinstance(category, dict):
            raise ValueError('content_categories must contain objects')
        key, label = category.get('key'), category.get('label')
        if not isinstance(key, str) or not key or key in seen or not isinstance(label, str) or not label:
            raise ValueError('Each content category needs a unique non-empty key and label')
        seen.add(key)
        paths = category.get('paths', [])
        if not isinstance(paths, list) or not all(isinstance(item, str) and item.startswith('$.') for item in paths):
            raise ValueError(f'Content category {key!r} has invalid paths')
        item = {'key': key, 'label': label, 'paths': paths, 'fallback': bool(category.get('fallback'))}
        if item['fallback']:
            if fallback is not None:
                raise ValueError('Only one content category may be the fallback')
            fallback = item
        else:
            result.append(item)
    if fallback is None:
        fallback = {'key': 'other', 'label': 'Other', 'paths': [], 'fallback': True}
    return result + [fallback]


def validate_config(cfg):
    for key in ('id_fields', 'group_fields', 'type_fields', 'owner_fields', 'name_fields',
                'updated_at_fields', 'ignore_fields', 'ignore_paths', 'unordered_arrays'):
        values = cfg[key]
        if not isinstance(values, list) or not all(isinstance(v, str) and v for v in values):
            raise ValueError(f'{key} must be a list of non-empty strings')
    if not cfg['id_fields']:
        raise ValueError('id_fields must contain at least one field')
    for key in ('array_keys', 'important_fields', 'change_thresholds', 'dashboard_display',
                'description_edit_thresholds', 'combined_update_size'):
        if not isinstance(cfg[key], dict):
            raise ValueError(f'{key} must be an object')
    for path, spec in cfg['array_keys'].items():
        if not isinstance(path, str) or not path.startswith('$'):
            raise ValueError('array_keys paths must start with $')
        array_key_fields(spec)
    for path, value in cfg['important_fields'].items():
        _number(value, f'important_fields[{path}]', positive=True)
    fraction = _number(cfg['schema_field_fraction'], 'schema_field_fraction')
    if not 0 < fraction <= 1:
        raise ValueError('schema_field_fraction must be greater than 0 and at most 1')
    for key, low, high in (('change_thresholds', 'small', 'medium'),
                           ('description_edit_thresholds', 'small', 'medium'),
                           ('combined_update_size', 'small_max', 'medium_max')):
        values = cfg[key]
        if low not in values or high not in values:
            raise ValueError(f'{key} needs {low} and {high}')
        a, b = (_number(values[name], f'{key}.{name}') for name in (low, high))
        if not 0 <= a <= b or (key == 'change_thresholds' and b > 1):
            raise ValueError(f'{key} thresholds must be non-negative and ordered')
    for key, values in cfg['dashboard_display'].items():
        if key.startswith('exclude_') and (not isinstance(values, list) or
                not all(isinstance(v, str) for v in values)):
            raise ValueError(f'dashboard_display.{key} must be a list of strings')
    phrases = cfg.get('unavailable_phrases', [])
    if not isinstance(phrases, str) and (not isinstance(phrases, list) or
            not all(isinstance(v, str) for v in phrases)):
        raise ValueError('unavailable_phrases must be a string or list of strings')
    if not isinstance(cfg['url_prefix'], str):
        raise ValueError('url_prefix must be a string')
    if cfg['invalid_json'] not in ('abort', 'skip'):
        raise ValueError('invalid_json must be abort or skip')
    if not isinstance(cfg['content_categories'], list) or not all(
            isinstance(item, dict) for item in cfg['content_categories']):
        raise ValueError('content_categories must be a list of objects')
    content_categories(cfg)
    return cfg


def _number(value, name, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f'{name} must be a finite number')
    if positive and value <= 0:
        raise ValueError(f'{name} must be positive')
    return value


def configuration(values):
    """Merge top-level overrides; explicit empty lists/maps remain meaningful."""
    if not isinstance(values, dict):
        raise ValueError('Configuration must be a JSON object')
    defaults = json.loads(DEFAULT_PATH.read_text(encoding='utf-8'))
    return validate_config({**defaults, **values})


def load_config(path, *, create=False):
    path = Path(path)
    if create and not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(DEFAULT_PATH.read_text(encoding='utf-8'), encoding='utf-8')
        print(f'Created {path}')
    return configuration(json.loads(path.read_text(encoding='utf-8-sig')))
