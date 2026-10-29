-- SPDX-License-Identifier: GPL-2.0-only
-- SPU per-sample time against the 768-clk1x budget (scripts/run_spu_budget.py).
-- One SPU (the project's spu.vhd with the audio probe's dbg_stash/dbg_busy)
-- with all 24 voices keyed on at pitch PITCH, reverb and CD audio on; SPU RAM
-- answers each request LAT clk1x later, plus up to JIT more (random). Reports
-- the longest busy run, the stash maximum and the dropped ticks.
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
use std.env.all;

entity tb_spu_budget is
    generic (SEED : natural := 1; OPERATIONS : natural := 4000;   -- OPERATIONS: samples to run
             LAT : natural := 3; JIT : natural := 0; PITCH : natural := 16#3FFF#;
             CACHE : natural := 0;          -- 1: model --spu-cache-slim (32 direct-mapped lines of 4 words, hit 2 clk1x, miss 2 + LAT + JIT)
             STRIDE : natural := 16#80#;    -- voice start spacing in 8-byte units (16#80# = 1 KB: every voice in the same cache set)
             RANDOM : natural := 0;         -- 1: pseudo-random voice start addresses (seeded) instead of STRIDE
             SET_BITS : natural := 5;       -- cache sets 2^SET_BITS (5 = the build)
             WAYS : natural := 1;           -- 2: two-way sets, LRU
             HDR : natural := 0;            -- 1: per-voice header store (24 entries) in front of the cache
             VIDX : natural := 0);          -- 1: voice reads use the voice number as the cache set (writes clear the cache)
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
        bus_write=>bus_write, bus_dataRead=>open, spu_dmaRequest=>open, dma_read=>'0', dma_readdata=>open,
        dma_write=>'0', dma_writedata=>(others=>'0'), sdram_dataWrite=>open, sdram_Adr=>adr, sdram_be=>open,
        sdram_rnw=>rnw, sdram_ena=>ena, sdram_dataRead=>sdram_dataRead, sdram_done=>sdram_done,
        mem_request=>open, mem_BURSTCNT=>open, mem_ADDR=>open, mem_DIN=>open, mem_BE=>open, mem_WE=>open, mem_RD=>open,
        mem_ack=>'0', mem_DOUT=>(others=>'0'), mem_DOUT_READY=>'0', SS_reset=>reset, loading_savestate=>'0',
        SS_DataWrite=>(others=>'0'), SS_Adr=>(others=>'0'), SS_wren=>'0', SS_rden=>'0', SS_DataRead=>open, SS_idle=>open,
        SS_RAM_dataWrite=>(others=>'0'), SS_RAM_Adr=>(others=>'0'), SS_RAM_request=>'0', SS_RAM_rnw=>'0',
        SS_RAM_dataRead=>open, SS_RAM_done=>open);
    -- SPU RAM: LAT (+ random 0..JIT) clk1x per request; ADPCM headers 0 (no end flag), other data noise
    process(clk1x)
        type ttags is array (0 to 255) of unsigned(13 downto 0);
        type tpres is array (0 to 255) of std_logic_vector(3 downto 0);
        variable tags, tags2 : ttags := (others => (others => '1'));
        variable pres, pres2 : tpres := (others => "0000");
        variable lru : std_logic_vector(0 to 255) := (others => '0');   -- 1: way 2 used last
        type thdr is array (0 to 23) of unsigned(14 downto 0);
        variable htag : thdr := (others => (others => '1'));
        variable hval : std_logic_vector(0 to 23) := (others => '0');
        variable v : natural;
        variable hdr_hit : boolean;
        alias voice_index is << signal .tb_spu_budget.dut.index : integer range 0 to 23 >>;
        alias voice_req is << signal .tb_spu_budget.dut.ram_isVoice : std_logic >>;
        variable set, wd : natural;
        variable delay : natural := 0;
        variable st : unsigned(31 downto 0) := to_unsigned(SEED * 7919 + 1, 32);
        variable h : unsigned(31 downto 0);
    begin
        if rising_edge(clk1x) then
            sdram_done<='0';
            if ena='1' then
                st := st xor shift_left(st,13); st := st xor shift_right(st,17); st := st xor shift_left(st,5);
                delay := LAT; if JIT > 0 then delay := LAT + to_integer(st(15 downto 0)) mod (JIT + 1); end if;
                hdr_hit := false;
                if CACHE = 1 and HDR = 1 and rnw = '1' and adr(3 downto 0) = "0000" then
                    v := voice_index;
                    if hval(v) = '1' and htag(v) = unsigned(adr(18 downto 4)) then hdr_hit := true; delay := 2; hits <= hits + 1;
                    else htag(v) := unsigned(adr(18 downto 4)); hval(v) := '1'; end if;
                end if;
                if CACHE = 1 and HDR = 1 and rnw = '0' then hval := (others => '0'); end if;   -- a write clears the header store
                if CACHE = 1 and not hdr_hit then
                    set := to_integer(unsigned(adr(SET_BITS + 3 downto 4)) xor unsigned(adr(2*SET_BITS + 3 downto SET_BITS + 4))
                                      xor resize(shift_right(unsigned(adr(18 downto 4)), 2*SET_BITS), SET_BITS));   -- the slim cache's set hash
                    wd := to_integer(unsigned(adr(3 downto 2)));
                    if VIDX = 1 then
                        if rnw = '0' then pres := (others => "0000"); end if;   -- a write clears every line
                        if voice_req = '1' then set := voice_index; else set := 31; end if;   -- non-voice reads: set 31 (unused by voices)
                    end if;
                    if rnw = '1' and tags(set) = unsigned(adr(18 downto 5)) and pres(set)(wd) = '1' then
                        delay := 2; hits <= hits + 1; lru(set) := '0';
                    elsif WAYS = 2 and rnw = '1' and tags2(set) = unsigned(adr(18 downto 5)) and pres2(set)(wd) = '1' then
                        delay := 2; hits <= hits + 1; lru(set) := '1';
                    else
                        delay := delay + 2;
                        if rnw = '1' then
                            if tags(set) = unsigned(adr(18 downto 5)) then pres(set)(wd) := '1'; lru(set) := '0';
                            elsif WAYS = 2 and tags2(set) = unsigned(adr(18 downto 5)) then pres2(set)(wd) := '1'; lru(set) := '1';
                            elsif WAYS = 2 and lru(set) = '0' then tags2(set) := unsigned(adr(18 downto 5)); pres2(set) := "0000"; pres2(set)(wd) := '1'; lru(set) := '1';
                            else tags(set) := unsigned(adr(18 downto 5)); pres(set) := "0000"; pres(set)(wd) := '1'; lru(set) := '0';
                            end if;
                        end if;
                    end if;
                end if;
                requests <= requests + 1;
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
            if timeout='1' then drops<=drops+1; end if;
            if to_integer(stash)>max_stash then max_stash<=to_integer(stash); end if;
            if busy='1' then run<=run+1; if run+1>max_busy then max_busy<=run+1; end if; else run<=0; end if;
        end if;
    end process;
    process
        procedure t is begin wait until rising_edge(clk1x); wait for 1 ns; end;
        impure function start(v : natural) return natural is
            variable r : unsigned(31 downto 0);
        begin
            if RANDOM = 1 then
                r := to_unsigned(SEED * 2654 + 12345, 32);
                for i in 0 to v loop r := r xor shift_left(r,13); r := r xor shift_right(r,17); r := r xor shift_left(r,5); end loop;
                return 16#0100# + 2 * (to_integer(r(15 downto 0)) mod 16#7000#);   -- 16-byte aligned blocks anywhere
            end if;
            return 16#0100# + STRIDE * v;
        end;
        procedure w(a : natural; d : natural) is
        begin bus_addr<=to_unsigned(a,10); bus_dataWrite<=std_logic_vector(to_unsigned(d,16)); bus_write<='1'; t; bus_write<='0'; t; t; end;
    begin
        for i in 1 to 20 loop t; end loop;
        reset<='0';
        for i in 1 to 20 loop t; end loop;
        w(16#1AA#, 16#C0C5#);              -- SPU on, unmute, reverb master, CD audio + CD reverb
        w(16#180#, 16#3FFF#); w(16#182#, 16#3FFF#); w(16#184#, 16#3FFF#); w(16#186#, 16#3FFF#);
        w(16#1B0#, 16#7FFF#); w(16#1B2#, 16#7FFF#); w(16#1A2#, 16#E000#);   -- CD volume, reverb work area
        for k in 0 to 31 loop w(16#1C0# + 2*k, 16#0400# + 37*k); end loop;  -- reverb registers
        for v in 0 to 23 loop
            w(16*v + 0, 16#3FFF#); w(16*v + 2, 16#3FFF#); w(16*v + 4, PITCH);
            w(16*v + 6, start(v)); w(16*v + 8, 16#00FF#); w(16*v + 10, 16#0000#);   -- start, ADSR (fast attack, sustain max)
            w(16*v + 14, start(v));
        end loop;
        w(16#198#, 16#FFFF#); w(16#19A#, 16#00FF#);   -- every voice into reverb
        w(16#188#, 16#FFFF#); w(16#18A#, 16#00FF#);   -- key on all 24
        wait until samples >= OPERATIONS;
        done<=true;
        report "BUDGET LAT=" & natural'image(LAT) & " JIT=" & natural'image(JIT) & " PITCH=" & natural'image(PITCH) &
            " CACHE=" & natural'image(CACHE) &  " STRIDE=" & natural'image(STRIDE) & " RANDOM=" & natural'image(RANDOM) & " SETS=" & natural'image(2**SET_BITS) & " WAYS=" & natural'image(WAYS) & " HDR=" & natural'image(HDR) & " VIDX=" & natural'image(VIDX) &
            " samples=" & natural'image(samples) & " requests=" & natural'image(requests) & " hits=" & natural'image(hits) &
            " out_samples=" & natural'image(out_count) & " out_hash=" & to_hstring(out_hash) &
            " max_busy=" & natural'image(max_busy) & " max_stash=" & natural'image(max_stash) & " drops=" & natural'image(drops);
        finish;
    end process;
end architecture;
