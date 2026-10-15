-- SPDX-License-Identifier: GPL-2.0-only
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

package psx_gpu_read_math is
    subtype wrap_words is unsigned(7 downto 0);
    subtype read_size is unsigned(10 downto 0);
    function rounded_read_size(x_low : unsigned(1 downto 0); raw_size : read_size) return read_size;
    function read_wrap(x : unsigned(9 downto 0); rounded_size : unsigned(10 downto 0)) return wrap_words;
end package;

package body psx_gpu_read_math is
    function rounded_read_size(x_low : unsigned(1 downto 0); raw_size : read_size) return read_size is
        variable first, second : boolean;
        variable increment : unsigned(1 downto 0) := "00";
        variable words : unsigned(8 downto 0);
        variable rounded : read_size;
    begin
        first := raw_size(1 downto 0) /= "00";
        second := x_low /= "00" and
            (raw_size(10 downto 2) /= 0 or
             (raw_size(1 downto 0) = "11" and x_low > 1) or
             (raw_size(1 downto 0) = "10" and x_low = "11"));
        if first xor second then increment(0) := '1'; end if;
        if first and second then increment(1) := '1'; end if;
        -- A single modulo-512 add preserves the original two conditional adds.
        words := raw_size(10 downto 2) + resize(increment,9);
        rounded := words & raw_size(1 downto 0);
        if rounded > 1024 then return to_unsigned(1024,11); end if;
        return rounded;
    end function;

    function read_wrap(x : unsigned(9 downto 0); rounded_size : unsigned(10 downto 0)) return wrap_words is
        variable extent : unsigned(10 downto 0);
    begin
        -- The existing caller caps its rounded request at one VRAM row.
        -- synthesis translate_off
        assert rounded_size <= 1024 report "GPU read-wrap request exceeds one VRAM row" severity failure;
        -- synthesis translate_on
        extent := resize(x, 11) + rounded_size;
        if extent(10) = '1' then return extent(9 downto 2); end if;
        return (others => '0');
    end function;
end package body;
