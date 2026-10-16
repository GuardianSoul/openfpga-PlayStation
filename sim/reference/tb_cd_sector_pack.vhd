-- cd-sector-pack address/latency check: the original mem.dpram addressed
-- as sector & offset against the packed RAM addressed as sector*640+offset
-- (the expressions cd_top uses after the transform), with random writes and
-- reads of every sector and offsets up to 588; q_b compared every cycle.
library IEEE;
use IEEE.std_logic_1164.all;
use IEEE.numeric_std.all;
use IEEE.math_real.all;
library mem;

entity tb_cd_sector_pack is
   generic (SEED : integer := 1; CYCLES : integer := 300000);
end entity;

architecture sim of tb_cd_sector_pack is
   signal clk1x : std_logic := '0';
   signal wsec, rsec : unsigned(2 downto 0) := (others => '0');
   signal woff, roff : integer range 0 to 588 := 0;
   signal wren : std_logic := '0';
   signal data : std_logic_vector(31 downto 0) := (others => '0');
   signal addrA_old, addrB_old, addrA_new, addrB_new : std_logic_vector(12 downto 0);
   signal q_old, q_new : std_logic_vector(31 downto 0);
   type tPacked is array(0 to 5119) of std_logic_vector(31 downto 0);
   signal packed : tPacked;
begin
   clk1x <= not clk1x after 10 ns;
   addrA_old <= std_logic_vector(wsec & to_unsigned(woff, 10));
   addrB_old <= std_logic_vector(rsec & to_unsigned(roff, 10));
   addrA_new <= std_logic_vector(resize(wsec & "000000000", 13) + resize(wsec & "0000000", 13) + to_unsigned(woff, 13));
   addrB_new <= std_logic_vector(resize(rsec & "000000000", 13) + resize(rsec & "0000000", 13) + to_unsigned(roff, 13));
   old_ram : entity mem.dpram generic map (addr_width => 13, data_width => 32)
      port map (clock_a => clk1x, address_a => addrA_old, data_a => data, wren_a => wren,
                clock_b => clk1x, address_b => addrB_old, data_b => x"00000000", wren_b => '0', q_b => q_old);
   process (clk1x) begin
      if rising_edge(clk1x) then
         if wren = '1' then packed(to_integer(unsigned(addrA_new))) <= data; end if;
         q_new <= packed(to_integer(unsigned(addrB_new)));
      end if;
   end process;
   process
      variable s1, s2 : positive; variable r : real; variable checks : integer := 0;
      impure function rnd(n : integer) return integer is begin uniform(s1, s2, r); return integer(floor(r * real(n))); end;
   begin
      s1 := SEED; s2 := SEED + 11;
      -- fill every used word once so both RAMs hold defined data
      for s in 0 to 7 loop for o in 0 to 588 loop
         wait until rising_edge(clk1x); wait for 1 ns;
         wsec <= to_unsigned(s, 3); woff <= o; wren <= '1'; data <= std_logic_vector(to_unsigned(s * 1000 + o, 32));
      end loop; end loop;
      for i in 0 to CYCLES - 1 loop
         wait until rising_edge(clk1x); wait for 1 ns;
         wsec <= to_unsigned(rnd(8), 3); woff <= rnd(589); wren <= '0'; if rnd(2) = 0 then wren <= '1'; end if;
         data <= std_logic_vector(to_unsigned(rnd(65536), 16)) & std_logic_vector(to_unsigned(rnd(65536), 16));
         rsec <= to_unsigned(rnd(8), 3); roff <= rnd(589);
         if i > 2 then
            wait for 15 ns;
            if (addrA_old /= addrB_old or wren = '0') then   -- same-word read-during-write is "don't care" in both
               null;
            end if;
            assert q_old = q_new report "cd sector pack mismatch at cycle " & integer'image(i) severity failure;
            checks := checks + 1;
         end if;
      end loop;
      report "PASS cd sector pack checks=" & integer'image(checks);
      std.env.finish;
   end process;
end architecture;
