#!/usr/bin/env python3
"""cd_top: the eight sector-buffer sizes in an MLAB, their non-zero flags in registers.

sectorBufferSizes (8 x 10 bits of registers) is tested for zero at three
indices in one clock (write + 4, write, read) and its value is read in one
place only, copySize at the read pointer. This step keeps eight flags
(sectorBufferNz) for every test and the clear-all, and puts the sizes in an
array written where a size is stored (procSize at the write pointer) and read
unregistered for copySize, so every decision and value keeps its clock. The
save-state readback/load of the sizes (ss_out/ss_in 96..103, unused by the
reset-only builds: SS_DataRead is open) carries the flags only.
Run after the cd_top steps (prepare_cd_timing_ps1.py among them).
Usage: prepare_cd_buffer_sizes.py PROJECT
"""
from pathlib import Path
import re
import sys

MARK = 'cd-buffer-sizes'


def once(text, old, new, what):
    if text.count(old) != 1: raise ValueError(f'{MARK}: {what} anchor found {text.count(old)} times')
    return text.replace(old, new)


def sizes_text(t):
    if MARK in t: raise ValueError(f'{MARK} already applied')
    t = once(t, "   signal sectorBufferSizes         : tsectorBufferSizes;\n",
             f"   signal sectorBufferSizes         : tsectorBufferSizes;   -- {MARK}: written and read for copySize only\n"
             f"   attribute ramstyle : string;\n"
             f"   attribute ramstyle of sectorBufferSizes : signal is \"MLAB\";   -- {MARK}\n"
             f"   signal sectorBufferNz            : std_logic_vector(0 to 7) := (others => '0');   -- {MARK}: size /= 0\n",
             'declaration')
    t = once(t, "sectorBufferSizes(to_integer((writeSectorPointer + 4))) /= 0) then -- allow up to 4 buffers to be filled",
             "sectorBufferNz(to_integer((writeSectorPointer + 4))) = '1') then -- allow up to 4 buffers to be filled", 'fill check')
    t = once(t, "      ss_out(i + 96)(11 downto 0) <= std_logic_vector(to_unsigned(sectorBufferSizes(i), 10)) & \"00\";\n",
             f"      ss_out(i + 96)(11 downto 0) <= (2 => sectorBufferNz(i), others => '0');   -- {MARK}: flags only (reset-only builds)\n",
             'save-state readback')
    t = once(t, "               sectorBufferSizes(i) <= to_integer(unsigned(ss_in(i + 96)(11 downto 2)));\n",
             f"               if (unsigned(ss_in(i + 96)(11 downto 2)) /= 0) then sectorBufferNz(i) <= '1'; else sectorBufferNz(i) <= '0'; end if;   -- {MARK}\n",
             'save-state load')
    t = once(t, "                  if (sectorBufferSizes(to_integer(writeSectorPointer)) /= 0) then\n                     --error <= '1';",
             "                  if (sectorBufferNz(to_integer(writeSectorPointer)) = '1') then\n                     --error <= '1';", 'overrun check')
    t = once(t, "                  sectorBufferSizes(to_integer(writeSectorPointer)) <= procSize;\n",
             "                  sectorBufferSizes(to_integer(writeSectorPointer)) <= procSize;\n"
             f"                  if (procSize /= 0) then sectorBufferNz(to_integer(writeSectorPointer)) <= '1'; else sectorBufferNz(to_integer(writeSectorPointer)) <= '0'; end if;   -- {MARK}\n",
             'size store')
    t = once(t, "                     sectorBufferSizes(to_integer(readSectorPointer)) <= 0;\n"
                "                     if (sectorBufferSizes(to_integer(readSectorPointer)) = 0) then\n",
             f"                     sectorBufferNz(to_integer(readSectorPointer)) <= '0';   -- {MARK}\n"
             "                     if (sectorBufferNz(to_integer(readSectorPointer)) = '0') then\n", 'copy start')
    t = once(t, "                   if (sectorBufferSizes(to_integer(writeSectorPointer)) /= 0) then -- additional irq for missed sector",
             "                   if (sectorBufferNz(to_integer(writeSectorPointer)) = '1') then -- additional irq for missed sector", 'missed sector')
    t = once(t, "               sectorBufferSizes <= (others => 0);\n",
             f"               sectorBufferNz <= (others => '0');   -- {MARK}\n", 'clear')
    left = [l for l in t.splitlines() if 'sectorBufferSizes' in l and MARK not in l and 'type tsectorBufferSizes' not in l and 'gSSsectorBufferSizes' not in l]
    want = ['                  sectorBufferSizes(to_integer(writeSectorPointer)) <= procSize;',
            '                        copySize <= sectorBufferSizes(to_integer(readSectorPointer));']
    if left != want: raise ValueError(f'{MARK}: other uses of sectorBufferSizes left: {left}')
    return t


def checked_text(t):
    """Simulation copy of sizes_text(t): the original array kept as a shadow
    (sectorBufferRef, assigned where the original assigned it) and checked
    every clock: each flag is (size /= 0), and a flagged entry holds the size."""
    t = sizes_text(t)
    adds = (
        (f"   signal sectorBufferNz            : std_logic_vector(0 to 7) := (others => '0');   -- {MARK}: size /= 0\n",
         "   signal sectorBufferRef           : tsectorBufferSizes := (others => 0);   -- check: the original array\n"),
        (f"else sectorBufferNz(to_integer(writeSectorPointer)) <= '0'; end if;   -- {MARK}\n",
         "                  sectorBufferRef(to_integer(writeSectorPointer)) <= procSize;   -- check\n"),
        (f"                     sectorBufferNz(to_integer(readSectorPointer)) <= '0';   -- {MARK}\n",
         "                     sectorBufferRef(to_integer(readSectorPointer)) <= 0;   -- check\n"),
        (f"               sectorBufferNz <= (others => '0');   -- {MARK}\n",
         "               sectorBufferRef <= (others => 0);   -- check\n"),
        (f"else sectorBufferNz(i) <= '0'; end if;   -- {MARK}\n",
         "               sectorBufferRef(i) <= to_integer(unsigned(ss_in(i + 96)(11 downto 2)));   -- check\n"),
    )
    for anchor, line in adds:
        t = once(t, anchor, anchor + line, 'check ' + line.strip()[:30])
    end = t.rindex('end architecture;')
    check = """   process (clk1x)   -- check: cd-buffer-sizes invariant
   begin
      if (rising_edge(clk1x)) then
         for i in 0 to 7 loop
            assert ((sectorBufferNz(i) = '1') = (sectorBufferRef(i) /= 0)) and (sectorBufferNz(i) = '0' or sectorBufferSizes(i) = sectorBufferRef(i))
               report "CDSIZES invariant broken at buffer " & integer'image(i) severity failure;
         end loop;
      end if;
   end process;

"""
    return t[:end] + check + t[end:]


def prepare_project(project):
    project = Path(project).resolve(); qsf = project/'apf_top.qsf'; q = qsf.read_text()
    hits = sorted(set(re.findall(r'"([^"]*/cd_top\.vhd)"', q)))
    if len(hits) != 1: raise ValueError(f'{MARK}: expected one cd_top.vhd in the project, found {hits}')
    out = project/MARK/'cd_top.vhd'; out.parent.mkdir(exist_ok=True)
    out.write_text(sizes_text(Path(hits[0]).read_text()))
    qsf.write_text(q.replace(f'"{hits[0]}"', f'"{out}"'))
    return out


if __name__ == '__main__':
    if len(sys.argv) != 2: raise SystemExit(__doc__.split('Usage: ')[1])
    print(prepare_project(sys.argv[1]))
