-- SPDX-License-Identifier: MIT
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
package psx_pgxp_gpu_pkg is
   type pgxp_div_type is record
      start : std_logic;
      done : std_logic;
      dividend : signed(52 downto 0);
      divisor : signed(32 downto 0);
      quotient : signed(52 downto 0);
      remainder : signed(32 downto 0);
   end record;
end package;
