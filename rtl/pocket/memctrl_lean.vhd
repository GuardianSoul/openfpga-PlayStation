-- SPDX-License-Identifier: GPL-2.0-only
-- Drop-in for PSX_MiSTer memctrl (same entity name and ports) for reset-only
-- Pocket builds: savestates are disabled, so SS_wren/SS_rden never assert.
--
-- Only the low 14 delay bits of six registers, MC_SPU_DELAY 29 and 27:24 and
-- MC_COMMON_DELAY 15:0 drive logic; everything else is only read back over
-- the bus. Those readback words live in a 16 x 32 block RAM (one entry per
-- register, as written; RAMSIZE for bus2 in its own RAM) read on the falling
-- clk1x edge, with valid bits that return the reset values until the first
-- write. Bits a bus write keeps (30, 28, 23:21 of the delay registers,
-- 31:18 of COMMON) can only come from reset, so they are constants here.
-- Contract (as for spu-static-readback): bus_addr and bus2_addr change only
-- on rising clk1x edges. Read data, write effects and outputs match memctrl
-- cycle for cycle.
library IEEE;
use IEEE.std_logic_1164.all;
use IEEE.numeric_std.all;

entity memctrl is
   port
   (
      clk1x                : in  std_logic;
      ce                   : in  std_logic;
      reset                : in  std_logic;

      bus_addr             : in  unsigned(5 downto 0);
      bus_dataWrite        : in  std_logic_vector(31 downto 0);
      bus_read             : in  std_logic;
      bus_write            : in  std_logic;
      bus_dataRead         : out std_logic_vector(31 downto 0);

      bus2_addr            : in  unsigned(3 downto 0);
      bus2_dataWrite       : in  std_logic_vector(31 downto 0);
      bus2_read            : in  std_logic;
      bus2_write           : in  std_logic;
      bus2_dataRead        : out std_logic_vector(31 downto 0);

      errorBuswidth        : out std_logic;

      spu_memctrl          : out unsigned(13 downto 0);
      cd_memctrl           : out unsigned(13 downto 0);
      bios_memctrl         : out unsigned(13 downto 0);
      ex1_memctrl          : out unsigned(13 downto 0);
      ex2_memctrl          : out unsigned(13 downto 0);
      ex3_memctrl          : out unsigned(13 downto 0);

      com0_delay           : out unsigned(3 downto 0);
      com1_delay           : out unsigned(3 downto 0);
      com2_delay           : out unsigned(3 downto 0);
      com3_delay           : out unsigned(3 downto 0);

      dma_spu_timing_on    : out std_logic;
      dma_spu_timing_value : out unsigned(3 downto 0);

      loading_savestate    : in  std_logic;
      SS_reset             : in  std_logic;
      SS_DataWrite         : in  std_logic_vector(31 downto 0);
      SS_Adr               : in  unsigned(4 downto 0);
      SS_wren              : in  std_logic;
      SS_rden              : in  std_logic;
      SS_DataRead          : out std_logic_vector(31 downto 0)
   );
end entity;

architecture arch of memctrl is

   type tReset is array(0 to 15) of std_logic_vector(31 downto 0);
   -- reset values in bus order: EXP1_BASE, EXP2_BASE (as read: 0x1F & base),
   -- EXP1, EXP3, BIOS, SPU, CDROM, EXP2, COMMON delays
   constant RESETS : tReset := (
      0 => x"1F000000", 1 => x"1F802000", 2 => x"0013243F", 3 => x"00003022", 4 => x"0013243F",
      5 => x"200931E1", 6 => x"00020843", 7 => x"00070777", 8 => x"00031125", others => x"00000000");
   constant RAMSIZE_RESET : std_logic_vector(31 downto 0) := x"00000B88";

   type tTable is array(0 to 15) of std_logic_vector(31 downto 0);
   signal readback      : tTable;
   attribute ramstyle : string;
   attribute ramstyle of readback : signal is "M10K, no_rw_check";
   signal readback_q    : std_logic_vector(31 downto 0);
   signal valid         : std_logic_vector(15 downto 0) := (others => '0');

   signal ramsize_ram   : tTable;
   attribute ramstyle of ramsize_ram : signal is "M10K, no_rw_check";
   signal ramsize_q     : std_logic_vector(31 downto 0);
   signal ramsize_valid : std_logic := '0';

   -- live bits
   type tDelay is array(0 to 5) of std_logic_vector(13 downto 0);   -- EXP1, EXP3, BIOS, SPU, CDROM, EXP2
   signal delay      : tDelay;
   signal spu_hi     : std_logic_vector(4 downto 0);                -- MC_SPU_DELAY 29, 27:24
   signal common_lo  : std_logic_vector(15 downto 0);

   signal word_index : integer range 0 to 15;
   signal write_word : std_logic_vector(31 downto 0);
   signal entry      : std_logic_vector(31 downto 0);

