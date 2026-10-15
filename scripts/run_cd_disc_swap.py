#!/usr/bin/env python3
"""Swap discs on the pinned CD controller: disc A read, lid open, disc B, lid close.

Two authored Mode 2 images with different payloads go through the original
raw-sector/track-info ports (sim/reference/psx_disc_host_swap.sv). The bench
reads disc A, opens the lid and checks GetStat's shell-open bit, loads disc
B's track table while open, closes the lid, polls GetStat until the bit
clears, then runs Init/GetID and reads disc B's first sector byte for byte.
A planted fault (disc A stays in the drive) must fail.
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
    directory = ROOT / 'build/sim/cd-disc-swap'; directory.mkdir(parents=True, exist_ok=True)
    meta = {}
    for name, make in (('a', synthetic_sector), ('b', sector_b)):
        sub = directory / name; sub.mkdir(exist_ok=True)
        (sub / f'disc_{name}.bin').write_bytes(b''.join(make(lba) for lba in range(12)))
        cue = sub / f'disc_{name}.cue'
        cue.write_text(f'FILE "disc_{name}.bin" BINARY\nTRACK 01 MODE2/2352\nINDEX 01 00:00:00\n')
        with DiscHost(cue) as host:
            meta[name] = host.prepare(sub)['metadata']
    manifest = prepare(directory / 'sources')
    verilog = directory / 'cd.v'; bench = directory / 'tb_cd_disc_swap.sv'
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
        text = (ROOT / 'sim/reference/tb_cd_disc_swap.sv').read_text()
        instance = re.search(r'CD_ENTITY cd\([^\n]+\);', text).group()
        text = text.replace(instance, re.sub(r'\.(\w+)\(', lambda m: '.' + ports[m[1].lower()] + '(', instance)).replace('CD_ENTITY', name)
        bench.write_text(text)
        subprocess.run([verilator, '--binary', '--timing', '-O3', '-j', '4', '-Wno-fatal', '--top-module', 'tb_cd_disc_swap',
                        '--Mdir', str(directory / 'obj'), str(verilog), str(ROOT / 'sim/reference/mister_memory_models.sv'),
                        str(ROOT / 'sim/reference/psx_disc_host_swap.sv'), str(bench)],
                       cwd=directory, stdout=log, stderr=subprocess.STDOUT, timeout=600, check=True)
    def run(meta_b, image_b=directory / 'b/disc_b.bin'):
        r = subprocess.run([str(directory / 'obj/Vtb_cd_disc_swap'), f'+disc_a={directory / "a/disc_a.bin"}',
                            f'+disc_b={image_b}', f'+meta_a={meta["a"]}', f'+meta_b={meta_b}',
                            '+disc_sectors=12'], cwd=directory, capture_output=True, text=True, timeout=3600)
        return r.returncode, r.stdout + r.stderr
    code, out = run(meta['b'])
    (directory / 'run.log').write_text(out)
    line = next((l for l in out.splitlines() if 'PASS CD disc swap' in l), None)
    print(line or out[-2000:])
    if code or not line: raise SystemExit('CD disc swap failed')
    # A swap that leaves disc A in the drive must be caught.
    code, out = run(meta['a'], directory / 'a/disc_a.bin')
    if code == 0: raise SystemExit('Planted no-swap fault not detected')
    print('Planted no-swap fault rejected')


if __name__ == '__main__': main()
