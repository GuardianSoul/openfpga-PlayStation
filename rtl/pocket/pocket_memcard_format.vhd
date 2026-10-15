-- SPDX-License-Identifier: GPL-2.0-only
-- pocket_memcard with a formatted blank card for a new save file.
--
-- Adapted from pocket_sotn (github.com/akoaysigod/pocket_sotn, GPL-2.0, pinned
-- b9fedc5) pocket/src/fpga/core/pocket_memcard.vhd; the original's notes follow
-- unchanged. Added here (prepare_pocket_early.py --memcard-format):
--  * On a game's first start APF creates the card file erased (all 0xFF, seen
--    on hardware 2026-10-15), so the card the game saw was unformatted. Games
--    without a format option of their own (Wipeout) then cannot save at all.
--  * When load_done rises (the BIOS and every slot are loaded; the CPU is still
--    held) and the card's first word does not start with the "MC" header magic
--    (nothing loaded, an erased or zeroed file, or a 0.5.0 card that never got
--    formatted), the card's header area (frames 0-63, 2,048 words) is written
--    as a factory-formatted card
--    (psx-spx "Memory Card Data Format", the same image as
--    rtl/pocket/psx_memcard_blank.sv): "MC" header and write-test frames, 15
--    free directory entries, an empty broken-sector list. The data blocks are
--    left as they are; a free block's contents are never read.
--  * busy stays high until that check (and any format) is done, so the CPU,
--    which waits for !busy, never sees the card half written. The BIOS and
--    every game treat a card without "MC" as unformatted, so nothing readable
--    is lost; data blocks are never written. A card with the header is never
--    touched, and the check runs once per power-on.
--
-- Analogue Pocket: memory card 1 file (APF nonvolatile data slot at bridge address 0x20000000, 128 KB).
-- The core keeps the live card in its DDR3 region 1 (memory card 1), which is PSRAM region 1 on the Pocket
-- (vram_psram: PSRAM word address {region, offset, half} = 0x40000 + byte offset / 4). So instead of MiSTer's
-- HPS load / save transfers, APF reads and writes that PSRAM region directly through vram_psram's aux port:
--  * bridge writes (slot load at start, core in reset) -> FIFO -> PSRAM writes
--  * bridge reads (APF saving the slot on exit): like agg23's data_unloader, the word at the address of a
--    bridge read is fetched when bridge_rd pulses and presented on bridge_rd_data for APF's next read.
--    APF reads the slot sequentially, so the following word is prefetched: a read then takes only the clock
--    domain crossings (~0.12 us) instead of a PSRAM access behind the core's traffic (up to ~0.5 us). The
--    prefetched word is dropped when the core writes to the card (card_write) or the card is loaded.
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

library altera_mf;
use altera_mf.altera_mf_components.all;

entity pocket_memcard_format is
   port
   (
      clk_74a        : in  std_logic;
      bridge_addr    : in  std_logic_vector(31 downto 0);
      bridge_wr      : in  std_logic;
      bridge_wr_data : in  std_logic_vector(31 downto 0);
      bridge_rd      : in  std_logic;
      bridge_rd_data : out std_logic_vector(31 downto 0) := (others => '0');
      overflow       : out std_logic := '0';   -- sticky: words lost on load (debug)
      -- PSRAM side (vram_psram aux port, clk2x)
      clk2x          : in  std_logic;
      reset_2x       : in  std_logic;
      busy           : out std_logic := '1';   -- load FIFO not empty or a transfer running
      aux_req        : out std_logic := '0';
      aux_we         : out std_logic := '0';
      aux_addr       : out std_logic_vector(20 downto 0) := (others => '0');
      aux_din        : out std_logic_vector(31 downto 0) := (others => '0');
      aux_dout       : in  std_logic_vector(31 downto 0);
      aux_done       : in  std_logic;
      card_write     : in  std_logic;            -- vram_psram: core write to the card region taken
      load_done      : in  std_logic             -- any clock: all slots loaded, CPU not yet started
   );
end entity;

