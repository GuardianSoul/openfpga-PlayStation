-- SPDX-License-Identifier: MIT
-- Copyright (c) 2026 PSX Pocket contributors
--
-- Fall-through FIFO with its storage in M10K block RAM, a drop-in for the
-- MiSTer SyncFifoFallThroughMLAB used as memorymux's CPU write FIFO.
--
-- Contract kept from the MLAB FIFO (all on rising clk):
--   * wrcnt advances on every Wr, rdcnt on every Rd (also when full/empty),
--     the word at wrcnt is written on every Wr, reset clears the pointers and
--     the count but not the storage.
--   * fifocnt/Empty/Full/NearFull/NearEmpty: the same registered equations.
--   * Dout, in the clock cycle after an edge, equals the stored word at rdcnt
--     including a word written on that same edge (the MLAB is written at the
--     edge and read asynchronously). memorymux uses Dout only while Empty is
--     low; Dout is exact whenever Empty is low.
--
-- M10K reads are synchronous, so Dout cannot be an asynchronous read of the
-- current rdcnt. Two read schemes, chosen by READ_MODE:
--
-- "BYPASS" (default): the read address register samples rd_next, the value
--   rdcnt takes at this edge (0 under reset, rdcnt + Rd otherwise), so after
--   the edge the M10K q already shows the word at the new rdcnt. The only
--   word q cannot show is one written on the same edge to that address
--   (mixed-port read-during-write, DONT_CARE in Cyclone V M10K). That case is
--   detected at the edge (Wr and wrcnt = rd_next) and Dout comes from a
--   DATAWIDTH-bit bypass register loaded with Din on every Wr. One clock,
--   full-cycle paths only.
--
-- "CLK2X": the read address register is clocked by clk2x (phase-aligned,
--   twice clk) and samples rdcnt directly. The clk2x edge in the middle of
--   each clk cycle reads the post-edge rdcnt after the write of the clk edge
--   has completed, so q shows exactly what the MLAB showed. The clk2x edge
--   coinciding with the clk edge may collide with the write (DONT_CARE) and
--   reads the old rdcnt, but q only changes after that edge, when every clk
--   consumer has already sampled. No bypass register; the consumers of Dout
--   get half a clk period (one clk2x period).
--
-- Timing at clk = 33.8688 MHz (29.53 ns), clk2x = 67.7376 MHz:
--   BYPASS: rdcnt/Rd -> rd_next -> M10K address register, Wr/Din -> M10K
--   write registers and bypass register, M10K q -> mux -> memorymux IDLE
--   logic: all clk-to-clk (29.5 ns), the same budget as the MLAB FIFO whose
--   asynchronous read is replaced by the M10K clock-to-output and one LUT.
--   CLK2X: rdcnt register -> M10K address register is clk -> clk2x (14.8 ns
--   setup, a few LUT-free hops); M10K q -> memorymux IDLE logic -> clk
--   registers is clk2x -> clk (14.8 ns) and must be met by the full fit.
--   The internal write-to-read gap (14.8 ns) is several times the M10K
--   write cycle at its specified fMAX.

library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
use ieee.math_real.all;

entity psx_wfifo_m10k is
   generic
   (
      SIZE              : integer;
      DATAWIDTH         : integer;
      NEARFULLDISTANCE  : integer;
      NEAREMPTYDISTANCE : integer := 0;
      READ_MODE         : string  := "BYPASS"
   );
   port
   (
      clk         : in  std_logic;
      clk2x       : in  std_logic := '0';
      reset       : in  std_logic;

      Din         : in  std_logic_vector(DATAWIDTH - 1 downto 0);
      Wr          : in  std_logic;
      Full        : out std_logic := '0';
      NearFull    : out std_logic := '0';

      Dout        : out std_logic_vector(DATAWIDTH - 1 downto 0);
      Rd          : in  std_logic;
      Empty       : out std_logic := '1';
      NearEmpty   : out std_logic := '0'
   );
end entity;

