-- SPDX-License-Identifier: GPL-2.0-only
-- rtl/pocket/joypad_pad_dualshock.vhd STICK_DPAD: a DualShock in digital mode
-- presses the D-pad from the left stick (DuckStation's default), in analog
-- mode it does not; without STICK_DPAD the stick never presses the D-pad.
library IEEE;
use IEEE.std_logic_1164.all;
use IEEE.numeric_std.all;

use work.pJoypad.all;

entity tb_joypad_stick_dpad is
   generic (STICK_DPAD : boolean := true);
end entity;

architecture sim of tb_joypad_stick_dpad is
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
   signal stickx : signed(7 downto 0) := (others => '0');
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

   process (j0, stickx) begin
      joypad1 <= j0;
      joypad1.PadPortEnable <= '1';
      joypad1.PadPortDS <= '1';
      joypad1.Analog1X <= stickx;
   end process;

   dut : entity work.joypad_pad
      generic map (STICK_DPAD => STICK_DPAD)
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
      variable lsb : std_logic_vector(7 downto 0);
      procedure transfer(tx : bytes_t; variable byte3 : out std_logic_vector(7 downto 0)) is
      begin
         for i in tx'range loop
            selected <= '1'; transmitValue <= tx(i); actionNext <= '1'; transmitting <= '1';
            wait until rising_edge(clk); wait for 1 ns;
            actionNext <= '0';
            for t in 0 to 30 loop
               if rvalid = '1' and i = 3 then byte3 := rbuf; end if;
               wait until rising_edge(clk); wait for 1 ns;
            end loop;
         end loop;
         selected <= '0'; transmitting <= '0';
         for t in 0 to 10 loop wait until rising_edge(clk); end loop; wait for 1 ns;
      end procedure;
      procedure check(want_left : std_logic; what : string) is
      begin
         transfer((x"01", x"42", x"00", x"00", x"00"), lsb);
         if lsb(7) /= not want_left then
            errs := errs + 1; report "FAIL " & what & ": buttons LSB " & to_hstring(lsb) severity warning;
         end if;
      end procedure;
      variable dummy : std_logic_vector(7 downto 0);
      variable expect_map : std_logic := '0';
   begin
      if STICK_DPAD then expect_map := '1'; end if;
      ce <= '1'; reset <= '1';
      wait until rising_edge(clk); wait until rising_edge(clk); wait for 1 ns; reset <= '0';
      for t in 0 to 10 loop wait until rising_edge(clk); end loop; wait for 1 ns;
      check('0', "digital mode, centred");
      stickx <= to_signed(-128, 8); wait for 1 ns;
      check(expect_map, "digital mode, stick left");
      stickx <= to_signed(-63, 8); wait for 1 ns;
      check('0', "digital mode, stick under threshold");
      stickx <= to_signed(-128, 8); wait for 1 ns;
      -- analog mode through config: 43 enter, 44 analog, 43 leave
      transfer((x"01", x"43", x"00", x"01", x"00"), dummy);
      transfer((x"01", x"44", x"00", x"01", x"02", x"00", x"00", x"00", x"00"), dummy);
      transfer((x"01", x"43", x"00", x"00", x"00", x"00", x"00", x"00", x"00"), dummy);
      check('0', "analog mode, stick left");
      if errs = 0 then report "PASS stick dpad STICK_DPAD=" & boolean'image(STICK_DPAD); end if;
      done <= true;
      wait;
   end process;
end architecture;
