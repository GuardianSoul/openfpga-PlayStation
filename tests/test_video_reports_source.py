"""Paired producer/consumer source guards for ordered video report events."""
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from prepare_video_reports_cdc import adapt_videoout,adapt_timer,restore,EDGE,STROBE
from prepare_video_config_cdc import adapt_videoout as forward
from prepare_video_return_ff import adapt_videoout as ff
from prepare_video_request_cdc import adapt_videoout as requests
from tool_paths import ROOT


class ReportsSource(unittest.TestCase):
    def test_only_known_sampler_and_declarations_change(self):
        raw=(ROOT/'references/PSX_MiSTer/rtl/gpu_videoout_async.vhd').read_text()
        for source in (requests(raw),requests(forward(raw)),requests(ff(forward(raw)))):
            with self.subTest(source=len(source)):
                adapted,patches=adapt_videoout(source)
                self.assertEqual(restore(adapted,patches),source)
                self.assertIn('src_clk=>clkvid,dst_clk=>clk1x,reset_async=>request_reset',adapted)
                self.assertIn('src_dot=>videoout_reports.dotclock',adapted)
                for field,bit in [('vsync',7),('irq_VBLANK',6),('hblank_tmr',5),
                        ('GPUSTAT_InterlaceField',4),('GPUSTAT_DrawingOddline',3),
                        ('inVsync',2),('interlacedDisplayField',1),('activeLineLSB',0)]:
                    self.assertIn(f'videoout_reports_1x.{field} <= reports_dest({bit});',adapted)
                self.assertIn('allowunpause1x <= reports_dest(8);',adapted)
                self.assertIn('videoout_reports_1x.dotclock <= reports_dot;',adapted)
                for name in ('videoout_reports_s1','videoout_reports_s2','allowunpause_s1','allowunpause_s2'):
                    self.assertNotRegex(adapted,r'\b'+name+r'\s*<=')
                # Rendering/fetching and the forward/request transport are
                # covered by the exact source restoration, not a new substitute.
                with self.assertRaises(ValueError):adapt_videoout(adapted)

    def test_unknown_sampler_and_missing_shared_reset_reject(self):
        raw=(ROOT/'references/PSX_MiSTer/rtl/gpu_videoout_async.vhd').read_text()
        source=requests(forward(raw))
        for before,after in [('videoout_reports_s2  <= videoout_reports_s1;',
                              'videoout_reports_s2 <= videoout_reports;'),
                             ('request_reset <= reset_1x','request_reset <= ce_1x')]:
            self.assertEqual(source.count(before),1)
            with self.assertRaises(ValueError):adapt_videoout(source.replace(before,after))

    def test_timer_changes_exactly_the_tick_interface(self):
        raw=(ROOT/'references/PSX_MiSTer/rtl/timer.vhd').read_text()
        changed=adapt_timer(raw)
        self.assertEqual(changed.replace(STROBE,EDGE),raw)
        self.assertEqual(changed.count(STROBE),1)
        with self.assertRaises(ValueError):adapt_timer(changed)
        with self.assertRaises(ValueError):adapt_timer(raw.replace(EDGE,'newTick(0) := not dotclock;'))


if __name__=='__main__':unittest.main()
