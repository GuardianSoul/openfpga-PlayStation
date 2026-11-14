#!/usr/bin/env python3
"""Turbo Mode turns the GPU pixel pacing off (follow-up to prepare_gpu_pixel_pace.py).

gpu gets an input paceOn (default '1'), passed to the pixel pipeline's paceOn;
psx_top drives it with "not TURBO_COMP", the same Turbo setting that already
drives the CPU's TURBO (prepare_turbo_setting.py: Turbo On sets TURBO_MEM and
TURBO_COMP). Turbo off (the default) keeps the PS1 pacing; Turbo on draws at
today's speed. The setting is static while a game runs; TURBO_COMP already
feeds clk1x/clk2x logic in psx_top, so no new crossing.

    python3 scripts/prepare_gpu_pace_turbo.py SOURCE --output OUT [--seed N]   (after prepare_gpu_pixel_pace)
"""
import sys
from pathlib import Path

MARK = 'gpu-pace-turbo'
GPU_PORT = '      drawSlow             : in  std_logic;\n'
GPU_MAP = '      paceActive           => not proc_idle, -- gpu-pixel-pace\n'
TOP_MAP = '      drawSlow             => drawSlow,\n'


def gpu_text(text):
    if MARK in text: raise SystemExit(f'{MARK}: already applied')
    for a in (GPU_PORT, GPU_MAP):
        if text.count(a) != 1: raise SystemExit(f'{MARK}: gpu.vhd anchor missing (run prepare_gpu_pixel_pace first): {a.strip()}')
    text = text.replace(GPU_PORT, GPU_PORT + f"      paceOn               : in  std_logic := '1'; -- {MARK}\n")
    return text.replace(GPU_MAP, GPU_MAP + f"      paceOn               => paceOn, -- {MARK}\n")


def top_text(text):
    if MARK in text: raise SystemExit(f'{MARK}: already applied')
    if text.count(TOP_MAP) != 1: raise SystemExit(f'{MARK}: psx_top.vhd gpu instance changed')
    if text.count('      TURBO_COMP            : in  std_logic;\n') != 1: raise SystemExit(f'{MARK}: psx_top has no TURBO_COMP')
    return text.replace(TOP_MAP, TOP_MAP + f"      paceOn               => not TURBO_COMP, -- {MARK}: Turbo draws at full speed\n")


def project(src, out, seed=None):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from prepare_save_checksum import prepare
    seed, changed = prepare(src, out, seed, {'gpu.vhd': gpu_text, 'psx_top.vhd': top_text})
    print(f'{MARK}: snapshot {Path(out).resolve()} (seed {seed})')
    for path, (old, new) in changed.items(): print(f'{MARK}: patched {path} (sha256 {old[:16]} -> {new[:16]})')


if __name__ == '__main__':
    if '--output' not in sys.argv: raise SystemExit(__doc__)
    seed = int(sys.argv[sys.argv.index('--seed') + 1]) if '--seed' in sys.argv else None
    project(sys.argv[1], sys.argv[sys.argv.index('--output') + 1], seed)
