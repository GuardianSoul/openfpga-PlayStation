"""DMA timing and RAM address adders shared (dma-share, after dma-m10k).

MiSTer's DMA adds one of eight increments to REP_target (its emulated
transfer-time budget) in the WORKING state; each "REP_target + n" became its
own 20-bit adder plus a 20-bit result mux. The last assignment still wins:
every branch now only picks the increment (rep_add) and one adder applies it
after the state case. ram_Adr's "+/- (cnt+1)*4" becomes one adder with a
two's complement step. Same values on the same clock edges. Independent of
dma-wd-default (prepare_dma_wd_default.py); either order works.
"""
from pathlib import Path
import sys

def R(t, old, new, n=1):
    if t.count(old) != n:
        raise ValueError('pinned DMA text changed: ' + old.strip().splitlines()[0] + f' ({t.count(old)})')
    return t.replace(old, new)

def dma_share_text(t):
    t = R(t, "      variable addtime         : integer range 0 to 511;\n",
             "      variable addtime         : integer range 0 to 511;\n"
             "      variable rep_add         : integer range 0 to 63;\n"
             "      variable rep_upd         : std_logic;\n"
             "      variable ram_step        : unsigned(22 downto 0);\n")
    t = R(t, "         errorCHOP      <= '0';\n",
             "         errorCHOP      <= '0';\n         rep_upd := '0'; rep_add := 0;\n")
    t = R(t, "                     REP_target  <= REP_target + 1;\n",
             "                     rep_upd := '1'; rep_add := 1;\n")
    t = R(t, "                           REP_target   <= REP_target + 4;\n                           if (toDevice = '1') then\n                              DMA_SPU_writeEna",
             "                           rep_add := 4;\n                           if (toDevice = '1') then\n                              DMA_SPU_writeEna")
    t = R(t, "REP_target   <= REP_target + 5;", "rep_add := 5;", 2)
    t = R(t, "REP_target   <= REP_target + 6;\n                              end if;", "rep_add := 6;\n                              end if;", 2)
    t = R(t, "                        REP_target   <= REP_target + 6;\n                        REP_required <= '1';",
             "                        rep_add := 6;\n                        REP_required <= '1';")
    t = R(t, "REP_target   <= REP_target + 4 + (to_integer(spu_timing_value) * 2);",
             "rep_add := 4 + (to_integer(spu_timing_value) * 2);")
    t = R(t, "                        REP_target <= REP_target + addtime;\n",
             "                        rep_add := addtime;\n")
    t = R(t, "                        REP_target <= REP_target + to_integer(com0_delay) - 1;\n",
             "                        rep_upd := '1'; rep_add := to_integer(com0_delay) - 1;\n")
    t = R(t, "            end case;\n\n--##############################################################\n--############################### ram read handling",
             "            end case;\n\n            if (rep_upd = '1') then\n               REP_target <= REP_target + rep_add;\n            end if;\n\n--##############################################################\n--############################### ram read handling")
    t = R(t, """               if (directionNeg = '1') then
                  ram_Adr <= std_logic_vector(unsigned(ram_Adr) - ((1 + to_integer(unsigned(ram_cnt))) * 4));
               else
                  ram_Adr <= std_logic_vector(unsigned(ram_Adr) + ((1 + to_integer(unsigned(ram_cnt))) * 4));
               end if;
""", """               -- one adder: step = +/-(cnt+1)*4; -(cnt+1) = not cnt in two's complement
               if (directionNeg = '1') then
                  ram_step := "1111111111111111111" & not unsigned(ram_cnt) & "00";
               else
                  ram_step := "000000000000000000" & (('0' & unsigned(ram_cnt)) + 1) & "00";
               end if;
               ram_Adr <= std_logic_vector(unsigned(ram_Adr) + ram_step);
""")
    return t

def apply_project(project):
    """Rewrite project/dma-m10k/dma.vhd (written by prepare_dma_m10k) in place."""
    path = Path(project) / 'dma-m10k/dma.vhd'
    path.write_text(dma_share_text(path.read_text()))
    return dict(path=str(path))


if __name__ == '__main__':
    if len(sys.argv) == 2: print(apply_project(sys.argv[1]))
    elif len(sys.argv) == 3: Path(sys.argv[2]).write_text(dma_share_text(Path(sys.argv[1]).read_text()))
    else: raise SystemExit('usage: prepare_dma_share.py PROJECT | prepare_dma_share.py IN.vhd OUT.vhd')
