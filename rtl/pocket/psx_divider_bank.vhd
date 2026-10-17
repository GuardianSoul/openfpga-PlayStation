-- SPDX-License-Identifier: GPL-2.0-only
-- Private adaptation of the pinned PSX_MiSTer signed restoring divider.
-- Reuse a bank for unconsumed numerator bits and accumulated quotient bits.
-- Intermediate quotient/remainder values while busy are not preserved.
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity divider is
   port (
      clk       : in std_logic;
      start     : in std_logic;
      done      : out std_logic := '0';
      busy      : out std_logic := '0';
      dividend  : in signed;
      divisor   : in signed;
      quotient  : out signed;
      remainder : out signed
   );
end entity;

architecture packed of divider is
   signal work_q : unsigned(dividend'length downto 0) := (others => '0');
   signal divisor_q : unsigned(divisor'length downto 0);
   signal remainder_q : unsigned(divisor'length downto 0);
   signal digit : integer range 0 to quotient'length := 0;
   signal active : boolean := false;
   signal done_q : std_logic := '0';
   signal sign_n, sign_d : std_logic := '0';
begin
   assert dividend'length = quotient'length and dividend'right = 0 and
          divisor'right = 0 and quotient'right = 0 and remainder'right = 0 and
          remainder'length = divisor'length
      report "Packed divider requires the selected equal dividend/quotient and divisor/remainder widths"
      severity failure;
   process(clk)
      variable r_next, diff : unsigned(divisor'length downto 0);
      variable bit_q : std_logic;
   begin
      if rising_edge(clk) then
         done_q <= '0';
         busy <= '0';
         r_next := remainder_q;
         if start = '1' then
            busy <= '1';
            active <= true;
            digit <= quotient'length;
            work_q <= unsigned(abs(dividend)) & '0';
            divisor_q <= '0' & unsigned(abs(divisor));
            sign_n <= dividend(dividend'left);
            sign_d <= divisor(divisor'left);
            r_next := (others => '0');
         elsif active then
            busy <= '1';
            diff := remainder_q - divisor_q;
            if diff(diff'left) = '0' then
               r_next := shift_left(diff, 1);
               bit_q := '1';
            else
               r_next := shift_left(remainder_q, 1);
               bit_q := '0';
            end if;
            r_next(0) := work_q(work_q'left);
            work_q <= work_q(work_q'left-1 downto 0) & bit_q;
            if digit = 0 then
               active <= false;
               done_q <= '1';
            else
               digit <= digit - 1;
            end if;
         end if;
         remainder_q <= r_next;
         if (sign_n xor sign_d) = '1' then
            quotient <= -signed(work_q(quotient'left downto 0));
         else
            quotient <= signed(work_q(quotient'left downto 0));
         end if;
         if sign_n = '1' then
            remainder <= -signed(r_next(remainder'left+1 downto 1));
         else
            remainder <= signed(r_next(remainder'left+1 downto 1));
         end if;
         done <= done_q;
      end if;
   end process;
end architecture;
