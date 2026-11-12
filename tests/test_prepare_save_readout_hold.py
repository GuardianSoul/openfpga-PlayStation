"""prepare_save_readout_hold.py: the device and SDC transforms."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import prepare_save_readout_hold as step

# the lines of the E4 device the step anchors on
DEVICE = '''module psx_save_exp1 #(
) (
    output reg         mdec_tap = 1'b0,
    input  wire        up_we,
    input  wire [20:0] up_addr
);
    always @(*) begin
        else if (reg_area) case (bus_addr[3:0])
            4'd11:   bus_dataRead = {5'd0, disc};
            4'd12:   bus_dataRead = {7'd0, spu_quiet};
            default: bus_dataRead = {5'd0, want_load, want_save, cp_busy};
        endcase
    end
    reg own_req = 1'b0;            // a copy word is on the aux port
    reg up_go = 1'b0;              // a card access is on the aux port
    always @(posedge clk2x) begin
        if (reset_2x) begin
            trap <= 1'b0;
        end else begin
            if (wr_edge && reg_area && !bus_addr[5] && !bus_addr[4]) case (bus_addr[3:0])
                4'd4: trap <= 1'b0;
                4'd12: begin spu_hold <= bus_dataWrite[0]; mdec_tap <= bus_dataWrite[1]; end   // save-mdec-tap
                default: ;
            endcase
            if (up_go) begin if (aux_done) up_go <= 1'b0; end
            else if (up_start) up_go <= 1'b1;
        end
    end
endmodule
`default_nettype wire
'''
SDC = ('set_max_delay 13 -from [get_registers {*|psx_memcard_slot:memcard_slot|req_we_74 '
       '*|psx_memcard_slot:memcard_slot|req_addr_74[*]}] -to [get_registers {*|psx_vram_x3:vram_x3|*}]\n')


class Device(unittest.TestCase):
    def test_transform_and_its_inverse(self):
        new = step.device_text(DEVICE)
        self.assertEqual(step.restore(new), DEVICE)
        self.assertIn("    reg rd_seen = 1'b0;\n", new)
        self.assertIn("4'd12:   bus_dataRead = {6'd0, rd_seen, spu_quiet};", new)
        self.assertIn("mdec_tap <= bus_dataWrite[1]; rd_seen <= 1'b0; end", new)
        # the set comes after the bus writes (it wins) and before the aux grant moves
        self.assertLess(new.index("rd_seen <= 1'b0; end"), new.index("if (up_go && !up_we && up_addr[20]) rd_seen <= 1'b1;"))
        self.assertLess(new.index("rd_seen <= 1'b1;"), new.index("if (up_go) begin if (aux_done)"))
        self.assertEqual(len(new.splitlines()) - len(DEVICE.splitlines()), 4)

    def test_refuses_a_second_run_and_changed_anchors(self):
        with self.assertRaises(ValueError):
            step.device_text(step.device_text(DEVICE))
        with self.assertRaises(ValueError):          # before save-mdec-tap
            step.device_text(DEVICE.replace('mdec_tap', 'other'))
        with self.assertRaises(ValueError):          # the read of 0x180C changed
            step.device_text(DEVICE.replace("{7'd0, spu_quiet}", "{6'd0, other, spu_quiet}"))
        with self.assertRaises(ValueError):          # two aux grants
            step.device_text(DEVICE.replace("            else if (up_start)", "            if (up_go) begin if (aux_done) up_go <= 1'b0; end\n            else if (up_start)"))

    def test_every_fault_applies_on_the_real_anchor_lines(self):
        new = step.device_text(DEVICE)
        for name in step.FAULTS:
            self.assertNotEqual(step.fault_text(new, name), new, name)

    def test_sdc(self):
        new = step.sdc_text(SDC)
        self.assertTrue(new.startswith(SDC))
        added = [l for l in new[len(SDC):].splitlines() if l and not l.startswith('#')]
        self.assertEqual(len(added), 2)
        for line, kind in zip(added, ('set_max_delay 13 ', 'set_min_delay -13 ')):
            self.assertTrue(line.startswith(kind))
            self.assertIn('memcard_slot|req_we_74 *|psx_memcard_slot:memcard_slot|req_addr_74[20]}]', line)
            self.assertTrue(line.endswith('-to [get_registers {*|psx_save_exp1:save_exp1|rd_seen}]'))
        with self.assertRaises(ValueError):
            step.sdc_text(new)
        with self.assertRaises(ValueError):
            step.sdc_text('# nothing of the slot\n')


if __name__ == '__main__':
    unittest.main()
