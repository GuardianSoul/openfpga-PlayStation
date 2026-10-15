-- SPDX-License-Identifier: MIT
-- pocket_memcard_format: APF loads card 1 (or not) through the bridge, then
-- load_done rises. The PSRAM model starts with a nonzero pattern, as PSRAM
-- holds after power-on. scripts/run_memcard_format.py checks the dump.
--   LOAD_KIND 0: no file loaded                    -> header area formatted
--             1: all-zero file                     -> header area formatted
--             2: real card ("MC", data everywhere) -> untouched
--             3: erased file (all 0xFF)            -> header area formatted
--             4: 0.5.0 card: 0xFF, junk in frame 63 and block 1 -> formatted, block 1 kept
--             5: "MC" then zeros                   -> untouched
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
use std.textio.all;

entity tb_memcard_format is
   generic (LOAD_KIND : natural := 0; DUMP : string := "card.txt");
end entity;

architecture sim of tb_memcard_format is
   constant WORDS : natural := 32768;
   type tMem is array (0 to WORDS-1) of std_logic_vector(31 downto 0);

   function initial_word(i : natural) return std_logic_vector is
   begin return std_logic_vector(to_unsigned(16#DEAD0000# / 65536, 16)) & std_logic_vector(to_unsigned(i, 16)); end function;
   function load_word(i : natural) return std_logic_vector is
   begin
      case LOAD_KIND is
         when 2 => if i = 0 then return x"0000434D"; else return x"5A" & std_logic_vector(to_unsigned(i, 24)); end if;
         when 3 => return x"FFFFFFFF";
         when 4 => if i >= 2016 and i < 2049 then return x"275A0C80"; else return x"FFFFFFFF"; end if;
         when 5 => if i = 0 then return x"0000434D"; else return x"00000000"; end if;
         when others => return x"00000000";
      end case;
   end function;

   signal clk_74a, clk2x : std_logic := '0';
   signal done_sim : boolean := false;
   signal bridge_addr, bridge_wr_data, bridge_rd_data : std_logic_vector(31 downto 0) := (others => '0');
   signal bridge_wr, bridge_rd, overflow : std_logic := '0';
   signal reset_2x : std_logic := '1';
   signal busy, aux_req, aux_we, aux_done, load_done : std_logic := '0';
   signal aux_addr : std_logic_vector(20 downto 0);
   signal aux_din, aux_dout : std_logic_vector(31 downto 0) := (others => '0');
   signal busy_fell_early : boolean := false;
begin
   clk_74a <= not clk_74a after 6734 ps when not done_sim;
   clk2x   <= not clk2x   after 7383 ps when not done_sim;

   dut : entity work.pocket_memcard_format port map (
      clk_74a => clk_74a, bridge_addr => bridge_addr, bridge_wr => bridge_wr, bridge_wr_data => bridge_wr_data,
      bridge_rd => bridge_rd, bridge_rd_data => bridge_rd_data, overflow => overflow,
      clk2x => clk2x, reset_2x => reset_2x, busy => busy,
      aux_req => aux_req, aux_we => aux_we, aux_addr => aux_addr, aux_din => aux_din, aux_dout => aux_dout,
      aux_done => aux_done, card_write => '0', load_done => load_done);

   -- vram_psram aux port: request held until done; done one clock, five clocks after the request.
   psram : process (clk2x, done_sim)
      variable mem : tMem;
      variable init : boolean := false;
      variable count : natural := 0;
      variable cool : boolean := false;
      variable l : line;
      file f : text;
   begin
      if not init then
         for i in 0 to WORDS-1 loop mem(i) := initial_word(i); end loop;
         init := true;
      end if;
      if rising_edge(clk2x) then
         aux_done <= '0';
         if cool then
            cool := false;
         elsif aux_req = '1' then
            assert unsigned(aux_addr) >= 16#40000# and unsigned(aux_addr) < 16#40000# + WORDS
               report "aux access outside card region 1" severity failure;
            count := count + 1;
            if count = 5 then
               count := 0; cool := true; aux_done <= '1';
               assert aux_we = '0' or busy = '1' report "card write after busy fell" severity failure;
               if aux_we = '1' then mem(to_integer(unsigned(aux_addr)) - 16#40000#) := aux_din;
               else aux_dout <= mem(to_integer(unsigned(aux_addr)) - 16#40000#); end if;
            end if;
         end if;
      end if;
      if done_sim'event and done_sim then
         file_open(f, DUMP, write_mode);
         for i in 0 to WORDS-1 loop hwrite(l, mem(i)); writeline(f, l); end loop;
         file_close(f);
      end if;
   end process;

   stim : process
      variable l : line;
      file f : text;
   begin
      wait for 100 ns;
      wait until rising_edge(clk2x); reset_2x <= '0';
      -- APF slot load: one word every 12 bridge clocks, core held.
      if LOAD_KIND /= 0 then
         for i in 0 to WORDS-1 loop
            wait until rising_edge(clk_74a);
            bridge_addr <= std_logic_vector(to_unsigned(16#20000000# + 4 * i, 32));
            bridge_wr_data <= load_word(i); bridge_wr <= '1';
            wait until rising_edge(clk_74a); bridge_wr <= '0';
            for k in 1 to 10 loop wait until rising_edge(clk_74a); end loop;
         end loop;
      end if;
      -- allcomplete, BIOS loaded: the boot gate's ready.
      for k in 1 to 200 loop wait until rising_edge(clk_74a); end loop;
      assert busy = '1' report "busy low before load_done" severity failure;
      load_done <= '1';
      wait until busy = '0' for 2 ms;
      assert busy = '0' report "busy never fell" severity failure;
      assert overflow = '0' report "load FIFO overflow" severity failure;
      -- APF save on quit: sequential bridge reads of the first 64 words.
      file_open(f, DUMP & ".read", write_mode);
      for i in 0 to 64 loop
         wait until rising_edge(clk_74a);
         bridge_addr <= std_logic_vector(to_unsigned(16#20000000# + 4 * i, 32)); bridge_rd <= '1';
         wait until rising_edge(clk_74a); bridge_rd <= '0';
         for k in 1 to 60 loop wait until rising_edge(clk_74a); end loop;
         hwrite(l, bridge_rd_data); writeline(f, l);
      end loop;
      file_close(f);
      for k in 1 to 20 loop wait until rising_edge(clk2x); end loop;
      done_sim <= true;
      wait for 100 ns;
      report "tb_memcard_format done" severity note;
      wait;
   end process;
end architecture;
