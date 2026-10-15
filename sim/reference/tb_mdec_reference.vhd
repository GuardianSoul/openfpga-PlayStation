-- SPDX-License-Identifier: GPL-2.0-only
-- Upstream MDEC driven by a vector file (B = register write, D = DMA input
-- word, R = output words to expect); every DMA output word is written to
-- OUT_FILE in hex for comparison with run_mdec_reference.py's model.
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
use std.textio.all;
use std.env.all;

entity tb_mdec_reference is
    generic (VECTOR_FILE : string := "mdec-reference.txt"; OUT_FILE : string := "mdec-out.txt");
end entity;
architecture test of tb_mdec_reference is
    signal clk1x,clk2x,reset : std_logic := '1';
    signal clock_toggle,sampled_toggle,clk2xIndex : std_logic := '0';
    signal address : unsigned(3 downto 0) := (others=>'0');
    signal write_data,dma_write_data : std_logic_vector(31 downto 0) := (others=>'0');
    signal bus_read,bus_write,dma_read,dma_write : std_logic := '0';
    signal a_read,a_dma : std_logic_vector(31 downto 0);
    signal a_wreq,a_rreq : std_logic;
    signal done : boolean := false;
    signal read_words : natural := 0;
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
    original : entity work.mdec
        port map (clk1x=>clk1x,clk2x=>clk2x,clk2xIndex=>clk2xIndex,ce=>'1',reset=>reset,
            bus_addr=>address,bus_dataWrite=>write_data,bus_read=>bus_read,bus_write=>bus_write,
            bus_dataRead=>a_read,dmaWriteRequest=>a_wreq,dmaReadRequest=>a_rreq,
            dma_write=>dma_write,dma_writedata=>dma_write_data,dma_read=>dma_read,dma_readdata=>a_dma,
            SS_reset=>reset,SS_DataWrite=>(others=>'0'),SS_Adr=>(others=>'0'),SS_wren=>'0',SS_rden=>'0',
            SS_DataRead=>open,SS_Idle=>open);
    -- Drain output whenever requested, with a varying gap.
    process
        variable gap : natural := 0;
        file output : text open write_mode is OUT_FILE;
        variable l : line;
    begin
        wait until falling_edge(clk1x); wait for 1 ns;
        if a_rreq='1' and reset='0' then
            -- dma_readdata is the output FIFO head; it pops on the 2x edge.
            hwrite(l, a_dma); writeline(output, l);
            dma_read<='1'; wait until falling_edge(clk1x); wait for 1 ns; dma_read<='0';
            read_words<=read_words+1;
            gap:=(gap+1) mod 5;
            for i in 1 to gap loop wait until falling_edge(clk1x); end loop;
        end if;
    end process;
    process
        file vectors : text open read_mode is VECTOR_FILE;
        variable line_data : line;
        variable operation : character;
        variable addr : std_logic_vector(7 downto 0);
        variable value : std_logic_vector(31 downto 0);
        variable words,stalls : natural := 0;
        procedure tick is begin wait until falling_edge(clk1x); wait for 1 ns; end;
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
                    while a_wreq/='1' loop
                        tick; stalls:=stalls+1;
                        assert stalls<200000 report "MDEC input request timeout" severity failure;
                    end loop;
                    dma_write_data<=value; dma_write<='1'; tick; dma_write<='0';
                when 'R' =>
                    -- Like a game, wait for the command's output before the next command.
                    words:=words+to_integer(unsigned(value));
                    stalls:=0;
                    while read_words<words loop
                        tick; stalls:=stalls+1;
                        assert stalls<200000 report "MDEC output timeout read=" & natural'image(read_words) & " expected=" & natural'image(words) severity failure;
                    end loop;
                when others => assert false report "Unknown vector" severity failure;
            end case;
        end loop;
        stalls:=0;
        while read_words<words loop
            tick; stalls:=stalls+1;
            assert stalls<200000 report "MDEC output timeout read=" & natural'image(read_words) & " expected=" & natural'image(words) severity failure;
        end loop;
        for i in 1 to 100 loop tick; end loop;
        done<=true;
        report "PASS MDEC reference run words=" & natural'image(read_words);
        finish;
    end process;
end architecture;
