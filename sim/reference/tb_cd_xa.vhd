-- Feeds raw XA sectors (588 words each, from the sync) into the pinned
-- MiSTer cd_xa decoder the way cd_top does: 588 writes with XA_start on the
-- last one. spu_tick pops one stereo 44.1 kHz frame every TICK cycles; each
-- popped frame is written to OUT_FILE as "left right" (signed decimal).
-- cd_xa only accepts XA_start while its ADPCM FIFO holds fewer than 16
-- samples (NEARFULLDISTANCE is a count), so each next sector is fed as soon
-- as the FIFO drops below that; the 16-frame output FIFO must not underrun.
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
use std.textio.all;

entity tb_cd_xa is
   generic (
      SECTOR_FILE : string  := "sectors.hex";
      OUT_FILE    : string  := "out.txt";
      SECTORS     : integer := 1;
      FRAMES      : integer := 2352;
      TICK        : integer := 256
   );
end entity;

architecture sim of tb_cd_xa is
   signal clk1x         : std_logic := '0';
   signal reset         : std_logic := '1';
   signal spu_tick      : std_logic := '0';
   signal XA_addr       : integer range 0 to 587 := 0;
   signal XA_data       : std_logic_vector(31 downto 0) := (others => '0');
   signal XA_write      : std_logic := '0';
   signal XA_start      : std_logic := '0';
   signal XA_reset      : std_logic := '0';
   signal XA_eof        : std_logic;
   signal cdaudio_left  : signed(15 downto 0);
   signal cdaudio_right : signed(15 downto 0);
   signal popped        : integer := 0;
   signal ticking       : boolean := false;
   signal done          : boolean := false;
begin
   clk1x <= not clk1x after 5 ns when not done;

   dut : entity work.cd_xa
      port map (
         clk1x => clk1x, reset => reset, spu_tick => spu_tick,
         CDDA_write => '0', CDDA_data => (others => '0'),
         XA_addr => XA_addr, XA_data => XA_data, XA_write => XA_write,
         XA_start => XA_start, XA_reset => XA_reset, XA_eof => XA_eof,
         cdaudio_left => cdaudio_left, cdaudio_right => cdaudio_right);

   feed : process
      file f        : text;
      variable l    : line;
      variable word : std_logic_vector(31 downto 0);
      alias nearfull is <<signal .tb_cd_xa.dut.FifoADPCM_NearFull : std_logic>>;
   begin
      file_open(f, SECTOR_FILE, read_mode);
      for i in 1 to 4 loop wait until rising_edge(clk1x); end loop;
      reset <= '0'; XA_reset <= '1';
      wait until rising_edge(clk1x);
      XA_reset <= '0';
      for i in 1 to 40 loop wait until rising_edge(clk1x); end loop;
      for s in 0 to SECTORS - 1 loop
         while s > 0 and nearfull = '1' loop
            wait until rising_edge(clk1x);
         end loop;
         for w in 0 to 587 loop
            readline(f, l); hread(l, word);
            XA_write <= '1'; XA_addr <= w; XA_data <= word;
            if w = 587 then XA_start <= '1'; end if;
            wait until rising_edge(clk1x);
         end loop;
         XA_write <= '0'; XA_start <= '0';
         for i in 1 to 2000 loop
            exit when nearfull = '1';
            wait until rising_edge(clk1x);
         end loop;
         assert nearfull = '1' report "sector " & integer'image(s) & " was not decoded" severity failure;
         if s = 0 then
            -- Let the output FIFO fill before draining.
            for i in 1 to 20000 loop wait until rising_edge(clk1x); end loop;
            ticking <= true;
         end if;
      end loop;
      wait;
   end process;

   drain : process
      file o          : text;
      variable l      : line;
      variable count  : integer := 0;
      alias empty is <<signal .tb_cd_xa.dut.FifoOut_Empty : std_logic>>;
   begin
      file_open(o, OUT_FILE, write_mode);
      wait until ticking;
      while count < SECTORS * FRAMES loop
         for i in 1 to TICK - 1 loop wait until rising_edge(clk1x); end loop;
         spu_tick <= '1';
         wait until rising_edge(clk1x);
         spu_tick <= '0';
         assert empty = '0' report "output FIFO underrun at frame " & integer'image(count) severity failure;
         wait until rising_edge(clk1x);
         write(l, to_integer(cdaudio_left)); write(l, string'(" "));
         write(l, to_integer(cdaudio_right)); writeline(o, l);
         count := count + 1; popped <= count;
      end loop;
      file_close(o);
      report "PASS cd_xa frames=" & integer'image(count) severity note;
      done <= true;
      wait;
   end process;
end architecture;
