-- SPDX-License-Identifier: GPL-2.0-only
-- Memory card 1 served directly from its memory window: the upstream joypad
-- (pad + joypad_mem) runs BIOS-style card commands through the SIO
-- registers while a 128 KiB window model, preloaded with a random card image,
-- answers joypad_mem's memory requests. No load/save copy engine is
-- involved. Every sector read must return the window's bytes, checksum and
-- end status; every sector write must land in the window and read back.
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
use std.env.all;
use std.textio.all;
use work.pJoypad.all;

entity tb_memcard_window is
    generic (SEED : natural := 1; SECTORS : natural := 24; DROP_WRITES : boolean := false; IMAGE_FILE : string := "");
end entity;
architecture test of tb_memcard_window is
    signal clk1x, clk2x : std_logic := '1';
    signal reset : std_logic := '1';
    signal toggle, sampled, clk2xIndex : std_logic := '0';
    signal pad1, padOff : joypad_t;
    signal bus_addr : unsigned(3 downto 0) := (others => '0');
    signal bus_dataWrite, bus_dataRead : std_logic_vector(31 downto 0) := (others => '0');
    signal bus_read, bus_write : std_logic := '0';
    signal bus_writeMask : std_logic_vector(3 downto 0) := (others => '0');
    signal mem1_request, mem1_WE, mem1_RD, mem2_request, mem2_WE, mem2_RD : std_logic;
    signal mem1_BURSTCNT, mem2_BURSTCNT, mem1_BE, mem2_BE : std_logic_vector(7 downto 0);
    signal mem1_ADDR, mem2_ADDR : std_logic_vector(19 downto 0);
    signal mem1_DIN, mem2_DIN : std_logic_vector(63 downto 0);
    signal mem1_ack : std_logic := '0';
    signal mem_DOUT : std_logic_vector(63 downto 0) := (others => '0');
    signal mem_DOUT_READY : std_logic := '0';
    type tcard is array (0 to 16383) of std_logic_vector(63 downto 0);
    shared variable card : tcard;
    signal memWrites, memReads : natural := 0;
    shared variable xcount : natural := 0;
    shared variable xlast : natural := 0;
