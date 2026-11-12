-- SPDX-License-Identifier: GPL-2.0-only
-- Lockstep of two compilations of the selected SPU (library ref = rc1i
-- original, library cand = patched), each with its own identical SDRAM model
-- (deterministic per-request latency). Every SPU output is compared after
-- every clk2x edge; noiseCnt/noiseLevel/adsrVolumeNew are also compared
-- through external names. Stimulus (seeded): ADPCM sample blocks in memory,
-- 24 voices with random pitch/volume/ADSR/loop/noise/pitch-mod, key on/off,
-- random CNT writes (noise shift/step, reverb, mute, IRQ), a reverb preset,
-- CD input, bus reads, ce gaps, IRQ trigger, resets, savestate-port writes of
-- noiseCnt/CNT, and directed CNT writes landing in CAPTURE2 (one clock before
-- the noise update) to test the one-ce lag of the noise threshold.
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
use ieee.math_real.all;
library ref, cand;

entity tb_spu_base_trim is
   generic (SEED : integer := 1; CYCLES : integer := 2000000; DIRECTED : integer := 400);
end entity;

architecture sim of tb_spu_base_trim is
   signal clk1x, clk2x : std_logic := '1';
   signal clk2xIndex   : std_logic := '0';
   signal toggle, sampled : std_logic := '0';
   signal ce, reset, SPUon, SPUIRQTrigger, REPRODUCIBLESPUIRQ, REVERBOFF : std_logic := '0';
   signal cd_left, cd_right : signed(15 downto 0) := (others => '0');
   signal bus_addr : unsigned(9 downto 0) := (others => '0');
   signal bus_dataWrite : std_logic_vector(15 downto 0) := (others => '0');
   signal bus_read, bus_write, dma_read, dma_write : std_logic := '0';
   signal dma_writedata : std_logic_vector(15 downto 0) := (others => '0');
   signal SS_reset, loading_savestate, SS_wren : std_logic := '0';
   signal SS_DataWrite : std_logic_vector(31 downto 0) := (others => '0');
   signal SS_Adr : unsigned(8 downto 0) := (others => '0');
   signal done_flag : boolean := false;

   type t_out is record
      spu_tick, irqOut, sound_timeout, sound_out_valid, spu_dmaRequest : std_logic;
      sound_out_left, sound_out_right, bus_dataRead, dma_readdata : std_logic_vector(15 downto 0);
      sdram_dataWrite : std_logic_vector(31 downto 0);
      sdram_Adr : std_logic_vector(18 downto 0);
      sdram_be : std_logic_vector(3 downto 0);
      sdram_rnw, sdram_ena : std_logic;
      mem_request, mem_WE, mem_RD : std_logic;
      mem_BURSTCNT, mem_BE : std_logic_vector(7 downto 0);
      mem_ADDR : std_logic_vector(19 downto 0);
      mem_DIN : std_logic_vector(63 downto 0);
      SS_DataRead : std_logic_vector(31 downto 0);
      SS_idle, SS_RAM_done : std_logic;
      SS_RAM_dataRead : std_logic_vector(15 downto 0);
   end record;
   type t_outs is array(0 to 1) of t_out;
   signal o : t_outs;
   type t_slv32 is array(0 to 1) of std_logic_vector(31 downto 0);
   signal sdram_dataRead : t_slv32 := (others => (others => '0'));
   type t_sl is array(0 to 1) of std_logic;
   signal sdram_done : t_sl;
   signal mem_busy : t_sl := (others => '0');

   signal compares, noise_fires, directed_hits, ce_gaps, resets_done, key_ons, adsr_writes, cnt_writes, ss_writes : natural := 0;
   signal noise_shift_seen : std_logic_vector(15 downto 0) := (others => '0');
   signal internal_mismatches : natural := 0;
   signal spu_ticks, sdram_reqs, sound_valids : natural := 0;
