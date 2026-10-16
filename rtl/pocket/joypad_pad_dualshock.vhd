-- SPDX-License-Identifier: GPL-2.0-only
-- DualShock-capable drop-in for PSX_MiSTer joypad_pad (same entity name and
-- ports) for fit-limited Pocket builds (prepare --dualshock-lean). It is the
-- pinned references/PSX_MiSTer/rtl/joypad_pad.vhd with every branch that the
-- Pocket tie-offs make unreachable removed, so the remaining code reads the
-- same as upstream:
--   joypad1: PadPortEnable='1', PadPortDS and PadPortDigital dynamic (the
--   menu checkbox drives one and its inverse), keys and both sticks dynamic,
--   every other PadPort* flag, ToggleDS and WheelMap '0';
--   joypad2..4 disabled or digital pads (never DualShock);
--   isMultitap, multitapDigital, multitapAnalog, DSAltSwitchMode,
--   neGconRumble, MouseEvent/Left/Right = '0'; ss_in = 0; portNr only
--   changes while no port is selected (as the SIO does).
-- Only port 1 can be a DualShock, so only its state is kept. Rumble motors
-- and save-state words are not produced (the Pocket has neither): the rumble
-- outputs and ss_out are zero. Every other output (receiveValid,
-- receiveBuffer, ack, isActive, padMode, JustifierIrqEnable) is cycle-exact
-- with the pinned entity under those constraints;
-- sim/pocket/tb_joypad_pad_dualshock.vhd checks it in lockstep.
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

architecture dualshock of joypad_pad is

   type tcontrollerState is
   (
      IDLE,
      READY,
      ID,
      BUTTONLSB,
      BUTTONMSB,
      ANALOGRIGHTX,
      ANALOGRIGHTY,
      ANALOGLEFTX,
      ANALOGLEFTY,
      CHANGECONFIG,
      SETSTATELSB,
      SETSTATEMSB,
      GETSTATE,
      COMMAND46,
      COMMAND47,
      COMMAND4C,
      UPDATERUMBLE,
      ROMRESPONSE
   );
   signal controllerState : tcontrollerState := IDLE;
   signal nextState : tcontrollerState := IDLE;

   type tcommands is
   (
      COMMAND_NONE,
      COMMAND_READ_INPUTS,
      COMMAND_CHANGE_CONFIG_MODE,
      COMMAND_GET_STATE,
      COMMAND_SET_STATE,
      COMMAND_46,
      COMMAND_47,
      COMMAND_4C,
      COMMAND_RUMBLE,
      COMMAND_UNKNOWN
   );
   signal command : tcommands := COMMAND_NONE;

   signal joypad          : joypad_t;

   signal digitalPadSave  : std_logic := '0';
   signal dsSave          : std_logic := '0';

   signal analogLarge     : std_logic_vector(7 downto 0);

   -- Port 1's DualShock state (upstream portStates(0)); port 2 is never a
   -- DualShock, so upstream's portStates(1) stays at its defaults.
   signal dsConfigMode    : std_logic := '0';
   signal dsAnalogMode    : std_logic := '0';
   signal dsAnalogLock    : std_logic := '0';
   signal dsRumbleConfig  : std_logic_vector(47 downto 0) := (others => '1');

   signal dsConfigModeSave  : std_logic := '0';
   signal dsAnalogModeSave  : std_logic := '0';

   type tresponse is array (natural range <>) of std_logic_vector(7 downto 0);
   constant response : tresponse :=(
      x"00", x"00", x"00", x"00", x"00", x"00", -- padding
      x"01", x"02",                             -- analog get part 1
      x"02", x"01", x"00",                      -- analog get part 2
      x"00", x"01", x"02", x"00", x"0A",        -- 46+00
      x"00", x"01", x"01", x"01", x"14",        -- 46+01
      x"00", x"02", x"00", x"01", x"00",        -- 47
      x"00", x"00", x"04", x"00", x"00",        -- 4C+00
      x"00", x"00", x"07", x"00", x"00",        -- 4C+01
      x"FF", x"FF", x"FF", x"FF",               -- (upstream multitap padding)
      x"FF", x"FF", x"FF", x"FF"
   );

   signal rom_pointer : integer range 0 to 44;
   signal bytecount   : integer range 0 to 7;
   signal rumblecount : integer range 0 to 5;

