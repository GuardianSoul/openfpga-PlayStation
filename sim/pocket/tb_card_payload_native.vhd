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
   generic (LOAD_KIND : natural := 0; DUMP : string := "card.txt"; READ_WORDS : positive := 65; AUX_DELAY : positive := 5; LOAD_GAP : natural := 10; FLIGHT_PS : natural := 13000);
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
   signal guest_write : std_logic := '0';
   signal guest_addr : natural range 0 to WORDS-1 := 7;
   signal guest_data : std_logic_vector(31 downto 0) := x"1234ABCD";
   type tcram is record
      a : std_logic_vector(21 downto 16); dq_out, dq_in : std_logic_vector(15 downto 0); dq_oe : std_logic;
      clk, adv_n, cre, ce0_n, ce1_n, oe_n, we_n, ub_n, lb_n : std_logic;
   end record;
   signal c0, c1 : tcram;   -- FPGA side
   signal m0, m1 : tcram;   -- PSRAM side (delayed)
   signal f0, f1 : std_logic_vector(15 downto 0);   -- FPGA data drivers
   signal dq0, dq1 : std_logic_vector(15 downto 0); -- PSRAM side data buses
   constant T_OUT : time := 4 ns;
   constant T_IN : time := 3 ns;
   constant ROW_WORDS : integer := 128;
   signal native_busy,native_rdy,native_rd,native_we,native_card_write : std_logic := '0';
   signal native_addr : std_logic_vector(24 downto 0) := (others=>'0');
   signal native_burst : std_logic_vector(7 downto 0) := x"01";
   signal native_dout,native_din : std_logic_vector(63 downto 0) := (others=>'0');
   signal native_be : std_logic_vector(7 downto 0) := (others=>'0');
   signal test_GPU_words,test_GPU_bursts,test_AUX_max : natural := 0;

