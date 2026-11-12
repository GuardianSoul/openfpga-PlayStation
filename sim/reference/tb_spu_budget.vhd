-- SPDX-License-Identifier: GPL-2.0-only
-- SPU per-sample time against the 768-clk1x budget (scripts/run_spu_budget.py).
-- One SPU (the project's spu.vhd with the audio probe's dbg_stash/dbg_busy)
-- with all 24 voices keyed on at pitch PITCH, reverb and CD audio on; SPU RAM
-- answers each request LAT clk1x later, plus up to JIT more (random). Reports
-- the longest busy run, the stash maximum and the dropped ticks.
--
-- The cache model (CACHE=1) follows rtl/pocket/psx_spu_ch3_cache_slim.sv:
-- - The line tag is the 16-byte ADPCM block, adr(18 downto 4), as in the cache.
--   Until 2026-11-11 the model compared adr(18 downto 5). With the address-hashed
--   sets that is the same thing (the set index holds address bit 4), but with
--   voice-numbered sets two neighbouring blocks shared a line, so a voice moving
--   into the second block of a pair hit on the first block's words: 86 % hits at
--   pitch 16#3FFF# where the cache gives 71 % (docs/spu-voice-sets.md). TAG32=1
--   keeps the old compare, only so that the earlier figures can be reproduced.
-- - VIDX=1 is VOICE_SETS=1 (every write clears the cache), VIDX=2 is
--   VOICE_SETS=2 (the owner table: a write clears the sets its 1 KiB region
--   lists and set 31 when it holds the written block). POST_WRITE=1 is the
--   cache's POST_WRITE: the SPU gets a write's answer as fast as a hit's, ch3
--   goes on with the write, and the next request is taken when ch3 has
--   answered (post_wait: how long those next requests waited).
-- - stale counts reads answered from a line whose word was written after the
--   line took it. It is kept apart from the model's own clears and must be 0;
--   a fault planted in the write clear shows here (run_spu_budget.py --fault).
-- Transfer writes (WR_WORDS > 0; none by default, and then the stimulus is the
-- one this bench always had) go in through the DMA write port as dma.vhd sends
-- an SPU upload; spu.vhd moves one FIFO halfword to RAM per voice step. The RAM
-- model ignores what is written: the bench measures time, the data never change.
-- The win_* figures count from sample WR_START, with or without writes.
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
use std.env.all;

entity tb_spu_budget is
    generic (SEED : natural := 1; OPERATIONS : natural := 4000;   -- OPERATIONS: samples to run
             LAT : natural := 3; JIT : natural := 0; PITCH : natural := 16#3FFF#;
             CACHE : natural := 0;          -- 1: model --spu-cache-slim (32 direct-mapped lines of 4 words, hit 2 clk1x, miss 2 + LAT + JIT)
             STRIDE : natural := 16#80#;    -- voice start spacing in 8-byte units (16#80# = 1 KB; with the 32-set hash every voice is in its own set)
             RANDOM : natural := 0;         -- 1: pseudo-random voice start addresses (seeded) instead of STRIDE
             SET_BITS : natural := 5;       -- cache sets 2^SET_BITS (5 = the build)
             WAYS : natural := 1;           -- 2: two-way sets, LRU
             HDR : natural := 0;            -- 1: per-voice header store (24 entries) in front of the cache
             VIDX : natural := 0;           -- 1: voice reads use the voice number as the cache set (writes clear the cache); 2: the same sets with the owner table
             TAG32 : natural := 0;          -- 1: the line tag is adr(18 downto 5), as this model had it until 2026-11-11 (see above)
             VBASE : natural := 16#0100#;   -- the first voice's start in 8-byte units (STRIDE layout). Addresses below 16#0200# (4 KiB) are
                                            -- spu_ram's capture RAM, not SDRAM: with the default the first voices start there
             RVB_BASE : natural := 16#E000#;-- reverb work area start; from 16#E000# (0x70000) it is spu_ram's block RAM, below that SDRAM
             WLAT : integer := -1;          -- clk1x a write waits at ch3 (the cache adds 2, as to a read miss); -1: as a read, LAT + JIT
             WR_WORDS : natural := 0;       -- transfer halfwords queued per event (0: no transfer writes)
             WR_PERIOD : natural := 1;      -- samples between events
             WR_START : natural := 60;      -- sample of the first event, and the first sample of the win_* figures
             WR_COUNT : natural := 0;       -- events (0: until the end)
             WR_BLOCK : natural := 32;      -- halfwords per DMA block (libspu: 16 words)
             WR_HOLD : natural := 32;       -- clk1x the SPU's DMA request is held before a block starts (dma.vhd spuReqHeld)
             WR_ADDR : natural := 16#2000#; -- transfer address register (8-byte units)
             WR_VOICE : integer := -1;      -- 0-23: the transfer starts at that voice's start block instead (a stream written around its voice)
             OWN_START : natural := 0;      -- VIDX=2, owners[] at the start: 0 empty (as after configuration); 1 every bit set;
                                            -- 2 voice v's bit set in the AREA_K KiB from its start (it has played all of its sample)
             AREA_K : natural := 16;
             POST_WRITE : natural := 0);    -- 1 (with VIDX=2): posted writes
