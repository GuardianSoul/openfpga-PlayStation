-- SPDX-License-Identifier: GPL-2.0-only
-- rtl/pocket/joypad_pad_dualshock.vhd ANALOG_LEVEL: the ToggleDS level
-- (Controller Mode setting) switches the DualShock between digital (ID 0x41)
-- and analog (0x73) at run time, the L3+R3 combos still work, and a mode
-- the game locked (0x44) holds until unlocked. Without ANALOG_LEVEL the
-- level does nothing. Last, Parasite Eve (USA)'s sequence (43 01, 44 01 00,
-- 43 00) with the setting on Digital: a DualShock goes analog (and the level
-- toggle Analog -> Digital brings it back); with DIGITAL_HOLD it stays
-- digital.
library IEEE;
use IEEE.std_logic_1164.all;
use IEEE.numeric_std.all;

use work.pJoypad.all;

entity tb_joypad_analog_level is
   generic (ANALOG_LEVEL : boolean := true; DIGITAL_HOLD : boolean := false);
end entity;

architecture sim of tb_joypad_analog_level is
   signal clk : std_logic := '0';
   signal ce, reset, selected, actionNext, transmitting : std_logic := '0';
   signal transmitValue : std_logic_vector(7 downto 0) := x"00";
   signal joypad1, j0 : joypad_t;
   signal zero9 : signed(8 downto 0) := (others => '0');
   signal zu8 : unsigned(7 downto 0) := (others => '0');
   signal zu9 : unsigned(8 downto 0) := (others => '0');
   signal ss0 : std_logic_vector(31 downto 0) := (others => '0');
   signal r1, r2, r3, r4 : std_logic_vector(15 downto 0);
   signal padMode, jirq : std_logic_vector(1 downto 0);
   signal active, rvalid, ack : std_logic;
   signal rbuf : std_logic_vector(7 downto 0);
   signal ss : std_logic_vector(31 downto 0);
   signal level, l3r3up, l3r3down : std_logic := '0';
   signal done : boolean := false;
   type bytes_t is array (natural range <>) of std_logic_vector(7 downto 0);
