"""The save handler must remain executable in the release CPU composition."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import prepare_cpu_exceptions as exceptions
from prepare_save_states import cpu_trap_text, memorymux_exp1_text, exp1_byte_bus_text


class SaveFetchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cpu = (ROOT / 'references/PSX_MiSTer/rtl/cpu.vhd').read_text()

    def test_both_preparation_orders(self):
        release = cpu_trap_text(exceptions.transform(self.cpu))
        reverse = exceptions.transform(cpu_trap_text(self.cpu))
        self.assertEqual(release, reverse)
        self.assertIn(exceptions.SAVE_BAD_FETCH, release)
        self.assertNotIn(exceptions.BAD_FETCH, release)
        self.assertEqual(exceptions.transform(release), release)

    def test_changed_accuracy_decode_is_rejected(self):
        cpu = exceptions.transform(self.cpu).replace(exceptions.BAD_FETCH, 'unknown_fetch_decode')
        with self.assertRaisesRegex(ValueError, 'CPU save-handler fetch error decode'):
            cpu_trap_text(cpu)

    def test_non_save_cpu_keeps_original_decode(self):
        self.assertIn(exceptions.BAD_FETCH, exceptions.transform(self.cpu))

    def test_internal_byte_bus_preserves_other_control_fields(self):
        source = (ROOT / 'references/PSX_MiSTer/rtl/memorymux.vhd').read_text()
        changed = exp1_byte_bus_text(source)
        self.assertEqual(changed.replace('(\"10\" & ex1_memctrl(11 downto 0))', 'ex1_memctrl'), source)
        self.assertIn('(\"10\" & ex1_memctrl(11 downto 0))', memorymux_exp1_text(source))
        with self.assertRaisesRegex(ValueError, 'internal EXP1 byte bus'):
            exp1_byte_bus_text(changed)


if __name__ == '__main__':
    unittest.main()