begin
   clk2x <= not clk2x after 5 ns when not done_flag;
   clk1x <= not clk1x after 10 ns when not done_flag;
   process (clk1x) begin if rising_edge(clk1x) then toggle <= not toggle; end if; end process;
   process (clk2x) begin if rising_edge(clk2x) then sampled <= toggle; if sampled = toggle then clk2xIndex <= '1'; else clk2xIndex <= '0'; end if; end if; end process;

   gdut : for i in 0 to 1 generate
      signal mem_ack, mem_DOUT_READY : std_logic := '0';
      signal ret : std_logic := '0';
   begin
      -- like psx_spu_sdram_adapter: done only while core ce is high
      sdram_done(i) <= ret and ce;
      gref : if i = 0 generate
         dut_ref : entity ref.spu port map (
            clk1x => clk1x, clk2x => clk2x, clk2xIndex => clk2xIndex, ce => ce, reset => reset,
            SPUon => SPUon, SPUIRQTrigger => SPUIRQTrigger, useSDRAM => '1',
            REPRODUCIBLESPUIRQ => REPRODUCIBLESPUIRQ, REPRODUCIBLESPUDMA => '0', REVERBOFF => REVERBOFF,
            cpuPaused => '0', spu_tick => o(i).spu_tick, cd_left => cd_left, cd_right => cd_right,
            irqOut => o(i).irqOut, sound_timeout => o(i).sound_timeout,
            sound_out_left => o(i).sound_out_left, sound_out_right => o(i).sound_out_right, sound_out_valid => o(i).sound_out_valid,
            bus_addr => bus_addr, bus_dataWrite => bus_dataWrite, bus_read => bus_read, bus_write => bus_write, bus_dataRead => o(i).bus_dataRead,
            spu_dmaRequest => o(i).spu_dmaRequest, dma_read => dma_read, dma_readdata => o(i).dma_readdata,
            dma_write => dma_write, dma_writedata => dma_writedata,
            sdram_dataWrite => o(i).sdram_dataWrite, sdram_Adr => o(i).sdram_Adr, sdram_be => o(i).sdram_be,
            sdram_rnw => o(i).sdram_rnw, sdram_ena => o(i).sdram_ena, sdram_dataRead => sdram_dataRead(i), sdram_done => sdram_done(i),
            mem_request => o(i).mem_request, mem_BURSTCNT => o(i).mem_BURSTCNT, mem_ADDR => o(i).mem_ADDR, mem_DIN => o(i).mem_DIN,
            mem_BE => o(i).mem_BE, mem_WE => o(i).mem_WE, mem_RD => o(i).mem_RD, mem_ack => mem_ack,
            mem_DOUT => (others => '0'), mem_DOUT_READY => mem_DOUT_READY,
            SS_reset => SS_reset, loading_savestate => loading_savestate, SS_DataWrite => SS_DataWrite, SS_Adr => SS_Adr,
            SS_wren => SS_wren, SS_rden => '0', SS_DataRead => o(i).SS_DataRead, SS_idle => o(i).SS_idle,
            SS_RAM_dataWrite => (others => '0'), SS_RAM_Adr => (others => '0'), SS_RAM_request => '0', SS_RAM_rnw => '1',
            SS_RAM_dataRead => o(i).SS_RAM_dataRead, SS_RAM_done => o(i).SS_RAM_done);
      end generate;
      gcand : if i = 1 generate
         dut_cand : entity cand.spu port map (
            clk1x => clk1x, clk2x => clk2x, clk2xIndex => clk2xIndex, ce => ce, reset => reset,
            SPUon => SPUon, SPUIRQTrigger => SPUIRQTrigger, useSDRAM => '1',
            REPRODUCIBLESPUIRQ => REPRODUCIBLESPUIRQ, REPRODUCIBLESPUDMA => '0', REVERBOFF => REVERBOFF,
            cpuPaused => '0', spu_tick => o(i).spu_tick, cd_left => cd_left, cd_right => cd_right,
            irqOut => o(i).irqOut, sound_timeout => o(i).sound_timeout,
            sound_out_left => o(i).sound_out_left, sound_out_right => o(i).sound_out_right, sound_out_valid => o(i).sound_out_valid,
            bus_addr => bus_addr, bus_dataWrite => bus_dataWrite, bus_read => bus_read, bus_write => bus_write, bus_dataRead => o(i).bus_dataRead,
            spu_dmaRequest => o(i).spu_dmaRequest, dma_read => dma_read, dma_readdata => o(i).dma_readdata,
            dma_write => dma_write, dma_writedata => dma_writedata,
            sdram_dataWrite => o(i).sdram_dataWrite, sdram_Adr => o(i).sdram_Adr, sdram_be => o(i).sdram_be,
            sdram_rnw => o(i).sdram_rnw, sdram_ena => o(i).sdram_ena, sdram_dataRead => sdram_dataRead(i), sdram_done => sdram_done(i),
            mem_request => o(i).mem_request, mem_BURSTCNT => o(i).mem_BURSTCNT, mem_ADDR => o(i).mem_ADDR, mem_DIN => o(i).mem_DIN,
            mem_BE => o(i).mem_BE, mem_WE => o(i).mem_WE, mem_RD => o(i).mem_RD, mem_ack => mem_ack,
            mem_DOUT => (others => '0'), mem_DOUT_READY => mem_DOUT_READY,
            SS_reset => SS_reset, loading_savestate => loading_savestate, SS_DataWrite => SS_DataWrite, SS_Adr => SS_Adr,
            SS_wren => SS_wren, SS_rden => '0', SS_DataRead => o(i).SS_DataRead, SS_idle => o(i).SS_idle,
            SS_RAM_dataWrite => (others => '0'), SS_RAM_Adr => (others => '0'), SS_RAM_request => '0', SS_RAM_rnw => '1',
            SS_RAM_dataRead => o(i).SS_RAM_dataRead, SS_RAM_done => o(i).SS_RAM_done);
      end generate;

      -- SDRAM model: 512 KiB of 16-bit words, ADPCM blocks preloaded from
      -- the same seed; each request completes after 1..7 clocks drawn from a
      -- per-model sequence (identical requests -> identical stalls).
      memory : process (clk1x)
         type t_words is array(0 to 262143) of natural range 0 to 65535;
         variable words : t_words;
         variable loaded : boolean := false;
         variable s1, s2 : positive;
         variable r : real;
         variable busy : boolean := false;
         variable wait_cnt : natural := 0;
         variable adr : natural;
         variable rnw : std_logic;
         variable wdata : natural;
         variable b, nib_hi, nib_lo, w : natural;
      begin
         if not loaded then
            -- ADPCM: 16-byte blocks, byte0 = filter<<4 | shift, byte1 = flags,
            -- 14 bytes of nibbles; mostly legal shift/filter, some 13..15/5..7.
            s1 := SEED * 7 + 3; s2 := SEED * 13 + 11;
            for blk in 0 to 16383 loop
               uniform(s1, s2, r); b := integer(floor(r * 16.0));
               if b > 12 then uniform(s1, s2, r); if r < 0.7 then b := b - 4; end if; end if;
               uniform(s1, s2, r); nib_hi := integer(floor(r * 8.0));
               if nib_hi > 4 then uniform(s1, s2, r); if r < 0.7 then nib_hi := nib_hi - 3; end if; end if;
               uniform(s1, s2, r);
               -- flags: bit0 loop end, bit1 repeat, bit2 loop start
               if r < 0.06 then w := 3; elsif r < 0.10 then w := 1; elsif r < 0.16 then w := 4; else w := 0; end if;
               words(16#800# + blk * 8) := b + nib_hi * 16 + w * 256;
               for k in 1 to 7 loop
                  uniform(s1, s2, r);
                  if r < 0.5 then -- smooth-ish waveform nibbles
                     uniform(s1, s2, r); nib_lo := integer(floor(r * 6.0)) + 5;
                     words(16#800# + blk * 8 + k) := nib_lo * 4369 mod 65536;
                  else
                     uniform(s1, s2, r); words(16#800# + blk * 8 + k) := integer(floor(r * 65536.0));
                  end if;
               end loop;
            end loop;
            for a in 0 to 16#7FF# loop words(a) := 0; end loop;
            for a in 16#800# + 16384 * 8 to 262143 loop words(a) := 0; end loop;
            s1 := SEED * 3 + 101; s2 := SEED * 5 + 7;
            loaded := true;
         end if;
         if rising_edge(clk1x) then
            if ret = '1' and ce = '1' then ret <= '0'; mem_busy(i) <= '0'; end if;
            if busy then
               if wait_cnt = 0 then
                  busy := false;
                  ret <= '1';
                  if rnw = '1' then
                     sdram_dataRead(i) <= std_logic_vector(to_unsigned(words((adr + 1) mod 262144), 16)) & std_logic_vector(to_unsigned(words(adr), 16));
                  else
                     words(adr) := wdata;
                  end if;
               else
                  wait_cnt := wait_cnt - 1;
               end if;
            end if;
            if o(i).sdram_ena = '1' then
               assert not busy and (ret = '0' or ce = '1') report "overlapping SDRAM request" severity failure;
               busy := true; mem_busy(i) <= '1';
               adr := to_integer(unsigned(o(i).sdram_Adr(18 downto 1)));
               rnw := o(i).sdram_rnw;
               wdata := to_integer(unsigned(o(i).sdram_dataWrite(15 downto 0)));
               uniform(s1, s2, r); wait_cnt := integer(floor(r * 7.0));
            end if;
         end if;
      end process;
   end generate;

   mon : block
   alias ref_noiseCnt   is << signal .tb_spu_base_trim.gdut(0).gref.dut_ref.noiseCnt   : unsigned(31 downto 0) >>;
   alias cand_noiseCnt  is << signal .tb_spu_base_trim.gdut(1).gcand.dut_cand.noiseCnt  : unsigned(31 downto 0) >>;
   alias ref_noiseLvl   is << signal .tb_spu_base_trim.gdut(0).gref.dut_ref.noiseLevel : unsigned(15 downto 0) >>;
   alias cand_noiseLvl  is << signal .tb_spu_base_trim.gdut(1).gcand.dut_cand.noiseLevel: unsigned(15 downto 0) >>;
   alias ref_adsrNew    is << signal .tb_spu_base_trim.gdut(0).gref.dut_ref.adsrVolumeNew  : signed(15 downto 0) >>;
   alias cand_adsrNew   is << signal .tb_spu_base_trim.gdut(1).gcand.dut_cand.adsrVolumeNew : signed(15 downto 0) >>;
   alias ref_CNT        is << signal .tb_spu_base_trim.gdut(0).gref.dut_ref.CNT : std_logic_vector(15 downto 0) >>;
   alias ref_ramReq     is << signal .tb_spu_base_trim.gdut(0).gref.dut_ref.ram_request : std_logic >>;
   alias ref_ramProc    is << signal .tb_spu_base_trim.gdut(0).gref.dut_ref.ispu_ram.ram_processed : std_logic >>;
   alias ref_capPos     is << signal .tb_spu_base_trim.gdut(0).gref.dut_ref.capturePosition : unsigned(9 downto 0) >>;

   begin
   -- Compare every output after every clk2x edge (both clk1x edges included).
   check : process (clk2x)
   begin
      if (o(0) /= o(1)) then
         report "MISMATCH output at " & time'image(now) & " (internal differences before: " & integer'image(internal_mismatches) & ")" severity failure;
      end if;
      if std_logic_vector(ref_noiseCnt) /= std_logic_vector(cand_noiseCnt) or std_logic_vector(ref_noiseLvl) /= std_logic_vector(cand_noiseLvl)
         or std_logic_vector(ref_adsrNew) /= std_logic_vector(cand_adsrNew) then
         if internal_mismatches = 0 then
            report "first internal noise/ADSR difference at " & time'image(now) severity note;
         end if;
         internal_mismatches <= internal_mismatches + 1;
      end if;
      compares <= compares + 1;
   end process;

   stim : process
      variable s1 : positive := SEED * 17 + 5;
      variable s2 : positive := SEED * 31 + 9;
      variable r : real;
      variable cyc : natural := 0;
      variable gap : natural := 0;
      variable v, a : natural;
      variable data : std_logic_vector(15 downto 0);
      variable shift : natural;
      variable last_noise : unsigned(31 downto 0);
      variable phase_directed : boolean := false;
      variable directed_done : natural := 0;
      variable cap : unsigned(9 downto 0);

      impure function rnd(n : natural) return natural is
      begin
         uniform(s1, s2, r);
         return integer(floor(r * real(n)));
      end function;
      impure function rnd16 return std_logic_vector is
      begin
         return std_logic_vector(to_unsigned(rnd(65536), 16));
      end function;

      procedure tick is
      begin
         wait until falling_edge(clk1x);
         cyc := cyc + 1;
         if cyc mod 50000 = 0 then
            report "progress cycles=" & integer'image(cyc) & " directed=" & integer'image(directed_done) &
               " capPos=" & to_hstring(ref_capPos) & " noiseCnt=" & to_hstring(ref_noiseCnt) & " CNT=" & to_hstring(ref_CNT) &
               " ticks=" & integer'image(spu_ticks) & " reqs=" & integer'image(sdram_reqs) & " valids=" & integer'image(sound_valids) severity note;
         end if;
      end procedure;
      procedure wr(addr : natural; d : std_logic_vector(15 downto 0)) is
      begin
         bus_addr <= to_unsigned(addr, 10); bus_dataWrite <= d; bus_write <= '1';
         tick;
         bus_write <= '0';
      end procedure;
      procedure random_cnt is
         variable c : std_logic_vector(15 downto 0);
      begin
         c := rnd16;
         c(15) := '1';                                   -- SPU enabled
         if rnd(8) /= 0 then c(14) := '1'; end if;       -- mostly unmuted
         if rnd(4) /= 0 then c(7) := '1'; end if;        -- mostly reverb master on
         c(5 downto 4) := "00";                          -- no manual transfer
         wr(16#1AA#, c);
         cnt_writes <= cnt_writes + 1;
      end procedure;
      procedure voice_setup(vi : natural) is
         variable base : natural;
      begin
         base := vi * 16;
         wr(base + 0, std_logic_vector(to_unsigned(rnd(16#4000#), 16)));          -- vol L (fixed)
         data := rnd16;
         if rnd(4) = 0 then wr(base + 2, '1' & data(14 downto 0)); else wr(base + 2, std_logic_vector(to_unsigned(rnd(16#4000#), 16))); end if;
         wr(base + 4, std_logic_vector(to_unsigned(rnd(16#4000#) + 16#200#, 16))); -- pitch
         wr(base + 6, std_logic_vector(to_unsigned(16#100# + rnd(16000), 16)));  -- start address (8-byte units)
         wr(base + 8, rnd16);                                                     -- ADSR low
         wr(base + 10, rnd16);                                                    -- ADSR high
         wr(base + 14, std_logic_vector(to_unsigned(16#100# + rnd(16000), 16))); -- repeat
         adsr_writes <= adsr_writes + 2;
      end procedure;
      procedure init_regs is
      begin
         wr(16#1AA#, x"C080");
         wr(16#180#, x"3FFF"); wr(16#182#, x"3FFF");
         wr(16#184#, x"3000"); wr(16#186#, x"3000");
         wr(16#1B0#, x"4000"); wr(16#1B2#, x"4000");
         -- reverb preset ("Room") with random perturbation of the mix volumes
         wr(16#1A2#, std_logic_vector(to_unsigned(16#E000# + rnd(16#1000#), 16)));
         wr(16#1C0#, x"007D"); wr(16#1C2#, x"005B"); wr(16#1C4#, x"6D80"); wr(16#1C6#, x"54B8");
         wr(16#1C8#, x"BED0"); wr(16#1CA#, x"0000"); wr(16#1CC#, x"0000"); wr(16#1CE#, x"BA80");
         wr(16#1D0#, x"5800"); wr(16#1D2#, x"5300"); wr(16#1D4#, x"04D6"); wr(16#1D6#, x"0333");
         wr(16#1D8#, x"03F0"); wr(16#1DA#, x"0227"); wr(16#1DC#, x"0374"); wr(16#1DE#, x"01EF");
         wr(16#1E0#, x"0334"); wr(16#1E2#, x"01B5"); wr(16#1E4#, x"0000"); wr(16#1E6#, x"0000");
         wr(16#1E8#, x"0000"); wr(16#1EA#, x"0000"); wr(16#1EC#, x"0000"); wr(16#1EE#, x"0000");
         wr(16#1F0#, x"0000"); wr(16#1F2#, x"0000"); wr(16#1F4#, x"01B4"); wr(16#1F6#, x"0136");
         wr(16#1F8#, x"00B8"); wr(16#1FA#, x"005C"); wr(16#1FC#, x"8000"); wr(16#1FE#, x"8000");
         for vi in 0 to 23 loop voice_setup(vi); end loop;
         data := rnd16; wr(16#198#, data); data := rnd16; wr(16#19A#, x"00" & data(7 downto 0)); -- reverb on
         data := rnd16; wr(16#194#, data and x"0421"); wr(16#196#, x"0000");    -- a few noise voices
         data := rnd16; wr(16#190#, data and x"5554"); wr(16#192#, x"0000");    -- pitch modulation
         wr(16#188#, x"FFFF"); wr(16#18A#, x"00FF");                              -- key on all
         key_ons <= key_ons + 24;
      end procedure;
      procedure do_reset(ls : std_logic) is
      begin
         reset <= '1'; loading_savestate <= ls;
         for k in 1 to 4 + rnd(8) loop tick; end loop;
         reset <= '0'; loading_savestate <= '0';
         tick;
         resets_done <= resets_done + 1;
      end procedure;
      procedure ss_write(adr : natural; d : std_logic_vector(31 downto 0)) is
      begin
         -- an SS_wren clock skips the ce branch: keep it off pending RAM completions
         ce <= '1'; gap := 0;
         while ref_ramReq = '1' or ref_ramProc = '1' or mem_busy(0) = '1' or mem_busy(1) = '1' loop tick; end loop;
         SS_Adr <= to_unsigned(adr, 9); SS_DataWrite <= d; SS_wren <= '1';
         tick;
         SS_wren <= '0';
         ss_writes <= ss_writes + 1;
      end procedure;
   begin
      REPRODUCIBLESPUIRQ <= '1' when SEED mod 2 = 0 else '0';
      SPUon <= '1'; ce <= '1';
      SS_reset <= '1'; reset <= '1';
      for k in 1 to 20 loop tick; end loop;
      SS_reset <= '0';
      for k in 1 to 220 loop tick; end loop;     -- context RAM clear (192 words)
      reset <= '0';
      tick;
      init_regs;
      while cyc < CYCLES loop
         -- ce gaps
         if gap > 0 then
            gap := gap - 1; ce <= '0';
            if gap = 0 then ce <= '1'; end if;
         elsif rnd(40) = 0 and ref_ramReq = '0' and ref_ramProc = '0' and SS_wren = '0' then
            -- (spu_ram completes capture/reverb-RAM accesses one clock after the
            -- request without regard to ce; the selected system never drops ce
            -- there, so neither does the bench)
            gap := 1 + rnd(9); ce <= '0'; ce_gaps <= ce_gaps + 1;
         end if;
         -- CD input
         if rnd(64) = 0 then cd_left <= signed(rnd16); cd_right <= signed(rnd16); end if;
         SPUIRQTrigger <= '0';
         if rnd(50000) = 0 then SPUIRQTrigger <= '1'; end if;
         case rnd(1500) is
            when 0 to 5 =>   -- key on some voices
               wr(16#188#, rnd16); data := rnd16; wr(16#18A#, x"00" & data(7 downto 0)); key_ons <= key_ons + 1;
            when 6 to 11 =>  -- key off some voices
               wr(16#18C#, rnd16); data := rnd16; wr(16#18E#, x"00" & data(7 downto 0));
            when 12 to 19 => -- ADSR rewrite of one voice (both halves, or one)
               v := rnd(24);
               if rnd(3) /= 0 then wr(v * 16 + 8, rnd16); end if;
               if rnd(3) /= 0 then wr(v * 16 + 10, rnd16); end if;
               adsr_writes <= adsr_writes + 1;
            when 20 to 23 => voice_setup(rnd(24));
            when 24 to 29 => random_cnt;
            when 30 to 31 => wr(16#194#, rnd16); data := rnd16; wr(16#196#, x"00" & data(7 downto 0));
            when 32 to 33 => wr(16#198#, rnd16);
            when 34 to 35 => wr(16#190#, rnd16);
            when 36 to 37 => v := rnd(24); wr(v * 16 + 12, rnd16);             -- ADSR volume
            when 38 to 45 =>  -- bus read of a random register
               a := rnd(512) * 2;
               bus_addr <= to_unsigned(a, 10); bus_read <= '1'; tick; bus_read <= '0';
            when 46 =>        -- savestate-port noiseCnt write near the current threshold
               shift := to_integer(unsigned(ref_CNT(13 downto 10)));
               last_noise := shift_left(to_unsigned(1, 32), 31 - shift) - to_unsigned(rnd(4) * 16#10000# + rnd(65536), 32);
               if rnd(2) = 0 then data := rnd16; last_noise(31 downto 16) := unsigned(data); data := rnd16; last_noise(15 downto 0) := unsigned(data); end if;
               ss_write(6, std_logic_vector(last_noise));
            when 47 =>        -- savestate-port CNT write (outside the ce branch)
               data := rnd16; data(15) := '1'; data(5 downto 4) := "00";
               ss_write(39, data & x"0000");
            when 48 =>
               if rnd(20) = 0 then do_reset('0'); init_regs;
               elsif rnd(20) = 0 then do_reset('1'); init_regs;
               end if;
            when others => tick;
         end case;
      end loop;

      -- Directed: a CNT write that lands in CAPTURE2, one clock before the
      -- noise update in CAPTURE3, alternating the noise shift 15 <-> 0 with
      -- noiseCnt preloaded so the old and new shift decide differently.
      ce <= '1'; gap := 0;
      wr(16#1AA#, x"FC80" or x"0100");         -- shift 15, step 1 (noiseCnt moves in CAPTURE1)
      wr(16#194#, x"FFFF"); wr(16#196#, x"00FF");
      wr(16#188#, x"FFFF"); wr(16#18A#, x"00FF");
      while directed_done < DIRECTED loop
         -- wait for the end of CAPTURE3 (capturePosition advances) ...
         cap := ref_capPos;
         while ref_capPos = cap loop tick; end loop;
         -- ... then for noiseCnt to move at the end of CAPTURE1
         loop
            last_noise := ref_noiseCnt;
            tick;
            exit when ref_noiseCnt /= last_noise;
         end loop;
         -- this cycle is CAPTURE2: CNT changes at its closing edge
         bus_addr <= to_unsigned(16#1AA#, 10); bus_write <= '1';
         if ref_CNT(13 downto 10) = "1111" then bus_dataWrite <= x"C180"; else bus_dataWrite <= x"FD80"; end if;
         tick; bus_write <= '0';
         directed_done := directed_done + 1;
         directed_hits <= directed_hits + 1;
      end loop;
      for k in 1 to 2000 loop tick; end loop;

      if internal_mismatches > 0 then
         report "MISMATCH internal only (outputs equal): noise/ADSR differed on " & integer'image(internal_mismatches) & " compares" severity failure;
      end if;
      report "PASS SPU base-trim lockstep seed=" & integer'image(SEED) & " cycles=" & integer'image(cyc) &
         " compares=" & integer'image(compares) & " ce_gaps=" & integer'image(ce_gaps) &
         " resets=" & integer'image(resets_done) & " key_on_writes=" & integer'image(key_ons) &
         " adsr_writes=" & integer'image(adsr_writes) & " cnt_writes=" & integer'image(cnt_writes) &
         " ss_writes=" & integer'image(ss_writes) & " noise_fires=" & integer'image(noise_fires) &
         " shifts_seen=" & to_hstring(noise_shift_seen) & " directed=" & integer'image(directed_hits) severity note;
      done_flag <= true;
      wait;
   end process;

   -- coverage: noise threshold fires per shift value (from the reference)
   cov : process (clk1x)
      variable prev_lvl : unsigned(15 downto 0) := (others => '0');
   begin
      if rising_edge(clk1x) then
         if ref_noiseLvl /= prev_lvl then
            noise_fires <= noise_fires + 1;
            if not is_x(ref_CNT(13 downto 10)) then
               noise_shift_seen(to_integer(unsigned(ref_CNT(13 downto 10)))) <= '1';
            end if;
         end if;
         prev_lvl := ref_noiseLvl;
         if o(0).spu_tick = '1' then spu_ticks <= spu_ticks + 1; end if;
         if o(0).sdram_ena = '1' then sdram_reqs <= sdram_reqs + 1; end if;
         if o(0).sound_out_valid = '1' then sound_valids <= sound_valids + 1; end if;
      end if;
   end process;
   end block;
end architecture;
