-- SPDX-License-Identifier: GPL-3.0-or-later
-- rtl/pocket/psx_gpu_recip_unit.vhd against the pinned PSX_MiSTer divider.
-- Each case starts one reciprocal and, while it runs, ten upstream dividers
-- (45-bit dividend n * 2^12, 25-bit divisor d, as gpu_poly and gpu_line start
-- them); then the ten numerators go through the unit one per clock, in a
-- burst or with gaps. quotient(31 downto 0) of every upstream divider must
-- equal the unit's result, which must also equal an exact integer quotient.
-- Operands cover |n| < 2^20 and 0 < |d| < 2^20 with boundaries, small and
-- power-of-two divisors, and numerators near multiples of d.
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
use ieee.math_real.all;

entity tb_gpu_recip_unit is
   generic (CASES : integer := 20000; SEED : integer := 1);
end entity;

architecture sim of tb_gpu_recip_unit is
   signal clk : std_logic := '0';
   signal done : boolean := false;
   signal recipStart, inValid, outValid, ready : std_logic := '0';
   signal recipDivisor, inN : signed(20 downto 0) := (others => '0');
   signal inIdx, outIdx : unsigned(3 downto 0) := (others => '0');
   signal outQ : signed(31 downto 0);

   type dividend_array is array(0 to 9) of signed(44 downto 0);
   type quotient_array is array(0 to 9) of signed(44 downto 0);
   type remainder_array is array(0 to 9) of signed(24 downto 0);
   signal divStart : std_logic := '0';
   signal dividends : dividend_array := (others => (others => '0'));
   signal divisor : signed(24 downto 0) := (others => '0');
   signal quotients : quotient_array;
   signal remainders : remainder_array;
   signal dones : std_logic_vector(0 to 9);
   type q32_array is array(0 to 9) of signed(31 downto 0);
   signal unitQ : q32_array;
   signal unitSeen : std_logic_vector(0 to 9) := (others => '0');
   signal clearSeen : boolean := false;