end entity;
architecture test of tb_spu_budget is
    signal clk1x,clk2x : std_logic := '1';
    signal reset : std_logic := '1';
    signal toggle,sampled,clk2xIndex : std_logic := '0';
    signal bus_addr : unsigned(9 downto 0) := (others=>'0');
    signal bus_dataWrite : std_logic_vector(15 downto 0) := (others=>'0');
    signal bus_read,bus_write : std_logic := '0';
    signal sdram_dataRead : std_logic_vector(31 downto 0) := (others=>'0');
    signal sdram_done, ena, rnw, tick, timeout, busy : std_logic := '0';
    signal adr : std_logic_vector(18 downto 0);
    signal stash : unsigned(3 downto 0);
    signal samples, drops, max_busy, max_stash, run, requests, hits : natural := 0;
    signal done : boolean := false;
    signal out_l, out_r : std_logic_vector(15 downto 0);
    signal out_v : std_logic;
    signal out_hash : unsigned(31 downto 0) := (others => '0');
    signal out_count : natural := 0;
    signal dma_write, dmareq : std_logic := '0';
    signal dma_writedata : std_logic_vector(15 downto 0) := (others => '0');
    signal wreqs, first_drop, stale, cleared : natural := 0;
    signal win_reads, win_hits, win_wreqs, win_runs, win_busy, win_max, win_drops : natural := 0;   -- from sample WR_START
    signal post_n, post_wait, post_wait_max : natural := 0;   -- posted writes from sample WR_START; the waits of the requests after them
    function vstart(v : natural) return natural is   -- a voice's start address register (8-byte units)
        variable r : unsigned(31 downto 0);
    begin
        if RANDOM = 1 then
            r := to_unsigned(SEED * 2654 + 12345, 32);
            for i in 0 to v loop r := r xor shift_left(r,13); r := r xor shift_right(r,17); r := r xor shift_left(r,5); end loop;
            return 16#0100# + 2 * (to_integer(r(15 downto 0)) mod 16#7000#);   -- 16-byte aligned blocks anywhere
        end if;
        return VBASE + STRIDE * v;
    end;
