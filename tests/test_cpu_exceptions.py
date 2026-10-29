import sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import prepare_cpu_exceptions as p

NOPS = ''.join(f'            when 16#{op:02X}# => -- {name} -> NOP \n               null; \n'
               for op, (_, name) in p.COP_OPS.items())
FETCH = ('            case (to_integer(FetchAddr(31 downto 29))) is\n               \n'
         '               when 0 | 4 => -- cache\n                  x;\n'
         '               when others =>\n                  -- todo\n               \n            end case;\n')
CPU = 'a\n' + FETCH + NOPS + 'b\n' + ''.join(old for _, old, _ in p.EDITS)


class CpuExceptionsTest(unittest.TestCase):
    def test_transform(self):
        out = p.transform(CPU)
        self.assertEqual(p.transform(out), out)
        self.assertNotIn('-> NOP', out)
        self.assertEqual(out.count('exceptionCode_3 <= x"B";'), 8)
        self.assertIn("if (cop0_SR(28) = '0') then", out)   # SWC0/LWC0 check CU0 even in kernel mode
        self.assertIn("fetchErrNow       <= '1';", out)
        self.assertIn('exceptionCode_3 <= x"6";', out)
        self.assertIn('decodeFetchErr <= fetchErr0 and not cacheHit and not cacheHitLast;', out)

    def test_project(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); (root / 'cpu.vhd').write_text(CPU)
            (root / 'apf_top.qsf').write_text(f'set_global_assignment -name VHDL_FILE "{root}/cpu.vhd" -library work\n')
            p.apply(root)
            self.assertIn(p.TAG, (root / 'cpu.vhd').read_text())

    def test_rejects_unknown_source(self):
        with self.assertRaises(ValueError):
            p.transform(CPU.replace('-- COP3 -> NOP', '-- COP3'))


if __name__ == '__main__':
    unittest.main()
