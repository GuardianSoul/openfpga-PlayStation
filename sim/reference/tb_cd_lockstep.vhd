-- SPDX-License-Identifier: MIT
-- Clock-level lockstep: the reference cd_top (library ref) and a candidate
-- cd_top (library work) receive identical inputs; every output must match on
-- every clk1x edge. Stimulus: random CPU command sequences (with parameters,
-- IRQ acks, response/data reads, data requests, DMA reads), ce gaps, occasional
-- resets, and a disc host that answers sector requests like
-- sim/reference/psx_disc_host.sv (ack, 2 idle, 1176 writes, 2 idle, ack low).
library ieee, ref;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
use ieee.math_real.all;
use std.textio.all;

entity tb_cd_lockstep is
   generic (SEED : integer := 1; RUN_CYCLES : integer := 2000000; SECTORS : integer := 40;
            METADATA : string := "metadata.hex"; FASTCD_IN : integer := 0;
            PAUSE_READ : integer := 0);   -- 1: also Pause mid-sector while reading or playing
end entity;

architecture sim of tb_cd_lockstep is
   signal clk1x, ce, reset : std_logic := '0';
   signal ce_hold : std_logic := '0';
   signal spu_tick, bus_read, bus_write, dma_read : std_logic := '0';
   signal bus_addr : unsigned(3 downto 0) := (others => '0');
   signal bus_dataWrite : std_logic_vector(7 downto 0) := (others => '0');
   signal cd_hps_ack, cd_hps_write, trackinfo_write : std_logic := '0';
   signal cd_hps_data : std_logic_vector(15 downto 0) := (others => '0');
   signal trackinfo_data : std_logic_vector(31 downto 0) := (others => '0');
   signal trackinfo_addr : std_logic_vector(8 downto 0) := (others => '0');
   signal hasCD : std_logic := '0';
   signal lidopen_s, fastcd_s : std_logic;
   signal done : boolean := false;

   type tout is record
      region_out : std_logic_vector(1 downto 0);
      pauseCD, Pause_idle_cd, fullyIdle, cdSlow, err, irqOut, cd_hps_req, resetFromCD, SS_idle : std_logic;
      LBAdisplay : unsigned(19 downto 0);
      cd_left, cd_right : signed(15 downto 0);
      bus_dataRead, dma_readdata : std_logic_vector(7 downto 0);
      cd_hps_lba : std_logic_vector(31 downto 0);
   end record;
   signal a, b : tout;
   signal cycles : integer := 0;
   signal n_irq, n_sectors, n_cmds : integer := 0;
