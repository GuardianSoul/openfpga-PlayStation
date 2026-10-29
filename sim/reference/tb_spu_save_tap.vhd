-- SPDX-License-Identifier: GPL-2.0-only
-- Save-state SPU tap (prepare_save_states.py spu_tap_text): two tapped SPUs
-- run from reset with different random register traffic (voices keyed on,
-- reverb and noise running). Both are held at a quiet point; the state of A
-- is read the way the save routine does (bus reads of the voice registers,
-- globals and reverb registers, SS reads of the rest) and written into B (bus writes,
-- then SS writes). After both are released on the same clock, B must give
-- the same output as A once the state a save does not carry (the decoded
-- ADPCM history and the pipeline registers) has been rewritten, SETTLE
-- samples later, stay identical to the end, and end with the same saved
-- state. FAULT skips one part of the restore (1 voice records, 2 envelope
-- counters, 3 voice registers, 4 reverb pointer); every fault must keep a
-- mismatch to the end or leave a state difference.
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
use std.env.all;

entity tb_spu_save_tap is
    generic (SEED : natural := 1; OPERATIONS : natural := 3000; SETTLE : natural := 400; SAMPLES : natural := 1500;
             FAULT : natural := 0);
end entity;
architecture test of tb_spu_save_tap is
    signal clk1x,clk2x : std_logic := '1';
    signal reset : std_logic := '1';
    signal toggle,sampled,clk2xIndex : std_logic := '0';
    type tbus is record
        addr : unsigned(9 downto 0);
        wdata : std_logic_vector(15 downto 0);
        rd,wr : std_logic;
        ss_adr : unsigned(8 downto 0);
        ss_wdata : std_logic_vector(31 downto 0);
        ss_rden,ss_wren,hold : std_logic;
        ram_adr : std_logic_vector(18 downto 0);
        ram_wdata : std_logic_vector(15 downto 0);
        ram_req,ram_rnw : std_logic;
    end record;
    constant IDLE_BUS : tbus := ((others=>'0'),(others=>'0'),'0','0',(others=>'0'),(others=>'0'),'0','0','0',(others=>'0'),(others=>'0'),'0','1');
    signal ba,bb : tbus := IDLE_BUS;
    type tout is record
        tick,irq,timeout,dmareq,rnw,ena : std_logic;
        l,r,rd,dma : std_logic_vector(15 downto 0);
        wdata : std_logic_vector(31 downto 0);
        adr : std_logic_vector(18 downto 0);
        be : std_logic_vector(3 downto 0);
    end record;
    signal a,b : tout;
    signal ssa,ssb : std_logic_vector(31 downto 0);
    signal qa,qb : std_logic;
    signal ramqa,ramqb : std_logic_vector(15 downto 0);
    signal ramda,ramdb : std_logic;
    signal rda,rdb : std_logic_vector(31 downto 0);
    signal dna,dnb : std_logic;
    signal compare : boolean := false;
    signal mismatches,cycles : natural := 0;
    signal last_mismatch : natural := 0;
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
    ua : entity work.spu_t port map (clk1x=>clk1x, clk2x=>clk2x, clk2xIndex=>clk2xIndex, ce=>'1', reset=>reset,
        SPUon=>'1', SPUIRQTrigger=>'0', useSDRAM=>'1', REPRODUCIBLESPUIRQ=>'1', REPRODUCIBLESPUDMA=>'0', REVERBOFF=>'0',
        cpuPaused=>'0', spu_tick=>a.tick, cd_left=>x"1234", cd_right=>x"F00D", irqOut=>a.irq, sound_timeout=>a.timeout,
        sound_out_left=>a.l, sound_out_right=>a.r, bus_addr=>ba.addr, bus_dataWrite=>ba.wdata, bus_read=>ba.rd,
        bus_write=>ba.wr, bus_dataRead=>a.rd, spu_dmaRequest=>a.dmareq, dma_read=>'0', dma_readdata=>a.dma,
        dma_write=>'0', dma_writedata=>(others=>'0'), sdram_dataWrite=>a.wdata, sdram_Adr=>a.adr, sdram_be=>a.be,
        sdram_rnw=>a.rnw, sdram_ena=>a.ena, sdram_dataRead=>rda, sdram_done=>dna,
        mem_request=>open, mem_BURSTCNT=>open, mem_ADDR=>open, mem_DIN=>open, mem_BE=>open, mem_WE=>open, mem_RD=>open,
        mem_ack=>'0', mem_DOUT=>(others=>'0'), mem_DOUT_READY=>'0', SS_reset=>reset, loading_savestate=>'0',
        SS_DataWrite=>ba.ss_wdata, SS_Adr=>ba.ss_adr, SS_wren=>ba.ss_wren, SS_rden=>ba.ss_rden, SS_DataRead=>ssa, SS_idle=>open,
        save_spu_hold=>ba.hold, save_spu_quiet=>qa,
        SS_RAM_dataWrite=>ba.ram_wdata, SS_RAM_Adr=>ba.ram_adr, SS_RAM_request=>ba.ram_req, SS_RAM_rnw=>ba.ram_rnw,
        SS_RAM_dataRead=>ramqa, SS_RAM_done=>ramda);
    ub : entity work.spu_t port map (clk1x=>clk1x, clk2x=>clk2x, clk2xIndex=>clk2xIndex, ce=>'1', reset=>reset,
        SPUon=>'1', SPUIRQTrigger=>'0', useSDRAM=>'1', REPRODUCIBLESPUIRQ=>'1', REPRODUCIBLESPUDMA=>'0', REVERBOFF=>'0',
        cpuPaused=>'0', spu_tick=>b.tick, cd_left=>x"1234", cd_right=>x"F00D", irqOut=>b.irq, sound_timeout=>b.timeout,
        sound_out_left=>b.l, sound_out_right=>b.r, bus_addr=>bb.addr, bus_dataWrite=>bb.wdata, bus_read=>bb.rd,
        bus_write=>bb.wr, bus_dataRead=>b.rd, spu_dmaRequest=>b.dmareq, dma_read=>'0', dma_readdata=>b.dma,
        dma_write=>'0', dma_writedata=>(others=>'0'), sdram_dataWrite=>b.wdata, sdram_Adr=>b.adr, sdram_be=>b.be,
        sdram_rnw=>b.rnw, sdram_ena=>b.ena, sdram_dataRead=>rdb, sdram_done=>dnb,
        mem_request=>open, mem_BURSTCNT=>open, mem_ADDR=>open, mem_DIN=>open, mem_BE=>open, mem_WE=>open, mem_RD=>open,
        mem_ack=>'0', mem_DOUT=>(others=>'0'), mem_DOUT_READY=>'0', SS_reset=>reset, loading_savestate=>'0',
        SS_DataWrite=>bb.ss_wdata, SS_Adr=>bb.ss_adr, SS_wren=>bb.ss_wren, SS_rden=>bb.ss_rden, SS_DataRead=>ssb, SS_idle=>open,
        save_spu_hold=>bb.hold, save_spu_quiet=>qb,
        SS_RAM_dataWrite=>bb.ram_wdata, SS_RAM_Adr=>bb.ram_adr, SS_RAM_request=>bb.ram_req, SS_RAM_rnw=>bb.ram_rnw,
        SS_RAM_dataRead=>ramqb, SS_RAM_done=>ramdb);
    -- SPU RAM model per SPU: address-derived read data after a short delay
    -- (writes are not stored, so both see the same RAM).
    memory : for i in 0 to 1 generate
        signal ena : std_logic;
        signal adr : std_logic_vector(18 downto 0);
        signal q : std_logic_vector(31 downto 0);
        signal done : std_logic := '0';
    begin
        ena <= a.ena when i = 0 else b.ena;
        adr <= a.adr when i = 0 else b.adr;
        process(clk1x)
            variable delay : natural := 0;
            variable h : unsigned(31 downto 0);
        begin
            if rising_edge(clk1x) then
                done<='0';
                if ena='1' then delay:=3;
                elsif delay>0 then
                    delay:=delay-1;
                    if delay=0 then
                        h:=resize(resize(unsigned(adr),32)*to_unsigned(40503,16),32);
                        q<=std_logic_vector(h xor x"9E3779B9"); done<='1';
                    end if;
                end if;
            end if;
        end process;
        g0 : if i = 0 generate rda <= q; dna <= done; end generate;
        g1 : if i = 1 generate rdb <= q; dnb <= done; end generate;
    end generate;
    process(clk1x) begin
        if falling_edge(clk1x) and compare then
            cycles<=cycles+1;
            -- (bus_dataRead holds the last guest read, which only A had)
            if a.tick/=b.tick or a.irq/=b.irq or a.timeout/=b.timeout or a.dmareq/=b.dmareq or a.rnw/=b.rnw or a.ena/=b.ena or
               a.l/=b.l or a.r/=b.r or a.dma/=b.dma or a.wdata/=b.wdata or a.adr/=b.adr or a.be/=b.be then mismatches<=mismatches+1; last_mismatch<=cycles; end if;
            if (a.l/=b.l or a.r/=b.r or a.adr/=b.adr) and cycles mod 76800 = 100 then
                report "diff at sample " & natural'image(cycles/768) & " l " & to_hstring(a.l) & "/" & to_hstring(b.l) &
                    " r " & to_hstring(a.r) & "/" & to_hstring(b.r) & " adr " & to_hstring(a.adr) & "/" & to_hstring(b.adr) &
                    " ena " & std_logic'image(a.ena) & std_logic'image(b.ena) & " irq " & std_logic'image(a.irq) & std_logic'image(b.irq) &
                    " wdata " & to_hstring(a.wdata) & "/" & to_hstring(b.wdata) & " tick " & std_logic'image(a.tick) & std_logic'image(b.tick) &
                    " dmareq " & std_logic'image(a.dmareq) & std_logic'image(b.dmareq) & " rd " & to_hstring(a.rd) & "/" & to_hstring(b.rd);
            end if;
        end if;
    end process;
    process
        variable state : unsigned(31 downto 0);
        impure function rnd(n : natural) return natural is
        begin
            state := state xor shift_left(state,13); state := state xor shift_right(state,17); state := state xor shift_left(state,5);
            return to_integer(state(30 downto 0)) mod n;
        end;
        procedure tick is begin wait until rising_edge(clk1x); wait for 1 ns; end;
        procedure traffic(signal bus_s : inout tbus; first : natural) is
            variable address : natural;
            variable data : std_logic_vector(15 downto 0);
        begin
            state := to_unsigned(first * 7919 + 1, 32);
            for op in 1 to OPERATIONS loop
                address := rnd(512) * 2;
                data := std_logic_vector(to_unsigned(rnd(65536),16));
                if address = 16#1AA# then data(5 downto 4):="00"; data(15):='1'; end if;  -- SPU on, no manual transfers
                if address = 16#1A8# then address := 16#1A6#; end if;                     -- no FIFO data writes
                if address >= 16#188# and address < 16#18C# and rnd(2) = 0 then data := (others=>'1'); end if;   -- key on
                bus_s.addr<=to_unsigned(address,10); bus_s.wdata<=data; bus_s.wr<='1'; tick; bus_s.wr<='0';
                for i in 1 to 40 + rnd(400) loop tick; end loop;
            end loop;
        end;
        type tglobals is array(0 to 255) of std_logic_vector(15 downto 0);
        type tss is array(0 to 511) of std_logic_vector(31 downto 0);
        variable g : tglobals;
        variable s : tss;
        procedure bus_read(signal bus_s : inout tbus; address : natural; value : out std_logic_vector(15 downto 0)) is
        begin
            bus_s.addr<=to_unsigned(address,10); bus_s.rd<='1'; tick; bus_s.rd<='0'; tick; value := a.rd;
        end;
        procedure bus_write(signal bus_s : inout tbus; address : natural; value : std_logic_vector(15 downto 0)) is
        begin
            bus_s.addr<=to_unsigned(address,10); bus_s.wdata<=value; bus_s.wr<='1'; tick; bus_s.wr<='0'; tick;
        end;
        procedure ss_read(signal bus_s : inout tbus; signal q : in std_logic_vector(31 downto 0); address : natural;
                          value : out std_logic_vector(31 downto 0)) is
        begin
            bus_s.ss_adr<=to_unsigned(address,9); tick; bus_s.ss_rden<='1'; tick; bus_s.ss_rden<='0'; tick; value := q;
        end;
        -- SPU RAM halfword A -> B through the savestate RAM port (the save
        -- routine's path: its writes also reach the on-chip capture and
        -- reverb copies, which the RAM model below does not store)
        procedure ram_copy(signal sa : inout tbus; signal sb : inout tbus; address : natural) is
            variable waited : natural;
        begin
            sa.ram_adr<=std_logic_vector(to_unsigned(address,19)); sa.ram_rnw<='1'; sa.ram_req<='1'; tick; sa.ram_req<='0';
            waited := 0;
            while ramda /= '1' loop tick; waited := waited + 1; assert waited < 1000 report "SPU RAM read never done" severity failure; end loop;
            sb.ram_adr<=std_logic_vector(to_unsigned(address,19)); sb.ram_wdata<=ramqa; sb.ram_rnw<='0'; sb.ram_req<='1'; tick; sb.ram_req<='0';
            waited := 0;
            while ramdb /= '1' loop tick; waited := waited + 1; assert waited < 1000 report "SPU RAM write never done" severity failure; end loop;
            sb.ram_rnw<='1';
        end;
        procedure ss_write(signal bus_s : inout tbus; address : natural; value : std_logic_vector(31 downto 0)) is
        begin
            bus_s.ss_adr<=to_unsigned(address,9); bus_s.ss_wdata<=value; bus_s.ss_wren<='1'; tick; bus_s.ss_wren<='0'; tick;
        end;
        function bus_restored(address : natural) return boolean is
        begin
            -- voice registers, globals and reverb registers a guest write restores
            -- without side effects the SS writes do not repair (ENDX, FIFO data,
            -- SPUSTAT and the current main volume are not written)
            return address < 16#200# and address /= 16#19C# and address /= 16#19E# and
                   address /= 16#1A8# and address /= 16#1AE# and address /= 16#1B8# and address /= 16#1BA#;
        end;
        function ss_saved(address : natural) return boolean is
        begin
            return (address >= 2 and address <= 6) or address = 37 or address = 40 or address = 43 or
                   (address >= 256 and address < 304) or (address >= 320 and address < 370) or (address >= 384 and address < 480);
        end;
        function skipped(address : natural) return boolean is
        begin
            return (FAULT = 1 and address >= 384 and address < 480) or (FAULT = 2 and address >= 320 and address < 370) or
                    (FAULT = 4 and address = 4);
        end;
        variable v16 : std_logic_vector(15 downto 0);
        variable a16 : std_logic_vector(15 downto 0);
        variable v32 : std_logic_vector(31 downto 0);
        variable waited : natural;
        variable state_diffs : natural := 0;
        variable pm : std_logic_vector(31 downto 0);
    begin
        for i in 1 to 20 loop tick; end loop;
        reset<='0';
        for i in 1 to 20 loop tick; end loop;
        -- A gets its traffic, then B gets different traffic (B keeps running meanwhile)
        traffic(ba, SEED);
        traffic(bb, SEED + 1000);
        -- quiet point on both
        ba.hold<='1'; bb.hold<='1';
        waited := 0;
        while not (qa = '1' and qb = '1') loop
            tick; waited := waited + 1;
            assert waited < 20000 report "SPU never reached the quiet point" severity failure;
        end loop;
        -- save A
        for i in 0 to 16#FF# loop bus_read(ba, 2 * i, v16); g(i) := v16; end loop;
        for i in 0 to 511 loop if ss_saved(i) then ss_read(ba, ssa, i, v32); s(i) := v32; end if; end loop;
        s(43) := g(16#DD#) & g(16#DC#);   -- current main volume: the port only writes it, the registers read it
        s(37) := g(16#CF#) & g(16#CE#);   -- ENDX and STAT (IRQ9): the port no longer reads them back
        s(40) := g(16#D7#) & x"0000";
        -- load B: SPU RAM first, as the save routine (the RAM model holds the
        -- same data for both, so only the on-chip capture and reverb areas)
        for i in 0 to 16#7FF# loop ram_copy(ba, bb, 2 * i); end loop;
        for i in 16#38000# to 16#3FFFF# loop ram_copy(ba, bb, 2 * i); end loop;
        -- then guest writes (they set side state the SS writes then fix)
        for i in 0 to 16#FF# loop
            if bus_restored(2 * i) and not (FAULT = 4 and 2 * i = 16#1A2#) and not (FAULT = 3 and 2 * i < 16#180#) then
                bus_write(bb, 2 * i, g(i));
            end if;
        end loop;
        for i in 0 to 511 loop if ss_saved(i) and not skipped(i) then ss_write(bb, i, s(i)); end if; end loop;
        assert qa = '1' and qb = '1' report "SPU left the quiet point" severity failure;
        -- release both on the same clock
        compare<=true;
        ba.hold<='0'; bb.hold<='0';
        for i in 1 to SAMPLES * 768 loop tick; end loop;
        compare<=false;
        -- hidden state at the end: hold both on the same clock and compare the
        -- saved words (a skipped part can hide in the output, for example
        -- envelope counters of voices whose volume is pinned at 0 or full)
        ba.hold<='1'; bb.hold<='1';
        waited := 0;
        while not (qa = '1' and qb = '1') loop
            tick; waited := waited + 1;
            assert waited < 20000 report "SPU never reached the quiet point at the end" severity failure;
        end loop;
        -- (a pitch-modulated voice steps by the previous voice's output; in the
        -- first samples after a load that output comes from the decoded ADPCM
        -- history the save does not carry, so its sample position may keep a
        -- small phase offset: its sample-position word is not compared)
        pm := g(16#C9#) & g(16#C8#);
        for i in 0 to 511 loop
            if ss_saved(i) and i /= 43 and i /= 37 and i /= 40 and not (i >= 384 and i < 480 and (i - 384) mod 4 = 2 and pm((i - 384) / 4) = '1') then
                ss_read(ba, ssa, i, v32); s(i) := v32;
                ss_read(bb, ssb, i, v32);
                if v32 /= s(i) then
                    if state_diffs < 4 then
                        report "SPU save tap: end state word " & natural'image(i) & " A=" & to_hstring(s(i)) & " B=" & to_hstring(v32);
                    end if;
                    state_diffs := state_diffs + 1;
                end if;
            end if;
        end loop;
        for i in 0 to 2 loop   -- ENDX and STAT by guest reads
            bus_read(ba, 16#19C# + 2 * i + 14 * (i / 2), v16); a16 := v16;
            bus_read(bb, 16#19C# + 2 * i + 14 * (i / 2), v16);
            if v16 /= a16 then
                if state_diffs < 4 then
                    report "SPU save tap: end register " & to_hstring(to_unsigned(16#19C# + 2 * i + 14 * (i / 2), 12)) & " A=" & to_hstring(a16) & " B=" & to_hstring(v16);
                end if;
                state_diffs := state_diffs + 1;
            end if;
        end loop;
        report "SPU save tap: cycles=" & natural'image(cycles) & " mismatches=" & natural'image(mismatches) &
               " last_mismatch_sample=" & natural'image(last_mismatch / 768) & " state_diffs=" & natural'image(state_diffs);
        if FAULT = 0 then
            assert mismatches = 0 or last_mismatch < SETTLE * 768 report "SPU save tap: B differs from A after settling" severity failure;
            assert state_diffs = 0 report "SPU save tap: B state differs from A at the end" severity failure;
            report "PASS SPU save tap";
        else
            assert last_mismatch >= (SAMPLES - 50) * 768 or state_diffs > 0 report "SPU save tap: planted fault not caught" severity failure;
            report "PASS SPU save tap fault caught";
        end if;
        finish;
    end process;
end architecture;