begin
   clk <= not clk after 5 ns when not done;

   j0.PadPortEnable <= '0'; j0.PadPortDigital <= '0'; j0.PadPortAnalog <= '0'; j0.PadPortMouse <= '0';
   j0.PadPortGunCon <= '0'; j0.PadPortNeGcon <= '0'; j0.PadPortJustif <= '0'; j0.PadPortDS <= '0';
   j0.PadPortStick <= '0'; j0.PadPortPopn <= '0'; j0.WheelMap <= '0'; j0.ToggleDS <= '0';
   j0.KeyTriangle <= '0'; j0.KeyCircle <= '0'; j0.KeyCross <= '0'; j0.KeySquare <= '0'; j0.KeySelect <= '0';
   j0.KeyStart <= '0'; j0.KeyRight <= '0'; j0.KeyLeft <= '0'; j0.KeyUp <= '0'; j0.KeyDown <= '0';
   j0.KeyR1 <= '0'; j0.KeyR2 <= '0'; j0.KeyR3 <= '0'; j0.KeyL1 <= '0'; j0.KeyL2 <= '0'; j0.KeyL3 <= '0';
   j0.Analog1X <= (others => '0'); j0.Analog1Y <= (others => '0'); j0.Analog2X <= (others => '0'); j0.Analog2Y <= (others => '0');

   process (j0, level, l3r3up, l3r3down) begin
      joypad1 <= j0;
      joypad1.PadPortEnable <= '1';
      joypad1.PadPortDS <= '1';
      joypad1.ToggleDS <= level;
      joypad1.KeyL3 <= l3r3up or l3r3down; joypad1.KeyR3 <= l3r3up or l3r3down;
      joypad1.KeyUp <= l3r3up; joypad1.KeyDown <= l3r3down;
   end process;

   dut : entity work.joypad_pad
      generic map (ANALOG_LEVEL => ANALOG_LEVEL, DIGITAL_HOLD => DIGITAL_HOLD)
      port map (clk1x => clk, ce => ce, reset => reset, DSAltSwitchMode => '0', isMultitap => '0',
         multitapDigital => '0', multitapAnalog => '0', joypad1 => joypad1, joypad2 => j0, joypad3 => j0, joypad4 => j0,
         joypad1_rumble => r1, joypad2_rumble => r2, joypad3_rumble => r3, joypad4_rumble => r4,
         neGconRumble => '0', padMode => padMode, portNr => 0, isPal => '0', selected => selected,
         actionNext => actionNext, transmitting => transmitting, transmitValue => transmitValue,
         isActive => active, slotIdle => '1', receiveValid => rvalid, receiveBuffer => rbuf, ack => ack,
         MouseEvent => '0', MouseLeft => '0', MouseRight => '0', MouseX => zero9, MouseY => zero9,
         GunX => zu8, GunY_scanlines => zu9, GunAimOffscreen => '0', JustifierIrqEnable => jirq,
         ss_in => ss0, ss_out => ss);

   stim : process
      variable errs : natural := 0;
      variable id : std_logic_vector(7 downto 0);
      procedure transfer(tx : bytes_t; variable byte1 : out std_logic_vector(7 downto 0)) is
      begin
         for i in tx'range loop
            selected <= '1'; transmitValue <= tx(i); actionNext <= '1'; transmitting <= '1';
            wait until rising_edge(clk); wait for 1 ns;
            actionNext <= '0';
            for t in 0 to 30 loop
               if rvalid = '1' and i = 1 then byte1 := rbuf; end if;
               wait until rising_edge(clk); wait for 1 ns;
            end loop;
         end loop;
         selected <= '0'; transmitting <= '0';
         for t in 0 to 10 loop wait until rising_edge(clk); end loop; wait for 1 ns;
      end procedure;
      -- The pad applies mode switches while selected, so a change shows from
      -- the poll after the one that sees it (one frame on a console).
      procedure settle is
         variable b : std_logic_vector(7 downto 0);
      begin
         for t in 0 to 10 loop wait until rising_edge(clk); end loop; wait for 1 ns;
         transfer((x"01", x"42", x"00", x"00", x"00"), b);
      end procedure;
      procedure check(analog : boolean; what : string) is
         variable want : std_logic_vector(7 downto 0);
      begin
         transfer((x"01", x"42", x"00", x"00", x"00"), id);
         if analog then want := x"73"; else want := x"41"; end if;
         if id /= want then
            errs := errs + 1; report "FAIL " & what & ": ID " & to_hstring(id) severity warning;
         end if;
      end procedure;
      variable dummy : std_logic_vector(7 downto 0);
   begin
      ce <= '1'; reset <= '1';
      wait until rising_edge(clk); wait until rising_edge(clk); wait for 1 ns; reset <= '0';
      settle;
      check(false, "digital at start");
      level <= '1'; settle;
      check(ANALOG_LEVEL, "level analog");
      level <= '0'; settle;
      check(false, "level digital");
      l3r3up <= '1'; settle; l3r3up <= '0';
      check(true, "L3+R3+Up still switches");
      l3r3down <= '1'; settle; l3r3down <= '0';
      check(false, "L3+R3+Down still switches");
      -- the game locks digital mode: 43 enter, 44 mode 0 lock 3, 43 leave
      transfer((x"01", x"43", x"00", x"01", x"00"), dummy);
      transfer((x"01", x"44", x"00", x"00", x"03", x"00", x"00", x"00", x"00"), dummy);
      transfer((x"01", x"43", x"00", x"00", x"00", x"00", x"00", x"00", x"00"), dummy);
      level <= '1'; settle;
      check(false, "locked mode holds");
      -- the game unlocks (44 mode 0 lock 2): the setting applies
      transfer((x"01", x"43", x"00", x"01", x"00"), dummy);
      transfer((x"01", x"44", x"00", x"00", x"02", x"00", x"00", x"00", x"00"), dummy);
      transfer((x"01", x"43", x"00", x"00", x"00", x"00", x"00", x"00", x"00"), dummy);
      settle;
      check(ANALOG_LEVEL, "after unlock");
      -- reset keeps the chosen level: the pad comes back in that mode
      reset <= '1'; wait until rising_edge(clk); wait for 1 ns; reset <= '0'; settle;
      check(ANALOG_LEVEL, "after reset with level analog");
      -- Parasite Eve (USA): on the Squaresoft logo, 43 01, 44 01 00 (analog, no lock), 43 00
      level <= '0'; settle;
      check(false, "digital before the game's request");
      transfer((x"01", x"43", x"00", x"01", x"00"), dummy);
      transfer((x"01", x"44", x"00", x"01", x"00", x"00", x"00", x"00", x"00"), dummy);
      transfer((x"01", x"43", x"00", x"00", x"00", x"00", x"00", x"00", x"00"), dummy);
      settle;
      check(not (DIGITAL_HOLD and ANALOG_LEVEL), "the game's 44 01 00 with the setting Digital");
      -- the user's workaround: Analog, then Digital
      level <= '1'; settle; level <= '0'; settle;
      check(not ANALOG_LEVEL, "after Analog -> Digital");
      if errs = 0 then report "PASS analog level ANALOG_LEVEL=" & boolean'image(ANALOG_LEVEL)
         & " DIGITAL_HOLD=" & boolean'image(DIGITAL_HOLD); end if;
      done <= true;
      wait;
   end process;
end architecture;
