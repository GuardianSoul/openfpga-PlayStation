-- SPDX-License-Identifier: GPL-2.0-only
-- Authored lane/result scheduling comparisons with unchanged MiSTer dividers.
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
entity tb_pool is end;
architecture test of tb_pool is
 signal clk:std_logic:='0'; signal reset:std_logic:='1';
 signal starts,dones,old_done:std_logic_vector(5 downto 0):=(others=>'0');
 signal nums,qs,old_q:signed(269 downto 0):=(others=>'0');
 signal dens,rs,old_r:signed(149 downto 0):=(others=>'0');
begin
 clk<=not clk after 5 ns;
 pool:entity work.psx_gpu_divider_pool port map(clk,reset,starts,nums,dens,dones,qs,rs);
 lanes:for i in 0 to 5 generate
  signal dividend,quotient:signed(44 downto 0);signal divisor,remainder:signed(24 downto 0);
 begin
  dividend<=nums(i*45+44 downto i*45);divisor<=dens(i*25+24 downto i*25);
  old_q(i*45+44 downto i*45)<=quotient;old_r(i*25+24 downto i*25)<=remainder;
  baseline:entity work.divider port map(clk=>clk,start=>starts(i),done=>old_done(i),busy=>open,
   dividend=>dividend,divisor=>divisor,quotient=>quotient,remainder=>remainder);
 end generate;
 process
  variable mask,seen:std_logic_vector(5 downto 0);
  variable expected_q:signed(269 downto 0);variable expected_r:signed(149 downto 0);
  variable checks,batches,n:integer:=0;
 begin
  wait until falling_edge(clk);reset<='0';
  for trial in 0 to 159 loop
   if trial>=120 then
    starts<="111111";wait until falling_edge(clk);starts<=(others=>'0');
    for delayed in 0 to (trial*17) mod 300 loop wait until falling_edge(clk);end loop;
    reset<='1';wait until falling_edge(clk);wait until falling_edge(clk);reset<='0';
    wait until falling_edge(clk);
    assert dones="000000" report "Aborted batch leaked completion" severity failure;
   end if;
   mask:=std_logic_vector(to_unsigned((trial mod 63)+1,6));
   for i in 0 to 5 loop
    nums(i*45+44 downto i*45)<=shift_left(to_signed((trial*9137+i*7753)-700000,45),20);
    if (trial+i) mod 11=0 then dens(i*25+24 downto i*25)<=to_signed(0,25);
    else dens(i*25+24 downto i*25)<=to_signed((trial*137+i*109)-12000,25);end if;
   end loop;
   if trial>=150 then
    nums(44 downto 0)<=shift_left(to_signed(-1,45),44);
    dens(24 downto 0)<=to_signed((trial mod 3)-1,25);
    nums(89 downto 45)<=shift_left(to_signed(1,45),44)-1;
    dens(49 downto 25)<=shift_left(to_signed(-1,25),24);
   end if;
   wait until falling_edge(clk);starts<=mask;
   for held in 0 to trial mod 4 loop wait until falling_edge(clk);end loop;
   starts<=(others=>'0');seen:=(others=>'0');n:=0;
   while seen/=mask loop
    wait until falling_edge(clk);seen:=seen or (old_done and mask);n:=n+1;
    assert n<100 report "Original divider timeout" severity failure;
   end loop;
   expected_q:=old_q;expected_r:=old_r;n:=0;
   while dones="000000" loop
    wait until falling_edge(clk);n:=n+1;assert n<500 report "Shared divider timeout" severity failure;
   end loop;
   assert dones=mask report "Divider completion mask mismatch" severity failure;
   for i in 0 to 5 loop
    if mask(i)='1' then
     assert qs(i*45+44 downto i*45)=expected_q(i*45+44 downto i*45) report "Divider quotient mismatch" severity failure;
     assert rs(i*25+24 downto i*25)=expected_r(i*25+24 downto i*25) report "Divider remainder mismatch" severity failure;
     checks:=checks+2;
    end if;
   end loop;
   batches:=batches+1;
   wait until falling_edge(clk);assert dones="000000" report "Completion must be a pulse" severity failure;
  end loop;
  report "PASS divider pool batches="&integer'image(batches)&" comparisons="&integer'image(checks);std.env.finish;
 end process;
end;
