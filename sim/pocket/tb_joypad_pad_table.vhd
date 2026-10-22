-- SPDX-License-Identifier: GPL-3.0-or-later
-- Lockstep check: rtl/pocket/joypad_pad_table.vhd against
-- rtl/pocket/joypad_pad_dualshock.vhd (renamed joypad_pad_lean by the run
-- script), both with RUMBLE_ECHO off as the Pocket builds them, under the
-- SIO's timing: transmitValue, selected and reset change when a byte starts
-- and actionNext comes three or more clocks later (joypad.vhd: JOY_BAUD*8);
-- buttons, sticks, the DualShock/digital checkbox, ToggleDS, slotIdle,
-- transmitting and ce change on any clock. Pad 1 DualShock or digital at
-- random, pad 2 disabled or (PAD2=1) digital. Random command bytes weighted
-- to 01/42/DualShock commands and their 00..03 arguments, so the config,
-- 0x44..0x4D replies and the L3+R3 combo are reached. Every output is
-- compared on every clock.
library IEEE;
use IEEE.std_logic_1164.all;
use IEEE.numeric_std.all;
use IEEE.math_real.all;

use work.pJoypad.all;

entity tb_joypad_pad_table is
   generic (PAD2 : integer := 0; CYCLES : integer := 400000; SEED : integer := 1;
            STICK_DPAD : boolean := true; ANALOG_LEVEL : boolean := false);
end entity;

architecture sim of tb_joypad_pad_table is
   signal clk : std_logic := '0';
   signal ce, reset, selected, actionNext, transmitting, slotIdle : std_logic := '0';
   signal transmitValue : std_logic_vector(7 downto 0) := x"00";
   signal portNr : integer range 0 to 1 := 0;
   signal j1, j2, j0 : joypad_t;
   signal zero9 : signed(8 downto 0) := (others => '0');
   signal zu8 : unsigned(7 downto 0) := (others => '0');
   signal zu9 : unsigned(8 downto 0) := (others => '0');
   signal ss0 : std_logic_vector(31 downto 0) := (others => '0');

   type outs_t is record
      r1, r2, r3, r4 : std_logic_vector(15 downto 0);
      padMode, jirq  : std_logic_vector(1 downto 0);
      active, rvalid, ack : std_logic;
      rbuf : std_logic_vector(7 downto 0);
      ss : std_logic_vector(31 downto 0);
   end record;
   signal a, b : outs_t;
   signal done : boolean := false;
