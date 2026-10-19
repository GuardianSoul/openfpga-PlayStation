-- SPDX-License-Identifier: GPL-2.0-only
-- Table-driven drop-in for rtl/pocket/joypad_pad_dualshock.vhd (same entity
-- name, ports and generics; prepare --pad-table). The command decoding of
-- the DualShock protocol moves from logic into one 512x20 M10K ROM: for each
-- controller state (and saved mode, where it matters) and class of the byte
-- being sent it holds the next state, ack, one side effect and the reply
-- (a byte, the buttons or the stick byte). The fixed reply sequences
-- (0x45/0x46/0x47/0x4C/0x4D and the config padding) are unrolled into
-- states of their own, so no reply pointer or byte counter is kept.
--
-- What stays in logic: the state register, the reply mux (byte, buttons with
-- the stick D-pad, stick byte), the mode bits (config, analog, lock,
-- ANALOG_LEVEL and the L3+R3 combo, unchanged) and the stick scaling table,
-- read every clock from the live sticks exactly as before; the byte class
-- comes from the other port of that M10K.
--
-- Timing: the ROM word is three clocks behind transmitValue and two behind
-- the state and saved modes. The SIO (joypad.vhd) sets transmitValue when a
-- byte starts and raises actionNext JOY_BAUD*8 clocks later, and a state or
-- saved mode only changes on actionNext, so every reply is cycle-exact with
-- joypad_pad_dualshock as long as transmitValue, selected and reset are
-- stable for three clocks before actionNext and actionNext pulses are three
-- or more clocks apart (sim/pocket/tb_joypad_pad_table.vhd checks it in
-- lockstep that way). RUMBLE_ECHO must stay false (the Pocket build): 0x4D
-- answers 0xFF.
library IEEE;
use IEEE.std_logic_1164.all;
use IEEE.numeric_std.all;

use work.pJoypad.all;

entity joypad_pad is
   generic (STICK_DPAD : boolean := false; RUMBLE_ECHO : boolean := false; ANALOG_LEVEL : boolean := false);
   port
   (
      clk1x                : in  std_logic;
      ce                   : in  std_logic;
      reset                : in  std_logic;

      DSAltSwitchMode      : in  std_logic;
      isMultitap           : in  std_logic := '0';
      multitapDigital      : in  std_logic;
      multitapAnalog       : in  std_logic;
      joypad1              : in  joypad_t;
      joypad2              : in  joypad_t;
      joypad3              : in  joypad_t;
      joypad4              : in  joypad_t;
      joypad1_rumble       : out std_logic_vector(15 downto 0) := (others => '0');
      joypad2_rumble       : out std_logic_vector(15 downto 0) := (others => '0');
      joypad3_rumble       : out std_logic_vector(15 downto 0) := (others => '0');
      joypad4_rumble       : out std_logic_vector(15 downto 0) := (others => '0');
      neGconRumble         : in  std_logic;
      padMode              : out std_logic_vector(1 downto 0);
      portNr               : in integer range 0 to 1;

      isPal                : in  std_logic;

      selected             : in  std_logic;
      actionNext           : in  std_logic := '0';
      transmitting         : in  std_logic := '0';
      transmitValue        : in  std_logic_vector(7 downto 0);

      isActive             : out std_logic := '0';
      slotIdle             : in  std_logic;

      receiveValid         : out std_logic;
      receiveBuffer        : out std_logic_vector(7 downto 0);
      ack                  : out std_logic;

      MouseEvent           : in  std_logic;
      MouseLeft            : in  std_logic;
      MouseRight           : in  std_logic;
      MouseX               : in  signed(8 downto 0);
      MouseY               : in  signed(8 downto 0);
      GunX                 : in  unsigned(7 downto 0);
      GunY_scanlines       : in  unsigned(8 downto 0);
      GunAimOffscreen      : in  std_logic;
      JustifierIrqEnable   : out std_logic_vector(1 downto 0);

      ss_in                : in  std_logic_vector(31 downto 0);
      ss_out               : out std_logic_vector(31 downto 0)
   );
