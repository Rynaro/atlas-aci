"""Verify the preserved interrupted receipt and resumed authoritative identity."""
import hashlib
import json
from pathlib import Path

receipt_path = Path('.spectra/state/aci-s08.execution.json')
receipt = json.loads(receipt_path.read_text())
interrupted = json.loads(Path('.spectra/evidence/aci-s08/interrupted-execution.json').read_text())
assert interrupted['status'] == 'IN_PROGRESS'
for key in ('slice_id', 'criteria_sha256'):
    assert receipt[key] == interrupted[key]
assert hashlib.sha256(Path('.spectra/plans/aci-s08.criteria.md').read_bytes()).hexdigest() == receipt['criteria_sha256']
assert list(Path('.spectra/state').glob('aci-s08.execution.json')) == [receipt_path]
print(json.dumps({'slice_id': receipt['slice_id'], 'criteria_sha256': receipt['criteria_sha256'], 'authoritative_receipt': str(receipt_path), 'identity_preserved': True}, sort_keys=True))
