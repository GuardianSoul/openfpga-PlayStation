-- SPDX-License-Identifier: GPL-2.0-only
-- Behavioural stand-ins for rtl/pocket/psx_gpu_base_dsp.sv and psx_gpu_work_dsp.sv
-- (altera_mult_add accumulators), as in scripts/run_gpu_base_dsp.py and
-- scripts/run_gpu_work_dsp.py. gpu_poly's own translate_off assertions check
-- them against the original registers on every falling edge.
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
entity psx_gpu_base_dsp is
   port(clk,enable,load,initialize: in std_logic;
        vertex: in std_logic_vector(7 downto 0);
        increment: in std_logic_vector(19 downto 0);
        value: out std_logic_vector(19 downto 0));
end entity;
architecture model of psx_gpu_base_dsp is
   signal acc: unsigned(19 downto 0) := (others=>'0');
begin
   value <= std_logic_vector(acc);
   process(clk) begin
      if rising_edge(clk) then
         if initialize='1' then acc <= (others=>'0');
         elsif enable='1' then
            if load='1' then acc <= unsigned(vertex) & x"800";
            else acc <= acc+unsigned(increment); end if;
         end if;
      end if;
   end process;
end architecture;

library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
entity psx_gpu_work_dsp is
   port(clk,enable,load,initialize: in std_logic;
        base,increment: in std_logic_vector(19 downto 0);
        value: out std_logic_vector(19 downto 0));
end entity;
architecture model of psx_gpu_work_dsp is
   signal acc: unsigned(19 downto 0) := (others=>'0');
begin
   value <= std_logic_vector(acc);
   process(clk) begin
      if rising_edge(clk) then
         if initialize='1' then acc <= (others=>'0');
         elsif enable='1' then
            if load='1' then acc <= unsigned(base);
            else acc <= acc+unsigned(increment); end if;
         end if;
      end if;
   end process;
end architecture;
