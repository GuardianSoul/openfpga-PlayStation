-- SPDX-License-Identifier: GPL-2.0-only
-- Restore gate for the "exact voices" SPU save port (state format PXS9).
-- Derived from tb_spu_global_ring.vhd (Codex's expanded-context fixture):
-- two SPUs A and B get different guest histories, both are held at the quiet
-- point, A's state is read through the guest bus and the save port and
-- written into B, both are released on the same clock and compared on every
-- falling clk1x edge from the first cycle.
--
-- Differences from the parent fixture:
--   * 8-word scalar ring at port 32-39; ports 7, 40-47 and 48-62 are never
--     accessed.
--   * A storing SPU RAM model per SPU (512 KiB, ADPCM blocks preloaded, a
--     latency derived from the address so equal requests stall equally).
--     B's model is a copy of A's at restore; the on-chip capture (0-0xFFF)
--     and reverb (0x70000-0x7FFFF) copies inside spu_ram go through the
--     SS_RAM port as in the parent fixture (words already equal are read
--     on both and not rewritten).
--   * B is entity spu_b (the same SPU text, or a planted mutation of it
--     against the unmutated A).
--   * A and B get their different histories side by side, not in turn.
--   * The comparison uses the SPU's internal RAM interface (ram_request /
--     ram_Adr / ram_rnw / ram_dataWrite, external names), so capture and
--     on-chip reverb accesses are seen too, plus the SDRAM pins.
--     Reverb work area = RAM addresses from mBASE*8 to 0x7FFFF: write data
--     there may differ (counted, not a mismatch); everything else is exact.
--   * Ring scalars, ENDX, record/volume/envelope RAM writes, the dry mix
--     (soundleft/right and the reverb input sums at the one clock where
--     cdProcStep = 0, i.e. after the CD add and before the reverb add) are
--     compared through external names.
--   * PCM: compared from the first sample tick after release (the DAC latch
--     is not restored, so before A's first sound_out_valid the outputs are
--     exempt). Per-window maxima of |A - B| are reported.
--   * TRAFFIC = 1: identical guest traffic on both after release, with
--     manual FIFO writes, DMA writes and DMA reads (dma_readdata compared);
--     half of the write transfers arm the SPU IRQ on an address they reach.
-- The pass/fail decision per case is taken by scripts/run_spu_exact_voices.py
-- from the "EV" report lines; this bench only fails by itself on a bench
-- malfunction (no quiet point, RAM port timeout) or an RTL assertion.
-- FAULT: 1 one decoded-sample bank word is not written to B (a word a
--          sounding voice reads next; first such voice from FAULT_PICK on)
--        2 cd_next_left/right not restored (B keeps its own ring bits
--          210-243)
--        3 the voice records of two neighbouring voices are swapped
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
use std.env.all;

entity tb_spu_exact_voices is
    generic (SEED : natural := 1; OPERATIONS : natural := 1500; SAMPLES : natural := 256;
             FAULT : natural := 0; FAULT_PICK : natural := 0; ABORT_WORDS : natural := 0;
             REVERB_OFF : natural := 0;   -- 1: REVERBOFF input high on both
             CNT7 : natural := 2;         -- SPUCNT bit 7 (reverb master enable): 0 / 1 forced, 2 random
             PRESET : natural := 0;       -- 1: short stable reverb preset, reverb registers not randomized
             TRAFFIC : natural := 0;      -- 1: guest traffic and RAM transfers after release
             WINDOW : natural := 64);
end entity;
architecture test of tb_spu_exact_voices is
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
        tick,valid,irq,timeout,dmareq,rnw,ena : std_logic;
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
    signal rda,rdb : std_logic_vector(31 downto 0) := (others=>'0');
    signal dna,dnb : std_logic := '0';
    signal rvoff_pin : std_logic;
    signal cd_al,cd_ar,cd_bl,cd_br,cd_bl_own,cd_br_own : signed(15 downto 0) := (others=>'0');
    signal dma_rd,dma_wr : std_logic := '0';
    signal dma_wdata : std_logic_vector(15 downto 0) := (others=>'0');
    signal compare, released, rd_compare, dump_req : boolean := false;
    signal mem_cmd : natural := 0;      -- 1 copy A -> B, 2 compare
    signal mem_out_diffs, mem_in_diffs : natural := 0;
    signal mem_base_word : natural := 0;
