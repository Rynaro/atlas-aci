"""Read-only S18 exact-output reentry: fixed identity and stable declared bytes."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--receipt', type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    receipt = json.loads(args.receipt.read_text())
    require(receipt['slice_id'] == 'ACI-S18', 'slice identity')
    criteria_sha = hashlib.sha256((root / '.spectra/plans/aci-s18.criteria.md').read_bytes()).hexdigest()
    state = json.loads((root / '.spectra/plans/aci-s18.state.json').read_text())
    require(criteria_sha == state['criteria_sha256'] == receipt['criteria_sha256'] == 'e4e84bc226ea5437c3f6931cd25acc7951f92ca7f701439a5b0baeea15ff88f6', 'frozen criteria')
    require(hashlib.sha256((root / '.spectra/plans/aci-s18.md').read_bytes()).hexdigest() == receipt['frozen_spec_sha256'] == '0a0ea3e005685affb5bf506ee159ebd89f891a0c3ad663c471656a8c753b3918', 'frozen plan')
    require(receipt['status'] in {'IN_PROGRESS', 'COMPLETE', 'ALREADY_SATISFIED'}, 'receipt state')
    completed = {r['ac_id'] for r in receipt['acceptance_results'] if r['exit_code'] == 0}
    require({f'AC-S18-{i:03d}' for i in range(1, 8)} <= completed, 'product acceptance incomplete')
    expected_scope = list(state['declared_scope'])
    for name in receipt.get('scope_amendments', []):
        amendment = json.loads((root / name).read_text())
        require(amendment.get('slice_id', amendment.get('parent_slice')) == receipt['slice_id'], 'amendment identity')
        require(amendment.get('criteria_changed', False) is False, 'criteria amended')
        for path in amendment.get('additional_paths', []):
            if path not in expected_scope:
                expected_scope.append(path)
    require(receipt['declared_paths'] == expected_scope, 'scope mismatch')
    exclusions = json.loads((root / '.spectra/changes/aci-s18/bookkeeping-amendment.json').read_text())['output_digest_exclusions']
    require(exclusions == receipt['output_digest_exclusions'] and len(exclusions) == 6, 'bookkeeping exclusions')
    paths = receipt['declared_paths']
    names = subprocess.check_output(['git', '-C', str(root), 'ls-files', '-z', '--', *paths]).decode().split('\0')
    actual = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in sorted(filter(None, names)) if name not in exclusions}
    require(actual == receipt['output_digests'], 'tracked implementation digest mismatch')
    head = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD']).decode().strip()
    require(head == receipt['output_commit'], 'exact output checkout required')
    subprocess.run(['git', '-C', str(root), 'diff', '--exit-code', receipt['output_commit'], '--', *paths, *[':(exclude)' + name for name in exclusions]], check=True)
    require(not subprocess.check_output(['git', '-C', str(root), 'status', '--porcelain']), 'reentry checkout must be clean')
    if receipt['status'] in {'COMPLETE', 'ALREADY_SATISFIED'}:
        require({'AC-S18-RUN-01', 'AC-S18-RUN-02'} <= completed, 'execution acceptance incomplete')
    print(json.dumps({'slice_id': receipt['slice_id'], 'criteria_sha256': criteria_sha, 'output_commit': head, 'output_digests': actual, 'status': 'ALREADY_SATISFIED' if receipt['status'] != 'IN_PROGRESS' else 'PRODUCTS_VERIFIED'}, sort_keys=True))


if __name__ == '__main__':
    main()