begin
   clk1x <= not clk1x after 14765 ps when not done;
   lidopen_s <= not hasCD;
   fastcd_s <= '1' when FASTCD_IN /= 0 else '0';

   gen : for side in 0 to 1 generate
      signal o : tout;
   begin
      r : if side = 0 generate
         u : entity ref.cd_top port map (
            clk1x => clk1x, ce => ce, reset => reset, INSTANTSEEK => '0', FORCECDSPEED => "000",
            LIMITREADSPEED => '0', hasCD => hasCD, LIDopen => lidopen_s, fastCD => fastcd_s,
            testSeek => '0', pauseOnCDSlow => '0', region => "10", region_out => o.region_out,
            pauseCD => o.pauseCD, Pause_idle_cd => o.Pause_idle_cd, fullyIdle => o.fullyIdle, cdSlow => o.cdSlow,
            error => o.err, LBAdisplay => o.LBAdisplay, irqOut => o.irqOut, spu_tick => spu_tick,
            cd_left => o.cd_left, cd_right => o.cd_right, mdec_idle => '1', bus_addr => bus_addr,
            bus_dataWrite => bus_dataWrite, bus_read => bus_read, bus_write => bus_write,
            bus_dataRead => o.bus_dataRead, dma_read => dma_read, dma_readdata => o.dma_readdata,
            cd_hps_req => o.cd_hps_req, cd_hps_lba => o.cd_hps_lba, cd_hps_lba_sim => open,
            cd_hps_ack => cd_hps_ack, cd_hps_write => cd_hps_write, cd_hps_data => cd_hps_data,
            trackinfo_data => trackinfo_data, trackinfo_addr => trackinfo_addr, trackinfo_write => trackinfo_write,
            resetFromCD => o.resetFromCD, SS_reset => reset, SS_DataWrite => (others => '0'), SS_Adr => (others => '0'),
            SS_wren => '0', SS_rden => '0', SS_DataRead => open, SS_idle => o.SS_idle);
         a <= o;
      end generate;
      d : if side = 1 generate
         u : entity work.cd_top port map (
            clk1x => clk1x, ce => ce, reset => reset, INSTANTSEEK => '0', FORCECDSPEED => "000",
            LIMITREADSPEED => '0', hasCD => hasCD, LIDopen => lidopen_s, fastCD => fastcd_s,
            testSeek => '0', pauseOnCDSlow => '0', region => "10", region_out => o.region_out,
            pauseCD => o.pauseCD, Pause_idle_cd => o.Pause_idle_cd, fullyIdle => o.fullyIdle, cdSlow => o.cdSlow,
            error => o.err, LBAdisplay => o.LBAdisplay, irqOut => o.irqOut, spu_tick => spu_tick,
            cd_left => o.cd_left, cd_right => o.cd_right, mdec_idle => '1', bus_addr => bus_addr,
            bus_dataWrite => bus_dataWrite, bus_read => bus_read, bus_write => bus_write,
            bus_dataRead => o.bus_dataRead, dma_read => dma_read, dma_readdata => o.dma_readdata,
            cd_hps_req => o.cd_hps_req, cd_hps_lba => o.cd_hps_lba, cd_hps_lba_sim => open,
            cd_hps_ack => cd_hps_ack, cd_hps_write => cd_hps_write, cd_hps_data => cd_hps_data,
            trackinfo_data => trackinfo_data, trackinfo_addr => trackinfo_addr, trackinfo_write => trackinfo_write,
            resetFromCD => o.resetFromCD, SS_reset => reset, SS_DataWrite => (others => '0'), SS_Adr => (others => '0'),
            SS_wren => '0', SS_rden => '0', SS_DataRead => open, SS_idle => o.SS_idle);
         b <= o;
      end generate;
   end generate;

   -- compare every output on every edge, after the first reset
   check : process(clk1x)
      variable started : boolean := false;
      variable irq_d, ack_d : std_logic := '0';
   begin
      if rising_edge(clk1x) then
         cycles <= cycles + 1;
         if reset = '1' then started := true; end if;
         if a.irqOut = '1' and irq_d = '0' then n_irq <= n_irq + 1; end if;
         irq_d := a.irqOut;
         if cd_hps_ack = '1' and ack_d = '0' then n_sectors <= n_sectors + 1; end if;
         ack_d := cd_hps_ack;
         if started and a /= b then
            report "LOCKSTEP MISMATCH cycle " & integer'image(cycles)
               & " irq " & std_logic'image(a.irqOut) & "/" & std_logic'image(b.irqOut)
               & " rd " & to_hstring(a.bus_dataRead) & "/" & to_hstring(b.bus_dataRead)
               & " dma " & to_hstring(a.dma_readdata) & "/" & to_hstring(b.dma_readdata)
               & " req " & std_logic'image(a.cd_hps_req) & "/" & std_logic'image(b.cd_hps_req)
               & " lba " & to_hstring(a.cd_hps_lba) & "/" & to_hstring(b.cd_hps_lba)
               & " lbadisp " & to_hstring(a.LBAdisplay) & "/" & to_hstring(b.LBAdisplay)
               & " idle " & std_logic'image(a.fullyIdle) & "/" & std_logic'image(b.fullyIdle)
               & " pause " & std_logic'image(a.pauseCD) & "/" & std_logic'image(b.pauseCD)
               severity failure;
         end if;
      end if;
   end process;

   spu : process(clk1x)
      variable n : integer := 0;
   begin
      if rising_edge(clk1x) then
         if n = 767 then n := 0; spu_tick <= '1'; else n := n + 1; spu_tick <= '0'; end if;
      end if;
   end process;

   -- ce: on, with rare pauses
   cegen : process(clk1x)
      variable s1, s2 : positive := SEED + 7;
      variable r : real;
      variable gap : integer := 0;
   begin
      if rising_edge(clk1x) then
         if gap > 0 then gap := gap - 1; ce <= '0';
         else
            uniform(s1, s2, r);
            -- the core only drops ce for pauses: rare, short or long
            if r < 1.0 / 400000.0 then gap := 1 + integer(r * 400000.0 * 3000.0); ce <= '0'; else ce <= '1'; end if;
         end if;
      end if;
   end process;

   -- disc host: track table once per reset, then sectors on request
   host : process
      file f : text;
      variable l : line;
      variable w : std_logic_vector(31 downto 0);
      variable s1, s2 : positive := SEED + 13;
      variable r : real;
      variable lba : integer;
      type twords is array(0 to 399) of std_logic_vector(31 downto 0);
      variable meta : twords;
   begin
      file_open(f, METADATA, read_mode);
      for i in 0 to 399 loop readline(f, l); hread(l, w); meta(i) := w; end loop;
      file_close(f);
      loop
         wait until falling_edge(clk1x) and reset = '0';
         for i in 0 to 399 loop
            trackinfo_addr <= std_logic_vector(to_unsigned(i, 9)); trackinfo_data <= meta(i); trackinfo_write <= '1';
            wait until falling_edge(clk1x); trackinfo_write <= '0';
            wait until falling_edge(clk1x);
         end loop;
         hasCD <= '1';
         while reset = '0' loop
            wait until falling_edge(clk1x);
            if a.cd_hps_req = '1' and cd_hps_ack = '0' then
               lba := to_integer(unsigned(a.cd_hps_lba(19 downto 0)));
               uniform(s1, s2, r);
               for i in 1 to integer(r * 3000.0) loop wait until falling_edge(clk1x); end loop;
               cd_hps_ack <= '1';
               wait until falling_edge(clk1x); wait until falling_edge(clk1x);
               for i in 0 to 1175 loop
                  if lba < 150 then cd_hps_data <= x"0000";
                  elsif lba >= 150 + SECTORS then cd_hps_data <= x"AAAA";
                  else cd_hps_data <= std_logic_vector(to_unsigned((lba * 101 + i * 29) mod 65536, 16)); end if;
                  cd_hps_write <= '1';
                  wait until falling_edge(clk1x);
               end loop;
               cd_hps_write <= '0';
               wait until falling_edge(clk1x); wait until falling_edge(clk1x);
               cd_hps_ack <= '0';
            end if;
         end loop;
      end loop;
   end process;

   -- CPU: random command traffic
   cpu : process
      variable s1, s2 : positive := SEED + 29;
      variable r : real;
      function pick(r : real; n : integer) return integer is begin return integer(floor(r * real(n))) mod n; end function;
      procedure tick(n : integer) is begin for i in 1 to n loop wait until falling_edge(clk1x); end loop; end procedure;
      -- Bus accesses only on clocks with ce, as the CPU's are.
      procedure wr(adr : integer; v : integer) is
      begin
         wait until falling_edge(clk1x) and ce = '1';
         bus_addr <= to_unsigned(adr, 4); bus_dataWrite <= std_logic_vector(to_unsigned(v mod 256, 8)); bus_write <= '1';
         wait until falling_edge(clk1x); bus_write <= '0';
      end procedure;
      procedure rd(adr : integer) is
      begin
         wait until falling_edge(clk1x) and ce = '1'; bus_addr <= to_unsigned(adr, 4); bus_read <= '1';
         wait until falling_edge(clk1x); bus_read <= '0';
      end procedure;
      type tcmds is array(natural range <>) of integer;
      constant cmds : tcmds := (16#01#, 16#02#, 16#03#, 16#06#, 16#07#, 16#08#, 16#09#, 16#0A#, 16#0B#, 16#0C#,
                                16#0D#, 16#0E#, 16#0F#, 16#10#, 16#11#, 16#12#, 16#13#, 16#14#, 16#15#, 16#16#,
                                16#19#, 16#1A#, 16#1B#, 16#1E#, 16#02#, 16#06#, 16#1B#, 16#09#, 16#15#);
      variable c, sec, n : integer;
   begin
      reset <= '1'; tick(12); reset <= '0';
      wait until hasCD = '1'; tick(100);
      wr(0, 1); wr(2, 31); wr(0, 1); wr(3, 31); wr(0, 0);
      while cycles < RUN_CYCLES loop
         uniform(s1, s2, r);
         if r < 0.30 then   -- a command with its parameters
            uniform(s1, s2, r); c := cmds(pick(r, cmds'length));
            wr(0, 0);
            if c = 16#02# then   -- Setloc inside or just past the disc
               uniform(s1, s2, r); sec := 150 + pick(r, SECTORS + 8);
               wr(2, ((sec / 4500) / 10) * 16 + (sec / 4500) mod 10);
               wr(2, (((sec / 75) mod 60) / 10) * 16 + ((sec / 75) mod 60) mod 10);
               wr(2, ((sec mod 75) / 10) * 16 + (sec mod 75) mod 10);
            elsif c = 16#0E# then uniform(s1, s2, r); wr(2, pick(r, 256));
            elsif c = 16#0D# then wr(2, 1); wr(2, 1);
            elsif c = 16#12# or c = 16#14# then uniform(s1, s2, r); wr(2, pick(r, 3));
            elsif c = 16#19# then   -- Test: SCEx counters, BIOS date or region string
               uniform(s1, s2, r); n := pick(r, 4);
               if n = 0 then wr(2, 16#04#); elsif n = 1 then wr(2, 16#05#); elsif n = 2 then wr(2, 16#20#); else wr(2, 16#22#); end if;
            end if;
            wr(1, c); n_cmds <= n_cmds + 1;
         elsif r < 0.55 then   -- poll and acknowledge IRQs, read responses
            wr(0, 1); rd(3); uniform(s1, s2, r);
            for i in 0 to pick(r, 10) loop wr(0, 1); rd(1); end loop;
            uniform(s1, s2, r);
            if r < 0.8 then wr(0, 1); wr(3, 31); end if;
            wr(0, 0);
         elsif r < 0.65 then   -- request data and read some bytes, or DMA
            wr(0, 0); wr(3, 16#80#); uniform(s1, s2, r);
            for i in 0 to pick(r, 40) loop
               uniform(s1, s2, r);
               if r < 0.5 then rd(2); else wait until falling_edge(clk1x) and ce = '1'; dma_read <= '1'; wait until falling_edge(clk1x); dma_read <= '0'; end if;
            end loop;
         elsif r < 0.70 then   -- audio: Setmode CDDA+report, Setloc in track 2, Play, then service IRQs
            wr(0, 0); uniform(s1, s2, r); wr(2, 16#05# + 16#80# * pick(r, 2)); wr(1, 16#0E#); n_cmds <= n_cmds + 1;
            tick(40000); wr(0, 1); wr(3, 31); wr(0, 0);
            uniform(s1, s2, r); sec := 150 + 40 + 150 + pick(r, 40);
            wr(2, ((sec / 4500) / 10) * 16 + (sec / 4500) mod 10);
            wr(2, (((sec / 75) mod 60) / 10) * 16 + ((sec / 75) mod 60) mod 10);
            wr(2, ((sec mod 75) / 10) * 16 + (sec mod 75) mod 10);
            wr(1, 16#02#); n_cmds <= n_cmds + 1;
            tick(40000); wr(0, 1); wr(3, 31); wr(0, 0);
            wr(1, 16#03#); n_cmds <= n_cmds + 1;
            for i in 0 to 60 loop
               uniform(s1, s2, r); tick(2000 + pick(r, 20000));
               wr(0, 1); rd(3); uniform(s1, s2, r);
               for j in 0 to pick(r, 10) loop rd(1); end loop;
               wr(3, 31); wr(0, 0);
            end loop;
         elsif PAUSE_READ = 1 and r < 0.80 then   -- Pause while reading (data track) or playing (audio track)
            uniform(s1, s2, r); n := pick(r, 2);
            uniform(s1, s2, r);
            if n = 0 then sec := 150 + pick(r, 30); else sec := 150 + 40 + 150 + pick(r, 30); end if;
            wr(0, 0);
            wr(2, ((sec / 4500) / 10) * 16 + (sec / 4500) mod 10);
            wr(2, (((sec / 75) mod 60) / 10) * 16 + ((sec / 75) mod 60) mod 10);
            wr(2, ((sec mod 75) / 10) * 16 + (sec mod 75) mod 10);
            wr(1, 16#02#); n_cmds <= n_cmds + 1;
            tick(40000); wr(0, 1); wr(3, 31); wr(0, 0);
            if n = 0 then wr(1, 16#06#); else wr(1, 16#03#); end if; n_cmds <= n_cmds + 1;
            uniform(s1, s2, r);
            for i in 0 to 2 + pick(r, 12) loop   -- let sectors pass, at a random point in one
               uniform(s1, s2, r); tick(2000 + pick(r, 30000));
               wr(0, 1); rd(3); rd(1); wr(3, 31); wr(0, 0);
            end loop;
            wr(1, 16#09#); n_cmds <= n_cmds + 1;
            for i in 0 to 20 loop
               uniform(s1, s2, r); tick(2000 + pick(r, 20000));
               wr(0, 1); rd(3); rd(1); wr(3, 31); wr(0, 0);
            end loop;
         elsif r < 0.995 then   -- idle
            uniform(s1, s2, r); tick(pick(r * r, 60000));
         else   -- reset
            reset <= '1'; tick(12); reset <= '0'; wait until hasCD = '1' for 1 ms; tick(1000);
            wr(0, 1); wr(2, 31); wr(0, 1); wr(3, 31); wr(0, 0);
         end if;
      end loop;
      report "LOCKSTEP PASS cycles " & integer'image(cycles) & " commands " & integer'image(n_cmds)
         & " irqs " & integer'image(n_irq) & " sectors " & integer'image(n_sectors) severity note;
      done <= true;
      wait;
   end process;
end architecture;
