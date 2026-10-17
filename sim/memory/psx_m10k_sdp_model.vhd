-- SPDX-License-Identifier: MIT
-- Copyright (c) 2026 PSX Pocket contributors
--
-- Simulation model of rtl/memory/psx_m10k_sdp.vhd (same entity and ports).
--
-- * Write: on a rising wrclock edge with wren = '1', data is stored at
--   wraddress (values sampled before the edge, as the M10K input registers).
-- * Read: on a rising rdclock edge the read address is registered and q is
--   updated to the stored word; q holds until the next rdclock edge. There
--   is no asynchronous path from rdaddress or from the array to q.
-- * Mixed-port read-during-write: when a write and a read happen on the same
--   simulation edge (same clock, or two coincident clocks) to the same
--   address, q becomes MIXED_RDW: "DONT_CARE" -> all 'X' (what altsyncram
--   promises), "OLD_DATA" -> the stored word before the write, "NEW_DATA" ->
--   the written word (not offered by Cyclone V M10K mixed ports; for negative
--   controls only). An 'X' read address also gives 'X'.
-- * Power-up contents and q are zero (power_up_uninitialized = "FALSE").

library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity psx_m10k_sdp is
   generic
   (
      WIDTH      : natural;
      WIDTHAD    : natural;
      DUAL_CLOCK : boolean := false;
      MIXED_RDW  : string  := "DONT_CARE"
   );
   port
   (
      wrclock    : in  std_logic;
      rdclock    : in  std_logic;
      wren       : in  std_logic;
      wraddress  : in  std_logic_vector(WIDTHAD - 1 downto 0);
      data       : in  std_logic_vector(WIDTH - 1 downto 0);
      rdaddress  : in  std_logic_vector(WIDTHAD - 1 downto 0);
      q          : out std_logic_vector(WIDTH - 1 downto 0) := (others => '0')
   );
end entity;

architecture model of psx_m10k_sdp is
begin

   assert MIXED_RDW = "DONT_CARE" or MIXED_RDW = "OLD_DATA" or MIXED_RDW = "NEW_DATA"
      report "psx_m10k_sdp model: unknown MIXED_RDW" severity failure;

   process (wrclock, rdclock)
      type memory_t is array(0 to 2**WIDTHAD - 1) of std_logic_vector(WIDTH - 1 downto 0);
      variable memory  : memory_t := (others => (others => '0'));
      variable wr_edge : boolean;
      variable rd_edge : boolean;
      variable collide : boolean;
   begin
      wr_edge := rising_edge(wrclock);
      if DUAL_CLOCK then
         rd_edge := rising_edge(rdclock);
      else
         rd_edge := wr_edge;
      end if;

      if rd_edge then
         if is_x(rdaddress) then
            q <= (others => 'X');
         else
            collide := wr_edge and wren = '1' and wraddress = rdaddress;
            if collide and MIXED_RDW = "DONT_CARE" then
               q <= (others => 'X');
            elsif collide and MIXED_RDW = "NEW_DATA" then
               q <= data;
            else
               q <= memory(to_integer(unsigned(rdaddress)));
            end if;
         end if;
      end if;

      if wr_edge then
         if wren = '1' then
            assert not is_x(wraddress) report "psx_m10k_sdp model: write to X address" severity failure;
            memory(to_integer(unsigned(wraddress))) := data;
         else
            assert wren = '0' report "psx_m10k_sdp model: X write enable" severity failure;
         end if;
      end if;
   end process;

end architecture;
