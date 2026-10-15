-- SPDX-License-Identifier: MIT
-- Simulation-only stand-in for altera_mf dcfifo (lpm_showahead OFF): q is
-- valid one read clock after rdreq. Each side sees the other's pointer
-- through a delay of PIPE clocks of its own, like rdsync/wrsync_delaypipe, so
-- rdempty clears a few read clocks after a write.
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

package altera_mf_components is
   component dcfifo
      generic (
         intended_device_family : string := "Cyclone V"; lpm_numwords : natural := 256;
         lpm_showahead : string := "OFF"; lpm_type : string := "dcfifo"; lpm_width : natural := 8;
         lpm_widthu : natural := 8; overflow_checking : string := "ON"; rdsync_delaypipe : natural := 4;
         underflow_checking : string := "ON"; use_eab : string := "ON"; wrsync_delaypipe : natural := 4);
      port (
         data : in std_logic_vector(lpm_width-1 downto 0); rdclk, rdreq, wrclk, wrreq : in std_logic;
         q : out std_logic_vector(lpm_width-1 downto 0); rdempty, wrfull : out std_logic);
   end component;
end package;

library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity dcfifo is
   generic (
      intended_device_family : string := "Cyclone V"; lpm_numwords : natural := 256;
      lpm_showahead : string := "OFF"; lpm_type : string := "dcfifo"; lpm_width : natural := 8;
      lpm_widthu : natural := 8; overflow_checking : string := "ON"; rdsync_delaypipe : natural := 4;
      underflow_checking : string := "ON"; use_eab : string := "ON"; wrsync_delaypipe : natural := 4);
   port (
      data : in std_logic_vector(lpm_width-1 downto 0); rdclk, rdreq, wrclk, wrreq : in std_logic;
      q : out std_logic_vector(lpm_width-1 downto 0) := (others => '0'); rdempty, wrfull : out std_logic);
end entity;

architecture sim of dcfifo is
   constant PIPE : natural := 4;
   type tMem is array (0 to lpm_numwords-1) of std_logic_vector(lpm_width-1 downto 0);
   type tPipe is array (0 to PIPE-1) of natural;
   signal mem : tMem;
   signal wr_count, rd_count : natural := 0;             -- totals, never wrap in a test
   signal wr_seen : tPipe := (others => 0);              -- wr_count seen by the read side
   signal rd_seen : tPipe := (others => 0);              -- rd_count seen by the write side
begin
   rdempty <= '1' when wr_seen(PIPE-1) = rd_count else '0';
   wrfull  <= '1' when wr_count - rd_seen(PIPE-1) >= lpm_numwords else '0';

   process (wrclk) begin
      if rising_edge(wrclk) then
         rd_seen <= rd_count & rd_seen(0 to PIPE-2);
         if (wrreq = '1') then
            assert wr_count - rd_seen(PIPE-1) < lpm_numwords report "dcfifo model: write while full" severity failure;
            mem(wr_count mod lpm_numwords) <= data;
            wr_count <= wr_count + 1;
         end if;
      end if;
   end process;

   process (rdclk) begin
      if rising_edge(rdclk) then
         wr_seen <= wr_count & wr_seen(0 to PIPE-2);
         if (rdreq = '1') then
            assert wr_seen(PIPE-1) /= rd_count report "dcfifo model: read while empty" severity failure;
            q <= mem(rd_count mod lpm_numwords);
            rd_count <= rd_count + 1;
         end if;
      end if;
   end process;
end architecture;
