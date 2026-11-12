-- SPDX-License-Identifier: MIT
-- Simulation models for the whole-GPU lockstep (scripts/run_gpu_req_hoist.py).
-- The same models are compiled for the reference and the candidate GPU, so
-- they only have to behave like a plausible RAM / CDC, not like the vendor
-- primitive bit for bit. Nothing here is synthesised.

-- psx_hold_cell.sv: an identity LUT.
library ieee;
use ieee.std_logic_1164.all;
entity psx_hold_cell is
   port (i : in std_logic; o : out std_logic);
end entity;
architecture model of psx_hold_cell is
begin
   o <= i;
end architecture;

-- pocket_dpram_sdp.vhd (altsyncram, write on A, registered-address read on B,
-- new data on a same-address read during write).
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
entity pocket_dpram_sdp is
   generic (addr_width : integer := 8; data_width : integer := 8);
   port (
      clock_a   : in  std_logic;
      clken_a   : in  std_logic := '1';
      address_a : in  std_logic_vector(addr_width - 1 downto 0);
      data_a    : in  std_logic_vector(data_width - 1 downto 0);
      wren_a    : in  std_logic := '0';
      q_a       : out std_logic_vector(data_width - 1 downto 0);
      clock_b   : in  std_logic;
      clken_b   : in  std_logic := '1';
      address_b : in  std_logic_vector(addr_width - 1 downto 0);
      data_b    : in  std_logic_vector(data_width - 1 downto 0) := (others => '0');
      wren_b    : in  std_logic := '0';
      q_b       : out std_logic_vector(data_width - 1 downto 0)
   );
end entity;
architecture model of pocket_dpram_sdp is
   type memory_t is array (0 to 2 ** addr_width - 1) of std_logic_vector(data_width - 1 downto 0);
   signal ram : memory_t := (others => (others => '0'));
begin
   q_a <= (others => '0');
   process (clock_a)
   begin
      if rising_edge(clock_a) then
         if (clken_a = '1' and wren_a = '1') then
            ram(to_integer(unsigned(address_a))) <= data_a;
         end if;
      end if;
   end process;
   process (clock_b)
   begin
      if rising_edge(clock_b) then
         if (clken_b = '1') then
            if (clken_a = '1' and wren_a = '1' and address_a = address_b) then
               q_b <= data_a;
            else
               q_b <= ram(to_integer(unsigned(address_b)));
            end if;
         end if;
      end if;
   end process;
end architecture;

-- psx_video_config_cdc.sv: ordered snapshots from clk1x to clkvid through a
-- dual-clock FIFO. Model: the same reset pipes and online handshakes, and a
-- FIFO whose pointers cross through two flip-flops each way.
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
entity psx_video_config_cdc is
   port (
      src_clk, dst_clk, reset_async, src_valid : in std_logic;
      src_data  : in  std_logic_vector(83 downto 0);
      src_ready : out std_logic;
      dst_data  : out std_logic_vector(83 downto 0);
      dst_update, dst_initialized, src_fault : out std_logic
   );
end entity;
architecture model of psx_video_config_cdc is
   signal src_reset_pipe, dst_reset_pipe : std_logic_vector(2 downto 0) := "111";
   signal src_reset, dst_reset : std_logic;
   signal source_online_meta, source_online_sync : std_logic := '0';
   signal destination_online_meta, destination_online_sync : std_logic := '0';
   signal fault, update, initialized : std_logic := '0';
   type memory_t is array (0 to 15) of std_logic_vector(83 downto 0);
   signal ram : memory_t := (others => (others => '0'));
   signal wr_ptr, rd_ptr : unsigned(4 downto 0) := (others => '0');
   signal wr_meta, wr_sync, rd_meta, rd_sync : unsigned(4 downto 0) := (others => '0');
   signal full, empty, ready, push, pop, wr_rst, rd_rst : std_logic;
begin
   src_reset <= src_reset_pipe(2);
   dst_reset <= dst_reset_pipe(2);
   process (src_clk, reset_async)
   begin
      if (reset_async = '1') then src_reset_pipe <= "111";
      elsif rising_edge(src_clk) then src_reset_pipe <= src_reset_pipe(1 downto 0) & '0';
      end if;
   end process;
   process (dst_clk, reset_async)
   begin
      if (reset_async = '1') then dst_reset_pipe <= "111";
      elsif rising_edge(dst_clk) then dst_reset_pipe <= dst_reset_pipe(1 downto 0) & '0';
      end if;
   end process;
   full   <= '1' when (wr_ptr(3 downto 0) = rd_sync(3 downto 0) and wr_ptr(4) /= rd_sync(4)) else '0';
   empty  <= '1' when (rd_ptr = wr_sync) else '0';
   ready  <= (not src_reset) and destination_online_sync and (not full) and (not fault);
   push   <= src_valid and ready;
   pop    <= (not dst_reset) and source_online_sync and (not empty);
   wr_rst <= src_reset or not destination_online_sync;
   rd_rst <= dst_reset or not source_online_sync;
   src_ready <= ready;
   src_fault <= fault;
   dst_update <= update;
   dst_initialized <= initialized;
   dst_data <= ram(to_integer(rd_ptr(3 downto 0)));
   process (src_clk, src_reset)
   begin
      if (src_reset = '1') then
         destination_online_meta <= '0'; destination_online_sync <= '0'; fault <= '0';
      elsif rising_edge(src_clk) then
         destination_online_meta <= not dst_reset; destination_online_sync <= destination_online_meta;
         if (src_valid = '1' and destination_online_sync = '1' and full = '1') then fault <= '1'; end if;
      end if;
   end process;
   process (dst_clk, dst_reset)
   begin
      if (dst_reset = '1') then
         source_online_meta <= '0'; source_online_sync <= '0'; update <= '0'; initialized <= '0';
      elsif rising_edge(dst_clk) then
         source_online_meta <= not src_reset; source_online_sync <= source_online_meta;
         update <= pop;
         if (pop = '1') then initialized <= '1'; end if;
      end if;
   end process;
   process (src_clk)
   begin
      if rising_edge(src_clk) then
         rd_meta <= rd_ptr; rd_sync <= rd_meta;
         if (wr_rst = '1') then
            wr_ptr <= (others => '0');
         elsif (push = '1') then
            ram(to_integer(wr_ptr(3 downto 0))) <= src_data;
            wr_ptr <= wr_ptr + 1;
         end if;
      end if;
   end process;
   process (dst_clk)
   begin
      if rising_edge(dst_clk) then
         wr_meta <= wr_ptr; wr_sync <= wr_meta;
         if (rd_rst = '1') then
            rd_ptr <= (others => '0');
         elsif (pop = '1') then
            rd_ptr <= rd_ptr + 1;
         end if;
      end if;
   end process;
end architecture;
