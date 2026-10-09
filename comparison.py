"""Semantic JSON normalization, paths, and comparison primitives."""
from __future__ import annotations

import difflib
import hashlib
import json
import re
from urllib.parse import quote, unquote

MISSING = object()
COMPARISON_KEYS = (
    "id_fields", "ignore_fields", "ignore_paths", "updated_at_fields",
    "unordered_arrays", "array_keys",
)


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def digest(value):
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


def path_matches(path: str, patterns: list[str]) -> bool:
    normalized = re.sub(r'\[[^]]*\]', '[]', path)
    return path in patterns or normalized in patterns


def normalize(value, cfg, path='$'):
    if isinstance(value, dict):
        return {k: normalize(v, cfg, f'{path}.{k}') for k, v in value.items()
                if k not in cfg['ignore_fields'] and not (path == '$' and k in cfg.get('updated_at_fields', []))
                and not path_matches(f'{path}.{k}', cfg['ignore_paths'])}
    if isinstance(value, list):
        result = [normalize(v, cfg, f'{path}[]') for v in value]
        if path_matches(path, cfg['unordered_arrays']):
            result.sort(key=canonical)
        return result
    return value


def leaves(value, path='$'):
    if isinstance(value, (dict, list)) and not value:
        yield path, value
    elif isinstance(value, dict):
        for key, child in value.items():
            yield from leaves(child, f'{path}.{key}')
    elif isinstance(value, list):
        for i, child in enumerate(value):
            yield from leaves(child, f'{path}[{i}]')
    else:
        yield path, value


def count_fields(value):
    return max(1, sum(1 for _ in leaves(value)))


def word_edits(old, new):
    a, b = re.findall(r"\b[\w'-]+\b", old), re.findall(r"\b[\w'-]+\b", new)
    return sum(max(i2-i1, j2-j1) for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b).get_opcodes() if tag != 'equal')


def array_key_fields(spec):
    """Return a validated list of fields from a string or composite key spec."""
    fields = [spec] if isinstance(spec, str) else spec
    if not isinstance(fields, list) or not fields or not all(isinstance(field, str) and field for field in fields):
        raise ValueError(f'array_keys values must be a field name or non-empty field-name list; got {spec!r}')
    return fields


def array_identity(item, fields):
    return tuple(canonical(item[field]) for field in fields)


def array_path(path, fields, identity):
    # Percent encoding keeps brackets and commas in source values from making
    # the human-readable JSON path ambiguous.
    parts = [f'{field}={quote(value, safe="")}' for field, value in zip(fields, identity)]
    return f'{path}[{",".join(parts)}]'


def changes(old, new, cfg, path='$'):
    if old is not MISSING and new is not MISSING and canonical(old) == canonical(new):
        return
    if isinstance(old, dict) and isinstance(new, dict):
        for key in sorted(old.keys() | new.keys()):
            yield from changes(old.get(key, MISSING), new.get(key, MISSING), cfg, f'{path}.{key}')
    elif isinstance(old, list) and isinstance(new, list):
        key_spec = cfg.get('array_keys', {}).get(path)
        if key_spec:
            keys = array_key_fields(key_spec)
        else:
            keys = []
        if keys and all(isinstance(x, dict) and all(key in x for key in keys) for x in old + new):
            a = {array_identity(x, keys): x for x in old}
            b = {array_identity(x, keys): x for x in new}
            if len(a) == len(old) and len(b) == len(new):
                for name in sorted(a.keys() | b.keys()):
                    yield from changes(a.get(name, MISSING), b.get(name, MISSING), cfg, array_path(path, keys, name))
                return
        for i in range(max(len(old), len(new))):
            yield from changes(old[i] if i < len(old) else MISSING,
                               new[i] if i < len(new) else MISSING, cfg, f'{path}[{i}]')
    else:
        # An object/array addition is expanded so new schema fields can be recognised.
        if old is MISSING and isinstance(new, (dict, list)):
            for leaf, val in leaves(new, path):
                yield leaf, MISSING, val
        elif new is MISSING and isinstance(old, (dict, list)):
            for leaf, val in leaves(old, path):
                yield leaf, val, MISSING
        else:
            yield path, old, new


def selector_values(selector):
    """Split selectors before decoding so punctuation in identities stays intact."""
    result = {}
    for part in selector.split(','):
        field, sep, encoded = part.partition('=')
        if not sep:
            continue
        decoded = unquote(encoded)
        try:
            result[field.strip()] = json.loads(decoded)
        except json.JSONDecodeError:
            result[field.strip()] = decoded
    return result
