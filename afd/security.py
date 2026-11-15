"""Prevent known credential formats or configured secret values from entering persisted data."""
import json
import os
import re
PATTERN=re.compile(r'(?:github_pat_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|dapi[a-fA-F0-9]{20,}|tvly-[A-Za-z0-9_-]{20,}|AKIA[A-Z0-9]{16})')

def reject_secrets(value):
    text=json.dumps(value,ensure_ascii=False)
    if PATTERN.search(text): raise ValueError('Credential-like content is not accepted')
    for key,secret in os.environ.items():
        if key.endswith(('_TOKEN','_KEY','_SECRET')) and len(secret)>=12 and secret in text:
            raise ValueError('Configured secret value is not accepted in application data')
