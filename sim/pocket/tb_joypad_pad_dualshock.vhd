-- SPDX-License-Identifier: GPL-3.0-or-later
-- Lockstep check: rtl/pocket/joypad_pad_dualshock.vhd against the pinned
-- PSX_MiSTer joypad_pad (renamed joypad_pad_ref by the run script) under the
-- Pocket tie-offs: pad 1 a DualShock or a digital pad (the menu checkbox,
-- toggled at random), pad 2 digital or disabled, no multitap, ss_in zero.
-- Random command bytes (weighted to 01/42/DualShock commands and their
-- 00..03 arguments), the L3+R3+Up/Down analog combo, random sticks, CE holds,
-- deselects, port switches while deselected, slot-busy and resets. Every
-- output except the rumble motors and ss_out (not produced) is compared on
-- every clock. Generic PAD2 enables port 2 as a second digital pad.
-- ECHO=1 builds the lean pad with RUMBLE_ECHO (exact everywhere); ECHO=0
-- (the Pocket build) may differ only in the six 0x4D config bytes, which
-- must then read 0xFF. actionNext never fires on two clocks in a row: the
-- SIO raises it once per byte, many clocks apart, and the lean pad reads its
-- reply table one clock behind.
library IEEE;
use IEEE.std_logic_1164.all;
use IEEE.numeric_std.all;
use IEEE.math_real.all;

use work.pJoypad.all;

entity tb_joypad_pad_dualshock is
   generic (PAD2 : integer := 0; CYCLES : integer := 400000; SEED : integer := 1; ECHO : integer := 1);
end entity;

