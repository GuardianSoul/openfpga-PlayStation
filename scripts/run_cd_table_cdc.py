#!/usr/bin/env python3
"""One-file table CDC with the APF fixture, 51 tracks and skewed payloads.

Only simulation copies acquire delays/faults. This checks digital protocol
and RAM leases, not metastability or routed physical timing.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess
import tempfile

from prepare_pocket_cd_cdc import CD_TABLE_SHA, preserve_sync_flops
from prepare_resource_targets import digest
from psx_disc_host import metadata_words, multitrack_metadata_words
from run_cd_menu_cdc import delayed_stream, once
from tool_paths import ROOT, find_tool


def table_bench(text):
    text = once(text, ' always #14.763 cclk=~cclk;', ''' real phase=0;
 initial begin
  if($value$plusargs("CORE_PHASE=%f",phase)) begin end
  #(phase);forever #14.763 cclk=~cclk;
 end''')
    text = once(text, ' integer sectors_a=40, sectors_b=9;',
                ' integer sectors_a=8040, sectors_b=9;')
    # The shared fixture uses offset 0x40. Relocate its authored B/invalid
    # slots first so the actual supported offset 1 has no duplicate slot ID.
    text = text.replace("16'h21", "16'h30").replace("32'h21", "32'h30")
    text = text.replace("16'h25", "16'h26").replace("32'h25", "32'h26")
    text = text.replace("16'h60", "16'h21").replace("32'h60", "32'h21")
    return text.replace("16'h64", "16'h25").replace("32'h64", "32'h25")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    parent = ROOT / 'build/sim/cd-table-cdc'; parent.mkdir(parents=True, exist_ok=True)
    work = args.output.resolve() if args.output else Path(tempfile.mkdtemp(dir=parent, prefix='checks-'))
    if args.output:
        if work.exists() or not work.is_relative_to(ROOT / 'build'):
            raise ValueError('Fresh build output required')
        work.mkdir(parents=True)
    print(work, flush=True)
    stream = ROOT / 'rtl/pocket/psx_cd_stream.sv'
    bench = ROOT / 'sim/pocket/tb_cd_stream.sv'
    dependencies = [ROOT / p for p in ('rtl/pocket/psx_apf_target_arb.sv',
        'rtl/pocket/psx_apf_table_arb.sv', 'references/MIRLO64/target/pocket/core_bridge_cmd.v',
        'references/MIRLO64/platform/pocket/common.v', 'sim/pocket/mf_datatable_model.sv')]
    protected = {str(p): digest(p) for p in [stream, bench, *dependencies, Path(__file__),
        ROOT / 'scripts/run_cd_menu_cdc.py', ROOT / 'scripts/prepare_pocket_cd_cdc.py',
        ROOT / 'scripts/psx_disc_host.py']}
    if digest(stream) != CD_TABLE_SHA:
        raise ValueError('Expected reviewed table-capable CD stream')
    for name, sectors in [('meta-a.hex', 8040), ('meta-b.hex', 9),
                           ('meta-a2.hex', 30), ('meta-big.hex', 317787)]:
        (work / name).write_text(''.join(f'{x:08x}\n' for x in metadata_words(sectors, region=2)))
    table = multitrack_metadata_words([40] + [160]*50, region=2)
    table[3] |= 0x8401
    assert len(table) == 400 and table[0] == 0x5133
    (work / 'meta-tb.hex').write_text(''.join(f'{x:08x}\n' for x in table))
    copied_bench = work / 'bench.sv'; copied_bench.write_text(table_bench(bench.read_text()))
    prepared = preserve_sync_flops(stream.read_text()); runs = []
    for name, fault in [('bounded13', None), ('late-table200', 'm_tb'),
                        ('reused-table-buffer', 'lease')]:
        directory = work / name; directory.mkdir()
        body = delayed_stream(prepared, fault, table=True)
        if fault == 'lease':
            body = once(body, '!(i==NBUF-1 && tb_hold)', "1'b1")
        rtl = directory / 'stream.sv'; rtl.write_text(body)
        command = [find_tool('verilator'), '--binary', '--timing', '-O3', '-j', '2', '-Wno-fatal',
            '+define+CD_STREAM_ARB', '+define+CD_STREAM_TRACKS=1', "+define+CD_STREAM_TB=16'd1",
            '--top-module', 'tb_cd_stream', '--Mdir', str(directory / 'obj'),
            *map(str, [rtl, copied_bench, *dependencies])]
        with (directory / 'build.log').open('w') as log:
            subprocess.run(command, cwd=work, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=300)
        for phase in ([0, .1, 6.7, 13.4] if fault is None else [0]):
            result = subprocess.run([str(directory / 'obj/Vtb_cd_stream'), '+CASE=9', f'+CORE_PHASE={phase}'],
                                    cwd=work, capture_output=True, text=True, timeout=180)
            output = result.stdout + result.stderr
            passed = (result.returncode != 0 and 'Track table word' in output if fault else
                result.returncode == 0 and output.count('PASS CD STREAM') == 1 and
                not re.search(r'%Fatal|%Error|Assertion failed', output))
            log = directory / f'phase{phase}.log'; log.write_text(output)
            runs.append(dict(name=name, phase_ns=phase, fault=fault, passed=passed,
                exit_code=result.returncode, rtl_sha256=digest(rtl), log_sha256=digest(log)))
            (work / 'report.json').write_text(json.dumps(dict(passed=False, runs=runs), indent=2)+'\n')
            print(name, phase, passed, output.strip()[-200:], flush=True)
            if not passed:
                raise RuntimeError('One-file CD CDC check failed')
    identity = all(digest(Path(p)) == sha for p, sha in protected.items())
    report = dict(passed=identity and len(runs)==6 and all(r['passed'] for r in runs),
        source_identity_verified=identity, original_sources=protected, runs=runs,
        MAX_TRACKS=1, TABLE_OFFSET=1, LIBCRYPT_SLOT=0, metadata_tracks=51, payload_bound_ns=13,
        table_RAM_lease_exercised=True, synchronizer_flops_preserved=23,
        per_bit_payload_skew_modelled=True, toggle_delivery_unmodified=True,
        analog_metastability_modelled=False, physical_timing_verified=False, hardware_verified=False)
    (work / 'report.json').write_text(json.dumps(report, indent=2)+'\n')
    if not report['passed']:
        raise RuntimeError('One-file CD CDC source/coverage changed')


if __name__ == '__main__':
    main()