architecture rtl of psx_wfifo_m10k is

   constant SIZEBITS : integer := integer(ceil(log2(real(SIZE))));
   constant TOPCOUNT : unsigned(SIZEBITS - 1 downto 0) := (others => '1');

   signal wrcnt       : unsigned(SIZEBITS - 1 downto 0) := (others => '0');
   signal rdcnt       : unsigned(SIZEBITS - 1 downto 0) := (others => '0');
   signal fifocnt     : unsigned(SIZEBITS - 1 downto 0) := (others => '0');
   signal at_top      : std_logic;
   signal at_zero     : std_logic;

   signal ram_q       : std_logic_vector(DATAWIDTH - 1 downto 0);

begin

   assert READ_MODE = "BYPASS" or READ_MODE = "CLK2X"
      report "psx_wfifo_m10k: READ_MODE must be BYPASS or CLK2X" severity failure;

   at_top  <= '1' when fifocnt = TOPCOUNT else '0';
   at_zero <= '1' when fifocnt = 0        else '0';

   -- Pointer and flag bookkeeping (same equations as the MLAB FIFO).
   process (clk)
      variable count : unsigned(SIZEBITS - 1 downto 0);
   begin
      if rising_edge(clk) then
         if reset = '1' then
            wrcnt   <= (others => '0');
            rdcnt   <= (others => '0');
            fifocnt <= (others => '0');
            Full    <= '0';
            Empty   <= '1';
         else
            count := fifocnt;
            if Wr = '1' and at_top = '0' then
               if Rd = '0' or at_zero = '1' then
                  count := count + 1;
               end if;
            elsif Rd = '1' and at_zero = '0' then
               count := count - 1;
            end if;

            if count < NEARFULLDISTANCE then NearFull <= '0'; else NearFull <= '1'; end if;
            if count >= NEAREMPTYDISTANCE then NearEmpty <= '0'; else NearEmpty <= '1'; end if;
            if count = 0 then Empty <= '1'; else Empty <= '0'; end if;
            if count = TOPCOUNT or at_top = '1' then Full <= '1'; else Full <= '0'; end if;

            if Wr = '1' then wrcnt <= wrcnt + 1; end if;
            if Rd = '1' then rdcnt <= rdcnt + 1; end if;
            fifocnt <= count;
         end if;
      end if;
   end process;

   gbypass : if READ_MODE = "BYPASS" generate
      signal rd_next     : unsigned(SIZEBITS - 1 downto 0);
      signal bypass_sel  : std_logic := '0';
      signal bypass_data : std_logic_vector(DATAWIDTH - 1 downto 0) := (others => '0');
   begin
      rd_next <= (others => '0') when reset = '1' else
                 rdcnt + 1       when Rd = '1'    else
                 rdcnt;

      iram : entity work.psx_m10k_sdp
      generic map (WIDTH => DATAWIDTH, WIDTHAD => SIZEBITS, DUAL_CLOCK => false)
      port map
      (
         wrclock   => clk,
         rdclock   => clk,
         wren      => Wr,
         wraddress => std_logic_vector(wrcnt),
         data      => Din,
         rdaddress => std_logic_vector(rd_next),
         q         => ram_q
      );

      process (clk)
      begin
         if rising_edge(clk) then
            if Wr = '1' and wrcnt = rd_next then
               bypass_sel <= '1';
            else
               bypass_sel <= '0';
            end if;
            if Wr = '1' then
               bypass_data <= Din;
            end if;
         end if;
      end process;

      Dout <= bypass_data when bypass_sel = '1' else ram_q;
   end generate;

   gclk2x : if READ_MODE = "CLK2X" generate
   begin
      iram : entity work.psx_m10k_sdp
      generic map (WIDTH => DATAWIDTH, WIDTHAD => SIZEBITS, DUAL_CLOCK => true)
      port map
      (
         wrclock   => clk,
         rdclock   => clk2x,
         wren      => Wr,
         wraddress => std_logic_vector(wrcnt),
         data      => Din,
         rdaddress => std_logic_vector(rdcnt),
         q         => ram_q
      );

      Dout <= ram_q;
   end generate;

end architecture;
