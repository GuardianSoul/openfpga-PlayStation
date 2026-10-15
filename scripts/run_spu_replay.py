#!/usr/bin/env python3
"""Replay a software-emulator SPU trace into the RTL SPU and compare output.

The trace comes from tools/ffvii-census (PSX_SPU_TRACE): register writes and
DMA SPU-RAM writes per output sample, CD audio input and the emulator's final
output before resampler attenuation. Register writes are applied right after
the RTL's sample tick for the sample they precede; DMA data is written
directly into the SPU RAM model. This isolates SPU arithmetic from CPU, DMA
and CD timing.
"""
import argparse
from pathlib import Path
import re
import struct
import subprocess

from prepare_system_verilator import prepare
from prepare_spu_context_ram import context_ram_source
from prepare_spu_reverb_table import reverb_table_source
from prepare_spu_reverb_resample import reverb_resample_source
from tool_paths import ROOT, find_tool
from ghdl_verilog import normalize_generated_verilog


def convert(trace, directory, samples):
    data = trace.read_bytes(); reference = []
    with (directory / 'events.txt').open('w') as events:
        for offset in range(0, len(data) - 11, 12):
            sample, kind, hi, addr, value = struct.unpack_from('<IBBHI', data, offset)
            if sample >= samples: break
            if kind == 1: events.write(f'{sample} 1 {addr:x} {value:x}\n')
            elif kind == 2: events.write(f'{sample} 2 {(hi << 16) | addr:x} {value:x}\n')
            elif kind == 3: events.write(f'{sample} 3 0 {value:x}\n')
            elif kind == 4:
                left, right = value >> 16, value & 0xFFFF
                reference.append((left - 65536 if left & 0x8000 else left, right - 65536 if right & 0x8000 else right))
    return reference


def compare(reference, rtl):
    best = None
    for lag in range(-4, 5):
        pairs = [(reference[i], rtl[i + lag]) for i in range(len(reference)) if 0 <= i + lag < len(rtl)]
        nonzero = [(a, b) for a, b in pairs if a != (0, 0) or b != (0, 0)]
        equal = sum(a == b for a, b in nonzero)
        if best is None or equal > best['equal_nonzero']:
            first = next((i for i, (a, b) in enumerate(pairs) if a != b), None)
            best = {'lag': lag, 'compared': len(pairs), 'nonzero': len(nonzero), 'equal_nonzero': equal,
                    'first_difference': first, 'max_error': max((max(abs(a[0] - b[0]), abs(a[1] - b[1])) for a, b in pairs), default=0)}
    return best


def replay(trace, samples=150000, variant='original', voice=-1, skip=0, name=None, mutate=None, evshift=0):
    """Run one replay; returns (reference, rtl outputs, work directory)."""
    directory = ROOT / 'build/sim/spu-replay' / (name or variant); directory.mkdir(parents=True, exist_ok=True)
    ghdl = find_tool('ghdl'); verilator = find_tool('verilator')
    reference = convert(Path(trace).resolve(), directory, samples)
    manifest = prepare(directory / 'sources')
    sources = manifest['sources']
    if variant in ('reverb-table', 'reverb-resample', 'candidate'):
        upstream = next(s for s in sources if Path(s['path']).name == 'spu.vhd')
        context = context_ram_source(directory / 'context', upstream['path'])
        candidate = reverb_table_source(directory / 'candidate', context['path'])
        if variant == 'candidate':
            from prepare_spu_guest_m10k import guest_m10k_text
            path = Path(candidate['path']).with_name('spu_guest_m10k.vhd')
            # prepare() serves the RAM entities from the opaque 'ramip' library.
            text = Path(candidate['path']).read_text().replace('entity ramip.RamMLAB', 'entity mem.RamMLAB')
            text = guest_m10k_text(text).replace('entity mem.RamMLAB', 'entity ramip.RamMLAB')
            path.write_text(text.replace('entity mem.dpram', 'entity ramip.dpram'))
            candidate = {**candidate, 'path': str(path)}
        if variant == 'reverb-resample':
            candidate = reverb_resample_source(directory / 'resample', candidate['path'])
            if mutate:
                path = Path(candidate['path']); path.write_text(mutate(path.read_text()))
        sources = [candidate if s is upstream else s for s in sources]
    verilog = directory / 'spu.v'
    with (directory / 'build.log').open('w') as log:
        for s in sources:
            subprocess.run([ghdl, '-a', '--std=08', '-frelaxed', '--work=' + s['library'], s['path']], cwd=directory, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=60)
        with verilog.open('w') as out:
            subprocess.run([ghdl, '--synth', '--std=08', '-frelaxed', '--vendor-library=ramip', '--out=verilog', 'spu'], cwd=directory, stdout=out, stderr=log, check=True, timeout=180)
        normalize_generated_verilog(verilog)
        converted = verilog.read_text()
        name = re.findall(r'^module (spu(?:_Barch[^\s]*)?)[ \t]*\n', converted, re.M)[0]
        header = converted[re.search(r'^module ' + re.escape(name) + r'[ \t]*\n', converted, re.M).start():].split(');', 1)[0]
        ports = {n.lower(): n for n in re.findall(r'\b(?:input|output)\s+(?:\[\d+:\d+\]\s+)?(\w+)', header)}
        text = (ROOT / 'sim/reference/tb_spu_replay.sv').read_text()
        instance = re.search(r'SPU_ENTITY spu_core\([^\n]+\);', text).group()
        bound = re.sub(r'\.(\w+)\(', lambda m: '.' + ports[m[1].lower()] + '(', instance)
        (directory / 'bench.sv').write_text(text.replace(instance, bound).replace('SPU_ENTITY', name))
        subprocess.run([verilator, '--binary', '--timing', '-O3', '-j', '8', '-Wno-fatal', '--top-module', 'tb_spu_mix', *(['-DVOICE_PROBE'] if variant == 'original' else []), *(['-DRS_PROBE'] if variant == 'reverb-resample' else []), '--Mdir', str(directory / 'obj'),
                        str(verilog), str(ROOT / 'sim/reference/mister_memory_models.sv'), str(directory / 'bench.sv')], cwd=directory, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=900)
    result = subprocess.run([str(directory / 'obj/Vtb_spu_mix'), f'+samples={samples}', f'+voice={voice}', *([f'+skip={skip}'] if skip else []), f'+evshift={evshift}'], cwd=directory, capture_output=True, text=True)
    print(result.stdout[-2000:], result.stderr[-2000:])
    rtl = [tuple(map(int, line.split()[1:])) for line in (directory / 'rtl_out.txt').read_text().splitlines()]
    return reference, rtl, directory


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('trace', type=Path)
    p.add_argument('--samples', type=int, default=150000)
    p.add_argument('--variant', choices=('original', 'reverb-table', 'reverb-resample', 'candidate'), default='original')
    p.add_argument('--voice', type=int, default=-1, help='Probe one voice (original only)')
    p.add_argument('--skip', type=int, default=0, help='Apply events before this sample without running samples')
    args = p.parse_args()
    reference, rtl, _ = replay(args.trace, args.samples, args.variant, args.voice, args.skip)
    print(compare(reference, rtl))


if __name__ == '__main__': main()