begin
    clk1x <= not clk1x after 14762 ps;
    clk2x <= not clk2x after 7381 ps;
    rvoff_pin <= '1' when REVERB_OFF = 1 else '0';
    process(clk1x) begin if rising_edge(clk1x) then toggle<=not toggle; end if; end process;
    process(clk2x) begin
        if rising_edge(clk2x) then
            sampled<=toggle; clk2xIndex<='0';
            if sampled=toggle then clk2xIndex<='1'; end if;
        end if;
    end process;
    -- CD input: own stream per SPU before release, A's stream on both after.
    process(clk1x)
        variable sa : unsigned(31 downto 0) := to_unsigned(SEED * 2654435 + 12345, 32);
        variable sb : unsigned(31 downto 0) := to_unsigned(SEED * 40503 + 977, 32);
        variable n : natural := 0;
    begin
        if rising_edge(clk1x) then
            n := n + 1;
            if n mod 96 = 0 then
                sa := sa xor shift_left(sa,13); sa := sa xor shift_right(sa,17); sa := sa xor shift_left(sa,5);
                sb := sb xor shift_left(sb,13); sb := sb xor shift_right(sb,17); sb := sb xor shift_left(sb,5);
                cd_al <= signed(sa(15 downto 0)); cd_ar <= signed(sa(31 downto 16));
                cd_bl_own <= signed(sb(15 downto 0)); cd_br_own <= signed(sb(31 downto 16));
            end if;
        end if;
    end process;
    cd_bl <= cd_al when released else cd_bl_own;
    cd_br <= cd_ar when released else cd_br_own;

    ua : entity work.spu port map (clk1x=>clk1x, clk2x=>clk2x, clk2xIndex=>clk2xIndex, ce=>'1', reset=>reset,
        SPUon=>'1', SPUIRQTrigger=>'0', useSDRAM=>'1', REPRODUCIBLESPUIRQ=>'1', REPRODUCIBLESPUDMA=>'0', REVERBOFF=>rvoff_pin,
        cpuPaused=>'0', spu_tick=>a.tick, cd_left=>cd_al, cd_right=>cd_ar, irqOut=>a.irq, sound_timeout=>a.timeout,
        sound_out_left=>a.l, sound_out_right=>a.r, sound_out_valid=>a.valid, bus_addr=>ba.addr, bus_dataWrite=>ba.wdata, bus_read=>ba.rd,
        bus_write=>ba.wr, bus_dataRead=>a.rd, spu_dmaRequest=>a.dmareq, dma_read=>dma_rd, dma_readdata=>a.dma,
        dma_write=>dma_wr, dma_writedata=>dma_wdata, sdram_dataWrite=>a.wdata, sdram_Adr=>a.adr, sdram_be=>a.be,
        sdram_rnw=>a.rnw, sdram_ena=>a.ena, sdram_dataRead=>rda, sdram_done=>dna,
        mem_ack=>'0', mem_DOUT=>(others=>'0'), mem_DOUT_READY=>'0', SS_reset=>reset, loading_savestate=>'0',
        SS_DataWrite=>ba.ss_wdata, SS_Adr=>ba.ss_adr, SS_wren=>ba.ss_wren, SS_rden=>ba.ss_rden, SS_DataRead=>ssa, SS_idle=>open,
        save_spu_hold=>ba.hold, save_spu_quiet=>qa,
        SS_RAM_dataWrite=>ba.ram_wdata, SS_RAM_Adr=>ba.ram_adr, SS_RAM_request=>ba.ram_req, SS_RAM_rnw=>ba.ram_rnw,
        SS_RAM_dataRead=>ramqa, SS_RAM_done=>ramda);
    ub : entity work.spu_b port map (clk1x=>clk1x, clk2x=>clk2x, clk2xIndex=>clk2xIndex, ce=>'1', reset=>reset,
        SPUon=>'1', SPUIRQTrigger=>'0', useSDRAM=>'1', REPRODUCIBLESPUIRQ=>'1', REPRODUCIBLESPUDMA=>'0', REVERBOFF=>rvoff_pin,
        cpuPaused=>'0', spu_tick=>b.tick, cd_left=>cd_bl, cd_right=>cd_br, irqOut=>b.irq, sound_timeout=>b.timeout,
        sound_out_left=>b.l, sound_out_right=>b.r, sound_out_valid=>b.valid, bus_addr=>bb.addr, bus_dataWrite=>bb.wdata, bus_read=>bb.rd,
        bus_write=>bb.wr, bus_dataRead=>b.rd, spu_dmaRequest=>b.dmareq, dma_read=>dma_rd, dma_readdata=>b.dma,
        dma_write=>dma_wr, dma_writedata=>dma_wdata, sdram_dataWrite=>b.wdata, sdram_Adr=>b.adr, sdram_be=>b.be,
        sdram_rnw=>b.rnw, sdram_ena=>b.ena, sdram_dataRead=>rdb, sdram_done=>dnb,
        mem_ack=>'0', mem_DOUT=>(others=>'0'), mem_DOUT_READY=>'0', SS_reset=>reset, loading_savestate=>'0',
        SS_DataWrite=>bb.ss_wdata, SS_Adr=>bb.ss_adr, SS_wren=>bb.ss_wren, SS_rden=>bb.ss_rden, SS_DataRead=>ssb, SS_idle=>open,
        save_spu_hold=>bb.hold, save_spu_quiet=>qb,
        SS_RAM_dataWrite=>bb.ram_wdata, SS_RAM_Adr=>bb.ram_adr, SS_RAM_request=>bb.ram_req, SS_RAM_rnw=>bb.ram_rnw,
        SS_RAM_dataRead=>ramqb, SS_RAM_done=>ramdb);

    -- Storing SPU RAM models (the part behind the SDRAM pins). Word = 16 bit.
    memory : process(clk1x)
        type t_words is array(0 to 1, 0 to 262143) of integer range 0 to 65535;
        variable words : t_words;
        variable loaded : boolean := false;
        type t_nat2 is array(0 to 1) of natural;
        type t_bool2 is array(0 to 1) of boolean;
        variable m_adr, m_w, m_delay : t_nat2 := (others => 0);
        variable m_rnw : std_logic_vector(0 to 1) := "11";
        variable m_busy : t_bool2 := (others => false);
        variable h : unsigned(31 downto 0);
        variable shift, filter, flags, pick, din, dout : natural;
        variable ena, rnw : std_logic;
        variable adr : std_logic_vector(18 downto 0);
        variable wd : std_logic_vector(31 downto 0);
        impure function nxt(n : natural) return natural is
        begin
            h := h xor shift_left(h,13); h := h xor shift_right(h,17); h := h xor shift_left(h,5);
            return to_integer(h(30 downto 0)) mod n;
        end;
    begin
        if not loaded then
            -- ADPCM: 16-byte blocks, word 0 = flags<<8 | filter<<4 | shift.
            h := to_unsigned(SEED * 7919 + 104729, 32);
            for w in 0 to 262143 loop words(0, w) := 0; end loop;
            for blk in 16#100# to 16#5FFF# loop
                shift := nxt(13); filter := nxt(5);
                pick := nxt(100);
                if pick < 5 then flags := 3; elsif pick < 8 then flags := 1; elsif pick < 14 then flags := 4; else flags := 0; end if;
                words(0, blk * 8) := shift + filter * 16 + flags * 256;
                for k in 1 to 7 loop
                    if nxt(2) = 0 then words(0, blk * 8 + k) := ((nxt(6) + 5) * 4369) mod 65536;
                    else words(0, blk * 8 + k) := nxt(65536); end if;
                end loop;
            end loop;
            for w in 0 to 262143 loop words(1, w) := words(0, w); end loop;
            loaded := true;
        end if;
        if rising_edge(clk1x) then
            if mem_cmd = 1 then
                for w in 0 to 262143 loop words(1, w) := words(0, w); end loop;
            elsif mem_cmd = 2 then
                din := 0; dout := 0;
                for w in 0 to 262143 loop
                    if words(1, w) /= words(0, w) then
                        if w >= mem_base_word then din := din + 1; else dout := dout + 1; end if;
                    end if;
                end loop;
                mem_in_diffs <= din; mem_out_diffs <= dout;
            end if;
            for i in 0 to 1 loop
                if i = 0 then ena := a.ena; rnw := a.rnw; adr := a.adr; wd := a.wdata; dna <= '0';
                else ena := b.ena; rnw := b.rnw; adr := b.adr; wd := b.wdata; dnb <= '0'; end if;
                if ena = '1' then
                    assert not m_busy(i) report "overlapping SPU RAM request" severity failure;
                    m_adr(i) := to_integer(unsigned(adr(18 downto 1)));
                    m_rnw(i) := rnw;
                    if is_x(wd(15 downto 0)) then m_w(i) := 0; else m_w(i) := to_integer(unsigned(wd(15 downto 0))); end if;
                    m_delay(i) := 1 + ((m_adr(i) + m_adr(i) / 16) mod 4);
                    m_busy(i) := true;
                elsif m_busy(i) then
                    m_delay(i) := m_delay(i) - 1;
                    if m_delay(i) = 0 then
                        m_busy(i) := false;
                        if m_rnw(i) = '1' then
                            wd := std_logic_vector(to_unsigned(words(i, (m_adr(i) + 1) mod 262144), 16)) &
                                  std_logic_vector(to_unsigned(words(i, m_adr(i)), 16));
                            if i = 0 then rda <= wd; else rdb <= wd; end if;
                        else
                            words(i, m_adr(i)) := m_w(i);
                        end if;
                        if i = 0 then dna <= '1'; else dnb <= '1'; end if;
                    end if;
                end if;
            end loop;
        end if;
    end process;

    mon : block
        alias a_req    is << signal .tb_spu_exact_voices.ua.ram_request : std_logic >>;
        alias b_req    is << signal .tb_spu_exact_voices.ub.ram_request : std_logic >>;
        alias a_rnw    is << signal .tb_spu_exact_voices.ua.ram_rnw : std_logic >>;
        alias b_rnw    is << signal .tb_spu_exact_voices.ub.ram_rnw : std_logic >>;
        alias a_istr   is << signal .tb_spu_exact_voices.ua.ram_isTransfer : std_logic >>;
        alias a_adr    is << signal .tb_spu_exact_voices.ua.ram_Adr : std_logic_vector(18 downto 0) >>;
        alias b_adr    is << signal .tb_spu_exact_voices.ub.ram_Adr : std_logic_vector(18 downto 0) >>;
        alias a_wd     is << signal .tb_spu_exact_voices.ua.ram_dataWrite : std_logic_vector(15 downto 0) >>;
        alias b_wd     is << signal .tb_spu_exact_voices.ub.ram_dataWrite : std_logic_vector(15 downto 0) >>;
        alias a_rdata  is << signal .tb_spu_exact_voices.ua.ram_dataRead : std_logic_vector(15 downto 0) >>;
        alias b_rdata  is << signal .tb_spu_exact_voices.ub.ram_dataRead : std_logic_vector(15 downto 0) >>;
        alias a_done   is << signal .tb_spu_exact_voices.ua.ram_done : std_logic >>;
        alias b_done   is << signal .tb_spu_exact_voices.ub.ram_done : std_logic >>;
        alias a_mbase  is << signal .tb_spu_exact_voices.ua.REVERB_mBASE : std_logic_vector(15 downto 0) >>;
        alias b_mbase  is << signal .tb_spu_exact_voices.ub.REVERB_mBASE : std_logic_vector(15 downto 0) >>;
        alias a_cnt    is << signal .tb_spu_exact_voices.ua.CNT : std_logic_vector(15 downto 0) >>;
        alias b_cnt    is << signal .tb_spu_exact_voices.ub.CNT : std_logic_vector(15 downto 0) >>;
        alias a_stat   is << signal .tb_spu_exact_voices.ua.STAT : std_logic_vector(15 downto 0) >>;
        alias b_stat   is << signal .tb_spu_exact_voices.ub.STAT : std_logic_vector(15 downto 0) >>;
        alias a_endx   is << signal .tb_spu_exact_voices.ua.ENDX : std_logic_vector(31 downto 0) >>;
        alias b_endx   is << signal .tb_spu_exact_voices.ub.ENDX : std_logic_vector(31 downto 0) >>;
        alias a_irq9   is << signal .tb_spu_exact_voices.ua.IRQ9 : std_logic >>;
        alias b_irq9   is << signal .tb_spu_exact_voices.ub.IRQ9 : std_logic >>;
        alias a_irqs   is << signal .tb_spu_exact_voices.ua.irqSaved : std_logic >>;
        alias b_irqs   is << signal .tb_spu_exact_voices.ub.irqSaved : std_logic >>;
        alias a_cap    is << signal .tb_spu_exact_voices.ua.capturePosition : unsigned(9 downto 0) >>;
        alias b_cap    is << signal .tb_spu_exact_voices.ub.capturePosition : unsigned(9 downto 0) >>;
        alias a_tick   is << signal .tb_spu_exact_voices.ua.sampleticks : unsigned(9 downto 0) >>;
        alias b_tick   is << signal .tb_spu_exact_voices.ub.sampleticks : unsigned(9 downto 0) >>;
        alias a_tra    is << signal .tb_spu_exact_voices.ua.ramTransferAddr : unsigned(18 downto 0) >>;
        alias b_tra    is << signal .tb_spu_exact_voices.ub.ramTransferAddr : unsigned(18 downto 0) >>;
        alias a_rca    is << signal .tb_spu_exact_voices.ua.reverbCurrentAddress : unsigned(17 downto 0) >>;
        alias b_rca    is << signal .tb_spu_exact_voices.ub.reverbCurrentAddress : unsigned(17 downto 0) >>;
        alias a_rright is << signal .tb_spu_exact_voices.ua.reverbRight : std_logic >>;
        alias b_rright is << signal .tb_spu_exact_voices.ub.reverbRight : std_logic >>;
        alias a_nlvl   is << signal .tb_spu_exact_voices.ua.noiseLevel : unsigned(15 downto 0) >>;
        alias b_nlvl   is << signal .tb_spu_exact_voices.ub.noiseLevel : unsigned(15 downto 0) >>;
        alias a_ncnt   is << signal .tb_spu_exact_voices.ua.noiseCnt : unsigned(31 downto 0) >>;
        alias b_ncnt   is << signal .tb_spu_exact_voices.ub.noiseCnt : unsigned(31 downto 0) >>;
        alias a_adsrw  is << signal .tb_spu_exact_voices.ua.voice_adsrWrite : std_logic_vector(23 downto 0) >>;
        alias b_adsrw  is << signal .tb_spu_exact_voices.ub.voice_adsrWrite : std_logic_vector(23 downto 0) >>;
        alias a_repw   is << signal .tb_spu_exact_voices.ua.voice_repeatWrite : std_logic_vector(23 downto 0) >>;
        alias b_repw   is << signal .tb_spu_exact_voices.ub.voice_repeatWrite : std_logic_vector(23 downto 0) >>;
        alias a_cvl    is << signal .tb_spu_exact_voices.ua.CURVOL_L : std_logic_vector(15 downto 0) >>;
        alias b_cvl    is << signal .tb_spu_exact_voices.ub.CURVOL_L : std_logic_vector(15 downto 0) >>;
        alias a_cvr    is << signal .tb_spu_exact_voices.ua.CURVOL_R : std_logic_vector(15 downto 0) >>;
        alias b_cvr    is << signal .tb_spu_exact_voices.ub.CURVOL_R : std_logic_vector(15 downto 0) >>;
        alias a_cdl    is << signal .tb_spu_exact_voices.ua.cd_next_left : signed(16 downto 0) >>;
        alias b_cdl    is << signal .tb_spu_exact_voices.ub.cd_next_left : signed(16 downto 0) >>;
        alias a_cdr    is << signal .tb_spu_exact_voices.ua.cd_next_right : signed(16 downto 0) >>;
        alias b_cdr    is << signal .tb_spu_exact_voices.ub.cd_next_right : signed(16 downto 0) >>;
        alias a_sl     is << signal .tb_spu_exact_voices.ua.soundleft : signed(23 downto 0) >>;
        alias b_sl     is << signal .tb_spu_exact_voices.ub.soundleft : signed(23 downto 0) >>;
        alias a_sr     is << signal .tb_spu_exact_voices.ua.soundright : signed(23 downto 0) >>;
        alias b_sr     is << signal .tb_spu_exact_voices.ub.soundright : signed(23 downto 0) >>;
        alias a_rsl    is << signal .tb_spu_exact_voices.ua.reverbsumleft : signed(23 downto 0) >>;
        alias b_rsl    is << signal .tb_spu_exact_voices.ub.reverbsumleft : signed(23 downto 0) >>;
        alias a_rsr    is << signal .tb_spu_exact_voices.ua.reverbsumright : signed(23 downto 0) >>;
        alias b_rsr    is << signal .tb_spu_exact_voices.ub.reverbsumright : signed(23 downto 0) >>;
        alias a_cdstep is << signal .tb_spu_exact_voices.ua.cdProcStep : integer range 0 to 4 >>;
        alias b_cdstep is << signal .tb_spu_exact_voices.ub.cdProcStep : integer range 0 to 4 >>;
        alias a_recw   is << signal .tb_spu_exact_voices.ua.RamVoiceRecord_write : std_logic >>;
        alias b_recw   is << signal .tb_spu_exact_voices.ub.RamVoiceRecord_write : std_logic >>;
        alias a_reca   is << signal .tb_spu_exact_voices.ua.RamVoiceRecord_addrA : unsigned(4 downto 0) >>;
        alias b_reca   is << signal .tb_spu_exact_voices.ub.RamVoiceRecord_addrA : unsigned(4 downto 0) >>;
        alias a_recd   is << signal .tb_spu_exact_voices.ua.RamVoiceRecord_dataA : std_logic_vector(102 downto 0) >>;
        alias b_recd   is << signal .tb_spu_exact_voices.ub.RamVoiceRecord_dataA : std_logic_vector(102 downto 0) >>;
        alias a_volw   is << signal .tb_spu_exact_voices.ua.RamVoiceVolumes_write : std_logic >>;
        alias b_volw   is << signal .tb_spu_exact_voices.ub.RamVoiceVolumes_write : std_logic >>;
        alias a_vola   is << signal .tb_spu_exact_voices.ua.RamVoiceVolumes_addrA : unsigned(5 downto 0) >>;
        alias b_vola   is << signal .tb_spu_exact_voices.ub.RamVoiceVolumes_addrA : unsigned(5 downto 0) >>;
        alias a_vold   is << signal .tb_spu_exact_voices.ua.RamVoiceVolumes_dataA : std_logic_vector(15 downto 0) >>;
        alias b_vold   is << signal .tb_spu_exact_voices.ub.RamVoiceVolumes_dataA : std_logic_vector(15 downto 0) >>;
        alias a_envw   is << signal .tb_spu_exact_voices.ua.EnvCounterRam_write : std_logic >>;
        alias b_envw   is << signal .tb_spu_exact_voices.ub.EnvCounterRam_write : std_logic >>;
        alias a_enva   is << signal .tb_spu_exact_voices.ua.EnvCounterRam_addrA : unsigned(5 downto 0) >>;
        alias b_enva   is << signal .tb_spu_exact_voices.ub.EnvCounterRam_addrA : unsigned(5 downto 0) >>;
        alias a_envd   is << signal .tb_spu_exact_voices.ua.EnvCounterRam_dataA : std_logic_vector(24 downto 0) >>;
        alias b_envd   is << signal .tb_spu_exact_voices.ub.EnvCounterRam_dataA : std_logic_vector(24 downto 0) >>;
        alias a_smpw   is << signal .tb_spu_exact_voices.ua.adpcm_ram_wren_a : std_logic_vector(3 downto 0) >>;
        alias b_smpw   is << signal .tb_spu_exact_voices.ub.adpcm_ram_wren_a : std_logic_vector(3 downto 0) >>;
        alias a_smpa   is << signal .tb_spu_exact_voices.ua.adpcm_ram_address_a : unsigned(7 downto 0) >>;
        alias b_smpa   is << signal .tb_spu_exact_voices.ub.adpcm_ram_address_a : unsigned(7 downto 0) >>;
        -- state the exact-voices port does not carry (positive control only)
        alias a_d1     is << signal .tb_spu_exact_voices.ua.reverbL_d1 : signed(15 downto 0) >>;
        alias b_d1     is << signal .tb_spu_exact_voices.ub.reverbL_d1 : signed(15 downto 0) >>;
        alias a_d2     is << signal .tb_spu_exact_voices.ua.reverbL_d2 : signed(15 downto 0) >>;
        alias b_d2     is << signal .tb_spu_exact_voices.ub.reverbL_d2 : signed(15 downto 0) >>;
        alias a_d3     is << signal .tb_spu_exact_voices.ua.reverbL_d3 : signed(15 downto 0) >>;
        alias b_d3     is << signal .tb_spu_exact_voices.ub.reverbL_d3 : signed(15 downto 0) >>;
        alias a_d4     is << signal .tb_spu_exact_voices.ua.reverbL_d4 : signed(15 downto 0) >>;
        alias b_d4     is << signal .tb_spu_exact_voices.ub.reverbL_d4 : signed(15 downto 0) >>;
        alias a_e1     is << signal .tb_spu_exact_voices.ua.reverbR_d1 : signed(15 downto 0) >>;
        alias b_e1     is << signal .tb_spu_exact_voices.ub.reverbR_d1 : signed(15 downto 0) >>;
        alias a_e2     is << signal .tb_spu_exact_voices.ua.reverbR_d2 : signed(15 downto 0) >>;
        alias b_e2     is << signal .tb_spu_exact_voices.ub.reverbR_d2 : signed(15 downto 0) >>;
        alias a_e3     is << signal .tb_spu_exact_voices.ua.reverbR_d3 : signed(15 downto 0) >>;
        alias b_e3     is << signal .tb_spu_exact_voices.ub.reverbR_d3 : signed(15 downto 0) >>;
        alias a_e4     is << signal .tb_spu_exact_voices.ua.reverbR_d4 : signed(15 downto 0) >>;
        alias b_e4     is << signal .tb_spu_exact_voices.ub.reverbR_d4 : signed(15 downto 0) >>;
        alias a_dnl    is << signal .tb_spu_exact_voices.ua.reverbDownLeft : signed(15 downto 0) >>;
        alias b_dnl    is << signal .tb_spu_exact_voices.ub.reverbDownLeft : signed(15 downto 0) >>;
        alias a_dnr    is << signal .tb_spu_exact_voices.ua.reverbDownRight : signed(15 downto 0) >>;
        alias b_dnr    is << signal .tb_spu_exact_voices.ub.reverbDownRight : signed(15 downto 0) >>;
        alias a_rvv    is << signal .tb_spu_exact_voices.ua.rvLastValid : std_logic_vector(1 downto 0) >>;
        alias b_rvv    is << signal .tb_spu_exact_voices.ub.rvLastValid : std_logic_vector(1 downto 0) >>;
    begin
    checker : process(clk1x)
        constant C_CTRL : natural := 0; constant C_IRQ : natural := 1; constant C_ENDX : natural := 2; constant C_DMA : natural := 3;
        constant C_RAMCTRL : natural := 4; constant C_RAMDATA : natural := 5; constant C_CAPTURE : natural := 6; constant C_RAMREAD : natural := 7;
        constant C_STATE : natural := 8; constant C_REC : natural := 9; constant C_DRY : natural := 10; constant C_BUSRD : natural := 11;
        type t_cnt is array(0 to 11) of natural;
        variable cnt : t_cnt := (others => 0);
        variable first : t_cnt := (others => 0);
        variable cyc, samp : natural := 0;
        variable w_in, w_in_diff, w_out, cap_w, tr_w, tr_r, r_in, r_out, r_in_diff, sdram_reqs, sdram_w_in_diff : natural := 0;
        variable dry_n, irq_n, endx_changes, dma_rd_n, dma_wr_n, rec_w, vol_w, env_w, smp_w : natural := 0;
        variable pcm_samples, pcm_cycles, pcm_first, pcm_last, pcm_peak : natural := 0;
        constant WINMAX : natural := 1023;
        type t_win is array(0 to WINMAX) of natural;
        variable win : t_win := (others => 0);
        variable nwin : natural := 0;
        variable base, adr, d, d2, w : natural;
        variable inside : boolean;
        variable last_rd, last_inside : boolean := false;
        variable prev_endx : std_logic_vector(31 downto 0) := (others => '0');
        variable dumped : boolean := false;
        function catname(c : natural) return string is
        begin
            case c is
                when 0 => return "ctrl"; when 1 => return "irq"; when 2 => return "endx"; when 3 => return "dma";
                when 4 => return "ram_ctrl"; when 5 => return "ram_data"; when 6 => return "capture_data"; when 7 => return "ram_read";
                when 8 => return "state"; when 9 => return "rec"; when 10 => return "dry"; when others => return "bus_read";
            end case;
        end;
        procedure hit(c : natural; name : string; va, vb : std_logic_vector) is
        begin
            if cnt(c) = 0 then first(c) := cyc; end if;
            cnt(c) := cnt(c) + 1;
            if cnt(c) <= 3 then
                report "EVMISMATCH cat=" & catname(c) & " signal=" & name & " cycle=" & natural'image(cyc) &
                       " sample=" & natural'image(samp) & " A=" & to_hstring(va) & " B=" & to_hstring(vb);
            end if;
        end;
        procedure hit1(c : natural; name : string; va, vb : std_logic) is
            variable xa, xb : std_logic_vector(3 downto 0) := "0000";
        begin
            xa(0) := va; xb(0) := vb; hit(c, name, xa, xb);
        end;
        function absdiff(x, y : std_logic_vector(15 downto 0)) return natural is
        begin
            if is_x(x) or is_x(y) then return 65535; end if;
            return abs(to_integer(signed(x)) - to_integer(signed(y)));
        end;
    begin
        if falling_edge(clk1x) then
            if compare then
                cyc := cyc + 1;
                if is_x(a_mbase) then base := 0; else base := to_integer(unsigned(a_mbase)) * 8; end if;
                -- control and timing
                if a.tick /= b.tick then hit1(C_CTRL, "spu_tick", a.tick, b.tick); end if;
                if a.valid /= b.valid then hit1(C_CTRL, "sound_out_valid", a.valid, b.valid); end if;
                if a.timeout /= b.timeout then hit1(C_CTRL, "sound_timeout", a.timeout, b.timeout); end if;
                if a_tick /= b_tick then hit(C_CTRL, "sampleticks", std_logic_vector(a_tick), std_logic_vector(b_tick)); end if;
                if a_cdstep /= b_cdstep then hit(C_CTRL, "cdProcStep", std_logic_vector(to_unsigned(a_cdstep, 4)), std_logic_vector(to_unsigned(b_cdstep, 4))); end if;
                if a_cnt /= b_cnt then hit(C_CTRL, "CNT", a_cnt, b_cnt); end if;
                if a_mbase /= b_mbase then hit(C_CTRL, "REVERB_mBASE", a_mbase, b_mbase); end if;
                if a.irq /= b.irq then hit1(C_IRQ, "irqOut", a.irq, b.irq); end if;
                if a.irq = '1' then irq_n := irq_n + 1; end if;
                if a_endx /= b_endx then hit(C_ENDX, "ENDX", a_endx, b_endx); end if;
                if a_endx /= prev_endx then endx_changes := endx_changes + 1; end if;
                prev_endx := a_endx;
                -- transfer handshakes
                if a.dmareq /= b.dmareq then hit1(C_DMA, "spu_dmaRequest", a.dmareq, b.dmareq); end if;
                if a_stat /= b_stat then hit(C_DMA, "STAT", a_stat, b_stat); end if;
                if a.dma /= b.dma then hit(C_DMA, "dma_readdata", a.dma, b.dma); end if;
                if dma_rd = '1' then dma_rd_n := dma_rd_n + 1; end if;
                if dma_wr = '1' then dma_wr_n := dma_wr_n + 1; end if;
                if rd_compare and a.rd /= b.rd then hit(C_BUSRD, "bus_dataRead", a.rd, b.rd); end if;
                -- RAM: completion of the previous request, then a new request
                if a_done /= b_done then hit1(C_RAMCTRL, "ram_done", a_done, b_done); end if;
                if a_done = '1' and last_rd then
                    if a_rdata /= b_rdata then
                        if last_inside then r_in_diff := r_in_diff + 1;
                        else hit(C_RAMREAD, "ram_dataRead", a_rdata, b_rdata); end if;
                    end if;
                    last_rd := false;
                end if;
                if a_req /= b_req then hit1(C_RAMCTRL, "ram_request", a_req, b_req); end if;
                if a_req = '1' then
                    if a_adr /= b_adr then hit(C_RAMCTRL, "ram_Adr", a_adr, b_adr); end if;
                    if a_rnw /= b_rnw then hit1(C_RAMCTRL, "ram_rnw", a_rnw, b_rnw); end if;
                    if is_x(a_adr) then adr := 0; else adr := to_integer(unsigned(a_adr)); end if;
                    inside := adr >= base;
                    if a_rnw = '0' then
                        if inside then
                            w_in := w_in + 1;
                            if a_wd /= b_wd then w_in_diff := w_in_diff + 1; end if;
                        else
                            w_out := w_out + 1;
                            if a_istr = '1' then tr_w := tr_w + 1; end if;
                            if adr < 16#1000# then
                                cap_w := cap_w + 1;
                                if a_wd /= b_wd then hit(C_CAPTURE, "capture write " & to_hstring(a_adr), a_wd, b_wd); end if;
                            elsif a_wd /= b_wd then hit(C_RAMDATA, "ram_dataWrite " & to_hstring(a_adr), a_wd, b_wd); end if;
                        end if;
                    else
                        if inside then r_in := r_in + 1; else r_out := r_out + 1; end if;
                        if a_istr = '1' then tr_r := tr_r + 1; end if;
                    end if;
                    last_rd := a_rnw = '1'; last_inside := inside;
                end if;
                -- the SDRAM pins
                if a.ena /= b.ena then hit1(C_RAMCTRL, "sdram_ena", a.ena, b.ena); end if;
                if a.ena = '1' then
                    sdram_reqs := sdram_reqs + 1;
                    if a.adr /= b.adr then hit(C_RAMCTRL, "sdram_Adr", a.adr, b.adr); end if;
                    if a.rnw /= b.rnw then hit1(C_RAMCTRL, "sdram_rnw", a.rnw, b.rnw); end if;
                    if a.be /= b.be then hit(C_RAMCTRL, "sdram_be", a.be, b.be); end if;
                    if a.rnw = '0' and a.wdata /= b.wdata then
                        if not is_x(a.adr) and to_integer(unsigned(a.adr)) >= base then sdram_w_in_diff := sdram_w_in_diff + 1;
                        else hit(C_RAMDATA, "sdram_dataWrite " & to_hstring(a.adr), a.wdata, b.wdata); end if;
                    end if;
                end if;
                -- scalar state of the ring
                if a_irq9 /= b_irq9 then hit1(C_STATE, "IRQ9", a_irq9, b_irq9); end if;
                if a_irqs /= b_irqs then hit1(C_STATE, "irqSaved", a_irqs, b_irqs); end if;
                if a_cap /= b_cap then hit(C_STATE, "capturePosition", std_logic_vector(a_cap), std_logic_vector(b_cap)); end if;
                if a_tra /= b_tra then hit(C_STATE, "ramTransferAddr", std_logic_vector(a_tra), std_logic_vector(b_tra)); end if;
                if a_rca /= b_rca then hit(C_STATE, "reverbCurrentAddress", std_logic_vector(a_rca), std_logic_vector(b_rca)); end if;
                if a_rright /= b_rright then hit1(C_STATE, "reverbRight", a_rright, b_rright); end if;
                if a_nlvl /= b_nlvl then hit(C_STATE, "noiseLevel", std_logic_vector(a_nlvl), std_logic_vector(b_nlvl)); end if;
                if a_ncnt /= b_ncnt then hit(C_STATE, "noiseCnt", std_logic_vector(a_ncnt), std_logic_vector(b_ncnt)); end if;
                if a_adsrw /= b_adsrw then hit(C_STATE, "voice_adsrWrite", a_adsrw, b_adsrw); end if;
                if a_repw /= b_repw then hit(C_STATE, "voice_repeatWrite", a_repw, b_repw); end if;
                if a_cvl /= b_cvl then hit(C_STATE, "CURVOL_L", a_cvl, b_cvl); end if;
                if a_cvr /= b_cvr then hit(C_STATE, "CURVOL_R", a_cvr, b_cvr); end if;
                if a_cdl /= b_cdl then hit(C_STATE, "cd_next_left", std_logic_vector(a_cdl), std_logic_vector(b_cdl)); end if;
                if a_cdr /= b_cdr then hit(C_STATE, "cd_next_right", std_logic_vector(a_cdr), std_logic_vector(b_cdr)); end if;
                -- voice record, volume, envelope counter and decoded-sample RAM writes
                if a_recw /= b_recw then hit1(C_REC, "RamVoiceRecord_write", a_recw, b_recw); end if;
                if a_recw = '1' then
                    rec_w := rec_w + 1;
                    if a_reca /= b_reca then hit(C_REC, "RamVoiceRecord_addrA", std_logic_vector(a_reca), std_logic_vector(b_reca)); end if;
                    if a_recd /= b_recd then hit(C_REC, "RamVoiceRecord_dataA voice " & natural'image(to_integer(a_reca)), a_recd, b_recd); end if;
                end if;
                if a_volw /= b_volw then hit1(C_REC, "RamVoiceVolumes_write", a_volw, b_volw); end if;
                if a_volw = '1' then
                    vol_w := vol_w + 1;
                    if a_vola /= b_vola then hit(C_REC, "RamVoiceVolumes_addrA", std_logic_vector(a_vola), std_logic_vector(b_vola)); end if;
                    if a_vold /= b_vold then hit(C_REC, "RamVoiceVolumes_dataA", a_vold, b_vold); end if;
                end if;
                if a_envw /= b_envw then hit1(C_REC, "EnvCounterRam_write", a_envw, b_envw); end if;
                if a_envw = '1' then
                    env_w := env_w + 1;
                    if a_enva /= b_enva then hit(C_REC, "EnvCounterRam_addrA", std_logic_vector(a_enva), std_logic_vector(b_enva)); end if;
                    if a_envd /= b_envd then hit(C_REC, "EnvCounterRam_dataA", a_envd, b_envd); end if;
                end if;
                if a_smpw /= b_smpw then hit(C_REC, "adpcm_ram_wren_a", a_smpw, b_smpw); end if;
                if a_smpw /= "0000" then
                    smp_w := smp_w + 1;
                    if a_smpa /= b_smpa then hit(C_REC, "adpcm_ram_address_a", std_logic_vector(a_smpa), std_logic_vector(b_smpa)); end if;
                end if;
                -- dry mix: voices + CD, before the reverb add of this sample
                if a_cdstep = 0 then
                    dry_n := dry_n + 1;
                    if a_sl /= b_sl then hit(C_DRY, "soundleft (dry)", std_logic_vector(a_sl), std_logic_vector(b_sl)); end if;
                    if a_sr /= b_sr then hit(C_DRY, "soundright (dry)", std_logic_vector(a_sr), std_logic_vector(b_sr)); end if;
                    if a_rsl /= b_rsl then hit(C_DRY, "reverbsumleft (reverb input)", std_logic_vector(a_rsl), std_logic_vector(b_rsl)); end if;
                    if a_rsr /= b_rsr then hit(C_DRY, "reverbsumright (reverb input)", std_logic_vector(a_rsr), std_logic_vector(b_rsr)); end if;
                end if;
                -- PCM, from the first sample tick after release
                if a.valid = '1' then
                    samp := samp + 1;
                    d := absdiff(a.l, b.l); d2 := absdiff(a.r, b.r);
                    if d2 > d then d := d2; end if;
                    if d > 0 then
                        pcm_samples := pcm_samples + 1;
                        if pcm_first = 0 then pcm_first := samp; end if;
                        pcm_last := samp;
                        if d > pcm_peak then pcm_peak := d; end if;
                        if pcm_samples <= 3 then
                            report "EVPCM sample=" & natural'image(samp) & " cycle=" & natural'image(cyc) & " A=" & to_hstring(a.l) & "," & to_hstring(a.r) &
                                   " B=" & to_hstring(b.l) & "," & to_hstring(b.r);
                        end if;
                    end if;
                    w := (samp - 1) / WINDOW;
                    if w <= WINMAX then
                        if d > win(w) then win(w) := d; end if;
                        if w + 1 > nwin then nwin := w + 1; end if;
                    end if;
                end if;
                if samp >= 1 and (a.l /= b.l or a.r /= b.r) then pcm_cycles := pcm_cycles + 1; end if;
            end if;
            if dump_req and not dumped then
                dumped := true;
                report "EV cycles=" & natural'image(cyc) & " samples=" & natural'image(samp) & " dry_samples=" & natural'image(dry_n);
                for c in 0 to 11 loop
                    report "EV mismatch_" & catname(c) & "=" & natural'image(cnt(c)) & " first_" & catname(c) & "=" & natural'image(first(c));
                end loop;
                report "EV pcm_sample_diffs=" & natural'image(pcm_samples) & " pcm_cycle_diffs=" & natural'image(pcm_cycles) &
                       " pcm_first_diff_sample=" & natural'image(pcm_first) & " pcm_last_diff_sample=" & natural'image(pcm_last) &
                       " pcm_peak_diff=" & natural'image(pcm_peak);
                report "EV writes_reverb_area=" & natural'image(w_in) & " writes_reverb_area_data_differ=" & natural'image(w_in_diff) &
                       " writes_outside=" & natural'image(w_out) & " capture_writes=" & natural'image(cap_w) &
                       " transfer_writes=" & natural'image(tr_w) & " transfer_reads=" & natural'image(tr_r) &
                       " reads_reverb_area=" & natural'image(r_in) & " reads_reverb_area_data_differ=" & natural'image(r_in_diff) &
                       " reads_outside=" & natural'image(r_out) & " sdram_requests=" & natural'image(sdram_reqs) &
                       " sdram_reverb_write_data_differ=" & natural'image(sdram_w_in_diff);
                report "EV irq_pulses=" & natural'image(irq_n) & " endx_changes=" & natural'image(endx_changes) &
                       " dma_read_words=" & natural'image(dma_rd_n) & " dma_write_words=" & natural'image(dma_wr_n) &
                       " record_writes=" & natural'image(rec_w) & " volume_writes=" & natural'image(vol_w) &
                       " envcounter_writes=" & natural'image(env_w) & " sample_writes=" & natural'image(smp_w);
                report "EV windows=" & natural'image(nwin) & " window=" & natural'image(WINDOW);
                for i in 0 to nwin - 1 loop
                    report "EVWIN " & natural'image(i) & " " & natural'image(win(i));
                end loop;
            end if;
        end if;
    end process;

    stimulus : process
        variable state : unsigned(31 downto 0);
        variable elapsed : natural := 0;
        impure function rnd(n : natural) return natural is
        begin
            state := state xor shift_left(state,13); state := state xor shift_right(state,17); state := state xor shift_left(state,5);
            return to_integer(state(30 downto 0)) mod n;
        end;
        impure function rnd16 return std_logic_vector is
        begin
            return std_logic_vector(to_unsigned(rnd(65536),16));
        end;
        function h16(n : natural) return std_logic_vector is
        begin
            return std_logic_vector(to_unsigned(n,16));
        end;
        procedure tick is begin wait until rising_edge(clk1x); wait for 1 ns; elapsed := elapsed + 1; end;
        procedure idle(n : natural) is begin for i in 1 to n loop tick; end loop; end;
        -- Guest writes the bench never makes at random: transfer control bits
        -- and FIFO data (sequenced explicitly), addresses in or near the
        -- reverb work area for voices and transfers (their data may differ
        -- there by design), a reverb base below 0x6C000.
        procedure legal(address : inout natural; data : inout std_logic_vector(15 downto 0)) is
        begin
            if PRESET = 1 and (address = 16#1A2# or address = 16#184# or address = 16#186# or (address >= 16#1C0# and address < 16#200#)) then
                address := 16#180# + 2 * rnd(2);
            end if;
            if address = 16#1A8# then address := 16#1A6#; end if;
            if address = 16#1AA# then
                data(5 downto 4) := "00"; data(15) := '1';
                if rnd(4) /= 0 then data(14) := '1'; end if;
                if rnd(4) /= 0 then data(0) := '1'; end if;
                if CNT7 = 0 then data(7) := '0'; elsif CNT7 = 1 then data(7) := '1'; end if;
            end if;
            if address = 16#1A6# or address = 16#1A4# then data := h16(16#200# + rnd(16#9000#)); end if;
            if address = 16#1A2# then data := h16(16#D800# + rnd(16#1000#)); end if;
            -- Reverb buffer offsets that keep every engine access inside the
            -- work area (the engine wraps once; a larger offset, or an APF
            -- delay above its buffer offset, would put reverb data elsewhere).
            if address = 16#1C0# or address = 16#1C2# then data := h16(rnd(16#100#)); end if;
            if address >= 16#1D4# and address < 16#1FC# then data := h16(16#100# + rnd(16#F00#)); end if;
            if address < 16#180# and (address mod 16 = 6 or address mod 16 = 14) then data := h16(16#200# + rnd(16#9000#)); end if;
            if address >= 16#188# and address < 16#18C# and rnd(2) = 0 then data := (others=>'1'); end if;   -- key on
        end;
        procedure wr(signal bus_s : inout tbus; address : natural; value : std_logic_vector(15 downto 0)) is
        begin
            bus_s.addr<=to_unsigned(address,10); bus_s.wdata<=value; bus_s.wr<='1'; tick; bus_s.wr<='0'; tick;
        end;
        procedure init(signal bus_s : inout tbus) is
            variable data : std_logic_vector(15 downto 0);
            variable base : natural;
        begin
            data := x"C001";
            if CNT7 /= 0 then data(7) := '1'; end if;
            wr(bus_s, 16#1AA#, data);
            wr(bus_s, 16#180#, x"3FFF"); wr(bus_s, 16#182#, x"3FFF");
            wr(bus_s, 16#1B0#, h16(16#1000# + rnd(16#3000#))); wr(bus_s, 16#1B2#, h16(16#1000# + rnd(16#3000#)));
            wr(bus_s, 16#1A4#, h16(16#200# + rnd(16#9000#)));
            if PRESET = 1 then
                -- The "Room" preset with its buffer offsets divided by 16, so
                -- the echo of a disturbance dies within a few thousand samples.
                wr(bus_s, 16#184#, x"3000"); wr(bus_s, 16#186#, x"3000");
                if SEED mod 2 = 1 then wr(bus_s, 16#1A2#, x"DFC0"); else wr(bus_s, 16#1A2#, x"E040"); end if;
                wr(bus_s, 16#1C0#, x"0008"); wr(bus_s, 16#1C2#, x"0006"); wr(bus_s, 16#1C4#, x"6D80"); wr(bus_s, 16#1C6#, x"54B8");
                wr(bus_s, 16#1C8#, x"BED0"); wr(bus_s, 16#1CA#, x"0000"); wr(bus_s, 16#1CC#, x"0000"); wr(bus_s, 16#1CE#, x"BA80");
                wr(bus_s, 16#1D0#, x"5800"); wr(bus_s, 16#1D2#, x"5300"); wr(bus_s, 16#1D4#, x"004D"); wr(bus_s, 16#1D6#, x"0033");
                wr(bus_s, 16#1D8#, x"003F"); wr(bus_s, 16#1DA#, x"0022"); wr(bus_s, 16#1DC#, x"0037"); wr(bus_s, 16#1DE#, x"001F");
                wr(bus_s, 16#1E0#, x"0033"); wr(bus_s, 16#1E2#, x"001B"); wr(bus_s, 16#1E4#, x"0000"); wr(bus_s, 16#1E6#, x"0000");
                wr(bus_s, 16#1E8#, x"0000"); wr(bus_s, 16#1EA#, x"0000"); wr(bus_s, 16#1EC#, x"0000"); wr(bus_s, 16#1EE#, x"0000");
                wr(bus_s, 16#1F0#, x"0000"); wr(bus_s, 16#1F2#, x"0000"); wr(bus_s, 16#1F4#, x"001B"); wr(bus_s, 16#1F6#, x"0013");
                wr(bus_s, 16#1F8#, x"000C"); wr(bus_s, 16#1FA#, x"0006"); wr(bus_s, 16#1FC#, x"6000"); wr(bus_s, 16#1FE#, x"6000");
                wr(bus_s, 16#198#, x"FFFF"); wr(bus_s, 16#19A#, x"00FF");
            else
                wr(bus_s, 16#184#, rnd16); wr(bus_s, 16#186#, rnd16);
                wr(bus_s, 16#1A2#, h16(16#D800# + rnd(16#1000#)));
                for i in 0 to 31 loop
                    base := 16#1C0# + 2 * i; data := rnd16; legal(base, data); wr(bus_s, base, data);
                end loop;
                wr(bus_s, 16#198#, rnd16); wr(bus_s, 16#19A#, x"00" & rnd16(7 downto 0));
            end if;
            for vi in 0 to 23 loop
                base := vi * 16;
                wr(bus_s, base + 0, h16(rnd(16#4000#)));
                wr(bus_s, base + 2, h16(rnd(16#4000#)));
                wr(bus_s, base + 4, h16(16#200# + rnd(16#3000#)));
                wr(bus_s, base + 6, h16(16#200# + rnd(16#9000#)));
                if vi mod 2 = 0 then
                    wr(bus_s, base + 8, x"00" & rnd16(7 downto 0));        -- fast attack
                    data := rnd16; data(15 downto 14) := "00";             -- sustain rises
                    wr(bus_s, base + 10, data);
                else
                    wr(bus_s, base + 8, rnd16); wr(bus_s, base + 10, rnd16);
                end if;
                wr(bus_s, base + 14, h16(16#200# + rnd(16#9000#)));
            end loop;
            data := rnd16; wr(bus_s, 16#194#, data and x"0421"); wr(bus_s, 16#196#, x"0000");   -- a few noise voices
            data := rnd16; wr(bus_s, 16#190#, data and x"5554"); wr(bus_s, 16#192#, x"0000");   -- pitch modulation
            wr(bus_s, 16#188#, x"FFFF"); wr(bus_s, 16#18A#, x"00FF");
        end;
        -- Different guest histories for A and B, applied side by side:
        -- own register addresses and data per SPU, common pauses.
        procedure history(first_a, first_b : natural) is
            variable sa, sb : unsigned(31 downto 0);
            variable address_a, address_b, pause : natural;
            variable data_a, data_b : std_logic_vector(15 downto 0);
        begin
            state := to_unsigned(first_a * 7919 + 1, 32); init(ba); sa := state;
            state := to_unsigned(first_b * 7919 + 1, 32); init(bb); sb := state;
            for op in 1 to OPERATIONS loop
                state := sa;
                address_a := rnd(512) * 2; data_a := rnd16; legal(address_a, data_a);
                pause := 40 + rnd(400);
                sa := state; state := sb;
                address_b := rnd(512) * 2; data_b := rnd16; legal(address_b, data_b);
                sb := state;
                ba.addr<=to_unsigned(address_a,10); ba.wdata<=data_a; ba.wr<='1';
                bb.addr<=to_unsigned(address_b,10); bb.wdata<=data_b; bb.wr<='1';
                tick; ba.wr<='0'; bb.wr<='0';
                idle(pause);
            end loop;
        end;
        type tglobals is array(0 to 255) of std_logic_vector(15 downto 0);
        type tss is array(0 to 511) of std_logic_vector(31 downto 0);
        type tring is array(0 to 7) of std_logic_vector(31 downto 0);
        variable g : tglobals;
        variable s : tss;
        variable ring, ringb : tring;
        procedure bus_read(signal bus_s : inout tbus; address : natural) is
        begin
            bus_s.addr<=to_unsigned(address,10); bus_s.rd<='1'; tick; bus_s.rd<='0'; tick;
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
        procedure ss_write(signal bus_s : inout tbus; address : natural; value : std_logic_vector(31 downto 0)) is
        begin
            bus_s.ss_adr<=to_unsigned(address,9); bus_s.ss_wdata<=value; bus_s.ss_wren<='1'; tick; bus_s.ss_wren<='0'; tick;
        end;
        -- SPU RAM halfword through the savestate RAM port (the on-chip
        -- capture and reverb copies in spu_ram are only reachable this way)
        procedure ram_read2(address : natural; va, vb : out std_logic_vector(15 downto 0)) is
            variable waited : natural := 0;
            variable da, db : boolean := false;
        begin
            ba.ram_adr<=std_logic_vector(to_unsigned(address,19)); ba.ram_rnw<='1'; ba.ram_req<='1';
            bb.ram_adr<=std_logic_vector(to_unsigned(address,19)); bb.ram_rnw<='1'; bb.ram_req<='1';
            tick; ba.ram_req<='0'; bb.ram_req<='0';
            while not (da and db) loop
                tick; waited := waited + 1;
                if ramda = '1' and not da then da := true; va := ramqa; end if;
                if ramdb = '1' and not db then db := true; vb := ramqb; end if;
                assert waited < 1000 report "SPU RAM read never done" severity failure;
            end loop;
        end;
        procedure ram_write_b(address : natural; value : std_logic_vector(15 downto 0)) is
            variable waited : natural := 0;
        begin
            bb.ram_adr<=std_logic_vector(to_unsigned(address,19)); bb.ram_wdata<=value; bb.ram_rnw<='0'; bb.ram_req<='1'; tick; bb.ram_req<='0';
            while ramdb /= '1' loop tick; waited := waited + 1; assert waited < 1000 report "SPU RAM write never done" severity failure; end loop;
            bb.ram_rnw<='1';
        end;
        function bus_restored(address : natural) return boolean is
        begin
            return address < 16#200# and address /= 16#19C# and address /= 16#19E# and
                   address /= 16#1A8# and address /= 16#1AE# and address /= 16#1B8# and address /= 16#1BA#;
        end;
        -- the indexed words of the exact-voices port (the ring at 32-39 and
        -- the decoded-sample pages at 128-255 are handled separately)
        function ss_saved(address : natural) return boolean is
        begin
            return (address >= 256 and address < 304) or (address >= 320 and address < 370) or (address >= 384 and address < 480);
        end;
        procedure wr2(address : natural; value : std_logic_vector(15 downto 0)) is
        begin
            ba.addr<=to_unsigned(address,10); ba.wdata<=value; ba.wr<='1';
            bb.addr<=to_unsigned(address,10); bb.wdata<=value; bb.wr<='1';
            tick; ba.wr<='0'; bb.wr<='0'; tick;
        end;
        procedure rd2(address : natural) is
        begin
            ba.addr<=to_unsigned(address,10); ba.rd<='1'; bb.addr<=to_unsigned(address,10); bb.rd<='1';
            tick; ba.rd<='0'; bb.rd<='0'; tick;
            rd_compare <= true;
        end;
        procedure wait_dmareq(ok : out boolean) is
            variable waited : natural := 0;
        begin
            while a.dmareq /= '1' and waited < 6000 loop tick; waited := waited + 1; end loop;
            ok := a.dmareq = '1';
        end;
        variable v16, w16 : std_logic_vector(15 downto 0);
        variable v32, vb32 : std_logic_vector(31 downto 0);
        variable big, bigb : std_logic_vector(255 downto 0);
        variable waited, address, n, vfault : natural;
        variable onchip_copied : natural := 0;
        variable fpage, fadr : natural := 0;
        variable ss_diffs, reg_diffs, page_diffs, cap_diffs, ring_diffs : natural := 0;
        variable fault_applied, history_differ, cdnext_differ : natural := 0;
        variable manual_words, dma_w_bursts, dma_r_bursts, post_writes, post_reads, dma_timeouts : natural := 0;
        variable cnt_shadow : std_logic_vector(15 downto 0);
        variable data : std_logic_vector(15 downto 0);
        variable ok : boolean;
    begin
        assert ABORT_WORDS < 8 report "ABORT_WORDS must be below the ring length" severity failure;
        idle(20);
        reset<='0';
        idle(240);   -- context RAM clear
        history(SEED, SEED + 1000);
        -- quiet point on both
        ba.hold<='1'; bb.hold<='1';
        waited := 0;
        while not (qa = '1' and qb = '1') loop
            tick; waited := waited + 1;
            assert waited < 40000 report "SPU never reached the quiet point" severity failure;
        end loop;
        -- save A: guest registers, ring, indexed words
        for i in 0 to 16#FF# loop bus_read(ba, 2 * i); g(i) := a.rd; end loop;
        for k in 0 to 7 loop ss_read(ba, ssa, 32 + k, v32); ring(k) := v32; end loop;
        for i in 0 to 511 loop if ss_saved(i) then ss_read(ba, ssa, i, v32); s(i) := v32; end if; end loop;
        -- load B: SPU RAM first. The SDRAM-backed part is copied model to
        -- model; the on-chip capture and reverb copies through the port.
        mem_cmd <= 1; tick; mem_cmd <= 0; tick;
        for i in 0 to 16#7FF# loop ram_read2(2 * i, v16, w16); if v16 /= w16 then ram_write_b(2 * i, v16); onchip_copied := onchip_copied + 1; end if; end loop;
        for i in 16#38000# to 16#3FFFF# loop ram_read2(2 * i, v16, w16); if v16 /= w16 then ram_write_b(2 * i, v16); onchip_copied := onchip_copied + 1; end if; end loop;
        -- then guest writes (they set side state the SS writes then fix)
        for i in 0 to 16#FF# loop
            if bus_restored(2 * i) then bus_write(bb, 2 * i, g(i)); end if;
        end loop;
        -- the ring: 8 words, every access rotates
        for k in 0 to 7 loop big(32 * k + 31 downto 32 * k) := ring(k); end loop;
        if FAULT = 2 then
            for k in 0 to 7 loop ss_read(bb, ssb, 32 + k, v32); bigb(32 * k + 31 downto 32 * k) := v32; end loop;
            if bigb(243 downto 210) /= big(243 downto 210) then fault_applied := 1; end if;
            big(243 downto 210) := bigb(243 downto 210);
        end if;
        for k in 0 to 7 loop ss_write(bb, 32 + k, big(32 * k + 31 downto 32 * k)); end loop;
        -- records, volumes, envelope counters
        if FAULT = 3 then
            vfault := SEED mod 23;
            for attempt in 0 to 22 loop
                exit when s(384 + 4 * vfault) /= s(388 + 4 * vfault) or s(385 + 4 * vfault) /= s(389 + 4 * vfault) or
                          s(386 + 4 * vfault) /= s(390 + 4 * vfault) or s(387 + 4 * vfault) /= s(391 + 4 * vfault);
                vfault := (vfault + 1) mod 23;
            end loop;
            for k in 0 to 3 loop
                if s(384 + 4 * vfault + k) /= s(388 + 4 * vfault + k) then fault_applied := 1; end if;
                v32 := s(384 + 4 * vfault + k); s(384 + 4 * vfault + k) := s(388 + 4 * vfault + k); s(388 + 4 * vfault + k) := v32;
            end loop;
            report "EV fault_voice=" & natural'image(vfault);
        end if;
        for i in 0 to 511 loop if ss_saved(i) then ss_write(bb, i, s(i)); end if; end loop;
        -- Fault 1 drops the word holding a sample the next output of a
        -- sounding voice interpolates (sample position + 2), where A and B differ.
        if FAULT = 1 then
            for attempt in 0 to 23 loop
                vfault := (FAULT_PICK + attempt) mod 24;
                n := to_integer(unsigned(s(386 + 4 * vfault)(17 downto 12))) + 2;
                fpage := vfault / 16 + 2 * ((n mod 4) / 2);
                fadr := 128 + (vfault mod 16) * 8 + (n / 4) mod 8;
                ss_write(ba, 63, std_logic_vector(to_unsigned(fpage, 32)));
                ss_write(bb, 63, std_logic_vector(to_unsigned(fpage, 32)));
                ss_read(ba, ssa, fadr, v32); ss_read(bb, ssb, fadr, vb32);
                if n mod 2 = 0 then ok := v32(15 downto 0) /= vb32(15 downto 0); else ok := v32(31 downto 16) /= vb32(31 downto 16); end if;
                if ok and s(386 + 4 * vfault)(28 downto 26) /= "000" then
                    fault_applied := 1;
                    report "EV fault_voice=" & natural'image(vfault) & " fault_page=" & natural'image(fpage) & " fault_word=" & natural'image(fadr);
                    exit;
                end if;
            end loop;
        end if;
        -- Decoded sample RAM: four 128-word quadrants behind page register 63.
        for page in 0 to 3 loop
            ss_write(ba, 63, std_logic_vector(to_unsigned(page, 32)));
            ss_write(bb, 63, std_logic_vector(to_unsigned(page, 32)));
            for adr in 128 to 255 loop
                ss_read(ba, ssa, adr, v32);
                if not (fault_applied = 1 and FAULT = 1 and page = fpage and adr = fadr) then ss_write(bb, adr, v32); end if;
            end loop;
        end loop;
        assert qa = '1' and qb = '1' report "SPU left the quiet point" severity failure;
        -- positive control: the state this port does not carry really differs
        if a_d1 /= b_d1 then history_differ := history_differ + 1; end if;
        if a_d2 /= b_d2 then history_differ := history_differ + 1; end if;
        if a_d3 /= b_d3 then history_differ := history_differ + 1; end if;
        if a_d4 /= b_d4 then history_differ := history_differ + 1; end if;
        if a_e1 /= b_e1 then history_differ := history_differ + 1; end if;
        if a_e2 /= b_e2 then history_differ := history_differ + 1; end if;
        if a_e3 /= b_e3 then history_differ := history_differ + 1; end if;
        if a_e4 /= b_e4 then history_differ := history_differ + 1; end if;
        if a_dnl /= b_dnl then history_differ := history_differ + 1; end if;
        if a_dnr /= b_dnr then history_differ := history_differ + 1; end if;
        if a_cdl /= b_cdl or a_cdr /= b_cdr then cdnext_differ := 1; end if;
        report "EV history_regs_differ=" & natural'image(history_differ) & " rvLastValid_A=" & to_string(a_rvv) & " rvLastValid_B=" & to_string(b_rvv) &
               " dac_differs_at_release=" & boolean'image(a.l /= b.l or a.r /= b.r) & " cd_next_differs_at_release=" & natural'image(cdnext_differ) &
               " mbase=" & to_hstring(a_mbase) & " cnt=" & to_hstring(a_cnt) & " fault_applied=" & natural'image(fault_applied) &
               " onchip_ram_words_copied=" & natural'image(onchip_copied);
        -- Abandoned ring reads must rotate back before normal processing:
        -- B is released early and needs 8 - ABORT_WORDS clocks to realign.
        if ABORT_WORDS > 0 then
            for adr in 32 to 31 + ABORT_WORDS loop ss_read(bb, ssb, adr, v32); end loop;
            bb.hold <= '0';
            idle(8 - ABORT_WORDS);
        end if;
        -- release both on the same clock
        compare<=true; released<=true;
        ba.hold<='0'; bb.hold<='0';
        elapsed := 0;
        if TRAFFIC = 0 then
            idle(SAMPLES * 768);
        else
            state := to_unsigned(SEED * 104729 + 17, 32);
            cnt_shadow := g(16#D5#);
            while elapsed < SAMPLES * 768 loop
                case rnd(16) is
                    when 0 to 5 =>
                        address := rnd(512) * 2; data := rnd16;
                        legal(address, data);
                        if address = 16#1AA# then cnt_shadow := data; end if;
                        wr2(address, data); post_writes := post_writes + 1;
                    when 6 to 8 =>
                        rd2(rnd(512) * 2); post_reads := post_reads + 1;
                    when 9 | 10 =>   -- manual write through the FIFO
                        address := 16#200# + rnd(16#9000#);
                        wr2(16#1A6#, h16(address));
                        data := cnt_shadow; data(5 downto 4) := "01";
                        if rnd(2) = 0 then   -- SPU IRQ on an address this transfer reaches
                            wr2(16#1A4#, h16(address + rnd(3))); data(6) := '0'; wr2(16#1AA#, data); data(6) := '1';
                        end if;
                        wr2(16#1AA#, data);
                        n := 1 + rnd(24);
                        for k in 1 to n loop wr2(16#1A8#, rnd16); end loop;
                        manual_words := manual_words + n;
                        idle(768 + rnd(1600));
                        wr2(16#1AA#, cnt_shadow);
                    when 11 | 12 =>  -- DMA write
                        address := 16#200# + rnd(16#9000#);
                        wr2(16#1A6#, h16(address));
                        data := cnt_shadow; data(5 downto 4) := "10";
                        if rnd(2) = 0 then   -- SPU IRQ on an address this transfer reaches
                            wr2(16#1A4#, h16(address + rnd(3))); data(6) := '0'; wr2(16#1AA#, data); data(6) := '1';
                        end if;
                        wr2(16#1AA#, data);
                        for burst in 1 to 1 + rnd(3) loop
                            wait_dmareq(ok);
                            if ok then
                                n := 1 + rnd(48);
                                dma_wr <= '1';
                                for k in 1 to n loop dma_wdata <= rnd16; tick; end loop;
                                dma_wr <= '0';
                                dma_w_bursts := dma_w_bursts + 1;
                            else
                                dma_timeouts := dma_timeouts + 1;
                            end if;
                            idle(rnd(300));
                        end loop;
                        wait_dmareq(ok);     -- FIFO drained
                        wr2(16#1AA#, cnt_shadow);
                    when 13 | 14 =>  -- DMA read (one in three from the capture buffers)
                        if rnd(3) = 0 then wr2(16#1A6#, h16(rnd(16#200#))); else wr2(16#1A6#, h16(16#200# + rnd(16#9000#))); end if;
                        data := cnt_shadow; data(5 downto 4) := "11"; wr2(16#1AA#, data);
                        for burst in 1 to 1 + rnd(3) loop
                            wait_dmareq(ok);
                            if ok then
                                n := 1 + rnd(32);
                                for k in 1 to n loop
                                    dma_rd <= '1'; tick; dma_rd <= '0';
                                    if rnd(4) = 0 then idle(1 + rnd(3)); end if;
                                end loop;
                                dma_r_bursts := dma_r_bursts + 1;
                            else
                                dma_timeouts := dma_timeouts + 1;
                            end if;
                            idle(rnd(300));
                        end loop;
                        wr2(16#1AA#, cnt_shadow);
                    when others => null;
                end case;
                idle(20 + rnd(300));
            end loop;
            wr2(16#1AA#, cnt_shadow);
            idle(4 * 768);
        end if;
        compare<=false;
        tick;
        -- hidden state at the end: hold both and compare what the port carries
        ba.hold<='1'; bb.hold<='1';
        waited := 0;
        while not (qa = '1' and qb = '1') loop
            tick; waited := waited + 1;
            assert waited < 40000 report "SPU never reached the quiet point at the end" severity failure;
        end loop;
        for k in 0 to 7 loop
            ss_read(ba, ssa, 32 + k, v32); ss_read(bb, ssb, 32 + k, vb32);
            if v32 /= vb32 then
                if ring_diffs < 4 then report "EVEND ring word " & natural'image(k) & " A=" & to_hstring(v32) & " B=" & to_hstring(vb32); end if;
                ring_diffs := ring_diffs + 1;
            end if;
        end loop;
        for i in 0 to 511 loop
            if ss_saved(i) then
                ss_read(ba, ssa, i, v32); ss_read(bb, ssb, i, vb32);
                if v32 /= vb32 then
                    if ss_diffs < 4 then report "EVEND port word " & natural'image(i) & " A=" & to_hstring(v32) & " B=" & to_hstring(vb32); end if;
                    ss_diffs := ss_diffs + 1;
                end if;
            end if;
        end loop;
        for page in 0 to 3 loop
            ss_write(ba, 63, std_logic_vector(to_unsigned(page, 32)));
            ss_write(bb, 63, std_logic_vector(to_unsigned(page, 32)));
            for adr in 128 to 255 loop
                ss_read(ba, ssa, adr, v32); ss_read(bb, ssb, adr, vb32);
                if v32 /= vb32 then
                    if page_diffs < 4 then report "EVEND sample page " & natural'image(page) & " word " & natural'image(adr) & " A=" & to_hstring(v32) & " B=" & to_hstring(vb32); end if;
                    page_diffs := page_diffs + 1;
                end if;
            end loop;
        end loop;
        for i in 0 to 16#FF# loop   -- every guest register, ENDX and STAT included
            ba.addr<=to_unsigned(2 * i,10); ba.rd<='1'; bb.addr<=to_unsigned(2 * i,10); bb.rd<='1'; tick; ba.rd<='0'; bb.rd<='0'; tick;
            if a.rd /= b.rd then
                if reg_diffs < 4 then report "EVEND register " & to_hstring(to_unsigned(2 * i, 12)) & " A=" & to_hstring(a.rd) & " B=" & to_hstring(b.rd); end if;
                reg_diffs := reg_diffs + 1;
            end if;
        end loop;
        for i in 0 to 16#7FF# loop  -- the capture buffers, read back
            ram_read2(2 * i, v16, w16);
            if v16 /= w16 then
                if cap_diffs < 4 then report "EVEND capture RAM " & to_hstring(to_unsigned(2 * i, 16)) & " A=" & to_hstring(v16) & " B=" & to_hstring(w16); end if;
                cap_diffs := cap_diffs + 1;
            end if;
        end loop;
        if is_x(a_mbase) then mem_base_word <= 0; else mem_base_word <= to_integer(unsigned(a_mbase)) * 4; end if;
        tick; mem_cmd <= 2; tick; mem_cmd <= 0; tick; tick;
        dump_req <= true;
        tick; tick;
        report "EV end_ring_diffs=" & natural'image(ring_diffs) & " end_port_diffs=" & natural'image(ss_diffs) &
               " end_sample_page_diffs=" & natural'image(page_diffs) & " end_register_diffs=" & natural'image(reg_diffs) &
               " end_capture_ram_diffs=" & natural'image(cap_diffs) & " end_ram_outside_diffs=" & natural'image(mem_out_diffs) &
               " end_ram_reverb_area_diffs=" & natural'image(mem_in_diffs);
        report "EV post_guest_writes=" & natural'image(post_writes) & " post_guest_reads=" & natural'image(post_reads) &
               " manual_fifo_words=" & natural'image(manual_words) & " dma_write_bursts=" & natural'image(dma_w_bursts) &
               " dma_read_bursts=" & natural'image(dma_r_bursts) & " dma_request_timeouts=" & natural'image(dma_timeouts);
        report "EV done=1";
        finish;
    end process;
    end block;
end architecture;
