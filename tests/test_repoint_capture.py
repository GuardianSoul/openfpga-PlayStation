import hashlib, json, subprocess, sys, tempfile, unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent/'scripts/repoint_capture.py'


def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()


class RepointCapture(unittest.TestCase):
    def make(self, tmp):
        work = '/ci/workspace/psx-fit@2/src'
        cap = tmp/'cap'; (cap/'output_files').mkdir(parents=True); (cap/'sub').mkdir()
        (cap/'apf_top.qsf').write_text(f'set_global_assignment -name SDC_FILE {work}/build/fits/x-s6/pocket.sdc\n')
        (cap/'sub/a.sv').write_text(f'// {work}/build/fits/x-s6/sub\nmodule a; endmodule\n')
        (cap/'output_files/apf_top.rbf').write_bytes(b'\0\1' + work.encode())
        (cap/'release-timing.json').write_text(json.dumps(
            {'project': f'{work}/build/fits/x-s6', 'qsf_sha256': sha(cap/'apf_top.qsf'), 'passed': True}))
        return cap

    def run_it(self, cap, root):
        return subprocess.run([sys.executable, str(SCRIPT), str(cap), '--root', str(root)], capture_output=True, text=True)

    def test_repoint(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t); cap = self.make(tmp); root = tmp/'repo'; root.mkdir()
            r = self.run_it(cap, root); self.assertEqual(r.returncode, 0, r.stderr)
            dest = root/'build/fits/x-s6'; self.assertEqual(r.stdout.strip(), str(dest))
            self.assertIn(str(dest/'pocket.sdc'), (dest/'apf_top.qsf').read_text())
            self.assertNotIn('/ci/', (dest/'sub/a.sv').read_text())
            self.assertEqual((dest/'output_files/apf_top.rbf').read_bytes(), (cap/'output_files/apf_top.rbf').read_bytes())
            self.assertIn('/ci/', (cap/'apf_top.qsf').read_text())          # capture untouched
            t2 = json.loads((dest/'release-timing.json').read_text())
            self.assertEqual(t2['project'], str(dest)); self.assertEqual(t2['qsf_sha256'], sha(dest/'apf_top.qsf'))
            self.assertIn('/ci/', (cap/'release-timing.json').read_text())
            again = self.run_it(cap, root); self.assertEqual(again.returncode, 0, again.stderr)   # reuse
            self.assertEqual(again.stdout.strip(), str(dest))

    def test_refuses_stale_timing(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t); cap = self.make(tmp); root = tmp/'repo'; root.mkdir()
            (cap/'apf_top.qsf').write_text('changed\n')
            self.assertNotEqual(self.run_it(cap, root).returncode, 0)

    def test_refuses_foreign_dest(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t); cap = self.make(tmp); root = tmp/'repo'
            (root/'build/fits/x-s6').mkdir(parents=True)
            self.assertNotEqual(self.run_it(cap, root).returncode, 0)


if __name__ == '__main__':
    unittest.main()
