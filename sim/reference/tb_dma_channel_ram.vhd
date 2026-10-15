-- Lockstep comparison of MiSTer dma (original) and dma_chanram (MADR/BCR in
-- LUT RAM). Both receive identical random stimulus; every output is compared
-- after every clk3x falling edge. The CPU-pause rule copies psx_top, so guest
-- register accesses only occur while the CPU owns the bus, as on hardware.
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
use ieee.math_real.all;

entity tb_dma_channel_ram is
   generic (CYCLES : integer := 400000; SEED : integer := 1);
end entity;

architecture sim of tb_dma_channel_ram is
   signal clk1x, clk3x : std_logic := '0';
   signal clk1xToggle, clk1xToggle3x, clk1xToggle3x_1, clk3xIndex : std_logic := '0';
   signal ce, reset, canDMA, cpuPaused : std_logic := '0';
   signal dma_wr, dma_reqprocessed, ram_dmafifo_read : std_logic := '0';
   signal dma_data, DMA_GPU_read, DMA_MDEC_read : std_logic_vector(31 downto 0) := (others => '0');
   signal gpu_dmaRequest, mdec_dmaWriteRequest, mdec_dmaReadRequest, spu_dmaRequest : std_logic := '0';
   signal cd_memctrl : unsigned(13 downto 0) := (others => '0');
   signal com0_delay, spu_timing_value : unsigned(3 downto 0) := (others => '0');
   signal spu_timing_on : std_logic := '0';
   signal DMA_CD_read : std_logic_vector(7 downto 0) := (others => '0');
   signal DMA_SPU_read : std_logic_vector(15 downto 0) := (others => '0');
   signal bus_addr : unsigned(6 downto 0) := (others => '0');
   signal bus_dataWrite : std_logic_vector(31 downto 0) := (others => '0');
   signal bus_read, bus_write : std_logic := '0';

   type tout is record
      errorCHOP, errorDMACPU, errorDMAFIFO, dmaRequest, dmaStallCPU, dmaOn, irqOut : std_logic;
      ram_Adr : std_logic_vector(22 downto 0);
      ram_cnt : std_logic_vector(1 downto 0);
      ram_ena : std_logic;
      ram_dmafifo_adr : std_logic_vector(22 downto 0);
      ram_dmafifo_data : std_logic_vector(31 downto 0);
      ram_dmafifo_empty : std_logic;
      dma_cache_Adr : std_logic_vector(20 downto 0);
      dma_cache_data : std_logic_vector(31 downto 0);
      dma_cache_write, DMA_GPU_waiting, DMA_GPU_writeEna, DMA_GPU_readEna : std_logic;
      DMA_GPU_write : std_logic_vector(31 downto 0);
      DMA_MDEC_writeEna, DMA_MDEC_readEna : std_logic;
      DMA_MDEC_write : std_logic_vector(31 downto 0);
      DMA_CD_readEna, DMA_SPU_writeEna, DMA_SPU_readEna : std_logic;
      DMA_SPU_write : std_logic_vector(15 downto 0);
      bus_dataRead : std_logic_vector(31 downto 0);
      SS_idle : std_logic;
   end record;
   signal o, c : tout;
   signal done : boolean := false;
