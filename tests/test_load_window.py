import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import prepare_load_window as p

MUX = Path(__file__).resolve().parents[1] / 'references/PSX_MiSTer/rtl/memorymux.vhd'
SAMPLE = ("         if (ram_done = '1') then\n            readram  <= '0';\n         end if;\n\n"
          "         if (ram_load_last > 0) then\n            ram_load_last <= ram_load_last - 1;\n         end if;\n")


class LoadWindowTest(unittest.TestCase):
    def test_gates_countdown_once(self):
        text = MUX.read_text() if MUX.exists() else SAMPLE
        out = p.memorymux_text(text)
        self.assertIn("if (ram_load_last > 0 and readram = '0') then", out)
        self.assertEqual(len(out.splitlines()), len(text.splitlines()))
        with self.assertRaises(ValueError):
            p.memorymux_text(out)


if __name__ == '__main__':
    unittest.main()