architecture sim of tb_joypad_pad_dualshock is
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
      generic map (RUMBLE_ECHO => ECHO /= 0)
      port map (clk1x => clk, ce => ce, reset => reset, DSAltSwitchMode => '0', isMultitap => '0',
         multitapDigital => '0', multitapAnalog => '0', joypad1 => j1, joypad2 => j2, joypad3 => j0, joypad4 => j0,
         joypad1_rumble => a.r1, joypad2_rumble => a.r2, joypad3_rumble => a.r3, joypad4_rumble => a.r4,
         neGconRumble => '0', padMode => a.padMode, portNr => portNr, isPal => '0', selected => selected,
         actionNext => actionNext, transmitting => transmitting, transmitValue => transmitValue,
         isActive => a.active, slotIdle => slotIdle, receiveValid => a.rvalid, receiveBuffer => a.rbuf, ack => a.ack,
         MouseEvent => '0', MouseLeft => '0', MouseRight => '0', MouseX => zero9, MouseY => zero9,
         GunX => zu8, GunY_scanlines => zu9, GunAimOffscreen => '0', JustifierIrqEnable => a.jirq,
         ss_in => ss0, ss_out => a.ss);

   ref : entity work.joypad_pad_ref
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
      variable sel_hold : natural := 0;
      variable sel_new, ds_now : std_logic := '1';
      variable f3s, s73s : natural := 0;
      variable rumble_left, rumble_ffs : natural := 0;
      variable rumble_byte : boolean;
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
      procedure rand_pad(signal j : out joypad_t; en : std_logic; ds : std_logic := '0') is
         variable p : joypad_t;
      begin
         p.PadPortEnable := en; p.PadPortDigital := en and not ds; p.PadPortAnalog := '0'; p.PadPortMouse := '0';
         p.PadPortGunCon := '0'; p.PadPortNeGcon := '0'; p.PadPortJustif := '0'; p.PadPortDS := ds;
         p.PadPortStick := '0'; p.PadPortPopn := '0'; p.WheelMap := '0'; p.ToggleDS := '0';
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
      rand_pad(j1, '1', ds_now);
      rand_pad(j2, '0');
      reset <= '1'; ce <= '1';
      wait until rising_edge(clk); wait until rising_edge(clk);
      reset <= '0';
      for n in 0 to CYCLES - 1 loop
         -- drive new inputs just after the edge, compare just before the next
         wait for 1 ns;
         ce <= bit_r(0.9);
         reset <= bit_r(0.00005);
         if sel_hold = 0 then
            sel_new := bit_r(0.85);
            selected <= sel_new;
            sel_hold := integer(floor(rnd*200.0));
            -- the SIO switches ports only between selections
            if sel_new = '0' and rnd < 0.3 then portNr <= integer(floor(rnd*2.0)) mod 2; end if;
         else
            sel_hold := sel_hold - 1;
         end if;
         if actionNext = '1' then actionNext <= '0'; else actionNext <= bit_r(0.15); end if;
         transmitting <= bit_r(0.8);
         slotIdle <= bit_r(0.95);
         transmitValue <= byte_r;
         if rnd < 0.0005 then ds_now := not ds_now; end if;
         if rnd < 0.01 then rand_pad(j1, '1', ds_now); end if;
         if rnd < 0.01 then
            if PAD2 /= 0 then rand_pad(j2, '1'); else rand_pad(j2, '0'); end if;
         end if;
         wait until rising_edge(clk);
         wait for 0 ns; wait for 0 ns;
         -- the reference's six 0x4D config bytes (after its F3 and 5A)
         rumble_byte := false;
         if reset = '1' or selected = '0' then
            rumble_left := 0;
         elsif b.rvalid = '1' then
            if b.rbuf = x"F3" and transmitValue = x"4D" then
               rumble_left := 7;
            elsif rumble_left > 0 then
               rumble_byte := rumble_left <= 6;
               rumble_left := rumble_left - 1;
            end if;
         end if;
         if ECHO = 0 and rumble_byte and a.rvalid = '1' and a.rbuf = x"FF" and b.rbuf /= x"FF" then
            rumble_ffs := rumble_ffs + 1;
         elsif a.rvalid /= b.rvalid or a.rbuf /= b.rbuf or a.ack /= b.ack or a.active /= b.active or
            a.padMode /= b.padMode or a.jirq /= b.jirq then
            errs := errs + 1;
            if errs <= 10 then
               report "mismatch at cycle " & integer'image(n) &
                  " rv " & std_logic'image(a.rvalid) & "/" & std_logic'image(b.rvalid) &
                  " rb " & integer'image(to_integer(unsigned(a.rbuf))) & "/" & integer'image(to_integer(unsigned(b.rbuf))) &
                  " ack " & std_logic'image(a.ack) & "/" & std_logic'image(b.ack) &
                  " act " & std_logic'image(a.active) & "/" & std_logic'image(b.active) &
                  " ss " & integer'image(to_integer(unsigned(a.ss(15 downto 0)))) & "/" & integer'image(to_integer(unsigned(b.ss(15 downto 0)))) &
                  " r1 " & integer'image(to_integer(unsigned(a.r1))) & "/" & integer'image(to_integer(unsigned(b.r1))) &
                  " pm " & integer'image(to_integer(unsigned(a.padMode))) & "/" & integer'image(to_integer(unsigned(b.padMode))) severity warning;
            end if;
         end if;
         if b.rvalid = '1' then valids := valids + 1; end if;
         if b.ack = '1' then acks := acks + 1; end if;
         if b.rvalid = '1' and b.rbuf = x"F3" then f3s := f3s + 1; end if;
         if b.rvalid = '1' and b.rbuf = x"73" then s73s := s73s + 1; end if;
      end loop;
      report "PAD2=" & integer'image(PAD2) & " cycles=" & integer'image(CYCLES) & " receiveValid=" & integer'image(valids) &
         " acks=" & integer'image(acks) & " config=" & integer'image(f3s) & " analog=" & integer'image(s73s) & " rumble_ff=" & integer'image(rumble_ffs) & " errs=" & integer'image(errs);
      assert errs = 0 report "FAIL" severity failure;
      assert valids > 1000 and f3s > 20 and s73s > 20 report "too little coverage" severity failure;
      done <= true;
      wait;
   end process;
end architecture;
