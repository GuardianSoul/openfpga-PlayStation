-- SPDX-License-Identifier: MIT
-- Save with a disc read in flight, load as a wake, the read completes
-- (docs/save-states-finish/drive-state.md). The project's cd_top with the save
-- taps. The bench's CPU plays a game that starts a read (CASE), then the save
-- routine's CD quiet check (cd_quiet: tap busy byte, stat seek bit, status
-- register, IRQ flags) once per retry, about 1 ms apart, the game running in
-- between, until it passes or RETRY_MAX retries; at the pass the tap words are
-- the state. Then a wake: cd_top is reset, the BIOS starts, the load routine
-- (load_start with the early-load step, cd_resume as ROUTINE) restores the
-- drive, and the restored game expects INT1 for the sector it waits for, three
-- in a row, and nothing else. CASE 9 skips the live save and loads the state
-- given by the S* generics (a state taken on hardware).
-- ROUTINE 0: cd_resume of routine g (stat reading -> Setloc(LBA + 1 + 150) +
--   ReadN; playing -> Setloc(LBA + 150) + Play; else nothing).
-- ROUTINE 1: with the drive-state routine step (drive-state.md): no +150, an idle
--   drive SeekL'd back to its position.
-- Result: one line "SAVEREAD <verdict> ..." (OK, STUCK, STRAY, WRONGLBA, SAVEFAIL).
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
use std.textio.all;

