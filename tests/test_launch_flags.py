"""prepare --fast-bios and --launch-bar source patches (docs/pocket-launch-timing.md)."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))

import prepare_fast_bios as fast  # noqa: E402
import prepare_launch_bar as bar  # noqa: E402


class FastBiosTest(unittest.TestCase):
    def test_upstream_memorymux(self):
        old = (ROOT/'references/PSX_MiSTer/rtl/memorymux.vhd').read_text()
        new = fast.patch_memorymux(old)
        added = [l for l in new.splitlines() if l not in old.splitlines()]
        # the flag, its reset, its clear on the shell's first RAM fetch, the WAITING gate
        self.assertEqual(sum('fast_bios' in l for l in added), 4)
        self.assertIn("if (waitcnt > 0 and (fastboot = '0' or fast_bios = '0')) then", new)
        self.assertIn("if (false and to_integer(addressBIOS_buf) >= 16#18000#", new)
        self.assertNotIn("fastboot = '1' and to_integer(addressBIOS_buf)", new)
        # every original wait is still there: only the countdown is skipped while fast_bios is set
        for w in ('waitcnt        <= 25;', 'waitcnt        <= 26;', 'when "10" => waitcnt <= 25;'):
            self.assertEqual(old.count(w), new.count(w))
        # the shell clears it: an instruction fetch from RAM at 0x10000 or above
        self.assertIn('if (mem_addressInstr(22 downto 16) /= "0000000") then fast_bios <= \'0\'', new)

    def test_refuses_changed_source(self):
        with self.assertRaises(ValueError): fast.patch_memorymux('entity memorymux is end;')
        with self.assertRaises(ValueError): fast.patch_wrapper('entity x is end;')

    def test_wrapper_and_core_top(self):
        wrapper = "entity psx_mister_psx_top_resource is\nport (\n    clk1x : in std_logic;\n);\n        fastboot => '0',\n"
        out = fast.patch_wrapper(wrapper)
        self.assertIn('    fast_bios_en : in std_logic;', out)
        self.assertIn('fastboot => fast_bios_en,', out)
        core = "    reg logo_skip=1'b0;\n    psx_mister_psx_top_resource psx (.clk1x(clk1x));\n"
        out = fast.patch_core_top(core)
        self.assertIn(".fast_bios_en(fast_bios_sync[1]),.clk1x", out)
        self.assertIn("bridge_addr==32'h50000070", out)


class LaunchBarTest(unittest.TestCase):
    def test_core_top(self):
        out = bar.patch_core_top('x\n    assign video_skip=apf_skip, video_rgb=apf_rgb;\ny\n')
        self.assertIn("video_rgb=!lb_on ? apf_rgb : {2'b0,lb_x}<lb_fpx ? 24'hffffff : 24'h404040;", out)
        self.assertIn("lb_fast_m<={lb_fast_m[0],1'b0};", out)                 # no --fast-bios: the normal rate
        self.assertIn("lb_fast_m<={lb_fast_m[0],fast_bios_set};", bar.patch_core_top("    reg fast_bios_set=1'b0;\n    assign video_skip=apf_skip, video_rgb=apf_rgb;\n"))
        self.assertIn('if (apf_de && |apf_rgb) lb_done<=1\'b1;', out)        # ends at the first picture
        self.assertIn('else if (lb_mode!=lb_mode_ref) lb_done<=1\'b1;', out)  # or at the BIOS's video mode change
        self.assertIn('lb_on=lb_run_m[1] && !lb_done && lb_ref_ok && apf_de', out)         # active pixels only, after Reset Exit
        self.assertNotIn('clk_74a', out)                                       # all in the video clock
        with self.assertRaises(ValueError): bar.patch_core_top('nothing')


if __name__ == '__main__': unittest.main()