begin
    clk1x <= not clk1x after 14762 ps;
    clk2x <= not clk2x after 7381 ps;
    process(clk1x) begin if rising_edge(clk1x) then toggle<=not toggle; end if; end process;
    process(clk2x) begin
        if rising_edge(clk2x) then
            sampled<=toggle; clk2xIndex<='0';
            if sampled=toggle then clk2xIndex<='1'; end if;
        end if;
    end process;
    dut : entity work.spu port map (clk1x=>clk1x, clk2x=>clk2x, clk2xIndex=>clk2xIndex, ce=>'1', reset=>reset,
        SPUon=>'1', SPUIRQTrigger=>'0', useSDRAM=>'1', REPRODUCIBLESPUIRQ=>'1', REPRODUCIBLESPUDMA=>'0', REVERBOFF=>'0',
        cpuPaused=>'0', spu_tick=>tick, cd_left=>x"1234", cd_right=>x"F00D", irqOut=>open, sound_timeout=>timeout,
        dbg_stash=>stash, dbg_busy=>busy,
        sound_out_left=>out_l, sound_out_right=>out_r, sound_out_valid=>out_v, bus_addr=>bus_addr, bus_dataWrite=>bus_dataWrite, bus_read=>bus_read,
        bus_write=>bus_write, bus_dataRead=>open, spu_dmaRequest=>dmareq, dma_read=>'0', dma_readdata=>open,
        dma_write=>dma_write, dma_writedata=>dma_writedata, sdram_dataWrite=>open, sdram_Adr=>adr, sdram_be=>open,
        sdram_rnw=>rnw, sdram_ena=>ena, sdram_dataRead=>sdram_dataRead, sdram_done=>sdram_done,
        mem_request=>open, mem_BURSTCNT=>open, mem_ADDR=>open, mem_DIN=>open, mem_BE=>open, mem_WE=>open, mem_RD=>open,
        mem_ack=>'0', mem_DOUT=>(others=>'0'), mem_DOUT_READY=>'0', SS_reset=>reset, loading_savestate=>'0',
        SS_DataWrite=>(others=>'0'), SS_Adr=>(others=>'0'), SS_wren=>'0', SS_rden=>'0', SS_DataRead=>open, SS_idle=>open,
        SS_RAM_dataWrite=>(others=>'0'), SS_RAM_Adr=>(others=>'0'), SS_RAM_request=>'0', SS_RAM_rnw=>'0',
        SS_RAM_dataRead=>open, SS_RAM_done=>open);
    -- SPU RAM: LAT (+ random 0..JIT) clk1x per request; ADPCM headers 0 (no end flag), other data noise
    process(clk1x)
        type ttags is array (0 to 255) of unsigned(14 downto 0);
        type tpres is array (0 to 255) of std_logic_vector(3 downto 0);
        variable tags, tags2 : ttags := (others => (others => '1'));
        variable pres, pres2 : tpres := (others => "0000");
        variable lru : std_logic_vector(0 to 255) := (others => '0');   -- 1: way 2 used last
        type thdr is array (0 to 23) of unsigned(14 downto 0);
        variable htag : thdr := (others => (others => '1'));
        variable hval : std_logic_vector(0 to 23) := (others => '0');
        variable v : natural;
        variable hdr_hit, hit_now : boolean;
        alias voice_index is << signal .tb_spu_budget.dut.index : integer range 0 to 23 >>;
        alias voice_req is << signal .tb_spu_budget.dut.ram_isVoice : std_logic >>;
        variable set, wd : natural;
        variable tg : unsigned(14 downto 0);
        variable delay : natural := 0;
        variable st : unsigned(31 downto 0) := to_unsigned(SEED * 7919 + 1, 32);
        variable h : unsigned(31 downto 0);
        -- VIDX=2: owners[], one entry per 1 KiB of SPU RAM (adr 18:10), one bit per set number modulo 16
        type town is array (0 to 511) of std_logic_vector(15 downto 0);
        impure function own_table return town is
            variable r : town := (others => (others => '0'));
            variable a : natural;
        begin
            if OWN_START = 1 then
                r := (others => (others => '1'));
            elsif OWN_START = 2 then
                for i in 0 to 23 loop
                    a := vstart(i) / 128;   -- the 1 KiB region of the voice's start block
                    for k in 0 to AREA_K - 1 loop
                        if a + k <= 511 then r(a + k)(i mod 16) := '1'; end if;
                    end loop;
                end loop;
            end if;
            return r;
        end;
        variable owners : town := own_table;
        variable bucket, nclr : natural;
        -- stale reads: the request number of the last write to each 32-bit word of SPU RAM, and of each line word's fill
        type tseq is array (0 to 131071) of natural;
        type tfill is array (0 to 255, 0 to 3) of natural;
        variable wrseq : tseq := (others => 0);
        variable fill1, fill2 : tfill := (others => (others => 0));
        variable seq, widx : natural := 0;
        -- POST_WRITE: clk1x until ch3 has answered the posted write and the cache takes a request again
        variable post_left, wraw, pwait : natural := 0;
        variable post_open : boolean := false;   -- no request has come since the last posted write
    begin
        if rising_edge(clk1x) then
            sdram_done<='0';
            if post_left > 0 then post_left := post_left - 1; end if;
            if ena='1' then
                st := st xor shift_left(st,13); st := st xor shift_right(st,17); st := st xor shift_left(st,5);
                delay := LAT; if JIT > 0 then delay := LAT + to_integer(st(15 downto 0)) mod (JIT + 1); end if;
                if rnw = '0' and WLAT >= 0 then delay := WLAT; end if;
                if rnw = '0' then wreqs <= wreqs + 1; end if;
                wraw := delay;
                seq := seq + 1; widx := to_integer(unsigned(adr(18 downto 2)));
                hdr_hit := false; hit_now := false;
                if CACHE = 1 and HDR = 1 and rnw = '1' and adr(3 downto 0) = "0000" then
                    v := voice_index;
                    if hval(v) = '1' and htag(v) = unsigned(adr(18 downto 4)) then hdr_hit := true; hit_now := true; delay := 2; hits <= hits + 1;
                    else htag(v) := unsigned(adr(18 downto 4)); hval(v) := '1'; end if;
                end if;
                if CACHE = 1 and HDR = 1 and rnw = '0' then hval := (others => '0'); end if;   -- a write clears the header store
                if CACHE = 1 and not hdr_hit then
                    set := to_integer(unsigned(adr(SET_BITS + 3 downto 4)) xor unsigned(adr(2*SET_BITS + 3 downto SET_BITS + 4))
                                      xor resize(shift_right(unsigned(adr(18 downto 4)), 2*SET_BITS), SET_BITS));   -- the slim cache's set hash
                    wd := to_integer(unsigned(adr(3 downto 2)));
                    if TAG32 = 1 then tg := '0' & unsigned(adr(18 downto 5)); else tg := unsigned(adr(18 downto 4)); end if;
                    if VIDX >= 1 then
                        if voice_req = '1' then set := voice_index; else set := 31; end if;   -- non-voice reads: set 31 (unused by voices)
                    end if;
                    if rnw = '0' then
                        nclr := 0;
                        if VIDX = 1 then   -- a write clears every line
                            for s in 0 to 31 loop
                                if pres(s) /= "0000" then nclr := nclr + 1; end if;
                            end loop;
                            pres := (others => "0000");
                        elsif VIDX = 2 then   -- a write clears the sets its region lists, then the entry
                            bucket := to_integer(unsigned(adr(18 downto 10)));
                            for s in 0 to 30 loop
                                if owners(bucket)(s mod 16) = '1' then
                                    if pres(s) /= "0000" then nclr := nclr + 1; end if;
                                    pres(s) := "0000";   -- owners: a listed set loses its line
                                end if;
                            end loop;
                            if tags(31) = tg then   -- set 31 is not in the table: cleared when it holds the written block
                                if pres(31) /= "0000" then nclr := nclr + 1; end if;
                                pres(31) := "0000";
                            end if;
                            owners(bucket) := (others => '0');
                        else   -- hash sets: the cache updates a word it holds
                            if tags(set) = tg and pres(set)(wd) = '1' then fill1(set, wd) := seq; end if;
                            if WAYS = 2 and tags2(set) = tg and pres2(set)(wd) = '1' then fill2(set, wd) := seq; end if;
                        end if;
                        if samples >= WR_START then cleared <= cleared + nclr; end if;
                        wrseq(widx) := seq;
                    end if;
                    if rnw = '1' and tags(set) = tg and pres(set)(wd) = '1' then
                        delay := 2; hits <= hits + 1; lru(set) := '0'; hit_now := true;
                        if wrseq(widx) > fill1(set, wd) then stale <= stale + 1; end if;
                    elsif WAYS = 2 and rnw = '1' and tags2(set) = tg and pres2(set)(wd) = '1' then
                        delay := 2; hits <= hits + 1; lru(set) := '1'; hit_now := true;
                        if wrseq(widx) > fill2(set, wd) then stale <= stale + 1; end if;
                    else
                        delay := delay + 2;
                        if rnw = '1' then
                            if tags(set) = tg then pres(set)(wd) := '1'; lru(set) := '0'; fill1(set, wd) := seq;
                            elsif WAYS = 2 and tags2(set) = tg then pres2(set)(wd) := '1'; lru(set) := '1'; fill2(set, wd) := seq;
                            elsif WAYS = 2 and lru(set) = '0' then tags2(set) := tg; pres2(set) := "0000"; pres2(set)(wd) := '1'; lru(set) := '1'; fill2(set, wd) := seq;
                            else tags(set) := tg; pres(set) := "0000"; pres(set)(wd) := '1'; lru(set) := '0'; fill1(set, wd) := seq;
                            end if;
                            if VIDX = 2 and set /= 31 then
                                owners(to_integer(unsigned(adr(18 downto 10))))(set mod 16) := '1';   -- owners: a fill lists its set
                            end if;
                        end if;
                    end if;
                    if POST_WRITE = 1 and VIDX = 2 then
                        -- A held write answers when ch3 does, wraw + 2 clk1x after it is taken; the SPU can have its next
                        -- request taken two clk1x after that. A posted write answers as a hit does, and a request that
                        -- comes while ch3 is still writing is taken wraw + 3 clk1x after the write was: one clk1x
                        -- sooner than the held write allows, more when the SPU has other work first.
                        pwait := post_left;
                        if post_open and samples >= WR_START then
                            post_wait <= post_wait + pwait;
                            if pwait > post_wait_max then post_wait_max <= pwait; end if;
                        end if;
                        post_open := false;
                        if rnw = '0' then
                            delay := 2; post_left := pwait + wraw + 3; post_open := true;
                            if samples >= WR_START then post_n <= post_n + 1; end if;
                        end if;
                        delay := delay + pwait;
                    end if;
                end if;
                requests <= requests + 1;
                if samples >= WR_START then
                    if rnw = '0' then win_wreqs <= win_wreqs + 1;
                    else
                        win_reads <= win_reads + 1;
                        if hit_now then win_hits <= win_hits + 1; end if;
                    end if;
                end if;
            elsif delay>0 then
                delay:=delay-1;
                if delay=0 then
                    h:=resize(resize(unsigned(adr),32)*to_unsigned(40503,16),32) xor x"9E3779B9";
                    if adr(2 downto 0) = "000" then h(15 downto 0) := x"0000"; end if;
                    sdram_dataRead<=std_logic_vector(h); sdram_done<='1';
                end if;
            end if;
        end if;
    end process;
    process(clk1x) begin
        if rising_edge(clk1x) and reset='0' then
            if tick='1' then samples<=samples+1; end if;
            -- every output sample, in order: a run is bit-identical to another when hash and count match
            if out_v='1' and out_count < OPERATIONS - 50 then
                out_hash <= resize(out_hash * 31, 32) xor unsigned(out_l & out_r); out_count <= out_count + 1;
            end if;
            if timeout='1' then
                drops<=drops+1;
                if drops = 0 then first_drop <= samples; end if;
                if samples >= WR_START then win_drops <= win_drops + 1; end if;
            end if;
            if to_integer(stash)>max_stash then max_stash<=to_integer(stash); end if;
            if busy='1' then run<=run+1; if run+1>max_busy then max_busy<=run+1; end if;
            else
                -- one busy run is one processed sample (the state passes IDLE between samples, also while catching up)
                if run > 0 and samples >= WR_START then
                    win_runs <= win_runs + 1; win_busy <= win_busy + run;
                    if run > win_max then win_max <= run; end if;
                end if;
                run<=0;
            end if;
        end if;
    end process;
    -- Transfer writes as dma.vhd sends them: an event queues WR_WORDS halfwords; while any are queued and the SPU has
    -- asked (spu_dmaRequest: transfer mode 2 and FifoIn empty) for WR_HOLD clk1x, one block of up to WR_BLOCK halfwords
    -- goes in, two halfwords per four clk1x.
    process(clk1x)
        variable queued, hold, left, phase, events, sent : natural := 0;
    begin
        if rising_edge(clk1x) then
            dma_write <= '0';
            if reset = '0' and WR_WORDS > 0 then
                if tick = '1' and samples >= WR_START and ((samples - WR_START) mod WR_PERIOD) = 0 and (WR_COUNT = 0 or events < WR_COUNT) then
                    queued := queued + WR_WORDS; events := events + 1;
                end if;
                if left > 0 then
                    if phase < 2 then
                        dma_write <= '1'; dma_writedata <= std_logic_vector(to_unsigned((sent * 40503) mod 65536, 16));
                        left := left - 1; sent := sent + 1;
                    end if;
                    phase := (phase + 1) mod 4;
                elsif queued > 0 and dmareq = '1' then
                    if hold < WR_HOLD then hold := hold + 1;
                    else
                        left := WR_BLOCK; if queued < left then left := queued; end if;
                        queued := queued - left; phase := 0; hold := 0;
                    end if;
                else
                    hold := 0;
                end if;
            end if;
        end if;
    end process;
    process
        procedure t is begin wait until rising_edge(clk1x); wait for 1 ns; end;
        procedure w(a : natural; d : natural) is
        begin bus_addr<=to_unsigned(a,10); bus_dataWrite<=std_logic_vector(to_unsigned(d,16)); bus_write<='1'; t; bus_write<='0'; t; t; end;
    begin
        for i in 1 to 20 loop t; end loop;
        reset<='0';
        for i in 1 to 20 loop t; end loop;
        if WR_WORDS > 0 then
            if WR_VOICE >= 0 then w(16#1A6#, vstart(WR_VOICE)); else w(16#1A6#, WR_ADDR); end if;   -- transfer address
            w(16#1AA#, 16#C0E5#);          -- as below, plus transfer mode 2 (DMA write): spu_dmaRequest = FifoIn empty
        else
            w(16#1AA#, 16#C0C5#);          -- SPU on, unmute, reverb master, CD audio + CD reverb
        end if;
        w(16#180#, 16#3FFF#); w(16#182#, 16#3FFF#); w(16#184#, 16#3FFF#); w(16#186#, 16#3FFF#);
        w(16#1B0#, 16#7FFF#); w(16#1B2#, 16#7FFF#); w(16#1A2#, RVB_BASE);   -- CD volume, reverb work area
        for k in 0 to 31 loop w(16#1C0# + 2*k, 16#0400# + 37*k); end loop;  -- reverb registers
        for v in 0 to 23 loop
            w(16*v + 0, 16#3FFF#); w(16*v + 2, 16#3FFF#); w(16*v + 4, PITCH);
            w(16*v + 6, vstart(v)); w(16*v + 8, 16#00FF#); w(16*v + 10, 16#0000#);   -- start, ADSR (fast attack, sustain max)
            w(16*v + 14, vstart(v));
        end loop;
        w(16#198#, 16#FFFF#); w(16#19A#, 16#00FF#);   -- every voice into reverb
        w(16#188#, 16#FFFF#); w(16#18A#, 16#00FF#);   -- key on all 24
        wait until samples >= OPERATIONS;
        done<=true;
        report "BUDGET LAT=" & natural'image(LAT) & " JIT=" & natural'image(JIT) & " PITCH=" & natural'image(PITCH) &
            " CACHE=" & natural'image(CACHE) &  " STRIDE=" & natural'image(STRIDE) & " RANDOM=" & natural'image(RANDOM) & " SETS=" & natural'image(2**SET_BITS) & " WAYS=" & natural'image(WAYS) & " HDR=" & natural'image(HDR) & " VIDX=" & natural'image(VIDX) &
            " samples=" & natural'image(samples) & " requests=" & natural'image(requests) & " hits=" & natural'image(hits) &
            " out_samples=" & natural'image(out_count) & " out_hash=" & to_hstring(out_hash) &
            " max_busy=" & natural'image(max_busy) & " max_stash=" & natural'image(max_stash) & " drops=" & natural'image(drops) &
            " TAG32=" & natural'image(TAG32) & " WLAT=" & integer'image(WLAT) & " WR_WORDS=" & natural'image(WR_WORDS) &
            " WR_PERIOD=" & natural'image(WR_PERIOD) & " OWN_START=" & natural'image(OWN_START) & " wreqs=" & natural'image(wreqs) &
            " first_drop=" & natural'image(first_drop) & " win_from=" & natural'image(WR_START) &
            " win_reads=" & natural'image(win_reads) & " win_hits=" & natural'image(win_hits) & " win_wreqs=" & natural'image(win_wreqs) &
            " win_runs=" & natural'image(win_runs) & " win_busy=" & natural'image(win_busy) & " win_max=" & natural'image(win_max) &
            " win_drops=" & natural'image(win_drops) & " cleared=" & natural'image(cleared) & " stale=" & natural'image(stale) &
            " POST=" & natural'image(POST_WRITE) & " post_n=" & natural'image(post_n) & " post_wait=" & natural'image(post_wait) &
            " post_wait_max=" & natural'image(post_wait_max);
        finish;
    end process;
end architecture;
