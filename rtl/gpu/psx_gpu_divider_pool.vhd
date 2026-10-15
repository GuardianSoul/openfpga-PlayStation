-- SPDX-License-Identifier: GPL-2.0-only
-- Original scheduler reusing the pinned MiSTer signed divider unchanged.
-- Experimental: six simultaneous GPU setup operations execute serially.
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity psx_gpu_divider_pool is
   port (
      clk       : in std_logic;
      reset     : in std_logic;
      starts    : in std_logic_vector(5 downto 0);
      dividends : in signed(269 downto 0);
      divisors  : in signed(149 downto 0);
      dones     : out std_logic_vector(5 downto 0) := (others=>'0');
      quotients : out signed(269 downto 0) := (others=>'0');
      remainders: out signed(149 downto 0) := (others=>'0')
   );
end entity;

architecture serial of psx_gpu_divider_pool is
   type tstate is (IDLE, RELEASE_START, SELECT_LANE, ISSUE, STARTED, WAIT_RESULT,
                  SETTLE1, SETTLE2, COMPLETE);
   type tnumerators is array(0 to 5) of signed(44 downto 0);
   type tdenominators is array(0 to 5) of signed(24 downto 0);
   signal state : tstate := IDLE;
   signal pending, batch : std_logic_vector(5 downto 0) := (others=>'0');
   signal numerator : tnumerators := (others=>(others=>'0'));
   signal denominator : tdenominators := (others=>(others=>'0'));
   signal lane : integer range 0 to 5 := 0;
   signal core_start, core_done : std_logic := '0';
   signal core_numerator, core_quotient : signed(44 downto 0) := (others=>'0');
   signal core_denominator, core_remainder : signed(24 downto 0) := (others=>'0');
begin
   divider_core : entity work.divider
   port map(clk=>clk, start=>core_start, done=>core_done, busy=>open,
            dividend=>core_numerator, divisor=>core_denominator,
            quotient=>core_quotient, remainder=>core_remainder);

   process(clk)
      variable chosen : integer range -1 to 5;
   begin
      if rising_edge(clk) then
         core_start <= '0';
         dones <= (others=>'0');
         if reset='1' then
            state<=IDLE; pending<=(others=>'0'); batch<=(others=>'0');
            quotients<=(others=>'0'); remainders<=(others=>'0');
         else
            -- Clients hold starts through a paused setup edge. Capture the
            -- final operands, then begin only after all strobes are released.
            if state=IDLE or state=RELEASE_START then
               for i in 0 to 5 loop
                  if starts(i)='1' then
                     numerator(i)<=dividends(i*45+44 downto i*45);
                     denominator(i)<=divisors(i*25+24 downto i*25);
                     pending(i)<='1'; batch(i)<='1';
                  end if;
               end loop;
            end if;
            -- synthesis translate_off
            if state/=IDLE and state/=RELEASE_START then
               assert starts="000000" report "GPU divider batch overlapped" severity failure;
            end if;
            -- synthesis translate_on
            case state is
               when IDLE =>
                  if starts/="000000" then state<=RELEASE_START; end if;
               when RELEASE_START =>
                  if starts="000000" then state<=SELECT_LANE; end if;
               when SELECT_LANE =>
                  chosen:=-1;
                  for i in 0 to 5 loop
                     if pending(i)='1' and chosen=-1 then chosen:=i; end if;
                  end loop;
                  if chosen=-1 then state<=SETTLE1;
                  else
                     lane<=chosen;
                     core_numerator<=numerator(chosen);
                     core_denominator<=denominator(chosen);
                     state<=ISSUE;
                  end if;
               when ISSUE =>
                  core_start<='1'; state<=STARTED;
               when STARTED =>
                  -- Ignore a stale completion from an aborted prior batch.
                  state<=WAIT_RESULT;
               when WAIT_RESULT =>
                  if core_done='1' then
                     -- Upstream clients repeatedly prepare from these values
                     -- while waiting. Write each completed lane directly and
                     -- avoid retaining a second copy of the result registers.
                     for i in 0 to 5 loop
                        if lane=i then
                           quotients(i*45+44 downto i*45)<=core_quotient;
                           remainders(i*25+24 downto i*25)<=core_remainder;
                        end if;
                     end loop;
                     pending(lane)<='0'; state<=SELECT_LANE;
                  end if;
               -- All lane outputs are now stable. Let boundary preparation
               -- settle before releasing completion for the whole batch.
               when SETTLE1 => state<=SETTLE2;
               when SETTLE2 => state<=COMPLETE;
               when COMPLETE =>
                  dones<=batch; batch<=(others=>'0'); state<=IDLE;
            end case;
         end if;
      end if;
   end process;
end architecture;
