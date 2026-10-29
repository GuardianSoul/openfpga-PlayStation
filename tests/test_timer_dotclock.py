import sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import prepare_timer_dotclock as p

VIDEO = 'a\n' + p.SIG_OLD + 'b\n' + p.DOT_OLD + 'c\n'
TIMER = 'a\n' + p.TICK_OLD + 'b\n'


class TimerDotclockTest(unittest.TestCase):
    def test_transform(self):
        v, t = p.video_text(VIDEO), p.timer_text(TIMER)
        self.assertEqual((p.video_text(v), p.timer_text(t)), (v, t))
        self.assertIn('videoout_reports.dotclock <= dotToggle;', v)
        self.assertIn('dotclock xor dotclock_1', t)

    def test_project(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); (root / 'v').mkdir()
            (root / 'v/gpu_videoout_async.vhd').write_text(VIDEO); (root / 'timer.vhd').write_text(TIMER)
            (root / 'apf_top.qsf').write_text(
                f'set_global_assignment -name VHDL_FILE "{root}/v/gpu_videoout_async.vhd" -library work\n'
                f'set_global_assignment -name VHDL_FILE "{root}/timer.vhd" -library work\n')
            p.apply(root)
            self.assertIn(p.TAG, (root / 'timer.vhd').read_text())

    def test_refuses_shared_source(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as other:
            root = Path(d); (Path(other) / 'timer.vhd').write_text(TIMER); (root / 'gpu_videoout_async.vhd').write_text(VIDEO)
            (root / 'apf_top.qsf').write_text(
                f'set_global_assignment -name VHDL_FILE "{root}/gpu_videoout_async.vhd"\n'
                f'set_global_assignment -name VHDL_FILE "{other}/timer.vhd"\n')
            with self.assertRaises(SystemExit):
                p.apply(root)


if __name__ == '__main__':
    unittest.main()
