#!/usr/bin/env python3
"""Room census GPU items 7 and 10: gpu_poly's mul9s pair as one difference, shared drawing-area compares (area; gpu_poly.vhd).

`mul` (census 7). imul9s1 / imul9s2 form p1 = muldiv1In1 * muldiv1In2 and
p2 = muldiv2In1 * muldiv2In2 (9 x 13 signed, 22 bits); both are registered
(muldivResult1clk / 2clk) in the same CALCCOLOR2 / CALCTEXTURE2 branches, and
their only reader is cse_gradDiff = resize(clk1 - clk2, 21). Here the 22-bit
difference p1 - p2 (mod 2^22, as the old 22-bit subtract) is registered once
where the two products were, and cse_gradDiff = resize(diff, 21): the same
value in every cycle, one register bank and one subtractor fewer, and Quartus
can map a*b - c*d to one DSP in sum-of-two mode (frees one DSP).

`area` (census 10). PREPARELINE compares xStart with drawingAreaLeft and
drawingAreaRight (xPosInLeft / xPosInRight, and `skip` when xStart > right);
PROCPIXELS compares xPos + 1 with the same two. The states are exclusive, so
one comparator pair takes cmpX = xPos + 1 in PROCPIXELS and max(xStart, 0)
elsewhere. For xStart < 0 the old code set xPosInLeft = (left = 0) and
xPosInRight = 1, which are the compares of 0; xStart > right is false for
xStart < 0 either way. And the 1024 - xStart size becomes (1023 - xStart) + 1,
one subtractor with a minuend mux instead of two.

POLY_SHARE_PARTS=mul,area (default both). POLY_SHARE_MUTANT (self test):
`add` (p1 + p2), `noclamp` (PREPARELINE compares the unclamped xStart).

    python3 scripts/prepare_gpu_poly_share.py PROJECT --output OUT [--seed N]
    run_gpu_bench.py --patch scripts/prepare_gpu_poly_share.py
"""
import os, re, sys
from pathlib import Path

MARK = 'gpu-poly-share'
MUTANT = os.environ.get('POLY_SHARE_MUTANT', '')
PARTS = set(os.environ.get('POLY_SHARE_PARTS', 'mul,area').split(','))

M_DECL = "   signal muldivResult2clk    : signed(21 downto 0) := (others => '0');    \n"
M_CSE = "   cse_gradDiff <= resize(muldivResult1clk - muldivResult2clk, 21);   -- lab-cse gpu-grad\n"
M_REG = "muldivResult1clk <= muldivResult1; muldivResult2clk <= muldivResult2;"
M_INST = re.compile(r"   imul9s1 : entity work\.mul9s\n.*?result   => muldivResult2\n   \);\n", re.S)

A_PL = ("                  if (xStart(43 downto 32) < 0) then\n"
        "                     xPos  <= (others => '0');\n"
        "                     if (drawingAreaLeft = 0) then xPosInLeft <= '1'; else xPosInLeft <= '0'; end if;\n"
        "                     xPosInRight <= '1';\n"
        "                  else\n"
        "                     xPos  <= xStart(43 downto 32);\n"
        "                     if (xStart(43 downto 32) >= to_integer(drawingAreaLeft)) then xPosInLeft <= '1'; else xPosInLeft <= '0'; end if;\n"
        "                     if (xStart(43 downto 32) <= to_integer(drawingAreaRight)) then xPosInRight <= '1'; else xPosInRight <= '0'; end if;\n"
        "                  end if;\n")
A_SIZE = ("                  elsif (xStart(43 downto 32) >= 0 and xEnd(43 downto 32) > 1023) then\n"
          "                     xSize <= to_unsigned(1024, 11) - unsigned(xStart(41 downto 32));\n"
          "                  else \n"
          "                     xSize <= ('0' & (unsigned(xEnd(41 downto 32)) - unsigned(xStart(41 downto 32)))) + 1;\n"
          "                  end if;\n")
A_SKIP = ("                  if (to_integer(xStart(43 downto 32)) > drawingAreaRight) then\n"
          "                     skip := '1';\n"
          "                  end if;                  \n")
A_PP = ("                     if (xPos + 1 >= to_integer(drawingAreaLeft)) then xPosInLeft <= '1'; else xPosInLeft <= '0'; end if;\n"
        "                     if (xPos + 1 <= to_integer(drawingAreaRight)) then xPosInRight <= '1'; else xPosInRight <= '0'; end if;\n")
A_DECL = "   signal xSize               : unsigned(10 downto 0) := (others => '0'); \n"


