library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
use std.env.all;
library optimized;

-- Compare reset-only specialization to upstream at every 2x tick.
-- Independent counters verify every cleared word and the four header reads.
entity tb_mister_reset_only is
    generic (FASTSIM : std_logic := '0'; ROUNDS : positive := 1; SEED : natural := 0);
end entity;
architecture test of tb_mister_reset_only is
    type t_output is record
        reset_out : std_logic;
        ss_reset : std_logic;
        load_done : std_logic;
        validSStates : std_logic_vector(3 downto 0);
        savestate_busy : std_logic;
        savestate_pause : std_logic;
        ddr3_savestate : std_logic;
        SS_DataWrite : std_logic_vector(31 downto 0);
        SS_Adr : unsigned(18 downto 0);
        SS_wren : std_logic_vector(16 downto 0);
        SS_rden : std_logic_vector(16 downto 0);
        loading_savestate : std_logic;
        saving_savestate : std_logic;
        ddr3_BURSTCNT : std_logic_vector(7 downto 0);
        ddr3_ADDR : std_logic_vector(25 downto 0);
        ddr3_DIN : std_logic_vector(63 downto 0);
        ddr3_BE : std_logic_vector(7 downto 0);
        ddr3_WE : std_logic;
        ddr3_RD : std_logic;
        SS_SPURAM_dataWrite : std_logic_vector(15 downto 0);
        SS_SPURAM_Adr : std_logic_vector(18 downto 0);
        SS_SPURAM_request : std_logic;
        SS_SPURAM_rnw : std_logic;
    end record;
    type t_pair is array(0 to 1) of t_output;
    signal o : t_pair;
    signal clk1x,clk2x : std_logic := '1';
    signal clock_toggle,sampled_toggle,clk2xIndex : std_logic := '0';
    signal ce,ss_idle,system_paused : std_logic := '0';
    signal reset_in,hps_busy,ddr_busy,ddr_ready,ram_done,spu_done : std_logic := '0';
    signal ddr_data : std_logic_vector(63 downto 0) := (others=>'0');