begin
   clk3x <= not clk3x after 5 ns when not done;
   clk1x <= not clk1x after 15 ns when not done;

   process (clk1x) begin if rising_edge(clk1x) then clk1xToggle <= not clk1xToggle; end if; end process;
   process (clk3x) begin
      if rising_edge(clk3x) then
         clk1xToggle3x <= clk1xToggle; clk1xToggle3x_1 <= clk1xToggle3x;
         clk3xIndex <= '0';
         if clk1xToggle3x_1 = clk1xToggle then clk3xIndex <= '1'; end if;
      end if;
   end process;

   original : entity work.dma port map (
      clk1x => clk1x, clk3x => clk3x, clk3xIndex => clk3xIndex, ce => ce, reset => reset,
      errorCHOP => o.errorCHOP, errorDMACPU => o.errorDMACPU, errorDMAFIFO => o.errorDMAFIFO,
      TURBO => '0', TURBO_CACHE => '0', ram8mb => '0', ignoreCDTiming => '0',
      canDMA => canDMA, cpuPaused => cpuPaused, dmaRequest => o.dmaRequest, dmaStallCPU => o.dmaStallCPU,
      dmaOn => o.dmaOn, irqOut => o.irqOut, ram_Adr => o.ram_Adr, ram_cnt => o.ram_cnt, ram_ena => o.ram_ena,
      dma_wr => dma_wr, dma_reqprocessed => dma_reqprocessed, dma_data => dma_data,
      ram_dmafifo_adr => o.ram_dmafifo_adr, ram_dmafifo_data => o.ram_dmafifo_data,
      ram_dmafifo_empty => o.ram_dmafifo_empty, ram_dmafifo_read => ram_dmafifo_read,
      dma_cache_Adr => o.dma_cache_Adr, dma_cache_data => o.dma_cache_data, dma_cache_write => o.dma_cache_write,
      gpu_dmaRequest => gpu_dmaRequest, DMA_GPU_waiting => o.DMA_GPU_waiting, DMA_GPU_writeEna => o.DMA_GPU_writeEna,
      DMA_GPU_readEna => o.DMA_GPU_readEna, DMA_GPU_write => o.DMA_GPU_write, DMA_GPU_read => DMA_GPU_read,
      mdec_dmaWriteRequest => mdec_dmaWriteRequest, mdec_dmaReadRequest => mdec_dmaReadRequest,
      DMA_MDEC_writeEna => o.DMA_MDEC_writeEna, DMA_MDEC_readEna => o.DMA_MDEC_readEna,
      DMA_MDEC_write => o.DMA_MDEC_write, DMA_MDEC_read => DMA_MDEC_read,
      cd_memctrl => cd_memctrl, com0_delay => com0_delay, DMA_CD_readEna => o.DMA_CD_readEna, DMA_CD_read => DMA_CD_read,
      spu_timing_on => spu_timing_on, spu_timing_value => spu_timing_value, spu_dmaRequest => spu_dmaRequest,
      DMA_SPU_writeEna => o.DMA_SPU_writeEna, DMA_SPU_readEna => o.DMA_SPU_readEna,
      DMA_SPU_write => o.DMA_SPU_write, DMA_SPU_read => DMA_SPU_read,
      bus_addr => bus_addr, bus_dataWrite => bus_dataWrite, bus_read => bus_read, bus_write => bus_write,
      bus_dataRead => o.bus_dataRead, loading_savestate => '0', SS_reset => '0', SS_DataWrite => (others => '0'),
      SS_Adr => (others => '0'), SS_wren => '0', SS_rden => '0', SS_DataRead => open, SS_idle => o.SS_idle);

   candidate : entity work.dma_chanram port map (
      clk1x => clk1x, clk3x => clk3x, clk3xIndex => clk3xIndex, ce => ce, reset => reset,
      errorCHOP => c.errorCHOP, errorDMACPU => c.errorDMACPU, errorDMAFIFO => c.errorDMAFIFO,
      TURBO => '0', TURBO_CACHE => '0', ram8mb => '0', ignoreCDTiming => '0',
      canDMA => canDMA, cpuPaused => cpuPaused, dmaRequest => c.dmaRequest, dmaStallCPU => c.dmaStallCPU,
      dmaOn => c.dmaOn, irqOut => c.irqOut, ram_Adr => c.ram_Adr, ram_cnt => c.ram_cnt, ram_ena => c.ram_ena,
      dma_wr => dma_wr, dma_reqprocessed => dma_reqprocessed, dma_data => dma_data,
      ram_dmafifo_adr => c.ram_dmafifo_adr, ram_dmafifo_data => c.ram_dmafifo_data,
      ram_dmafifo_empty => c.ram_dmafifo_empty, ram_dmafifo_read => ram_dmafifo_read,
      dma_cache_Adr => c.dma_cache_Adr, dma_cache_data => c.dma_cache_data, dma_cache_write => c.dma_cache_write,
      gpu_dmaRequest => gpu_dmaRequest, DMA_GPU_waiting => c.DMA_GPU_waiting, DMA_GPU_writeEna => c.DMA_GPU_writeEna,
      DMA_GPU_readEna => c.DMA_GPU_readEna, DMA_GPU_write => c.DMA_GPU_write, DMA_GPU_read => DMA_GPU_read,
      mdec_dmaWriteRequest => mdec_dmaWriteRequest, mdec_dmaReadRequest => mdec_dmaReadRequest,
      DMA_MDEC_writeEna => c.DMA_MDEC_writeEna, DMA_MDEC_readEna => c.DMA_MDEC_readEna,
      DMA_MDEC_write => c.DMA_MDEC_write, DMA_MDEC_read => DMA_MDEC_read,
      cd_memctrl => cd_memctrl, com0_delay => com0_delay, DMA_CD_readEna => c.DMA_CD_readEna, DMA_CD_read => DMA_CD_read,
      spu_timing_on => spu_timing_on, spu_timing_value => spu_timing_value, spu_dmaRequest => spu_dmaRequest,
      DMA_SPU_writeEna => c.DMA_SPU_writeEna, DMA_SPU_readEna => c.DMA_SPU_readEna,
      DMA_SPU_write => c.DMA_SPU_write, DMA_SPU_read => DMA_SPU_read,
      bus_addr => bus_addr, bus_dataWrite => bus_dataWrite, bus_read => bus_read, bus_write => bus_write,
      bus_dataRead => c.bus_dataRead, loading_savestate => '0', SS_reset => '0', SS_DataWrite => (others => '0'),
      SS_Adr => (others => '0'), SS_wren => '0', SS_rden => '0', SS_DataRead => open, SS_idle => c.SS_idle);

   -- psx_top's CPU/DMA ownership rule.
   process (clk1x) begin
      if rising_edge(clk1x) then
         if reset = '1' then cpuPaused <= '0';
         elsif ce = '1' then
            if (cpuPaused = '1' and o.dmaOn = '1') or (o.dmaRequest = '1' and canDMA = '1') then cpuPaused <= '1';
            elsif o.dmaOn = '0' then cpuPaused <= '0'; end if;
         end if;
      end if;
   end process;

   stimulus : process
      variable s1, s2 : positive := 1;
      variable cycle, transfers, reads, nonzero, writes : integer := 0;
      variable last_on : std_logic := '0';
      variable r : real;
      variable addr : unsigned(6 downto 0);
      impure function rnd return real is begin uniform(s1, s2, r); return r; end;
      impure function bits(n : integer) return std_logic_vector is
         variable v : std_logic_vector(n - 1 downto 0);
      begin
         for i in v'range loop if rnd < 0.5 then v(i) := '1'; else v(i) := '0'; end if; end loop;
         return v;
      end;
      impure function chance(p : real) return std_logic is begin if rnd < p then return '1'; end if; return '0'; end;
   begin
      s1 := SEED; s2 := SEED * 7 + 3;
      reset <= '1'; ce <= '1'; canDMA <= '1';
      for i in 0 to 5 loop wait until rising_edge(clk1x); end loop;
      while cycle < CYCLES loop
         wait until falling_edge(clk1x);
         reset <= chance(0.00005);
         ce <= chance(0.97);
         canDMA <= chance(0.9);
         dma_wr <= chance(0.3); dma_reqprocessed <= chance(0.2); dma_data <= bits(32);
         ram_dmafifo_read <= chance(0.5);
         gpu_dmaRequest <= chance(0.6); mdec_dmaWriteRequest <= chance(0.5); mdec_dmaReadRequest <= chance(0.5);
         spu_dmaRequest <= chance(0.6);
         DMA_GPU_read <= bits(32); DMA_MDEC_read <= bits(32); DMA_CD_read <= bits(8); DMA_SPU_read <= bits(16);
         if rnd < 0.001 then cd_memctrl <= unsigned(bits(14)); com0_delay <= unsigned(bits(4));
            spu_timing_on <= chance(0.5); spu_timing_value <= unsigned(bits(4)); end if;
         bus_read <= '0'; bus_write <= '0';
         if cpuPaused = '0' and rnd < 0.15 then
            addr := unsigned(bits(7));
            bus_addr <= addr;
            if rnd < 0.5 then
               bus_read <= '1'; reads := reads + 1;
            else
               bus_write <= '1'; writes := writes + 1;
               bus_dataWrite <= bits(32);
               -- Short block counts and enabled channels make transfers finish.
               if addr(3 downto 2) = "01" or rnd < 0.3 then bus_dataWrite(31 downto 4) <= (others => '0'); end if;
               -- MiSTer only implements MDEC-in as a write and MDEC-out as a read.
               if addr(3 downto 2) = "10" then
                  case to_integer(addr(6 downto 4)) is
                     when 0 => bus_dataWrite(0) <= '1';
                     when 1 => bus_dataWrite(0) <= '0';
                     when others => null;
                  end case;
               end if;
            end if;
         end if;
         if o.bus_dataRead /= x"00000000" and o.bus_dataRead /= x"FFFFFFFF" then nonzero := nonzero + 1; end if;
         if last_on = '1' and o.dmaOn = '0' then transfers := transfers + 1; end if;
         last_on := o.dmaOn;
         cycle := cycle + 1;
      end loop;
      report "PASS DMA channel RAM cycles=" & integer'image(cycles) & " transfers=" & integer'image(transfers) &
         " guest_reads=" & integer'image(reads) & " nonzero_reads=" & integer'image(nonzero) &
         " guest_writes=" & integer'image(writes) severity note;
      done <= true; wait;
   end process;

   compare : process (clk3x) begin
      if falling_edge(clk3x) and now > 100 ns then
         assert o = c report "DMA output mismatch at " & time'image(now) severity failure;
      end if;
   end process;
end architecture;
