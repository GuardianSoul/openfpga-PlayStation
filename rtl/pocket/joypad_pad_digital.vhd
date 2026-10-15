-- SPDX-License-Identifier: GPL-2.0-only
-- Digital-pad-only drop-in for PSX_MiSTer joypad_pad (same entity name and
-- ports), for fit-limited Pocket builds.
--
-- Cycle-exact with the pinned references/PSX_MiSTer/rtl/joypad_pad.vhd under
-- these input constraints, which the Pocket top ties off:
--   joypadN.PadPortEnable='0' or PadPortDigital='1', every other PadPort*
--   flag '0' (no analog/DualShock/mouse/gun/neGcon/justifier/stick/pop'n);
--   isMultitap, multitapDigital, multitapAnalog, neGconRumble = '0';
--   ss_in = 0 (save states off).
-- Under them MiSTer's pad answers 0x01 (FF), 0x42 (41 5A LSB MSB) and parks
-- in READY on 0x43-0x4D like a real SCPH-1080; rumble, padMode, Justifier
-- IRQ and save-state words stay zero. sim/pocket/tb_joypad_pad_digital.vhd
-- runs both entities in lockstep and compares every output.
library IEEE;
use IEEE.std_logic_1164.all;
use IEEE.numeric_std.all;

use work.pJoypad.all;

entity joypad_pad is
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

architecture digital of joypad_pad is

   type tcontrollerState is (IDLE, READY, ID, BUTTONLSB, BUTTONMSB);
   signal controllerState : tcontrollerState := IDLE;

   signal joypad          : joypad_t;
   signal digitalPadSave  : std_logic := '0';
   signal active          : std_logic := '0';
   -- MiSTer reports port 1's DualShock rumble indices (-1 when idle) in
   -- ss_out; reset loads them from ss_in (zero) until the port is selected.
   signal rumbleIndexZero : std_logic := '0';

begin

   joypad1_rumble     <= (others => '0');
   joypad2_rumble     <= (others => '0');
   joypad3_rumble     <= (others => '0');
   joypad4_rumble     <= (others => '0');
   padMode            <= "00";
   JustifierIrqEnable <= "00";
   ss_out             <= x"00000" & (7 downto 0 => not rumbleIndexZero) & x"0";

   isActive <= active;
   joypad   <= joypad1 when portNr = 0 else joypad2;

   process (clk1x)
   begin
      if rising_edge(clk1x) then

         receiveValid   <= '0';
         receiveBuffer  <= x"00";
         ack            <= '0';

         if (reset = '1') then

            controllerState <= IDLE;
            active          <= '0';
            rumbleIndexZero <= '1';

         elsif (ce = '1') then

            if (selected = '0') then
               active          <= '0';
               controllerState <= IDLE;
            elsif (portNr = 0) then
               rumbleIndexZero <= '0';
            end if;

            if (actionNext = '1' and transmitting = '1' and selected = '1' and joypad.PadPortEnable = '1') then
               if (active = '0' and slotIdle = '1') then
                  if (controllerState = IDLE and transmitValue = x"01") then
                     controllerState <= READY;
                     active          <= '1';
                     ack             <= '1';
                     digitalPadSave  <= joypad.PadPortDigital;
                     receiveValid    <= '1';
                     receiveBuffer   <= x"FF";
                  end if;
               elsif (active = '1') then
                  case (controllerState) is
                     when IDLE =>
                        if (transmitValue = x"01") then
                           controllerState <= READY;
                           ack             <= '1';
                           digitalPadSave  <= joypad.PadPortDigital;
                           receiveValid    <= '1';
                           receiveBuffer   <= x"FF";
                        end if;

                     when READY =>
                        if (transmitValue = x"42") then
                           receiveBuffer   <= x"41";
                           controllerState <= ID;
                           ack             <= '1';
                           receiveValid    <= '1';
                        elsif (transmitValue = x"43" or transmitValue = x"44" or transmitValue = x"45" or
                               transmitValue = x"46" or transmitValue = x"47" or transmitValue = x"4C" or
                               transmitValue = x"4D") then
                           null; -- DualShock commands: a digital pad stays in READY
                        else
                           controllerState <= IDLE;
                        end if;

                     when ID =>
                        receiveBuffer   <= x"5A";
                        controllerState <= BUTTONLSB;
                        ack             <= '1';
                        receiveValid    <= '1';

                     when BUTTONLSB =>
                        receiveBuffer(0) <= not joypad.KeySelect;
                        receiveBuffer(1) <= not joypad.KeyL3;
                        receiveBuffer(2) <= not joypad.KeyR3;
                        receiveBuffer(3) <= not joypad.KeyStart;
                        receiveBuffer(4) <= not joypad.KeyUp;
                        receiveBuffer(5) <= not joypad.KeyRight;
                        receiveBuffer(6) <= not joypad.KeyDown;
                        receiveBuffer(7) <= not joypad.KeyLeft;
                        if (digitalPadSave = '1') then
                           if (to_integer(joypad.Analog1X) <= -64) then receiveBuffer(7) <= '0'; end if;
                           if (to_integer(joypad.Analog1X) >=  64) then receiveBuffer(5) <= '0'; end if;
                           if (to_integer(joypad.Analog1Y) <= -64) then receiveBuffer(4) <= '0'; end if;
                           if (to_integer(joypad.Analog1Y) >=  64) then receiveBuffer(6) <= '0'; end if;
                        end if;
                        controllerState <= BUTTONMSB;
                        ack             <= '1';
                        receiveValid    <= '1';

                     when BUTTONMSB =>
                        receiveBuffer(0) <= not joypad.KeyL2;
                        receiveBuffer(1) <= not joypad.KeyR2;
                        receiveBuffer(2) <= not joypad.KeyL1;
                        receiveBuffer(3) <= not joypad.KeyR1;
                        receiveBuffer(4) <= not joypad.KeyTriangle;
                        receiveBuffer(5) <= not joypad.KeyCircle;
                        receiveBuffer(6) <= not joypad.KeyCross;
                        receiveBuffer(7) <= not joypad.KeySquare;
                        receiveValid     <= '1';
                        controllerState  <= IDLE;
                  end case;
               end if;
            end if;

         end if;
      end if;
   end process;

end architecture;