begin

   errorBuswidth <= not delay(3)(12);

   ex1_memctrl  <= unsigned(delay(0));
   ex3_memctrl  <= unsigned(delay(1));
   bios_memctrl <= unsigned(delay(2));
   spu_memctrl  <= unsigned(delay(3));
   cd_memctrl   <= unsigned(delay(4));
   ex2_memctrl  <= unsigned(delay(5));

   com0_delay   <= unsigned(common_lo( 3 downto  0));
   com1_delay   <= unsigned(common_lo( 7 downto  4));
   com2_delay   <= unsigned(common_lo(11 downto  8));
   com3_delay   <= unsigned(common_lo(15 downto 12));

   dma_spu_timing_on    <= spu_hi(4);
   dma_spu_timing_value <= unsigned(spu_hi(3 downto 0));

   SS_DataRead <= (others => '0');

   word_index <= to_integer(bus_addr(5 downto 2));

   -- the word a bus write leaves in the register (memctrl's merge with the
   -- kept bits, which only ever hold their reset value)
   process (bus_addr, bus_dataWrite, word_index)
      variable r : std_logic_vector(31 downto 0);
   begin
      r := RESETS(word_index);
      case word_index is
         when 0 | 1 => write_word <= x"1F" & bus_dataWrite(23 downto 0);
         when 2 to 7 => write_word <= bus_dataWrite(31) & r(30) & bus_dataWrite(29) & r(28) & bus_dataWrite(27 downto 24) & r(23 downto 21) & bus_dataWrite(20 downto 0);
         when 8 => write_word <= r(31 downto 18) & bus_dataWrite(17 downto 0);
         when others => write_word <= (others => '0');
      end case;
   end process;

   -- falling-edge block RAM reads (addresses change on rising edges only)
   process (clk1x)
   begin
      if falling_edge(clk1x) then
         readback_q <= readback(word_index);
         ramsize_q  <= ramsize_ram(to_integer(bus2_addr));
      end if;
   end process;

   entry <= readback_q when valid(word_index) = '1' else RESETS(word_index);

   process (clk1x)
   begin
      if rising_edge(clk1x) then

         if (reset = '1' and loading_savestate = '0') then

            valid         <= (others => '0');
            ramsize_valid <= '0';
            for i in 0 to 5 loop
               delay(i) <= RESETS(i + 2)(13 downto 0);
            end loop;
            spu_hi    <= RESETS(5)(29) & RESETS(5)(27 downto 24);
            common_lo <= RESETS(8)(15 downto 0);

         elsif (ce = '1') then

            bus_dataRead  <= (others => '0');
            bus2_dataRead <= (others => '0');

            if (bus_read = '1') then
               if (word_index <= 8) then bus_dataRead <= entry; end if;
            end if;

            if (bus_write = '1' and bus_addr(1 downto 0) = "00" and word_index <= 8) then
               readback(word_index) <= write_word;
               valid(word_index)    <= '1';
               if (word_index >= 2 and word_index <= 7) then
                  delay(word_index - 2) <= bus_dataWrite(13 downto 0);
               end if;
               if (word_index = 5) then
                  spu_hi <= bus_dataWrite(29) & bus_dataWrite(27 downto 24);
               end if;
               if (word_index = 8) then
                  common_lo <= bus_dataWrite(15 downto 0);
               end if;
            end if;

            if (bus2_read = '1') then
               if (bus2_addr = 0) then
                  if (ramsize_valid = '1') then bus2_dataRead <= ramsize_q; else bus2_dataRead <= RAMSIZE_RESET; end if;
               else
                  bus2_dataRead <= (others => '1');
               end if;
            end if;

            if (bus2_write = '1' and bus2_addr = 0) then
               ramsize_ram(0) <= bus2_dataWrite;
               ramsize_valid  <= '1';
            end if;

         end if;
      end if;
   end process;

end architecture;
