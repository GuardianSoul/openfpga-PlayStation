-- SPDX-License-Identifier: GPL-2.0-only
-- Actual combined PGXP GPU divider: share magnitude, restoring subtraction
-- and sign correction across native45/25 and enhanced53/33 operations.
-- Native wrapping is a width selection on the shared result, not a second ALU.
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
entity psx_pgxp_divider is
 port(clk,start,native_width:in std_logic;done:out std_logic:='0';busy:out std_logic:='0';
      dividend:in signed(52 downto 0);divisor:in signed(32 downto 0);
      quotient:out signed(52 downto 0);remainder:out signed(32 downto 0));
end entity;
architecture packed of psx_pgxp_divider is
 signal work_q:unsigned(53 downto 0):=(others=>'0');
 signal divisor_q,remainder_q:unsigned(33 downto 0);
 signal digit:integer range 0 to 53:=0;
 signal active:boolean:=false;
 signal done_q,sign_n,sign_d,native_q:std_logic:='0';
begin
 process(clk)
  variable r_next,diff:unsigned(33 downto 0);
  variable n_value,q_value:signed(52 downto 0);
  variable d_value,r_value:signed(32 downto 0);
  variable n_magnitude:unsigned(52 downto 0);
  variable bit_q:std_logic;
 begin
  if rising_edge(clk)then
   done_q<='0';busy<='0';r_next:=remainder_q;
   if start='1'then
    busy<='1';active<=true;native_q<=native_width;r_next:=(others=>'0');
    n_value:=dividend;d_value:=divisor;
    if native_width='1'then n_value:=resize(dividend(44 downto 0),53);d_value:=resize(divisor(24 downto 0),33);end if;
    n_magnitude:=unsigned(abs(n_value));divisor_q<='0'&unsigned(abs(d_value));
    sign_n<=n_value(52);sign_d<=d_value(32);
    if native_width='1'then digit<=45;work_q<=shift_left(n_magnitude,8)&'0';
    else digit<=53;work_q<=n_magnitude&'0';end if;
   elsif active then
    busy<='1';diff:=remainder_q-divisor_q;
    if (native_q='1' and diff(25)='0') or (native_q='0' and diff(33)='0')then r_next:=shift_left(diff,1);bit_q:='1';
    else r_next:=shift_left(remainder_q,1);bit_q:='0';end if;
    if native_q='1'then r_next(33 downto 26):=(others=>'0');end if;
    r_next(0):=work_q(53);work_q<=work_q(52 downto 0)&bit_q;
    if digit=0 then active<=false;done_q<='1';else digit<=digit-1;end if;
   end if;
   remainder_q<=r_next;
   q_value:=signed(work_q(52 downto 0));r_value:=signed(r_next(33 downto 1));
   if(sign_n xor sign_d)='1'then q_value:=-q_value;end if;
   if sign_n='1'then r_value:=-r_value;end if;
   if native_q='1'then quotient<=resize(q_value(44 downto 0),53);remainder<=resize(r_value(24 downto 0),33);
   else quotient<=q_value;remainder<=r_value;end if;
   done<=done_q;
  end if;
 end process;
end architecture;
