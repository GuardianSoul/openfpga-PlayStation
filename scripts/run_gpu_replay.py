#!/usr/bin/env python3
"""Replay a recorded game GPU stream into the pinned MiSTer GPU and compare VRAM.

The trace comes from Beetle PSX (Mednafen's software renderer) with the
census patch (tools/ffvii-census, PSX_GPU_TRACE): every GP0/GP1 write, every
VRAM-to-CPU read and VRAM snapshots at idle frame ends. The bench
(sim/reference/tb_gpu_replay.sv) starts from the first snapshot, replays the
writes in order and dumps VRAM at every later snapshot. Each snapshot is
compared pixel for pixel; differences are summarised and written as PNG
images (MiSTer | Beetle | difference) under build/sim/gpu-replay/<name>/.

Usage: run_gpu_replay.py TRACE [--name NAME] [--candidate]. --candidate
replays into the reset-only candidate's GPU (every GPU variant). Needs a
local game trace, so it is not part of `make test`.
"""
import argparse
import collections
import re
import struct
import subprocess
import sys
import zlib
from pathlib import Path

from prepare_system_verilator import prepare
from tool_paths import ROOT, find_tool

PIXELS = 1024 * 512


def candidate_gpu(directory, log):
    """The reset-only candidate's GPU: line share, divider pool, timing prune,
    coordinates and borrowed inputs (prepare_system_verilator / transcode),
    plus every opt-in GPU transform in prepare_full_pocket order."""
    import prepare_gpu_borrowed
    import run_gpu_line_share as share
    from prepare_gpu_poly_share import poly_share_text
    from prepare_gpu_vertex_ram import vertex_ram_text
    from prepare_gpu_mul_narrow import mul_narrow_text
    from prepare_gpu_mul_retime import mul_retime_text
    from prepare_gpu_half_pipe import half_pipe_text
    from prepare_gpu_poly_onehot import onehot_text
    from prepare_gpu_no_watchdog import no_watchdog_text
    from prepare_gpu_tag_generation import tag_generation_text
    from prepare_resource_targets import digest
    original_text, original_prepare = prepare_gpu_borrowed.borrowed_text, share.prepare
    def text_for(name, text):
        text = original_text(name, text)
        if name == 'gpu_poly.vhd':
            text = onehot_text(half_pipe_text(mul_retime_text(mul_narrow_text(vertex_ram_text(poly_share_text(text))))))
        if name == 'gpu_rect.vhd':
            text = no_watchdog_text(text)
        return text
    def prepare_with_tags(target, **kwargs):
        manifest = original_prepare(target, **kwargs)
        source = next(s for s in manifest['sources'] if Path(s['path']).name == 'gpu_pixelpipeline.vhd')
        folder = Path(target) / 'gpu-tag-generation'; folder.mkdir(parents=True, exist_ok=True)
        path = folder / 'gpu_pixelpipeline.vhd'; path.write_text(tag_generation_text(Path(source['path']).read_text()))
        manifest['sources'][manifest['sources'].index(source)] = {**source, 'path': str(path), 'sha256': digest(path)}
        return manifest
    prepare_gpu_borrowed.borrowed_text, share.prepare = text_for, prepare_with_tags
    try:
        _, verilog, name = share.transcode(directory / 'candidate', True, find_tool('ghdl'), log, borrowed=True)
    finally:
        prepare_gpu_borrowed.borrowed_text, share.prepare = original_text, original_prepare
    return verilog, name


def build(directory, candidate=False):
    ghdl = find_tool('ghdl'); verilator = find_tool('verilator') or str(ROOT / 'build/tools/oss-cad-suite/bin/verilator')
    binary = directory / 'obj/Vtb_gpu_probe'
    template = ROOT / 'sim/reference/tb_gpu_replay.sv'
    stamp = directory / 'bench.sv'
    if binary.exists() and stamp.exists() and stamp.stat().st_mtime >= template.stat().st_mtime:
        return binary
    with (directory / 'build.log').open('w') as log:
        if candidate:
            verilog, name = candidate_gpu(directory, log)
        else:
            manifest = prepare(directory / 'sources')
            verilog = directory / 'system.v'
            for source in manifest['sources']:
                subprocess.run([ghdl, '-a', '--std=08', '-frelaxed', f'--work={source["library"]}', source['path']],
                               cwd=directory, stdout=log, stderr=subprocess.STDOUT, timeout=60, check=True)
            with verilog.open('w') as output:
                subprocess.run([ghdl, '--synth', '--std=08', '-frelaxed', '--vendor-library=ramip', '--out=verilog',
                                'psx_mister_psx_top_resource'], cwd=directory, stdout=output, stderr=log, timeout=180, check=True)
            from ghdl_verilog import normalize_generated_verilog
            normalize_generated_verilog(verilog)
            name = re.findall(r'^module (gpu(?:_Barch[^\s]*)?)\s*\n', verilog.read_text(), re.M)[0]
        text = verilog.read_text()
        header = text[re.search(r'^module ' + re.escape(name) + r'\s*\n', text, re.M).start():].split(');', 1)[0]
        ports = {p.lower(): p for p in re.findall(r'\b(?:input|output)\s+(?:\[\d+:\d+\]\s+)?(\w+)', header)}
        bench_text = template.read_text()
        instance = re.search(r'GPU_ENTITY gpu\(.*?\);', bench_text, re.S).group()
        bound = re.sub(r'\.(\w+)\(', lambda m: '.' + ports[m[1].lower()] + '(', instance)
        stamp.write_text(bench_text.replace(instance, bound).replace('GPU_ENTITY', name))
        subprocess.run([verilator, '--binary', '--timing', '-O3', '-j', '8', '-Wno-fatal', '--output-split', '20000',
                        '--top-module', 'tb_gpu_probe', '--Mdir', str(directory / 'obj'), str(verilog),
                        str(ROOT / 'sim/reference/mister_memory_models.sv'), str(stamp)],
                       cwd=directory, stdout=log, stderr=subprocess.STDOUT, timeout=3600, check=True)
    return binary