architecture arch of pocket_memcard_format is

   constant SLOT_BASE : std_logic_vector(3 downto 0) := x"2";
   constant PSRAM_BASE : unsigned(20 downto 0) := to_unsigned(16#40000#, 21);   -- region 1

   -- load FIFO: {word index(14:0), data(31:0)}
   signal fifo_wrreq  : std_logic := '0';
   signal fifo_data   : std_logic_vector(46 downto 0) := (others => '0');
   signal fifo_wrfull : std_logic;
   signal fifo_rdreq  : std_logic := '0';
   signal fifo_q      : std_logic_vector(46 downto 0);
   signal fifo_empty  : std_logic;

   -- bridge read: address (74a) -> fetch (2x) -> data (74a)
   signal rd_1        : std_logic := '0';
   signal rd_word     : std_logic_vector(14 downto 0) := (others => '0');
   signal rd_tog      : std_logic := '0';
   signal rd_tog_s    : std_logic_vector(2 downto 0) := (others => '0');
   signal rd_ack      : std_logic := '0';
   signal rd_ack_s    : std_logic_vector(2 downto 0) := (others => '0');
   signal rd_data     : std_logic_vector(31 downto 0) := (others => '0');

   type tState is (IDLE, FIFO_WAIT, FIFO_WRITE, MEM_WAIT, READ_WAIT, PF_WAIT, FMT_WRITE, FMT_WAIT);
   signal state       : tState := IDLE;

   -- prefetched word
   signal pf_valid    : std_logic := '0';
   signal pf_dirty    : std_logic := '0';
   signal pf_word     : unsigned(14 downto 0) := (others => '0');
   signal pf_data     : std_logic_vector(31 downto 0) := (others => '0');

   -- blank-card format
   signal done_s      : std_logic_vector(2 downto 0) := (others => '0');
   signal card_ok     : std_logic := '0';   -- loaded frame 0 starts with "MC"
   signal checked     : std_logic := '0';   -- format decision taken
   signal fmt_word    : unsigned(10 downto 0) := (others => '0');

   -- Word w (0-2047) of a formatted card's frames 0-63, little-endian bytes.
   function blank_word(w : unsigned(10 downto 0)) return std_logic_vector is
      variable frame : integer range 0 to 63;
      variable slot  : integer range 0 to 31;
   begin
      frame := to_integer(w(10 downto 5));
      slot  := to_integer(w(4 downto 0));
      if (frame = 0 or frame = 63) then
         if (slot = 0)  then return x"0000434D"; end if;   -- "MC"
         if (slot = 31) then return x"0E000000"; end if;   -- checksum 4D xor 43
      elsif (frame < 16) then                              -- free directory entry
         if (slot = 0)  then return x"000000A0"; end if;
         if (slot = 2)  then return x"0000FFFF"; end if;   -- no next block
         if (slot = 31) then return x"A0000000"; end if;   -- checksum
      elsif (frame < 36) then                              -- no broken sector
         if (slot = 0)  then return x"FFFFFFFF"; end if;
         if (slot = 2)  then return x"0000FFFF"; end if;   -- checksum of FF x6 is 0
      end if;
      return x"00000000";
   end function;

begin

   ---------------------------------------------------------------- clk_74a
   process (clk_74a)
   begin
      if rising_edge(clk_74a) then
         fifo_wrreq <= '0';
         if (bridge_wr = '1' and bridge_addr(31 downto 28) = SLOT_BASE and unsigned(bridge_addr(27 downto 17)) = 0) then
            if (fifo_wrfull = '1') then
               overflow <= '1';
            else
               fifo_wrreq <= '1';
               fifo_data  <= bridge_addr(16 downto 2) & bridge_wr_data;
            end if;
         end if;

         rd_1 <= bridge_rd;
         if (bridge_rd = '1' and rd_1 = '0' and bridge_addr(31 downto 28) = SLOT_BASE) then
            rd_word <= bridge_addr(16 downto 2);
            rd_tog  <= not rd_tog;
         end if;

         rd_ack_s <= rd_ack_s(1 downto 0) & rd_ack;
         if (rd_ack_s(2) /= rd_ack_s(1)) then
            bridge_rd_data <= rd_data;
         end if;
      end if;
   end process;

   iloadfifo : dcfifo
   generic map
   (
      intended_device_family => "Cyclone V", lpm_numwords => 256, lpm_showahead => "OFF", lpm_type => "dcfifo",
      lpm_width => 47, lpm_widthu => 8, overflow_checking => "ON", rdsync_delaypipe => 4,
      underflow_checking => "ON", use_eab => "ON", wrsync_delaypipe => 4
   )
   port map
   (
      data => fifo_data, rdclk => clk2x, rdreq => fifo_rdreq, wrclk => clk_74a, wrreq => fifo_wrreq,
      q => fifo_q, rdempty => fifo_empty, wrfull => fifo_wrfull
   );

   ---------------------------------------------------------------- clk2x
   process (clk2x)
   begin
      if rising_edge(clk2x) then
         fifo_rdreq <= '0';
         rd_tog_s   <= rd_tog_s(1 downto 0) & rd_tog;
         done_s     <= done_s(1 downto 0) & load_done;

         if (reset_2x = '1') then
            state    <= IDLE;
            aux_req  <= '0';
            busy     <= '1';
            pf_valid <= '0';
            card_ok  <= '0';
            checked  <= '0';
         else
            busy <= '0';
            if (fifo_empty = '0' or state = FIFO_WAIT or state = FIFO_WRITE or state = MEM_WAIT) then busy <= '1'; end if;
            if (checked = '0' or state = FMT_WRITE or state = FMT_WAIT) then busy <= '1'; end if;

            case (state) is
               when IDLE =>
                  if (fifo_empty = '0') then
                     fifo_rdreq <= '1';
                     pf_valid   <= '0';
                     state      <= FIFO_WAIT;
                  elsif (checked = '0' and done_s(2) = '1') then
                     checked  <= '1';
                     pf_valid <= '0';
                     fmt_word <= (others => '0');
                     if (card_ok = '0') then state <= FMT_WRITE; end if;
                  elsif (rd_tog_s(2) /= rd_ack) then
                     if (pf_valid = '1' and pf_word = unsigned(rd_word)) then
                        -- prefetch hit: answer now, prefetch the next word
                        rd_data  <= pf_data;
                        rd_ack   <= rd_tog_s(2);
                        pf_valid <= '0';
                        pf_word  <= pf_word + 1;
                        aux_req  <= '1';
                        aux_we   <= '0';
                        aux_addr <= std_logic_vector(PSRAM_BASE + (pf_word + 1));
                        state    <= PF_WAIT;
                     else                       -- not sequential: read the word itself
                        pf_valid <= '0';
                        aux_req  <= '1';
                        aux_we   <= '0';
                        aux_addr <= std_logic_vector(PSRAM_BASE + unsigned(rd_word));
                        state    <= READ_WAIT;
                     end if;
                  elsif (pf_valid = '0') then   -- (re)fetch the prefetch word
                     aux_req  <= '1';
                     aux_we   <= '0';
                     aux_addr <= std_logic_vector(PSRAM_BASE + pf_word);
                     state    <= PF_WAIT;
                  end if;

               when FIFO_WAIT =>                -- q valid one clock after rdreq
                  state <= FIFO_WRITE;

               when FIFO_WRITE =>
                  aux_req  <= '1';
                  aux_we   <= '1';
                  aux_addr <= std_logic_vector(PSRAM_BASE + unsigned(fifo_q(46 downto 32)));
                  aux_din  <= fifo_q(31 downto 0);
                  if (unsigned(fifo_q(46 downto 32)) = 0) then   -- word 0: header magic "MC"
                     if (fifo_q(15 downto 0) = x"434D") then card_ok <= '1'; else card_ok <= '0'; end if;
                  end if;
                  state    <= MEM_WAIT;

               when FMT_WRITE =>
                  aux_req  <= '1';
                  aux_we   <= '1';
                  aux_addr <= std_logic_vector(PSRAM_BASE + resize(fmt_word, 21));
                  aux_din  <= blank_word(fmt_word);
                  state    <= FMT_WAIT;

               when FMT_WAIT =>
                  if (aux_done = '1') then
                     aux_req  <= '0';
                     fmt_word <= fmt_word + 1;
                     if (fmt_word = 2047) then state <= IDLE; else state <= FMT_WRITE; end if;
                  end if;

               when MEM_WAIT =>
                  if (aux_done = '1') then
                     aux_req <= '0';
                     state   <= IDLE;
                  end if;

               when READ_WAIT =>                -- demand read (no prefetch hit), then prefetch the next word
                  if (aux_done = '1') then
                     rd_data  <= aux_dout;
                     rd_ack   <= rd_tog_s(2);
                     pf_word  <= unsigned(rd_word) + 1;
                     pf_valid <= '0';
                     aux_req  <= '0';
                     state    <= IDLE;          -- IDLE starts the prefetch (pf_valid = '0')
                  end if;

               when PF_WAIT =>
                  if (aux_done = '1') then
                     aux_req  <= '0';
                     pf_data  <= aux_dout;
                     pf_valid <= not pf_dirty;
                     state    <= IDLE;
                  end if;
            end case;

            -- the core wrote to the card: the prefetched word may be old
            if (card_write = '1') then
               pf_valid <= '0';
            end if;
            if (state /= PF_WAIT) then
               pf_dirty <= '0';
            elsif (card_write = '1') then
               pf_dirty <= '1';
            end if;
         end if;
      end if;
   end process;

end architecture;
