#!/usr/bin/env python3
"""Reset-clock stop/phase/freeze checks and existing mailbox/APF data oracles."""
import json
from pathlib import Path
import re
import subprocess
import tempfile

from prepare_pocket_reset_release import adapt_boot, adapt_cdc, LOADER_CLOCKS, LOADER_OUTPUTS
from prepare_resource_targets import digest
from psx_disc_host import metadata_words, multitrack_metadata_words
from tool_paths import ROOT, find_tool


def main():
    parent = ROOT / 'build/sim/reset-release'
    parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='checks-', dir=parent))
    print(work, flush=True)
    protected_paths = [ROOT / p for p in (
        'rtl/pocket/psx_boot_gate.sv', 'rtl/memory/psx_mem_cdc.sv',
        'sim/pocket/tb_reset_release.sv', 'sim/memory/tb_psx_mem_cdc.sv',
        'sim/pocket/tb_apf_bios_boot.sv', 'rtl/pocket/psx_bios_loader.sv',
        'sim/pocket/tb_bios_probe.sv', 'references/MIRLO64/target/pocket/core_bridge_cmd.v',
        'references/MIRLO64/platform/pocket/common.v',
        'scripts/prepare_pocket_reset_release.py', 'scripts/run_pocket_reset_release.py',
        'rtl/pocket/psx_cd_stream.sv', 'sim/pocket/tb_cd_stream.sv',
        'rtl/pocket/psx_disc_swap.sv', 'rtl/pocket/psx_disc_select.sv',
        'sim/pocket/mf_datatable_model.sv', 'scripts/psx_disc_host.py')]
    protected = {str(p): digest(p) for p in protected_paths}
    boot_text = adapt_boot((ROOT / 'rtl/pocket/psx_boot_gate.sv').read_text())
    cdc_text = adapt_cdc((ROOT / 'rtl/memory/psx_mem_cdc.sv').read_text())
    top_text = '''module reset_release_top(input wire clk_74a,clk1x,power_reset_n,cpu_locked,
    output wire cd_host_reset,cd_core_reset);
    psx_mem_cdc loader_cdc (
''' + LOADER_CLOCKS + '\n' + LOADER_OUTPUTS + '''
        .s_valid(1'b0),.s_write(1'b0),.s_ready(),.s_addr(25'b0),.s_halfwords(4'b0),
        .s_wdata(128'b0),.s_byte_en(16'b0),.s_rsp_valid(),.s_rsp_ready(1'b1),
        .s_rsp_rdata(),.s_rsp_error(),.d_valid(),.d_write(),.d_ready(1'b1),
        .d_addr(),.d_halfwords(),.d_wdata(),.d_byte_en(),.d_rsp_valid(1'b0),
        .d_rsp_ready(),.d_rsp_rdata(128'b0),.d_rsp_error(1'b0));
endmodule
'''
    boot, cdc, wrapper = (work / n for n in ('psx_boot_gate.sv', 'psx_mem_cdc.sv', 'top.sv'))
    boot.write_text(boot_text); cdc.write_text(cdc_text); wrapper.write_text(top_text)
    runs = []
    def run(name, top, sources, parameters, marker, fault=False, verilator=False,
            run_args=(), extra_flags=(), reuse_launch=None):
        directory = work / name; directory.mkdir()
        if verilator:
            cmd = [find_tool('verilator'), '--binary', '--timing', '-O3', '-j', '4', '-Wno-fatal',
                   '--top-module', top, '--Mdir', str(directory / 'obj'), *extra_flags, *map(str, sources)]
            launch = [str(directory / ('obj/V' + top))]
        else:
            cmd = [find_tool('iverilog'), '-g2012', '-s', top, '-o', str(directory / 'test.vvp'),
                   *(f'-P{top}.{p}' for p in parameters), *map(str, sources)]
            launch = [find_tool('vvp'), str(directory / 'test.vvp')]
        if reuse_launch is None:
            with (directory / 'build.log').open('w') as f:
                subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT, check=True, timeout=300)
        else:
            launch = reuse_launch
        p = subprocess.run([*launch, *run_args], cwd=work, capture_output=True, text=True, timeout=120)
        output = p.stdout + p.stderr; log = directory / 'run.log'; log.write_text(output)
        good = ((p.returncode != 0 and marker in output) if fault else
                (p.returncode == 0 and output.count(marker) == 1 and
                 not re.search(r'%Fatal|%Error|Assertion failed', output)))
        runs.append(dict(name=name, passed=good, fault=fault, exit_code=p.returncode,
                         marker=marker, log=str(log), log_sha256=digest(log),
                         sources={str(s): digest(s) for s in sources}))
        print(name, good, output.strip(), flush=True)
        if not good:
            raise RuntimeError('Reset-release check failed: ' + name)
        return launch
    release_sources = [boot, cdc, wrapper, ROOT / 'sim/pocket/tb_reset_release.sv',
                       ROOT / 'rtl/pocket/psx_disc_swap.sv', ROOT / 'rtl/pocket/psx_disc_select.sv']
    for host, cpu, phase in ((7, 11, 3), (11, 5, 1), (5, 17, 7)):
        run(f'release-{host}-{cpu}', 'tb_reset_release', release_sources,
            [f'HOST_HALF={host}', f'CPU_HALF={cpu}', f'PHASE={phase}'], 'PASS reset release')
    # Raw reset can start the sequencer before the CPU-domain release. Guest
    # reset can instead erase a legitimate swap during a BIOS freeze/reload.
    bench = ROOT / 'sim/pocket/tb_reset_release.sv'
    before = '.clk(clk1x),.reset(cd_core_reset),.manual_lid_open'
    if bench.read_text().count(before) != 1:
        raise ValueError('Disc-swap reset fault anchor changed')
    for name, signal, marker in (
            ('fault-swap-raw-reset', 'raw_reset', 'Disc swap active during domain reset'),
            ('fault-swap-guest-reset', 'cpu_reset', 'Guest reset disturbed disc swap')):
        variant = work / (name + '.sv')
        variant.write_text(bench.read_text().replace(before,
            f'.clk(clk1x),.reset({signal}),.manual_lid_open', 1))
        run(name, 'tb_reset_release', [variant if p == bench else p for p in release_sources],
            [], marker, True)
    for source, dest, phase in ((10, 8, 3), (7, 11, 1), (17, 5, 2)):
        run(f'mailbox-{source}-{dest}', 'tb_psx_mem_cdc', [cdc, ROOT / 'sim/memory/tb_psx_mem_cdc.sv'],
            [f'SRC_HALF={source}', f'DST_HALF={dest}', f'DST_PHASE={phase}'], 'PASS CDC')
    # A reset bypass and a dead freeze acknowledgement must fail independently.
    wrapper.write_text(top_text.replace('.dst_reset(cd_core_reset)', '.dst_reset()').replace(
        '\nendmodule', '\nassign cd_core_reset=!power_reset_n || !cpu_locked;\nendmodule'))
    run('fault-raw-reset', 'tb_reset_release', release_sources, [], 'Stopped core clock released reset', True)
    wrapper.write_text(top_text)
    boot.write_text(boot_text.replace('assign freeze_ack=ack_sync;', "assign freeze_ack=1'b0;"))
    run('fault-freeze', 'tb_reset_release', release_sources, [], 'Freeze acknowledgement missing', True)
    boot.write_text(boot_text)
    cdc.write_text(cdc_text.replace('<= response_payload;', "<= response_payload ^ 129'b1;"))
    run('fault-payload', 'tb_psx_mem_cdc', [cdc, ROOT / 'sim/memory/tb_psx_mem_cdc.sv'], [],
        'CDC response payload mismatch', True)
    cdc.write_text(cdc_text)
    fifo = work / 'fifo.sv'
    fifo.write_text((ROOT / 'sim/pocket/tb_bios_probe.sv').read_text().split('module tb_bios_probe;', 1)[0])
    run('apf-bios', 'tb_apf_bios_boot', [ROOT / 'rtl/pocket/psx_bios_loader.sv', boot,
        ROOT / 'references/MIRLO64/target/pocket/core_bridge_cmd.v',
        ROOT / 'references/MIRLO64/platform/pocket/common.v',
        ROOT / 'sim/pocket/tb_apf_bios_boot.sv', fifo], [], 'PASS APF BIOS boot', verilator=True)
    # Use the existing independent sector/metadata oracle with the actual new
    # reset wiring. Its case 5 resets while an APF transaction is still leased.
    cd_bench = (ROOT / 'sim/pocket/tb_cd_stream.sv').read_text()
    changes = [
        (' reg hclk=0,cclk=0,host_reset=1,reset=1;', ''' reg hclk=0,cclk=0,host_reset=1,reset=1;
 wire cd_host_reset,cd_core_reset;
 reset_release_top reset_domains(.clk_74a(hclk),.clk1x(cclk),
  .power_reset_n(!(host_reset || reset)),.cpu_locked(1'b1),
  .cd_host_reset(cd_host_reset),.cd_core_reset(cd_core_reset));'''),
        ('.host_clk(hclk),.host_reset(host_reset)', '.host_clk(hclk),.host_reset(cd_host_reset)'),
        ('.read_error(read_error),.clk(cclk),.reset(reset)',
         '.read_error(read_error),.clk(cclk),.reset(cd_core_reset)'),
    ]
    for before, after in changes:
        if cd_bench.count(before) != 1:
            raise ValueError('CD reset bench anchor changed')
        cd_bench = cd_bench.replace(before, after, 1)
    cd = work / 'tb_cd_stream.sv'; cd.write_text(cd_bench)
    for name, sectors in [('meta-a.hex', 40), ('meta-b.hex', 9), ('meta-a2.hex', 30), ('meta-big.hex', 317787)]:
        (work / name).write_text(''.join(f'{w:08x}\n' for w in metadata_words(sectors, region=2)))
    (work / 'meta-mt.hex').write_text(''.join(f'{w:08x}\n' for w in multitrack_metadata_words([20, 162, 155], region=2)))
    cd_sources = [ROOT / 'rtl/pocket/psx_cd_stream.sv', cd, wrapper, cdc,
                  ROOT / 'references/MIRLO64/target/pocket/core_bridge_cmd.v',
                  ROOT / 'references/MIRLO64/platform/pocket/common.v',
                  ROOT / 'sim/pocket/mf_datatable_model.sv']
    launch = None
    for case in (0, 5, 6, 7):
        launch = run(f'cd-reset-{case}', 'tb_cd_stream', cd_sources, [], 'PASS CD STREAM',
                     verilator=True, run_args=[f'+CASE={case}'],
                     extra_flags=['+define+CD_STREAM_TRACKS=19'], reuse_launch=launch)
    identity = all(digest(Path(p)) == sha for p, sha in protected.items())
    report = dict(passed=identity and all(r['passed'] for r in runs), runs=runs,
                  original_source_identity_verified=identity, original_sources=protected,
                  reset_phase_clock_stop_and_freeze_verified=True,
                  mailbox_oracle_completed_per_clock_pair=702, mailbox_aborts_per_pair=3,
                  APF_BIOS_bytes=524288, physical_metastability_verified=False,
                  CD_stream_reset_wiring_verified=True, CD_metadata_tracks=19,
                  CD_startup_remount_case_verified=True,
                  disc_swap_domain_reset_and_guest_freeze_verified=True,
                  physical_timing_verified=False, hardware_verified=False)
    (work / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(work / 'report.json', flush=True)
    if not report['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
