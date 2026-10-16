-- SPDX-License-Identifier: GPL-3.0-or-later
-- prepare --dualshock: the pinned MiSTer joypad_pad driven through the PSX
-- wrapper's pad 1 assignments (inserted at @FIELDS@ by
-- scripts/run_joypad_dualshock.py from prepare_pocket_dualshock_pad.py).
-- Plays the DualShock sequence an analog-only game (Ape Escape) sends:
-- poll, enter config, set analog + lock, exit config, poll; checks the ID
-- bytes and that the four stick bytes are the APF stick bytes (with MiSTer's
-- +1/8 axis gain). With the
-- menu checkbox off the same sequence must keep answering as a digital pad.
library IEEE;
use IEEE.std_logic_1164.all;
use IEEE.numeric_std.all;

use work.pJoypad.all;

entity tb_joypad_dualshock is
   generic (DUALSHOCK : integer := 1; STICKS : std_logic_vector(31 downto 0) := x"20E017C4");
end entity;

architecture sim of tb_joypad_dualshock is
   signal clk : std_logic := '0';
   signal ce, reset, selected, actionNext, transmitting : std_logic := '0';
   signal transmitValue : std_logic_vector(7 downto 0) := x"00";
   signal joypad1, j0 : joypad_t;
   signal pad_dualshock : std_logic;
   signal pad_sticks : std_logic_vector(31 downto 0);
   signal joypad1_KeyCross, joypad1_KeyL1 : std_logic := '0';
   signal zero9 : signed(8 downto 0) := (others => '0');
   signal zu8 : unsigned(7 downto 0) := (others => '0');
   signal zu9 : unsigned(8 downto 0) := (others => '0');
   signal ss0 : std_logic_vector(31 downto 0) := (others => '0');
   signal r1, r2, r3, r4 : std_logic_vector(15 downto 0);
   signal padMode, jirq : std_logic_vector(1 downto 0);
   signal active, rvalid, ack : std_logic;
   signal rbuf : std_logic_vector(7 downto 0);
   signal ss : std_logic_vector(31 downto 0);
   signal done : boolean := false;
   type bytes_t is array (natural range <>) of std_logic_vector(7 downto 0);
begin
   clk <= not clk after 5 ns when not done;
   pad_dualshock <= '1' when DUALSHOCK /= 0 else '0';
   pad_sticks <= STICKS;

   j0.PadPortEnable <= '0'; j0.PadPortDigital <= '0'; j0.PadPortAnalog <= '0'; j0.PadPortMouse <= '0';
   j0.PadPortGunCon <= '0'; j0.PadPortNeGcon <= '0'; j0.PadPortJustif <= '0'; j0.PadPortDS <= '0';
   j0.PadPortStick <= '0'; j0.PadPortPopn <= '0'; j0.WheelMap <= '0'; j0.ToggleDS <= '0';
   j0.KeyTriangle <= '0'; j0.KeyCircle <= '0'; j0.KeyCross <= '0'; j0.KeySquare <= '0'; j0.KeySelect <= '0';
   j0.KeyStart <= '0'; j0.KeyRight <= '0'; j0.KeyLeft <= '0'; j0.KeyUp <= '0'; j0.KeyDown <= '0';
   j0.KeyR1 <= '0'; j0.KeyR2 <= '0'; j0.KeyR3 <= '0'; j0.KeyL1 <= '0'; j0.KeyL2 <= '0'; j0.KeyL3 <= '0';
   j0.Analog1X <= (others => '0'); j0.Analog1Y <= (others => '0'); j0.Analog2X <= (others => '0'); j0.Analog2Y <= (others => '0');

   -- The release wrapper's pad 1 lines (digital pad base), then the
   -- --dualshock replacements.
   joypad1.PadPortEnable <= '1';
   joypad1.PadPortAnalog <= '0';
   joypad1.PadPortMouse <= '0';
   joypad1.PadPortGunCon <= '0';
   joypad1.PadPortNeGcon <= '0';
   joypad1.PadPortJustif <= '0';
   joypad1.PadPortStick <= '0';
   joypad1.PadPortPopn <= '0';
   joypad1.WheelMap <= '0';
   joypad1.ToggleDS <= '0';
   joypad1.KeyTriangle <= '0'; joypad1.KeyCircle <= '0'; joypad1.KeyCross <= joypad1_KeyCross;
   joypad1.KeySquare <= '0'; joypad1.KeySelect <= '0'; joypad1.KeyStart <= '0'; joypad1.KeyRight <= '0';
   joypad1.KeyLeft <= '0'; joypad1.KeyUp <= '0'; joypad1.KeyDown <= '0'; joypad1.KeyR1 <= '0';
   joypad1.KeyR2 <= '0'; joypad1.KeyR3 <= '0'; joypad1.KeyL1 <= joypad1_KeyL1; joypad1.KeyL2 <= '0'; joypad1.KeyL3 <= '0';
