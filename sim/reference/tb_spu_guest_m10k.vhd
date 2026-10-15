-- SPDX-License-Identifier: GPL-2.0-only
-- Cycle lockstep of the SPU (context RAM + reverb table) and the
-- spu-guest-m10k variant: random register writes and readbacks of every SPU
-- address while voices, reverb and transfers run; every output is compared
-- on every clk1x cycle.
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
use std.env.all;

entity tb_spu_guest_m10k is
    generic (SEED : natural := 1; OPERATIONS : natural := 20000);
end entity;
architecture test of tb_spu_guest_m10k is
    signal clk1x,clk2x : std_logic := '1';
    signal reset : std_logic := '1';
    signal toggle,sampled,clk2xIndex : std_logic := '0';
    signal bus_addr : unsigned(9 downto 0) := (others=>'0');
    signal bus_dataWrite : std_logic_vector(15 downto 0) := (others=>'0');
    signal bus_read,bus_write : std_logic := '0';
    signal sdram_dataRead : std_logic_vector(31 downto 0) := (others=>'0');
    signal sdram_done : std_logic := '0';
    type tout is record
        tick,irq,timeout,dmareq,rnw,ena : std_logic;
        l,r,rd,dma : std_logic_vector(15 downto 0);
        wdata : std_logic_vector(31 downto 0);
        adr : std_logic_vector(18 downto 0);
        be : std_logic_vector(3 downto 0);
    end record;
    signal a,b : tout;
    signal compared,reads : natural := 0;
    signal done : boolean := false;
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
    original : entity work.spu port map (clk1x=>clk1x, clk2x=>clk2x, clk2xIndex=>clk2xIndex, ce=>'1', reset=>reset,
        SPUon=>'1', SPUIRQTrigger=>'0', useSDRAM=>'1', REPRODUCIBLESPUIRQ=>'1', REPRODUCIBLESPUDMA=>'0', REVERBOFF=>'0',
        cpuPaused=>'0', spu_tick=>a.tick, cd_left=>x"1234", cd_right=>x"F00D", irqOut=>a.irq, sound_timeout=>a.timeout,
        sound_out_left=>a.l, sound_out_right=>a.r, bus_addr=>bus_addr, bus_dataWrite=>bus_dataWrite, bus_read=>bus_read,
        bus_write=>bus_write, bus_dataRead=>a.rd, spu_dmaRequest=>a.dmareq, dma_read=>'0', dma_readdata=>a.dma,
        dma_write=>'0', dma_writedata=>(others=>'0'), sdram_dataWrite=>a.wdata, sdram_Adr=>a.adr, sdram_be=>a.be,
        sdram_rnw=>a.rnw, sdram_ena=>a.ena, sdram_dataRead=>sdram_dataRead, sdram_done=>sdram_done,
        mem_request=>open, mem_BURSTCNT=>open, mem_ADDR=>open, mem_DIN=>open, mem_BE=>open, mem_WE=>open, mem_RD=>open,
        mem_ack=>'0', mem_DOUT=>(others=>'0'), mem_DOUT_READY=>'0', SS_reset=>reset, loading_savestate=>'0',
        SS_DataWrite=>(others=>'0'), SS_Adr=>(others=>'0'), SS_wren=>'0', SS_rden=>'0', SS_DataRead=>open, SS_idle=>open,
        SS_RAM_dataWrite=>(others=>'0'), SS_RAM_Adr=>(others=>'0'), SS_RAM_request=>'0', SS_RAM_rnw=>'0',
        SS_RAM_dataRead=>open, SS_RAM_done=>open);
    variant : entity work.spu_g port map (clk1x=>clk1x, clk2x=>clk2x, clk2xIndex=>clk2xIndex, ce=>'1', reset=>reset,
        SPUon=>'1', SPUIRQTrigger=>'0', useSDRAM=>'1', REPRODUCIBLESPUIRQ=>'1', REPRODUCIBLESPUDMA=>'0', REVERBOFF=>'0',
        cpuPaused=>'0', spu_tick=>b.tick, cd_left=>x"1234", cd_right=>x"F00D", irqOut=>b.irq, sound_timeout=>b.timeout,
        sound_out_left=>b.l, sound_out_right=>b.r, bus_addr=>bus_addr, bus_dataWrite=>bus_dataWrite, bus_read=>bus_read,
        bus_write=>bus_write, bus_dataRead=>b.rd, spu_dmaRequest=>b.dmareq, dma_read=>'0', dma_readdata=>b.dma,
        dma_write=>'0', dma_writedata=>(others=>'0'), sdram_dataWrite=>b.wdata, sdram_Adr=>b.adr, sdram_be=>b.be,
        sdram_rnw=>b.rnw, sdram_ena=>b.ena, sdram_dataRead=>sdram_dataRead, sdram_done=>sdram_done,
        mem_request=>open, mem_BURSTCNT=>open, mem_ADDR=>open, mem_DIN=>open, mem_BE=>open, mem_WE=>open, mem_RD=>open,
        mem_ack=>'0', mem_DOUT=>(others=>'0'), mem_DOUT_READY=>'0', SS_reset=>reset, loading_savestate=>'0',
        SS_DataWrite=>(others=>'0'), SS_Adr=>(others=>'0'), SS_wren=>'0', SS_rden=>'0', SS_DataRead=>open, SS_idle=>open,
        SS_RAM_dataWrite=>(others=>'0'), SS_RAM_Adr=>(others=>'0'), SS_RAM_request=>'0', SS_RAM_rnw=>'0',
        SS_RAM_dataRead=>open, SS_RAM_done=>open);
    -- Memory: answers the original's requests after a short delay with
    -- address-derived data (writes are not stored).
    process(clk1x)
        variable delay : natural := 0;
        variable h : unsigned(31 downto 0);
    begin
        if rising_edge(clk1x) then
            sdram_done<='0';
            if a.ena='1' then delay:=3;
            elsif delay>0 then
                delay:=delay-1;
                if delay=0 then
                    h:=resize(resize(unsigned(a.adr),32)*to_unsigned(40503,16),32);
                    sdram_dataRead<=std_logic_vector(h xor x"9E3779B9"); sdram_done<='1';
                end if;
            end if;
        end if;
    end process;
    process(clk1x) begin
        if falling_edge(clk1x) and reset='0' and not done then
            assert a=b report "SPU lockstep mismatch at cycle " & natural'image(compared) &
                " read " & to_hstring(a.rd) & "/" & to_hstring(b.rd) & " out " & to_hstring(a.l) & "/" & to_hstring(b.l)
                severity failure;
            compared<=compared+1;
        end if;
    end process;
    process
        variable state : unsigned(31 downto 0) := to_unsigned(SEED * 7919 + 1, 32);
        impure function rnd(n : natural) return natural is
        begin
            state := state xor shift_left(state,13); state := state xor shift_right(state,17); state := state xor shift_left(state,5);
            return to_integer(state(30 downto 0)) mod n;
        end;
        procedure tick is begin wait until rising_edge(clk1x); wait for 1 ns; end;
        variable address : natural;
        variable data : std_logic_vector(15 downto 0);
    begin
        for i in 1 to 20 loop tick; end loop;
        reset<='0';
        for i in 1 to 20 loop tick; end loop;
        for op in 1 to OPERATIONS loop
            address := rnd(512) * 2;
            data := std_logic_vector(to_unsigned(rnd(65536),16));
            if address = 16#1AA# then data(5 downto 4):="00"; data(15):='1'; end if;  -- keep SPU on, no manual transfers
            if address = 16#1A8# then address := 16#1A6#; end if;                     -- no FIFO data writes
            if rnd(3) = 0 then
                bus_addr<=to_unsigned(address,10); bus_dataWrite<=data; bus_write<='1'; tick; bus_write<='0';
            else
                bus_addr<=to_unsigned(address,10); bus_read<='1'; tick; bus_read<='0'; reads<=reads+1;
            end if;
            for i in 1 to rnd(4) loop tick; end loop;
            -- Back-to-back write then read of the same address.
            if rnd(8) = 0 then
                bus_addr<=to_unsigned(address,10); bus_dataWrite<=not data; bus_write<='1'; tick; bus_write<='0';
                bus_read<='1'; tick; bus_read<='0'; reads<=reads+1;
            end if;
        end loop;
        for i in 1 to 2000 loop tick; end loop;
        done<=true;
        report "PASS SPU guest block RAM lockstep reads=" & natural'image(reads) & " cycles=" & natural'image(compared);
        finish;
    end process;
end architecture;
