library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
use std.textio.all;
use std.env.all;

-- Generated DC-only probes: custom scale matrices, DMA gaps and output stalls.
-- No AC/chroma rounding, real movie, CD delivery or hardware timing proof.
entity tb_mister_mdec is
    generic (VECTOR_FILE : string := "mdec-vectors.txt");
end entity;
architecture test of tb_mister_mdec is
    signal clk1x,clk2x,reset : std_logic := '1';
    signal clock_toggle,sampled_toggle,clk2xIndex : std_logic := '0';
    signal address : unsigned(3 downto 0) := (others=>'0');
    signal write_data,read_data,dma_write_data,dma_read_data : std_logic_vector(31 downto 0) := (others=>'0');
    signal bus_read,bus_write,dma_read,dma_write,write_request,read_request : std_logic := '0';
    signal ref_read_data,ref_dma_data : std_logic_vector(31 downto 0);
    signal ref_write_request,ref_read_request : std_logic;
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
    dut : entity work.psx_mister_mdec_resource
        port map (clk1x=>clk1x,clk2x=>clk2x,clk2xIndex=>clk2xIndex,ce=>'1',reset=>reset,
            bus_addr=>address,bus_dataWrite=>write_data,bus_read=>bus_read,bus_write=>bus_write,
            bus_dataRead=>read_data,dmaWriteRequest=>write_request,dmaReadRequest=>read_request,
            dma_write=>dma_write,dma_writedata=>dma_write_data,dma_read=>dma_read,dma_readdata=>dma_read_data);
    reference_dut : entity work.mdec
        port map (clk1x=>clk1x,clk2x=>clk2x,clk2xIndex=>clk2xIndex,ce=>'1',reset=>reset,
            bus_addr=>address,bus_dataWrite=>write_data,bus_read=>bus_read,bus_write=>bus_write,
            bus_dataRead=>ref_read_data,dmaWriteRequest=>ref_write_request,dmaReadRequest=>ref_read_request,
            dma_write=>dma_write,dma_writedata=>dma_write_data,dma_read=>dma_read,dma_readdata=>ref_dma_data,
            SS_reset=>reset,SS_DataWrite=>(others=>'0'),SS_Adr=>(others=>'0'),SS_wren=>'0',SS_rden=>'0',
            SS_DataRead=>open,SS_Idle=>open);
    process
        file vectors : text open read_mode is VECTOR_FILE;
        variable line_data : line;
        variable operation : character;
        variable addr : std_logic_vector(7 downto 0);
        variable value,status,held : std_logic_vector(31 downto 0);
        variable words,operations,stalls : natural := 0;
        procedure tick is begin wait until falling_edge(clk1x); wait for 1 ns; end;
        procedure read_status(variable result : out std_logic_vector(31 downto 0)) is
        begin
            address<=x"4"; bus_read<='1'; tick; bus_read<='0'; result:=read_data;
            assert read_data=ref_read_data report "MDEC wrapper status mismatch" severity failure;
            tick;
        end;
        procedure await_output is
            variable count : natural := 0;
        begin
            loop
                read_status(status); exit when status(31)='0'; count:=count+1;
                assert count<20000 report "MDEC output timeout" severity failure;
            end loop;
            assert read_request='1' and ref_read_request='1' report "MDEC output request absent" severity failure;
        end;
    begin
        for i in 1 to 12 loop tick; end loop;
        reset<='0'; tick;
        while not endfile(vectors) loop
            readline(vectors,line_data); read(line_data,operation); hread(line_data,addr); hread(line_data,value);
            case operation is
                when 'B' =>
                    address<=unsigned(addr(3 downto 0)); write_data<=value; bus_write<='1';
                    tick; bus_write<='0'; tick;
                when 'D' =>
                    stalls:=0;
                    while write_request/='1' loop
                        tick; stalls:=stalls+1;
                        assert stalls<20000 report "MDEC input request timeout" severity failure;
                    end loop;
                    assert ref_write_request='1' report "MDEC wrapper input request mismatch" severity failure;
                    dma_write_data<=value; dma_write<='1'; tick; dma_write<='0';
                    for i in 0 to operations mod 3 loop tick; end loop;
                when 'H' =>
                    await_output; held:=dma_read_data;
                    for i in 1 to to_integer(unsigned(value)) loop
                        tick;
                        assert dma_read_data=held and dma_read_data=ref_dma_data
                            report "MDEC changed held output under backpressure" severity failure;
                    end loop;
                when 'E' =>
                    await_output;
                    assert dma_read_data=value and ref_dma_data=value
                        report "MDEC word=" & natural'image(words) & " got=" & to_hstring(dma_read_data) &
                               " expected=" & to_hstring(value) severity failure;
                    dma_read<='1'; tick; dma_read<='0'; words:=words+1;
                    for i in 0 to words mod 4 loop tick; end loop;
                when 'F' =>
                    for i in 1 to 10 loop tick; end loop;
                    read_status(status);
                    assert status(31)='1' report "MDEC produced excess output" severity failure;
                when others => assert false report "Unknown MDEC probe" severity failure;
            end case;
            operations:=operations+1;
        end loop;
        report "PASS MDEC DC probes words=" & natural'image(words);
        finish;
    end process;
    process begin wait for 50 ms; assert false report "MDEC bench timeout" severity failure; end process;
end architecture;
