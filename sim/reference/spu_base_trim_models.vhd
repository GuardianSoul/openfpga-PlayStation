-- SPDX-License-Identifier: GPL-2.0-only
-- Behavioural simulation models of the vendor RAM wrappers used by the
-- selected SPU (tb_spu_base_trim.vhd). Both lockstep instances use the same
-- models, so these need the wrappers' port timing, not exact vendor
-- collision behaviour:
--   pocket_dpram_sdp : altsyncram DUAL_PORT, A writes, B reads through a
--                      registered address (old data on a same-edge collision)
--   dpram            : BIDIR_DUAL_PORT, registered address, new data on the
--                      writing port
--   dpram_dif        : wide port A write, narrow port B read (DDR3 cache)
--   RamMLAB          : registered write, asynchronous read
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity pocket_dpram_sdp is
   generic (addr_width : integer := 8; data_width : integer := 8);
   port (
      clock_a   : in  std_logic;
      clken_a   : in  std_logic := '1';
      address_a : in  std_logic_vector(addr_width-1 downto 0);
      data_a    : in  std_logic_vector(data_width-1 downto 0);
      wren_a    : in  std_logic := '0';
      q_a       : out std_logic_vector(data_width-1 downto 0);
      clock_b   : in  std_logic;
      clken_b   : in  std_logic := '1';
      address_b : in  std_logic_vector(addr_width-1 downto 0);
      data_b    : in  std_logic_vector(data_width-1 downto 0) := (others => '0');
      wren_b    : in  std_logic := '0';
      q_b       : out std_logic_vector(data_width-1 downto 0)
   );
end pocket_dpram_sdp;

architecture sim of pocket_dpram_sdp is
   type memory_t is array(0 to 2**addr_width-1) of std_logic_vector(data_width-1 downto 0);
   signal ram : memory_t := (others => (others => '0'));
begin
   q_a <= (others => '0');
   process (clock_a) begin
      if rising_edge(clock_a) then
         if clken_a = '1' and wren_a = '1' then
            ram(to_integer(unsigned(address_a))) <= data_a;
         end if;
      end if;
   end process;
   process (clock_b) begin
      if rising_edge(clock_b) then
         if clken_b = '1' then
            q_b <= ram(to_integer(unsigned(address_b)));
         end if;
      end if;
   end process;
end sim;

library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity dpram is
   generic (addr_width : integer := 8; data_width : integer := 8);
   port (
      clock_a   : in  std_logic;
      clken_a   : in  std_logic := '1';
      address_a : in  std_logic_vector(addr_width-1 downto 0);
      data_a    : in  std_logic_vector(data_width-1 downto 0);
      wren_a    : in  std_logic := '0';
      q_a       : out std_logic_vector(data_width-1 downto 0);
      clock_b   : in  std_logic;
      clken_b   : in  std_logic := '1';
      address_b : in  std_logic_vector(addr_width-1 downto 0);
      data_b    : in  std_logic_vector(data_width-1 downto 0) := (others => '0');
      wren_b    : in  std_logic := '0';
      q_b       : out std_logic_vector(data_width-1 downto 0)
   );
end dpram;

architecture sim of dpram is
   type memory_t is array(0 to 2**addr_width-1) of std_logic_vector(data_width-1 downto 0);
begin
   -- one process for both ports (the SPU drives both from clk1x)
   process (clock_a, clock_b)
      variable ram : memory_t := (others => (others => '0'));
   begin
      if rising_edge(clock_a) and clken_a = '1' then
         if wren_a = '1' then ram(to_integer(unsigned(address_a))) := data_a; end if;
         q_a <= ram(to_integer(unsigned(address_a)));
      end if;
      if rising_edge(clock_b) and clken_b = '1' then
         if wren_b = '1' then ram(to_integer(unsigned(address_b))) := data_b; end if;
         q_b <= ram(to_integer(unsigned(address_b)));
      end if;
   end process;
end sim;

library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity dpram_dif is
   generic (addr_width_a : integer := 8; data_width_a : integer := 8;
            addr_width_b : integer := 8; data_width_b : integer := 8);
   port (
      clock_a   : in  std_logic;
      clken_a   : in  std_logic := '1';
      address_a : in  std_logic_vector(addr_width_a-1 downto 0);
      data_a    : in  std_logic_vector(data_width_a-1 downto 0);
      wren_a    : in  std_logic := '0';
      q_a       : out std_logic_vector(data_width_a-1 downto 0);
      clock_b   : in  std_logic;
      clken_b   : in  std_logic := '1';
      address_b : in  std_logic_vector(addr_width_b-1 downto 0);
      data_b    : in  std_logic_vector(data_width_b-1 downto 0) := (others => '0');
      wren_b    : in  std_logic := '0';
      q_b       : out std_logic_vector(data_width_b-1 downto 0)
   );
end dpram_dif;

architecture sim of dpram_dif is
   constant RATIO : integer := data_width_a / data_width_b;
   type memory_t is array(0 to 2**addr_width_b-1) of std_logic_vector(data_width_b-1 downto 0);
begin
   q_a <= (others => '0');
   process (clock_a, clock_b)
      variable ram : memory_t := (others => (others => '0'));
   begin
      if rising_edge(clock_a) and clken_a = '1' and wren_a = '1' then
         for k in 0 to RATIO-1 loop
            ram(to_integer(unsigned(address_a)) * RATIO + k) := data_a((k+1)*data_width_b-1 downto k*data_width_b);
         end loop;
      end if;
      if rising_edge(clock_b) and clken_b = '1' then
         q_b <= ram(to_integer(unsigned(address_b)));
      end if;
   end process;
end sim;

library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity RamMLAB is
   generic (width : natural; width_byteena : natural := 1; widthad : natural);
   port (
      inclock   : in  std_logic;
      wren      : in  std_logic;
      data      : in  std_logic_vector(width-1 downto 0);
      wraddress : in  std_logic_vector(widthad-1 downto 0);
      rdaddress : in  std_logic_vector(widthad-1 downto 0);
      q         : out std_logic_vector(width-1 downto 0)
   );
end RamMLAB;

architecture sim of RamMLAB is
   type memory_t is array(0 to 2**widthad-1) of std_logic_vector(width-1 downto 0);
   signal ram : memory_t := (others => (others => '0'));
begin
   process (inclock) begin
      if rising_edge(inclock) and wren = '1' then
         ram(to_integer(unsigned(wraddress))) <= data;
      end if;
   end process;
   q <= ram(to_integer(unsigned(rdaddress)));
end sim;
