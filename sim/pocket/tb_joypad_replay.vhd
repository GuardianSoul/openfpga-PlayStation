-- SPDX-License-Identifier: GPL-3.0-or-later
-- Replays a recorded pad byte stream (DuckStation PSX_PAD_TRACE: one line
-- per byte, "IN OUT ACK" in hex) into the pinned MiSTer joypad_pad as a
-- DualShock with no buttons and centred sticks, and compares every answer
-- byte and ACK with the recorded controller. A transaction is selected until
-- a byte without ACK. Used by scripts/run_joypad_replay.py.
library IEEE;
use IEEE.std_logic_1164.all;
use IEEE.numeric_std.all;
use std.textio.all;

use work.pJoypad.all;

entity tb_joypad_replay is
   generic (TRACE : string := "pad.txt"; DUALSHOCK : integer := 1; MAX_REPORT : integer := 20);
end entity;

architecture sim of tb_joypad_replay is
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
   signal done : boolean := false;
begin
   clk <= not clk after 5 ns when not done;

   j0.PadPortEnable <= '0'; j0.PadPortDigital <= '0'; j0.PadPortAnalog <= '0'; j0.PadPortMouse <= '0';
   j0.PadPortGunCon <= '0'; j0.PadPortNeGcon <= '0'; j0.PadPortJustif <= '0'; j0.PadPortDS <= '0';
   j0.PadPortStick <= '0'; j0.PadPortPopn <= '0'; j0.WheelMap <= '0'; j0.ToggleDS <= '0';
   j0.KeyTriangle <= '0'; j0.KeyCircle <= '0'; j0.KeyCross <= '0'; j0.KeySquare <= '0'; j0.KeySelect <= '0';
   j0.KeyStart <= '0'; j0.KeyRight <= '0'; j0.KeyLeft <= '0'; j0.KeyUp <= '0'; j0.KeyDown <= '0';
   j0.KeyR1 <= '0'; j0.KeyR2 <= '0'; j0.KeyR3 <= '0'; j0.KeyL1 <= '0'; j0.KeyL2 <= '0'; j0.KeyL3 <= '0';
   j0.Analog1X <= (others => '0'); j0.Analog1Y <= (others => '0'); j0.Analog2X <= (others => '0'); j0.Analog2Y <= (others => '0');

   process (j0) begin
      joypad1 <= j0;
      joypad1.PadPortEnable <= '1';
      if DUALSHOCK /= 0 then joypad1.PadPortDS <= '1'; else joypad1.PadPortDigital <= '1'; end if;
   end process;

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
      file f : text;
      variable l : line;
      variable vin, vout : std_logic_vector(7 downto 0);
      variable vack : std_logic_vector(3 downto 0);
      variable got : std_logic_vector(7 downto 0);
      variable got_ack : std_logic;
      variable bytes, errs, ack_errs, n_tx, bad_tx : natural := 0;
      variable tx_bad, first : boolean := true;
   begin
      file_open(f, TRACE, read_mode);
      ce <= '1'; reset <= '1';
      wait until rising_edge(clk); wait until rising_edge(clk); wait for 1 ns;
      reset <= '0';
      for t in 0 to 10 loop wait until rising_edge(clk); end loop; wait for 1 ns;
      tx_bad := false; first := true;
      while not endfile(f) loop
         readline(f, l);
         hread(l, vin); hread(l, vout); hread(l, vack);
         if first then n_tx := n_tx + 1; tx_bad := false; first := false; end if;
         selected <= '1';
         transmitValue <= vin; actionNext <= '1'; transmitting <= '1';
         wait until rising_edge(clk); wait for 1 ns;
         actionNext <= '0';
         got := x"FF"; got_ack := '0';   -- no answer: the line floats high
         for t in 0 to 60 loop
            if rvalid = '1' then got := rbuf; end if;
            if ack = '1' then got_ack := '1'; end if;
            wait until rising_edge(clk); wait for 1 ns;
         end loop;
         bytes := bytes + 1;
         if got /= vout then
            errs := errs + 1; tx_bad := true;
            if errs <= MAX_REPORT then
               report "byte " & integer'image(bytes) & " tx " & integer'image(n_tx) & " in " & to_hstring(vin) &
                  ": core " & to_hstring(got) & " recorded " & to_hstring(vout) severity warning;
            end if;
         end if;
         if got_ack /= vack(0) then
            ack_errs := ack_errs + 1; tx_bad := true;
            if ack_errs <= MAX_REPORT then
               report "byte " & integer'image(bytes) & " tx " & integer'image(n_tx) & " in " & to_hstring(vin) &
                  ": core ack " & std_logic'image(got_ack) & " recorded " & to_hstring(vack) severity warning;
            end if;
         end if;
         if vack(0) = '0' then
            if tx_bad then bad_tx := bad_tx + 1; end if;
            selected <= '0'; transmitting <= '0';
            for t in 0 to 20 loop wait until rising_edge(clk); end loop;
            wait for 1 ns;
            first := true;
         end if;
      end loop;
      report "REPLAY bytes=" & integer'image(bytes) & " transactions=" & integer'image(n_tx) &
         " byte_errs=" & integer'image(errs) & " ack_errs=" & integer'image(ack_errs) & " bad_tx=" & integer'image(bad_tx);
      done <= true;
      wait;
   end process;
end architecture;
