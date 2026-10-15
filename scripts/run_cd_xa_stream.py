#!/usr/bin/env python3
"""FFVII FMV audio through the pinned cd_top at real double-speed timing.

Needs the FFVII disc 1 image (PSX_FFVII_DISC1 or roms/); skips without it.
The bench (sim/reference/tb_cd_xa_stream.sv) runs Init, Demute, unity CD
volume, SetFilter 1/0, SetMode 0xC8 (2x, XA-ADPCM, filter), SetLoc to the
first movie on the disc and ReadN, acknowledging data sectors as an FMV
player would. It logs every sector cd_top hands to cd_xa and whether cd_xa
accepted it, every 44.1 kHz pop or underrun, and cd_left/right.

Checks: no XA sector of the stream is skipped (the decoder only accepts a
sector while fewer than 16 samples are queued), each delivered sector is
the next file 1/channel 0 audio sector on the disc, the popped samples
equal the Python reference decoder (run_cd_xa.Decoder) for the accepted
sectors, and cd_left/right equal the decoder output at unity volume.
Underruns (the 44.1 kHz tick finding no frame) are counted and reported.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

from prepare_system_verilator import prepare
from psx_disc_host import DiscHost
from run_cd_xa import Decoder
from tool_paths import ROOT, find_tool

FIRST_LBA = 129516  # first sector of the first FMV on disc 1 (file index, without the 150-sector pregap)


def disc_image():
    for candidate in (os.environ.get('PSX_FFVII_DISC1', ''),
                      str(ROOT / 'roms/Final Fantasy VII (Disc 1)/Final Fantasy VII (Disc 1).bin')):
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    return None


def msf(lba):
    absolute = lba + 150
    bcd = lambda v: f'{v // 10}{v % 10}'
    return bcd(absolute // 4500), bcd(absolute // 75 % 60), bcd(absolute % 75)


def build(directory, mm, ss, ff, fault=False):
    ghdl = find_tool('ghdl'); verilator = find_tool('verilator') or str(ROOT / 'build/tools/oss-cad-suite/bin/verilator')
    manifest = prepare(directory / 'sources')
    if fault:
        # Planted fault: a drive 7% too fast at 2x (150 -> 160 sectors/s).
        source = next(Path(s['path']) for s in manifest['sources'] if Path(s['path']).name == 'cd_top.vhd')
        text = source.read_text(); old = 'READSPEED2X             : integer := 44100 * 16#300# / 150;'
        if text.count(old) != 1: raise ValueError('cd_top read speed constant changed')
        source.write_text(text.replace(old, old.replace('/ 150', '/ 160')))
    verilog = directory / 'cd.v'; bench = directory / 'tb_cd_xa_stream.sv'
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
        text = (ROOT / 'sim/reference/tb_cd_xa_stream.sv').read_text()
        instance = re.search(r'CD_ENTITY cd\([^\n]+\);', text).group()
        text = text.replace(instance, re.sub(r'\.(\w+)\(', lambda m: '.' + ports[m[1].lower()] + '(', instance)).replace('CD_ENTITY', name)
        bench.write_text(text)
        subprocess.run([verilator, '--binary', '--timing', '-O3', '-j', '4', '-Wno-fatal', '--top-module', 'tb_cd_xa_stream',
                        f'-DMM={mm}', f'-DSS={ss}', f'-DFF={ff}',
                        '--Mdir', str(directory / 'obj'), str(verilog), str(ROOT / 'sim/reference/mister_memory_models.sv'),
                        str(ROOT / 'sim/reference/psx_disc_host_swap.sv'), str(bench)],
                       cwd=directory, stdout=log, stderr=subprocess.STDOUT, timeout=900, check=True)


def parse(log):
    sectors, pops, underruns = [], [], []
    lines = log.read_text().splitlines()
    i = 0
    while i < len(lines):
        parts = lines[i].split()
        if parts[0] == 'S':
            data = b''.join(int(w, 16).to_bytes(4, 'little') for w in lines[i + 1:i + 589])
            sectors.append((int(parts[1]), parts[2] == '1', data)); i += 589; continue
        if parts[0] == 'P':
            pops.append(tuple(map(int, parts[1:])))
        elif parts[0] == 'U':
            underruns.append(int(parts[1]))
        i += 1
    return sectors, pops, underruns


def main():
    image = disc_image()
    if image is None:
        print('SKIP FFVII XA stream: disc 1 image not found (set PSX_FFVII_DISC1)'); return
    count = int(os.environ.get('PSX_XA_STREAM_SECTORS', '40'))
    directory = ROOT / 'build/sim/cd-xa-stream'; directory.mkdir(parents=True, exist_ok=True)
    cue = directory / 'disc.cue'
    cue.write_text(f'FILE "{image}" BINARY\nTRACK 01 MODE2/2352\nINDEX 01 00:00:00\n')
    with DiscHost(cue) as host:
        meta = host.prepare(directory)['metadata']
    def check(bench, count, fault=False):
        bench.mkdir(parents=True, exist_ok=True)
        build(bench, *msf(FIRST_LBA), fault=fault)
        return run(bench, image, meta, count)

    failures = check(directory, count)
    if failures:
        sys.exit('XA stream check failed: ' + '; '.join(failures))
    print('PASS FFVII XA stream at 2x drive timing')
    failures = check(directory / 'fault', 24, fault=True)
    if not any('skipped' in f for f in failures):
        sys.exit('Planted fast-drive fault was not detected: ' + '; '.join(failures))
    print('Planted fast-drive fault rejected (sectors skipped)')


def run(directory, image, meta, count):
    log = directory / 'xa_stream.log'
    sectors_on_disc = image.stat().st_size // 2352
    result = subprocess.run([str(directory / 'obj/Vtb_cd_xa_stream'), f'+disc_a={image}', f'+disc_b={image}',
                             f'+meta_a={meta}', f'+meta_b={meta}', f'+disc_sectors={sectors_on_disc}',
                             f'+xa_sectors={count}', f'+log={log}'],
                            cwd=directory, capture_output=True, text=True, timeout=7200)
    out = result.stdout + result.stderr
    line = next((l for l in out.splitlines() if 'PASS CD XA stream' in l), None)
    if result.returncode or not line:
        return ['bench failed: ' + out[-500:]]
    print(line)
    sectors, pops, underruns = parse(log)
    failures = []
    # The stream on disc, in order.
    expected_sectors = []
    with image.open('rb') as disc:
        disc.seek(FIRST_LBA * 2352)
        while len(expected_sectors) < len(sectors):
            data = disc.read(2352)
            if data[15] == 2 and data[16] == 1 and data[17] == 0 and data[18] & 0x24 == 0x24:
                expected_sectors.append(data)
    if [s[2] for s in sectors] != expected_sectors:
        failures.append('delivered sectors are not the consecutive file 1/channel 0 audio sectors')
    rejected = [n for n, (_, accepted, _) in enumerate(sectors) if not accepted]
    if rejected:
        failures.append(f'cd_xa skipped XA sectors {rejected[:10]} of {len(sectors)}')
    reference = Decoder('rtl').decode([s[2] for s in sectors if s[1]])
    # Each tick logs "U" first if the output FIFO was empty, then always "P" with
    # the (held) decoder output; only non-underrun ticks are pops.
    popped, underrun, ticks = [], False, []
    pop_lines = iter(pops)
    for line in log.read_text().splitlines():
        if line.startswith('U '):
            underrun = True
        elif line.startswith('P '):
            frame = next(pop_lines)
            ticks.append(underrun)
            if not underrun:
                popped.append(frame[:2])
            underrun = False
    mismatch = next((i for i in range(min(len(popped), len(reference))) if popped[i] != reference[i]), None)
    if mismatch is not None:
        failures.append(f'popped frame {mismatch} {popped[mismatch]} != reference {reference[mismatch]}')
    # Pause resets the XA decoder (cmdResetXa), so the frames of the sector
    # still playing when the bench pauses are dropped.
    if not len(reference) - 2352 <= len(popped) <= len(reference):
        failures.append(f'{len(popped)} frames popped, reference has {len(reference)}')
    volume_bad = sum(1 for p in pops if (p[2], p[3]) != (p[0], p[1]))
    if volume_bad:
        failures.append(f'{volume_bad} ticks where cd_left/right differ from the decoder at unity volume')
    # Underruns between the first and the last frame: the stream ran dry.
    first = ticks.index(False) if False in ticks else 0
    last = len(ticks) - 1 - ticks[::-1].index(False) if False in ticks else 0
    mid = [i for i in range(first, last) if ticks[i]]
    print(f'{len(sectors)} XA sectors delivered, {len(rejected)} skipped; {len(popped)} frames equal the reference; '
          f'underrun ticks during the stream: {len(mid)}')
    return failures


if __name__ == '__main__':
    main()
