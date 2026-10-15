library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity ghdl_shift_probe is
    port (value_in : in signed(31 downto 0);
          shift_in : in unsigned(4 downto 0);
          wide_signed : out signed(31 downto 0);
          narrow_signed : out signed(15 downto 0);
          unsigned_out : out unsigned(31 downto 0));
end entity;
architecture test of ghdl_shift_probe is
begin
    wide_signed <= shift_right(value_in, to_integer(shift_in));
    narrow_signed <= shift_right(signed(value_in(15 downto 0)), to_integer(shift_in));
    unsigned_out <= shift_right(unsigned(value_in), to_integer(shift_in));
end architecture;
