-- SPDX-License-Identifier: GPL-3.0-or-later
-- gpu-recip keeps three GPU dividers with an 11-bit divisor. This bench runs
-- the release packed divider (rtl/pocket/psx_divider_bank.vhd, entity
-- divider) with the 11-bit divisor next to the pinned PSX_MiSTer divider with
-- the upstream 25-bit divisor (compiled as divider_mister) on the operands
-- gpu_poly and gpu_line start: edge slopes (dx << 32 adjusted by dy - 1,
-- |dx| < 1024, 0 <= dy < 512, dy = 0 included), line steps
-- ((d << 32) +- (points - 1), |d| <= points <= 1023) and the line's
-- points / |dy|. The full 45-bit quotient (except for a zero dy, whose
-- quotient gpu_poly never uses) and the done pulse must match.
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
use ieee.math_real.all;

entity tb_gpu_narrow_divider is
   generic (CASES : integer := 20000; SEED : integer := 1);
end entity;

architecture sim of tb_gpu_narrow_divider is
   signal clk : std_logic := '0';
   signal finished : boolean := false;
   signal start : std_logic := '0';
   signal dividend : signed(44 downto 0) := (others => '0');
   signal divisor : signed(24 downto 0) := (others => '0');
   signal qWide, qNarrow : signed(44 downto 0);
   signal rWide : signed(24 downto 0);
   signal rNarrow : signed(10 downto 0);
   signal doneWide, doneNarrow : std_logic;
begin
   clk <= not clk after 5 ns when not finished;

   wide : entity work.divider_mister
      port map (clk => clk, start => start, done => doneWide, dividend => dividend, divisor => divisor,
                quotient => qWide, remainder => rWide);
   narrow : entity work.divider
      port map (clk => clk, start => start, done => doneNarrow, dividend => dividend, divisor => divisor(10 downto 0),
                quotient => qNarrow, remainder => rNarrow);

   process
      variable s1, s2 : positive := 1;
      variable r : real;
      variable kind, dx, dy, p, errs, zeros : integer := 0;
      variable d45 : signed(44 downto 0);
      impure function rnd return real is begin uniform(s1, s2, r); return r; end function;
      impure function rint(lo, hi : integer) return integer is
      begin return lo + integer(floor(rnd * real(hi - lo + 1))) mod (hi - lo + 1); end function;
   begin
      s1 := SEED; s2 := SEED + 5;
      wait until rising_edge(clk);
      for c in 0 to CASES - 1 loop
         kind := c mod 3;
         if kind = 0 then
            -- gpu_poly slopeDividend
            dx := rint(-1023, 1023); dy := rint(0, 511);
            if rnd < 0.05 then dy := 0; elsif rnd < 0.1 then dy := 511; end if;
            if rnd < 0.05 then dx := 1023; elsif rnd < 0.1 then dx := -1023; end if;
            d45 := resize(to_signed(dx, 13) & x"00000000", 45);
            if dx < 0 then d45 := d45 - (dy - 1); elsif dx > 0 then d45 := d45 + (dy - 1); end if;
            dividend <= d45; divisor <= to_signed(dy, 25);
         elsif kind = 1 then
            -- gpu_line x/y steps
            p := rint(1, 1023); if rnd < 0.1 then p := 1023; end if;
            dx := rint(-p, p);
            d45 := resize(to_signed(dx, 13) & x"00000000", 45);
            if dx < 0 then d45 := d45 - (p - 1); else d45 := d45 + (p - 1); end if;
            dividend <= d45; divisor <= to_signed(p, 25);
         else
            -- gpu_line points / |dy| (1 when dy = 0)
            p := rint(1, 1023); dy := rint(1, 511);
            dividend <= to_signed(p, 45); divisor <= to_signed(dy, 25);
         end if;
         start <= '1';
         wait until rising_edge(clk); wait for 1 ns;
         start <= '0';
         for k in 0 to 60 loop
            wait until rising_edge(clk); wait for 1 ns;
            if doneWide /= doneNarrow then errs := errs + 1; end if;
            exit when doneWide = '1';
         end loop;
         wait until rising_edge(clk); wait for 1 ns;
         -- gpu_poly ignores the quotient of a zero dy (vt(1).y = vt(0).y or
         -- vt(2).y = vt(1).y select 0 instead; dy1 = 0 is culled)
         if to_integer(divisor) = 0 then zeros := zeros + 1;
         elsif qWide /= qNarrow then
            errs := errs + 1;
            if errs < 10 then
               report "mismatch kind " & integer'image(kind) & " divisor " & integer'image(to_integer(divisor)) severity warning;
            end if;
         end if;
      end loop;
      report "NARROW cases=" & integer'image(CASES) & " zero_dy=" & integer'image(zeros) & " errs=" & integer'image(errs);
      assert errs = 0 report "FAIL" severity failure;
      finished <= true;
      wait;
   end process;
end architecture;
