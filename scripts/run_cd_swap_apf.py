#!/usr/bin/env python3
"""Whole Pocket disc swap: SD loader, swap sequencer and the pinned CD controller.

Disc A (12 sectors) and disc B (14 sectors) are authored Mode 2 images. The
APF firmware model reloads slot 0 with disc B while cd_top is reading disc A;
psx_disc_swap opens the lid on disc_pending, psx_cd_stream drains and
remounts, the lid closes, and disc B's first sector is read byte for byte.
Every track word must belong to the file then in the slot. A planted fault
(disc B's expected table replaced by disc A's) must fail.
"""
from pathlib import Path
import re
import subprocess

from prepare_system_verilator import prepare
from psx_disc_host import DiscHost
from run_cd_controller import synthetic_sector
from tool_paths import ROOT, find_tool


def sector_b(lba):
    data = bytearray(synthetic_sector(lba))
    data[24:24 + 2048] = bytes((i * 31 + 7) & 255 for i in range(2048))
    return bytes(data)


def main():
    ghdl = find_tool('ghdl'); verilator = find_tool('verilator') or str(ROOT / 'build/tools/oss-cad-suite/bin/verilator')
    directory = ROOT / 'build/sim/cd-swap-apf'; directory.mkdir(parents=True, exist_ok=True)
    meta = {}
    for name, make in (('a', synthetic_sector), ('b', sector_b)):
        sub = directory / name; sub.mkdir(exist_ok=True)
        (sub / f'disc_{name}.bin').write_bytes(b''.join(make(lba) for lba in range(12 if name == 'a' else 14)))
        cue = sub / f'disc_{name}.cue'
        cue.write_text(f'FILE "disc_{name}.bin" BINARY\nTRACK 01 MODE2/2352\nINDEX 01 00:00:00\n')
        with DiscHost(cue) as host:
            meta[name] = host.prepare(sub)['metadata']
    manifest = prepare(directory / 'sources')
    verilog = directory / 'cd.v'; bench = directory / 'tb_cd_swap_apf.sv'
    with (directory / 'build.log').open('w') as log:
        for source in manifest['sources']:
            subprocess.run([ghdl, '-a', '--std=08', '-frelaxed', f'--work={source["library"]}', source['path']],
                           cwd=directory, stdout=log, stderr=subprocess.STDOUT, timeout=60, check=True)
        with verilog.open('w') as output:
            subprocess.run([ghdl, '--synth', '--std=08', '-frelaxed', '--vendor-library=ramip', '--out=verilog', 'cd_top'],
                           cwd=directory, stdout=output, stderr=log, timeout=120, check=True)
        from ghdl_verilog import normalize_generated_verilog
        normalize_generated_verilog(verilog)
        converted = verilog.read_text()
        name = re.findall(r'^module (cd_top(?:_Barch[^\s]*)?)\s*\n', converted, re.M)[0]
        header = converted[converted.index('module ' + name):].split(');', 1)[0]
        ports = {p.lower(): p for p in re.findall(r'\b(?:input|output)\s+(?:\[\d+:\d+\]\s+)?(\w+)', header)}
        text = (ROOT / 'sim/pocket/tb_cd_swap_apf.sv').read_text()
        instance = re.search(r'CD_ENTITY cd\([^\n]+\);', text).group()
        text = text.replace(instance, re.sub(r'\.(\w+)\(', lambda m: '.' + ports[m[1].lower()] + '(', instance)).replace('CD_ENTITY', name)
        bench.write_text(text)
        subprocess.run([verilator, '--binary', '--timing', '-O3', '-j', '4', '-Wno-fatal', '--top-module', 'tb_cd_swap_apf',
                        '--Mdir', str(directory / 'obj'), str(verilog), str(ROOT / 'sim/reference/mister_memory_models.sv'),
                        *[str(ROOT / p) for p in ('rtl/pocket/psx_cd_stream.sv', 'rtl/pocket/psx_disc_swap.sv', 'rtl/pocket/psx_disc_select.sv',
                          'references/MIRLO64/target/pocket/core_bridge_cmd.v', 'references/MIRLO64/platform/pocket/common.v',
                          'sim/pocket/mf_datatable_model.sv')], str(bench)],
                       cwd=directory, stdout=log, stderr=subprocess.STDOUT, timeout=600, check=True)
    def run(meta_b, image_b=directory / 'b/disc_b.bin'):
        r = subprocess.run([str(directory / 'obj/Vtb_cd_swap_apf'), f'+disc_a={directory / "a/disc_a.bin"}',
                            f'+disc_b={image_b}', f'+meta_a={meta["a"]}', f'+meta_b={meta_b}',
                            '+sectors_a=12', '+sectors_b=14'], cwd=directory, capture_output=True, text=True, timeout=3600)
        return r.returncode, r.stdout + r.stderr
    code, out = run(meta['b'])
    (directory / 'run.log').write_text(out)
    line = next((l for l in out.splitlines() if 'PASS CD swap via APF' in l), None)
    print(line or out[-2000:])
    if code or not line: raise SystemExit('CD swap via APF failed')
    # A table that is not the reloaded file's must be caught.
    code, out = run(meta['a'])
    if code == 0: raise SystemExit('Planted no-swap fault not detected')
    print('Planted wrong-table fault rejected')


if __name__ == '__main__': main()