def convert(trace, out):
    """Trace -> stream text + initial VRAM hex; returns {frame: vram halfwords} for later snapshots."""
    data = trace.read_bytes()
    snapshots, i, first = {}, 0, True
    with (out / 'stream.txt').open('w') as stream:
        while i < len(data):
            kind, value = chr(data[i]), int.from_bytes(data[i + 1:i + 5], 'little'); i += 5
            if kind == 'D':  # dropped by Beetle on a full FIFO; the MiSTer bus stalls instead
                continue
            if kind == 'V':
                vram = data[i:i + PIXELS * 2]; i += PIXELS * 2
                if first:
                    words = struct.unpack(f'<{PIXELS // 2}I', vram)
                    (out / 'vram_initial.hex').write_text('\n'.join(f'{w:08x}' for w in words) + '\n')
                    first = False
                    continue
                snapshots[value] = vram
            stream.write(f'{kind} {value:08x}\n')
    return snapshots


def png(path, width, height, rgb):
    raw = b''.join(b'\0' + rgb[y * width * 3:(y + 1) * width * 3] for y in range(height))
    def chunk(t, d): return struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t + d) & 0xFFFFFFFF)
    path.write_bytes(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)) +
                     chunk(b'IDAT', zlib.compress(raw, 6)) + chunk(b'IEND', b''))


def to_rgb(p):
    return bytes(((p & 31) << 3, ((p >> 5) & 31) << 3, ((p >> 10) & 31) << 3))


def compare(out, frame, beetle):
    words = [int(l, 16) for l in (out / f'vram_{frame}.hex').read_text().split()]
    mister = [h for w in words for h in (w & 0xFFFF, w >> 16)]
    reference = struct.unpack(f'<{PIXELS}H', beetle)
    diff = [i for i in range(PIXELS) if mister[i] != reference[i]]
    if diff:
        rgb = bytearray()
        for y in range(512):
            for x in range(1024):
                rgb += to_rgb(mister[y * 1024 + x])
            for x in range(1024):
                rgb += to_rgb(reference[y * 1024 + x])
            for x in range(1024):
                i = y * 1024 + x
                rgb += b'\xff\x00\xff' if mister[i] != reference[i] else bytes(c // 4 for c in to_rgb(reference[i]))
        png(out / f'diff_{frame}.png', 3072, 512, bytes(rgb))
    return diff, mister, reference


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('trace'); parser.add_argument('--name', default=None)
    parser.add_argument('--candidate', action='store_true', help="the reset-only candidate's GPU variants")
    args = parser.parse_args()
    trace = Path(args.trace)
    directory = ROOT / ('build/sim/gpu-replay-candidate' if args.candidate else 'build/sim/gpu-replay')
    directory.mkdir(parents=True, exist_ok=True)
    out = directory / (args.name or trace.stem); out.mkdir(parents=True, exist_ok=True)
    binary = build(directory, args.candidate)
    snapshots = convert(trace, out)
    result = subprocess.run([str(binary), f'+stream={out / "stream.txt"}', f'+out={out}'], cwd=out,
                            capture_output=True, text=True, timeout=36000)
    (out / 'run.log').write_text(result.stdout + result.stderr)
    line = next((l for l in result.stdout.splitlines() if 'PASS GPU replay' in l), None)
    if result.returncode or not line:
        sys.exit('GPU replay failed:\n' + (result.stdout + result.stderr)[-1500:])
    print(line)
    total = 0
    for frame in sorted(snapshots):
        diff, mister, reference = compare(out, frame, snapshots[frame])
        total += len(diff)
        if diff:
            rows = collections.Counter(i // 1024 for i in diff)
            xs = [i % 1024 for i in diff]
            print(f'frame {frame}: {len(diff)} pixels differ (x {min(xs)}-{max(xs)}, y {min(rows)}-{max(rows)}); '
                  f'e.g. ({diff[0] % 1024},{diff[0] // 1024}) MiSTer {mister[diff[0]]:04x} Beetle {reference[diff[0]]:04x}')
        else:
            print(f'frame {frame}: VRAM identical')
    print(f'{len(snapshots)} snapshots, {total} differing pixels in total')


if __name__ == '__main__':
    main()
