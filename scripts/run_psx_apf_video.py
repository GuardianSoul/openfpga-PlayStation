#!/usr/bin/env python3
"""Run rtl/pocket/psx_apf_video.sv benches (480i field hold, scaler slot select, interlace
feature bits), the psx_perf_log and psx_bios_patch benches and the paired-PSRAM backend benches."""
import subprocess

from tool_paths import ROOT, find_tool


def main():
    folder = ROOT/'build/sim/psx-apf-video'; folder.mkdir(parents=True, exist_ok=True)
    video = ['rtl/pocket/psx_apf_video.sv', 'rtl/pocket/psx_perf_log.sv']
    psram = ['sim/pocket/psram_behavior.sv', 'rtl/pocket/psx_psram_pair.sv', 'rtl/pocket/psx_psram_clients.sv']
    for bench, sources in (('tb_psx_apf_video', video), ('tb_apf_slot', video), ('tb_apf_interlace', video), ('tb_apf_interlace:w0', video),
                           ('tb_perf_log', video), ('tb_perf_log:s1', video), ('tb_bios_patch', ['rtl/pocket/psx_bios_patch.sv']), ('tb_native_loader', ['rtl/pocket/psx_native_memory.sv']), ('tb_psram_pair', psram), ('tb_psram_pair:0', psram), ('tb_psram_clients', psram)):
        bench, _, paired = bench.partition(':')
        weave = paired == 'w0'
        binary = folder/(bench+paired)
        subprocess.run([find_tool('iverilog'), '-g2012', '-s', bench, *([f'-Ptb_psram_pair.PAIRED={paired}'] if paired and not weave and paired != 's1' else []), *(['-Ptb_apf_interlace.W=0'] if weave else []), *(['-Ptb_perf_log.STAGES=1'] if paired == 's1' else []), '-o', str(binary),
                        str(ROOT/f'sim/pocket/{bench}.sv'), *(str(ROOT/s) for s in sources)], check=True, timeout=60)
        out = subprocess.run([find_tool('vvp'), str(binary)], capture_output=True, text=True, timeout=600).stdout
        if 'errs=0' not in out: raise SystemExit(f'{bench} failed:\n{out}')
        print(bench, 'errs=0')


if __name__ == '__main__':
    main()
