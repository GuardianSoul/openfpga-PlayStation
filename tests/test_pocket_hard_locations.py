"""Preserve compiler QSF spelling while selecting measured hard resources."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/select_pocket_hard_locations.tcl'
sys.path.insert(0, str(SCRIPT.parent))
from tool_paths import find_tool
QUARTUS = find_tool('quartus_sh')


@unittest.skipUnless(QUARTUS, 'Quartus unavailable')
class HardLocations(unittest.TestCase):
    def run_selector(self, after=None, inventory=None, before=None, existing=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            text = 'set_location_assignment CLKCTRL_G7 -to "core_top:ic|altera_pll:cpu_pll|outclk_wire[0]~CLKENA0"\n'
            files = {
                'before.qsf': before or 'set_global_assignment -name MISC_FILE "original.hv"\n',
                'after.qsf': text if after is None else after,
                'physical.tsv': inventory or 'name\tlocation\ttype\nic|cpu_pll|outclk_wire[0]~CLKENA0\tCLKCTRL_G7\tcyclonev_clkena\n',
            }
            for name, content in files.items():
                (root / name).write_text(content)
            output = root / 'selected.qsf'
            if existing:
                output.write_text('protected\n')
            result = subprocess.run([str(QUARTUS), '-t', str(SCRIPT), str(root / 'before.qsf'),
                str(root / 'after.qsf'), str(root / 'physical.tsv'), str(output)],
                capture_output=True, text=True)
            self.assertEqual(files, {name: (root / name).read_text() for name in files})
            return result, output.read_text() if output.exists() else None

    def test_raw_indexed_quote_survives(self):
        source = 'set_location_assignment CLKCTRL_G7 -to "core_top:ic|altera_pll:cpu_pll|outclk_wire[0]~CLKENA0"\n'
        result, output = self.run_selector(source)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(output, source)
        self.assertNotIn('{', output)

    def test_settings_not_copied_and_existing_location_excluded(self):
        line = 'set_location_assignment CLKCTRL_G7 -to "core_top:ic|altera_pll:cpu_pll|outclk_wire[0]~CLKENA0"\n'
        before = 'set_location_assignment PIN_V15 -to clk_74a\n'
        result, output = self.run_selector(before + line + 'set_global_assignment -name LAST_QUARTUS_VERSION "changed"\n', before=before)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(output, line)

    def test_registers_and_aliases_excluded(self):
        hard = 'set_location_assignment CLKCTRL_G7 -to "core_top:ic|altera_pll:cpu_pll|outclk_wire[0]~CLKENA0"\n'
        inv = 'name\tlocation\ttype\nic|cpu_pll|outclk_wire[0]~CLKENA0\tCLKCTRL_G7\tcyclonev_clkena\nic|reg\tFF_X1_Y2_N3\tcyclonev_ff\n'
        result, output = self.run_selector(hard + 'set_location_assignment FF_X1_Y2_N3 -to core_top:ic|reg\nset_location_assignment LCCOMB_X1_Y2_N3 -to alias\n', inv)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(output, hard)

    def test_wrong_location_rejects_without_partial_output(self):
        result, output = self.run_selector('set_location_assignment CLKCTRL_G8 -to "core_top:ic|altera_pll:cpu_pll|outclk_wire[0]~CLKENA0"\n')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Actual parent location mismatch', result.stderr)
        self.assertIsNone(output)

    def test_duplicate_canonical_target_rejects(self):
        line = 'set_location_assignment CLKCTRL_G7 -to "core_top:ic|altera_pll:cpu_pll|outclk_wire[0]~CLKENA0"\n'
        result, output = self.run_selector(line * 2)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Ambiguous physical target', result.stderr)
        self.assertIsNone(output)

    def test_duplicate_inventory_rejects(self):
        row = 'ic|cpu_pll|outclk_wire[0]~CLKENA0\tCLKCTRL_G7\tcyclonev_clkena\n'
        result, output = self.run_selector(inventory='name\tlocation\ttype\n' + row * 2)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('duplicate physical cell', result.stderr)
        self.assertIsNone(output)

    def test_existing_output_protected(self):
        result, output = self.run_selector(existing=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Fresh output required', result.stderr)
        self.assertEqual(output, 'protected\n')

    def test_unknown_location_grammar_rejects(self):
        result, output = self.run_selector('set_location_assignment CLKCTRL_G7 -from wrong -to target\n')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Unknown location grammar', result.stderr)
        self.assertIsNone(output)


if __name__ == '__main__':
    unittest.main()
