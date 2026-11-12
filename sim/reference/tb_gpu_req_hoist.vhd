-- SPDX-License-Identifier: MIT
-- Whole-GPU lockstep for gpu-req-hoist (scripts/run_gpu_req_hoist.py).
-- lref.gpu is the reviewed GPU of the frozen project, lcand.gpu the same GPU
-- after scripts/prepare_gpu_req_hoist.py. Both get identical inputs on every
-- clock. The environment (CPU bus, DMA, DDR/VRAM model with random busy,
-- random vram_pause, random ce and savestate_busy) follows the reference
-- only. Every output port of the entity, and the seven internal request
-- lines (dbg_req, a simulation-only port), are compared at every falling
-- clk2x and clkvid edge.
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
use ieee.math_real.all;
use std.textio.all;
library lref;
library lcand;

entity tb_gpu_req_hoist is
   generic (
      STIM      : string  := "stim.txt";
      SEED      : integer := 1;
      SYNCVIDEO : integer := 1
   );
end entity;

architecture sim of tb_gpu_req_hoist is
   signal clk1x                 : std_logic := '1';
   signal clk2x                 : std_logic := '1';
   signal clk2xIndex            : std_logic := '0';
   signal clkvid                : std_logic := '1';
   signal ce                    : std_logic := '1';
   signal reset                 : std_logic := '1';
   signal savestate_busy        : std_logic := '0';
   signal system_paused         : std_logic := '0';
   signal ditherOff             : std_logic := '0';
   signal interlaced480pHack    : std_logic := '0';
   signal REPRODUCIBLEGPUTIMING : std_logic := '0';
   signal videoout_on           : std_logic := '1';
   signal isPal                 : std_logic := '0';
   signal pal60                 : std_logic := '0';
   signal fpscountOn            : std_logic := '0';
   signal noTexture             : std_logic := '0';
   signal textureFilter         : std_logic_vector(1 downto 0) := (others => '0');
   signal textureFilterStrength : std_logic_vector(1 downto 0) := (others => '0');
   signal textureFilter2DOff    : std_logic := '0';
   signal dither24              : std_logic := '0';
   signal render24              : std_logic := '0';
   signal drawSlow              : std_logic := '0';
   signal debugmodeOn           : std_logic := '0';
   signal syncVideoOut          : std_logic := '0';
   signal syncInterlace         : std_logic := '0';
   signal rotate180             : std_logic := '0';
   signal fixedVBlank           : std_logic := '0';
   signal vCrop                 : std_logic_vector(1 downto 0) := (others => '0');
   signal hCrop                 : std_logic := '0';
   signal oldGPU                : std_logic := '0';
   signal Gun1CrosshairOn       : std_logic := '0';
   signal Gun1X                 : unsigned(7 downto 0) := (others => '0');
   signal Gun1Y_scanlines       : unsigned(8 downto 0) := (others => '0');
   signal Gun1offscreen         : std_logic := '0';
   signal Gun2CrosshairOn       : std_logic := '0';
   signal Gun2X                 : unsigned(7 downto 0) := (others => '0');
   signal Gun2Y_scanlines       : unsigned(8 downto 0) := (others => '0');
   signal Gun2offscreen         : std_logic := '0';
   signal cdSlow                : std_logic := '0';
   signal errorOn               : std_logic := '0';
   signal errorEna              : std_logic := '0';
   signal errorCode             : unsigned(3 downto 0) := (others => '0');
   signal LBAOn                 : std_logic := '0';
   signal LBAdisplay            : unsigned(19 downto 0) := (others => '0');
   signal bus_addr              : unsigned(3 downto 0) := (others => '0');
   signal bus_dataWrite         : std_logic_vector(31 downto 0) := (others => '0');
   signal bus_read              : std_logic := '0';
   signal bus_write             : std_logic := '0';
   signal dmaOn                 : std_logic := '0';
   signal DMA_GPU_waiting       : std_logic := '0';
   signal DMA_GPU_writeEna      : std_logic := '0';
   signal DMA_GPU_readEna       : std_logic := '0';
   signal DMA_GPU_write         : std_logic_vector(31 downto 0) := (others => '0');
   signal vram_pause            : std_logic := '0';
   signal vram_BUSY             : std_logic := '0';
   signal vram_DOUT             : std_logic_vector(63 downto 0) := (others => '0');
   signal vram_DOUT_READY       : std_logic := '0';
   signal loading_savestate     : std_logic := '0';
   signal SS_reset              : std_logic := '1';
   signal SS_DataWrite          : std_logic_vector(31 downto 0) := (others => '0');
   signal SS_Adr                : unsigned(2 downto 0) := (others => '0');
   signal SS_wren_GPU           : std_logic := '0';
   signal SS_wren_Timing        : std_logic := '0';
   signal SS_rden_GPU           : std_logic := '0';
   signal SS_rden_Timing        : std_logic := '0';
   signal r_allowunpause        : std_logic;
   signal c_allowunpause        : std_logic;
   signal r_Gun1IRQ10           : std_logic;
   signal c_Gun1IRQ10           : std_logic;
   signal r_Gun2IRQ10           : std_logic;
   signal c_Gun2IRQ10           : std_logic;
   signal r_errorLINE           : std_logic;
   signal c_errorLINE           : std_logic;
   signal r_errorRECT           : std_logic;
   signal c_errorRECT           : std_logic;
   signal r_errorPOLY           : std_logic;
   signal c_errorPOLY           : std_logic;
   signal r_errorGPU            : std_logic;
   signal c_errorGPU            : std_logic;
   signal r_errorMASK           : std_logic;
   signal c_errorMASK           : std_logic;
   signal r_errorFIFO           : std_logic;
   signal c_errorFIFO           : std_logic;
   signal r_bus_dataRead        : std_logic_vector(31 downto 0);
   signal c_bus_dataRead        : std_logic_vector(31 downto 0);
   signal r_save_gpu_ranges     : std_logic_vector(62 downto 0);
   signal c_save_gpu_ranges     : std_logic_vector(62 downto 0);
   signal r_bus_stall           : std_logic;
   signal c_bus_stall           : std_logic;
   signal r_gpu_dmaRequest      : std_logic;
   signal c_gpu_dmaRequest      : std_logic;
   signal r_DMA_GPU_read        : std_logic_vector(31 downto 0);
   signal c_DMA_GPU_read        : std_logic_vector(31 downto 0);
   signal r_irq_VBLANK          : std_logic;
   signal c_irq_VBLANK          : std_logic;
   signal r_irq_GPU             : std_logic;
   signal c_irq_GPU             : std_logic;
   signal r_vram_paused         : std_logic;
   signal c_vram_paused         : std_logic;
   signal r_vram_BURSTCNT       : std_logic_vector(7 downto 0);
   signal c_vram_BURSTCNT       : std_logic_vector(7 downto 0);
   signal r_vram_ADDR           : std_logic_vector(27 downto 0);
   signal c_vram_ADDR           : std_logic_vector(27 downto 0);
   signal r_vram_DIN            : std_logic_vector(63 downto 0);
   signal c_vram_DIN            : std_logic_vector(63 downto 0);
   signal r_vram_BE             : std_logic_vector(7 downto 0);
   signal c_vram_BE             : std_logic_vector(7 downto 0);
   signal r_vram_WE             : std_logic;
   signal c_vram_WE             : std_logic;
   signal r_vram_RD             : std_logic;
   signal c_vram_RD             : std_logic;
   signal r_hblank_tmr          : std_logic;
   signal c_hblank_tmr          : std_logic;
   signal r_vblank_tmr          : std_logic;
   signal c_vblank_tmr          : std_logic;
   signal r_dotclock            : std_logic;
   signal c_dotclock            : std_logic;
   signal r_video_hsync         : std_logic;
   signal c_video_hsync         : std_logic;
   signal r_video_vsync         : std_logic;
   signal c_video_vsync         : std_logic;
   signal r_video_hblank        : std_logic;
   signal c_video_hblank        : std_logic;
   signal r_video_vblank        : std_logic;
   signal c_video_vblank        : std_logic;
   signal r_video_dispblank     : std_logic;
   signal c_video_dispblank     : std_logic;
   signal r_video_DisplayWidth  : unsigned(10 downto 0);
   signal c_video_DisplayWidth  : unsigned(10 downto 0);
   signal r_video_DisplayHeight : unsigned(9 downto 0);
   signal c_video_DisplayHeight : unsigned(9 downto 0);
   signal r_video_DisplayOffsetX: unsigned(9 downto 0);
   signal c_video_DisplayOffsetX: unsigned(9 downto 0);
   signal r_video_DisplayOffsetY: unsigned(8 downto 0);
   signal c_video_DisplayOffsetY: unsigned(8 downto 0);
   signal r_video_ce            : std_logic;
   signal c_video_ce            : std_logic;
   signal r_video_interlace     : std_logic;
   signal c_video_interlace     : std_logic;
   signal r_video_r             : std_logic_vector(7 downto 0);
   signal c_video_r             : std_logic_vector(7 downto 0);
   signal r_video_g             : std_logic_vector(7 downto 0);
   signal c_video_g             : std_logic_vector(7 downto 0);
   signal r_video_b             : std_logic_vector(7 downto 0);
   signal c_video_b             : std_logic_vector(7 downto 0);
   signal r_video_isPal         : std_logic;
   signal c_video_isPal         : std_logic;
   signal r_video_fbmode        : std_logic;
   signal c_video_fbmode        : std_logic;
   signal r_video_fb24          : std_logic;
   signal c_video_fb24          : std_logic;
   signal r_video_hResMode      : std_logic_vector(2 downto 0);
   signal c_video_hResMode      : std_logic_vector(2 downto 0);
   signal r_video_frameindex    : std_logic_vector(3 downto 0);
   signal c_video_frameindex    : std_logic_vector(3 downto 0);
   signal r_SS_DataRead_GPU     : std_logic_vector(31 downto 0);
   signal c_SS_DataRead_GPU     : std_logic_vector(31 downto 0);
   signal r_SS_DataRead_Timing  : std_logic_vector(31 downto 0);
   signal c_SS_DataRead_Timing  : std_logic_vector(31 downto 0);
   signal r_SS_Idle             : std_logic;
   signal c_SS_Idle             : std_logic;
   signal r_dbg_req             : std_logic_vector(6 downto 0);
   signal c_dbg_req             : std_logic_vector(6 downto 0);
   signal toggle, sampled : std_logic := '0';
   -- environment settings (stimulus op 6)
   signal env_busy_p     : integer := 0;   -- of 256 per clk2x: a busy run starts
   signal env_busy_run   : integer := 0;   -- longest busy run - 1
   signal env_pause_p    : integer := 0;   -- of 65536 per clk2x: vram_pause rises
   signal env_pause_hold : integer := 0;   -- longest pause - 1
   signal env_lat_min    : integer := 1;   -- clocks from an accepted read to the first word (+1)
   signal env_lat_max    : integer := 1;
   signal env_gap_p      : integer := 0;   -- of 256 per word: one clock without data
   signal env_ce_act     : integer := 0;   -- of 256 per clk1x: ce low while the GPU is busy
   signal env_ce_idle    : integer := 0;   -- of 256 per clk1x: ce low while SS_Idle
   signal env_ss_p       : integer := 0;   -- of 65536 per clk1x: a savestate_busy pulse starts
   signal cycle     : integer := 0;
   signal program   : integer := 0;
   signal compares  : integer := 0;
   signal timeouts  : integer := 0;
   signal resets    : integer := 0;
   signal cpu_words : integer := 0;
   signal dma_words : integer := 0;
   signal done      : boolean := false;
