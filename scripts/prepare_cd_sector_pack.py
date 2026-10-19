#!/usr/bin/env python3
"""cd_top's eight sector buffers packed at 640 words each (20 M10K, not 32).

iramSectorBuffers is an 8192 x 32 dual-port RAM addressed as
sector(2:0) & offset(9:0), but a sector holds at most 588 words (procCount
and copyReadAddr are integer range 0 to 588), so 3,488 of its 8,192 words
are never used and it costs 32 M10K. Here the same data sits at
sector * 640 + offset ((s << 9) + (s << 7) + offset, a 13-bit add on each
port) in a 5,120-word RAM: 20 M10K. Every used (sector, offset) pair maps to
a distinct word, write port A and read port B keep their registered timing
(one clk1x read latency, as mem.dpram), so cd_top behaves identically.
Savestate access to the buffers is dropped: reset-only builds only.
"""
import argparse
from pathlib import Path


def _replace(text, old, new, count=1):
    if text.count(old) != count:
        raise ValueError('cd-sector-pack: cd_top source changed near: ' + old.strip().splitlines()[0][:80])
    return text.replace(old, new)


def sector_pack_text(text):
    text = _replace(text, '''   sectorBuffers_addrB <= std_logic_vector(to_unsigned(to_integer(SS_Adr - 2048), 13)) when (SS_rden = '1' and SS_Adr >= 2048 and SS_Adr < 2048 + (1024 * 8)) else 
                          std_logic_vector(copySectorPointer & to_unsigned(copyReadAddr, 10));
   
   iramSectorBuffers: entity mem.dpram
   generic map 
   ( 
      addr_width => 13, 
      data_width => 32
   )
   port map
   (
      clock_a     => clk1x,
      address_a   => sectorBuffers_addrA,
      data_a      => sectorBuffers_DataA,
      wren_a      => sectorBuffers_wrenA,
      
      clock_b     => clk1x,
      address_b   => sectorBuffers_addrB,
      data_b      => x"00000000",
      wren_b      => '0',
      q_b         => sectorBuffers_DataB
   );
''', '''   -- cd-sector-pack: sector * 640 + offset (offsets stay below 589)
   sectorBuffers_addrB <= std_logic_vector(resize(copySectorPointer & "000000000", 13) + resize(copySectorPointer & "0000000", 13)
                                           + to_unsigned(copyReadAddr, 13));

   sectorPackRam : block
      -- Bits 29:0 in 2,048 x 5 slices (six wide, three deep: 18 M10K) and
      -- bits 31:30 in 8,192 x 1 slices (2 M10K): 20 M10K with a 3:1 read
      -- mux. (One 5,120 x 32 RAM in 512 x 20 slices is also 20 M10K, but
      -- ten deep: its 10:1 read mux cost about 100 ALMs.)
      type tPackedLo is array(0 to 5119) of std_logic_vector(29 downto 0);
      type tPackedHi is array(0 to 5119) of std_logic_vector(1 downto 0);
      signal packedLo : tPackedLo;
      signal packedHi : tPackedHi;
      attribute ramstyle : string;
      attribute ramstyle of packedLo : signal is "M10K, no_rw_check";
      attribute ramstyle of packedHi : signal is "M10K, no_rw_check";
      attribute max_depth : integer;
      attribute max_depth of packedLo : signal is 2048;
      attribute max_depth of packedHi : signal is 8192;
   begin
      process (clk1x)
      begin
         if rising_edge(clk1x) then
            if (sectorBuffers_wrenA = '1') then
               packedLo(to_integer(unsigned(sectorBuffers_addrA))) <= sectorBuffers_DataA(29 downto 0);
               packedHi(to_integer(unsigned(sectorBuffers_addrA))) <= sectorBuffers_DataA(31 downto 30);
            end if;
            sectorBuffers_DataB <= packedHi(to_integer(unsigned(sectorBuffers_addrB))) & packedLo(to_integer(unsigned(sectorBuffers_addrB)));
         end if;
      end process;
   end block;
''')
    text = _replace(text, '''                  sectorBuffers_addrA <= std_logic_vector(writeSectorPointer & to_unsigned(procCount, 10));''',
                    '''                  sectorBuffers_addrA <= std_logic_vector(resize(writeSectorPointer & "000000000", 13) + resize(writeSectorPointer & "0000000", 13)
                                                          + to_unsigned(procCount, 13));''')
    text = _replace(text, '''            if (SS_Adr >= 2048 and SS_Adr < 2048 + (1024 * 8)) then 
               sectorBuffers_addrA <= std_logic_vector(to_unsigned(to_integer(SS_Adr - 2048), 13));''',
                    '''            if (false) then   -- cd-sector-pack: no savestate access to the sector buffers
               sectorBuffers_addrA <= (others => '0');''')
    return text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    args.output.write_text(sector_pack_text(args.source.read_text()))


if __name__ == '__main__':
    main()
