-- SPDX-License-Identifier: GPL-2.0-only
-- One MDEC driven by a vector file, for scripts/run_save_mdec_tap.py
-- (docs/save-states-finish/mdec-tap.md). Every line is "op aa vvvvvvvv" (hex):
--   B aa v   register write (aa = 0 command/data, 4 control)
--   D 00 v   one DMA-in word (waits for the data-in request)
--   I 00 v   the same, reading (and logging) requested output words while it waits
--   R 00 n   read n output words by DMA as they are requested; a timeout fails
--   Q 00 n   the same, but a timeout only ends the wait ("Q got k of n")
--   F 00 n   n DMA-out reads whether the MDEC requests them or not
--   S 00 0   status read
--   W 00 n   wait n clk1x
--   X 00 0   core reset pulse
--   M 00 v   table tap on (v = 1) or off      (bench with the tap ports only)
--   T aa 0   table tap address aa, then log the quant byte and the scale word
-- Output lines: "O word" (R/Q), "F word request" (F), "S word", "T aa qq ssss",
-- "Q got k of n". The lines marked TAP are removed for an MDEC without the tap.
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
use std.textio.all;
use std.env.all;

entity tb_mdec_tap is
    generic (VECTOR_FILE : string := "mdec-tap.txt"; OUT_FILE : string := "mdec-tap-out.txt");
end entity;
architecture test of tb_mdec_tap is
    signal clk1x,clk2x,reset : std_logic := '1';
    signal clock_toggle,sampled_toggle,clk2xIndex : std_logic := '0';
    signal address : unsigned(3 downto 0) := (others=>'0');
    signal write_data,dma_write_data : std_logic_vector(31 downto 0) := (others=>'0');
    signal bus_read,bus_write,dma_read,dma_write : std_logic := '0';
    signal a_read,a_dma : std_logic_vector(31 downto 0);
    signal a_wreq,a_rreq : std_logic;
    signal tap_on : std_logic := '0';
    signal tap_adr : unsigned(6 downto 0) := (others=>'0');
    signal tap_yuv : std_logic_vector(7 downto 0) := (others=>'0');
    signal tap_scale : std_logic_vector(15 downto 0) := (others=>'0');
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
    dut : entity work.mdec
        port map (clk1x=>clk1x,clk2x=>clk2x,clk2xIndex=>clk2xIndex,ce=>'1',reset=>reset,
            bus_addr=>address,bus_dataWrite=>write_data,bus_read=>bus_read,bus_write=>bus_write,
            bus_dataRead=>a_read,dmaWriteRequest=>a_wreq,dmaReadRequest=>a_rreq,
            dma_write=>dma_write,dma_writedata=>dma_write_data,dma_read=>dma_read,dma_readdata=>a_dma,
            save_tap_on=>tap_on,save_tap_adr=>tap_adr,save_tap_yuv=>tap_yuv,save_tap_scale=>tap_scale, -- TAP
            SS_reset=>reset,SS_DataWrite=>(others=>'0'),SS_Adr=>(others=>'0'),SS_wren=>'0',SS_rden=>'0',
            SS_DataRead=>open,SS_Idle=>open);
    process
        file vectors : text open read_mode is VECTOR_FILE;
        file output : text open write_mode is OUT_FILE;
        variable line_data,l : line;
        variable operation : character;
        variable addr : std_logic_vector(7 downto 0);
        variable value : std_logic_vector(31 downto 0);
        variable count,got,stalls : natural := 0;
        procedure tick is begin wait until falling_edge(clk1x); wait for 1 ns; end;
        procedure put(tag : string; word : std_logic_vector) is
        begin write(l, tag); hwrite(l, word); writeline(output, l); end;
    begin
        for i in 1 to 12 loop tick; end loop;
        reset<='0'; tick;
        while not endfile(vectors) loop
            readline(vectors,line_data); read(line_data,operation); hread(line_data,addr); hread(line_data,value);
            count:=to_integer(unsigned(value(30 downto 0)));
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
                when 'I' =>
                    stalls:=0;
                    while a_wreq/='1' loop
                        if a_rreq='1' then
                            put("O ", a_dma); dma_read<='1'; tick; dma_read<='0'; stalls:=0;
                        else
                            tick; stalls:=stalls+1;
                        end if;
                        assert stalls<200000 report "MDEC input request timeout" severity failure;
                    end loop;
                    dma_write_data<=value; dma_write<='1'; tick; dma_write<='0';
                when 'R' | 'Q' =>
                    got:=0; stalls:=0;
                    while got<count and stalls<200000 loop
                        if a_rreq='1' then
                            -- dma_readdata is the output FIFO head; it pops on the 2x edge
                            put("O ", a_dma);
                            dma_read<='1'; tick; dma_read<='0'; got:=got+1; stalls:=0;
                        else
                            tick; stalls:=stalls+1;
                        end if;
                    end loop;
                    assert operation='Q' or got=count
                        report "MDEC output timeout read=" & natural'image(got) & " expected=" & natural'image(count) severity failure;
                    if operation='Q' then
                        write(l, string'("Q got ") & natural'image(got) & " of " & natural'image(count)); writeline(output, l);
                    end if;
                when 'F' =>
                    for i in 1 to count loop
                        write(l, string'("F ")); hwrite(l, a_dma); write(l, string'(" ") & std_logic'image(a_rreq)(2)); writeline(output, l);
                        dma_read<='1'; tick; dma_read<='0'; tick;
                    end loop;
                when 'S' =>
                    address<=x"4"; bus_read<='1'; tick; bus_read<='0'; put("S ", a_read); tick;
                when 'W' =>
                    for i in 1 to count loop tick; end loop;
                when 'X' =>
                    reset<='1'; for i in 1 to 4 loop tick; end loop; reset<='0'; tick;
                when 'M' =>
                    tap_on<=value(0); tick;
                when 'T' =>
                    tap_adr<=unsigned(addr(6 downto 0));
                    for i in 1 to 4 loop tick; end loop;
                    write(l, string'("T ")); hwrite(l, addr); write(l, string'(" ")); hwrite(l, tap_yuv);
                    write(l, string'(" ")); hwrite(l, tap_scale); writeline(output, l);
                when others => assert false report "Unknown vector" severity failure;
            end case;
        end loop;
        for i in 1 to 100 loop tick; end loop;
        report "PASS MDEC tap bench run";
        finish;
    end process;
end architecture;
