library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
use std.textio.all;
use std.env.all;
use work.pexport.all;

-- Original MiSTer CPU and GTE execute an authored reset program with delayed
-- memory responses. No caches, actual BIOS, external PHY or game execution.
entity tb_mister_cpu_gte is
    generic (PROGRAM_FILE : string := "cpu-gte/cpu-gte-program.hex";
             EXPECTED_FILE : string := "cpu-gte/cpu-gte-expected.hex"; SEED : natural := 1;
             TRACE : boolean := false; PAUSES : boolean := false);
end entity;
architecture test of tb_mister_cpu_gte is
    type words is array(natural range <>) of std_logic_vector(31 downto 0);
    impure function load(path : string; size : natural) return words is
        file data : text open read_mode is path;
        variable row : line;
        variable memory : words(0 to size-1) := (others=>(others=>'0'));
        variable index : natural := 0;
    begin
        while not endfile(data) loop
            assert index<size report "Diagnostic image exceeds memory" severity failure;
            readline(data,row); hread(row,memory(index)); index:=index+1;
        end loop;
        return memory;
    end function;
    constant rom : words(0 to 131071) := load(PROGRAM_FILE,131072);
    constant expected : words(0 to 2643) := load(EXPECTED_FILE,2644);
    signal clk1x,clk2x,clk3x : std_logic := '1';
    signal reset,ss_reset : std_logic := '1';
    signal ce : std_logic := '1';
    signal clock_toggle,sampled_toggle,index2x : std_logic := '0';
    signal request,rnw,isdata,iscache,done,ram_done,ram_rnw : std_logic := '0';
    signal addr_instr,addr_data : unsigned(31 downto 0);
    signal reqsize : unsigned(1 downto 0);
    signal mask,oldtags : std_logic_vector(3 downto 0);
    signal write_data,read_data,ram_data : std_logic_vector(31 downto 0) := (others=>'0');
    signal gte_busy,gte_read_ena,gte_write_ena,gte_cmd_ena : std_logic;
    signal gte_read_addr,gte_write_addr : unsigned(5 downto 0);
    signal gte_read_data,gte_write_data,gte_cmd_data : unsigned(31 downto 0);
    signal cpu_done : std_logic;
    signal cpu_export : cpu_export_type;
    signal retired,commands : natural := 0;