begin
    clk1x <= not clk1x after 15 ns;
    clk2x <= not clk2x after 7500 ps;
    process(clk1x) begin
        if rising_edge(clk1x) then clock_toggle<=not clock_toggle; end if;
    end process;
    process(clk2x) begin
        if rising_edge(clk2x) then
            sampled_toggle<=clock_toggle; clk2xIndex<='0';
            if sampled_toggle=clock_toggle then clk2xIndex<='1'; end if;
        end if;
    end process;
    dut0 : entity work.savestates
        generic map (FASTSIM=>FASTSIM,Softmap_SaveState_ADDR=>58720256)
        port map (
            clk1x=>clk1x,
            clk2x=>clk2x,
            clk2xIndex=>clk2xIndex,
            ce=>ce,
            reset_in=>reset_in,
            reset_out=>o(0).reset_out,
            ss_reset=>o(0).ss_reset,
            hps_busy=>hps_busy,
            loadExe=>'0',
            load_done=>o(0).load_done,
            validSStates=>o(0).validSStates,
            savestate_number=>0,
            increaseSSHeaderCount=>'0',
            save=>'0',
            load=>'0',
            savestate_address=>0,
            savestate_busy=>o(0).savestate_busy,
            SS_idle=>ss_idle,
            system_paused=>system_paused,
            savestate_pause=>o(0).savestate_pause,
            ddr3_savestate=>o(0).ddr3_savestate,
            useSPUSDRAM=>'1',
            SS_DataWrite=>o(0).SS_DataWrite,
            SS_Adr=>o(0).SS_Adr,
            SS_wren=>o(0).SS_wren,
            SS_rden=>o(0).SS_rden,
            SS_DataRead_CPU=>(others=>'0'),
            SS_DataRead_GPU=>(others=>'0'),
            SS_DataRead_GPUTiming=>(others=>'0'),
            SS_DataRead_DMA=>(others=>'0'),
            SS_DataRead_GTE=>(others=>'0'),
            SS_DataRead_JOYPAD=>(others=>'0'),
            SS_DataRead_MDEC=>(others=>'0'),
            SS_DataRead_MEMORY=>(others=>'0'),
            SS_DataRead_TIMER=>(others=>'0'),
            SS_DataRead_SOUND=>(others=>'0'),
            SS_DataRead_IRQ=>(others=>'0'),
            SS_DataRead_SIO=>(others=>'0'),
            SS_DataRead_SCP=>(others=>'0'),
            SS_DataRead_CD=>(others=>'0'),
            sdram_done=>ram_done,
            loading_savestate=>o(0).loading_savestate,
            saving_savestate=>o(0).saving_savestate,
            ddr3_BUSY=>ddr_busy,
            ddr3_DOUT=>ddr_data,
            ddr3_DOUT_READY=>ddr_ready,
            ddr3_BURSTCNT=>o(0).ddr3_BURSTCNT,
            ddr3_ADDR=>o(0).ddr3_ADDR,
            ddr3_DIN=>o(0).ddr3_DIN,
            ddr3_BE=>o(0).ddr3_BE,
            ddr3_WE=>o(0).ddr3_WE,
            ddr3_RD=>o(0).ddr3_RD,
            ram_done=>ram_done,
            ram_data=>(others=>'0'),
            SS_SPURAM_dataWrite=>o(0).SS_SPURAM_dataWrite,
            SS_SPURAM_Adr=>o(0).SS_SPURAM_Adr,
            SS_SPURAM_request=>o(0).SS_SPURAM_request,
            SS_SPURAM_rnw=>o(0).SS_SPURAM_rnw,
            SS_SPURAM_dataRead=>(others=>'0'),
            SS_SPURAM_done=>spu_done
        );
    dut1 : entity optimized.savestates
        generic map (FASTSIM=>FASTSIM,Softmap_SaveState_ADDR=>58720256)
        port map (
            clk1x=>clk1x,
            clk2x=>clk2x,
            clk2xIndex=>clk2xIndex,
            ce=>ce,
            reset_in=>reset_in,
            reset_out=>o(1).reset_out,
            ss_reset=>o(1).ss_reset,
            hps_busy=>hps_busy,
            loadExe=>'0',
            load_done=>o(1).load_done,
            validSStates=>o(1).validSStates,
            savestate_number=>0,
            increaseSSHeaderCount=>'0',
            save=>'0',
            load=>'0',
            savestate_address=>0,
            savestate_busy=>o(1).savestate_busy,
            SS_idle=>ss_idle,
            system_paused=>system_paused,
            savestate_pause=>o(1).savestate_pause,
            ddr3_savestate=>o(1).ddr3_savestate,
            useSPUSDRAM=>'1',
            SS_DataWrite=>o(1).SS_DataWrite,
            SS_Adr=>o(1).SS_Adr,
            SS_wren=>o(1).SS_wren,
            SS_rden=>o(1).SS_rden,
            SS_DataRead_CPU=>(others=>'0'),
            SS_DataRead_GPU=>(others=>'0'),
            SS_DataRead_GPUTiming=>(others=>'0'),
            SS_DataRead_DMA=>(others=>'0'),
            SS_DataRead_GTE=>(others=>'0'),
            SS_DataRead_JOYPAD=>(others=>'0'),
            SS_DataRead_MDEC=>(others=>'0'),
            SS_DataRead_MEMORY=>(others=>'0'),
            SS_DataRead_TIMER=>(others=>'0'),
            SS_DataRead_SOUND=>(others=>'0'),
            SS_DataRead_IRQ=>(others=>'0'),
            SS_DataRead_SIO=>(others=>'0'),
            SS_DataRead_SCP=>(others=>'0'),
            SS_DataRead_CD=>(others=>'0'),
            sdram_done=>ram_done,
            loading_savestate=>o(1).loading_savestate,
            saving_savestate=>o(1).saving_savestate,
            ddr3_BUSY=>ddr_busy,
            ddr3_DOUT=>ddr_data,
            ddr3_DOUT_READY=>ddr_ready,
            ddr3_BURSTCNT=>o(1).ddr3_BURSTCNT,
            ddr3_ADDR=>o(1).ddr3_ADDR,
            ddr3_DIN=>o(1).ddr3_DIN,
            ddr3_BE=>o(1).ddr3_BE,
            ddr3_WE=>o(1).ddr3_WE,
            ddr3_RD=>o(1).ddr3_RD,
            ram_done=>ram_done,
            ram_data=>(others=>'0'),
            SS_SPURAM_dataWrite=>o(1).SS_SPURAM_dataWrite,
            SS_SPURAM_Adr=>o(1).SS_SPURAM_Adr,
            SS_SPURAM_request=>o(1).SS_SPURAM_request,
            SS_SPURAM_rnw=>o(1).SS_SPURAM_rnw,
            SS_SPURAM_dataRead=>(others=>'0'),
            SS_SPURAM_done=>spu_done
        );
    process
        variable ticks,round_no,idle_ticks,start_tick : natural := 0;
        variable reads,vram_words,spu_words,ram_words,scratch_words : natural := 0;
        variable ddr_wait,ram_wait,spu_wait,ram_hold,spu_hold : natural := 0;
        variable ddr_is_read,spu_seen : boolean := false;
        variable expected_vram,expected_spu,expected_ram : natural := 0;
        procedure tick is begin wait until falling_edge(clk2x); wait for 1 ns; end;
    begin
        if FASTSIM='0' then
            expected_vram:=131072; expected_spu:=262144; expected_ram:=524288;
        end if;
        for n in 1 to 8 loop tick; end loop;
        reset_in<='1'; ce<='1'; start_tick:=ticks;
        loop
            tick; ticks:=ticks+1;
            assert ticks<30000000 report "Reset comparison timeout" severity failure;
            assert o(0).reset_out=o(1).reset_out and o(0).ss_reset=o(1).ss_reset and
                o(0).load_done=o(1).load_done and o(0).validSStates=o(1).validSStates and
                o(0).savestate_busy=o(1).savestate_busy and o(0).savestate_pause=o(1).savestate_pause and
                o(0).ddr3_savestate=o(1).ddr3_savestate and o(0).loading_savestate=o(1).loading_savestate and
                o(0).saving_savestate=o(1).saving_savestate and o(0).SS_wren=o(1).SS_wren and
                o(0).SS_rden=o(1).SS_rden and o(0).ddr3_RD=o(1).ddr3_RD and o(0).ddr3_WE=o(1).ddr3_WE and
                o(0).SS_SPURAM_request=o(1).SS_SPURAM_request
                report "Reset control mismatch tick=" & natural'image(ticks) severity failure;
            if o(0).ddr3_RD='1' or o(0).ddr3_WE='1' then
                assert o(0).ddr3_ADDR=o(1).ddr3_ADDR and o(0).ddr3_BURSTCNT=o(1).ddr3_BURSTCNT
                    report "Reset DDR request mismatch" severity failure;
            end if;
            if o(0).ddr3_WE='1' then
                assert o(0).ddr3_DIN=o(1).ddr3_DIN and o(0).ddr3_BE=o(1).ddr3_BE
                    report "Reset DDR write mismatch" severity failure;
            end if;
            if unsigned(o(0).SS_wren)>0 then
                assert o(0).SS_Adr=o(1).SS_Adr and o(0).SS_DataWrite=o(1).SS_DataWrite
                    report "Reset SS payload mismatch" severity failure;
                assert o(0).SS_DataWrite=x"00000000" report "Nonzero guest reset write" severity failure;
                assert o(0).SS_wren=std_logic_vector(to_unsigned(2**12,17)) or
                       o(0).SS_wren=std_logic_vector(to_unsigned(2**16,17))
                    report "Reset wrote unexpected subsystem" severity failure;
            end if;
            if o(0).SS_SPURAM_request='1' then
                assert o(0).SS_SPURAM_Adr=o(1).SS_SPURAM_Adr and
                    o(0).SS_SPURAM_dataWrite=o(1).SS_SPURAM_dataWrite and
                    o(0).SS_SPURAM_rnw=o(1).SS_SPURAM_rnw
                    report "Reset SPU payload mismatch" severity failure;
            end if;
            assert o(0).SS_rden=(o(0).SS_rden'range=>'0') report "Reset requested SS readback" severity failure;
            assert o(0).loading_savestate='0' and o(0).saving_savestate='0'
                report "Reset unexpectedly entered state transfer" severity failure;

            if ticks-start_tick=12 then reset_in<='0'; end if;
            if ticks-start_tick=49 then system_paused<='1'; end if;
            if ticks-start_tick=67 then ss_idle<='1'; end if;
            if (ticks+SEED) mod 7<2 then ce<='0'; else ce<='1'; end if;
            if (ticks+SEED) mod 11<3 then hps_busy<='1'; else hps_busy<='0'; end if;

            ddr_ready<='0'; ram_done<='0'; spu_done<='0';
            if ram_hold>0 then ram_done<='1'; ram_hold:=ram_hold-1; end if;
            if spu_hold>0 then spu_done<='1'; spu_hold:=spu_hold-1; end if;
            if ddr_wait>0 then
                ddr_wait:=ddr_wait-1;
                if ddr_wait=0 then
                    ddr_busy<='0';
                    if ddr_is_read then
                        ddr_ready<='1';
                        if reads mod 2=0 then ddr_data<=x"000FFFFE01234567";
                        else ddr_data<=x"DEADBEEF89ABCDEF"; end if;
                    end if;
                end if;
            elsif o(0).ddr3_RD='1' or o(0).ddr3_WE='1' then
                assert o(0).ddr3_BURSTCNT=x"01" report "Reset DDR burst count" severity failure;
                ddr_is_read:=o(0).ddr3_RD='1';
                if ddr_is_read then
                    assert to_integer(unsigned(o(0).ddr3_ADDR))=58720256+(reads mod 4)*1048576
                        report "Reset header address" severity failure;
                    reads:=reads+1;
                else
                    assert expected_vram>0 report "FASTSIM reset wrote VRAM" severity failure;
                    assert to_integer(unsigned(o(0).ddr3_ADDR))=2*(vram_words mod expected_vram)
                        report "Reset VRAM address sequence" severity failure;
                    assert o(0).ddr3_DIN=x"0000000000000000" and o(0).ddr3_BE=x"FF"
                        report "Reset VRAM data/mask" severity failure;
                    vram_words:=vram_words+1;
                end if;
                ddr_busy<='1'; ddr_wait:=1+(ticks+SEED) mod 4;
            end if;
            if ram_wait>0 then
                ram_wait:=ram_wait-1;
                if ram_wait=0 then ram_done<='1'; ram_hold:=1; end if;
            end if;
            if spu_wait>0 then
                spu_wait:=spu_wait-1;
                if spu_wait=0 then spu_done<='1'; spu_hold:=1; end if;
            end if;
            -- One sample per 1x write cycle, including consecutive asserted cycles.
            if clk1x='1' and o(0).SS_wren(12)='1' then
                assert to_integer(o(0).SS_Adr)=scratch_words mod 256
                    report "Reset scratchpad address sequence" severity failure;
                scratch_words:=scratch_words+1;
            end if;
            if clk1x='1' and o(0).SS_wren(16)='1' then
                assert expected_ram>0 and ram_wait=0 report "Unexpected reset RAM transaction" severity failure;
                assert to_integer(o(0).SS_Adr)=ram_words mod expected_ram
                    report "Reset RAM address sequence" severity failure;
                ram_words:=ram_words+1; ram_wait:=1+(ticks+SEED) mod 3;
            end if;
            if o(0).SS_SPURAM_request='0' then spu_seen:=false;
            elsif not spu_seen then
                assert expected_spu>0 and spu_wait=0 report "Unexpected reset SPU transaction" severity failure;
                assert to_integer(unsigned(o(0).SS_SPURAM_Adr))=2*(spu_words mod expected_spu)
                    report "Reset SPU address sequence" severity failure;
                assert o(0).SS_SPURAM_dataWrite=x"0000" and o(0).SS_SPURAM_rnw='0'
                    report "Reset SPU payload" severity failure;
                spu_words:=spu_words+1; spu_seen:=true; spu_wait:=1+(ticks+SEED) mod 3;
            end if;
            if ticks-start_tick>100 and o(0).savestate_busy='0' then
                idle_ticks:=idle_ticks+1;
                if idle_ticks=24 then
                    round_no:=round_no+1;
                    assert reads=4*round_no and scratch_words=256*round_no and
                        vram_words=expected_vram*round_no and spu_words=expected_spu*round_no and
                        ram_words=expected_ram*round_no
                        report "Reset did not clear all guest memory" severity failure;
                    assert o(0).validSStates="1010" report "Reset header validity scan" severity failure;
                    if round_no=ROUNDS then
                        report "PASS reset-only FASTSIM=" & std_logic'image(FASTSIM) & " rounds=" & natural'image(ROUNDS) &
                            " header_reads=" & natural'image(reads) & " scratch_words=" & natural'image(scratch_words) &
                            " spu_halfwords=" & natural'image(spu_words) & " vram_doublewords=" & natural'image(vram_words) &
                            " ram_words=" & natural'image(ram_words) & " compared_ticks=" & natural'image(ticks);
                        finish;
                    end if;
                    reset_in<='1'; system_paused<='0'; ss_idle<='0'; start_tick:=ticks; idle_ticks:=0;
                end if;
            else idle_ticks:=0; end if;
        end loop;
    end process;
end architecture;
