"""Require complete source-bound proofs before selecting release cleanup."""
import json
from pathlib import Path
import shutil

from build_pocket import source
from prepare_resource_targets import digest
from prepare_release_init import release_init_source
from tool_paths import ROOT


def validate_cleanup(path, kind):
    path = Path(path).resolve()
    report = json.loads(path.read_text())
    if not report.get('passed') or not report.get('source_identity_verified'):
        raise ValueError('Complete release cleanup proof required')
    if any(digest(Path(p)) != sha for p, sha in report['protected_sha256'].items()):
        raise ValueError('Release cleanup proof source changed')
    expected = (['full-offchip', 'fast-offchip', 'full-internal', 'retry',
                 'bad-main-end', 'bad-SPU-boundary', 'bad-retry'] if kind == 'init' else
                ['original', 'lost-card-write', 'lost-CD-request', 'wrong-button'])
    if [r['name'] for r in report['runs']] != expected or not all(r['passed'] for r in report['runs']):
        raise ValueError('Release cleanup coverage missing')
    clean = 4 if kind == 'init' else 1
    if any(r['exit_code'] != 0 for r in report['runs'][:clean]) or any(
            r['exit_code'] == 0 for r in report['runs'][clean:]):
        raise ValueError('Release cleanup positive/fault outcomes invalid')
    names = (['scripts/prepare_release_init.py', 'scripts/prepare_reset_only.py',
              'scripts/run_release_init.py', 'references/PSX_MiSTer/rtl/savestates.vhd'] if kind == 'init' else
             ['scripts/prepare_release_host_trim.py', 'scripts/run_release_host_trim.py',
              'rtl/pocket/psx_pocket_pad.sv'])
    if any(report['protected_sha256'].get(str(ROOT / p)) != digest(ROOT / p) for p in names):
        raise ValueError('Cleanup proof does not bind selected implementation')
    return report, dict(path=str(path), sha256=digest(path))


def apply_release_init(manifest, directory, contract_path):
    directory = Path(directory).resolve()
    if manifest.get('kind') != 'physical-psx-budget' or not manifest.get('reset_only') or 'release_init' in manifest:
        raise ValueError('Lean initialization needs a fresh reset-only physical build')
    report, proof = validate_cleanup(contract_path, 'init')
    if any(digest(Path(s['path'])) != s['sha256'] for s in manifest['sources']) or any(
            digest(directory / n) != sha for n, sha in manifest['project_hashes'].items()):
        raise ValueError('Initialization input identity changed')
    selected = [s for s in manifest['sources'] if Path(s['path']).name == 'savestates.vhd']
    if len(selected) != 1 or selected[0]['sha256'] != report['selected_source']['baseline']['sha256']:
        raise ValueError('Selected reset controller differs from tested baseline')
    old = selected[0]
    generated = release_init_source(directory / 'release-init')
    if generated['sha256'] != report['selected_source']['sha256']:
        raise ValueError('Generated initialization differs from tested implementation')
    replacement = source(Path(generated['path']))
    qsf = directory / 'apf_top.qsf'
    if qsf.read_text().count(old['path']) != 1:
        raise ValueError('Expected one reset source assignment')
    qsf.write_text(qsf.read_text().replace(old['path'], replacement['path']))
    shutil.copy2(ROOT / 'references/PSX_MiSTer/LICENSE', directory / 'release-init/COPYING.GPL2')
    manifest['sources'] = [replacement if s is old else s for s in manifest['sources']]
    manifest['project_hashes']['apf_top.qsf'] = digest(qsf)
    manifest['release_init'] = dict(source=generated, contract=proof, preparer_sha256=digest(Path(__file__)),
                                    pause_idle_logic_preserved=True, full_top_fit_verified=False)
    return manifest
