"""Source contracts for an opt-in coherent request transport."""
import re
import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from prepare_video_request_cdc import adapt_videoout
from prepare_video_config_cdc import adapt_videoout as forward
from prepare_video_return_ff import adapt_videoout as ff
from tool_paths import ROOT


class RequestSource(unittest.TestCase):
    def test_raw_forward_and_ff_sources_keep_other_crossings(self):
        raw=(ROOT/'references/PSX_MiSTer/rtl/gpu_videoout_async.vhd').read_text()
        for source in (raw,forward(raw),ff(forward(raw))):
            with self.subTest(source=len(source)):
                adapted=adapt_videoout(source)
                for name in ('videoout_reports_s1','videoout_reports_s2','videoout_reports_1x',
                             'allowunpause_s1','allowunpause_s2','allowunpause1x'):
                    before=re.findall(r'\b'+name+r'\s*<=\s*[^;]+;',source)
                    after=re.findall(r'\b'+name+r'\s*<=\s*[^;]+;',adapted)
                    self.assertEqual(before,after)
                self.assertIn('videoout_request_vid <= videoout_request;',adapted)
                self.assertNotRegex(adapted,r'\bvideoout_request_s[12]\b')
                self.assertIn('if rising_edge(clk1x) then request_reset <= reset_1x;',adapted)
                self.assertEqual(adapted.count('component psx_video_request_cdc is'),1)
                self.assertIn("request_initialized='1' and request_fault='0'",adapted)
                with self.assertRaises(ValueError):adapt_videoout(adapted)

    def test_modified_original_stage_rejects(self):
        raw=(ROOT/'references/PSX_MiSTer/rtl/gpu_videoout_async.vhd').read_text()
        raw=raw.replace('videoout_request_s2  <= videoout_request_s1;', 'videoout_request_s2 <= videoout_request;')
        with self.assertRaises(ValueError):adapt_videoout(raw)

    def test_shared_packet_implementation_and_clock_bounds(self):
        rtl=(ROOT/'rtl/pocket/psx_video_request_cdc.sv').read_text()
        self.assertIn('psx_video_config_cdc #(.WIDTH(39),.ADDR_WIDTH(4))',rtl)
        self.assertNotIn('ce',rtl.split('module psx_video_request_cdc',1)[1].split(');',1)[0].replace('src_ready',''))
        sdc=(ROOT/'scripts/pocket_video_request_scope.tcl').read_text()
        self.assertIn('request_src_period<1.2*$request_dst_period',sdc)
        self.assertNotRegex(sdc,r'set_false_path[^\n]*get_clocks')
        self.assertNotIn('global request_channel',sdc)


if __name__=='__main__':unittest.main()
