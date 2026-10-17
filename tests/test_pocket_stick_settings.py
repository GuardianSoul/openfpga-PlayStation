"""prepare --stick-settings on a core_top shaped like the release base after
--dualshock, --pad-combos and --shoulder-setting, simulated end to end in
iverilog: menu writes on the bridge, handheld keys in, D-pad and sticks out."""
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
from prepare_pocket_stick_settings import stick_settings_text  # noqa: E402

# Joypad wires first, as the release host trim leaves them; the pad block and
# the shoulder rows as prepare_pocket_early writes them (rows 2 and 3 only).
TOP = """`timescale 1ns/1ps
module top;
    reg clk_74a=0, clk1x=0, cpu_locked=0, bridge_wr=0;
    reg [31:0] bridge_addr=0, bridge_wr_data=0, cont1_key=0, cont1_joy=32'h80808080;
    always #6.734 clk_74a=!clk_74a;
    always #14.76 clk1x=!clk1x;
    wire joypad1_KeyUp=pocket_pad_buttons[0];
    wire joypad1_KeyDown=pocket_pad_buttons[1];
    wire joypad1_KeyLeft=pocket_pad_buttons[2];
    wire joypad1_KeyRight=pocket_pad_buttons[3];
    wire [15:0] pocket_pad_buttons;
    wire [5:0] pad_combo_enable, pad_combos;   // L, R, Y, A, X, B
    wire [3:0] pad_combo_docked, pad_dpad_combos;
    wire pad_dpad_combo_enable;
    assign pad_combo_enable=6'b001111;   // Select+L/R/Y/A = L2/R2/L3/R3
    assign pad_dpad_combo_enable=1'b0;   // Select+D-pad: --stick-settings
    reg dualshock_setting=1'b1;
    wire pad_dualshock; wire [31:0] pad_sticks;
    psx_pocket_analog pad_analog (.clk1x(clk1x),.reset_n(cpu_locked),.clk_74a(clk_74a),.dualshock_setting(dualshock_setting),
        .apf_type(cont1_key[31:28]),.apf_joy(cont1_joy),.dualshock(pad_dualshock),.sticks(pad_sticks));
    psx_pocket_pad #(.HOLD_BITS(6),.TAP_BITS(4)) pad_input (.clk1x(clk1x),.reset_n(cpu_locked),
        .apf_keys(cont1_key[15:0]),.combo_enable(pad_combo_enable),.dpad_combo_enable(pad_dpad_combo_enable),
        .buttons(pocket_pad_buttons),.combos(pad_combos),.dpad_combos(pad_dpad_combos),.docked(pad_combo_docked));
    reg [1:0] shoulder_set2=2'd2;   // SELECT + L/R
    always @(posedge clk_74a) if (bridge_wr && bridge_addr==32'h50000014) shoulder_set2<=bridge_wr_data[1:0]|bridge_wr_data[9:8]|bridge_wr_data[17:16]|bridge_wr_data[25:24];
    (* async_reg="true" *) reg [1:0] shoulder_meta2=2'd2, shoulder_sel2=2'd2;
    always @(posedge clk1x) begin shoulder_meta2<=shoulder_set2; shoulder_sel2<=shoulder_meta2; end
    reg [1:0] shoulder_set3=2'd3;   // SELECT + Y/A
    always @(posedge clk_74a) if (bridge_wr && bridge_addr==32'h50000018) shoulder_set3<=bridge_wr_data[1:0]|bridge_wr_data[9:8]|bridge_wr_data[17:16]|bridge_wr_data[25:24];
    (* async_reg="true" *) reg [1:0] shoulder_meta3=2'd3, shoulder_sel3=2'd3;
    always @(posedge clk1x) begin shoulder_meta3<=shoulder_set3; shoulder_sel3<=shoulder_meta3; end
    wire joypad1_KeyL3=(shoulder_sel3==2'd3 & pad_combos[2]);
    wire joypad1_KeyR3=(shoulder_sel3==2'd3 & pad_combos[3]);
    wire [31:0] wrapper_sticks;
    assign wrapper_sticks=pad_sticks_probe;
    wire [31:0] pad_sticks_probe;
    sink wrapper (.pad_sticks(pad_sticks),.out(pad_sticks_probe));
BENCH
endmodule
module sink(input wire [31:0] pad_sticks, output wire [31:0] out); assign out=pad_sticks; endmodule
"""

