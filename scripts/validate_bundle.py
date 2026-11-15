"""Validate deployment YAML against the installed CLI schema, entirely offline."""
import json
import subprocess
from pathlib import Path

import jsonschema
import yaml

ROOT = Path(__file__).resolve().parent.parent
schema = json.loads(subprocess.check_output(['databricks', 'bundle', 'schema'], text=True, cwd=ROOT))


def translate_patterns(obj):
    # The CLI emits Go regex Unicode categories. This bundle uses ASCII identifiers.
    if isinstance(obj, dict):
        if 'pattern' in obj:
            obj['pattern'] = obj['pattern'].replace(r'[\p{L}\p{N}]', '[A-Za-z0-9]').replace(r'\p{L}', '[A-Za-z]').replace(r'\p{N}', '[0-9]')
        for value in obj.values():
            translate_patterns(value)
    elif isinstance(obj, list):
        for value in obj:
            translate_patterns(value)


translate_patterns(schema)
configuration = yaml.safe_load((ROOT / 'databricks.yml').read_text())
errors = sorted(jsonschema.Draft202012Validator(schema).iter_errors(configuration), key=lambda e: str(list(e.absolute_path)))
for error in errors:
    print(f'{list(error.absolute_path)}: {error.message}')
if errors:
    raise SystemExit(1)
print('PASS: Databricks bundle matches the installed CLI schema (offline; workspace resources not checked).')