entity tb_cd_save_read is
   generic (METADATA : string := "metadata.hex";
            CASE_N : integer := 0;
            ROUTINE : integer := 0;
            DELAY : integer := 100000;      -- clocks of game time between the case's last step and the save request
            STALL : integer := 0;           -- 1: the Pocket serves no sector from the case's stall point until the save ends
            LSTALL : integer := 0;          -- 1: no sector served during the load routine either (released at its end)
            TARGET : integer := 2150;       -- the game's Setloc (absolute LBA, MSF frames)
            MODE : integer := 16#80#;       -- the game's mode
            SSTAT : integer := 16#22#; SMODE : integer := 16#80#; SLBA : integer := 33902;  -- CASE 9: the state's tap words
            SFLAGS : integer := 0;          -- CASE 9: tap bits 63:57 of the state
            POLL : integer := 10;
            RESTORE : integer := 2000000;   -- clocks between load_start and cd_resume (the page copies)
            RETRY_MAX : integer := 500;
            FREEZE : integer := 20000000;   -- case 8: clocks the drive stands still (CD_FREEZE) with the Pocket silent
            GAP : integer := 40000000;      -- case 10: clocks the Pocket serves nothing
            TOUT : integer := 10000000;     -- case 10: the game's time-out before it asks again
            RECOV : integer := 1;           -- case 10: 0 waits only; 1 Setloc + ReadN; 2 Pause, Setloc + ReadN; 3 Init, Setmode, Setloc + ReadN
            NOSAVE : integer := 0;          -- 1: no save and no load (the drive's own behaviour, the reference)
            FAULT : integer := 0);
end entity;

architecture sim of tb_cd_save_read is
   signal clk1x, reset : std_logic := '0';
   signal ce : std_logic := '1';             -- CD_FREEZE: cd_top's clock enable
   signal spu_tick, bus_read, bus_write : std_logic := '0';
   signal bus_addr : unsigned(3 downto 0) := (others => '0');
   signal bus_dataWrite : std_logic_vector(7 downto 0) := (others => '0');
   signal cd_hps_ack, cd_hps_write, trackinfo_write : std_logic := '0';
   signal cd_hps_data : std_logic_vector(15 downto 0) := (others => '0');
   signal trackinfo_data : std_logic_vector(31 downto 0) := (others => '0');
   signal trackinfo_addr : std_logic_vector(8 downto 0) := (others => '0');
   signal hasCD, meta_done : std_logic := '0';
   signal done : boolean := false;
   signal irqOut, cd_hps_req : std_logic;
   signal bus_dataRead, dma_readdata : std_logic_vector(7 downto 0);
   signal cd_hps_lba : std_logic_vector(31 downto 0);
   signal cd_state : std_logic_vector(63 downto 0);
   signal cycles : integer := 0;
   signal served, last_served : integer := 0;
   signal host_stall : std_logic := '0';
   signal irq_latch, irq_clear, irq_clear_seen : std_logic := '0';
begin
   clk1x <= not clk1x after 14765 ps when not done;

   u : entity work.cd_top port map (
      clk1x => clk1x, ce => ce, reset => reset, INSTANTSEEK => '0', FORCECDSPEED => "000",
      LIMITREADSPEED => '0', hasCD => hasCD, LIDopen => '0', fastCD => '0',
      testSeek => '0', pauseOnCDSlow => '0', region => "00", region_out => open,
      pauseCD => open, Pause_idle_cd => open, fullyIdle => open, cdSlow => open,
      error => open, LBAdisplay => open, irqOut => irqOut, spu_tick => spu_tick,
      cd_left => open, cd_right => open, mdec_idle => '1', bus_addr => bus_addr,
      bus_dataWrite => bus_dataWrite, bus_read => bus_read, bus_write => bus_write,
      bus_dataRead => bus_dataRead, dma_read => '0', dma_readdata => dma_readdata,
      cd_hps_req => cd_hps_req, cd_hps_lba => cd_hps_lba, cd_hps_lba_sim => open,
      cd_hps_ack => cd_hps_ack, cd_hps_write => cd_hps_write, cd_hps_data => cd_hps_data,
      trackinfo_data => trackinfo_data, trackinfo_addr => trackinfo_addr, trackinfo_write => trackinfo_write,
      resetFromCD => open, SS_reset => reset, SS_DataWrite => (others => '0'), SS_Adr => (others => '0'),
      SS_wren => '0', SS_rden => '0', SS_DataRead => open, SS_idle => open,
      save_cd_state => cd_state, save_set_sel => "000", save_set_data => x"00");

   count : process(clk1x) begin if rising_edge(clk1x) then cycles <= cycles + 1; end if; end process;

   latch : process(clk1x)
   begin
      if rising_edge(clk1x) then
         if irq_clear /= irq_clear_seen then irq_clear_seen <= irq_clear; irq_latch <= '0'; end if;
         if irqOut = '1' then irq_latch <= '1'; end if;
      end if;
   end process;

   spu : process(clk1x)
      variable n : integer := 0;
   begin
      if rising_edge(clk1x) then
         if n = 767 then n := 0; spu_tick <= '1'; else n := n + 1; spu_tick <= '0'; end if;
      end if;
   end process;

   -- the Pocket: serves each sector request after 1,500 clocks; the sector's header carries its LBA (MSF frames)
   host : process
      file f : text;
      variable l : line;
      variable w : std_logic_vector(31 downto 0);
      variable lba : integer;
      type twords is array(0 to 399) of std_logic_vector(31 downto 0);
      variable meta : twords;
   begin
      file_open(f, METADATA, read_mode);
      for i in 0 to 399 loop readline(f, l); hread(l, w); meta(i) := w; end loop;
      file_close(f);
      wait until falling_edge(clk1x) and reset = '0';
      for i in 0 to 399 loop
         trackinfo_addr <= std_logic_vector(to_unsigned(i, 9)); trackinfo_data <= meta(i); trackinfo_write <= '1';
         wait until falling_edge(clk1x); trackinfo_write <= '0';
         wait until falling_edge(clk1x);
      end loop;
      hasCD <= '1';
      meta_done <= '1';
      loop
         wait until falling_edge(clk1x);
         if cd_hps_req = '1' and cd_hps_ack = '0' and host_stall = '0' then
            lba := to_integer(unsigned(cd_hps_lba(19 downto 0)));
            for i in 1 to 1500 loop wait until falling_edge(clk1x); end loop;
            cd_hps_ack <= '1';
            wait until falling_edge(clk1x); wait until falling_edge(clk1x);
            for i in 0 to 1175 loop
               if i = 0 then cd_hps_data <= x"FF00";
               elsif i <= 4 then cd_hps_data <= x"FFFF";
               elsif i = 5 then cd_hps_data <= x"00FF";
               elsif i = 6 then
                  cd_hps_data <= std_logic_vector(to_unsigned(((lba / 75) mod 60 / 10) * 16 + (lba / 75) mod 60 mod 10, 8))
                               & std_logic_vector(to_unsigned(((lba / 4500) / 10) * 16 + (lba / 4500) mod 10, 8));
               elsif i = 7 then
                  cd_hps_data <= x"02" & std_logic_vector(to_unsigned(((lba mod 75) / 10) * 16 + (lba mod 75) mod 10, 8));
               elsif i <= 11 then cd_hps_data <= x"0000";
               else cd_hps_data <= std_logic_vector(to_unsigned((lba * 101 + i * 29) mod 65536, 16)); end if;
               cd_hps_write <= '1';
               wait until falling_edge(clk1x);
            end loop;
            cd_hps_write <= '0';
            wait until falling_edge(clk1x); wait until falling_edge(clk1x);
            cd_hps_ack <= '0'; served <= served + 1; last_served <= lba;
         end if;
      end loop;
   end process;

   cpu : process
      variable kind, v, v0, s6, refused, n1, strays, last_stray, first_lba, inits, retries, recovs : integer := 0;
      variable want, got_int1, last_int1, wrong, s_stat, s_mode, s_filt, s_lba, s_flags, s_irqena : integer := 0;
      variable saved : boolean := false;
      procedure tick(n : integer) is begin for i in 1 to n loop wait until falling_edge(clk1x); end loop; end procedure;
      procedure wr(adr : integer; d : integer) is
      begin
         wait until falling_edge(clk1x);
         bus_addr <= to_unsigned(adr, 4); bus_dataWrite <= std_logic_vector(to_unsigned(d mod 256, 8)); bus_write <= '1';
         wait until falling_edge(clk1x); bus_write <= '0';
      end procedure;
      procedure rd(adr : integer; d : out integer) is
      begin
         wait until falling_edge(clk1x); bus_addr <= to_unsigned(adr, 4); bus_read <= '1';
         wait until falling_edge(clk1x); bus_read <= '0'; d := to_integer(unsigned(bus_dataRead));
      end procedure;
      impure function tap_busy return integer is begin return to_integer(unsigned(cd_state(63 downto 56))); end function;
      impure function tap_stat return integer is begin return to_integer(unsigned(cd_state(7 downto 0))); end function;
      impure function tap_lba return integer is begin return to_integer(unsigned(cd_state(55 downto 32))); end function;
      procedure bcd3(lba : integer) is
         variable m, s, fr : integer;
      begin
         m := lba / 4500; s := lba / 75 mod 60; fr := lba mod 75;
         wr(0, 0); wr(2, (m / 10) * 16 + m mod 10); wr(2, (s / 10) * 16 + s mod 10); wr(2, (fr / 10) * 16 + fr mod 10);
      end procedure;
      procedure g_cmd(c : integer) is begin wr(0, 0); wr(1, c); end procedure;
      -- the game's interrupt handler: one interrupt (acked), INT1 counted with the tap's LBA (the sector delivered)
      procedure g_int(k : out integer; limit : integer) is
         variable d, n : integer;
      begin
         n := 0;
         while irq_latch = '0' and n < limit loop tick(16); n := n + 16; end loop;
         if irq_latch = '0' then k := 0; return; end if;
         irq_clear <= not irq_clear; tick(2);
         wr(0, 1); rd(3, d); k := d mod 8;
         if k = 1 then
            got_int1 := got_int1 + 1;
            if want >= 0 and tap_lba /= want then wrong := wrong + 1; end if;
            last_int1 := tap_lba; want := tap_lba + 1;
         end if;
         for i in 0 to 7 loop rd(1, d); end loop;
         wr(0, 1); wr(3, 31); wr(0, 0);
         tick(64);
      end procedure;
      -- the game between two save retries: handles what comes for n clocks
      procedure g_run(n : integer) is
         variable t, k : integer;
      begin
         t := cycles + n;
         while cycles < t loop
            if irq_latch = '1' then g_int(k, 16); else tick(16); end if;
         end loop;
      end procedure;
      -- the routine's cd_wait (a2 = looks) and cd_cmd: res = first response byte or -1
      procedure r_wait(a2 : integer; res : out integer) is
         variable d, f, looks, r0 : integer;
      begin
         looks := a2;
         wr(0, 1);
         loop
            rd(3, d); f := d mod 8; looks := looks - 1; tick(POLL);
            if f /= 0 then
               rd(1, r0);
               loop
                  rd(0, d);
                  exit when (d / 32) mod 2 = 0;
                  rd(1, d);
               end loop;
               wr(3, 31);
               if f = 1 then null;
               elsif f = 3 or f = 2 then res := r0; return;
               else res := -1; return; end if;
            elsif looks = 0 then res := -1; return;
            end if;
         end loop;
      end procedure;
      procedure r_cmd(c : integer; np : integer; p : integer; res : out integer) is
         variable q : integer;
      begin
         wr(0, 0); q := p;
         for i in 1 to np loop wr(2, q mod 256); q := q / 256; end loop;
         wr(1, c);
         r_wait(16#40000#, res);
      end procedure;
      -- the routine's msf (the +150 of routine g unless ROUTINE 1)
      procedure r_msf(lba : integer; p : out integer) is
         variable m, s, fr, a : integer;
      begin
         a := lba;
         if ROUTINE = 0 or FAULT = 3 then a := a + 150; end if;
         m := a / 4500; s := a / 75 mod 60; fr := a mod 75;
         p := ((m / 10) * 16 + m mod 10) + 256 * ((s / 10) * 16 + s mod 10) + 65536 * ((fr / 10) * 16 + fr mod 10);
      end procedure;
      -- cd_quiet: true at a quiet point (the state taken then)
      procedure r_quiet(ok : out boolean) is
         variable b, st, d, t2, ena, fl, idx : integer;
      begin
         ok := false;
         b := tap_busy; st := tap_stat;
         if b /= 0 or (st / 64) mod 2 = 1 then return; end if;
         rd(0, d); t2 := d mod 256;
         idx := d mod 4;
         t2 := (t2 / 128) * 128 + ((t2 / 32) mod 2) * 32 + ((t2 / 8) mod 2) * 8;
         if not (t2 = 8 or (t2 = 16#28# and retries >= RETRY_MAX / 2)) then return; end if;
         wr(0, 0); rd(3, ena); wr(0, 1); rd(3, fl); wr(0, idx);
         if fl mod 8 /= 0 then return; end if;
         if tap_busy /= 0 then return; end if;
         s_stat := tap_stat; s_mode := to_integer(unsigned(cd_state(15 downto 8)));
         s_filt := to_integer(unsigned(cd_state(31 downto 16))); s_lba := tap_lba;
         s_flags := tap_busy; s_irqena := ena mod 32;
         ok := true;
      end procedure;
      procedure verdict(name : string) is
      begin
         report "SAVEREAD " & name & " case=" & integer'image(CASE_N) & " routine=" & integer'image(ROUTINE)
            & " delay=" & integer'image(DELAY) & " stall=" & integer'image(STALL) & " fault=" & integer'image(FAULT)
            & " retries=" & integer'image(retries) & " s_stat=" & integer'image(s_stat) & " s_lba=" & integer'image(s_lba)
            & " s_flags=" & integer'image(s_flags) & " int1_before=" & integer'image(got_int1)
            & " int1=" & integer'image(n1) & " strays=" & integer'image(strays) & " stray_kind=" & integer'image(last_stray)
            & " first_lba=" & integer'image(first_lba) & " want=" & integer'image(want)
            & " wrong_before=" & integer'image(wrong) & " served=" & integer'image(served)
            & " recovs=" & integer'image(recovs) severity note;
         done <= true;
         wait;
      end procedure;
      variable p, d, flags, after_read, t0 : integer := 0;
      variable ok : boolean;
   begin
      want := -1;
      reset <= '1'; tick(12); reset <= '0';
      wait until meta_done = '1'; tick(100);
      wr(0, 1); wr(2, 31); wr(0, 1); wr(3, 31); wr(0, 0);
      after_read := 0;   -- 1: the restored game sends ReadN itself after the load
      if CASE_N /= 9 then
         -- ---------------------------------------------------------------
         -- the game: drive up, mode, a read
         -- ---------------------------------------------------------------
         tick(15000000);
         g_cmd(16#01#); g_int(kind, 4000000); g_cmd(16#01#); g_int(kind, 4000000);
         g_cmd(16#0A#); g_int(kind, 4000000); g_int(kind, 40000000);
         wr(0, 0); wr(2, MODE); g_cmd(16#0E#); g_int(kind, 4000000);
         tick(200000);
         want := TARGET;
         case CASE_N is
            when 0 | 5 =>                                -- SeekL complete, then ReadN: the gap before the first sector
               bcd3(TARGET); g_cmd(16#02#); g_int(kind, 4000000);
               g_cmd(16#15#); g_int(kind, 4000000); g_int(kind, 40000000);
               if CASE_N = 5 or STALL = 1 then host_stall <= '1'; end if;
               g_cmd(16#06#); g_int(kind, 4000000);
            when 1 =>                                    -- Setloc + ReadN (the seek inside ReadN)
               bcd3(TARGET); g_cmd(16#02#); g_int(kind, 4000000);
               if STALL = 1 then host_stall <= '1'; end if;
               g_cmd(16#06#); g_int(kind, 4000000);
            when 2 | 3 | 8 | 10 =>                                -- reading on: three sectors in; 3: the next request not served
               bcd3(TARGET); g_cmd(16#02#); g_int(kind, 4000000);
               g_cmd(16#06#); g_int(kind, 4000000);
               g_int(kind, 40000000); g_int(kind, 4000000); g_int(kind, 4000000);
               if CASE_N = 3 or STALL = 1 then host_stall <= '1'; end if;
            when 4 | 7 =>                                -- Setloc acked, ReadN not sent yet (7: sent at retry 5)
               bcd3(TARGET); g_cmd(16#02#); g_int(kind, 4000000);
               after_read := 1;
            when 6 =>                                    -- reading, then Pause complete; ReadN (no Setloc) after the load
               bcd3(TARGET); g_cmd(16#02#); g_int(kind, 4000000);
               g_cmd(16#06#); g_int(kind, 4000000);
               g_int(kind, 40000000); g_int(kind, 4000000); g_int(kind, 4000000);
               g_cmd(16#09#); g_int(kind, 4000000); g_int(kind, 4000000);
               after_read := 1;
            when others => null;
         end case;
         if CASE_N = 10 then
            -- the Pocket serves nothing for GAP clocks; the game asks again after each time-out
            host_stall <= '1'; t0 := cycles;
            while cycles - t0 < GAP loop
               p := got_int1; g_run(TOUT);
               if got_int1 = p and cycles - t0 < GAP then
                  recovs := recovs + 1;
                  case RECOV is
                     when 1 => bcd3(want); g_cmd(16#02#); g_int(kind, 4000000); g_cmd(16#06#); g_int(kind, 4000000);
                     when 2 => g_cmd(16#09#); g_int(kind, 4000000); g_int(kind, 40000000);
                               bcd3(want); g_cmd(16#02#); g_int(kind, 4000000); g_cmd(16#06#); g_int(kind, 4000000);
                     when 3 => g_cmd(16#0A#); g_int(kind, 4000000); g_int(kind, 40000000);
                               wr(0, 0); wr(2, MODE); g_cmd(16#0E#); g_int(kind, 4000000);
                               bcd3(want); g_cmd(16#02#); g_int(kind, 4000000); g_cmd(16#06#); g_int(kind, 4000000);
                     when others => null;
                  end case;
               end if;
            end loop;
            host_stall <= '0';
         end if;
         if NOSAVE = 1 then host_stall <= '0'; tick(DELAY); end if;
         -- ---------------------------------------------------------------
         -- the save: cd_quiet once per retry, the game running in between
         -- ---------------------------------------------------------------
         if NOSAVE = 0 then g_run(DELAY); end if;
         while NOSAVE = 0 loop
            r_quiet(ok);
            exit when ok;
            retries := retries + 1;
            if CASE_N = 7 and retries = 5 then g_cmd(16#06#); after_read := 0; end if;   -- the game's ReadN: in the state from now on
            if retries > RETRY_MAX then host_stall <= '0'; verdict("SAVEFAIL"); end if;
            g_run(33000);
         end loop;
         -- the state: the game waits for INT1 of `want` (Setloc target or the sector after its last)
         if CASE_N = 8 then
            -- a save that freezes the drive from its quiet point to the end of the read-out while the
            -- Pocket serves nothing; then the game goes on (no load): the next sectors, none lost
            -- (FAULT 5: no freeze: the drive reads on while the game is held and the Pocket serves)
            if FAULT = 5 then tick(FREEZE);
            else host_stall <= '1'; ce <= '0'; tick(FREEZE); ce <= '1'; tick(1000); host_stall <= '0'; end if;
         else
            tick(10000);
            host_stall <= '0';
         end if;
      else
         s_stat := SSTAT; s_mode := SMODE; s_filt := 0; s_lba := SLBA; s_flags := SFLAGS; s_irqena := 31;
         want := SLBA + 1;
      end if;

      -- ------------------------------------------------------------------
      -- the wake: core reset, BIOS start, the load routine
      -- ------------------------------------------------------------------
      if NOSAVE = 0 and CASE_N /= 8 then
      reset <= '1'; tick(12); reset <= '0';
      irq_clear <= not irq_clear; tick(2);
      if LSTALL = 1 then host_stall <= '1'; end if;
      tick(200000);
      -- load_start
      wr(0, 1); wr(2, 0); wr(3, 16#5F#);
      s6 := 16#400#;
      loop
         d := tap_busy; rd(3, flags); s6 := s6 - 1;
         exit when d = 0 and flags mod 8 = 0;
         r_wait(16#100#, v0);
         exit when s6 = 0;
      end loop;
      -- early-load: Getstat; not (motor on, shell closed): GetID and again
      s6 := 1024; refused := 1;
      loop
         r_cmd(16#01#, 0, 0, v0);
         if v0 >= 0 and (v0 mod 4) / 2 = 1 and (v0 mod 32) / 16 = 0 then refused := 0; exit; end if;
         s6 := s6 - 1;
         r_cmd(16#1A#, 0, 0, v); inits := inits + 1;
         if v >= 0 then r_wait(16#100000#, v); end if;
         exit when s6 = 0;
      end loop;
      if refused = 1 then verdict("REFUSED"); end if;
      if v0 >= 0 and v0 / 32 /= 0 then
         r_cmd(16#09#, 0, 0, v0);
         r_wait(16#100000#, v0);
      end if;
      r_cmd(16#0E#, 1, s_mode, v0);
      r_cmd(16#0D#, 2, s_filt, v0);
      r_cmd(16#0C#, 0, 0, v0);
      tick(RESTORE);
      wr(0, 0); wr(3, 0);
      irq_clear <= not irq_clear; tick(2);
      -- cd_resume
      if ROUTINE = 0 then
         if (s_stat / 32) mod 2 = 1 then
            r_msf(s_lba + 1, p); r_cmd(16#02#, 3, p, v0); r_cmd(16#06#, 0, 0, v0);
         elsif s_stat / 128 = 1 then
            r_msf(s_lba, p); r_cmd(16#02#, 3, p, v0); r_cmd(16#03#, 0, 0, v0);
         end if;
      else
         -- drive-state routine step: no +150 (FAULT 3 puts it back); reading -> Setloc(LBA + 1) + ReadN
         -- (FAULT 4: LBA); playing -> Setloc(LBA) + Play; idle with motor on and LBA /= 0 -> Setloc(LBA) +
         -- SeekL, its second response taken (FAULT 1: no seek; FAULT 2: the second response left)
         if (s_stat / 32) mod 2 = 1 then
            if FAULT = 4 then r_msf(s_lba, p); else r_msf(s_lba + 1, p); end if;
            r_cmd(16#02#, 3, p, v0); r_cmd(16#06#, 0, 0, v0);
         elsif s_stat / 128 = 1 then
            r_msf(s_lba, p); r_cmd(16#02#, 3, p, v0); r_cmd(16#03#, 0, 0, v0);
         elsif (s_stat / 2) mod 2 = 1 and s_lba /= 0 and FAULT /= 1 then
            r_msf(s_lba, p); r_cmd(16#02#, 3, p, v0); r_cmd(16#15#, 0, 0, v0);
            if FAULT /= 2 then r_wait(16#100000#, v0); end if;
         end if;
      end if;
      wr(0, 1); wr(2, s_irqena); wr(0, 0);
      tick(100000);
      host_stall <= '0';
      end if;

      -- ------------------------------------------------------------------
      -- the restored game: (ReadN of its own), then INT1 for want, +1, +2 and nothing else
      -- ------------------------------------------------------------------
      first_lba := -1;
      if after_read = 1 then
         g_cmd(16#06#);
         d := 0;
         while irq_latch = '0' and d < 4000000 loop tick(16); d := d + 16; end loop;
         irq_clear <= not irq_clear; tick(2);
         wr(0, 1); rd(3, flags);
         if flags mod 8 /= 3 then strays := strays + 1; last_stray := flags mod 8; end if;
         for i in 0 to 7 loop rd(1, d); end loop;
         wr(0, 1); wr(3, 31); wr(0, 0);
         if CASE_N = 6 then want := -3; end if;   -- after Pause: the drive's own position (the runner compares with NOSAVE)
      end if;
      while n1 < 3 loop
         d := 0;
         while irq_latch = '0' and d < 45000000 loop tick(16); d := d + 16; end loop;
         exit when irq_latch = '0';
         irq_clear <= not irq_clear; tick(2);
         wr(0, 1); rd(3, flags); kind := flags mod 8;
         if kind = 1 then
            n1 := n1 + 1;
            if n1 = 1 then first_lba := tap_lba; end if;
         else
            strays := strays + 1; last_stray := kind;
         end if;
         for i in 0 to 7 loop rd(1, d); end loop;
         wr(0, 1); wr(3, 31); wr(0, 0);
         tick(64);
      end loop;
      if n1 < 3 then verdict("STUCK");
      elsif want /= -3 and first_lba /= want then verdict("WRONGLBA");
      elsif strays /= 0 then verdict("STRAY");
      elsif wrong /= 0 then verdict("WRONGBEFORE");
      else verdict("OK"); end if;
   end process;
end architecture;
