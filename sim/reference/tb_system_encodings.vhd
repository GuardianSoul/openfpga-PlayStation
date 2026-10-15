library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
use std.env.all;

-- Check the output bit encodings used by full-system GHDL compatibility copies.
-- Original Natural conversions reject negative values that FPGA wires encode
-- modulo their width. These checks do not waive waveform or device behavior.
entity tb_system_encodings is end entity;
architecture test of tb_system_encodings is begin
    process
        variable analog : signed(7 downto 0);
        variable offset : unsigned(7 downto 0);
        variable linecode : unsigned(8 downto 0);
        variable position : std_logic_vector(19 downto 0);
        variable value : integer;
        variable analog_checks,line_checks,position_checks : natural := 0;
    begin
        for input in -128 to 127 loop
            analog:=to_signed(input,8);
            offset:=to_unsigned(to_integer(resize(analog,9)+128),8);
            assert offset=unsigned(analog+signed'(x"80")) and to_integer(offset)=input+128
                report "Analog offset encoding changed" severity failure;
            analog_checks:=analog_checks+1;
        end loop;
        for lineMax in 0 to 512 loop
            for subtract in 1 to 2 loop
                value:=lineMax-subtract;
                linecode:=resize(unsigned(to_signed(value,10)),9);
                assert to_integer(linecode)=value mod 512 report "Line limit encoding changed" severity failure;
                line_checks:=line_checks+1;
            end loop;
        end loop;
        for display in 0 to 511 loop
            for start in 0 to 314 loop
                for mode in 0 to 2 loop
                    value:=display-start;
                    if mode=1 then value:=value*2; elsif mode=2 then value:=value*2+1; end if;
                    linecode:=resize(unsigned(to_signed(value,12)),9);
                    assert to_integer(linecode)=value mod 512 report "Display offset encoding changed" severity failure;
                    line_checks:=line_checks+1;
                end loop;
            end loop;
        end loop;
        for index in -524288 to 524287 loop
            position:=std_logic_vector(to_signed(index,20));
            assert unsigned(position)=resize(unsigned(to_signed(index,32)),20) and to_integer(signed(position))=index
                report "CD signed position encoding changed" severity failure;
            position_checks:=position_checks+1;
        end loop;
        report "PASS system encodings analog=" & natural'image(analog_checks) &
            " lines=" & natural'image(line_checks) & " positions=" & natural'image(position_checks);
        finish;
    end process;
end architecture;
