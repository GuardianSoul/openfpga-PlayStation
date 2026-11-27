-- cd_subcpu: microcoded CD controller sub-CPU for cd_top (cd-rewrite-061, docs/cd-rewrite-061-port.md).
-- Firmware runs the command, drive and motor logic. Guest-visible timing comes from the timer unit and
-- the commit stage: firmware stages strobes, flag writes, register writes and response bytes ahead of time,
-- and hardware releases them on the clock a timer expires, as the old cd_top processes did.
--
-- Instruction word (20 bits): op(19:15) a(14:11) b(10:7) imm7(6:0), c = imm7(3:0), imm11 = (10:0).
--  0 NOP          1 ADD a=b+c      2 SUB a=b-c      3 AND a=b&c      4 OR a=b|c       5 XOR a=b^c
--  6 ADDI a=b+s7  7 ANDI a=b&u7    8 ORI a=b|u7     9 SHF a=shift(b,fn)  fn: 0 <<1  1 >>1  2 <<4  3 >>4
-- 10 LIMM a=u11  11 LSH a=(a<<11)|u11  12 CMP b,c  13 CMPI b,u7  14 BTST b,bit5  15 BSET a=b|bit  16 BCLR a=b&~bit
-- 17 BR cond,t12 18 CALL t12  19 RET  20 IN a,port7  21 OUT port7,b  22 LD a=[b+u5]  23 ST [b+u5]=a (128 words: b(6:0)+u5)
-- 4 clocks per instruction (fetch, read b, read c/a, execute); LD and LDT add one/two.
-- 24 LDT a=pm[b+u7]  25 WAIT a,c (a = events & c, clears them; stalls while 0)
-- Flags Z/C/N: set by ADD, SUB, ADDI, CMP, CMPI (C = carry of ADD, borrow of SUB/CMP); BTST sets Z.
-- Conditions (BR cond(14:12) target(11:0)): 0 AL 1 EQ 2 NE 3 LO 4 HS 5 MI 6 HI 7 LS.
-- IN ports: 0..95 come from cd_top (in_sel/in_data, one field per port, zero-extended); 97 WORK, 98 DRIVE,
--   99 MOTOR timer values (0 when idle), 100 events (peek), 101 flags, 102 clocks since the last timer expiry.
-- OUT ports: 0..95 go to cd_top; internal: 96..99 load+arm timer, 100..103 disarm, 105 fwflag,
--   106..108 commit slot strobes (arms the slot), 109..111 commit slot flag set(2:0), response FIFO reset (3),
--   flag clear (6:4), parameter FIFO reset (7),
--   112 immediate strobes(15:0) + flag set (19:16), 113 flag clear (3:0), 114 streamer push, 115 streamer arm,
--   116 streamer clear, 117 event clear, 118 staged write data, 119 staged write tag (slot 8:7, port 6:0), 120 staged clear, 121 ACK staging fresh,
--   126 parameter FIFO reset delay for the next commit. Aged timer loads are firmware: IN 102 clocks since the last
--   timer event, IN 103 clocks the old cmd_delay counted since the command byte / last ACK, IN 98 the DRIVE timer.
library IEEE;
use IEEE.std_logic_1164.all;
use IEEE.numeric_std.all;

