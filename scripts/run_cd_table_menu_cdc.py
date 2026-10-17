#!/usr/bin/env python3
"""One-file tables composed with the real disc menu, swap and APF transport.

Authored 51-track metadata and sector data; digital propagation, not physical
timing, game execution or analog metastability. Shared RTL stays unchanged.
"""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile

from prepare_pocket_cd_cdc import CD_TABLE_SHA, preserve_sync_flops, preserve_menu_flops
from prepare_resource_targets import digest
from psx_disc_host import metadata_words, multitrack_metadata_words
from run_cd_menu_cdc import delayed_stream, menu_bench, once
from tool_paths import ROOT, find_tool


def combined_bench(text):
    text = menu_bench(text)
    for old, new in (
            ('0:return 40;', '0:return 8040;'),
            ('size_a=40*2352;', 'size_a=8040*2352;'),
            ('fetch(101,40,150,0)', 'fetch(101,8040,150,0)'),
            ('function automatic integer slot_size(input integer slot);',
             'function automatic integer slot_size(input integer slot);\n  if(slot==102) return 1600;'),
            ("if(`CD_STREAM_TB!=0 && id==16'h60)", "if(`CD_STREAM_TB!=0 && id==16'd102)"),
            ('(which==0 || which==2)', '(which==0)'),
            ('$readmemh("meta-a.hex",meta_a);',
             '$readmemh("meta-tb.hex",meta_tb);$readmemh("meta-a.hex",meta_a);'),
            ('reg [19:0] old_publication=0;', 'reg [20:0] old_publication=0;')):
        text = once(text, old, new)
    text = text.replace('{dut.m_slot,dut.m_present}', '{dut.m_slot,dut.m_present,dut.m_tb}')
    end = text.index(' endtask', text.index(' task automatic write_table();'))
    text = text[:end] + '''  // Only disc A has a table file; the others exercise generated fallback.
  write_bus(32'hf8002020,swap(32'd102));
  write_bus(32'hf8002024,swap(32'd1600));
''' + text[end:]
    return text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    parent = ROOT / 'build/sim/cd-table-menu-cdc'; parent.mkdir(parents=True, exist_ok=True)
    work = args.output.resolve() if args.output else Path(tempfile.mkdtemp(dir=parent, prefix='checks-'))
    if args.output:
        if work.exists() or not work.is_relative_to(ROOT / 'build'):
            raise ValueError('Fresh ignored build output required')
        work.mkdir(parents=True)
    print(work, flush=True)
    stream = ROOT / 'rtl/pocket/psx_cd_stream.sv'
    menu = ROOT / 'rtl/pocket/psx_disc_menu.sv'
    bench = ROOT / 'sim/pocket/tb_cd_stream.sv'
    dependencies = [ROOT / p for p in ('rtl/pocket/psx_disc_swap.sv',
        'rtl/pocket/psx_apf_target_arb.sv', 'rtl/pocket/psx_apf_table_arb.sv',
        'references/MIRLO64/target/pocket/core_bridge_cmd.v',
        'references/MIRLO64/platform/pocket/common.v', 'sim/pocket/mf_datatable_model.sv')]
    protected = {str(p): digest(p) for p in [stream, menu, bench, *dependencies, Path(__file__),
        ROOT / 'scripts/run_cd_menu_cdc.py', ROOT / 'scripts/prepare_pocket_cd_cdc.py',
        ROOT / 'scripts/psx_disc_host.py']}
    if digest(stream) != CD_TABLE_SHA:
        raise ValueError('Expected reviewed table-capable stream')
    for name, size in [('meta-a.hex', 8040), ('meta-b.hex', 9),
                        ('meta-a2.hex', 30), ('meta-big.hex', 12)]:
        (work / name).write_text(''.join(f'{x:08x}\n' for x in metadata_words(size, region=2)))
    table = multitrack_metadata_words([40]+[160]*50, region=2); table[3] |= 0x8401
    assert len(table)==400 and table[0]==0x5133
    (work / 'meta-tb.hex').write_text(''.join(f'{x:08x}\n' for x in table))
    copied_bench = work / 'bench.sv'; copied_bench.write_text(combined_bench(bench.read_text()))
    copied_menu = work / 'menu.sv'; copied_menu.write_text(preserve_menu_flops(menu.read_text()))
    prepared = preserve_sync_flops(stream.read_text()); runs = []
    for name, fault in [('bounded13', None), ('late-table200', 'm_tb')]:
        directory = work / name; directory.mkdir()
        rtl = directory / 'stream.sv'; rtl.write_text(delayed_stream(prepared, fault, table=True))
        command = [find_tool('verilator'), '--binary', '--timing', '-O3', '-j', '2', '-Wno-fatal',
            '+define+CD_STREAM_ARB', '+define+CD_STREAM_TRACKS=1', "+define+CD_STREAM_TB=16'd1",
            '--top-module', 'tb_cd_stream', '--Mdir', str(directory / 'obj'),
            *map(str, [rtl, copied_menu, copied_bench, *dependencies])]
        with (directory / 'build.log').open('w') as log:
            subprocess.run(command, cwd=work, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=300)
        for phase, mask in ([(p,m) for p in [0,.1,6.7,13.4] for m in [15,7,5]] if not fault else [(0,15)]):
            result = subprocess.run([str(directory / 'obj/Vtb_cd_stream'),
                f'+CORE_PHASE={phase}', f'+PRESENT_MASK={mask}'], cwd=work,
                capture_output=True, text=True, timeout=180)
            output = result.stdout + result.stderr
            passed = (result.returncode!=0 and 'Track table word' in output if fault else
                result.returncode==0 and output.count('PASS CD MENU CDC')==1)
            log = directory / f'phase{phase}-mask{mask}.log'; log.write_text(output)
            runs.append(dict(name=name, fault=fault, phase_ns=phase, present_mask=mask,
                passed=passed, exit_code=result.returncode, log_sha256=digest(log), rtl_sha256=digest(rtl)))
            (work / 'report.json').write_text(json.dumps(dict(passed=False,runs=runs),indent=2)+'\n')
            print(name,phase,mask,passed,output.strip()[-200:],flush=True)
            if not passed:
                raise RuntimeError('One-file/menu composition failed')
    identity = all(digest(Path(p))==sha for p,sha in protected.items())
    report = dict(passed=identity and len(runs)==13 and all(x['passed'] for x in runs),
        source_identity_verified=identity, original_sources=protected, runs=runs,
        metadata_tracks=51, table_file_slots=[102], disc_slots=[101,201,301,401],
        MAX_TRACKS=1, TABLE_OFFSET=1, LIBCRYPT_SLOT=0, DISC_STEP=100,
        payload_bound_ns=13, card_traffic_and_arbitration=True, held_mount_payload_includes_m_tb=True,
        clock_phases_ns=[0,.1,6.7,13.4], presence_masks=[15,7,5],
        guest_CD_controller_connected=False, game_execution_verified=False,
        physical_timing_verified=False, hardware_verified=False)
    (work / 'report.json').write_text(json.dumps(report,indent=2)+'\n')
    if not report['passed']:
        raise RuntimeError('Composition source/coverage changed')


if __name__ == '__main__':
    main()