begin
   clk_74a <= not clk_74a after 6734 ps when not done_sim;
   clk2x   <= not clk2x   after 7381275 fs when not done_sim;

   dut : entity work.pocket_memcard_format generic map (TEST_FLIGHT_PS => FLIGHT_PS) port map (
      clk_74a => clk_74a, bridge_addr => bridge_addr, bridge_wr => bridge_wr, bridge_wr_data => bridge_wr_data,
      bridge_rd => bridge_rd, bridge_rd_data => bridge_rd_data, overflow => overflow,
      clk2x => clk2x, reset_2x => reset_2x, busy => busy,
      aux_req => aux_req, aux_we => aux_we, aux_addr => aux_addr, aux_din => aux_din, aux_dout => aux_dout,
      aux_done => aux_done, card_write => native_card_write, load_done => load_done);

   idut : entity work.vram_psram
   port map (clk => clk2x, reset => reset_2x, ddr_busy => native_busy, ddr_burstcnt => native_burst, ddr_addr => native_addr, ddr_dout => native_dout,
             ddr_dout_ready => native_rdy, ddr_rd => native_rd, ddr_din => native_din, ddr_be => native_be, ddr_we => native_we,
             aux_req => aux_req, aux_we => aux_we, aux_addr => aux_addr, aux_din => aux_din, aux_dout => aux_dout,
             aux_done => aux_done, card_write => native_card_write,
             cram0_a => c0.a, cram0_dq_out => c0.dq_out, cram0_dq_oe => c0.dq_oe, cram0_dq_in => c0.dq_in, cram0_clk => c0.clk,
             cram0_adv_n => c0.adv_n, cram0_cre => c0.cre, cram0_ce0_n => c0.ce0_n, cram0_ce1_n => c0.ce1_n, cram0_oe_n => c0.oe_n,
             cram0_we_n => c0.we_n, cram0_ub_n => c0.ub_n, cram0_lb_n => c0.lb_n,
             cram1_a => c1.a, cram1_dq_out => c1.dq_out, cram1_dq_oe => c1.dq_oe, cram1_dq_in => c1.dq_in, cram1_clk => c1.clk,
             cram1_adv_n => c1.adv_n, cram1_cre => c1.cre, cram1_ce0_n => c1.ce0_n, cram1_ce1_n => c1.ce1_n, cram1_oe_n => c1.oe_n,
             cram1_we_n => c1.we_n, cram1_ub_n => c1.ub_n, cram1_lb_n => c1.lb_n);

   -- board: FPGA outputs -> PSRAM after T_OUT, PSRAM data -> FPGA inputs after T_IN
   m0.a <= transport c0.a after T_OUT;   m0.clk <= transport c0.clk after T_OUT;   m0.adv_n <= transport c0.adv_n after T_OUT;
   m0.cre <= transport c0.cre after T_OUT;   m0.ce0_n <= transport c0.ce0_n after T_OUT;   m0.ce1_n <= transport c0.ce1_n after T_OUT;
   m0.oe_n <= transport c0.oe_n after T_OUT;   m0.we_n <= transport c0.we_n after T_OUT;   m0.ub_n <= transport c0.ub_n after T_OUT;
   m0.lb_n <= transport c0.lb_n after T_OUT;
   m1.a <= transport c1.a after T_OUT;   m1.clk <= transport c1.clk after T_OUT;   m1.adv_n <= transport c1.adv_n after T_OUT;
   m1.cre <= transport c1.cre after T_OUT;   m1.ce0_n <= transport c1.ce0_n after T_OUT;   m1.ce1_n <= transport c1.ce1_n after T_OUT;
   m1.oe_n <= transport c1.oe_n after T_OUT;   m1.we_n <= transport c1.we_n after T_OUT;   m1.ub_n <= transport c1.ub_n after T_OUT;
   m1.lb_n <= transport c1.lb_n after T_OUT;
   f0 <= c0.dq_out when c0.dq_oe = '1' else (others => 'Z');
   f1 <= c1.dq_out when c1.dq_oe = '1' else (others => 'Z');
   dq0 <= transport f0 after T_OUT;
   dq1 <= transport f1 after T_OUT;
   c0.dq_in <= transport dq0 after T_IN;
   c1.dq_in <= transport dq1 after T_IN;

   im0 : entity work.psram_model generic map (ROW_WORDS => ROW_WORDS)
   port map (a => m0.a, dq => dq0, clk => m0.clk, cre => m0.cre, adv_n => m0.adv_n, ce0_n => m0.ce0_n, ce1_n => m0.ce1_n,
             oe_n => m0.oe_n, we_n => m0.we_n, ub_n => m0.ub_n, lb_n => m0.lb_n);
   im1 : entity work.psram_model generic map (ROW_WORDS => ROW_WORDS)
   port map (a => m1.a, dq => dq1, clk => m1.clk, cre => m1.cre, adv_n => m1.adv_n, ce0_n => m1.ce0_n, ce1_n => m1.ce1_n,
             oe_n => m1.oe_n, we_n => m1.we_n, ub_n => m1.ub_n, lb_n => m1.lb_n);


   GPU : process
      variable index : natural := 0;
   begin
      wait until reset_2x='0';
      loop wait until falling_edge(clk2x);exit when native_busy='0';end loop;
      while not done_sim loop
         wait until falling_edge(clk2x);
         if native_busy='0' then
            native_addr<=std_logic_vector(to_unsigned(index mod 4096,25));
            case index mod 5 is
               when 0 => native_burst<=x"00"; -- 256 DDR words, preemptible by AUX
               when 1 => native_burst<=x"80";
               when 2 => native_burst<=x"01";
               when 3 => native_burst<=x"20";
               when others => native_burst<=x"07";
            end case;
            native_rd<='1';test_GPU_bursts<=test_GPU_bursts+1;
            wait until falling_edge(clk2x);native_rd<='0';index:=index+1;
         end if;
      end loop;
      wait;
   end process;
   check_GPU : process(clk2x)
   begin
      if rising_edge(clk2x) and native_rdy='1' then
         assert native_dout=x"0000000000000000" report "GPU_BACKGROUND_DATA_MISMATCH" severity failure;
         test_GPU_words<=test_GPU_words+1;
      end if;
   end process;
   AUX_deadline : process(clk2x)
      variable initialized,eligible,pending : boolean := false;
      variable age : natural := 0;
   begin
      if rising_edge(clk2x) then
         if native_busy='0' then initialized:=true;end if;
         if initialized and aux_req='0' then eligible:=true;end if;
         if eligible and not pending and aux_req='1' then age:=0;pending:=true;eligible:=false;end if;
         if pending then
            age:=age+1;
            assert age<=30 report "NATIVE_AUX_DEADLINE_OVER30" severity failure;
            if aux_done='1' then
               if age>test_AUX_max then test_AUX_max<=age;end if;
               pending:=false;
            end if;
         end if;
      end if;
   end process;
   composed_coverage : process
   begin
      wait until done_sim;wait for 1 ns;
      assert test_GPU_bursts>100 and test_GPU_words>1000 report "NATIVE_GPU_COVERAGE" severity failure;
      report "CARD_NATIVE_COVERAGE GPU_bursts=" & natural'image(test_GPU_bursts)
         & " GPU_words=" & natural'image(test_GPU_words) & " AUX_max=" & natural'image(test_AUX_max);
      wait;
   end process;

   stim : process
      variable l : line;
      file f : text;
      procedure read_word(i : natural) is
      begin
         wait until rising_edge(clk_74a);
         bridge_addr <= std_logic_vector(to_unsigned(16#20000000# + 4*i,32));bridge_rd<='1';
         wait until rising_edge(clk_74a);bridge_rd<='0';
         for k in 1 to 86 loop wait until rising_edge(clk_74a);end loop;
      end procedure;
      type tIndices is array(0 to 4) of natural;
      constant random_indices : tIndices := (32767,2,1047,9,0);
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
            for k in 1 to 86 loop wait until rising_edge(clk_74a); end loop;
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
      for i in 0 to READ_WORDS-1 loop
         read_word(i);
         hwrite(l,bridge_rd_data);writeline(f,l);
      end loop;
      file_close(f);
      if LOAD_KIND=2 then
         file_open(f,DUMP & ".random",write_mode);
         for i in random_indices'range loop
            read_word(random_indices(i));hwrite(l,bridge_rd_data);writeline(f,l);
         end loop;
         file_close(f);
      end if;
      for k in 1 to 20 loop wait until rising_edge(clk2x); end loop;
      done_sim <= true;
      wait for 100 ns;
      report "tb_memcard_format done" severity note;
      wait;
   end process;
end architecture;
