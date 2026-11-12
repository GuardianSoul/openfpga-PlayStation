-- SPDX-License-Identifier: GPL-2.0-only
-- Exhaustive check of the spu_noise.patch rewrite (base_trims_056): for all 16
-- noise shifts s = CNT(13:10) and all 65536 values of noiseCnt(31:16) (each
-- with several low halves), the original
--    noiseCheck := shift_right(x"8000", s) & x"0000";
--    fire := noiseCnt >= noiseCheck; next := noiseCnt and (noiseCheck - 1)
-- equals the patched
--    noiseMask := not shift_right(x"7FFF", s);
--    fire := (noiseCnt(31:16) and noiseMask) /= 0; next(31:16) := noiseCnt(31:16) and not noiseMask
-- The expressions are copied verbatim from spu.vhd and the patch.
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity tb_spu_noise_mask is
end entity;

architecture sim of tb_spu_noise_mask is
begin
   process
      variable noiseCheck : unsigned(31 downto 0);
      variable noiseShift : unsigned(3 downto 0);
      variable noiseMask  : unsigned(15 downto 0);
      variable noiseCnt, next_a, next_b : unsigned(31 downto 0);
      variable fire_a, fire_b : boolean;
      variable cnt : std_logic_vector(15 downto 0);
      variable checks, fires : natural := 0;
      type t_lows is array(0 to 3) of unsigned(15 downto 0);
      constant lows : t_lows := (x"0000", x"0001", x"8000", x"FFFF");
   begin
      for s in 0 to 15 loop
         cnt := (others => '0');
         cnt(13 downto 10) := std_logic_vector(to_unsigned(s, 4));
         noiseCheck := shift_right(to_unsigned(16#8000#, 16), to_integer(unsigned(cnt(13 downto 10)))) & x"0000";
         noiseShift := unsigned(cnt(13 downto 10));
         noiseMask := not shift_right(to_unsigned(16#7FFF#, 16), to_integer(noiseShift));
         for hi in 0 to 65535 loop
            for l in lows'range loop
               noiseCnt := to_unsigned(hi, 16) & lows(l);
               fire_a := noiseCnt >= noiseCheck;
               next_a := noiseCnt and (noiseCheck - 1);
               fire_b := (noiseCnt(31 downto 16) and noiseMask) /= 0;
               next_b := noiseCnt;
               next_b(31 downto 16) := noiseCnt(31 downto 16) and not noiseMask;
               if fire_a /= fire_b or (fire_a and next_a /= next_b) then
                  report "MISMATCH noise s=" & integer'image(s) & " hi=" & integer'image(hi) severity failure;
               end if;
               if fire_a then fires := fires + 1; end if;
               checks := checks + 1;
            end loop;
         end loop;
      end loop;
      report "PASS noise mask shifts=16 checks=" & integer'image(checks) & " fires=" & integer'image(fires) severity note;
      wait;
   end process;
end architecture;
