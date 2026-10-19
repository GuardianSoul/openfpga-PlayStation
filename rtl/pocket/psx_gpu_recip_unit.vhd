-- SPDX-License-Identifier: GPL-2.0-only
-- Shared-denominator gradient divider for the PSX_MiSTer GPU (gpu-recip).
--
-- gpu_poly divides up to ten gradient numerators of one triangle by the same
-- denom, and gpu_line three colour steps by the same points. Upstream starts
-- a 46-clock restoring divider per quotient; this unit computes one
-- reciprocal R = floor(2^32 / |d|) per triangle or line (33 clocks, started
-- with the edge divisions) and then one quotient per clock through a
-- pipelined multiply:
--
--    qe = floor(|n| * R / 2^20)       (= floor(|n| * 2^12 * R / 2^32))
--    r  = |n| * 2^12 - qe * |d|       (0 <= r < 2 * |d|, see below)
--    q  = qe + 1 when r >= |d| else qe
--    out = -q when sign(n) /= sign(d) else q, bits 31..0
--
-- which equals the upstream divider's quotient(31 downto 0) for the dividend
-- n * 2^12 and divisor d (truncation toward zero, sign from both operands).
-- Exactness: with e = 2^32/|d| - R in [0, 1), |n| * 2^12 / |d| - |n| * R / 2^20
-- = |n| * e / 2^20 < 1 for |n| < 2^20, so qe is q or q - 1 and r < 2 * |d|.
-- r is formed modulo 2^21, which holds it because 2 * |d| <= 2^21.
-- Everything advances with the GPU clock enable, like the engines.
-- Callers guarantee |n| < 2^20 and 0 < |d| < 2^20 (the polygon and line
-- culling bounds: |n| <= 521730, |denom| <= 522753, points <= 1023).
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity psx_gpu_recip_unit is
   port (
      clk          : in  std_logic;
      ce           : in  std_logic;
      reset        : in  std_logic;
      recipStart   : in  std_logic;
      recipDivisor : in  signed(20 downto 0);
      recipReady   : out std_logic;
      inValid      : in  std_logic;
      inN          : in  signed(20 downto 0);
      inIdx        : in  unsigned(3 downto 0);
      outValid     : out std_logic := '0';
      outIdx       : out unsigned(3 downto 0) := (others => '0');
      outQ         : out signed(31 downto 0) := (others => '0')
   );
end entity;

architecture rtl of psx_gpu_recip_unit is
   signal dAbs      : unsigned(19 downto 0) := (others => '0');
   signal dSign     : std_logic := '0';
   signal recip     : unsigned(32 downto 0) := (others => '0');
   signal remAcc    : unsigned(19 downto 0) := (others => '0');
   signal step      : integer range 0 to 33 := 0;
   signal ready     : std_logic := '0';

   signal s1Valid   : std_logic := '0';
   signal s1N       : unsigned(19 downto 0) := (others => '0');
   signal s1Sign    : std_logic := '0';
   signal s1Idx     : unsigned(3 downto 0) := (others => '0');

   signal s2Valid   : std_logic := '0';
   signal s2Q       : unsigned(31 downto 0) := (others => '0');
   signal s2A       : unsigned(20 downto 0) := (others => '0');
   signal s2Sign    : std_logic := '0';
   signal s2Idx     : unsigned(3 downto 0) := (others => '0');

   signal s3Valid   : std_logic := '0';
   signal s3Q       : unsigned(31 downto 0) := (others => '0');
   signal s3R       : unsigned(20 downto 0) := (others => '0');
   signal s3Sign    : std_logic := '0';
   signal s3Idx     : unsigned(3 downto 0) := (others => '0');
begin
   -- A start in this clock makes the old reciprocal stale.
   recipReady <= ready and not recipStart;

   process (clk)
      variable acc     : unsigned(20 downto 0);
      variable product : unsigned(52 downto 0);
      variable c       : std_logic;
      variable carry   : unsigned(0 downto 0);
   begin
      if rising_edge(clk) then
         if (ce = '1') then
         -- Reciprocal: restoring division of 2^32 (a one, then 32 zeros).
         if (recipStart = '1') then
            if (recipDivisor < 0) then
               dAbs <= unsigned(-recipDivisor(19 downto 0));
            else
               dAbs <= unsigned(recipDivisor(19 downto 0));
            end if;
            dSign  <= recipDivisor(20);
            remAcc <= (others => '0');
            step   <= 33;
            ready  <= '0';
         elsif (step > 0) then
            if (step = 33) then acc := (0 => '1', others => '0'); else acc := remAcc & '0'; end if;
            if (acc >= ('0' & dAbs)) then
               acc := acc - ('0' & dAbs);
               recip <= recip(31 downto 0) & '1';
            else
               recip <= recip(31 downto 0) & '0';
            end if;
            remAcc <= acc(19 downto 0);
            step   <= step - 1;
            if (step = 1) then ready <= '1'; end if;
         end if;

         -- Stage 1: magnitude and result sign.
         s1Valid <= inValid;
         s1Idx   <= inIdx;
         s1Sign  <= inN(20) xor dSign;
         if (inN < 0) then s1N <= unsigned(-inN(19 downto 0)); else s1N <= unsigned(inN(19 downto 0)); end if;

         -- Stage 2: estimate floor(|n| * R / 2^20), keep |n| * 2^12 mod 2^21.
         product := s1N * recip;
         s2Valid <= s1Valid;
         s2Idx   <= s1Idx;
         s2Sign  <= s1Sign;
         s2Q     <= product(51 downto 20);
         s2A     <= s1N(8 downto 0) & x"000";

         -- Stage 3: remainder modulo 2^21.
         s3Valid <= s2Valid;
         s3Idx   <= s2Idx;
         s3Sign  <= s2Sign;
         s3Q     <= s2Q;
         s3R     <= s2A - resize(s2Q(20 downto 0) * dAbs, 21);

         -- Stage 4: the correction step and the sign in one adder:
         -- qe + c, or -(qe + c) = not qe + (1 - c).
         if (s3R >= ('0' & dAbs)) then c := '1'; else c := '0'; end if;
         carry(0) := c xor s3Sign;
         outValid <= s3Valid;
         outIdx   <= s3Idx;
         outQ     <= signed((s3Q xor (31 downto 0 => s3Sign)) + carry);

         end if;

         if (reset = '1') then
            s1Valid  <= '0';
            s2Valid  <= '0';
            s3Valid  <= '0';
            outValid <= '0';
            step     <= 0;
            ready    <= '0';
         end if;
      end if;
   end process;
end architecture;
