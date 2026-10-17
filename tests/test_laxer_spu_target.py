"""Guard the area probe's live interface; these are not SPU audio tests."""
from pathlib import Path
import re
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from prepare_laxer_spu_target import PORTS, TOP, UPSTREAM, prepare, wrapper


class LaxerSpuTargetTests(unittest.TestCase):
    def setUp(self):
        self.text = (UPSTREAM / 'hdlSPU/rtl/SPU.sv').read_text()

    def test_every_port_is_bound_to_its_own_live_top_port(self):
        text = wrapper(self.text)
        self.assertIn('module ' + TOP, text)
        self.assertEqual(len(PORTS), 24)
        bindings = re.findall(r'\.(\w+)\(([^)]+)\)', text)
        self.assertEqual(bindings, [(name, name) for _, _, name in PORTS])
        self.assertEqual(len(set(name for _, _, name in PORTS)), len(PORTS))

    def test_unknown_port_rejects(self):
        text = self.text.replace('module SPU(', 'module SPU(input unexpected,', 1)
        with self.assertRaisesRegex(ValueError, 'port contract'):
            wrapper(text)

    def test_changed_memory_address_width_rejects(self):
        text, count = re.subn(r'\[17:0\](\s+o_adrRAM)', r'[16:0]\1', self.text, count=1)
        self.assertEqual(count, 1)
        with self.assertRaisesRegex(ValueError, 'port contract'):
            wrapper(text)

    def test_duplicate_port_rejects(self):
        text = self.text.replace('module SPU(', 'module SPU(input i_clk,', 1)
        with self.assertRaisesRegex(ValueError, 'port contract'):
            wrapper(text)

    def test_existing_directory_rejects_before_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp); sentinel = path / 'keep'; sentinel.write_bytes(b'preserve')
            with self.assertRaisesRegex(ValueError, 'Fresh'):
                prepare(path)
            self.assertEqual(list(path.iterdir()), [sentinel])
            self.assertEqual(sentinel.read_bytes(), b'preserve')

    def test_existing_manifest_rejects_before_creating_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp); manifest = path / 'manifest.json'; manifest.write_bytes(b'preserve')
            with self.assertRaisesRegex(ValueError, 'Fresh'):
                prepare(path / 'new-target')
            self.assertFalse((path / 'new-target').exists())
            self.assertEqual(manifest.read_bytes(), b'preserve')


if __name__ == '__main__':
    unittest.main()
