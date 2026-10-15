#!/usr/bin/env python3
"""Run two reset/diagnostic epochs with the original external memory models.

Control for run_shared_sdram_system: verifies the producer can reset; it does
not verify physical memory commits or match native timing cycle for cycle.
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

from prepare_resource_targets import digest
from tool_paths import ROOT, find_tool


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-build', type=Path, required=True,
        help='Full-reset directory produced by run_shared_sdram_system')
    args = parser.parse_args()
    source = args.source_build.resolve()
    source_report = source / 'report.json'
    recorded = json.loads(source_report.read_text())
    if recorded['preparation']['fast_reset']:
        parser.error('Control requires the full-reset conversion')
    original = source / 'system.v'
    if digest(original) != recorded['source_sha256'][str(original)]:
        parser.error('Recorded converted netlist changed')
    for s in recorded['preparation']['sources']:
        if digest(Path(s['path'])) != s['sha256']:
            parser.error('Prepared source changed: ' + s['path'])
    parent = ROOT / 'build/sim/shared-reset-control'
    parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='checks-', dir=parent))
    print(work, flush=True)
    netlist = work / 'system.v'; shutil.copyfile(original, netlist)
    shutil.copyfile(source / 'expected.hex', work / 'expected.hex')
    words = (source / 'boot.hex').read_text().split()
    (work / 'boot.hex8').write_text(''.join(f'{b:02x}\n' for w in words
                                          for b in int(w, 16).to_bytes(4, 'little')))
    template = ROOT / 'sim/reference/tb_psx_system_fast.sv'
    text = template.read_text()
    for before, after in (
        ('logic pause=0;', 'logic pause=1;'),
        ('initial begin repeat(100) @(negedge clk1x);reset=0;end', ''),
        ('$display("PASS FAST SYSTEM cycles=%d checks=%d",cycles,checks);$finish;',
         'diagnostic=0;pause=1;epoch_done=1;')):
        if text.count(before) != 1:
            raise ValueError('Control bench anchor changed: ' + before)
        text = text.replace(before, after)
    tail = '''reg epoch_done=0;
initial begin
 repeat(100) @(negedge clk1x);
 for(integer epoch=0;epoch<2;epoch++) begin
  epoch_done=0;diagnostic=1;checks=0;pause=1;reset=1;
  repeat(12) @(negedge clk1x);reset=0;
  wait(core.component_core.isavestates.savestate_busy);
  wait(!core.component_core.isavestates.savestate_busy);
  repeat(256) @(negedge clk1x);pause=0;
  wait(epoch_done);wait(isPaused);repeat(256) @(negedge clk1x);
  $display("CONTROL_DIAGNOSTIC epoch=%0d cycles=%0d checks=%0d",epoch,cycles,checks);
 end
 $display("PASS RESET PRODUCER CONTROL epochs=2 checks=5296");$finish;
end
endmodule
'''
    if text.count('endmodule\n') != 1:
        raise ValueError('Control bench tail changed')
    bench = work / 'tb.sv'; bench.write_text(text.replace('endmodule\n', tail))
    models = ROOT / 'sim/reference/mister_memory_models.sv'
    sources = [netlist, models, bench]
    inputs = [*sources, original, source_report, template, Path(__file__),
              source / 'boot.hex', source / 'expected.hex', work / 'boot.hex8', work / 'expected.hex']
    protected = {str(p): digest(p) for p in inputs}
    with (work / 'build.log').open('w') as log:
        subprocess.run([find_tool('verilator'), '--binary', '--timing', '-O3', '-j', '4',
            '-Wno-fatal', '--output-split', '20000', '--top-module', 'tb_psx_fast',
            '--Mdir', str(work / 'obj'), *map(str, sources)], cwd=work,
            stdout=log, stderr=subprocess.STDOUT, check=True, timeout=600)
    binary = work / 'obj/Vtb_psx_fast'
    with (work / 'run.log').open('w') as log:
        result = subprocess.run(['stdbuf', '-oL', str(binary), '+max_cycles=16000000'],
            cwd=work, stdout=log, stderr=subprocess.STDOUT, timeout=600)
    output = (work / 'run.log').read_text()
    identity = all(digest(Path(p)) == sha for p, sha in protected.items())
    passed = (identity and result.returncode == 0 and
        output.count('PASS RESET PRODUCER CONTROL epochs=2 checks=5296') == 1 and
        output.count('CONTROL_DIAGNOSTIC epoch=') == 2 and '%Fatal' not in output)
    report = dict(passed=passed, exit_code=result.returncode, source_sha256=protected,
        binary_sha256=digest(binary), log_sha256=digest(work / 'run.log'), output=output,
        source_identity_verified=identity,
        scope='Full-reset MiSTer conversion with original behavioral external memory models; two diagnostic epochs',
        native_memory_verified=False, physical_commit_verified=False,
        cycle_equivalence_verified=False, game_verified=False, hardware_verified=False)
    (work / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(output[-2000:], flush=True); print(work / 'report.json', flush=True)
    if not passed:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