begin
    clk1x <= not clk1x after 15 ns;
    clk2x <= not clk2x after 7500 ps;
    clk3x <= not clk3x after 5 ns;
    process(clk1x) begin
        if rising_edge(clk1x) then clock_toggle<=not clock_toggle; end if;
    end process;
    process(clk2x) begin
        if rising_edge(clk2x) then
            sampled_toggle<=clock_toggle; index2x<='0';
            if sampled_toggle=clock_toggle then index2x<='1'; end if;
            if reset='0' and ce='1' and index2x='1' and gte_cmd_ena='1' then commands<=commands+1; end if;
            if TRACE and reset='0' and ce='1' then
                if index2x='1' and gte_write_ena='1' then
                    report "GTE write " & to_hstring(gte_write_addr) & "=" & to_hstring(gte_write_data);
                end if;
                if gte_read_ena='1' then report "GTE read " & to_hstring(gte_read_addr) & "=" & to_hstring(gte_read_data); end if;
            end if;
        end if;
    end process;
    cpu : entity work.cpu port map (
        clk1x=>clk1x,clk2x=>clk2x,clk3x=>clk3x,ce=>ce,reset=>reset,
        TURBO=>'0',TURBO_CACHE=>'0',TURBO_CACHE50=>'0',irqRequest=>'0',dmaStallCPU=>'0',cpuPaused=>'0',
        error=>open,error2=>open,mem_request=>request,mem_rnw=>rnw,mem_isData=>isdata,mem_isCache=>iscache,
        mem_oldtagvalids=>oldtags,mem_addressInstr=>addr_instr,mem_addressData=>addr_data,
        mem_reqsize=>reqsize,mem_writeMask=>mask,mem_dataWrite=>write_data,
        mem_dataRead=>read_data,mem_done=>done,mem_fifofull=>'0',mem_tagvalids=>"0000",
        cache_wr=>"0000",cache_data=>(others=>'0'),cache_addr=>(others=>'0'),stallNext=>open,
        dma_cache_Adr=>(others=>'0'),dma_cache_data=>(others=>'0'),dma_cache_write=>'0',
        ram_done=>ram_done,ram_rnw=>ram_rnw,ram_dataRead=>ram_data,
        gte_busy=>gte_busy,gte_readEna=>gte_read_ena,gte_readAddr=>gte_read_addr,gte_readData=>gte_read_data,
        gte_writeAddr=>gte_write_addr,gte_writeData=>gte_write_data,gte_writeEna=>gte_write_ena,
        gte_cmdData=>gte_cmd_data,gte_cmdEna=>gte_cmd_ena,
        SS_reset=>ss_reset,SS_DataWrite=>(others=>'0'),SS_Adr=>(others=>'0'),SS_wren_CPU=>'0',SS_wren_SCP=>'0',
        SS_rden_CPU=>'0',SS_rden_SCP=>'0',SS_DataRead_CPU=>open,SS_DataRead_SCP=>open,SS_idle=>open,
        cpu_done=>cpu_done,cpu_export=>cpu_export,debug_firstGTE=>'0'
    );
    gte : entity work.gte port map (
        clk1x=>clk1x,clk2x=>clk2x,clk2xIndex=>index2x,ce=>ce,reset=>reset,WIDESCREEN=>"00",TURBO=>'0',
        gte_busy=>gte_busy,gte_readAddr=>gte_read_addr,gte_readData=>gte_read_data,gte_readEna=>gte_read_ena,
        gte_writeAddr_in=>gte_write_addr,gte_writeData_in=>gte_write_data,gte_writeEna_in=>gte_write_ena,
        gte_cmdData=>gte_cmd_data,gte_cmdEna=>gte_cmd_ena,
        loading_savestate=>'0',SS_reset=>'0',SS_DataWrite=>(others=>'0'),SS_Adr=>(others=>'0'),
        SS_wren=>'0',SS_rden=>'0',SS_DataRead=>open,SS_idle=>open,debug_firstGTE=>open
    );
    process
    begin
        for edge in 1 to 4 loop wait until falling_edge(clk1x); end loop;
        ss_reset<='0';
        for edge in 1 to 50 loop wait until falling_edge(clk1x); end loop;
        reset<='0';
        wait;
    end process;
    process(clk1x)
        variable ticks : natural := 0;
    begin
        if falling_edge(clk1x) then
            ticks:=ticks+1;
            ce<='1';
            if PAUSES and reset='0' and (ticks+SEED) mod 17<3 then ce<='0'; end if;
        end if;
        if rising_edge(clk1x) and reset='0' and ce='1' and cpu_done='1' then
            retired<=retired+1;
            if TRACE then report "CPU pc=" & to_hstring(cpu_export.pc) & " op=" & to_hstring(cpu_export.opcode) &
                " r8=" & to_hstring(cpu_export.regs(8)) & " r9=" & to_hstring(cpu_export.regs(9)); end if;
        end if;
    end process;
    process(clk1x)
        variable ram : words(0 to 16383) := (others=>(others=>'0'));
        variable pending : boolean := false;
        variable delay : natural := 0;
        variable address : unsigned(31 downto 0);
        variable physical : unsigned(28 downto 0);
        variable transaction,checks : natural := 0;
        variable check_index : natural;
    begin
        if rising_edge(clk1x) then
            if reset='1' then done<='0'; ram_done<='0'; pending:=false;
            elsif ce='1' then
                done<='0'; ram_done<='0';
                if pending then
                    if delay=0 then
                        physical:=address(28 downto 0);
                        if physical>=16#1fc00000# and physical<16#1fc80000# then
                            read_data<=rom(to_integer(physical(18 downto 2)));
                            ram_data<=rom(to_integer(physical(18 downto 2)));
                        elsif physical<65536 then
                            read_data<=ram(to_integer(physical(15 downto 2)));
                            ram_data<=ram(to_integer(physical(15 downto 2)));
                        else assert false report "Unexpected diagnostic read " & to_hstring(address) severity failure;
                        end if;
                        done<='1'; ram_done<='1'; ram_rnw<='1'; pending:=false;
                    else delay:=delay-1; end if;
                end if;
                if request='1' then
                    assert not pending report "CPU issued overlapping reads" severity failure;
                    assert iscache='0' report "Diagnostic unexpectedly requested a cache fill" severity failure;
                    address:=addr_instr;
                    if isdata='1' then address:=addr_data; end if;
                    physical:=address(28 downto 0); transaction:=transaction+1;
                    if rnw='1' then pending:=true; delay:=(transaction+SEED) mod 5;
                    else
                        assert isdata='1' and physical<65536 and mask="1111"
                            report "Unexpected diagnostic write " & to_hstring(address) severity failure;
                        ram(to_integer(physical(15 downto 2))):=write_data;
                        if physical>=4096 and physical<4096+expected'length*4 then
                            check_index:=to_integer((physical-4096)/4);
                            assert check_index=checks report "Result stores missing or reordered" severity failure;
                            assert write_data=expected(check_index)
                                report "CPU/GTE mismatch index=" & natural'image(check_index) & " got=" &
                                    to_hstring(write_data) & " expected=" & to_hstring(expected(check_index)) severity failure;
                            checks:=checks+1;
                        elsif physical=16#ff00# then
                            assert write_data=x"600df00d" and checks=expected'length and commands=448
                                report "CPU/GTE diagnostic incomplete" severity failure;
                            assert ram(16#f004#/4)=x"ffff8001" report "LWC2/SWC2 roundtrip mismatch" severity failure;
                            report "PASS MiSTer CPU/GTE seed=" & natural'image(SEED) & " retired=" & natural'image(retired) &
                                " commands=" & natural'image(commands) & " checks=" & natural'image(checks) &
                                " transactions=" & natural'image(transaction);
                            finish;
                        end if;
                    end if;
                end if;
            end if;
        end if;
    end process;
    process begin wait for 100 ms; assert false report "CPU/GTE execution timeout" severity failure; end process;
end architecture;
