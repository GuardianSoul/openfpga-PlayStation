"""Generate the full-system fixture around the observed pinned entity."""
from pathlib import Path
from prepare_resource_targets import SYSTEM_FIXED, ports, joypad_fields


def system_bench(directory):
    declarations=ports('psx_top'); fixed=dict(SYSTEM_FIXED)
    fixed.update(region='"00"',biosregion='"00"',LIDopen="'1'",hasCD="'0'",isPal="'0'")
    header='''library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
use std.textio.all;
use std.env.all;
use work.pexport.all;
use work.pJoypad.all;
library tb;
entity tb_psx_system is
    generic (BOOT_FILE : string := "boot.hex8"; EXPECTED_FILE : string := "expected.hex";
        EXPECTED_CHECKS : natural := 2648; CHECK_DIAGNOSTIC : boolean := true;
        MAX_CYCLES : positive := 2000000; TRACE_LIMIT : natural := 10000);
end entity;
architecture test of tb_psx_system is
'''
    text=header
    for name,direction,kind in declarations:
        if kind=='joypad_t':
            pad=int(name[-1]); values=[]
            for field,fieldkind in joypad_fields():
                value="(others=>'0')" if fieldkind.startswith('signed') else "'0'"
                if pad==1 and field in ('PadPortEnable','PadPortDigital'): value="'1'"
                values.append(field+'=>'+value)
            initial='('+','.join(values)+')'
        elif name.startswith('clk'): initial="'1'"
        elif name=='reset': initial="'1'"
        elif direction=='in' and fixed.get(name) not in (None,'open'): initial=fixed[name]
        elif kind=='std_logic': initial="'0'"
        elif kind.startswith('integer'): initial='0'
        else: initial="(others=>'0')"
        text+=f'    signal {name} : {kind} := {initial};\n'
    text+='''    signal sim_cpu_done,sim_reset : std_logic;
    signal sim_cpu_state : cpu_export_type;
    signal completed : boolean := false;
    signal retired,ram_requests,vram_requests,spu_requests,cache_beats,nonzero_audio : natural := 0;
    signal cycles : natural := 0;
    signal last_pc,last_opcode : unsigned(31 downto 0) := (others=>'0');
begin
    -- Exact related nominal PSX clocks (33.8688/67.7376/101.6064 MHz).
    clk1x <= not clk1x after 14762550 fs;
    clk2x <= not clk2x after 7381275 fs;
    clk3x <= not clk3x after 4920850 fs;
    clkvid <= not clkvid after 9312170 fs;
    core : entity work.psx_top generic map(is_simu=>'1') port map (
'''
    text+=',\n'.join(f'        {name}=>{name}' for name,_,_ in declarations)
    text+=''',
        sim_cpu_done=>sim_cpu_done,sim_cpu_state=>sim_cpu_state,sim_reset=>sim_reset
    );
    main_memory : entity tb.sdram_model3x
        generic map (DOREFRESH=>'1',SCRIPTLOADING=>'0',FILELOADING=>'1',INITFILE=>BOOT_FILE,
                     INITADDR=>8388608,MEMORY_BYTES=>16777216)
        port map (clk=>clk1x,clk3x=>clk3x,refresh=>ram_refresh,addr=>"00" & ram_Adr,
            req=>ram_ena,ram_dma=>ram_dma,ram_dmacnt=>ram_cntDMA,ram_iscache=>ram_cache,
            rnw=>ram_rnw,be=>ram_be,di=>ram_dataWrite,do=>open,do32=>ram_dataRead32,done=>ram_done,
            cache_wr=>cache_wr,cache_data=>cache_data,cache_addr=>cache_addr,
            dma_wr=>dma_wr,dma_data=>dma_data,reqprocessed=>dma_reqprocessed,ram_idle=>open,
            ram_dmafifo_adr=>ram_dmafifo_adr,ram_dmafifo_data=>ram_dmafifo_data,
            ram_dmafifo_empty=>ram_dmafifo_empty,ram_dmafifo_read=>ram_dmafifo_read,
            fileSize=>open,exe_initial_pc=>open,exe_initial_gp=>open,exe_load_address=>open,
            exe_file_size=>open,exe_stackpointer=>open);
    spu_memory : entity tb.sdram_model3x
        generic map (DOREFRESH=>'0',SCRIPTLOADING=>'0',FILELOADING=>'0',MEMORY_BYTES=>524288)
        port map (clk=>clk1x,clk3x=>clk3x,refresh=>'0',addr=>"00000000" & spuram_Adr,
            req=>spuram_ena,ram_dma=>'0',ram_dmacnt=>"00",ram_iscache=>'0',rnw=>spuram_rnw,
            be=>spuram_be,di=>spuram_dataWrite,do=>open,do32=>spuram_dataRead,done=>spuram_done,
            cache_wr=>open,cache_data=>open,cache_addr=>open,dma_wr=>open,dma_data=>open,
            reqprocessed=>open,ram_idle=>open,ram_dmafifo_adr=>(others=>'0'),ram_dmafifo_data=>(others=>'0'),
            ram_dmafifo_empty=>'1',ram_dmafifo_read=>open,fileSize=>open,
            exe_initial_pc=>open,exe_initial_gp=>open,exe_load_address=>open,exe_file_size=>open,exe_stackpointer=>open);
    video_memory : entity tb.ddrram_model generic map (SLOWTIMING=>15)
        port map (DDRAM_CLK=>clk2x,DDRAM_BUSY=>ddr3_BUSY,DDRAM_BURSTCNT=>ddr3_BURSTCNT,
            DDRAM_ADDR=>"0011" & ddr3_ADDR(27 downto 3),DDRAM_DOUT=>ddr3_DOUT,
            DDRAM_DOUT_READY=>ddr3_DOUT_READY,DDRAM_RD=>ddr3_RD,DDRAM_DIN=>ddr3_DIN,
            DDRAM_BE=>ddr3_BE,DDRAM_WE=>ddr3_WE);
    process begin
        for i in 1 to 100 loop wait until falling_edge(clk1x); end loop;
        reset<='0'; wait;
    end process;
    process(clk1x)
        file trace : text open write_mode is "cpu-trace.csv";
        variable row : line;
        variable samples,last_audio : natural := 0;
    begin
        if rising_edge(clk1x) then
            cycles<=cycles+1;
            if ram_ena='1' then ram_requests<=ram_requests+1; end if;
            if spuram_ena='1' then spu_requests<=spu_requests+1; end if;
            if sound_out_left/=x"0000" or sound_out_right/=x"0000" then nonzero_audio<=nonzero_audio+1; end if;
            if sim_reset='0' and sim_cpu_done='1' then
                retired<=retired+1;last_pc<=sim_cpu_state.pc;last_opcode<=sim_cpu_state.opcode;
                if samples<TRACE_LIMIT then
                    write(row,cycles);write(row,string'(","));write(row,to_hstring(sim_cpu_state.pc));
                    write(row,string'(","));write(row,to_hstring(sim_cpu_state.opcode));
                    write(row,string'(","));write(row,to_hstring(sim_cpu_state.cause));writeline(trace,row);
                    samples:=samples+1;
                end if;
            end if;
        end if;
    end process;
    process(clk3x) begin
        if rising_edge(clk3x) and cache_wr/="0000" then cache_beats<=cache_beats+1; end if;
    end process;
    process(clk2x) begin
        if rising_edge(clk2x) and (ddr3_RD='1' or ddr3_WE='1') then vram_requests<=vram_requests+1; end if;
    end process;
    diagnostic : if CHECK_DIAGNOSTIC generate
        process(clk1x)
            file expected : text open read_mode is EXPECTED_FILE;
            variable row : line;
            variable value : std_logic_vector(31 downto 0);
            variable checks : natural := 0;
        begin
            if rising_edge(clk1x) and ram_ena='1' and ram_rnw='0' then
                if unsigned(ram_Adr)>=4096 and unsigned(ram_Adr)<4096+EXPECTED_CHECKS*4 then
                    assert to_integer(unsigned(ram_Adr))=4096+checks*4
                        report "System result stores missing/reordered" severity failure;
                    assert not endfile(expected) report "Too many result stores" severity failure;
                    readline(expected,row);hread(row,value);
                    assert ram_dataWrite=value report "System result mismatch index=" & natural'image(checks) &
                        " got=" & to_hstring(ram_dataWrite) & " expected=" & to_hstring(value) severity failure;
                    checks:=checks+1;
                elsif unsigned(ram_Adr)=16#ff00# then
                    assert ram_dataWrite=x"600df00d" and checks=EXPECTED_CHECKS and endfile(expected)
                        report "System diagnostic incomplete" severity failure;
                    completed<=true;
                end if;
            end if;
        end process;
    end generate;
    process
    begin
        wait until falling_edge(clk1x);
        if completed or cycles>=MAX_CYCLES then
            report "SYSTEM_RESULT cycles=" & natural'image(cycles) & " retired=" & natural'image(retired) &
                " ram=" & natural'image(ram_requests) & " vram=" & natural'image(vram_requests) &
                " spu=" & natural'image(spu_requests) & " cache_beats=" & natural'image(cache_beats) &
                " audio_nonzero_cycles=" & natural'image(nonzero_audio) & " pc=" & to_hstring(last_pc) &
                " opcode=" & to_hstring(last_opcode) & " completed=" & boolean'image(completed);
            if CHECK_DIAGNOSTIC then assert completed report "System diagnostic timeout" severity failure; end if;
            finish;
        end if;
    end process;
end architecture;
'''
    path=Path(directory)/'tb_psx_system.vhd';path.write_text(text);return path