begin
   clk2x  <= not clk2x after 7381275 fs;
   clk1x  <= not clk1x after 14762550 fs;
   clkvid <= not clkvid after 9312170 fs;
   syncVideoOut <= '1' when SYNCVIDEO /= 0 else '0';

   process (clk1x)
   begin
      if rising_edge(clk1x) then toggle <= not toggle; end if;
   end process;
   process (clk2x)
   begin
      if rising_edge(clk2x) then
         sampled <= toggle;
         if (sampled = toggle) then clk2xIndex <= '1'; else clk2xIndex <= '0'; end if;
      end if;
   end process;

   ref : entity lref.gpu
      port map (
         clk1x => clk1x,
         clk2x => clk2x,
         clk2xIndex => clk2xIndex,
         clkvid => clkvid,
         ce => ce,
         reset => reset,
         savestate_busy => savestate_busy,
         system_paused => system_paused,
         ditherOff => ditherOff,
         interlaced480pHack => interlaced480pHack,
         REPRODUCIBLEGPUTIMING => REPRODUCIBLEGPUTIMING,
         videoout_on => videoout_on,
         isPal => isPal,
         pal60 => pal60,
         fpscountOn => fpscountOn,
         noTexture => noTexture,
         textureFilter => textureFilter,
         textureFilterStrength => textureFilterStrength,
         textureFilter2DOff => textureFilter2DOff,
         dither24 => dither24,
         render24 => render24,
         drawSlow => drawSlow,
         debugmodeOn => debugmodeOn,
         syncVideoOut => syncVideoOut,
         syncInterlace => syncInterlace,
         rotate180 => rotate180,
         fixedVBlank => fixedVBlank,
         vCrop => vCrop,
         hCrop => hCrop,
         oldGPU => oldGPU,
         Gun1CrosshairOn => Gun1CrosshairOn,
         Gun1X => Gun1X,
         Gun1Y_scanlines => Gun1Y_scanlines,
         Gun1offscreen => Gun1offscreen,
         Gun2CrosshairOn => Gun2CrosshairOn,
         Gun2X => Gun2X,
         Gun2Y_scanlines => Gun2Y_scanlines,
         Gun2offscreen => Gun2offscreen,
         cdSlow => cdSlow,
         errorOn => errorOn,
         errorEna => errorEna,
         errorCode => errorCode,
         LBAOn => LBAOn,
         LBAdisplay => LBAdisplay,
         bus_addr => bus_addr,
         bus_dataWrite => bus_dataWrite,
         bus_read => bus_read,
         bus_write => bus_write,
         dmaOn => dmaOn,
         DMA_GPU_waiting => DMA_GPU_waiting,
         DMA_GPU_writeEna => DMA_GPU_writeEna,
         DMA_GPU_readEna => DMA_GPU_readEna,
         DMA_GPU_write => DMA_GPU_write,
         vram_pause => vram_pause,
         vram_BUSY => vram_BUSY,
         vram_DOUT => vram_DOUT,
         vram_DOUT_READY => vram_DOUT_READY,
         loading_savestate => loading_savestate,
         SS_reset => SS_reset,
         SS_DataWrite => SS_DataWrite,
         SS_Adr => SS_Adr,
         SS_wren_GPU => SS_wren_GPU,
         SS_wren_Timing => SS_wren_Timing,
         SS_rden_GPU => SS_rden_GPU,
         SS_rden_Timing => SS_rden_Timing,
         allowunpause => r_allowunpause,
         Gun1IRQ10 => r_Gun1IRQ10,
         Gun2IRQ10 => r_Gun2IRQ10,
         errorLINE => r_errorLINE,
         errorRECT => r_errorRECT,
         errorPOLY => r_errorPOLY,
         errorGPU => r_errorGPU,
         errorMASK => r_errorMASK,
         errorFIFO => r_errorFIFO,
         bus_dataRead => r_bus_dataRead,
         save_gpu_ranges => r_save_gpu_ranges,
         bus_stall => r_bus_stall,
         gpu_dmaRequest => r_gpu_dmaRequest,
         DMA_GPU_read => r_DMA_GPU_read,
         irq_VBLANK => r_irq_VBLANK,
         irq_GPU => r_irq_GPU,
         vram_paused => r_vram_paused,
         vram_BURSTCNT => r_vram_BURSTCNT,
         vram_ADDR => r_vram_ADDR,
         vram_DIN => r_vram_DIN,
         vram_BE => r_vram_BE,
         vram_WE => r_vram_WE,
         vram_RD => r_vram_RD,
         hblank_tmr => r_hblank_tmr,
         vblank_tmr => r_vblank_tmr,
         dotclock => r_dotclock,
         video_hsync => r_video_hsync,
         video_vsync => r_video_vsync,
         video_hblank => r_video_hblank,
         video_vblank => r_video_vblank,
         video_dispblank => r_video_dispblank,
         video_DisplayWidth => r_video_DisplayWidth,
         video_DisplayHeight => r_video_DisplayHeight,
         video_DisplayOffsetX => r_video_DisplayOffsetX,
         video_DisplayOffsetY => r_video_DisplayOffsetY,
         video_ce => r_video_ce,
         video_interlace => r_video_interlace,
         video_r => r_video_r,
         video_g => r_video_g,
         video_b => r_video_b,
         video_isPal => r_video_isPal,
         video_fbmode => r_video_fbmode,
         video_fb24 => r_video_fb24,
         video_hResMode => r_video_hResMode,
         video_frameindex => r_video_frameindex,
         SS_DataRead_GPU => r_SS_DataRead_GPU,
         SS_DataRead_Timing => r_SS_DataRead_Timing,
         SS_Idle => r_SS_Idle,
         dbg_req => r_dbg_req
      );
   cand : entity lcand.gpu
      port map (
         clk1x => clk1x,
         clk2x => clk2x,
         clk2xIndex => clk2xIndex,
         clkvid => clkvid,
         ce => ce,
         reset => reset,
         savestate_busy => savestate_busy,
         system_paused => system_paused,
         ditherOff => ditherOff,
         interlaced480pHack => interlaced480pHack,
         REPRODUCIBLEGPUTIMING => REPRODUCIBLEGPUTIMING,
         videoout_on => videoout_on,
         isPal => isPal,
         pal60 => pal60,
         fpscountOn => fpscountOn,
         noTexture => noTexture,
         textureFilter => textureFilter,
         textureFilterStrength => textureFilterStrength,
         textureFilter2DOff => textureFilter2DOff,
         dither24 => dither24,
         render24 => render24,
         drawSlow => drawSlow,
         debugmodeOn => debugmodeOn,
         syncVideoOut => syncVideoOut,
         syncInterlace => syncInterlace,
         rotate180 => rotate180,
         fixedVBlank => fixedVBlank,
         vCrop => vCrop,
         hCrop => hCrop,
         oldGPU => oldGPU,
         Gun1CrosshairOn => Gun1CrosshairOn,
         Gun1X => Gun1X,
         Gun1Y_scanlines => Gun1Y_scanlines,
         Gun1offscreen => Gun1offscreen,
         Gun2CrosshairOn => Gun2CrosshairOn,
         Gun2X => Gun2X,
         Gun2Y_scanlines => Gun2Y_scanlines,
         Gun2offscreen => Gun2offscreen,
         cdSlow => cdSlow,
         errorOn => errorOn,
         errorEna => errorEna,
         errorCode => errorCode,
         LBAOn => LBAOn,
         LBAdisplay => LBAdisplay,
         bus_addr => bus_addr,
         bus_dataWrite => bus_dataWrite,
         bus_read => bus_read,
         bus_write => bus_write,
         dmaOn => dmaOn,
         DMA_GPU_waiting => DMA_GPU_waiting,
         DMA_GPU_writeEna => DMA_GPU_writeEna,
         DMA_GPU_readEna => DMA_GPU_readEna,
         DMA_GPU_write => DMA_GPU_write,
         vram_pause => vram_pause,
         vram_BUSY => vram_BUSY,
         vram_DOUT => vram_DOUT,
         vram_DOUT_READY => vram_DOUT_READY,
         loading_savestate => loading_savestate,
         SS_reset => SS_reset,
         SS_DataWrite => SS_DataWrite,
         SS_Adr => SS_Adr,
         SS_wren_GPU => SS_wren_GPU,
         SS_wren_Timing => SS_wren_Timing,
         SS_rden_GPU => SS_rden_GPU,
         SS_rden_Timing => SS_rden_Timing,
         allowunpause => c_allowunpause,
         Gun1IRQ10 => c_Gun1IRQ10,
         Gun2IRQ10 => c_Gun2IRQ10,
         errorLINE => c_errorLINE,
         errorRECT => c_errorRECT,
         errorPOLY => c_errorPOLY,
         errorGPU => c_errorGPU,
         errorMASK => c_errorMASK,
         errorFIFO => c_errorFIFO,
         bus_dataRead => c_bus_dataRead,
         save_gpu_ranges => c_save_gpu_ranges,
         bus_stall => c_bus_stall,
         gpu_dmaRequest => c_gpu_dmaRequest,
         DMA_GPU_read => c_DMA_GPU_read,
         irq_VBLANK => c_irq_VBLANK,
         irq_GPU => c_irq_GPU,
         vram_paused => c_vram_paused,
         vram_BURSTCNT => c_vram_BURSTCNT,
         vram_ADDR => c_vram_ADDR,
         vram_DIN => c_vram_DIN,
         vram_BE => c_vram_BE,
         vram_WE => c_vram_WE,
         vram_RD => c_vram_RD,
         hblank_tmr => c_hblank_tmr,
         vblank_tmr => c_vblank_tmr,
         dotclock => c_dotclock,
         video_hsync => c_video_hsync,
         video_vsync => c_video_vsync,
         video_hblank => c_video_hblank,
         video_vblank => c_video_vblank,
         video_dispblank => c_video_dispblank,
         video_DisplayWidth => c_video_DisplayWidth,
         video_DisplayHeight => c_video_DisplayHeight,
         video_DisplayOffsetX => c_video_DisplayOffsetX,
         video_DisplayOffsetY => c_video_DisplayOffsetY,
         video_ce => c_video_ce,
         video_interlace => c_video_interlace,
         video_r => c_video_r,
         video_g => c_video_g,
         video_b => c_video_b,
         video_isPal => c_video_isPal,
         video_fbmode => c_video_fbmode,
         video_fb24 => c_video_fb24,
         video_hResMode => c_video_hResMode,
         video_frameindex => c_video_frameindex,
         SS_DataRead_GPU => c_SS_DataRead_GPU,
         SS_DataRead_Timing => c_SS_DataRead_Timing,
         SS_Idle => c_SS_Idle,
         dbg_req => c_dbg_req
      );

   -- DDR / VRAM model and vram_pause, driven by the reference's port.
   ddr : process (clk2x)
      type mem_t is array (0 to 2 ** 20 - 1) of integer;
      type mem_ptr is access mem_t;
      variable lo, hi : mem_ptr;
      variable s1 : positive := 1 + (SEED mod 2147483000);
      variable s2 : positive := 77;
      variable r : real;
      variable busy_run, pause_cnt, lat, cnt, idx : integer := 0;
      variable active : boolean := false;
      variable w : std_logic_vector(63 downto 0);
      impure function rnd(n : integer) return integer is
      begin
         if n <= 0 then return 0; end if;
         uniform(s1, s2, r);
         return integer(floor(r * real(n)));
      end function;
   begin
      if rising_edge(clk2x) then
         if lo = null then
            lo := new mem_t; hi := new mem_t;
            for i in mem_t'range loop
               uniform(s1, s2, r); lo(i) := integer(floor(r * 4294967295.0)) - 2147483647;
               uniform(s1, s2, r); hi(i) := integer(floor(r * 4294967295.0)) - 2147483647;
            end loop;
         end if;
         -- a request is taken in the clock where busy is low
         if (vram_BUSY = '0') then
            if (r_vram_RD = '1') then
               assert not active report "TB a read was issued while a burst is in flight" severity failure;
               idx := to_integer(unsigned(r_vram_ADDR(22 downto 3)));
               cnt := to_integer(unsigned(r_vram_BURSTCNT));
               lat := env_lat_min + rnd(env_lat_max - env_lat_min + 1);
               active := cnt > 0;
            elsif (r_vram_WE = '1') then
               idx := to_integer(unsigned(r_vram_ADDR(22 downto 3)));
               w := std_logic_vector(to_signed(hi(idx), 32)) & std_logic_vector(to_signed(lo(idx), 32));
               for i in 0 to 7 loop
                  if (r_vram_BE(i) = '1') then w(i * 8 + 7 downto i * 8) := r_vram_DIN(i * 8 + 7 downto i * 8); end if;
               end loop;
               lo(idx) := to_integer(signed(w(31 downto 0)));
               hi(idx) := to_integer(signed(w(63 downto 32)));
            end if;
         end if;
         vram_DOUT_READY <= '0';
         if active then
            if lat > 0 then
               lat := lat - 1;
            elsif rnd(256) >= env_gap_p then
               vram_DOUT <= std_logic_vector(to_signed(hi(idx), 32)) & std_logic_vector(to_signed(lo(idx), 32));
               vram_DOUT_READY <= '1';
               idx := (idx + 1) mod 2 ** 20;
               cnt := cnt - 1;
               active := cnt > 0;
            end if;
         end if;
         if busy_run > 0 then
            busy_run := busy_run - 1; vram_BUSY <= '1';
         elsif rnd(256) < env_busy_p then
            busy_run := rnd(env_busy_run + 1); vram_BUSY <= '1';
         else
            vram_BUSY <= '0';
         end if;
         if (vram_pause = '1') then
            if pause_cnt > 0 then pause_cnt := pause_cnt - 1; else vram_pause <= '0'; end if;
         elsif rnd(65536) < env_pause_p then
            vram_pause <= '1'; pause_cnt := rnd(env_pause_hold + 1);
         end if;
      end if;
   end process;

   -- CPU bus, DMA, configuration inputs, ce and savestate_busy. Everything
   -- changes at the falling clk2x edge in the first half of a clk1x period.
   driver : process
      file f : text;
      variable l : line;
      variable vop, va, vb : std_logic_vector(31 downto 0);
      variable op, a, n, run : integer;
      variable s1 : positive := 1 + ((SEED + 4711) mod 2147483000);
      variable s2 : positive := 1234;
      variable r : real;
      variable ok : boolean;
      impure function rnd(n : integer) return integer is
      begin
         uniform(s1, s2, r);
         return integer(floor(r * real(n)));
      end function;
      procedure tick(force_ce : boolean := false) is
      begin
         wait until falling_edge(clk2x) and clk1x = '1';
         if force_ce then ce <= '1';
         elsif (r_SS_Idle = '1') then
            if rnd(256) < env_ce_idle then ce <= '0'; else ce <= '1'; end if;
         else
            if rnd(256) < env_ce_act then ce <= '0'; else ce <= '1'; end if;
         end if;
         -- savestate_busy: short pulses (it freezes the display fetch while high)
         if (savestate_busy = '1') then
            if rnd(8) = 0 then savestate_busy <= '0'; end if;
         elsif rnd(65536) < env_ss_p then
            savestate_busy <= '1';
         end if;
      end procedure;
      procedure recover is
      begin
         timeouts <= timeouts + 1;
         bus_read <= '0'; bus_write <= '0'; DMA_GPU_writeEna <= '0'; DMA_GPU_readEna <= '0';
         reset <= '1';
         for i in 1 to 4 loop tick(true); end loop;
         reset <= '0';
         for i in 1 to 4 loop tick(true); end loop;
      end procedure;
      -- waits until the bus is not stalled; false after a recovery
      procedure unstalled(good : out boolean) is
         variable k : integer := 0;
      begin
         good := true;
         loop
            tick(true);
            exit when r_bus_stall = '0';
            k := k + 1;
            if k > 200000 then recover; good := false; exit; end if;
         end loop;
      end procedure;
   begin
      for i in 1 to 100 loop tick(true); end loop;
      reset <= '0'; SS_reset <= '0';
      for i in 1 to 100 loop tick(true); end loop;
      file_open(f, STIM, read_mode);
      while not endfile(f) loop
         readline(f, l);
         hread(l, vop); hread(l, va); hread(l, vb);
         op := to_integer(unsigned(vop(7 downto 0)));
         a := to_integer(unsigned(va(30 downto 0)));
         case op is
            when 1 =>      -- bus write
               unstalled(ok);
               bus_addr <= unsigned(va(3 downto 0)); bus_dataWrite <= vb; bus_write <= '1';
               tick(true);
               bus_write <= '0';
            when 2 =>      -- a GPUREAD words over the bus
               for i in 1 to a loop
                  unstalled(ok);
                  exit when not ok;
                  bus_addr <= x"0"; bus_read <= '1';
                  tick(true);
                  bus_read <= '0';
                  cpu_words <= cpu_words + 1;
               end loop;
               unstalled(ok);
            when 3 =>      -- GPUSTAT
               unstalled(ok);
               bus_addr <= x"4"; bus_read <= '1';
               tick(true);
               bus_read <= '0';
            when 4 =>      -- wait until the reference is idle, at most a * 1024 clk1x
               n := 0; run := 0;
               loop
                  tick;
                  if (r_SS_Idle = '1') then run := run + 1; else run := 0; end if;
                  exit when run >= 6;
                  n := n + 1;
                  if n > a * 1024 then recover; exit; end if;
               end loop;
            when 5 =>      -- a clk1x periods
               for i in 1 to a loop tick; end loop;
            when 6 =>      -- configuration input or environment setting
               n := to_integer(unsigned(vb(30 downto 0)));
               case a is
                  when 0  => render24 <= vb(0);
                  when 1  => noTexture <= vb(0);
                  when 2  => textureFilter <= vb(1 downto 0);
                  when 3  => textureFilterStrength <= vb(1 downto 0);
                  when 4  => textureFilter2DOff <= vb(0);
                  when 5  => ditherOff <= vb(0);
                  when 6  => dither24 <= vb(0);
                  when 7  => drawSlow <= vb(0);
                  when 8  => oldGPU <= vb(0);
                  when 9  => interlaced480pHack <= vb(0);
                  when 10 => dmaOn <= vb(0);
                  when 11 => DMA_GPU_waiting <= vb(0);
                  when 16 => env_busy_p <= n;
                  when 17 => env_busy_run <= n;
                  when 18 => env_pause_p <= n;
                  when 19 => env_pause_hold <= n;
                  when 20 => env_lat_min <= n;
                  when 21 => env_lat_max <= n;
                  when 22 => env_gap_p <= n;
                  when 23 => env_ce_act <= n;
                  when 24 => env_ce_idle <= n;
                  when 25 => env_ss_p <= n;
                  when others => report "TB unknown setting" severity failure;
               end case;
            when 7 =>      -- reset for a clk1x periods
               reset <= '1'; resets <= resets + 1;
               for i in 1 to a loop tick(true); end loop;
               reset <= '0';
            when 8 =>      -- DMA write
               tick(true);
               DMA_GPU_write <= vb; DMA_GPU_writeEna <= '1';
               tick(true);
               DMA_GPU_writeEna <= '0';
            when 9 =>      -- a DMA reads, each when the reference requests one
               for i in 1 to a loop
                  n := 0; ok := true;
                  loop
                     tick(true);
                     exit when r_gpu_dmaRequest = '1';
                     n := n + 1;
                     if n > 200000 then recover; ok := false; exit; end if;
                  end loop;
                  exit when not ok;
                  DMA_GPU_readEna <= '1';
                  tick(true);
                  DMA_GPU_readEna <= '0';
                  dma_words <= dma_words + 1;
               end loop;
            when 10 =>     -- program boundary
               program <= program + 1;
            when others => report "TB unknown stimulus op" severity failure;
         end case;
      end loop;
      for i in 1 to 2000 loop tick(true); end loop;
      done <= true;
      wait;
   end process;

   compare : process (clk2x, clkvid)
      procedure fail(name : string) is
      begin
         report "MISMATCH " & name & " cycle=" & integer'image(cycle) & " program=" & integer'image(program) severity failure;
      end procedure;
      procedure chk(name : string; a, b : std_logic) is
      begin
         if a /= b then fail(name); end if;
      end procedure;
      procedure chk(name : string; a, b : std_logic_vector) is
      begin
         if a /= b then fail(name); end if;
      end procedure;
      procedure chk(name : string; a, b : unsigned) is
      begin
         -- as bit patterns: numeric_std's /= is true for any operand with a 'U'
         if std_logic_vector(a) /= std_logic_vector(b) then fail(name); end if;
      end procedure;
   begin
      if falling_edge(clk2x) or falling_edge(clkvid) then
      chk("allowunpause", r_allowunpause, c_allowunpause);
      chk("Gun1IRQ10", r_Gun1IRQ10, c_Gun1IRQ10);
      chk("Gun2IRQ10", r_Gun2IRQ10, c_Gun2IRQ10);
      chk("errorLINE", r_errorLINE, c_errorLINE);
      chk("errorRECT", r_errorRECT, c_errorRECT);
      chk("errorPOLY", r_errorPOLY, c_errorPOLY);
      chk("errorGPU", r_errorGPU, c_errorGPU);
      chk("errorMASK", r_errorMASK, c_errorMASK);
      chk("errorFIFO", r_errorFIFO, c_errorFIFO);
      chk("bus_dataRead", r_bus_dataRead, c_bus_dataRead);
      chk("save_gpu_ranges", r_save_gpu_ranges, c_save_gpu_ranges);
      chk("bus_stall", r_bus_stall, c_bus_stall);
      chk("gpu_dmaRequest", r_gpu_dmaRequest, c_gpu_dmaRequest);
      chk("DMA_GPU_read", r_DMA_GPU_read, c_DMA_GPU_read);
      chk("irq_VBLANK", r_irq_VBLANK, c_irq_VBLANK);
      chk("irq_GPU", r_irq_GPU, c_irq_GPU);
      chk("vram_paused", r_vram_paused, c_vram_paused);
      chk("vram_BURSTCNT", r_vram_BURSTCNT, c_vram_BURSTCNT);
      chk("vram_ADDR", r_vram_ADDR, c_vram_ADDR);
      chk("vram_DIN", r_vram_DIN, c_vram_DIN);
      chk("vram_BE", r_vram_BE, c_vram_BE);
      chk("vram_WE", r_vram_WE, c_vram_WE);
      chk("vram_RD", r_vram_RD, c_vram_RD);
      chk("hblank_tmr", r_hblank_tmr, c_hblank_tmr);
      chk("vblank_tmr", r_vblank_tmr, c_vblank_tmr);
      chk("dotclock", r_dotclock, c_dotclock);
      chk("video_hsync", r_video_hsync, c_video_hsync);
      chk("video_vsync", r_video_vsync, c_video_vsync);
      chk("video_hblank", r_video_hblank, c_video_hblank);
      chk("video_vblank", r_video_vblank, c_video_vblank);
      chk("video_dispblank", r_video_dispblank, c_video_dispblank);
      chk("video_DisplayWidth", r_video_DisplayWidth, c_video_DisplayWidth);
      chk("video_DisplayHeight", r_video_DisplayHeight, c_video_DisplayHeight);
      chk("video_DisplayOffsetX", r_video_DisplayOffsetX, c_video_DisplayOffsetX);
      chk("video_DisplayOffsetY", r_video_DisplayOffsetY, c_video_DisplayOffsetY);
      chk("video_ce", r_video_ce, c_video_ce);
      chk("video_interlace", r_video_interlace, c_video_interlace);
      chk("video_r", r_video_r, c_video_r);
      chk("video_g", r_video_g, c_video_g);
      chk("video_b", r_video_b, c_video_b);
      chk("video_isPal", r_video_isPal, c_video_isPal);
      chk("video_fbmode", r_video_fbmode, c_video_fbmode);
      chk("video_fb24", r_video_fb24, c_video_fb24);
      chk("video_hResMode", r_video_hResMode, c_video_hResMode);
      chk("video_frameindex", r_video_frameindex, c_video_frameindex);
      chk("SS_DataRead_GPU", r_SS_DataRead_GPU, c_SS_DataRead_GPU);
      chk("SS_DataRead_Timing", r_SS_DataRead_Timing, c_SS_DataRead_Timing);
      chk("SS_Idle", r_SS_Idle, c_SS_Idle);
      chk("dbg_req", r_dbg_req, c_dbg_req);
         compares <= compares + 1;
      end if;
      if falling_edge(clk2x) then
         cycle <= cycle + 1;
      end if;
   end process;

   -- What the run exercised, counted on the reference.
   coverage : process (clk2x, done)
      type count_t is array (0 to 6) of integer;
      variable req : count_t := (others => 0);
      variable prev : std_logic_vector(6 downto 0) := (others => '0');
      variable rd, we, rd_held, we_held, pause_cycles, paused, b128, mirror, words : integer := 0;
      variable ce0, ce0_vout, ssb, ssb_vout, req_we, busy_cycles, ready_last : integer := 0;
      variable out_line : line;
   begin
      if rising_edge(clk2x) then
         if (r_vram_RD = '1') then
            if (vram_BUSY = '0') then
               rd := rd + 1;
               if (r_vram_BURSTCNT = x"80") then b128 := b128 + 1; end if;
               if (r_vram_ADDR(22) = '1') then mirror := mirror + 1; end if;
            else rd_held := rd_held + 1; end if;
         end if;
         if (r_vram_WE = '1') then
            if (vram_BUSY = '0') then we := we + 1; else we_held := we_held + 1; end if;
         end if;
         if (vram_BUSY = '1') then busy_cycles := busy_cycles + 1; end if;
         if (vram_pause = '1') then pause_cycles := pause_cycles + 1; end if;
         if (r_vram_paused = '1') then paused := paused + 1; end if;
         if (vram_DOUT_READY = '1') then words := words + 1; end if;
         if (ce = '0') then ce0 := ce0 + 1; end if;
         if (savestate_busy = '1') then ssb := ssb + 1; end if;
         for i in 0 to 6 loop
            if (r_dbg_req(i) = '1' and prev(i) = '0') then req(i) := req(i) + 1; end if;
         end loop;
         if (r_dbg_req(6) = '1' and ce = '0') then ce0_vout := ce0_vout + 1; end if;
         if (r_dbg_req(6) = '1' and savestate_busy = '1') then ssb_vout := ssb_vout + 1; end if;
         if (r_dbg_req /= "0000000" and r_vram_WE = '1') then req_we := req_we + 1; end if;
         prev := r_dbg_req;
         if (cycle mod 250000 = 249999) then
            report "PROGRESS cycle=" & integer'image(cycle + 1) & " program=" & integer'image(program)
               & " timeouts=" & integer'image(timeouts) & " reads=" & integer'image(rd) & " writes=" & integer'image(we)
               & " videoout=" & integer'image(req(6)) severity note;
         end if;
      end if;
      if done'event and done then
         write(out_line, string'("COVERAGE reads=") & integer'image(rd) & " writes=" & integer'image(we)
            & " read_held=" & integer'image(rd_held) & " write_held=" & integer'image(we_held)
            & " busy_cycles=" & integer'image(busy_cycles) & " pause_cycles=" & integer'image(pause_cycles)
            & " paused=" & integer'image(paused) & " burst128=" & integer'image(b128) & " mirror_reads=" & integer'image(mirror)
            & " read_words=" & integer'image(words) & " ce_low=" & integer'image(ce0) & " savestate_busy=" & integer'image(ssb)
            & " videoout_req_ce_low=" & integer'image(ce0_vout) & " videoout_req_savestate_busy=" & integer'image(ssb_vout)
            & " req_with_write_done=" & integer'image(req_we));
         writeline(output, out_line);
         write(out_line, string'("REQUESTS vram2vram=") & integer'image(req(0)) & " vram2cpu=" & integer'image(req(1))
            & " line=" & integer'image(req(2)) & " rect=" & integer'image(req(3)) & " poly=" & integer'image(req(4))
            & " pipeline=" & integer'image(req(5)) & " videoout=" & integer'image(req(6)));
         writeline(output, out_line);
         write(out_line, string'("PASS gpu req hoist programs=") & integer'image(program) & " cycles=" & integer'image(cycle)
            & " compares=" & integer'image(compares) & " outputs=49 timeouts=" & integer'image(timeouts)
            & " resets=" & integer'image(resets) & " cpu_words=" & integer'image(cpu_words) & " dma_words=" & integer'image(dma_words));
         writeline(output, out_line);
         std.env.finish;
      end if;
   end process;
end architecture;
