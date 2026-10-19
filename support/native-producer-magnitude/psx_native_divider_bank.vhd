-- SPDX-License-Identifier: GPL-2.0-only
-- Adapt the selected restoring arithmetic; all six lanes remain parallel.
-- Native45/25 adaptation of the separately qualified parallel bank.
-- Contract: the real polygon/line producers issue one simultaneous batch.
-- A later batch supersedes aborted lanes; only requested completed results
-- are consumed. Result FFs and native45-digit done clocks are retained.
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
use work.psx_native_divider_pkg.all;
entity psx_native_divider_bank is
 port(clk,boundary_batch,line_batch:in std_logic;
      numerator_negative:in std_logic_vector(0 to 5);
      request:in native_div_array;response:out native_div_array);
end;
architecture parallel_lanes of psx_native_divider_bank is
 type work_array is array(0 to 5)of unsigned(45 downto 0);
 type remainder_array is array(0 to 5)of unsigned(25 downto 0);
 type small_array is array(0 to 2)of unsigned(12 downto 0);
 signal work_q:work_array:=(others=>(others=>'0'));
 signal remainder_q:remainder_array:=(others=>(others=>'0'));
 signal small_d:small_array:=(others=>(others=>'0'));
 signal common_d:unsigned(25 downto 0):=(others=>'0');
 signal sign_n,mask_q:std_logic_vector(0 to 5):=(others=>'0');
 signal sign_d,boundary_q,line_q:std_logic:='0';
 signal digit:integer range 0 to 45:=0;
 signal active:boolean:=false;
 signal done_q:std_logic:='0';
begin
 process(clk)
  variable launch:boolean;
  variable q_value:signed(44 downto 0);
  variable d_value,r_value:signed(24 downto 0);
  variable n_magnitude:unsigned(44 downto 0);
  variable r_next,diff,d_operand:unsigned(25 downto 0);
  variable bit_q,d_negative:std_logic;
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
     assert request(5).divisor>=0 and request(5).divisor<8192
       report "GPU line last denominator range" severity failure;
     for i in 1 to 4 loop
      assert request(i).divisor=request(0).divisor report "GPU line shared denominator" severity failure;
     end loop;
    else
     assert request(3).start='1' and request(4).start=request(5).start
       report "GPU attribute batch shape" severity failure;
     for i in 1 to 5 loop
      if request(i).start='1'then assert request(i).divisor=request(0).divisor
       report "GPU attribute shared denominator" severity failure;end if;
     end loop;
    end if;
    -- synthesis translate_on
    active<=true;boundary_q<=boundary_batch;line_q<=line_batch;
    digit<=45;
    d_value:=request(0).divisor;
    common_d<='0'&unsigned(abs(d_value));sign_d<=d_value(24);
    if boundary_batch='1'then
     for i in 0 to 2 loop small_d(i)<=unsigned(request(i).divisor(12 downto 0));end loop;
    elsif line_batch='1'then small_d(2)<=unsigned(request(5).divisor(12 downto 0));end if;
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
      r_next:=(others=>'0');
      n_magnitude:=unsigned(request(i).dividend);sign_n(i)<=numerator_negative(i);
      work_q(i)<=n_magnitude&'0';
     end if;
    elsif active and mask_q(i)='1'then
     d_operand:=common_d;
     if i<3 then
      if boundary_q='1'then d_operand:=resize(small_d(i),26);end if;
     elsif i=5 then
      if line_q='1'then d_operand:=resize(small_d(2),26);end if;
     end if;
     diff:=remainder_q(i)-d_operand;
     if diff(25)='0'then
      r_next:=shift_left(diff,1);bit_q:='1';
     else r_next:=shift_left(remainder_q(i),1);bit_q:='0';end if;
     r_next(0):=work_q(i)(45);work_q(i)<=work_q(i)(44 downto 0)&bit_q;
    end if;
    remainder_q(i)<=r_next;
    d_negative:=sign_d;
    if(i<3 and boundary_q='1')or(i=5 and line_q='1')then d_negative:='0';end if;
    q_value:=signed(work_q(i)(44 downto 0));r_value:=signed(r_next(25 downto 1));
    if(sign_n(i)xor d_negative)='1'then q_value:=-q_value;end if;
    if sign_n(i)='1'then r_value:=-r_value;end if;
    response(i).quotient<=q_value;response(i).remainder<=r_value;
   end loop;
  end if;
 end process;
end;
