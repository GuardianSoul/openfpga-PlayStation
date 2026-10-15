#!/usr/bin/env python3
"""Compare serialized GPU arithmetic/scheduling with six original dividers."""
import json
import argparse
from pathlib import Path
import subprocess

from prepare_resource_targets import UPSTREAM, digest
from tool_paths import ROOT, find_tool


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--borrowed-inputs',action='store_true',help='Check the generated producer-held scheduler and reject an invalid input change')
    parser.add_argument('--output',type=Path,help='Independent output directory for another toolchain')
    args=parser.parse_args()
    ghdl = find_tool('ghdl')
    if not ghdl:
        raise SystemExit('GHDL is required')
    directory = args.output.resolve() if args.output else ROOT / ('build/sim/gpu-divider-borrowed-unit' if args.borrowed_inputs else 'build/sim/gpu-divider-unit')
    directory.mkdir(parents=True, exist_ok=True)
    sources = [UPSTREAM / 'rtl/divider.vhd', ROOT / 'rtl/gpu/psx_gpu_divider_pool.vhd',
               ROOT / 'sim/reference/tb_gpu_divider_pool.vhd']
    if args.borrowed_inputs:
        from prepare_gpu_borrowed import borrowed_text
        copied=directory/'psx_gpu_divider_pool.vhd'
        copied.write_text(borrowed_text(copied.name,sources[1].read_text()))
        sources[1]=copied
    hashes = {str(path): digest(path) for path in sources}
    output = []
    for command in ([ghdl, '-a', '--std=08', *(str(path) for path in sources)],
                    [ghdl, '-r', '--std=08', 'tb_pool', '--assert-level=error']):
        result = subprocess.run(command, cwd=directory, capture_output=True, text=True, timeout=60)
        output.append(result.stdout + result.stderr)
        if result.returncode:
            (directory / 'checks.log').write_text(''.join(output))
            raise SystemExit(''.join(output))
    text = ''.join(output)
    print(text, end='')
    (directory / 'checks.log').write_text(text)
    identity = all(digest(ROOT / path) == sha for path, sha in hashes.items())
    passed = identity and 'PASS divider pool batches=160 comparisons=938' in text
    rejection=None
    if passed and args.borrowed_inputs:
        template=sources[2].read_text()
        marker="starts<=(others=>'0');seen:=(others=>'0');n:=0;"
        if template.count(marker)!=1:raise ValueError('Divider fixture release edge changed')
        negative=template.replace(marker,"""starts<=(others=>'0');
   wait until falling_edge(clk);wait until falling_edge(clk);
   nums(44 downto 0)<=nums(44 downto 0)+1;
   seen:=(others=>'0');n:=0;""")
        negative=negative.replace('tb_pool','tb_bad')
        path=directory/'tb_bad.vhd';path.write_text(negative)
        analyzed=subprocess.run([ghdl,'-a','--std=08',str(path)],cwd=directory,capture_output=True,text=True,timeout=60)
        if analyzed.returncode:raise SystemExit(analyzed.stdout+analyzed.stderr)
        result=subprocess.run([ghdl,'-r','--std=08','tb_bad','--assert-level=error'],cwd=directory,capture_output=True,text=True,timeout=60)
        rejection=result.stdout+result.stderr
        (directory/'contract-rejection.log').write_text(rejection)
        passed &= result.returncode!=0 and 'Borrowed GPU operands changed before completion' in rejection
        passed &= all(digest(Path(path))==sha for path,sha in hashes.items())
        if passed:print('PASS borrowed-input contract rejects active-lane mutation')
    report = {'source_sha256': hashes, 'source_identity_verified': identity,
              'ghdl_version': subprocess.check_output([ghdl, '--version'], text=True),
              'passed': passed, 'stdout': text, 'batches': 160, 'result_comparisons': 938,
              'reset_abort_cases': 40, 'latency_changed': True,
              'borrowed_inputs':args.borrowed_inputs,'active_lane_mutation_rejected':bool(rejection) and passed,
              'game_verified': False, 'hardware_verified': False}
    (directory / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    if not passed:
        raise SystemExit('Divider comparison checks failed')


if __name__ == '__main__':
    main()