entity cd_subcpu is
   generic
   (
      PM_ABITS  : natural := 12;     -- program counter bits (branch/call targets are 12 bits)
      PM_DEPTH  : natural := 1536;   -- program memory words (x 20 bits; 512 per M10K)
      FW_WRITABLE : boolean := true; -- debug image load port; false in release builds (program memory is a ROM)
      SIM_SPLIT : boolean := false;  -- simulation: program ROM as two halves (Verilator constant limit)
      NSRC      : natural := 48;     -- live response byte sources
      DW        : natural := 32      -- data path width (firmware values < 2^(DW-1); the product uses 28)
   );
   port
   (
      clk          : in  std_logic;
      ce           : in  std_logic;
      reset        : in  std_logic;

      fw_we        : in  std_logic;                        -- debug-only image load
      fw_addr      : in  std_logic_vector(PM_ABITS - 1 downto 0);
      fw_data      : in  std_logic_vector(19 downto 0);

      in_sel       : out unsigned(6 downto 0);             -- IN field select (combinational in cd_top)
      in_data      : in  std_logic_vector(31 downto 0);
      ev_in        : in  std_logic_vector(15 downto 0);    -- rising edges latch as events 0..11 (12..15: timers)

      out_we       : out std_logic;
      out_adr      : out unsigned(6 downto 0);
      out_d        : out std_logic_vector(26 downto 0);

      -- old counters' per-clock conditions
      irq_pending  : in  std_logic;                        -- CDROM_IRQFLAG /= 0
      dma_read     : in  std_logic;
      limit_speed  : in  std_logic;                        -- LIMITREADSPEED
      fetch_idle   : in  std_logic;
      drive_ready  : in  std_logic;                        -- fetch, process, copy idle and no readOnDisk(_buffer)
      drive_busy   : in  std_logic;
      tval_drive   : out unsigned(26 downto 0);            -- driveDelay
      drive_fire   : out std_logic;                        -- DRIVE timer event (commit release clock)

      strobe       : out std_logic_vector(15 downto 0);    -- one-clock pulses (commit or immediate)
      flags        : out std_logic_vector(3 downto 0);     -- 0 cmd_busy 1 cmdPending 2 working 3 spare

      src          : in  std_logic_vector(NSRC * 8 - 1 downto 0);
      resp_wr      : out std_logic;
      resp_din     : out std_logic_vector(7 downto 0);
      resp_reset   : out std_logic;
      param_reset  : out std_logic;                        -- parameter FIFO reset on a commit (cflag bit 7)
      dbg          : out std_logic_vector(19 downto 0)   -- simulation trace: events(7:0) & pc(11:0)
   );
end entity;