begin
    clk1x <= not clk1x after 14762 ps;
    clk2x <= not clk2x after 7381 ps;
    process(clk1x) begin if rising_edge(clk1x) then toggle <= not toggle; end if; end process;
    process(clk2x) begin
        if rising_edge(clk2x) then
            sampled <= toggle; clk2xIndex <= '0';
            if sampled = toggle then clk2xIndex <= '1'; end if;
        end if;
    end process;

    process begin
        padOff <= (PadPortEnable => '0', PadPortDigital => '0', PadPortAnalog => '0', PadPortMouse => '0',
                   PadPortGunCon => '0', PadPortNeGcon => '0', PadPortJustif => '0', PadPortDS => '0',
                   PadPortStick => '0', PadPortPopn => '0', WheelMap => '0', ToggleDS => '0',
                   KeyTriangle => '0', KeyCircle => '0', KeyCross => '0', KeySquare => '0', KeySelect => '0',
                   KeyStart => '0', KeyRight => '0', KeyLeft => '0', KeyUp => '0', KeyDown => '0',
                   KeyR1 => '0', KeyR2 => '0', KeyR3 => '0', KeyL1 => '0', KeyL2 => '0', KeyL3 => '0',
                   Analog1X => (others => '0'), Analog1Y => (others => '0'),
                   Analog2X => (others => '0'), Analog2Y => (others => '0'));
        wait for 1 ns;
        pad1 <= padOff; pad1.PadPortEnable <= '1'; pad1.PadPortDigital <= '1';
        wait;
    end process;

    dut : entity work.joypad port map (clk1x => clk1x, clk2x => clk2x, clk2xIndex => clk2xIndex, ce => '1', reset => reset,
        isPal => '0', DSAltSwitchMode => '0', joypad1 => pad1, joypad2 => padOff, joypad3 => padOff, joypad4 => padOff,
        multitap => '0', multitapDigital => '0', multitapAnalog => '0', neGconRumble => '0',
        joypad1_rumble => open, joypad2_rumble => open, joypad3_rumble => open, joypad4_rumble => open, padMode => open,
        memcard1_available => '1', memcard2_available => '0', irqRequest => open,
        MouseEvent => '0', MouseLeft => '0', MouseRight => '0', MouseX => (others => '0'), MouseY => (others => '0'),
        Gun1X => (others => '0'), Gun2X => (others => '0'), Gun1Y_scanlines => (others => '0'), Gun2Y_scanlines => (others => '0'),
        Gun1AimOffscreen => '0', Gun2AimOffscreen => '0', JustifierIrqEnable => open,
        snacPort1_in => '0', snacPort2_in => '0', actionNextSnac => '0', receiveValidSnac => '0', ackSnac => '0', snacMC => '0',
        receiveBufferSnac => (others => '0'), transmitValueSnac => open, selectedPort1Snac => open, selectedPort2Snac => open,
        clk9Snac => open, beginTransferSnac => open,
        mem1_request => mem1_request, mem1_BURSTCNT => mem1_BURSTCNT, mem1_ADDR => mem1_ADDR, mem1_DIN => mem1_DIN,
        mem1_BE => mem1_BE, mem1_WE => mem1_WE, mem1_RD => mem1_RD, mem1_ack => mem1_ack,
        mem2_request => mem2_request, mem2_BURSTCNT => mem2_BURSTCNT, mem2_ADDR => mem2_ADDR, mem2_DIN => mem2_DIN,
        mem2_BE => mem2_BE, mem2_WE => mem2_WE, mem2_RD => mem2_RD, mem2_ack => '0',
        mem_DOUT => mem_DOUT, mem_DOUT_READY => mem_DOUT_READY,
        bus_addr => bus_addr, bus_dataWrite => bus_dataWrite, bus_read => bus_read, bus_write => bus_write,
        bus_writeMask => bus_writeMask, bus_dataRead => bus_dataRead,
        SS_reset => reset, SS_DataWrite => (others => '0'), SS_Adr => (others => '0'), SS_wren => '0', SS_rden => '0',
        SS_DataRead => open, SS_idle => open);

    -- Card window model: 64-bit words, ack after a short delay, read bursts
    -- streamed with DOUT_READY like the psx_top arbiter.
    process(clk2x)
        variable delay, left, word : natural := 0;
        variable busy : boolean := false;
        variable rearm : boolean := true;  -- a request is accepted only after the previous one dropped
    begin
        if rising_edge(clk2x) then
            mem1_ack <= '0'; mem_DOUT_READY <= '0';
            if mem1_request = '0' then rearm := true; end if;
            if not busy and rearm and mem1_request = '1' then
                rearm := false;
                busy := true; delay := 4; word := to_integer(unsigned(mem1_ADDR(19 downto 3)));
                left := to_integer(unsigned(mem1_BURSTCNT));
            elsif busy then
                if delay > 0 then
                    delay := delay - 1;
                    if delay = 0 then
                        mem1_ack <= '1';
                        if mem1_WE = '1' then
                            assert mem1_BE = x"FF" report "unexpected byte enables" severity failure;
                            if not DROP_WRITES then card(word) := mem1_DIN; end if; memWrites <= memWrites + 1;
                            busy := false;
                        end if;
                    end if;
                elsif left > 0 then
                    mem_DOUT <= card(word); mem_DOUT_READY <= '1'; memReads <= memReads + 1;
                    word := word + 1; left := left - 1;
                    if left = 0 then busy := false; end if;
                end if;
            end if;
        end if;
    end process;

    process
        variable state : unsigned(31 downto 0) := to_unsigned(SEED * 2654435 + 12345, 32);
        impure function rnd(n : natural) return natural is
        begin
            state := state xor shift_left(state, 13); state := state xor shift_right(state, 17); state := state xor shift_left(state, 5);
            return to_integer(state(30 downto 0)) mod n;
        end;
        procedure tick is begin wait until falling_edge(clk1x); end;
        procedure busw(a : natural; d : std_logic_vector(31 downto 0); m : std_logic_vector(3 downto 0)) is
        begin
            bus_addr <= to_unsigned(a, 4); bus_dataWrite <= d; bus_writeMask <= m; bus_write <= '1'; tick;
            bus_write <= '0'; tick;
        end;
        procedure busr(a : natural; d : out std_logic_vector(31 downto 0)) is
        begin
            -- bus_dataRead is valid for one clock after the read edge.
            bus_addr <= to_unsigned(a, 4); bus_read <= '1'; tick; bus_read <= '0'; d := bus_dataRead; tick;
        end;
        procedure exchange(tx : natural; rx : out natural) is
            variable st, d : std_logic_vector(31 downto 0);
            variable n : natural := 0;
        begin
            busw(0, x"000000" & std_logic_vector(to_unsigned(tx, 8)), "1111");
            loop
                busr(4, st); exit when st(1) = '1';
                n := n + 1; assert n < 20000 report "no response byte, JOY_STAT " & to_hstring(st) & " at exchange " & natural'image(xcount) & " tx " & natural'image(tx) & " previous rx " & natural'image(xlast) severity failure;
            end loop;
            busr(0, d); rx := to_integer(unsigned(d(7 downto 0))); xcount := xcount + 1; xlast := rx;
            for i in 1 to 300 loop tick; end loop;
            busw(8, x"10130000", "1100");   -- acknowledge the IRQ, keep TX enable + select
        end;
        procedure selectCard is begin
            busw(8, x"10030000", "1100"); for i in 1 to 50 loop tick; end loop;
        end;
        procedure deselect is begin
            busw(8, x"00000000", "1100"); for i in 1 to 2000 loop tick; end loop;
        end;
        function cardByte(addr : natural) return natural is
            variable w : std_logic_vector(63 downto 0);
        begin
            w := card(addr / 8);
            return to_integer(unsigned(w(8 * (addr mod 8) + 7 downto 8 * (addr mod 8))));
        end;
        type tdata is array (0 to 127) of natural;
        variable data : tdata;
        variable rx, chk, sector, reads, writes : natural := 0;
        variable w : std_logic_vector(63 downto 0);
        file image : text;
        variable l : line;
    begin
        if IMAGE_FILE'length = 0 then
            for i in 0 to 16383 loop
                for b in 0 to 7 loop w(8 * b + 7 downto 8 * b) := std_logic_vector(to_unsigned(rnd(256), 8)); end loop;
                card(i) := w;
            end loop;
        else
            -- Preload a given image (one 64-bit hex word per line), e.g. the blank card.
            file_open(image, IMAGE_FILE, read_mode);
            for i in 0 to 16383 loop readline(image, l); hread(l, w); card(i) := w; end loop;
            file_close(image);
        end if;
        for i in 1 to 20 loop tick; end loop;
        reset <= '0';
        for i in 1 to 100 loop tick; end loop;
        busw(8, x"00400000", "1100");   -- SIO reset
        busw(8, x"0000000D", "0011");   -- mode
        busw(12, x"00880000", "1100");  -- baud
        for n in 1 to SECTORS loop
            if n = 1 then sector := 0; elsif n = 2 then sector := 1023; else sector := rnd(1024); end if;
            if n mod 2 = 0 then
                -- Write a random sector, then check the window.
                for i in 0 to 127 loop data(i) := rnd(256); end loop;
                chk := (sector / 256) mod 256; chk := to_integer(to_unsigned(chk, 8) xor to_unsigned(sector mod 256, 8));
                selectCard;
                exchange(16#81#, rx); exchange(16#57#, rx); exchange(0, rx); exchange(0, rx);
                exchange(sector / 256, rx); exchange(sector mod 256, rx);
                for i in 0 to 127 loop
                    exchange(data(i), rx);
                    chk := to_integer(to_unsigned(chk, 8) xor to_unsigned(data(i), 8));
                end loop;
                exchange(chk, rx);
                exchange(0, rx); assert rx = 16#5C# report "write ack1 " & natural'image(rx) severity failure;
                exchange(0, rx); assert rx = 16#5D# report "write ack2 " & natural'image(rx) severity failure;
                exchange(0, rx); assert rx = 16#47# report "write end " & natural'image(rx) severity failure;
                deselect;
                for i in 0 to 127 loop
                    assert cardByte(sector * 128 + i) = data(i)
                        report "window byte " & natural'image(i) & " of sector " & natural'image(sector) & " got " & natural'image(cardByte(sector * 128 + i)) & " expected " & natural'image(data(i)) & " memWrites " & natural'image(memWrites) severity failure;
                end loop;
                writes := writes + 1;
            end if;
            -- Read the sector back through the protocol.
            selectCard;
            exchange(16#81#, rx); exchange(16#52#, rx); exchange(0, rx); exchange(0, rx);
            exchange(sector / 256, rx); exchange(sector mod 256, rx);
            exchange(0, rx); assert rx = 16#5C# report "read ack1 " & natural'image(rx) severity failure;
            exchange(0, rx); assert rx = 16#5D# report "read ack2 " & natural'image(rx) severity failure;
            exchange(0, rx); assert rx = sector / 256 report "read msb" severity failure;
            exchange(0, rx); assert rx = sector mod 256 report "read lsb" severity failure;
            chk := to_integer(to_unsigned(sector / 256, 8) xor to_unsigned(sector mod 256, 8));
            for i in 0 to 127 loop
                exchange(0, rx);
                assert rx = cardByte(sector * 128 + i)
                    report "read byte " & natural'image(i) & " of sector " & natural'image(sector) &
                           " got " & natural'image(rx) & " expected " & natural'image(cardByte(sector * 128 + i)) severity failure;
                chk := to_integer(to_unsigned(chk, 8) xor to_unsigned(rx, 8));
            end loop;
            exchange(0, rx); assert rx = chk report "read checksum" severity failure;
            exchange(0, rx); assert rx = 16#47# report "read end " & natural'image(rx) severity failure;
            deselect;
            reads := reads + 1;
        end loop;
        report "PASS memcard window reads=" & natural'image(reads) & " writes=" & natural'image(writes) &
               " mem_words_read=" & natural'image(memReads) & " mem_words_written=" & natural'image(memWrites);
        finish;
    end process;
end architecture;