end entity;

architecture table of joypad_pad is

   -- Byte classes (transmitValue): the values any state tells apart. They
   -- come from the upper half of the stick ROM (its second port).
   constant K_00 : natural := 0;  constant K_01 : natural := 1;  constant K_02 : natural := 2;  constant K_03 : natural := 3;
   constant K_42 : natural := 4;  constant K_43 : natural := 5;  constant K_44 : natural := 6;  constant K_45 : natural := 7;
   constant K_46 : natural := 8;  constant K_47 : natural := 9;  constant K_4C : natural := 10; constant K_4D : natural := 11;
   constant K_OTHER : natural := 12;

   -- States 0..63 read the ROM per byte class: index i (0..15) is stored as
   -- 4 * i, ROM address '0' & i & class. States 64..127 ignore the byte:
   -- ROM address '1' & state(5:0) & class(1:0), the same word four times.
   constant S_IDLE    : natural := 4 * 0;
   constant S_BLSBCFG : natural := 4 * 1;   -- buttons LSB of 0x43 outside config mode (sets config mode)
   constant S_CHCFG   : natural := 4 * 2;   -- 0x43 in config mode
   constant S_SETL    : natural := 4 * 3;
   constant S_SETM    : natural := 4 * 4;
   constant S_C46     : natural := 4 * 5;
   constant S_C47     : natural := 4 * 6;
   constant S_C4C     : natural := 4 * 7;
   constant S_READY   : natural := 4 * 8;   -- read as 4 * (8 + 4 * dsSave + 2 * (dsSave and config) + analog)
   constant G         : natural := 64;
   constant S_ID_READ : natural := G + 0;
   constant S_ID_CFG  : natural := G + 1;
   constant S_ID_CFGN : natural := G + 2;
   constant S_ID_SET  : natural := G + 3;
   constant S_ID_GET  : natural := G + 4;
   constant S_ID_46   : natural := G + 5;
   constant S_ID_47   : natural := G + 6;
   constant S_ID_4C   : natural := G + 7;
   constant S_ID_4D   : natural := G + 8;
   constant S_BLSB    : natural := G + 9;
   constant S_BMSB    : natural := G + 10;  -- read as + 1 with the analog or config mode saved
   constant S_ANRX    : natural := G + 12;
   constant S_ANRY    : natural := G + 13;
   constant S_ANLX    : natural := G + 14;
   constant S_ANLY    : natural := G + 15;
   constant S_GETST   : natural := G + 16;  -- read as + 1 with the analog mode saved
   constant S_Z       : natural := G + 18;  -- 5 x 0x00 (config padding; 0x43/0x46/0x47/0x4C default)
   constant S_GP      : natural := G + 23;  -- 0x45: 01 02, then S_GETST
   constant S_GQ      : natural := G + 25;  -- 0x45 after the mode byte: 02 01 00
   constant S_A0      : natural := G + 28;  -- 0x46 00: 00 01 02 00 0A
   constant S_A1      : natural := G + 33;  -- 0x46 01: 00 01 01 01 14
   constant S_B       : natural := G + 38;  -- 0x47 00: 00 02 00 01 00
   constant S_C0      : natural := G + 43;  -- 0x4C 00: 00 00 04 00 00
   constant S_C1      : natural := G + 48;  -- 0x4C 01: 00 00 07 00 00
   constant S_F       : natural := G + 53;  -- 0x4D: 6 x 0xFF

   -- Reply sources
   constant R_CONST : natural := 0;  -- the word's byte
   constant R_LSB   : natural := 1;  -- buttons, with the stick D-pad
   constant R_MSB   : natural := 2;
   constant R_STICK : natural := 3;  -- the scaled stick byte

   -- Side effects; the value is transmitValue bit 0 (00/01, lock 02/03).
   -- Accepting 0x01 in IDLE saves the modes (state IDLE and ack).
   constant E_NONE : natural := 0; constant E_CFG : natural := 1; constant E_ANA : natural := 2; constant E_LCK : natural := 3;

   -- ROM word: next state (6:0), ack (7), effect (9:8), source (11:10), byte (19:12).
   -- receiveValid is ack, or always outside IDLE and READY.
   type trom is array (0 to 511) of std_logic_vector(19 downto 0);
   type tcodes is array (natural range <>) of natural;
   function w(nxt : natural; acks : boolean; src : natural; byte : natural; effect : natural := E_NONE) return std_logic_vector is
      variable v : std_logic_vector(19 downto 0);
   begin
      v(6 downto 0)   := std_logic_vector(to_unsigned(nxt, 7));
      v(7)            := '0';
      if acks then v(7) := '1'; end if;
      v(9 downto 8)   := std_logic_vector(to_unsigned(effect, 2));
      v(11 downto 10) := std_logic_vector(to_unsigned(src, 2));
      v(19 downto 12) := std_logic_vector(to_unsigned(byte, 8));
      return v;
   end function;
   function c(byte : natural) return std_logic_vector is begin return w(0, false, R_CONST, byte); end function;
   -- every ROM address of a state that reads the class (s, k) or that ignores it (s)
   function ak(s, k : natural) return natural is begin return (s / 4) * 16 + k; end function;
   function ab(s, k : natural) return natural is begin return 256 + (s - G) * 4 + k; end function;
   function put(t0 : trom; s : natural; v : std_logic_vector) return trom is
      variable t : trom := t0;
   begin
      for k in 0 to 3 loop t(ab(s, k)) := v; end loop;
      return t;
   end function;
   -- a fixed reply sequence from state first, then to nxt (no ack when that is IDLE)
   function chain(t0 : trom; first : natural; replies : tcodes; nxt : natural) return trom is
      variable t : trom := t0;
   begin
      for i in replies'range loop
         if i < replies'high then
            t := put(t, first + i, w(first + i + 1, true, R_CONST, replies(i)));
         else
            t := put(t, first + i, w(nxt, nxt /= S_IDLE, R_CONST, replies(i)));
         end if;
      end loop;
      return t;
   end function;
   function make_rom return trom is
      variable t : trom := (others => (others => '0'));
      variable r : natural;
      variable id : natural;
   begin
      -- IDLE: 0x01 selects the pad
      for k in 0 to 15 loop t(ak(S_IDLE, k)) := w(S_IDLE, false, R_CONST, 0); end loop;
      t(ak(S_IDLE, K_01)) := w(S_READY, true, R_CONST, 16#FF#);
      -- READY: the command byte (variant: dsSave, dsSave and config, analog);
      -- the reply is the ID byte; an unaccepted DualShock command stays in READY
      for v in 0 to 7 loop
         r := S_READY + 4 * v;
         if (v / 2) mod 2 = 1 and v / 4 = 1 then id := 16#F3#; elsif v mod 2 = 1 then id := 16#73#; else id := 16#41#; end if;
         for k in 0 to 15 loop t(ak(r, k)) := w(S_IDLE, false, R_CONST, 0); end loop;
         t(ak(r, K_42)) := w(S_ID_READ, true, R_CONST, id);
         for k in K_43 to K_4D loop t(ak(r, k)) := w(S_READY, false, R_CONST, 0); end loop;
         if v / 4 = 1 then
            if (v / 2) mod 2 = 1 then
               t(ak(r, K_43)) := w(S_ID_CFG, true, R_CONST, id);
               t(ak(r, K_44)) := w(S_ID_SET, true, R_CONST, id);
               t(ak(r, K_45)) := w(S_ID_GET, true, R_CONST, id);
               t(ak(r, K_46)) := w(S_ID_46, true, R_CONST, id);
               t(ak(r, K_47)) := w(S_ID_47, true, R_CONST, id);
               t(ak(r, K_4C)) := w(S_ID_4C, true, R_CONST, id);
               t(ak(r, K_4D)) := w(S_ID_4D, true, R_CONST, id);
            else
               t(ak(r, K_43)) := w(S_ID_CFGN, true, R_CONST, id);
            end if;
         end if;
      end loop;
      -- ID: 0x5A
      t := put(t, S_ID_READ, w(S_BLSB, true, R_CONST, 16#5A#));
      t := put(t, S_ID_CFG, w(S_CHCFG, true, R_CONST, 16#5A#));
      t := put(t, S_ID_CFGN, w(S_BLSBCFG, true, R_CONST, 16#5A#));
      t := put(t, S_ID_SET, w(S_SETL, true, R_CONST, 16#5A#));
      t := put(t, S_ID_GET, w(S_GP, true, R_CONST, 16#5A#));
      t := put(t, S_ID_46, w(S_C46, true, R_CONST, 16#5A#));
      t := put(t, S_ID_47, w(S_C47, true, R_CONST, 16#5A#));
      t := put(t, S_ID_4C, w(S_C4C, true, R_CONST, 16#5A#));
      t := put(t, S_ID_4D, w(S_F, true, R_CONST, 16#5A#));
      -- buttons and sticks
      t := put(t, S_BLSB, w(S_BMSB, true, R_LSB, 0));
      for k in 0 to 15 loop t(ak(S_BLSBCFG, k)) := w(S_BMSB, true, R_LSB, 0); end loop;
      t(ak(S_BLSBCFG, K_00)) := w(S_BMSB, true, R_LSB, 0, E_CFG);
      t(ak(S_BLSBCFG, K_01)) := w(S_BMSB, true, R_LSB, 0, E_CFG);
      t := put(t, S_BMSB, w(S_IDLE, false, R_MSB, 0));
      t := put(t, S_BMSB + 1, w(S_ANRX, true, R_MSB, 0));
      t := put(t, S_ANRX, w(S_ANRY, true, R_STICK, 0));
      t := put(t, S_ANRY, w(S_ANLX, true, R_STICK, 0));
      t := put(t, S_ANLX, w(S_ANLY, true, R_STICK, 0));
      t := put(t, S_ANLY, w(S_IDLE, false, R_STICK, 0));
      -- DualShock config commands
      for k in 0 to 15 loop t(ak(S_CHCFG, k)) := w(S_Z, true, R_CONST, 0); end loop;
      t(ak(S_CHCFG, K_00)) := w(S_Z, true, R_CONST, 0, E_CFG);
      t(ak(S_CHCFG, K_01)) := w(S_Z, true, R_CONST, 0, E_CFG);
      for k in 0 to 15 loop t(ak(S_SETL, k)) := w(S_SETM, true, R_CONST, 0); end loop;
      t(ak(S_SETL, K_00)) := w(S_SETM, true, R_CONST, 0, E_ANA);
      t(ak(S_SETL, K_01)) := w(S_SETM, true, R_CONST, 0, E_ANA);
      for k in 0 to 15 loop t(ak(S_SETM, k)) := w(S_Z + 1, true, R_CONST, 0); end loop;
      t(ak(S_SETM, K_02)) := w(S_Z + 1, true, R_CONST, 0, E_LCK);
      t(ak(S_SETM, K_03)) := w(S_Z + 1, true, R_CONST, 0, E_LCK);
      t := put(t, S_GETST, w(S_GQ, true, R_CONST, 16#00#));
      t := put(t, S_GETST + 1, w(S_GQ, true, R_CONST, 16#01#));
      for k in 0 to 15 loop t(ak(S_C46, k)) := w(S_Z, true, R_CONST, 0); end loop;
      t(ak(S_C46, K_00)) := w(S_A0, true, R_CONST, 0);
      t(ak(S_C46, K_01)) := w(S_A1, true, R_CONST, 0);
      for k in 0 to 15 loop t(ak(S_C47, k)) := w(S_Z, true, R_CONST, 0); end loop;
      t(ak(S_C47, K_00)) := w(S_B, true, R_CONST, 0);
      for k in 0 to 15 loop t(ak(S_C4C, k)) := w(S_Z, true, R_CONST, 0); end loop;
      t(ak(S_C4C, K_00)) := w(S_C0, true, R_CONST, 0);
      t(ak(S_C4C, K_01)) := w(S_C1, true, R_CONST, 0);
      t := chain(t, S_Z, (16#00#, 16#00#, 16#00#, 16#00#, 16#00#), S_IDLE);
      t := chain(t, S_GP, (16#01#, 16#02#), S_GETST);
      t := chain(t, S_GQ, (16#02#, 16#01#, 16#00#), S_IDLE);
      t := chain(t, S_A0, (16#00#, 16#01#, 16#02#, 16#00#, 16#0A#), S_IDLE);
      t := chain(t, S_A1, (16#00#, 16#01#, 16#01#, 16#01#, 16#14#), S_IDLE);
      t := chain(t, S_B, (16#00#, 16#02#, 16#00#, 16#01#, 16#00#), S_IDLE);
      t := chain(t, S_C0, (16#00#, 16#00#, 16#04#, 16#00#, 16#00#), S_IDLE);
      t := chain(t, S_C1, (16#00#, 16#00#, 16#07#, 16#00#, 16#00#), S_IDLE);
      t := chain(t, S_F, (16#FF#, 16#FF#, 16#FF#, 16#FF#, 16#FF#, 16#FF#), S_IDLE);
      return t;
   end function;
   signal rom : trom := make_rom; -- never written: an M10K ROM
   attribute romstyle : string;
   attribute romstyle of rom : signal is "M10K";
   signal romAddr : std_logic_vector(8 downto 0);
   signal romWord : std_logic_vector(19 downto 0);

   -- 0..255: scaled stick byte for each signed axis value (upstream adds
   -- 1/8 and converts -127..127 to 0..255); 256..511: the byte class of each
   -- transmitValue. Dual-port: sticks on one port, the class on the other.
   type tsticks is array (0 to 511) of std_logic_vector(7 downto 0);
   function make_sticks return tsticks is
      variable t : tsticks := (others => std_logic_vector(to_unsigned(K_OTHER, 8)));
      variable v : signed(8 downto 0);
   begin
      for i in 0 to 255 loop
         v := resize(signed(to_unsigned(i, 8)), 9);
         v := v + v / 8;
         if    (v > 127) then v := to_signed(127, 9);
         elsif (v < -128) then v := to_signed(-128, 9);
         end if;
         t(i) := std_logic_vector(to_unsigned(to_integer(v) + 128, 8));
      end loop;
      t(256 + 16#00#) := std_logic_vector(to_unsigned(K_00, 8));
      t(256 + 16#01#) := std_logic_vector(to_unsigned(K_01, 8));
      t(256 + 16#02#) := std_logic_vector(to_unsigned(K_02, 8));
      t(256 + 16#03#) := std_logic_vector(to_unsigned(K_03, 8));
      t(256 + 16#42#) := std_logic_vector(to_unsigned(K_42, 8));
      t(256 + 16#43#) := std_logic_vector(to_unsigned(K_43, 8));
      t(256 + 16#44#) := std_logic_vector(to_unsigned(K_44, 8));
      t(256 + 16#45#) := std_logic_vector(to_unsigned(K_45, 8));
      t(256 + 16#46#) := std_logic_vector(to_unsigned(K_46, 8));
      t(256 + 16#47#) := std_logic_vector(to_unsigned(K_47, 8));
      t(256 + 16#4C#) := std_logic_vector(to_unsigned(K_4C, 8));
      t(256 + 16#4D#) := std_logic_vector(to_unsigned(K_4D, 8));
      return t;
   end function;
   signal sticks : tsticks := make_sticks;
   attribute romstyle of sticks : signal is "M10K";
   signal stickAddr : integer range 0 to 255;
   signal analogLarge : std_logic_vector(7 downto 0);
   signal classWord : std_logic_vector(7 downto 0);

   signal state     : std_logic_vector(6 downto 0) := std_logic_vector(to_unsigned(S_IDLE, 7));
   signal readState : std_logic_vector(6 downto 0);
   signal action    : std_logic;
   signal idleReady : std_logic;

   signal joypad          : joypad_t;

   signal digitalPadSave  : std_logic := '0';
   signal dsSave          : std_logic := '0';
   signal dsConfigModeSave : std_logic := '0';
   signal dsAnalogModeSave : std_logic := '0';

   -- Port 1's DualShock state (as joypad_pad_dualshock)
   signal dsConfigMode    : std_logic := '0';
   signal dsAnalogMode    : std_logic := '0';
   signal dsAnalogLock    : std_logic := '0';
   signal analogLevelSeen : std_logic := '0';

   signal reply : std_logic_vector(7 downto 0);

begin

   assert not RUMBLE_ECHO report "joypad_pad_table: RUMBLE_ECHO is not supported" severity failure;

   padMode <= '0' & dsAnalogMode;
   ss_out  <= (others => '0');
   joypad1_rumble <= (others => '0');
   joypad2_rumble <= (others => '0');
   joypad3_rumble <= (others => '0');
   joypad4_rumble <= (others => '0');
   JustifierIrqEnable <= "00";

   joypad <= joypad1 when portNr = 0 else joypad2;

   -- READY, BUTTONMSB and GETSTATE read the word of their saved-mode variant
   readState <= std_logic_vector(to_unsigned(S_READY, 7)) or ("00" & dsSave & (dsSave and dsConfigModeSave) & dsAnalogModeSave & "00")
                   when state = std_logic_vector(to_unsigned(S_READY, 7)) else
                state or ("000000" & (dsAnalogModeSave or dsConfigModeSave))
                   when state = std_logic_vector(to_unsigned(S_BMSB, 7)) else
                state or ("000000" & dsAnalogModeSave)
                   when state = std_logic_vector(to_unsigned(S_GETST, 7)) else
                state;
   romAddr(8)          <= readState(6);
   romAddr(7 downto 4) <= readState(5 downto 2);
   romAddr(3 downto 2) <= readState(1 downto 0) when readState(6) = '1' else classWord(3 downto 2);
   romAddr(1 downto 0) <= classWord(1 downto 0);

   stickAddr <= to_integer(unsigned(joypad.Analog2Y)) when state = std_logic_vector(to_unsigned(S_ANRY, 7)) else
                to_integer(unsigned(joypad.Analog1X)) when state = std_logic_vector(to_unsigned(S_ANLX, 7)) else
                to_integer(unsigned(joypad.Analog1Y)) when state = std_logic_vector(to_unsigned(S_ANLY, 7)) else
                to_integer(unsigned(joypad.Analog2X));

   process (clk1x)
   begin
      if rising_edge(clk1x) then
         romWord     <= rom(to_integer(unsigned(romAddr)));
         analogLarge <= sticks(stickAddr);
         classWord   <= sticks(256 + to_integer(unsigned(transmitValue)));
      end if;
   end process;

   process (romWord, digitalPadSave, dsSave, dsAnalogModeSave, dsConfigModeSave, joypad, analogLarge)
      variable b : std_logic_vector(7 downto 0);
   begin
      case to_integer(unsigned(romWord(11 downto 10))) is
         when R_LSB =>
            b := not (joypad.KeyLeft & joypad.KeyDown & joypad.KeyRight & joypad.KeyUp &
                      joypad.KeyStart & joypad.KeyR3 & joypad.KeyL3 & joypad.KeySelect);
            if (digitalPadSave = '1' or (STICK_DPAD and dsSave = '1' and dsAnalogModeSave = '0' and dsConfigModeSave = '0')) then
               if (to_integer(joypad.Analog1X) <= -64) then b(7) := '0'; end if;
               if (to_integer(joypad.Analog1X) >=  64) then b(5) := '0'; end if;
               if (to_integer(joypad.Analog1Y) <= -64) then b(4) := '0'; end if;
               if (to_integer(joypad.Analog1Y) >=  64) then b(6) := '0'; end if;
            end if;
         when R_MSB =>
            b := not (joypad.KeySquare & joypad.KeyCross & joypad.KeyCircle & joypad.KeyTriangle &
                      joypad.KeyR1 & joypad.KeyL1 & joypad.KeyR2 & joypad.KeyL2);
         when R_STICK => b := analogLarge;
         when others  => b := romWord(19 downto 12);
      end case;
      reply <= b;
   end process;

   action <= '1' when actionNext = '1' and transmitting = '1' and selected = '1' and joypad.PadPortEnable = '1' and
                      (isActive = '1' or slotIdle = '1') else '0';
   idleReady <= '1' when state = std_logic_vector(to_unsigned(S_IDLE, 7)) or state = std_logic_vector(to_unsigned(S_READY, 7)) else '0';

   process (clk1x)
   begin
      if rising_edge(clk1x) then

         receiveValid   <= '0';
         receiveBuffer  <= x"00";

         ack <= '0';

         if (reset = '1') then

            state           <= std_logic_vector(to_unsigned(S_IDLE, 7));
            isActive        <= '0';

            dsConfigMode    <= '0';
            dsAnalogMode    <= '0';
            dsAnalogLock    <= '0';
            analogLevelSeen <= '0';

         elsif (ce = '1') then

            if (selected = '0') then
               isActive <= '0';
               state    <= std_logic_vector(to_unsigned(S_IDLE, 7));
            elsif (joypad.PadPortDS = '1') then
               -- only port 1 can be a DualShock
               if (dsAnalogLock = '0') then
                  if (ANALOG_LEVEL and joypad.ToggleDS /= analogLevelSeen) then
                     dsAnalogMode    <= joypad.ToggleDS;
                     analogLevelSeen <= joypad.ToggleDS;
                  end if;
                  -- switch from button combo
                  if (joypad.KeyL3 = '1' and joypad.KeyR3 = '1' and joypad.KeyUp = '1') then
                     dsAnalogMode <= '1';
                  end if;
                  if (joypad.KeyL3 = '1' and joypad.KeyR3 = '1' and joypad.KeyDown = '1') then
                     dsAnalogMode <= '0';
                  end if;
               end if;
            elsif (portNr = 0) then
               dsConfigMode   <= '0';
               dsAnalogMode   <= '0';
               dsAnalogLock   <= '0';
            end if;

            if (action = '1') then
               state         <= romWord(6 downto 0);
               receiveBuffer <= reply;
               ack           <= romWord(7);
               receiveValid  <= romWord(7) or not idleReady;
               if (state = std_logic_vector(to_unsigned(S_IDLE, 7)) and romWord(7) = '1') then
                  isActive       <= '1';
                  digitalPadSave <= joypad.PadPortDigital;
                  dsSave         <= joypad.PadPortDS;
                  if (portNr = 0) then
                     dsConfigModeSave <= dsConfigMode;
                     dsAnalogModeSave <= dsAnalogMode;
                  else
                     dsConfigModeSave <= '0';
                     dsAnalogModeSave <= '0';
                  end if;
               end if;
               case to_integer(unsigned(romWord(9 downto 8))) is
                  when E_CFG  => dsConfigMode <= transmitValue(0);
                  when E_ANA  => dsAnalogMode <= transmitValue(0);
                  when E_LCK  => dsAnalogLock <= transmitValue(0);
                  when others => null;
               end case;
            end if;

         end if; -- ce
      end if; -- clock
   end process;

end architecture;
