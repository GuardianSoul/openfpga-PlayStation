import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from prepare_pocket_disc_swap import disc_swap_text  # noqa: E402
from prepare_pocket_dualshock_pad import dualshock_wrapper_text, DUALSHOCK_TOP  # noqa: E402

TOP = """    wire LIDopen;
    wire cd_stream_reset=!power_reset_n || !cpu_locked;
    assign LIDopen=1'b0;
    reg cd_table_grant=0;
    psx_cd_stream #(.DEFAULT_SLOT(16'd101), .MAX_TRACKS(19)) disc_stream (.host_clk(clk_74a),
        .clk(clk1x), .reset(cd_stream_reset), .host_region(2'd2), .disc_slot_id(16'd101), .disc_select_strobe(1'b0),
        .disc_change_done(cd_disc_change_done), .has_disc(hasCD), .disc_pending(cd_disc_pending));
"""


class DiscSwapEdit(unittest.TestCase):
    def test_wires_sequencer_like_the_apf_swap_bench(self):
        text = disc_swap_text(TOP)
        self.assertNotIn("assign LIDopen=1'b0;", text)
        self.assertIn('assign LIDopen=swap_lid_open;', text)
        self.assertIn(".disc_slot_id(swap_slot_id), .disc_select_strobe(swap_select_strobe),", text)
        self.assertIn("psx_disc_select #(.DISC_SLOT(16'd101)) disc_select", text)
        self.assertIn('.disc_pending(cd_disc_pending), .quiet(cd_guest_idle), .change_req(swap_change_req)', text)
        self.assertIn('.guest_idle(cd_guest_idle),', text)
        self.assertIn('.disc_change_done(cd_disc_change_done));', text)
        # declared before the loader uses them
        self.assertLess(text.index('wire [15:0] swap_slot_id;'), text.index('psx_cd_stream #('))

    def test_rejects_missing_anchors(self):
        with self.assertRaises(ValueError): disc_swap_text(TOP.replace("assign LIDopen=1'b0;\n", ''))
        with self.assertRaises(ValueError): disc_swap_text(TOP.replace(".disc_select_strobe(1'b0)", '.disc_select_strobe(x)'))


class DualShockEdit(unittest.TestCase):
    WRAPPER = """    joypad1_KeyL3 : in std_logic;
    joypad1.PadPortDigital <= '1';
    joypad1.PadPortDS <= '0';
    joypad1.Analog1X <= (others=>'0');
    joypad1.Analog1Y <= (others=>'0');
    joypad1.Analog2X <= (others=>'0');
    joypad1.Analog2Y <= (others=>'0');
"""

    def test_wrapper(self):
        text = dualshock_wrapper_text(self.WRAPPER)
        self.assertIn('pad_sticks : in std_logic_vector(31 downto 0);', text)
        self.assertIn('joypad1.PadPortDS <= pad_dualshock;', text)
        self.assertEqual(len(re.findall(r'pad_sticks\(\d+\)\)', text)), 4)
        with self.assertRaises(ValueError): dualshock_wrapper_text(self.WRAPPER.replace("PadPortDS <= '0'", "PadPortDS <= '1'"))

    def test_top_anchors_are_unique_strings(self):
        for old, new in DUALSHOCK_TOP: self.assertIn(old, new)


if __name__ == '__main__':
    unittest.main()
