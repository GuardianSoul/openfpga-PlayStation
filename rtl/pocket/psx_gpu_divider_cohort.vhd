-- SPDX-License-Identifier: GPL-2.0-only
-- Consumer-bound adaptation of PSX_MiSTer's packed restoring divider.
-- All active lanes launch together; only lane zero's done is consumed.
-- Idle/inactive lane outputs are deliberately not a generic divider contract.
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
entity psx_gpu_divider_cohort is port (
 clk : in std_logic;
 starts : in std_logic_vector(5 downto 0);
 dividends : in std_logic_vector(269 downto 0);
 divisors : in std_logic_vector(149 downto 0);
 quotients : out std_logic_vector(269 downto 0);
 remainders : out std_logic_vector(149 downto 0);
 done : out std_logic := '0'
);end;
architecture adapted of psx_gpu_divider_cohort is
 type t_work is array(0 to 5)of unsigned(45 downto 0);
 type t_rem is array(0 to 5)of unsigned(25 downto 0);
 type t_div is array(0 to 3)of unsigned(25 downto 0);
 signal work_q:t_work:=(others=>(others=>'0'));
 signal remainder_q:t_rem;
 signal divisor_q:t_div;
 signal sign_n:std_logic_vector(5 downto 0):=(others=>'0');
 signal sign_d:std_logic_vector(3 downto 0):=(others=>'0');
 signal digit:integer range 0 to 45:=0;
 signal active:boolean:=false;
 signal done_q:std_logic:='0';
 function group_of(i:integer)return integer is
 begin case i is when 3=>return 0;when 4=>return 1;when 5=>return 3;when others=>return i;end case;end;
begin
 process(clk)
  variable r_next,diff:unsigned(25 downto 0);
  variable bit_q:std_logic;
  variable n:signed(44 downto 0);
  variable d:signed(24 downto 0);
  variable j,g:integer;
  variable launch:boolean;
 begin
  if rising_edge(clk)then
   launch:=starts/="000000";
   -- synthesis translate_off
   assert not launch or starts(0)='1' report "cohort requires primary launch" severity failure;
   assert starts(1)=starts(0) and starts(2)=starts(0) report "cohort primary mask" severity failure;
   assert starts(3)=starts(4) or (starts(3)='1' and starts(4)='0') report "cohort trailing mask" severity failure;
   assert starts(5)='0' or starts(4)='1' report "cohort final mask" severity failure;
   if starts(3)='1'then assert starts(0)='1' and divisors(99 downto 75)=divisors(24 downto 0) report "cohort divisor 3 lease" severity failure;end if;
   if starts(4)='1'then assert starts(1)='1' and divisors(124 downto 100)=divisors(49 downto 25) report "cohort divisor 4 lease" severity failure;end if;
   -- synthesis translate_on
   done_q<='0';done<=done_q;
   if launch then
    active<=true;digit<=45;
    for k in 0 to 3 loop
     if k=3 then j:=5;else j:=k;end if;
     if starts(j)='1'then
      d:=signed(divisors(j*25+24 downto j*25));
      divisor_q(k)<='0'&unsigned(abs(d));sign_d(k)<=d(24);
     end if;
    end loop;
   elsif active then
    if digit=0 then active<=false;done_q<='1';else digit<=digit-1;end if;
   end if;
   for i in 0 to 5 loop
    g:=group_of(i);r_next:=remainder_q(i);
    if launch then
     if starts(i)='1'then
      n:=signed(dividends(i*45+44 downto i*45));
      work_q(i)<=unsigned(abs(n))&'0';sign_n(i)<=n(44);r_next:=(others=>'0');
     end if;
    elsif active then
     diff:=remainder_q(i)-divisor_q(g);
     if diff(25)='0'then r_next:=shift_left(diff,1);bit_q:='1';else r_next:=shift_left(remainder_q(i),1);bit_q:='0';end if;
     r_next(0):=work_q(i)(45);work_q(i)<=work_q(i)(44 downto 0)&bit_q;
    end if;
    remainder_q(i)<=r_next;
    if(sign_n(i)xor sign_d(g))='1'then quotients(i*45+44 downto i*45)<=std_logic_vector(-signed(work_q(i)(44 downto 0)));
    else quotients(i*45+44 downto i*45)<=std_logic_vector(work_q(i)(44 downto 0));end if;
    if sign_n(i)='1'then remainders(i*25+24 downto i*25)<=std_logic_vector(-signed(r_next(25 downto 1)));
    else remainders(i*25+24 downto i*25)<=std_logic_vector(r_next(25 downto 1));end if;
   end loop;
  end if;
 end process;
end;
