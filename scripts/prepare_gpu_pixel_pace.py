#!/usr/bin/env python3
"""PS1-rate pixel pacing in gpu_pixelpipeline.vhd (docs/timing-gpu.md, too-fast rows).

The core draws untextured pixels faster than a PS1 (ps1-tests gpu/bandwidth:
flat rectangles and quads 0.93-0.94x, semi-transparent 0.88-0.91x). This adds
a debt counter (8 fractional bits, in clk2x): every pixel that leaves stage 1
adds its PS1 cost, every clk2x takes one clk off, and the pipeline stalls one
clk while the debt is above SLACK. Costs per pixel, from the PS1 rows (hblanks
per 400 calls x 4,305.6 clk2x per hblank / pixels): opaque untextured 1.07
clk2x (Rectangle 1.066, Polygon quad 1.076, 1/4 and 1/2 off screen 1.09-1.11),
semi-transparent untextured 1.613 (Rectangle 1.606, quad 1.633). Textured
pixels get no pacing: the PS1 log has only a transparent 4-bit sprite and a
scaled 15-bit quad, too little to model the texture cache.

Only the stall changes; pixels and their order are the old pipeline's. The
existing drawSlow toggle is untouched. PACE_ON (a new input, default '1') turns
the pacing off, for Turbo Mode.

    python3 scripts/prepare_gpu_pixel_pace.py FILE            (in place)
    python3 scripts/prepare_gpu_pixel_pace.py PROJECT --output OUT [--seed N]   (frozen snapshot, Area chain step)
    run_gpu_bench.py --patch scripts/prepare_gpu_pixel_pace.py      (bench variant)
"""
import sys
from pathlib import Path

import os
FLAT, SLACK = 274, 512                   # 1.0703 clk2x per pixel; up to 2 clk ahead
# Semi-transparent pixels: the PS1 rows give 1.6133. The semi path waits on its line read in bursts; with credit
# only inside a command (paceActive) the semi rows land at 0.996-1.005x PS1 (docs/timing-gpu.md); 0 = not paced.
SEMI = int(os.environ.get('PACE_SEMI', '413'))   # 1.6133 clk2x per semi-transparent pixel
CREDIT = int(os.environ.get('PACE_CREDIT', '65280'))  # clks spent waiting inside a command count, up to CREDIT/256 clk
# (a semi row's line read is ~130 clk); with no command running (paceActive low) the debt never goes below 0
MARK = 'gpu-pixel-pace'


def transform(text):
    if MARK in text: raise SystemExit('already paced')
    port = '      drawSlow             : in  std_logic;\n'
    stall = "   pipeline_stall <= '1' when (pixelStall = '1' or state /= IDLE or slowdown = '1') else '0';\n"
    slow = "            -- slowdown\n            if (slowdown = '1') then\n"
    decl = "   signal slowdown            : std_logic := '0';\n"
    for a in (port, stall, slow, decl):
        if text.count(a) != 1: raise SystemExit(f'{MARK}: anchor changed: {a.strip()[:60]}')
    text = text.replace(port, port + f"      paceOn               : in  std_logic := '1'; -- {MARK}\n"
                                f"      paceActive           : in  std_logic := '1'; -- {MARK}: a GP0 command is running\n")
    text = text.replace(decl, decl + f"   signal paceDebt            : signed(17 downto 0) := (others => '0'); -- {MARK}\n"
                                     f"   signal paceFloor           : signed(17 downto 0); -- {MARK}\n"
                                     f"   signal paceStall           : std_logic; -- {MARK}\n")
    text = text.replace(stall, f"   paceFloor <= to_signed(-{CREDIT}, 18) when paceActive = '1' else (others => '0'); -- {MARK}\n"
                        f"   paceStall <= '1' when (paceOn = '1' and paceDebt > {SLACK}) else '0'; -- {MARK}\n" +
                        stall.replace("slowdown = '1')", "slowdown = '1' or paceStall = '1')"))
    text = text.replace(slow, f"""            -- {MARK}: PS1 cost of each untextured pixel leaving stage 1, one clk2x paid per clk
            -- one clk paid per clk, down to -CREDIT: time spent waiting on memory counts toward the PS1 time
            if (paceDebt >= paceFloor + 256) then paceDebt <= paceDebt - 256; else paceDebt <= paceFloor; end if;
            if (stage1_valid = '1' and pipeline_stall = '0' and stage1_texture = '0') then
               if (stage1_transparent = '1') then   -- SEMI = 0: no extra time
                  if (paceDebt >= paceFloor + 256) then paceDebt <= paceDebt - 256 + {SEMI}; else paceDebt <= paceFloor + {SEMI}; end if;
               else
                  if (paceDebt >= paceFloor + 256) then paceDebt <= paceDebt - 256 + {FLAT}; else paceDebt <= paceFloor + {FLAT}; end if;
               end if;
            end if;
""" + slow)
    return text


GPU_HOOK = "      drawSlow             => drawSlow,\n"


def transform_gpu(text):
    if text.count(GPU_HOOK) != 1: raise SystemExit(f'{MARK}: gpu.vhd pixel pipeline instance changed')
    return text.replace(GPU_HOOK, GPU_HOOK + f"      paceActive           => not proc_idle, -- {MARK}\n")


def patch_file(name, text):   # run_gpu_bench.py --patch hook
    if name == 'gpu_pixelpipeline.vhd': return transform(text)
    if name == 'gpu.vhd': return transform_gpu(text)
    return text


def project(src, out, seed=None):
    """Frozen snapshot of a prepared project (prepare_save_checksum.prepare pattern), both GPU files patched."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from prepare_save_checksum import prepare
    seed, changed = prepare(src, out, seed, {'gpu_pixelpipeline.vhd': transform, 'gpu.vhd': transform_gpu})
    print(f'{MARK}: snapshot {Path(out).resolve()} (seed {seed})')
    for path, (old, new) in changed.items(): print(f'{MARK}: patched {path} (sha256 {old[:16]} -> {new[:16]})')
    print(f'{MARK}: flat {FLAT}/256, semi {SEMI}/256 clk2x per pixel, slack {SLACK}, credit {CREDIT} (1/256 clk2x)')


if __name__ == '__main__':
    if '--output' in sys.argv:
        seed = int(sys.argv[sys.argv.index('--seed') + 1]) if '--seed' in sys.argv else None
        project(sys.argv[1], sys.argv[sys.argv.index('--output') + 1], seed)
    else: f = Path(sys.argv[1]); f.write_text(transform(f.read_text())); print(f'{MARK}: {f}')