begin

   padMode <= '0' & dsAnalogMode;
   ss_out  <= (others => '0');
   joypad1_rumble <= (others => '0');
   joypad2_rumble <= (others => '0');
   joypad3_rumble <= (others => '0');
   joypad4_rumble <= (others => '0');
   JustifierIrqEnable <= "00";

   joypad <= joypad1 when portNr = 0 else joypad2;

   process (clk1x)
      variable newAnalog : signed(8 downto 0) := (others => '0');
   begin
      if rising_edge(clk1x) then

         receiveValid   <= '0';
         receiveBuffer  <= x"00";

         ack <= '0';

         -- increase analog values by 1/8 and convert from -127..127 to 0..255
         newAnalog := resize(joypad.Analog2X, 9);
         case (controllerState) is
            when ANALOGRIGHTX => newAnalog := resize(joypad.Analog2X, 9);
            when ANALOGRIGHTY => newAnalog := resize(joypad.Analog2Y, 9);
            when ANALOGLEFTX  => newAnalog := resize(joypad.Analog1X, 9);
            when ANALOGLEFTY  => newAnalog := resize(joypad.Analog1Y, 9);
            when others => null;
         end case;

         newAnalog := newAnalog + newAnalog / 8;

         if    (newAnalog > 127) then newAnalog := to_signed(127, 9);
         elsif (newAnalog < -128) then newAnalog := to_signed(-128, 9);
         end if;

         analogLarge <= std_logic_vector(to_unsigned(to_integer(newAnalog) + 128, 8));

         if (reset = '1') then

            controllerState <= IDLE;
            isActive        <= '0';

            -- ss_in is zero on the Pocket
            dsConfigMode   <= '0';
            dsAnalogMode   <= '0';
            dsAnalogLock   <= '0';
            dsRumbleConfig <= (others => '1');

         elsif (ce = '1') then

            if (selected = '0') then
               isActive        <= '0';
               controllerState <= IDLE;
               command         <= COMMAND_NONE;
            elsif (joypad.PadPortDS = '1') then
               -- only port 1 can be a DualShock
               if (dsAnalogLock = '0') then
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
               dsRumbleConfig <= (others => '0');
            end if;

            if (actionNext = '1' and transmitting = '1') then
               if (selected = '1' and joypad.PadPortEnable = '1') then
                  if (isActive = '0' and slotIdle = '1') then
                     if (controllerState = IDLE and transmitValue = x"01") then
                        controllerState <= READY;
                        isActive        <= '1';
                        ack             <= '1';
                        digitalPadSave  <= joypad.PadPortDigital;
                        dsSave          <= joypad.PadPortDS;
                        receiveValid    <= '1';
                        receiveBuffer   <= x"FF";
                        if (portNr = 0) then
                           dsConfigModeSave <= dsConfigMode;
                           dsAnalogModeSave <= dsAnalogMode;
                        else
                           dsConfigModeSave <= '0';
                           dsAnalogModeSave <= '0';
                        end if;
                     end if;
                  elsif (isActive = '1') then
                     case (controllerState) is
                        when IDLE =>
                           if (transmitValue = x"01") then
                              command         <= COMMAND_NONE;
                              controllerState <= READY;
                              isActive        <= '1';
                              ack             <= '1';
                              digitalPadSave  <= joypad.PadPortDigital;
                              dsSave          <= joypad.PadPortDS;
                              receiveValid    <= '1';
                              receiveBuffer   <= x"FF";
                              if (portNr = 0) then
                                 dsConfigModeSave <= dsConfigMode;
                                 dsAnalogModeSave <= dsAnalogMode;
                              else
                                 dsConfigModeSave <= '0';
                                 dsAnalogModeSave <= '0';
                              end if;
                           end if;

                        when READY =>
                           if (transmitValue = x"42") then
                              command <= COMMAND_READ_INPUTS;
                              if (dsSave = '1' and dsConfigModeSave = '1') then
                                 receiveBuffer   <= x"F3";
                              elsif (dsAnalogModeSave = '1') then
                                 receiveBuffer   <= x"73";
                              else
                                 receiveBuffer   <= x"41";
                              end if;
                              controllerState <= ID;
                              ack             <= '1';
                              receiveValid    <= '1';
                           elsif (transmitValue = x"43") then
                              command <= COMMAND_CHANGE_CONFIG_MODE;
                              if (dsSave = '1') then
                                 if (dsConfigModeSave = '1') then
                                    receiveBuffer   <= x"F3";
                                 elsif (dsAnalogModeSave = '1') then
                                    receiveBuffer   <= x"73";
                                 else
                                    receiveBuffer   <= x"41";
                                 end if;
                                 controllerState <= ID;
                                 ack             <= '1';
                                 receiveValid    <= '1';
                              end if;
                           elsif (transmitValue = x"44") then
                              command <= COMMAND_SET_STATE;
                              if (dsSave = '1' and dsConfigModeSave = '1') then
                                 receiveBuffer   <= x"F3";
                                 controllerState <= ID;
                                 ack             <= '1';
                                 receiveValid    <= '1';
                                 dsRumbleConfig  <= (others => '1');
                              end if;
                           elsif (transmitValue = x"45") then
                              command <= COMMAND_GET_STATE;
                              if (dsSave = '1' and dsConfigModeSave = '1') then
                                 receiveBuffer   <= x"F3";
                                 controllerState <= ID;
                                 ack             <= '1';
                                 receiveValid    <= '1';
                              end if;
                           elsif (transmitValue = x"46") then
                              command <= COMMAND_46;
                              if (dsSave = '1' and dsConfigModeSave = '1') then
                                 receiveBuffer   <= x"F3";
                                 controllerState <= ID;
                                 ack             <= '1';
                                 receiveValid    <= '1';
                              end if;
                           elsif (transmitValue = x"47") then
                              command <= COMMAND_47;
                              if (dsSave = '1' and dsConfigModeSave = '1') then
                                 receiveBuffer   <= x"F3";
                                 controllerState <= ID;
                                 ack             <= '1';
                                 receiveValid    <= '1';
                              end if;
                           elsif (transmitValue = x"4C") then
                              command <= COMMAND_4C;
                              if (dsSave = '1' and dsConfigModeSave = '1') then
                                 receiveBuffer   <= x"F3";
                                 controllerState <= ID;
                                 ack             <= '1';
                                 receiveValid    <= '1';
                              end if;
                           elsif (transmitValue = x"4D") then
                              command <= COMMAND_RUMBLE;
                              if (dsSave = '1' and dsConfigModeSave = '1') then
                                 receiveBuffer   <= x"F3";
                                 controllerState <= ID;
                                 ack             <= '1';
                                 receiveValid    <= '1';
                              end if;
                           else
                              controllerState <= IDLE;
                              command <= COMMAND_UNKNOWN;
                           end if;

                        when ID =>
                           receiveBuffer   <= x"5A";
                           controllerState <= BUTTONLSB;

                           if (command = COMMAND_CHANGE_CONFIG_MODE and dsConfigModeSave = '1') then
                              controllerState <= CHANGECONFIG;
                           elsif (command = COMMAND_SET_STATE) then
                              controllerState <= SETSTATELSB;
                           elsif (command = COMMAND_46) then
                              controllerState <= COMMAND46;
                           elsif (command = COMMAND_47) then
                              controllerState <= COMMAND47;
                           elsif (command = COMMAND_4C) then
                              controllerState <= COMMAND4C;
                           elsif (command = COMMAND_RUMBLE) then
                              controllerState <= UPDATERUMBLE;
                              rumblecount     <= 0;
                           elsif (command = COMMAND_GET_STATE) then
                              rom_pointer     <= 6;
                              bytecount       <= 2;
                              controllerState <= ROMRESPONSE;
                              nextState       <= GETSTATE;
                           end if;

                           ack             <= '1';

                           if (command = COMMAND_UNKNOWN) then
                              controllerState <= IDLE;
                           else
                              ack             <= '1';
                           end if;

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

                           controllerState  <= BUTTONMSB;
                           ack              <= '1';
                           receiveValid     <= '1';

                           if (command = COMMAND_CHANGE_CONFIG_MODE and portNr = 0) then
                              if (transmitValue = x"01") then
                                 dsConfigMode <= '1';
                              elsif (transmitValue = x"00") then
                                 dsConfigMode <= '0';
                              end if;
                           end if;

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
                           if (dsAnalogModeSave = '1' or dsConfigModeSave = '1') then
                              controllerState <= ANALOGRIGHTX;
                              ack <= '1';
                           else
                              controllerState <= IDLE;
                           end if;

                        when ANALOGRIGHTX =>
                           receiveBuffer   <= analogLarge;
                           receiveValid    <= '1';
                           controllerState <= ANALOGRIGHTY;
                           ack             <= '1';

                        when ANALOGRIGHTY =>
                           receiveBuffer   <= analogLarge;
                           receiveValid    <= '1';
                           controllerState <= ANALOGLEFTX;
                           ack             <= '1';

                        when ANALOGLEFTX =>
                           receiveBuffer   <= analogLarge;
                           receiveValid    <= '1';
                           controllerState <= ANALOGLEFTY;
                           ack             <= '1';

                        when ANALOGLEFTY =>
                           receiveBuffer   <= analogLarge;
                           receiveValid    <= '1';
                           controllerState <= IDLE;

                        -- Dualshock (reached on port 1 only: dsSave and its
                        -- config mode are needed to get here)
                        when CHANGECONFIG =>
                           receiveValid    <= '1';
                           receiveBuffer   <= x"00";
                           ack             <= '1';
                           if (transmitValue = x"01") then
                              dsConfigMode <= '1';
                           elsif (transmitValue = x"00") then
                              dsConfigMode <= '0';
                           end if;
                           rom_pointer     <= 0;
                           bytecount       <= 5;
                           controllerState <= ROMRESPONSE;
                           nextState       <= IDLE;

                        when SETSTATELSB =>
                           if (transmitValue = x"00") then
                              dsAnalogMode <= '0';
                           elsif  (transmitValue = x"01") then
                              dsAnalogMode <= '1';
                           end if;
                           receiveValid    <= '1';
                           receiveBuffer   <= x"00";
                           ack             <= '1';
                           controllerState <= SETSTATEMSB;

                        when SETSTATEMSB =>
                           if (transmitValue = x"02") then
                              dsAnalogLock <= '0';
                           elsif (transmitValue = x"03") then
                              dsAnalogLock <= '1';
                           end if;
                           receiveValid    <= '1';
                           receiveBuffer   <= x"00";
                           ack             <= '1';
                           rom_pointer     <= 0;
                           bytecount       <= 4;
                           controllerState <= ROMRESPONSE;
                           nextState       <= IDLE;

                        when GETSTATE =>
                           if (dsAnalogModeSave = '1') then
                              receiveBuffer   <= x"01";
                           else
                              receiveBuffer   <= x"00";
                           end if;
                           receiveValid    <= '1';
                           ack             <= '1';
                           rom_pointer     <= 8;
                           bytecount       <= 3;
                           controllerState <= ROMRESPONSE;
                           nextState       <= IDLE;

                        when COMMAND46 =>
                           receiveValid    <= '1';
                           ack             <= '1';
                           if (transmitValue = x"00") then
                              rom_pointer <= 11;
                           elsif (transmitValue = x"01") then
                              rom_pointer <= 16;
                           else
                              rom_pointer <= 0;
                           end if;
                           bytecount       <= 5;
                           controllerState <= ROMRESPONSE;
                           nextState       <= IDLE;

                        when COMMAND47 =>
                           receiveValid    <= '1';
                           receiveBuffer   <= x"00";
                           ack             <= '1';
                           if (transmitValue = x"00") then
                              rom_pointer <= 21;
                           else
                              rom_pointer <= 0;
                           end if;
                           bytecount       <= 5;
                           controllerState <= ROMRESPONSE;
                           nextState       <= IDLE;

                        when COMMAND4C =>
                           receiveValid    <= '1';
                           receiveBuffer   <= x"00";
                           ack             <= '1';
                           if (transmitValue = x"00") then
                              rom_pointer <= 26;
                           elsif (transmitValue = x"01") then
                              rom_pointer <= 31;
                           else
                              rom_pointer <= 0;
                           end if;
                           bytecount       <= 5;
                           controllerState <= ROMRESPONSE;
                           nextState       <= IDLE;

                        when UPDATERUMBLE =>
                           receiveValid    <= '1';
                           receiveBuffer   <= dsRumbleConfig(((8 * rumblecount) + 7) downto (8 * rumblecount));
                           dsRumbleConfig(((8 * rumblecount) + 7) downto (8 * rumblecount)) <= transmitValue;

                           if (rumblecount < 5) then
                              rumblecount <= rumblecount + 1;
                              ack         <= '1';
                           else
                              controllerState <= IDLE;
                           end if;

                        when ROMRESPONSE =>
                           if (bytecount > 0) then
                              receiveBuffer   <= response(rom_pointer);
                              receiveValid    <= '1';
                              bytecount <= bytecount - 1;
                              rom_pointer <= rom_pointer + 1;
                              ack <= '1';
                           else
                              nextState <= IDLE; -- we shouldn't normally get here
                           end if;

                           if (bytecount = 1) then -- last byte, prepare next state
                              nextState <= IDLE;
                              controllerState <= nextState;
                              if (nextState = IDLE) then
                                 ack <= '0';
                              end if;
                           end if;

                     end case;
                  end if;
               end if; -- joy select

            end if; -- transmit

         end if; -- ce
      end if; -- clock
   end process;

end architecture;
