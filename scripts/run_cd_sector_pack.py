#!/usr/bin/env python3
"""cd-sector-pack: packed 5,120-word sector RAM against the original 8,192-word one.

Random writes and reads of every sector and offset 0..588 through the
original mem.dpram (sector & offset) and the packed RAM (sector*640+offset)
with the address expressions the transform puts into cd_top; read data must
match every cycle (3 seeds). The transform itself is applied to the pinned
cd_top to check its anchors. A planted wrong stride (sector*576) must fail.
"""
from pathlib import Path
import subprocess

from prepare_cd_sector_pack import sector_pack_text
from tool_paths import ROOT, find_tool

UPSTREAM = ROOT / 'references/PSX_MiSTer'
BENCH = ROOT / 'sim/reference/tb_cd_sector_pack.vhd'


def run(directory, bench_text, seeds):
    ghdl = find_tool('ghdl'); directory.mkdir(parents=True, exist_ok=True)
    log = (directory / 'build.log').open('w')
    subprocess.run([ghdl, '-a', '--std=08', '-frelaxed', '--work=mem', str(UPSTREAM / 'sim/system/src/mem/dpram.vhd')], cwd=directory, stdout=log, stderr=subprocess.STDOUT, check=True)
    bench = directory / 'tb.vhd'; bench.write_text(bench_text)
    subprocess.run([ghdl, '-a', '--std=08', '-frelaxed', str(bench)], cwd=directory, stdout=log, stderr=subprocess.STDOUT, check=True)
    subprocess.run([ghdl, '-e', '--std=08', '-frelaxed', 'tb_cd_sector_pack'], cwd=directory, stdout=log, stderr=subprocess.STDOUT, check=True)
    results = []
    for seed in seeds:
        r = subprocess.run([ghdl, '-r', '--std=08', '-frelaxed', 'tb_cd_sector_pack', f'-gSEED={seed}', '--assert-level=failure', '--ieee-asserts=disable'],
                           cwd=directory, capture_output=True, text=True, timeout=1800)
        out = r.stdout + r.stderr
        results.append((r.returncode == 0 and 'PASS cd sector pack' in out, next((l for l in out.splitlines() if 'PASS' in l or 'mismatch' in l), out[-200:])))
    return results


MODEL = '''   process (clk1x) begin
      if rising_edge(clk1x) then
         if wren = '1' then packed(to_integer(unsigned(addrA_new))) <= data; end if;
         q_new <= packed(to_integer(unsigned(addrB_new)));
      end if;
   end process;
'''


def bench_with_transform_ram():
    """The bench with its RAM model replaced by the transform's own
    sectorPackRam block from the transformed pinned cd_top."""
    packed = sector_pack_text((UPSTREAM / 'rtl/cd_top.vhd').read_text())   # anchors match the pinned source
    start = packed.index('   sectorPackRam : block'); end = packed.index('   end block;', start) + len('   end block;\n')
    block = packed[start:end]
    for old, new in (('sectorBuffers_wrenA', 'wren'), ('sectorBuffers_addrA', 'addrA_new'), ('sectorBuffers_DataA', 'data'),
                     ('sectorBuffers_addrB', 'addrB_new'), ('sectorBuffers_DataB', 'q_new')):
        block = block.replace(old, new)
    text = BENCH.read_text()
    if text.count(MODEL) != 1: raise SystemExit('bench RAM model anchor changed')
    return text.replace(MODEL, block)


def main():
    directory = ROOT / 'build/sim/cd-sector-pack'; text = bench_with_transform_ram(); failures = []
    for ok, line in run(directory / 'pack', text, (1, 2, 3)):
        print('PASS' if ok else 'FAIL', line.strip()[-120:])
        if not ok: failures.append('lockstep')
    fault = text.replace('addrB_new <= std_logic_vector(resize(rsec & "000000000", 13) + resize(rsec & "0000000", 13)',
                         'addrB_new <= std_logic_vector(resize(rsec & "000000000", 13) + resize(rsec & "000000", 13)')
    if fault == text: raise SystemExit('fault anchor changed')
    caught = not run(directory / 'fault', fault, (1,))[0][0]
    print('fault stride-576:', 'rejected' if caught else 'NOT DETECTED')
    if not caught: failures.append('fault')
    if failures: raise SystemExit('cd sector pack check failed: ' + ', '.join(failures))
    print('cd sector buffers packed at 640 words: identical reads; planted fault rejected')


if __name__ == '__main__': main()
