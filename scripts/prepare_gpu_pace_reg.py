#!/usr/bin/env python3
"""Registered pacer stall in gpu_pixelpipeline.vhd (timing fix after prepare_gpu_pixel_pace.py).

K4 s6 (#1482) failed on clk2x gpu_pixelpipeline|paceDebt -> gpu|vram_BURSTCNT
(-0.140 ns 0C): paceStall = paceOn and paceDebt > 512 (an 18-bit compare)
feeds pipeline_stall, which gates the rect/poly/line VRAM request and its
position/size (state = REQUESTLINE and pipeline_stall = '0') in the same clk.

This step computes the debt's next value as a signal (paceNext: the same
decrement-to-floor and per-pixel cost as the pacer) and loads paceOver <=
paceNext > SLACK together with paceDebt <= paceNext, in the same ce scope. So
paceOver = (paceDebt > SLACK) on every clk, and paceStall = paceOn and paceOver
is the old value: cycle-exact, no added request latency. The compare moves from
the request path onto the D input of one register.

PACE_REG_CHECK=1 (bench only): compares the old expression with the new one on
every clk2x after reset; a mismatch sets a sticky flag that flips bit 0 of every
later pixel colour (ghdl --synth drops assertions). PACE_REG_MUTANT=no-semi|gt511
plants a fault.

    python3 scripts/prepare_gpu_pace_reg.py PROJECT --output OUT [--seed N]   (after the pacer and Turbo steps)
    run_gpu_bench.py --patch scripts/prepare_gpu_pace_reg.py                  (bench variant on a paced project)
"""
import os, re, sys
from pathlib import Path

MARK = 'gpu-pace-reg'
MUTANT = os.environ.get('PACE_REG_MUTANT', '')
STALL_RE = re.compile(r"   paceStall <= '1' when \(paceOn = '1' and paceDebt > (\d+)\) else '0'; -- gpu-pixel-pace\n")
BLOCK_RE = re.compile(
    r"            if \(paceDebt >= paceFloor \+ 256\) then paceDebt <= paceDebt - 256; else paceDebt <= paceFloor; end if;\n"
    r"            if \(stage1_valid = '1' and pipeline_stall = '0' and stage1_texture = '0'\) then\n"
    r"               if \(stage1_transparent = '1'\) then [^\n]*\n"
    r"                  if \(paceDebt >= paceFloor \+ 256\) then paceDebt <= paceDebt - 256 \+ (\d+); else paceDebt <= paceFloor \+ \1; end if;\n"
    r"               else\n"
    r"                  if \(paceDebt >= paceFloor \+ 256\) then paceDebt <= paceDebt - 256 \+ (\d+); else paceDebt <= paceFloor \+ \2; end if;\n"
    r"               end if;\n"
    r"            end if;\n")
DECL = "   signal paceStall           : std_logic; -- gpu-pixel-pace\n"
COLOR = "   pixelColor  <= stage6_alphabit & stage6_cb(7 downto 3) & stage6_cg(7 downto 3) & stage6_cr(7 downto 3);\n"


def transform(text):
    if MARK in text: raise SystemExit(f'{MARK}: already applied')
    s, b = STALL_RE.findall(text), BLOCK_RE.findall(text)
    if len(s) != 1 or len(b) != 1 or text.count(DECL) != 1:
        raise SystemExit(f'{MARK}: pacer text not found (run prepare_gpu_pixel_pace first)')
    slack = int(s[0]); semi, flat = b[0]
    if MUTANT == 'no-semi': semi = '0'
    cmp_slack = slack - 1 if MUTANT == 'gt511' else slack
    text = text.replace(DECL, DECL +
        f"   signal paceDec             : signed(17 downto 0); -- {MARK}\n"
        f"   signal paceNext            : signed(17 downto 0); -- {MARK}\n"
        f"   signal paceOver            : std_logic := '0'; -- {MARK}: paceDebt > {slack}, registered\n")
    text = STALL_RE.sub(lambda m:
        f"   -- {MARK}: the debt's next value; paceOver is loaded with its compare next to paceDebt\n"
        f"   paceDec  <= paceDebt - 256 when (paceDebt >= paceFloor + 256) else paceFloor; -- {MARK}\n"
        f"   paceNext <= paceDec + {semi} when (stage1_valid = '1' and pipeline_stall = '0' and stage1_texture = '0' and stage1_transparent = '1') else\n"
        f"               paceDec + {flat} when (stage1_valid = '1' and pipeline_stall = '0' and stage1_texture = '0') else paceDec; -- {MARK}\n"
        f"   paceStall <= paceOn and paceOver; -- {MARK} (was: paceOn = '1' and paceDebt > {slack})\n", text)
    text = BLOCK_RE.sub(lambda m:
        f"            paceDebt <= paceNext; -- {MARK}: same value as the pacer's update\n"
        f"            if (paceNext > {cmp_slack}) then paceOver <= '1'; else paceOver <= '0'; end if; -- {MARK}\n", text)
    if os.environ.get('PACE_REG_CHECK') == '1':
        if text.count(COLOR) != 1: raise SystemExit(f'{MARK}: pixelColor anchor changed')
        text = text.replace(DECL, DECL + f"   signal paceMismatch        : std_logic := '0'; -- {MARK} check\n")
        text = text.replace(COLOR, COLOR.replace(";\n", f" xor (15 downto 1 => '0', 0 => paceMismatch); -- {MARK} check\n") +
            f"   process (clk2x) begin -- {MARK} check: old compare against the register\n"
            f"      if rising_edge(clk2x) then\n"
            f"         if (reset = '0' and ((paceDebt > {slack}) /= (paceOver = '1'))) then paceMismatch <= '1'; end if;\n"
            f"      end if;\n"
            f"   end process;\n")
    return text


def patch_file(name, text):   # run_gpu_bench.py --patch hook
    return transform(text) if name == 'gpu_pixelpipeline.vhd' else text


def project(src, out, seed=None):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from prepare_save_checksum import prepare
    seed, changed = prepare(src, out, seed, {'gpu_pixelpipeline.vhd': transform})
    print(f'{MARK}: snapshot {Path(out).resolve()} (seed {seed})')
    for path, (old, new) in changed.items(): print(f'{MARK}: patched {path} (sha256 {old[:16]} -> {new[:16]})')


if __name__ == '__main__':
    if '--output' not in sys.argv: raise SystemExit(__doc__)
    seed = int(sys.argv[sys.argv.index('--seed') + 1]) if '--seed' in sys.argv else None
    project(sys.argv[1], sys.argv[sys.argv.index('--output') + 1], seed)