begin
   clk <= not clk after 5 ns when not done;

   dut : entity work.joypad_pad
      generic map (STICK_DPAD => STICK_DPAD, RUMBLE_ECHO => false, ANALOG_LEVEL => ANALOG_LEVEL)
      port map (clk1x => clk, ce => ce, reset => reset, DSAltSwitchMode => '0', isMultitap => '0',
         multitapDigital => '0', multitapAnalog => '0', joypad1 => j1, joypad2 => j2, joypad3 => j0, joypad4 => j0,
         joypad1_rumble => a.r1, joypad2_rumble => a.r2, joypad3_rumble => a.r3, joypad4_rumble => a.r4,
         neGconRumble => '0', padMode => a.padMode, portNr => portNr, isPal => '0', selected => selected,
         actionNext => actionNext, transmitting => transmitting, transmitValue => transmitValue,
         isActive => a.active, slotIdle => slotIdle, receiveValid => a.rvalid, receiveBuffer => a.rbuf, ack => a.ack,
         MouseEvent => '0', MouseLeft => '0', MouseRight => '0', MouseX => zero9, MouseY => zero9,
         GunX => zu8, GunY_scanlines => zu9, GunAimOffscreen => '0', JustifierIrqEnable => a.jirq,
         ss_in => ss0, ss_out => a.ss);

   ref : entity work.joypad_pad_lean
      generic map (STICK_DPAD => STICK_DPAD, RUMBLE_ECHO => false, ANALOG_LEVEL => ANALOG_LEVEL)
      port map (clk1x => clk, ce => ce, reset => reset, DSAltSwitchMode => '0', isMultitap => '0',
         multitapDigital => '0', multitapAnalog => '0', joypad1 => j1, joypad2 => j2, joypad3 => j0, joypad4 => j0,
         joypad1_rumble => b.r1, joypad2_rumble => b.r2, joypad3_rumble => b.r3, joypad4_rumble => b.r4,
         neGconRumble => '0', padMode => b.padMode, portNr => portNr, isPal => '0', selected => selected,
         actionNext => actionNext, transmitting => transmitting, transmitValue => transmitValue,
         isActive => b.active, slotIdle => slotIdle, receiveValid => b.rvalid, receiveBuffer => b.rbuf, ack => b.ack,
         MouseEvent => '0', MouseLeft => '0', MouseRight => '0', MouseX => zero9, MouseY => zero9,
         GunX => zu8, GunY_scanlines => zu9, GunAimOffscreen => '0', JustifierIrqEnable => b.jirq,
         ss_in => ss0, ss_out => b.ss);

   stim : process
      variable s1, s2 : positive := 1;
      variable r : real;
      variable errs, valids, acks : natural := 0;
      variable gap : natural := 0;
      variable ds_now, toggle : std_logic := '1';
      variable ds2_now, toggle2 : std_logic := '1';   -- PAD2=2: pad 2 is a DualShock / digital pad too
      variable f3s, s73s, longs, ffs : natural := 0;
      impure function rnd return real is begin uniform(s1, s2, r); return r; end function;
      impure function bit_r(p : real) return std_logic is begin if rnd < p then return '1'; else return '0'; end if; end function;
      impure function byte_r return std_logic_vector is
         variable x : real;
      begin
         x := rnd;
         if    x < 0.22 then return x"01";
         elsif x < 0.40 then return x"42";
         elsif x < 0.50 then return x"43";
         elsif x < 0.55 then return x"44";
         elsif x < 0.58 then return x"45";
         elsif x < 0.61 then return x"46";
         elsif x < 0.64 then return x"47";
         elsif x < 0.67 then return x"4C";
         elsif x < 0.70 then return x"4D";
         elsif x < 0.82 then return x"00";
         elsif x < 0.86 then return x"02";
         elsif x < 0.90 then return x"03";
         else return std_logic_vector(to_unsigned(integer(floor(rnd*256.0)) mod 256, 8));
         end if;
      end function;
      procedure rand_pad(signal j : out joypad_t; en : std_logic; ds : std_logic := '0'; tg : std_logic := '0') is
         variable p : joypad_t;
      begin
         p.PadPortEnable := en; p.PadPortDigital := en and not ds; p.PadPortAnalog := '0'; p.PadPortMouse := '0';
         p.PadPortGunCon := '0'; p.PadPortNeGcon := '0'; p.PadPortJustif := '0'; p.PadPortDS := ds;
         p.PadPortStick := '0'; p.PadPortPopn := '0'; p.WheelMap := '0'; p.ToggleDS := tg;
         p.KeyTriangle := bit_r(0.3); p.KeyCircle := bit_r(0.3); p.KeyCross := bit_r(0.3); p.KeySquare := bit_r(0.3);
         p.KeySelect := bit_r(0.2); p.KeyStart := bit_r(0.2); p.KeyRight := bit_r(0.3); p.KeyLeft := bit_r(0.3);
         p.KeyUp := bit_r(0.3); p.KeyDown := bit_r(0.3); p.KeyR1 := bit_r(0.3); p.KeyR2 := bit_r(0.3);
         p.KeyR3 := bit_r(0.4); p.KeyL1 := bit_r(0.3); p.KeyL2 := bit_r(0.3); p.KeyL3 := bit_r(0.4);
         p.Analog1X := to_signed(integer(floor(rnd*256.0)) - 128, 8);
         p.Analog1Y := to_signed(integer(floor(rnd*256.0)) - 128, 8);
         p.Analog2X := to_signed(integer(floor(rnd*256.0)) - 128, 8);
         p.Analog2Y := to_signed(integer(floor(rnd*256.0)) - 128, 8);
         j <= p;
      end procedure;
   begin
      s1 := SEED; s2 := SEED + 7;
      rand_pad(j0, '0');
      rand_pad(j1, '1', ds_now, toggle);
      rand_pad(j2, '0');
      reset <= '1'; ce <= '1';
      wait until rising_edge(clk); wait until rising_edge(clk);
      reset <= '0';
      for n in 0 to CYCLES - 1 loop
         -- drive new inputs just after the edge, compare just before the next
         wait for 1 ns;
         ce <= bit_r(0.9);
         actionNext <= '0';
         reset <= '0';
         if gap = 0 then
            -- a byte starts: the SIO's transmitValue (and, between bytes, a
            -- new selection, port or reset), then actionNext 2..12 clocks later
            transmitValue <= byte_r;
            if rnd < 0.03 then
               selected <= bit_r(0.7);
               -- the SIO switches ports only between selections
               if selected = '0' and rnd < 0.3 then portNr <= integer(floor(rnd*2.0)) mod 2; end if;
            elsif selected = '0' and rnd < 0.3 then
               selected <= '1';
            end if;
            if rnd < 0.0005 then reset <= '1'; end if;
            gap := 3 + integer(floor(rnd*10.0));
         else
            gap := gap - 1;
            if gap = 0 then actionNext <= '1'; end if;
         end if;
         transmitting <= bit_r(0.9);
         slotIdle <= bit_r(0.95);
         if rnd < 0.0005 then ds_now := not ds_now; end if;
         if PAD2 = 2 and rnd < 0.0005 then ds2_now := not ds2_now; end if;
         if rnd < 0.001 then toggle := not toggle; end if;
         if PAD2 = 2 and rnd < 0.001 then toggle2 := not toggle2; end if;
         if rnd < 0.01 then rand_pad(j1, '1', ds_now, toggle); end if;
         if rnd < 0.01 then
            if PAD2 = 2 then rand_pad(j2, '1', ds2_now, toggle2); elsif PAD2 /= 0 then rand_pad(j2, '1'); else rand_pad(j2, '0'); end if;
         end if;
         wait until rising_edge(clk);
         wait for 0 ns; wait for 0 ns;
         if a.rvalid /= b.rvalid or a.rbuf /= b.rbuf or a.ack /= b.ack or a.active /= b.active or
            a.padMode /= b.padMode or a.jirq /= b.jirq or a.ss /= b.ss or a.r1 /= b.r1 then
            errs := errs + 1;
            if errs <= 10 then
               report "mismatch at cycle " & integer'image(n) &
                  " rv " & std_logic'image(a.rvalid) & "/" & std_logic'image(b.rvalid) &
                  " rb " & integer'image(to_integer(unsigned(a.rbuf))) & "/" & integer'image(to_integer(unsigned(b.rbuf))) &
                  " ack " & std_logic'image(a.ack) & "/" & std_logic'image(b.ack) &
                  " act " & std_logic'image(a.active) & "/" & std_logic'image(b.active) &
                  " pm " & integer'image(to_integer(unsigned(a.padMode))) & "/" & integer'image(to_integer(unsigned(b.padMode))) severity warning;
            end if;
         end if;
         if b.rvalid = '1' then valids := valids + 1; end if;
         if b.ack = '1' then acks := acks + 1; end if;
         if b.rvalid = '1' and b.rbuf = x"F3" then f3s := f3s + 1; end if;
         if b.rvalid = '1' and b.rbuf = x"73" then s73s := s73s + 1; end if;
         if b.rvalid = '1' and (b.rbuf = x"0A" or b.rbuf = x"14" or b.rbuf = x"04" or b.rbuf = x"07") then longs := longs + 1; end if;
         if b.rvalid = '1' and b.rbuf = x"FF" and b.ack = '1' and a.active = '1' then ffs := ffs + 1; end if;
      end loop;
      report "TABLE PAD2=" & integer'image(PAD2) & " cycles=" & integer'image(CYCLES) & " receiveValid=" & integer'image(valids) &
         " acks=" & integer'image(acks) & " config=" & integer'image(f3s) & " analog=" & integer'image(s73s) &
         " sequences=" & integer'image(longs) & " ff=" & integer'image(ffs) & " errs=" & integer'image(errs);
      assert errs = 0 report "FAIL" severity failure;
      assert valids > 1000 and f3s > 20 and s73s > 20 and longs > 5 report "too little coverage" severity failure;
      done <= true;
      wait;
   end process;
end architecture;