architecture arch of cd_subcpu is

   type t_pm is array (0 to PM_DEPTH - 1) of std_logic_vector(19 downto 0);
   constant PM_INIT : t_pm := (others => (others => '0'));   -- cd-rewrite-061: image inserted by the generator
   signal pm : t_pm := PM_INIT;
   attribute ramstyle : string;
   attribute ramstyle of pm : signal is "M10K";
   attribute max_depth : integer;
   attribute max_depth of pm : signal is 1024;   -- 1K x 10 slices: a 4:1 read mux per port (512 x 20 would need 7:1)

   signal pc       : unsigned(PM_ABITS - 1 downto 0) := (others => '0');
   signal ir       : std_logic_vector(19 downto 0) := (others => '0');
   signal pmb_addr : unsigned(PM_ABITS - 1 downto 0) := (others => '0');
   signal rom_addr : unsigned(PM_ABITS - 1 downto 0);   -- one read port: the fetch, or LDT's table word in phase 5
   signal ldt_rd   : std_logic_vector(3 downto 0) := (others => '0');
   signal phase    : unsigned(2 downto 0) := "000";      -- 0 fetch, 1 read b, 2 read c/a, 3 execute, 4 LD data, 5/6 LDT
   signal fz, fc, fn : std_logic := '0';

   type t_stack is array (0 to 3) of unsigned(PM_ABITS - 1 downto 0);
   signal stack : t_stack := (others => (others => '0'));
   signal sp    : unsigned(1 downto 0) := "00";

   -- registers 0..15 and data RAM 128..255 in one M10K (simple dual port, registered read)
   type t_rf is array (0 to 255) of std_logic_vector(DW - 1 downto 0);
   signal rfm          : t_rf := (others => (others => '0'));
   attribute ramstyle of rfm : signal is "M10K";
   signal rf_we        : std_logic := '0';
   signal rf_wa        : std_logic_vector(7 downto 0) := (others => '0');
   signal rf_wd        : std_logic_vector(DW - 1 downto 0) := (others => '0');
   signal rf_ra        : unsigned(7 downto 0);
   signal rf_q, ra_r   : std_logic_vector(DW - 1 downto 0) := (others => '0');
   signal ra, rb       : std_logic_vector(DW - 1 downto 0);
   signal dm_addr      : unsigned(6 downto 0);
   signal inw          : std_logic_vector(DW - 1 downto 0);

   signal ev, ev_in_1 : std_logic_vector(15 downto 0) := (others => '0');

   -- timers: 0 ACK (cmd_delay), 1 WORK (workDelay), 2 DRIVE (driveDelay), 3 MOTOR (motorStartClocks)
   signal t_ack   : unsigned(16 downto 0) := (others => '0');
   signal t_work  : unsigned(24 downto 0) := (others => '0');
   signal t_drive : unsigned(26 downto 0) := (others => '0');
   signal t_motor : unsigned(25 downto 0) := (others => '0');
   signal tarm    : std_logic_vector(3 downto 0) := (others => '0');
   signal tfire   : std_logic_vector(3 downto 0) := (others => '0');
   -- fwflag: 0 WORK holds at 1 (initseek), 1 WORK holds while MOTOR /= 0 (initseek), 2 DRIVE holds while MOTOR /= 0,
   --         3 ACK expiry waits while WORK > 8448 (GetStat during a Pause reply; old workDelayBeforeReply), 4 working (ACK guard)
   signal fwflag  : std_logic_vector(4 downto 0) := (others => '0');
   -- ACK staging is stale after a WORK/DRIVE event until the firmware re-stages it (OUT 121)
   signal ack_dirty : std_logic := '0';
   -- clocks since the last timer expiry (firmware subtracts its own latency from reloads)
   signal ev_age : unsigned(11 downto 0) := (others => '0');
   -- clocks since the command byte (ev_in(0) edge): exact ACK loads
   signal bc_age : unsigned(11 downto 0) := (others => '0');

   -- commit slots 0 ACK, 1 WORK, 2 DRIVE
   type t_c16 is array (0 to 2) of std_logic_vector(15 downto 0);
   type t_c8  is array (0 to 2) of std_logic_vector(7 downto 0);
   signal cstrobe : t_c16 := (others => (others => '0'));
   signal cflag   : t_c8 := (others => (others => '0'));   -- 3:0 set, 7:4 clear
   signal cvalid  : std_logic_vector(2 downto 0) := (others => '0');
   signal flags_r : std_logic_vector(3 downto 0) := (others => '0');
   signal strobe_r : std_logic_vector(15 downto 0) := (others => '0');

   -- streamer
   type t_sq is array (0 to 15) of std_logic_vector(21 downto 0);   -- 21:14 clear mask, 13:8 source, 7:0 OR literal
   signal sq_we     : std_logic := '0';                         -- the list lives in an MLAB (all 308 M10K are taken)
   signal sq_wa     : std_logic_vector(3 downto 0) := (others => '0');
   signal sq_wd     : std_logic_vector(21 downto 0) := (others => '0');
   signal sq_n, sq_ptr : unsigned(4 downto 0) := (others => '0');
   signal sq_timer  : std_logic := '0';                         -- 0 ACK, 1 WORK (mode 01)
   signal sq_slot   : unsigned(1 downto 0) := "00";             -- commit slot (mode 11)
   signal sq_mode   : std_logic_vector(1 downto 0) := "00";     -- 00 off, 01 timer counts N..1, 10 one per clock, 11 after a commit
   signal sq_go, sq_rst_at : std_logic := '0';
   signal resp_wr_r, resp_reset_r, prm_r : std_logic := '0';
   signal prm_dly, prm_cnt : unsigned(2 downto 0) := (others => '0');   -- parameter FIFO reset delay (Setloc 5, Setfilter 3)
   signal resp_din_r : std_logic_vector(7 downto 0) := (others => '0');
   signal sq_ent     : std_logic_vector(21 downto 0);
   signal sq_cnt     : unsigned(4 downto 0);

   -- staged writes in MLAB: 35:34 slot, 33:27 port, 26:0 data
   signal wq_we      : std_logic := '0';
   signal wq_wa, wq_ra : std_logic_vector(2 downto 0) := (others => '0');
   signal wq_wd, wq_q : std_logic_vector(35 downto 0);
   signal wq_data    : std_logic_vector(26 downto 0) := (others => '0');
   signal wq_tag     : std_logic_vector(8 downto 0) := (others => '0');
   signal wq_n, wq_ptr : unsigned(3 downto 0) := (others => '0');
   signal wq_go      : std_logic_vector(2 downto 0) := (others => '0');

