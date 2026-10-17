#!/usr/bin/env python3
"""SPU CD-input queue: each sample tick mixes the CD sample of that tick.

spu.vhd reads the live cd_left/cd_right ports when it mixes (cdProcStep, at
the end of the voice loop) and when it writes the capture buffers (CAPTURE0/1).
cd_top replaces those ports a few clocks after every spu_tick. On time, the SPU
reads them long after that update and before the next one, so it uses the
sample of the tick it is processing. When the SPU runs late (stashedSamples > 0:
SDRAM latency, a cache miss, a bus_write stall), the voice loop of tick N can
end after tick N+1 has started. It then mixes tick N+1's CD sample, and the
catch-up tick mixes it again: one CD/XA sample is dropped and one repeated,
while voices and the output sample count are unaffected. That is the FMV
(XA) crackle that can happen without any sound_timeout.

This patch latches each tick's CD pair into a 16-entry MLAB queue at
sampleticks = 16 (cd_top's 7-step update has finished) and pops one entry
when a tick's processing ends (CAPTURE3). Mixing and capture read the queue
head, which is always the entry of the tick being processed (at most 15
stashed ticks plus the one in flight). A tick dropped by sound_timeout drops
its entry. With an empty queue (never during processing) the live ports are
used. On-time ticks read exactly the value the original reads, so outputs are
cycle-identical whenever the SPU is never late. DuckStation behaves the same
way: SPU::Execute pops one CD audio frame per generated sample.
"""
import argparse
from pathlib import Path
import re


def _replace(text, old, new, count=1):
    if text.count(old) != count:
        raise ValueError('spu-cd-queue: SPU source changed near: ' + old.strip().splitlines()[0][:80])
    return text.replace(old, new)


PUSH_TICK = 16


def cd_queue_text(text):
    text = _replace(text,
        "   signal cd_next_right          : signed(16 downto 0);\n",
        "   signal cd_next_right          : signed(16 downto 0);\n"
        "   -- spu-cd-queue: CD pair of each pending tick, oldest first\n"
        "   signal cdq_wren               : std_logic := '0';\n"
        "   signal cdq_wp                 : unsigned(3 downto 0) := (others => '0');\n"
        "   signal cdq_rp                 : unsigned(3 downto 0) := (others => '0');\n"
        "   signal cdq_count              : integer range 0 to 16 := 0;\n"
        "   signal cdq_skip               : std_logic := '0';\n"
        "   signal cdq_head               : std_logic_vector(31 downto 0);\n"
        "   signal cd_use_left            : signed(15 downto 0);\n"
        "   signal cd_use_right           : signed(15 downto 0);\n")
    # Queue RAM at the top of the architecture body, with the MLAB entity
    # from the library the SPU's own RamMLAB instances use.
    libraries = set(re.findall(r'entity (\w+)\.RamMLAB\b', text))
    if len(libraries) != 1: raise ValueError('spu-cd-queue: RamMLAB library not unique')
    library = libraries.pop()
    body = re.search(r'^architecture arch of \w+ is[ \t\r]*\n.*?^begin[ \t\r]*\n', text, re.M | re.S)
    if not body: raise ValueError('spu-cd-queue: architecture body not found')
    text = (text[:body.end()] +
        "\n   -- spu-cd-queue\n"
        "   icdq : entity " + library + ".RamMLAB\n"
        "   GENERIC MAP (width => 32, widthad => 4, width_byteena => 1)\n"
        "   PORT MAP (\n"
        "      inclock    => clk1x,\n"
        "      wren       => cdq_wren,\n"
        "      data       => std_logic_vector(cd_left) & std_logic_vector(cd_right),\n"
        "      wraddress  => std_logic_vector(cdq_wp),\n"
        "      rdaddress  => std_logic_vector(cdq_rp),\n"
        "      q          => cdq_head\n"
        "   );\n"
        "   cdq_wren     <= '1' when (ce = '1' and reset = '0' and sampleticks = " + str(PUSH_TICK) + " and cdq_skip = '0') else '0';\n"
        "   cd_use_left  <= signed(cdq_head(31 downto 16)) when (cdq_count > 0) else cd_left;\n"
        "   cd_use_right <= signed(cdq_head(15 downto  0)) when (cdq_count > 0) else cd_right;\n"
        + text[body.end():])
    text = _replace(text,
        "            stashedSamples       <= 0;\n",
        "            stashedSamples       <= 0;\n"
        "            cdq_wp               <= (others => '0');\n"
        "            cdq_rp               <= (others => '0');\n"
        "            cdq_count            <= 0;\n"
        "            cdq_skip             <= '0';\n")
    text = _replace(text,
        "               else\n                  sound_timeout <= '1';\n               end if;\n            end if;\n",
        "               else\n                  sound_timeout <= '1';\n"
        "                  cdq_skip      <= '1'; -- spu-cd-queue: this tick is dropped, so is its CD pair\n"
        "               end if;\n            end if;\n"
        "\n"
        "            -- spu-cd-queue bookkeeping (push at sampleticks = " + str(PUSH_TICK) + ", pop when a tick ends)\n"
        "            if (sampleticks = " + str(PUSH_TICK) + ") then\n"
        "               cdq_skip <= '0';\n"
        "            end if;\n"
        "            if (cdq_wren = '1') then\n"
        "               cdq_wp <= cdq_wp + 1;\n"
        "               if (state = CAPTURE3 and cdq_count > 0) then\n"
        "                  cdq_rp <= cdq_rp + 1;\n"
        "               elsif (cdq_count = 16) then\n"
        "                  cdq_rp <= cdq_rp + 1; -- cannot happen (15 stashed + 1 active); keep the newest\n"
        "               else\n"
        "                  cdq_count <= cdq_count + 1;\n"
        "               end if;\n"
        "            elsif (state = CAPTURE3 and cdq_count > 0) then\n"
        "               cdq_rp    <= cdq_rp + 1;\n"
        "               cdq_count <= cdq_count - 1;\n"
        "            end if;\n")
    text = _replace(text, "ram_dataWrite <= std_logic_vector(cd_left);", "ram_dataWrite <= std_logic_vector(cd_use_left);")
    text = _replace(text, "ram_dataWrite <= std_logic_vector(cd_right);", "ram_dataWrite <= std_logic_vector(cd_use_right);")
    text = _replace(text, "soundmul1 <= resize(cd_left, 24);", "soundmul1 <= resize(cd_use_left, 24);")
    text = _replace(text, "soundmul1 <= resize(cd_right, 24);", "soundmul1 <= resize(cd_use_right, 24);")
    return text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    args.output.write_text(cd_queue_text(args.source.read_text()))


if __name__ == '__main__':
    main()