BENCH = """    integer errors=0;
    localparam UP=0, DOWN=1, LEFT=2, RIGHT=3, A=4, B=5, X=6, Y=7, SELECT=14;
    task write(input [31:0] a, input [31:0] d); begin @(posedge clk_74a); bridge_wr<=1; bridge_addr<=a; bridge_wr_data<=d; @(posedge clk_74a); bridge_wr<=0; repeat(8) @(posedge clk1x); end endtask
    task keys(input [15:0] k); begin cont1_key[15:0]=k; repeat(30) @(posedge clk1x); end endtask
    task check_out(input [3:0] d, input [31:0] s, input [8*40-1:0] what);
        if({joypad1_KeyRight,joypad1_KeyLeft,joypad1_KeyDown,joypad1_KeyUp}!==d || wrapper_sticks!==s) begin
            errors=errors+1; $display("FAIL %0s: dpad %b want %b, sticks %h want %h", what,
                {joypad1_KeyRight,joypad1_KeyLeft,joypad1_KeyDown,joypad1_KeyUp}, d, wrapper_sticks, s); end
    endtask
    initial begin
        cont1_key[31:28]=4'h1;   // handheld: centred sticks
        repeat(4) @(posedge clk1x); cpu_locked=1; repeat(10) @(posedge clk1x);
        keys(1<<UP); check_out(4'b0001, 32'h80808080, "default D-pad");
        keys(0); keys(1<<SELECT|1<<DOWN); check_out(4'b0000, 32'h8080FF80, "Select+D-pad default left stick");
        keys(0); repeat(40) @(posedge clk1x);
        write(32'h5000001C, 32'h00000200);   // D-pad = right stick (value in byte 1)
        keys(1<<LEFT); check_out(4'b0000, 32'h80008080, "D-pad = right stick");
        write(32'h5000001C, 0); write(32'h50000020, 3);   // Select+D-pad = D-pad
        keys(1<<SELECT|1<<RIGHT); check_out(4'b1000, 32'h80808080, "Select+D-pad as D-pad");
        keys(0); repeat(40) @(posedge clk1x);
        write(32'h50000018, 5);   // Select + A/B/X/Y = right stick
        keys(1<<SELECT|1<<X|1<<A); check_out(4'b0000, 32'h00FF8080, "Select+X/A right stick");
        if(joypad1_KeyL3!==0) begin errors=errors+1; $display("FAIL face stick also pressed L3"); end
        keys(0); repeat(40) @(posedge clk1x);
        write(32'h50000018, 3);   // back to L3/R3
        keys(1<<SELECT|1<<Y); if(joypad1_KeyL3!==1) begin errors=errors+1; $display("FAIL Select+Y no longer L3"); end
        check_out(4'b0000, 32'h80808080, "Select+Y as L3: no stick");
        keys(0); repeat(40) @(posedge clk1x);
        keys(1<<SELECT|1<<Y|1<<A|1<<UP); check_out(4'b0001, 32'h80808080, "Select+Y+A+Up: L3+R3+Up for analog mode");
        keys(0); repeat(40) @(posedge clk1x);
        keys(1<<SELECT|1<<B); check_out(4'b0000, 32'h80808080, "Select+B with L3/R3 row");
        keys(0); repeat(40) @(posedge clk1x);
        dualshock_setting=0; write(32'h5000001C, 1); keys(1<<UP); check_out(4'b0001, 32'h80808080, "digital pad: stick mode falls back to D-pad");
        if(errors==0) $display("PASS stick settings");
        $finish;
    end
"""


class StickSettings(unittest.TestCase):
    def edited(self):
        return stick_settings_text(TOP)

    def test_edits(self):
        text = self.edited()
        self.assertIn("reg [2:0] shoulder_set3=3'd3;", text)
        self.assertIn('    wire joypad1_KeyUp;\n', text)
        self.assertIn("assign pad_dpad_combo_enable=sel_dpad_sel!=2'd0;", text)
        self.assertNotIn("assign pad_combo_enable=6'b001111;", text)
        self.assertIn('.pad_sticks(pad_sticks_mapped),', text)
        self.assertLess(text.index('shoulder_sel3<=shoulder_meta3; end'), text.index('psx_pocket_stick_map stick_map'))
        with self.assertRaises(ValueError): stick_settings_text(TOP.replace("reg [1:0] shoulder_set3=2'd3;", ''))

    def test_simulation(self):
        iverilog = shutil.which('iverilog')
        if not iverilog: raise unittest.SkipTest('iverilog not installed')
        work = Path(tempfile.mkdtemp())
        (work/'top.sv').write_text(self.edited().replace('BENCH', BENCH))
        subprocess.run([iverilog, '-g2012', '-s', 'top', '-o', str(work/'a.out'), str(work/'top.sv')]
                       + [str(ROOT/'rtl/pocket'/n) for n in ('psx_pocket_pad_combo.sv', 'psx_pocket_analog.sv', 'psx_pocket_stick_map.sv')],
                       check=True)
        out = subprocess.run(['vvp', '-n', str(work/'a.out')], capture_output=True, text=True, check=True).stdout
        self.assertIn('PASS stick settings', out, out)


if __name__ == '__main__':
    unittest.main()
