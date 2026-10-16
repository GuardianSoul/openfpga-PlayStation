-- Lockstep check: PSX_MiSTer memctrl (renamed memctrl_ref) against
-- rtl/pocket/memctrl_lean.vhd. Inputs change just after rising clk1x
-- edges; every output is compared just before each rising edge.
library IEEE;
use IEEE.std_logic_1164.all;
use IEEE.numeric_std.all;
use IEEE.math_real.all;

entity tb_memctrl_lean is
   generic (SEED : integer := 1; CYCLES : integer := 400000);
end entity;

architecture sim of tb_memctrl_lean is
   signal clk1x, ce, reset : std_logic := '0';
   signal bus_addr : unsigned(5 downto 0) := (others => '0');
   signal bus2_addr : unsigned(3 downto 0) := (others => '0');
   signal bus_dataWrite, bus2_dataWrite : std_logic_vector(31 downto 0) := (others => '0');
   signal bus_read, bus_write, bus2_read, bus2_write : std_logic := '0';
   type tOut is record
      dr, dr2 : std_logic_vector(31 downto 0);
      err : std_logic;
      spu, cd, bios, ex1, ex2, ex3 : unsigned(13 downto 0);
      c0, c1, c2, c3 : unsigned(3 downto 0);
      ton : std_logic; tval : unsigned(3 downto 0);
   end record;
   signal a, b : tOut;
   signal ssr_a, ssr_b : std_logic_vector(31 downto 0);
   constant ZERO32 : std_logic_vector(31 downto 0) := (others => '0');
begin
   clk1x <= not clk1x after 10 ns;

   ref : entity work.memctrl_ref port map (clk1x, ce, reset, bus_addr, bus_dataWrite, bus_read, bus_write, a.dr,
      bus2_addr, bus2_dataWrite, bus2_read, bus2_write, a.dr2, a.err, a.spu, a.cd, a.bios, a.ex1, a.ex2, a.ex3,
      a.c0, a.c1, a.c2, a.c3, a.ton, a.tval, '0', '0', ZERO32, "00000", '0', '0', ssr_a);
   dut : entity work.memctrl port map (clk1x, ce, reset, bus_addr, bus_dataWrite, bus_read, bus_write, b.dr,
      bus2_addr, bus2_dataWrite, bus2_read, bus2_write, b.dr2, b.err, b.spu, b.cd, b.bios, b.ex1, b.ex2, b.ex3,
      b.c0, b.c1, b.c2, b.c3, b.ton, b.tval, '0', '0', ZERO32, "00000", '0', '0', ssr_b);

   process
      variable s1, s2 : positive;
      variable r : real;
      variable reads, writes, nonreset_reads : integer := 0;
      impure function rnd(n : integer) return integer is begin uniform(s1, s2, r); return integer(floor(r * real(n))); end;
      impure function rword return std_logic_vector is
      begin return std_logic_vector(to_unsigned(rnd(65536), 16)) & std_logic_vector(to_unsigned(rnd(65536), 16)); end;
   begin
      s1 := SEED; s2 := SEED * 7 + 3;
      reset <= '1';
      wait until rising_edge(clk1x); wait until rising_edge(clk1x);
      for i in 0 to CYCLES - 1 loop
         wait until rising_edge(clk1x); wait for 1 ns;
         ce <= '1'; if rnd(8) = 0 then ce <= '0'; end if;
         reset <= '0'; if rnd(20000) = 0 then reset <= '1'; end if;
         if rnd(4) = 0 then bus_addr <= to_unsigned(rnd(64), 6); else bus_addr <= to_unsigned(rnd(10) * 4, 6); end if;
         bus_dataWrite <= rword; bus_read <= '0'; bus_write <= '0';
         case rnd(4) is when 0 => bus_read <= '1'; reads := reads + 1; when 1 => bus_write <= '1'; writes := writes + 1; when others => null; end case;
         bus2_addr <= to_unsigned(rnd(3), 4); bus2_dataWrite <= rword; bus2_read <= '0'; bus2_write <= '0';
         case rnd(6) is when 0 => bus2_read <= '1'; when 1 => bus2_write <= '1'; when others => null; end case;
         wait for 18 ns;   -- just before the next rising edge
         assert a = b report "memctrl lean mismatch at cycle " & integer'image(i) severity failure;
         if a.dr /= ZERO32 then nonreset_reads := nonreset_reads + 1; end if;
      end loop;
      report "PASS memctrl lean cycles=" & integer'image(CYCLES) & " reads=" & integer'image(reads) & " writes=" & integer'image(writes) & " nonzero_reads=" & integer'image(nonreset_reads);
      std.env.finish;
   end process;
end architecture;
