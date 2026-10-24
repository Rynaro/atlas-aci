"""Read-only S10 executor re-entry: verify identity, products and output bytes."""

import argparse
import hashlib
import json
import subprocess
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--receipt', type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    receipt = json.loads(args.receipt.read_text())
    assert receipt['slice_id'] == 'ACI-S10'
    criteria = root / '.spectra/plans/aci-s10.criteria.md'
    criteria_sha = hashlib.sha256(criteria.read_bytes()).hexdigest()
    state = json.loads((root / '.spectra/plans/aci-s10.state.json').read_text())
    assert criteria_sha == state['criteria_sha256'] == receipt['criteria_sha256']
    assert receipt['status'] in {'IN_PROGRESS', 'COMPLETE', 'ALREADY_SATISFIED'}
    completed = {r['ac_id'] for r in receipt['acceptance_results'] if r['exit_code'] == 0}
    assert {f'AC-S10-{i:03d}' for i in range(1, 7)} <= completed
    expected_scope = list(state['declared_scope'])
    for name in receipt.get('scope_amendments', []):
        amendment = json.loads((root / name).read_text())
        assert amendment.get('slice_id', amendment.get('parent_slice')) == receipt['slice_id']
        assert amendment.get('criteria_changed', False) is False
        expected_scope.extend(amendment['additional_paths'])
    assert receipt['declared_paths'] == expected_scope
    exclusions = json.loads((root / '.spectra/changes/aci-s10/bookkeeping-amendment.json').read_text())['output_digest_exclusions']
    assert exclusions == receipt['output_digest_exclusions']
    paths = receipt['declared_paths']
    names = subprocess.check_output(['git', '-C', str(root), 'ls-files', '-z', '--', *paths]).decode().split('\0')
    actual = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in sorted(filter(None, names)) if name not in exclusions}
    assert actual == receipt['output_digests'], 'Tracked implementation digest mismatch'
    subprocess.run(['git', '-C', str(root), 'diff', '--exit-code', receipt['output_commit'], '--', *paths, *[':(exclude)' + name for name in exclusions]], check=True)
    assert not subprocess.check_output(['git', '-C', str(root), 'status', '--porcelain']), 'Re-entry checkout must be clean'
    if receipt['status'] in {'COMPLETE', 'ALREADY_SATISFIED'}:
        assert {'AC-S10-RUN-01', 'AC-S10-RUN-02'} <= completed
    print(json.dumps({'slice_id': receipt['slice_id'], 'criteria_sha256': criteria_sha,
                      'output_commit': receipt['output_commit'], 'output_digests': actual,
                      'status': 'ALREADY_SATISFIED' if receipt['status'] != 'IN_PROGRESS' else 'PRODUCTS_VERIFIED'}, sort_keys=True))


if __name__ == '__main__':
    main()