--@FIELDS@

   dut : entity work.joypad_pad
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
      variable got : bytes_t(0 to 8);

      procedure transfer(tx : bytes_t; got : out bytes_t) is
         variable n : natural;
      begin
         selected <= '1';
         for i in tx'range loop
            transmitValue <= tx(i); actionNext <= '1'; transmitting <= '1';
            wait until rising_edge(clk); wait for 1 ns;
            actionNext <= '0';
            got(i - tx'low) := x"EE";   -- no answer
            for t in 0 to 200 loop
               if rvalid = '1' then got(i - tx'low) := rbuf; exit; end if;
               wait until rising_edge(clk); wait for 1 ns;
            end loop;
            -- a few idle clocks between bytes, like the SIO's bit clock
            for t in 0 to 20 loop wait until rising_edge(clk); end loop;
            wait for 1 ns;
         end loop;
         transmitting <= '0'; selected <= '0';
         for t in 0 to 40 loop wait until rising_edge(clk); end loop;
         wait for 1 ns;
      end procedure;

      procedure expect(what : string; got_byte, want : std_logic_vector(7 downto 0)) is
      begin
         if got_byte /= want then
            errs := errs + 1;
            report what & ": got " & integer'image(to_integer(unsigned(got_byte))) &
               " want " & integer'image(to_integer(unsigned(want))) severity warning;
         end if;
      end procedure;

      -- MiSTer's DualShock answers each axis with +1/8 gain, clamped
      -- (joypad_pad analogLarge): the expected byte for an APF byte.
      function boosted(b : std_logic_vector(7 downto 0)) return std_logic_vector is
         variable v : integer;
      begin
         v := to_integer(unsigned(b)) - 128;
         v := v + v / 8;
         if v > 127 then v := 127; elsif v < -128 then v := -128; end if;
         return std_logic_vector(to_unsigned(v + 128, 8));
      end function;

      constant POLL : bytes_t(0 to 8) := (x"01", x"42", x"00", x"00", x"00", x"00", x"00", x"00", x"00");
      constant ENTER : bytes_t(0 to 4) := (x"01", x"43", x"00", x"01", x"00");
      constant ANALOG : bytes_t(0 to 8) := (x"01", x"44", x"00", x"01", x"03", x"00", x"00", x"00", x"00");
      constant LEAVE : bytes_t(0 to 8) := (x"01", x"43", x"00", x"00", x"5A", x"5A", x"5A", x"5A", x"5A");
      variable s : std_logic_vector(31 downto 0);
      variable lsb : std_logic_vector(7 downto 0);
   begin
      s := STICKS;
      ce <= '1'; reset <= '1';
      wait until rising_edge(clk); wait until rising_edge(clk); wait for 1 ns;
      reset <= '0';
      for t in 0 to 10 loop wait until rising_edge(clk); end loop; wait for 1 ns;

      joypad1_KeyCross <= '1'; joypad1_KeyL1 <= '1';
      transfer(POLL(0 to 4), got(0 to 4));
      expect("first poll ID", got(1), x"41");
      expect("first poll 5A", got(2), x"5A");
      -- A digital pad (checkbox off) also reads the left stick as the d-pad
      -- (MiSTer, threshold 64 from centre); DualShock digital mode does not.
      lsb := x"FF";
      if DUALSHOCK = 0 then
         if to_integer(unsigned(s(7 downto 0))) - 128 <= -64 then lsb(7) := '0'; end if;
         if to_integer(unsigned(s(7 downto 0))) - 128 >= 64 then lsb(5) := '0'; end if;
         if to_integer(unsigned(s(15 downto 8))) - 128 <= -64 then lsb(4) := '0'; end if;
         if to_integer(unsigned(s(15 downto 8))) - 128 >= 64 then lsb(6) := '0'; end if;
      end if;
      expect("first poll buttons LSB", got(3), lsb);
      expect("first poll buttons MSB (Cross+L1)", got(4), x"BB");

      transfer(ENTER, got(0 to 4));
      transfer(ANALOG, got);
      transfer(LEAVE, got);
      transfer(POLL, got);
      if DUALSHOCK /= 0 then
         expect("analog poll ID", got(1), x"73");
         expect("analog poll 5A", got(2), x"5A");
         expect("analog poll buttons MSB", got(4), x"BB");
         expect("right stick X", got(5), boosted(s(23 downto 16)));
         expect("right stick Y", got(6), boosted(s(31 downto 24)));
         expect("left stick X", got(7), boosted(s(7 downto 0)));
         expect("left stick Y", got(8), boosted(s(15 downto 8)));
      else
         expect("digital poll ID", got(1), x"41");
         expect("digital poll buttons MSB", got(4), x"BB");
      end if;

      report "DUALSHOCK=" & integer'image(DUALSHOCK) & " errs=" & integer'image(errs);
      done <= true;
      wait;
   end process;
end architecture;
