#!/usr/bin/env python3
"""Copy a captured Pocket top with its GPU sources taken from another run.

The captured early-hardware base (a measured spu-scalar-full-top run) keeps
every non-GPU file. Its GPU sources (gpu.vhd, the line/poly/rect engines,
pixel pipeline, copy engines and read math) are replaced by those of a
fresh prepare_full_pocket.py --prepare-only run with a different GPU variant
set, and the GPU divider pool is dropped when that run has none. This is how
the 2026-10-15 base without gpu-divider-pool/gpu-borrowed-inputs was made
(the pool serialised polygon setup's six divisions; Tekken 3's fighters
were see-through until it was reverted, docs/gpu-divider-pool.md).
"""
import argparse
import re
import shutil
from pathlib import Path

GPU_FILES = ('gpu.vhd', 'gpu_poly.vhd', 'gpu_line.vhd', 'gpu_rect.vhd', 'gpu_pixelpipeline.vhd', 'gpu_fillVram.vhd',
             'gpu_vram2vram.vhd', 'gpu_vram2cpu.vhd', 'psx_gpu_read_math.vhd')
TEXT = ('.qsf', '.sv', '.v', '.vhd', '.sdc', '.json', '.tcl', '.qpf')


def paths(qsf, name):
    return re.findall(r'"([^"]*/' + re.escape(name) + r')"', qsf)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('captured', type=Path, help='captured top directory (contains apf_top.qsf)')
    parser.add_argument('gpu_run', type=Path, help='prepare_full_pocket.py --prepare-only run with the wanted GPU variants')
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    captured, run, out = args.captured.resolve(), args.gpu_run.resolve(), args.output.resolve()
    if out.exists(): parser.error(f'{out} exists')
    shutil.copytree(captured, out, ignore=shutil.ignore_patterns('db', 'incremental_db', 'output_files'))
    for p in out.rglob('*'):
        if p.is_file() and p.suffix in TEXT:
            try: text = p.read_text()
            except UnicodeDecodeError: continue
            if str(captured) in text: p.write_text(text.replace(str(captured), str(out)))
    qsf_path = out/'apf_top.qsf'; q = qsf_path.read_text(); fresh = (run/'apf_top.qsf').read_text()
    destination = out/'gpu-sources'; destination.mkdir()
    for name in GPU_FILES:
        old, new = paths(q, name), paths(fresh, name)
        if len(old) != 1 or len(new) != 1: parser.error(f'{name}: expected one source in each project, found {len(old)}/{len(new)}')
        copy = destination/name; shutil.copyfile(new[0], copy)
        q = q.replace(f'"{old[0]}"', f'"{copy}"')
    if not paths(fresh, 'psx_gpu_divider_pool.vhd'):
        pool = re.findall(r'^set_global_assignment -name VHDL_FILE "[^"]*/psx_gpu_divider_pool\.vhd".*\n', q, re.M)
        if len(pool) > 1: parser.error('more than one divider pool source')
        for line in pool: q = q.replace(line, '')
    qsf_path.write_text(q)
    print(out)


if __name__ == '__main__': main()
