-- SPDX-License-Identifier: GPL-2.0-only
-- Adapt the selected restoring arithmetic; all six lanes remain parallel.
-- Contract: the real polygon/line producers issue one simultaneous batch.
-- A later batch supersedes aborted lanes; only requested completed results
-- are consumed. Result FFs and native45/enhanced53 done clocks are retained.
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
use work.psx_pgxp_gpu_pkg.all;
entity psx_pgxp_divider_bank is
 port(clk,native_width,boundary_batch,line_batch:in std_logic;
      common_denominator:in signed(32 downto 0);
      request:in pgxp_div_array;response:out pgxp_div_array);
end;
architecture parallel_lanes of psx_pgxp_divider_bank is
 type work_array is array(0 to 5)of unsigned(53 downto 0);
 type remainder_array is array(0 to 5)of unsigned(33 downto 0);
 type low_array is array(0 to 5)of unsigned(12 downto 0);
 signal work_q:work_array:=(others=>(others=>'0'));
 signal remainder_q:remainder_array:=(others=>(others=>'0'));
 signal divisor_low:low_array:=(others=>(others=>'0'));
 signal divisor_high:unsigned(33 downto 13):=(others=>'0');
 signal sign_n,mask_q:std_logic_vector(0 to 5):=(others=>'0');
 signal sign_d,native_q:std_logic:='0';
 signal digit:integer range 0 to 53:=0;
 signal active:boolean:=false;
 signal done_q:std_logic:='0';
begin
 process(clk)
  variable launch:boolean;
  variable n_value,q_value:signed(52 downto 0);
  variable d_value,r_value:signed(32 downto 0);
  variable n_magnitude:unsigned(52 downto 0);
  variable d_magnitude:unsigned(33 downto 0);
  variable r_next,diff,d_operand:unsigned(33 downto 0);
  variable bit_q:std_logic;
 begin
  if rising_edge(clk)then
   launch:=request(0).start='1';
   done_q<='0';
   if launch then
    -- synthesis translate_off
    assert request(0).start=request(1).start and request(0).start=request(2).start
      report "GPU divider batch lost required parallel lanes" severity failure;
    if boundary_batch='1'then
     assert line_batch='0' and request(3).start='0' and request(4).start='0' and request(5).start='0'
       report "GPU boundary batch shape" severity failure;
     for i in 0 to 2 loop
      assert request(i).divisor>=0 and request(i).divisor<8192
       report "GPU boundary denominator range" severity failure;
     end loop;
    elsif line_batch='1'then
     assert request(3).start='1' and request(4).start='1' and request(5).start='1'
       report "GPU line batch shape" severity failure;
     assert request(0).divisor>=0 and request(0).divisor<8192
       report "GPU line common denominator range" severity failure;
     assert request(5).divisor>=0 and request(5).divisor<8192
       report "GPU line last denominator range" severity failure;
     for i in 1 to 4 loop
      assert request(i).divisor=request(0).divisor report "GPU line shared denominator" severity failure;
     end loop;
    else
     assert request(3).start='1' and request(4).start=request(5).start
       report "GPU attribute batch shape" severity failure;
     for i in 0 to 5 loop
      assert request(i).divisor=0 report "GPU attributes use one determinant port" severity failure;
     end loop;
    end if;
    -- synthesis translate_on
    active<=true;native_q<=native_width;
    if native_width='1'then digit<=45;else digit<=53;end if;
    -- Snapshot the existing producer determinant ONCE. During all digits,
    -- every subtractor sees only the concatenation of its stored parts.
    d_value:=common_denominator;
    d_magnitude:='0'&unsigned(abs(d_value));
    if native_width='1'then d_magnitude:=shift_right(d_magnitude,8);end if;
    if boundary_batch='1' or line_batch='1'then
     divisor_high<=(others=>'0');sign_d<='0';
    else divisor_high<=d_magnitude(33 downto 13);sign_d<=d_value(32);end if;
    for i in 0 to 5 loop
     if boundary_batch='1'then
      divisor_low(i)<=(others=>'0');
      if i<3 then
       if native_width='1'then divisor_low(i)<=shift_right(unsigned(request(i).divisor(12 downto 0)),4);
       else divisor_low(i)<=unsigned(request(i).divisor(12 downto 0));end if;
      end if;
     elsif line_batch='1'then
      if i=5 then divisor_low(i)<=unsigned(request(5).divisor(12 downto 0));
      else divisor_low(i)<=unsigned(request(0).divisor(12 downto 0));end if;
     else divisor_low(i)<=d_magnitude(12 downto 0);end if;
    end loop;
   elsif active then
    if digit=0 then active<=false;done_q<='1';else digit<=digit-1;end if;
   end if;
   for i in 0 to 5 loop
    response(i).start<='0';response(i).dividend<=(others=>'0');response(i).divisor<=(others=>'0');
    response(i).done<=done_q and mask_q(i);
    r_next:=remainder_q(i);
    if launch then
     mask_q(i)<=request(i).start;
     if request(i).start='1'then
      r_next:=(others=>'0');n_value:=request(i).dividend;
      if native_width='1' and line_batch='1'then n_value:=resize(n_value(44 downto 0),53);end if;
      n_magnitude:=unsigned(abs(n_value));sign_n(i)<=n_value(52);
      if native_width='1' and line_batch='1'then work_q(i)<=shift_left(n_magnitude,8)&'0';
      elsif native_width='1' and boundary_batch='1'then
       -- Q4 biased magnitude =16*originalMagnitude+15 (unless dx=0).
       -- Truncate the UNSIGNED magnitude, avoiding negative SAR rounding.
       work_q(i)<=n_magnitude(48 downto 4)&"00000000"&'0';
      else
       -- Native attributes already carry their original magnitude<<8.
       -- The same work alignment supports 45 native or53 enhanced digits.
       work_q(i)<=n_magnitude&'0';
      end if;
     end if;
    elsif active and mask_q(i)='1'then
     d_operand:=divisor_high&divisor_low(i);
     diff:=remainder_q(i)-d_operand;
     if(native_q='1'and diff(25)='0')or(native_q='0'and diff(33)='0')then
      r_next:=shift_left(diff,1);bit_q:='1';
     else r_next:=shift_left(remainder_q(i),1);bit_q:='0';end if;
     if native_q='1'then r_next(33 downto 26):=(others=>'0');end if;
     r_next(0):=work_q(i)(53);work_q(i)<=work_q(i)(52 downto 0)&bit_q;
    end if;
    remainder_q(i)<=r_next;
    q_value:=signed(work_q(i)(52 downto 0));r_value:=signed(r_next(33 downto 1));
    if(sign_n(i)xor sign_d)='1'then q_value:=-q_value;end if;
    if sign_n(i)='1'then r_value:=-r_value;end if;
    if native_q='1'then
     response(i).quotient<=resize(q_value(44 downto 0),53);
     response(i).remainder<=resize(r_value(24 downto 0),33);
    else response(i).quotient<=q_value;response(i).remainder<=r_value;end if;
   end loop;
  end if;
 end process;
end;
