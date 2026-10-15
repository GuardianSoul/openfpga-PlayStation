library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
use std.env.all;
entity tb_video_reports_timer is end;
architecture test of tb_video_reports_timer is
    signal clk : std_logic := '0';
    signal ce,reset : std_logic := '1';
    signal dot,hblank,wr : std_logic := '0';
    signal address : unsigned(5 downto 0) := (others=>'0');
    signal data : std_logic_vector(31 downto 0) := (others=>'0');
    signal current : unsigned(15 downto 0);
    signal irq : std_logic;
begin
    clk <= not clk after 15 ns;
    dut : entity work.timer port map (
        clk1x=>clk,ce=>ce,reset=>reset,error=>open,
        dotclock=>dot,hblank=>hblank,vblank=>'0',
        irqRequest0=>irq,irqRequest1=>open,irqRequest2=>open,
        bus_addr=>address,bus_dataWrite=>data,bus_read=>'0',bus_write=>wr,bus_dataRead=>open,
        export_t_current0=>current,export_t_current1=>open,export_t_current2=>open,
        loading_savestate=>'0',SS_reset=>'0',SS_DataWrite=>(others=>'0'),SS_Adr=>(others=>'0'),
        SS_wren=>'0',SS_rden=>'0',SS_DataRead=>open);
    process
        procedure step(d,b : std_logic; enabled : std_logic := '1') is
        begin
            wait until falling_edge(clk);dot<=d;hblank<=b;ce<=enabled;
            wait until rising_edge(clk);wait for 1 ns;
        end procedure;
        procedure write_reg(a,v : natural) is
        begin
            wait until falling_edge(clk);address<=to_unsigned(a,6);data<=std_logic_vector(to_unsigned(v,32));wr<='1';dot<='0';
            wait until rising_edge(clk);wait for 1 ns;
            wait until falling_edge(clk);wr<='0';
        end procedure;
        procedure mode(v : natural) is
        begin
            write_reg(4,v);step('0','0');step('0','0');
        end procedure;
        variable before : natural;
    begin
        step('0','0');step('0','0');reset<='0';
        mode(16#0100#);
        for i in 1 to 40 loop step('1','0');end loop;
        assert current=40 report "Adjacent tick strobes were lost" severity failure;
        for i in 1 to 6 loop step('1','0','0');end loop;
        step('0','0','0');step('0','0');
        assert current=40 report "CE stall changed or replayed timer ticks" severity failure;
        for i in 1 to 3 loop step('1','0');end loop;
        assert current=43 report "Strobe restart count mismatch" severity failure;

        -- Sync mode 0 pauses inside HBlank. Tick and gate travel together.
        mode(16#0101#);
        for i in 1 to 6 loop step('1','0');end loop;
        for i in 1 to 8 loop step('1','1');end loop;
        for i in 1 to 4 loop step('1','0');end loop;
        assert current=10 report "Tick/HBlank pairing failed in pause mode" severity failure;
        -- Sync mode 2 counts only inside HBlank, resetting on its falling edge.
        mode(16#0105#);
        for i in 1 to 6 loop step('1','0');end loop;
        for i in 1 to 5 loop step('1','1');end loop;
        assert current=5 report "Inside-HBlank tick count mismatch" severity failure;
        step('0','0');
        assert current=0 report "HBlank reset edge changed" severity failure;
        for i in 1 to 3 loop step('1','0');end loop;
        for i in 1 to 4 loop step('1','1');end loop;
        assert current=4 report "Outside-HBlank ticks leaked into counter" severity failure;

        mode(16#0100#);write_reg(0,16#FFFD#);step('0','0');step('0','0');
        for i in 1 to 3 loop step('1','0');end loop;
        assert current=0 report "Strobe counter wrap mismatch" severity failure;
        mode(16#0158#);write_reg(8,2);step('0','0');step('0','0');
        for i in 1 to 3 loop step('1','0');end loop;
        assert current=0 and irq='1' report "Target IRQ/reset changed" severity failure;
        step('1','0');
        assert current=0 and irq='0' report "Target block-next cycle changed" severity failure;
        step('1','0');assert current=1 report "Target restart changed" severity failure;

        mode(0);before:=to_integer(current);
        for i in 1 to 20 loop step('0','0');end loop;
        assert current=before+20 report "System-clock timer mode changed" severity failure;
        report "PASS REPORTS_TIMER adjacent/CE/HBlank/wrap/targetIRQ/system-clock" severity note;
        finish;
    end process;
end;
