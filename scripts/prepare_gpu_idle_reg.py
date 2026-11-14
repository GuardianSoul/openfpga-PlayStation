#!/usr/bin/env python3
"""Registered fifoOut_idle terms in gpu.vhd (timing fix, docs/timing-gpu.md "K4 clk2x pixel64filled path").

fifoOut_idle = fifoOut_Empty and not fifoOut_Wr and not fifoOut_Wr_1 and not pixel64filled
reaches vram_BURSTCNT combinationally: the pixel pipeline's palette request
(state = REQUESTPALETTE and fifoOut_idle = '1') gates its request and its
position/size, which feed the request mux, rounded_read_size and BURSTCNT.
K4 s12 (#1477) missed by 43 ps on pixel64filled -> vram_BURSTCNT.

This step keeps fifoOut_Empty (a FIFO register) as is and replaces the other
three terms by one register, fifoOut_noWr, loaded with their next-state value:
  Wr_1'   = Wr
  Wr', filled' from the pixel-collect process:
    reset                   -> Wr' = 0, filled' = 0
    ce = 0                  -> Wr' = 0, filled' = filled
    vramFill_pixelWrite     -> Wr' = 1
    pixelWrite              -> filled' = 1
    timeout fires           -> Wr' = 1   (pixel64timeout > 0 and (= 1 or pipeline_busy = 0))
    otherwise               -> Wr' = 0, filled' = filled
  so noWr' = not Wr and (reset or (not filled and not (ce and (vramFill_pixelWrite
             or pixelWrite or (timeout > 0 and (timeout = 1 or pipeline_busy = 0)))))).
fifoOut_idle keeps its value every clk (cycle-exact, no request latency); its
cone shrinks from four registers to two. The pixel pipeline gets its own copy
(fifoOut_noWrPipe, dont_merge) so the palette path has no fan-out to the five
other modules.

IDLE_REG_CHECK=1 (bench only) compares the old expression with both new copies
on every clk2x after reset; a mismatch sets a sticky flag that flips bit 0 of
every later VRAM write, so it shows in the VRAM dumps (ghdl --synth drops
assertions). IDLE_REG_MUTANT=no-pixelwrite|no-timeout|no-wr plants a fault.

    python3 scripts/prepare_gpu_idle_reg.py PROJECT --output OUT [--seed N]   (frozen snapshot, Area chain step)
    run_gpu_bench.py --patch scripts/prepare_gpu_idle_reg.py                   (bench variant)
"""
import os, sys
from pathlib import Path

MARK = 'gpu-idle-reg'
OLD_IDLE = "   fifoOut_idle <= '1' when (fifoOut_Empty = '1' and fifoOut_Wr = '0' and fifoOut_Wr_1 = '0' and pixel64filled = '0') else '0';\n"
DECL = "   signal fifoOut_idle              : std_logic;\n"
PIPE_MAP = ("      fifoOut_idle         => fifoOut_idle,\n"
            "      pipeline_busy        => pipeline_busy,\n"
            "      pipeline_stall       => pipeline_stall,      \n"
            "      pipeline_new         => pipeline_new,        \n")   # the pixel pipeline instance
DIN = "                        vram_DIN        <= fifoOut_Dout(63 downto 0);\n"
# MUTANT (equivalence-check self test): drop one next-state term
MUTANT = os.environ.get('IDLE_REG_MUTANT', '')


def next_expr(name_ok=True):
    terms = ["vramFill_pixelWrite = '1'", "pixelWrite = '1'",
             "(pixel64timeout > 0 and (pixel64timeout = 1 or pipeline_busy = '0'))"]
    if MUTANT == 'no-pixelwrite': terms.remove("pixelWrite = '1'")
    if MUTANT == 'no-timeout': terms = terms[:2]
    busy = ' or '.join(terms)
    wr = "fifoOut_Wr = '0'" if MUTANT != 'no-wr' else "true"
    return (f"({wr} and (reset = '1' or (pixel64filled = '0' and not (ce = '1' and ({busy})))))")


