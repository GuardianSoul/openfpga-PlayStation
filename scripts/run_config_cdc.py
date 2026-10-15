#!/usr/bin/env python3
"""Exercise coherent stable configuration levels across asynchronous clocks.

Compiles frozen copies in a fresh directory. Also requires two deliberately
broken RTL copies to fail the coherence/held-payload assertions. This is a
protocol simulation, not metastability, fitted timing, or Pocket qualification.
"""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

from prepare_resource_targets import digest
from tool_paths import ROOT, find_tool


def main():
    parent = ROOT/'build/sim/config-cdc'
    parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='checks-', dir=parent))
    print(work, flush=True)
    originals = [ROOT/'rtl/pocket/psx_config_cdc.sv', ROOT/'sim/pocket/tb_psx_config_cdc.sv',
        Path(__file__).resolve(), ROOT/'scripts/tool_paths.py', ROOT/'scripts/prepare_resource_targets.py']
    archive = {}; protected = {}
    for i, original in enumerate(originals):
        sha = digest(original)
        copy = work/'input-archive'/str(i)/original.name
        copy.parent.mkdir(parents=True)
        shutil.copyfile(original, copy)
        if digest(copy) != sha:
            raise ValueError('Configuration CDC input changed during archive: '+str(original))
        archive[str(original)] = dict(path=str(copy), sha256=sha)
        protected[str(original)] = sha
        protected[str(copy)] = sha
    rtl, bench = [Path(archive[str(p)]['path']) for p in originals[:2]]
    compiler, simulator = find_tool('iverilog'), find_tool('vvp')
    runs = []

    def run(name, source, sh=6.734, dh=14.76255, phase=0, expected_fault=None):
        folder = work/name
        folder.mkdir()
        binary = folder/'tb.vvp'
        command = [compiler, '-g2012', '-s', 'tb', f'-Ptb.SH={sh}', f'-Ptb.DH={dh}',
            f'-Ptb.DP={phase}', '-o', str(binary), str(source), str(bench)]
        with (folder/'compile.log').open('w') as log:
            compiled = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=60)
        result = dict(name=name, rtl_sha256=digest(source), compiler_command=command,
            compiler_exit_code=compiled.returncode, accepted=False, expected_fault=expected_fault)
        if compiled.returncode == 0:
            log = folder/'run.log'
            with log.open('w') as output:
                completed = subprocess.run([simulator, str(binary)], stdout=output,
                    stderr=subprocess.STDOUT, timeout=60)
            text = log.read_text()
            result.update(exit_code=completed.returncode, output=text, log_sha256=digest(log),
                binary_sha256=digest(binary))
            result['accepted'] = (completed.returncode != 0 and expected_fault in text
                if expected_fault else completed.returncode == 0 and text.count('PASS CONFIG_CDC') == 1
                and 'FATAL' not in text)
        runs.append(result)
        print(name, 'PASS' if result['accepted'] else 'FAIL', flush=True)

    for name, sh, dh, phase in [('host-fast', 6.734, 14.76255, 0),
            ('shifted', 6.734, 14.76255, 7.1), ('dest-fast', 14.76255, 6.734, 2.3),
            ('slow-dest', 6.734, 31.7, 5.2)]:
        run(name, rtl, sh, dh, phase)
    text = rtl.read_text()
    faults = [
        ('overwrite', '            acknowledge_meta<=acknowledge;acknowledge_sync<=acknowledge_meta;',
            '            payload<=src_data;\n            acknowledge_meta<=acknowledge;acknowledge_sync<=acknowledge_meta;',
            'Outstanding payload was overwritten'),
        ('live-bus', 'dst_data<=payload;', 'dst_data<=src_data;', 'Torn or wrong configuration snapshot')]
    for name, before, after, marker in faults:
        if text.count(before) != 1:
            raise ValueError('Configuration CDC fault anchor changed: '+name)
        source = work/('fault-'+name+'.sv')
        source.write_text(text.replace(before, after))
        protected[str(source)] = digest(source)
        run('fault-'+name, source, expected_fault=marker)
    identity = all(digest(Path(p)) == sha for p, sha in protected.items())
    passed = identity and len(runs) == 6 and all(r['accepted'] for r in runs)
    report = dict(passed=passed, source_identity_verified=identity, source_sha256=protected,
        input_archives=archive, runs=runs, coherent_stable_levels_verified=passed,
        fault_controls_rejected=passed, hardware_verified=False, timing_verified=False,
        limitations=['Intermediate source levels may coalesce; never use for pulses or lossless commands',
            'Both domains must share asynchronous reset; local release takes three clocks',
            'No analog metastability or physical flight/skew/reset qualification in RTL simulation'])
    (work/'report.json').write_text(json.dumps(report, indent=2)+'\n')
    print(work/'report.json', flush=True)
    if not passed:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