def transform(text):
    if MARK in text: raise SystemExit(f'{MARK}: already applied')
    if 'mul' in PARTS:
        for a in (M_DECL, M_CSE):
            if text.count(a) != 1: raise SystemExit(f'{MARK}: gpu_poly anchor changed: {a.strip()[:60]}')
        n = text.count(M_REG)
        if n != 10 or len(M_INST.findall(text)) != 1: raise SystemExit(f'{MARK}: mul9s pair layout changed ({n} result registers)')
        op = '+' if MUTANT == 'add' else '-'
        text = text.replace(M_DECL, M_DECL +
            f"   signal mulDiff, mulDiffClk : signed(21 downto 0) := (others => '0'); -- {MARK}: p1 - p2 (mod 2^22), registered once\n")
        text = M_INST.sub(f"   -- {MARK}: imul9s1 / imul9s2 as one difference (one DSP in sum-of-two mode)\n"
                          f"   mulDiff <= (muldiv1In1 * muldiv1In2) {op} (muldiv2In1 * muldiv2In2);\n", text)
        text = text.replace(M_REG, "mulDiffClk <= mulDiff;")   # no comment: the rest of the line follows
        text = text.replace(M_CSE, f"   cse_gradDiff <= resize(mulDiffClk, 21);   -- lab-cse gpu-grad, {MARK}\n")
        for w in ('muldivResult1clk', 'muldivResult2clk', 'muldivResult1 ', 'muldivResult2 '):
            rest = [l for l in text.splitlines() if w in l and 'signal' not in l]
            if rest: raise SystemExit(f'{MARK}: {w.strip()} still used: {rest[0].strip()[:80]}')
    if 'area' in PARTS:
        for a in (A_PL, A_SIZE, A_SKIP, A_PP, A_DECL):
            if text.count(a) != 1: raise SystemExit(f'{MARK}: gpu_poly anchor changed: {a.strip()[:60]}')
        clamp = "xStart(43 downto 32)" if MUTANT == 'noclamp' else "(others => '0') when xStart(43 downto 32) < 0 else xStart(43 downto 32)"
        text = text.replace(A_DECL, A_DECL +
            f"   signal cmpX                : signed(11 downto 0); -- {MARK}: one drawing-area comparator pair\n"
            f"   signal cmpGeL, cmpLeR      : std_logic; -- {MARK}\n"
            f"   signal sizeMin             : unsigned(9 downto 0); -- {MARK}: 1023 or xEnd, one subtractor\n")
        k = re.compile(r"\nbegin[ \t]*\n").search(text, text.index('architecture')).end()
        text = text[:k] + (
            f"   -- {MARK}: PREPARELINE / PROCPIXELS share one comparator pair and one size subtractor\n"
            f"   cmpX   <= xPos + 1 when state = PROCPIXELS else {clamp};\n"
            "   cmpGeL <= '1' when cmpX >= to_integer(drawingAreaLeft) else '0';\n"
            "   cmpLeR <= '1' when cmpX <= to_integer(drawingAreaRight) else '0';\n"
            "   sizeMin <= to_unsigned(1023, 10) when (xStart(43 downto 32) >= 0 and xEnd(43 downto 32) > 1023) else unsigned(xEnd(41 downto 32));\n\n") + text[k:]
        text = text.replace(A_PL,
            "                  if (xStart(43 downto 32) < 0) then\n"
            "                     xPos  <= (others => '0');\n"
            "                  else\n"
            "                     xPos  <= xStart(43 downto 32);\n"
            "                  end if;\n"
            f"                  xPosInLeft <= cmpGeL; xPosInRight <= cmpLeR; -- {MARK}: compares of max(xStart, 0)\n")
        text = text.replace(A_SIZE,
            "                  else\n"
            f"                     xSize <= ('0' & (sizeMin - unsigned(xStart(41 downto 32)))) + 1; -- {MARK}: 1024 - x = (1023 - x) + 1\n"
            "                  end if;\n")
        text = text.replace(A_SKIP,
            f"                  if (cmpLeR = '0') then -- {MARK}: xStart > right (false for xStart < 0 either way)\n"
            "                     skip := '1';\n"
            "                  end if;\n")
        text = text.replace(A_PP, f"                     xPosInLeft <= cmpGeL; xPosInRight <= cmpLeR; -- {MARK}: compares of xPos + 1\n")
    return text


def patch_file(name, text):   # run_gpu_bench.py --patch hook
    return transform(text) if name == 'gpu_poly.vhd' else text


def project(src, out, seed=None):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from prepare_save_checksum import prepare
    seed, changed = prepare(src, out, seed, {'gpu_poly.vhd': transform})
    print(f'{MARK}: snapshot {Path(out).resolve()} (seed {seed}) parts {sorted(PARTS)}{" mutant " + MUTANT if MUTANT else ""}')
    for path, (old, new) in changed.items(): print(f'{MARK}: patched {path} (sha256 {old[:16]} -> {new[:16]})')


if __name__ == '__main__':
    if '--output' not in sys.argv: raise SystemExit(__doc__)
    seed = int(sys.argv[sys.argv.index('--seed') + 1]) if '--seed' in sys.argv else None
    project(sys.argv[1], sys.argv[sys.argv.index('--output') + 1], seed)