def gpu_text(text):
    if MARK in text: raise SystemExit(f'{MARK}: already applied')
    for a in (OLD_IDLE, DECL, PIPE_MAP):
        if text.count(a) != 1: raise SystemExit(f'{MARK}: gpu.vhd anchor changed: {a.strip()[:70]}')
    text = text.replace(DECL, DECL +
        f"   signal fifoOut_noWr              : std_logic := '1'; -- {MARK}: not Wr, Wr_1, pixel64filled (registered)\n"
        f"   signal fifoOut_noWrPipe          : std_logic := '1'; -- {MARK}: the pixel pipeline's copy\n"
        f"   signal fifoOut_idlePipe          : std_logic; -- {MARK}\n"
        f"   attribute dont_merge : boolean; -- {MARK}\n"
        f"   attribute dont_merge of fifoOut_noWrPipe : signal is true; -- {MARK}\n")
    new = (f"   -- {MARK}: fifoOut_Wr, fifoOut_Wr_1 and pixel64filled folded into one register holding\n"
           f"   -- their next-state NOR (same value every clk as the old expression)\n"
           f"   process (clk2x) begin\n"
           f"      if rising_edge(clk2x) then\n"
           f"         if {next_expr()} then fifoOut_noWr <= '1'; fifoOut_noWrPipe <= '1';\n"
           f"         else fifoOut_noWr <= '0'; fifoOut_noWrPipe <= '0'; end if;\n"
           f"      end if;\n"
           f"   end process;\n"
           f"   fifoOut_idle     <= fifoOut_Empty and fifoOut_noWr; -- {MARK}\n"
           f"   fifoOut_idlePipe <= fifoOut_Empty and fifoOut_noWrPipe; -- {MARK}\n")
    check = os.environ.get('IDLE_REG_CHECK') == '1'
    if check:   # synthesizable (the bench goes through ghdl --synth): sticky mismatch flips bit 0 of later VRAM writes
        if text.count(DIN) != 1: raise SystemExit(f'{MARK}: vram_DIN anchor changed')
        text = text.replace(DIN, DIN.replace('fifoOut_Dout(63 downto 0);', "fifoOut_Dout(63 downto 0) xor (63 downto 1 => '0', 0 => idleMismatch); -- " + MARK + " check"))
        text = text.replace(DECL, DECL + f"   signal idleMismatch              : std_logic := '0'; -- {MARK} check\n")
        new += (f"   process (clk2x) begin -- {MARK} check: old expression against the new registers\n"
                f"      if rising_edge(clk2x) then\n"
                f"         if (reset = '0' and (fifoOut_idle /= fifoOut_idlePipe or\n"
                f"             fifoOut_idle /= (fifoOut_Empty and not fifoOut_Wr and not fifoOut_Wr_1 and not pixel64filled))) then\n"
                f"            idleMismatch <= '1';\n"
                f"         end if;\n"
                f"      end if;\n"
                f"   end process;\n")
    text = text.replace(OLD_IDLE, new)
    return text.replace(PIPE_MAP, PIPE_MAP.replace('=> fifoOut_idle,', f'=> fifoOut_idlePipe, -- {MARK}'))


def patch_file(name, text):   # run_gpu_bench.py --patch hook
    return gpu_text(text) if name == 'gpu.vhd' else text


def project(src, out, seed=None):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from prepare_save_checksum import prepare
    seed, changed = prepare(src, out, seed, {'gpu.vhd': gpu_text})
    print(f'{MARK}: snapshot {Path(out).resolve()} (seed {seed})')
    for path, (old, new) in changed.items(): print(f'{MARK}: patched {path} (sha256 {old[:16]} -> {new[:16]})')


if __name__ == '__main__':
    if '--output' not in sys.argv: raise SystemExit(__doc__)
    seed = int(sys.argv[sys.argv.index('--seed') + 1]) if '--seed' in sys.argv else None
    project(sys.argv[1], sys.argv[sys.argv.index('--output') + 1], seed)