begin
   clk <= not clk after 5 ns when not done;

   dut : entity work.psx_gpu_recip_unit
      port map (clk => clk, ce => '1', reset => '0', recipStart => recipStart, recipDivisor => recipDivisor,
                recipReady => ready, inValid => inValid, inN => inN, inIdx => inIdx,
                outValid => outValid, outIdx => outIdx, outQ => outQ);

   gref : for i in 0 to 9 generate
      ref : entity work.divider
         port map (clk => clk, start => divStart, done => dones(i), dividend => dividends(i), divisor => divisor,
                   quotient => quotients(i), remainder => remainders(i));
   end generate;

   stim : process
      variable s1, s2 : positive := 1;
      variable r : real;
      variable d : integer;
      type int_array is array(0 to 9) of integer;
      variable nums : int_array;
      variable got : integer;
      variable errs, checks, upstream, corrections : natural := 0;
      variable exact : signed(44 downto 0);
      variable mag : unsigned(44 downto 0);
      variable cycles : natural;
      variable gaps : boolean;
      impure function rnd return real is begin uniform(s1, s2, r); return r; end function;
      impure function rint(lo, hi : integer) return integer is
      begin return lo + integer(floor(rnd * real(hi - lo + 1))) mod (hi - lo + 1); end function;
      impure function pick_d return integer is
         variable x : real; variable v : integer;
      begin
         x := rnd;
         if    x < 0.15 then v := rint(1, 16);
         elsif x < 0.30 then v := 2 ** rint(0, 19); if rnd < 0.5 and v > 1 then v := v - 1; elsif v < 2**19 then v := v + rint(0, 1); end if;
         elsif x < 0.40 then v := rint(522700, 522753);
         elsif x < 0.50 then v := 1048575 - rint(0, 8);
         elsif x < 0.65 then v := rint(1, 1023);
         else v := rint(1, 1048575);
         end if;
         if rnd < 0.5 then v := -v; end if;
         return v;
      end function;
      impure function pick_n(dd : integer) return integer is
         variable x : real; variable v : integer; variable a : integer;
      begin
         a := abs(dd);
         x := rnd;
         if    x < 0.10 then v := 0;
         elsif x < 0.20 then v := 1048575 - rint(0, 4);
         elsif x < 0.30 then v := rint(0, 255);
         elsif x < 0.55 then
            -- n * 4096 just below, at or above a multiple of |d|
            x := floor(rnd * realmin(1048575.0 * 4096.0 / real(a), 2.0e9));
            v := integer(floor(x * real(a) / 4096.0)) + rint(-1, 1);
            if v < 0 then v := 0; end if;
            if v > 1048575 then v := 1048575; end if;
         else v := rint(0, 1048575);
         end if;
         if rnd < 0.5 then v := -v; end if;
         return v;
      end function;
   begin
      s1 := SEED; s2 := SEED + 11;
      wait until rising_edge(clk);
      for c in 0 to CASES - 1 loop
         d := pick_d;
         for k in 0 to 9 loop nums(k) := pick_n(d); end loop;
         gaps := rnd < 0.3;
         wait for 1 ns;
         recipStart <= '1'; recipDivisor <= to_signed(d, 21);
         divStart <= '1'; divisor <= to_signed(d, 25);
         for k in 0 to 9 loop dividends(k) <= resize(to_signed(nums(k), 33) & x"000", 45); end loop;
         wait until rising_edge(clk); wait for 1 ns;
         recipStart <= '0'; recipDivisor <= (others => '0'); divStart <= '0';
         cycles := 0;
         while ready /= '1' loop
            wait until rising_edge(clk); wait for 1 ns; cycles := cycles + 1;
            assert cycles < 40 report "reciprocal too slow" severity failure;
         end loop;
         assert cycles <= 34 report "reciprocal latency " & integer'image(cycles) severity failure;
         -- feed numerators, collect results as they come out
         got := 0;
         for k in 0 to 9 loop
            inValid <= '1'; inN <= to_signed(nums(k), 21); inIdx <= to_unsigned(k, 4);
            if gaps and rnd < 0.5 then
               wait until rising_edge(clk); wait for 1 ns; inValid <= '0'; cycles := cycles + 1;
            end if;
            wait until rising_edge(clk); wait for 1 ns;
            inValid <= '0';
         end loop;
         -- wait for the upstream dividers
         -- the upstream quotients hold after done (48 clocks after the start)
         cycles := cycles + 10;
         while cycles < 52 loop wait until rising_edge(clk); wait for 1 ns; cycles := cycles + 1; end loop;
         -- results are checked in the monitor; wait for the pipeline to drain
         for k in 0 to 5 loop wait until rising_edge(clk); end loop;
         wait for 1 ns;
         for k in 0 to 9 loop
            mag := unsigned(abs(dividends(k))) / resize(unsigned(abs(divisor)), 45);
            if (dividends(k)(44) xor divisor(24)) = '1' then exact := -signed(mag); else exact := signed(mag); end if;
            if unitSeen(k) /= '1' or unitQ(k) /= quotients(k)(31 downto 0) or unitQ(k) /= exact(31 downto 0) then
               errs := errs + 1;
               if errs < 10 then
                  report "mismatch case " & integer'image(c) & " d=" & integer'image(d) & " n=" & integer'image(nums(k)) &
                     " unit=" & integer'image(to_integer(unitQ(k))) & " upstream=" & integer'image(to_integer(quotients(k)(31 downto 0))) severity warning;
               end if;
            end if;
            if quotients(k)(31 downto 0) /= exact(31 downto 0) then upstream := upstream + 1; end if;
            if nums(k) /= 0 and abs(to_integer(quotients(k)(31 downto 0))) > 0 then corrections := corrections + 1; end if;
         end loop;
         clearSeen <= true; wait until rising_edge(clk); wait for 1 ns; clearSeen <= false;
         checks := checks + 10;
      end loop;
      report "RECIP cases=" & integer'image(CASES) & " quotients=" & integer'image(checks) & " nonzero=" & integer'image(corrections) &
         " upstream_vs_exact=" & integer'image(upstream) & " errs=" & integer'image(errs);
      assert errs = 0 and upstream = 0 report "FAIL" severity failure;
      done <= true;
      wait;
   end process;

   -- Record each unit result by index.
   monitor : process
   begin
      wait until rising_edge(clk);
      if outValid = '1' then
         unitQ(to_integer(outIdx)) <= outQ;
         unitSeen(to_integer(outIdx)) <= '1';
      end if;
      if clearSeen then unitSeen <= (others => '0'); end if;
   end process;
end architecture;
