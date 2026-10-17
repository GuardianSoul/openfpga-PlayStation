library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
use ieee.std_logic_textio.all;
use std.textio.all;
use std.env.all;
library candidate;
entity tb is
   generic (N : positive := 33; D : positive := 33);
end entity;
architecture test of tb is
   signal clk : std_logic := '0';
   signal start : std_logic := '0';
   signal dividend : signed(N-1 downto 0) := (others => '0');
   signal divisor : signed(D-1 downto 0) := (others => '0');
   signal done_a, done_b, busy_a, busy_b : std_logic;
   signal qa, qb : signed(N-1 downto 0);
   signal ra, rb : signed(D-1 downto 0);
begin
   clk <= not clk after 5 ns;
   a : entity work.divider port map(clk,start,done_a,busy_a,dividend,divisor,qa,ra);
   b : entity candidate.divider port map(clk,start,done_b,busy_b,dividend,divisor,qb,rb);
   process
      file vectors : text open read_mode is "vectors.hex";
      variable l : line;
      variable x : std_logic_vector(N-1 downto 0);
      variable y : std_logic_vector(D-1 downto 0);
      variable eq : std_logic_vector(N-1 downto 0);
      variable er : std_logic_vector(D-1 downto 0);
      variable abort_after, gaps : integer;
      variable ticks, completed, aborted : integer := 0;
      procedure tick is
      begin
         wait until rising_edge(clk); wait for 1 ps;
         assert done_a = done_b report "Done mismatch" severity failure;
         assert busy_a = busy_b report "Busy mismatch" severity failure;
      end procedure;
   begin
      tick;
      while not endfile(vectors) loop
         readline(vectors,l);
         hread(l,x);hread(l,y);hread(l,eq);hread(l,er);read(l,abort_after);read(l,gaps);
         wait until falling_edge(clk);
         dividend <= signed(x);divisor <= signed(y);start <= '1';tick;
         wait until falling_edge(clk);start <= '0';ticks := 0;
         loop
            tick;ticks:=ticks+1;
            if done_a = '1' then
               assert ticks=N+2 report "Publication cycle changed" severity failure;
               assert qa=qb and ra=rb report "Reference result mismatch" severity failure;
               assert qb=signed(eq) and rb=signed(er) report "Integer oracle mismatch" severity failure;
               completed:=completed+1;
               exit;
            end if;
            if abort_after>0 and ticks=abort_after then aborted:=aborted+1;exit;end if;
            assert ticks<N+5 report "Divider timeout" severity failure;
         end loop;
         if abort_after=0 then
            for i in 1 to gaps loop
               tick;
               assert qa=qb and ra=rb report "Idle result mismatch" severity failure;
            end loop;
         end if;
      end loop;
      report "PASS packed divider completed="&integer'image(completed)&" aborted="&integer'image(aborted);
      finish;
   end process;
end architecture;