begin

   gpm : if not SIM_SPLIT generate
      process (clk) begin
         if rising_edge(clk) then
            ir <= pm(to_integer(rom_addr));
            if FW_WRITABLE and fw_we = '1' then
               pm(to_integer(unsigned(fw_addr))) <= fw_data;
            end if;
         end if;
      end process;
   end generate;
   -- simulation only (Verilator's constant width limit): the same ROM as two address halves
   gpm_split : if SIM_SPLIT generate
      constant HALF : natural := PM_DEPTH / 2;
      type t_half is array (0 to HALF - 1) of std_logic_vector(19 downto 0);
      function half_of(lo : natural) return t_half is
         variable h : t_half;
      begin
         for i in 0 to HALF - 1 loop h(i) := PM_INIT(lo + i); end loop;
         return h;
      end function;
      signal pm0 : t_half := half_of(0);
      signal pm1 : t_half := half_of(HALF);
   begin
      process (clk) begin
         if rising_edge(clk) then
            if to_integer(rom_addr) < HALF then ir <= pm0(to_integer(rom_addr)); else ir <= pm1(to_integer(rom_addr) - HALF); end if;
         end if;
      end process;
   end generate;
   rom_addr <= pmb_addr when phase = "101" else pc;

   process (clk) begin
      if rising_edge(clk) then
         if rf_we = '1' then rfm(to_integer(unsigned(rf_wa))) <= rf_wd; end if;
         rf_q <= rfm(to_integer(rf_ra));
      end if;
   end process;
   dm_addr <= unsigned(ra_r(6 downto 0)) + resize(unsigned(ir(4 downto 0)), 7);
   rf_ra <= "0000" & unsigned(ir(10 downto 7)) when phase = "001" else
            "0000" & unsigned(ir(14 downto 11)) when (phase = "010" or phase = "011") and (ir(19 downto 15) = "10111" or ir(19 downto 15) = "01011") else
            "0000" & unsigned(ir(3 downto 0)) when phase = "010" or (phase = "011" and ir(19 downto 15) /= "10110") else
            '1' & dm_addr;   -- LD: data RAM read during execute
   ra <= ra_r;
   rb <= rf_q;
   wq_wd <= wq_tag & wq_data;
   iwq : entity work.RamMLAB generic map (width => 36, widthad => 3) port map (inclock => clk, wren => wq_we,
      data => wq_wd, wraddress => wq_wa, rdaddress => wq_ra, q => wq_q);
   wq_ra <= std_logic_vector(wq_ptr(2 downto 0));

   in_sel <= unsigned(ir(6 downto 0));
   process (all)
      variable p : integer range 0 to 127;
   begin
      p := to_integer(unsigned(ir(6 downto 0)));
      case p is
         when 97  => inw <= (others => '0'); inw(24 downto 0) <= std_logic_vector(t_work);   -- 0 when idle (fired or disarmed)
         when 98  => inw <= (others => '0'); inw(26 downto 0) <= std_logic_vector(t_drive);   -- 0 when idle (fired or disarmed)
         when 99  => inw <= (others => '0');   -- MOTOR: only bit 16 (>= 65,536) and bit 0 (/= 0), all the firmware tests
                     if t_motor(25 downto 16) /= 0 then inw(16) <= '1'; end if;
                     if t_motor /= 0 then inw(0) <= '1'; end if;
         when 100 => inw <= (others => '0'); inw(15 downto 0) <= ev;
         when 101 => inw <= (others => '0'); inw(3 downto 0) <= flags_r;
         when 102 => inw <= (others => '0'); inw(11 downto 0) <= std_logic_vector(ev_age);
         when 103 => inw <= (others => '0'); inw(11 downto 0) <= std_logic_vector(bc_age);
         when others => inw <= in_data(DW - 1 downto 0);
      end case;
   end process;

   isq : entity work.RamMLAB generic map (width => 22, widthad => 4) port map (inclock => clk, wren => sq_we,
      data => sq_wd, wraddress => sq_wa, rdaddress => std_logic_vector(sq_ptr(3 downto 0)), q => sq_ent);
   sq_cnt <= sq_n - sq_ptr;

   process (clk)
      variable op   : integer range 0 to 31;
      variable a33, b33, r33 : unsigned(DW downto 0);
      variable r    : std_logic_vector(DW - 1 downto 0);
      variable wr, take, addop : std_logic;
      variable cnd  : integer range 0 to 15;
      variable bitn : integer range 0 to 31;
      variable p    : integer range 0 to 127;
      variable si   : integer range 0 to 63;
      variable v_ev, v_rep : std_logic_vector(15 downto 0);
      variable v_fl : std_logic_vector(3 downto 0);
      variable v_st : std_logic_vector(15 downto 0);
      variable v_ow : boolean;
      variable s_slot : integer range 0 to 3;
      variable v_go : std_logic_vector(2 downto 0);
   begin
      if rising_edge(clk) then
         rf_we <= '0'; out_we <= '0'; wq_we <= '0'; sq_we <= '0';
         resp_wr_r <= '0'; resp_reset_r <= '0'; prm_r <= '0';
         tfire <= (others => '0');
         ev_in_1 <= ev_in;

         if reset = '1' then
            pc <= (others => '0'); phase <= "000"; sp <= "00";
            ev_in_1 <= (others => '0');   -- a level present at reset release is an edge (old newCD_1 starts at 0)
            ev <= (others => '0'); tarm <= (others => '0'); cvalid <= (others => '0');
            flags_r <= (others => '0'); strobe_r <= (others => '0');
            sq_mode <= "00"; sq_n <= (others => '0'); sq_ptr <= (others => '0');
            wq_n <= (others => '0'); wq_ptr <= (others => '0'); wq_go <= (others => '0');
            fwflag <= (others => '0'); ack_dirty <= '0';
         elsif ce = '1' then
            v_ev := ev or (ev_in and not ev_in_1);
            v_fl := flags_r; v_st := (others => '0'); v_ow := false; v_go := wq_go;

            ----------------------------------------------------------------- timers (old counters' rules)
            -- ACK (cmd_delay): raised to 8192 and held while an IRQ or a DMA read is pending. fw13 approximation: the
            -- old wait while drive or work is within 100 clocks of its event is gone (an ACK up to 101 clocks early)
            if tarm(0) = '1' then
               if irq_pending = '1' or dma_read = '1' then
                  if t_ack < 8192 then t_ack <= to_unsigned(8192, 17); end if;
               elsif t_ack /= 0 then
                  t_ack <= t_ack - 1;
               elsif not (fwflag(3) = '1' and fwflag(4) = '1' and t_work > 8447) and ack_dirty = '0' then   -- old workDelayBeforeReply = '0' (WORK runs one under workDelay)
                  tarm(0) <= '0'; tfire(0) <= '1'; v_ev(12) := '1';
                  if cvalid(0) = '1' and cflag(0)(3) = '1' then resp_reset_r <= '1'; end if;
                  if cvalid(0) = '1' and wq_ptr /= wq_n then wq_go(0) <= '1'; v_go(0) := '1'; end if;
               end if;
            end if;
            -- WORK (workDelay): initseek holds; at 11 it holds while an IRQ is pending (old code 7; the reload was always 11)
            if tarm(1) = '1' then
               if t_work = 11 and irq_pending = '1' then
                  null;   -- the firmware's only reload value is 11: hold
               elsif t_work /= 0 then
                  if not ((fwflag(1) = '1' and t_motor /= 0) or (fwflag(0) = '1' and t_work = 1)) then
                     t_work <= t_work - 1;
                  end if;
               else
                  tarm(1) <= '0'; tfire(1) <= '1'; v_ev(13) := '1';
                  if cvalid(1) = '1' and cflag(1)(3) = '1' then resp_reset_r <= '1'; end if;
                  if cvalid(1) = '1' and wq_ptr /= wq_n then wq_go(1) <= '1'; v_go(1) := '1'; end if;
               end if;
            end if;
            -- DRIVE (driveDelay): waits for MOTOR (unless the fw gate is off), the LIMITREADSPEED rule and the fetch
            if tarm(2) = '1' and not (fwflag(2) = '1' and t_motor /= 0) then
               if t_drive /= 0 then
                  if (limit_speed = '0' or irq_pending = '0' or t_drive > 8191) and (fetch_idle = '1' or t_drive > 127) then
                     t_drive <= t_drive - 1;
                  end if;
               elsif drive_ready = '1' then
                  tarm(2) <= '0'; tfire(2) <= '1'; v_ev(14) := '1';
                  if cvalid(2) = '1' and cflag(2)(3) = '1' then resp_reset_r <= '1'; end if;
                  if cvalid(2) = '1' and wq_ptr /= wq_n then wq_go(2) <= '1'; v_go(2) := '1'; end if;
               end if;
            end if;
            -- MOTOR (motorStartClocks)
            if tarm(3) = '1' then
               if t_motor /= 0 then t_motor <= t_motor - 1;
               else tarm(3) <= '0'; tfire(3) <= '1'; v_ev(15) := '1'; end if;
            end if;

            if tarm(0) = '1' and (tfire(1) = '1' or tfire(2) = '1') then ack_dirty <= '1'; end if;
            -- the WORK hold at 1 (Init during its implicit seek) ends with the seek: the DRIVE event (old driveState leaves
            -- SEEKIMPLICIT on that clock); the firmware's next fwflag write agrees
            if tfire(2) = '1' then fwflag(0) <= '0'; end if;
            if prm_cnt /= 0 then prm_cnt <= prm_cnt - 1; if prm_cnt = 1 then prm_r <= '1'; end if; end if;
            if tfire /= "0000" then ev_age <= (others => '0'); elsif ev_age /= x"FFF" then ev_age <= ev_age + 1; end if;
            -- clocks the old cmd_delay would have counted since the command byte (it waits for IRQ / DMA; 12 bits, saturating:
            -- the firmware reads it within a few hundred clocks)
            -- restarts on the command byte and on the ACK event (Init re-arms its second pass from the first)
            if (ev_in(0) = '1' and ev_in_1(0) = '0') or tfire(0) = '1' then bc_age <= (others => '0');
            elsif bc_age /= x"FFF" and irq_pending = '0' and dma_read = '0' then
               bc_age <= bc_age + 1;
            end if;

            ----------------------------------------------------------------- commit stage
            for s in 0 to 2 loop
               if cvalid(s) = '1' and tfire(s) = '1' then
                  v_st := v_st or cstrobe(s);
                  v_fl := (v_fl and not ('0' & cflag(s)(6 downto 4))) or ('0' & cflag(s)(2 downto 0));
                  if cflag(s)(7) = '1' then
                     if prm_dly = 0 then prm_r <= '1'; else prm_cnt <= prm_dly; end if;
                  end if;
                  cvalid(s) <= '0';
                  if wq_ptr /= wq_n then wq_go(s) <= '1'; end if;
                  if sq_mode = "11" and to_integer(sq_slot) = s then sq_go <= '1'; end if;
               end if;
            end loop;

            ----------------------------------------------------------------- response streamer
            if sq_ptr < sq_n then
               if (sq_mode = "01" and ((sq_timer = '0' and tarm(0) = '1' and t_ack(16 downto 5) = 0 and t_ack(4 downto 0) = sq_cnt) or
                                       (sq_timer = '1' and tarm(1) = '1' and t_work(24 downto 5) = 0 and t_work(4 downto 0) = sq_cnt))) or
                  sq_mode = "10" or (sq_mode = "11" and sq_go = '1') then
                  si := to_integer(unsigned(sq_ent(13 downto 8)));
                  if si /= 63 then resp_wr_r <= '1'; end if;   -- source 63: skip (no byte at this count)
                  if si = 0 or si >= NSRC then
                     resp_din_r <= sq_ent(7 downto 0);
                  else
                     resp_din_r <= (src(8 * si + 7 downto 8 * si) and not sq_ent(21 downto 14)) or sq_ent(7 downto 0);
                  end if;
                  sq_ptr <= sq_ptr + 1;
               elsif sq_mode = "01" and sq_rst_at = '1' and
                     ((sq_timer = '0' and tarm(0) = '1' and t_ack(16 downto 5) = 0 and t_ack(4 downto 0) = sq_cnt + 1) or
                      (sq_timer = '1' and tarm(1) = '1' and t_work(24 downto 5) = 0 and t_work(4 downto 0) = sq_cnt + 1)) then
                  resp_reset_r <= '1';
               end if;
            end if;

            ----------------------------------------------------------------- core
            case phase is
               when "000" =>
                  phase <= "001";
               when "001" =>
                  phase <= "010";
               when "010" =>
                  ra_r <= rf_q;
                  phase <= "011";
               when "011" =>
                  op := to_integer(unsigned(ir(19 downto 15)));
                  bitn := to_integer(unsigned(ir(4 downto 0)));
                  p := to_integer(unsigned(ir(6 downto 0)));
                  wr := '0'; r := (others => '0');
                  pc <= pc + 1;
                  phase <= "000";
                  -- one adder: ADD/ADDI a+b, SUB/CMP/CMPI a-b
                  a33 := unsigned('0' & ra);
                  case op is
                     when 6      => b33 := unsigned(resize(signed(ir(6 downto 0)), DW + 1));
                     when 13     => b33 := resize(unsigned(ir(6 downto 0)), DW + 1);
                     when others => b33 := unsigned('0' & rb);
                  end case;
                  if op = 2 or op = 12 or op = 13 then
                     r33 := a33 - b33;
                  else
                     r33 := a33 + b33;
                  end if;
                  addop := '0';
                  case op is
                     when 1 | 2 | 6 => r := std_logic_vector(r33(DW - 1 downto 0)); wr := '1'; addop := '1';
                     when 12 | 13   => addop := '1';
                     when 3 => r := ra and rb; wr := '1';
                     when 4 => r := ra or rb; wr := '1';
                     when 5 => r := ra xor rb; wr := '1';
                     when 7 => r := ra and std_logic_vector(resize(unsigned(ir(6 downto 0)), DW)); wr := '1';
                     when 8 => r := ra or std_logic_vector(resize(unsigned(ir(6 downto 0)), DW)); wr := '1';
                     when 9 =>
                        case ir(1 downto 0) is
                           when "00"   => r := ra(DW - 2 downto 0) & '0';
                           when "01"   => r := '0' & ra(DW - 1 downto 1);
                           when "10"   => r := ra(DW - 5 downto 0) & x"0";
                           when others => r := x"0" & ra(DW - 1 downto 4);
                        end case;
                        wr := '1';
                     when 10 => r(10 downto 0) := ir(10 downto 0); wr := '1';
                     when 11 => r := rb(DW - 12 downto 0) & ir(10 downto 0); wr := '1';
                     when 14 => if bitn < DW then fz <= not ra(bitn); else fz <= '1'; end if;
                     when 15 => r := ra; if bitn < DW then r(bitn) := '1'; end if; wr := '1';
                     when 16 => r := ra; if bitn < DW then r(bitn) := '0'; end if; wr := '1';
                     when 17 =>   -- cond(14:12), target(11:0)
                        cnd := to_integer(unsigned(ir(14 downto 12)));
                        case cnd is
                           when 0 => take := '1';
                           when 1 => take := fz;
                           when 2 => take := not fz;
                           when 3 => take := fc;
                           when 4 => take := not fc;
                           when 5 => take := fn;
                           when 6 => take := (not fc) and (not fz);
                           when others => take := fc or fz;
                        end case;
                        if take = '1' then pc <= unsigned(ir(PM_ABITS - 1 downto 0)); end if;
                     when 18 =>
                        stack(to_integer(sp)) <= pc + 1; sp <= sp + 1; pc <= unsigned(ir(PM_ABITS - 1 downto 0));
                     when 19 =>
                        pc <= stack(to_integer(sp - 1)); sp <= sp - 1;
                     when 20 => r := inw; wr := '1';
                     when 21 =>
                        if p < 96 then
                           out_we <= '1'; out_adr <= unsigned(ir(6 downto 0)); out_d <= ra(26 downto 0); v_ow := true;
                        else
                           case p is
                              when 96  => t_ack   <= unsigned(ra(16 downto 0)); tarm(0) <= '1';
                              when 97  => t_work  <= unsigned(ra(24 downto 0)); tarm(1) <= '1';
                              when 98  => t_drive <= unsigned(ra(26 downto 0)); tarm(2) <= '1';
                              when 99  => t_motor <= unsigned(ra(25 downto 0)); tarm(3) <= '1';
                              when 100 => tarm(0) <= '0'; t_ack <= (others => '0');   -- an idle timer reads 0
                              when 101 => tarm(1) <= '0'; t_work <= (others => '0');
                              when 102 => tarm(2) <= '0'; t_drive <= (others => '0');
                              when 103 => tarm(3) <= '0'; t_motor <= (others => '0');
                              when 105 => fwflag <= ra(4 downto 0);
                              when 106 => cstrobe(0) <= ra(15 downto 0); cvalid(0) <= '1';
                              when 107 => cstrobe(1) <= ra(15 downto 0); cvalid(1) <= '1';
                              when 108 => cstrobe(2) <= ra(15 downto 0); cvalid(2) <= '1';
                              when 109 => cflag(0) <= ra(7 downto 0);
                              when 110 => cflag(1) <= ra(7 downto 0);
                              when 111 => cflag(2) <= ra(7 downto 0);
                              when 112 => v_st := v_st or ra(15 downto 0); v_fl := v_fl or ra(19 downto 16);
                              when 113 => v_fl := v_fl and not ra(3 downto 0);
                              when 114 => sq_we <= '1'; sq_wa <= std_logic_vector(sq_n(3 downto 0)); sq_wd <= ra(21 downto 0); sq_n <= sq_n + 1;
                              when 115 => sq_mode <= ra(1 downto 0); sq_timer <= ra(2); sq_slot <= unsigned(ra(4 downto 3));
                                          sq_rst_at <= ra(5); sq_ptr <= (others => '0'); sq_go <= '0';
                              when 116 => sq_n <= (others => '0'); sq_ptr <= (others => '0'); sq_mode <= "00";
                              when 117 => v_ev := v_ev and not ra(15 downto 0);
                              when 118 => wq_data <= ra(26 downto 0);
                              when 119 => wq_tag <= ra(8 downto 0); wq_we <= '1'; wq_wa <= std_logic_vector(wq_n(2 downto 0)); wq_n <= wq_n + 1;
                              when 120 => wq_n <= (others => '0'); wq_ptr <= (others => '0'); wq_go <= (others => '0');
                              when 121 => ack_dirty <= '0';
                              when 126 => prm_dly <= unsigned(ra(2 downto 0));
                              when others => null;
                           end case;
                        end if;
                     when 22 => pc <= pc; phase <= "100";   -- LD: rf_ra = 32 + addr this clock, data next
                     when 23 => rf_we <= '1'; rf_wa <= '1' & std_logic_vector(dm_addr); rf_wd <= rb;
                     when 24 =>
                        pmb_addr <= unsigned(ra(PM_ABITS - 1 downto 0)) + unsigned(ir(6 downto 0));
                        pc <= pc; phase <= "101";
                     when 25 =>
                        v_rep := ev and rb(15 downto 0);
                        if v_rep = x"0000" then
                           pc <= pc; phase <= "011";
                        else
                           r := (others => '0'); r(15 downto 0) := v_rep; wr := '1';
                           v_ev := v_ev and not v_rep;
                        end if;
                     when others => null;
                  end case;
                  if addop = '1' then
                     if r33(DW - 1 downto 0) = 0 then fz <= '1'; else fz <= '0'; end if;
                     fc <= r33(DW); fn <= r33(DW - 1);
                  end if;
                  if wr = '1' then
                     rf_we <= '1'; rf_wa <= "0000" & ir(14 downto 11); rf_wd <= r;
                  end if;
               when "100" =>
                  rf_we <= '1'; rf_wa <= "0000" & ir(14 downto 11); rf_wd <= rf_q;
                  pc <= pc + 1; phase <= "000";
               when "101" =>                 -- the port reads the table word; ir still holds LDT
                  ldt_rd <= ir(14 downto 11);
                  phase <= "110";
               when others =>                -- ir holds the table word for this clock; the fetch reloads LDT next
                  rf_we <= '1'; rf_wa <= "0000" & ldt_rd; rf_wd <= std_logic_vector(resize(unsigned(ir), DW));
                  pc <= pc + 1; phase <= "000";
            end case;

            ----------------------------------------------------------------- staged writes (after the core's OUT)
            if wq_ptr < wq_n and not v_ow then
               s_slot := to_integer(unsigned(wq_q(35 downto 34)));
               if s_slot < 3 and v_go(s_slot mod 3) = '1' then
                  if unsigned(wq_q(33 downto 27)) = 98 then          -- staged DRIVE reload (next sector period)
                     t_drive <= unsigned(wq_q(26 downto 0)); tarm(2) <= '1';
                  else
                     out_we  <= '1';
                     out_adr <= unsigned(wq_q(33 downto 27));
                     out_d   <= wq_q(26 downto 0);
                  end if;
                  wq_ptr  <= wq_ptr + 1;
               end if;
            elsif wq_n /= 0 and wq_ptr = wq_n then
               wq_n <= (others => '0'); wq_ptr <= (others => '0'); wq_go <= (others => '0');
            end if;

            ev <= v_ev; flags_r <= v_fl; strobe_r <= v_st;
         end if;
      end if;
   end process;

   dbg <= ev(7 downto 0) & std_logic_vector(resize(pc, 12));
   tval_drive <= t_drive;
   drive_fire <= tfire(2);
   strobe     <= strobe_r;
   flags      <= flags_r;
   resp_wr    <= resp_wr_r;
   resp_din   <= resp_din_r;
   resp_reset <= resp_reset_r;
   param_reset <= prm_r;

end architecture;
