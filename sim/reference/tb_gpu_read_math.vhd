library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
use std.env.all;
use work.psx_gpu_read_math.all;

entity tb_gpu_read_math is end entity;
architecture test of tb_gpu_read_math is
begin
    process
        variable expected,checked,rounding_checked,quantum,increments : natural := 0;
    begin
        for x in 0 to 1023 loop
            for length in 0 to 1024 loop
                expected := 0;
                if x + length > 1024 then expected := (x + length - 1024) / 4; end if;
                assert to_integer(read_wrap(to_unsigned(x,10),to_unsigned(length,11))) = expected
                    report "GPU read-wrap arithmetic mismatch" severity failure;
                checked := checked + 1;
            end loop;
        end loop;
        report "PASS GPU read-wrap comparisons=" & natural'image(checked);
        for x_low in 0 to 3 loop
            for raw_size in 0 to 2047 loop
                increments := 0;
                if raw_size mod 4 /= 0 then increments := increments + 1; end if;
                if x_low /= 0 and x_low + raw_size > 4 then increments := increments + 1; end if;
                quantum := ((raw_size / 4) + increments) mod 512;
                expected := quantum * 4 + (raw_size mod 4);
                if expected > 1024 then expected := 1024; end if;
                assert to_integer(rounded_read_size(to_unsigned(x_low,2),to_unsigned(raw_size,11))) = expected
                    report "GPU read rounding mismatch" severity failure;
                rounding_checked := rounding_checked + 1;
            end loop;
        end loop;
        report "PASS GPU read rounding comparisons=" & natural'image(rounding_checked);
        finish;
    end process;
end architecture;
