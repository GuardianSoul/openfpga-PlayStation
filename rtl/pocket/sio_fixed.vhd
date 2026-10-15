-- SPDX-License-Identifier: GPL-2.0-only
-- Fit-limited Pocket builds: PSX_MiSTer sio with its registers fixed at their
-- reset values (STAT 5, MODE 0, CTRL 0, BAUD 0xDC). Reads keep MiSTer's
-- timing and address decode; writes are ignored. MiSTer's SIO has no serial
-- link or IRQ, and FFVII never touches it (docs/ffvii-hardware-census.md),
-- so only a game that writes then reads back an SIO register sees a change.
library IEEE;
use IEEE.std_logic_1164.all;
use IEEE.numeric_std.all;

entity sio is
   port
   (
      clk1x                : in  std_logic;
      ce                   : in  std_logic;
      reset                : in  std_logic;

      bus_addr             : in  unsigned(3 downto 0);
      bus_dataWrite        : in  std_logic_vector(31 downto 0);
      bus_read             : in  std_logic;
      bus_write            : in  std_logic;
      bus_writeMask        : in  std_logic_vector(3 downto 0);
      bus_dataRead         : out std_logic_vector(31 downto 0);

      loading_savestate    : in  std_logic;
      SS_reset             : in  std_logic;
      SS_DataWrite         : in  std_logic_vector(31 downto 0);
      SS_Adr               : in  unsigned(2 downto 0);
      SS_wren              : in  std_logic;
      SS_rden              : in  std_logic;
      SS_DataRead          : out std_logic_vector(31 downto 0)
   );
end entity;

architecture fixed of sio is
begin

   process (clk1x)
   begin
      if (rising_edge(clk1x)) then
         if (reset = '1') then
            null;
         elsif (ce = '1') then
            bus_dataRead <= (others => '0');
            if (bus_read = '1') then
               case (bus_addr(3 downto 1) & '0') is
                  when x"4" => bus_dataRead <= x"00000005";
                  when x"8" => bus_dataRead <= x"00000000";
                  when x"A" => bus_dataRead <= x"00000000";
                  when x"E" => bus_dataRead <= x"000000DC";
                  when others => bus_dataRead <= (others => '1');
               end case;
            end if;
         end if;
         if (SS_rden = '1') then
            SS_DataRead <= (others => '0');
         end if;
      end if;
   end process;

end architecture;
