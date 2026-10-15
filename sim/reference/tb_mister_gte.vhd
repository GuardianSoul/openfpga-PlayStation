library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
use std.textio.all;
use std.env.all;

-- Drives the unmodified MiSTer GTE using generated mathematical probes.
-- No CPU adapter, hardware timing proof or full command coverage is implied.
entity tb_mister_gte is
    generic (VECTOR_FILE : string := "gte-vectors.txt");
end entity;

architecture test of tb_mister_gte is
    signal clk1x : std_logic := '1';
    signal clk2x : std_logic := '1';
    signal reset : std_logic := '1';
    signal clock_toggle, sampled_toggle, clk2xIndex : std_logic := '0';
    signal busy, read_enable, write_enable, command_enable : std_logic := '0';
    signal read_address, write_address : unsigned(5 downto 0) := (others=>'0');
    signal read_data, write_data, command_data : unsigned(31 downto 0) := (others=>'0');
    signal area_busy : std_logic;
    signal area_read_data : unsigned(31 downto 0);
begin
    clk1x <= not clk1x after 15 ns;
    clk2x <= not clk2x after 7500 ps;
    process(clk1x) begin
        if rising_edge(clk1x) then clock_toggle <= not clock_toggle; end if;
    end process;
    process(clk2x) begin
        if rising_edge(clk2x) then
            sampled_toggle <= clock_toggle;
            clk2xIndex <= '0';
            if sampled_toggle=clock_toggle then clk2xIndex <= '1'; end if;
        end if;
    end process;
    dut : entity work.gte
        port map (
            clk1x=>clk1x, clk2x=>clk2x, clk2xIndex=>clk2xIndex, ce=>'1', reset=>reset,
            WIDESCREEN=>"00", TURBO=>'0', gte_busy=>busy,
            gte_readAddr=>read_address, gte_readData=>read_data, gte_readEna=>read_enable,
            gte_writeAddr_in=>write_address, gte_writeData_in=>write_data, gte_writeEna_in=>write_enable,
            gte_cmdData=>command_data, gte_cmdEna=>command_enable,
            loading_savestate=>'0', SS_reset=>'0', SS_DataWrite=>(others=>'0'), SS_Adr=>(others=>'0'),
            SS_wren=>'0', SS_rden=>'0', SS_DataRead=>open, SS_idle=>open, debug_firstGTE=>open
        );
    resource_dut : entity work.psx_mister_gte_resource
        port map (
            clk1x=>clk1x, clk2x=>clk2x, clk2xIndex=>clk2xIndex, ce=>'1', reset=>reset,
            gte_busy=>area_busy, gte_readAddr=>read_address, gte_readData=>area_read_data,
            gte_readEna=>read_enable, gte_writeAddr_in=>write_address,
            gte_writeData_in=>write_data, gte_writeEna_in=>write_enable,
            gte_cmdData=>command_data, gte_cmdEna=>command_enable
        );
    process
        variable read_accepted : boolean;
    begin
        wait until rising_edge(clk2x); read_accepted:=read_enable='1'; wait for 1 ns;
        if reset='0' then
            assert area_busy=busy report "Resource wrapper changed GTE busy behavior" severity failure;
            if read_accepted then
                assert area_read_data=read_data report "Resource wrapper changed GTE register result" severity failure;
            end if;
        end if;
    end process;
    process
        file vectors : text open read_mode is VECTOR_FILE;
        variable line_data : line;
        variable operation : character;
        variable address : std_logic_vector(7 downto 0);
        variable value : std_logic_vector(31 downto 0);
        variable operations, commands, checks, wait_cycles : natural := 0;
    begin
        for edge in 1 to 10 loop wait until falling_edge(clk1x); end loop;
        reset <= '0';
        wait until falling_edge(clk1x);
        while not endfile(vectors) loop
            readline(vectors,line_data);
            read(line_data,operation); hread(line_data,address); hread(line_data,value);
            assert busy='0' report "GTE remained busy before a probe" severity failure;
            case operation is
                when 'W' =>
                    write_address<=unsigned(address(5 downto 0)); write_data<=unsigned(value); write_enable<='1';
                    wait until falling_edge(clk1x); write_enable<='0';
                    wait until falling_edge(clk1x);
                when 'R' =>
                    read_address<=unsigned(address(5 downto 0)); read_enable<='1';
                    wait until falling_edge(clk1x); read_enable<='0';
                    assert read_data=unsigned(value)
                        report "GTE mismatch probe=" & natural'image(operations) & " reg=" & to_hstring(address) &
                               " got=" & to_hstring(read_data) & " expected=" & to_hstring(value) severity failure;
                    checks:=checks+1;
                    wait until falling_edge(clk1x);
                when 'C' =>
                    command_data<=unsigned(value); command_enable<='1';
                    wait until falling_edge(clk1x); command_enable<='0';
                    wait for 1 ns; -- coincident clk2x acceptance settles in a delta cycle
                    assert busy='1' report "GTE command did not start" severity failure;
                    wait_cycles:=0;
                    while busy='1' loop
                        wait until falling_edge(clk1x); wait_cycles:=wait_cycles+1;
                        assert wait_cycles<100 report "GTE command timeout" severity failure;
                    end loop;
                    commands:=commands+1;
                    wait until falling_edge(clk1x);
                when others => assert false report "Unknown GTE probe operation" severity failure;
            end case;
            operations:=operations+1;
        end loop;
        report "PASS MiSTer GTE operations=" & natural'image(operations) &
               " commands=" & natural'image(commands) & " checks=" & natural'image(checks);
        report "PASS FF resource GTE wrapper matched upstream busy/results";
        finish;
    end process;
    process begin wait for 10 ms; assert false report "GTE probe timeout" severity failure; end process;
end architecture;
