"""Source identity and protocol/recovery verdict guards for the PAD fixture."""
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from run_apf_pad_checks import PAD, fault_profiles, profiles, simulation_copy, verdict


class APFPadChecks(unittest.TestCase):
    def test_runtime_body_and_resolved_input_are_retained(self):
        original=PAD.read_text();text=simulation_copy(original)
        self.assertIn('synch_3 s01(pad_1wire, pad_1wire_s, clk, pad_1wire_r, pad_1wire_f);',text)
        self.assertIn('localparam  BITLEN = 60;',text)
        self.assertIn('reg [20:0]  rx_timeout;',text)
        self.assertIn('rx_timeout = SIM_RX_SEED;',text)
        self.assertEqual(text.count('pad_1wire_driver <='),5)
        self.assertEqual(original,PAD.read_text())

    def test_unknown_runtime_change_rejects(self):
        original=PAD.read_text()
        for old,new in [('BITLEN = 60','BITLEN = 59'),('posedge clk','negedge clk'),
                        ('rx_word_shift[30:0], pad_1wire_s','rx_word_shift[30:0], ~pad_1wire_s')]:
            with self.assertRaises(ValueError):simulation_copy(original.replace(old,new))

    def test_verdict_checks_complete_reports_and_real_recovery(self):
        good='PASS APF_PAD polls=2 reports=2 hearts=1 aborts=0 drops=0 resets=0 timeouts=0 words=24 decoded_bits=96 sent_bits=768\n'
        case=dict(parameters={})
        self.assertTrue(verdict(good,0,case))
        for output,code in [(good,1),(good+good,0),(good+'FAIL\n',0),('',0),
                            (good.replace('words=24','words=23'),0),
                            (good.replace('hearts=1','hearts=0'),0)]:
            self.assertFalse(verdict(output,code,case))
        self.assertFalse(verdict(good,0,dict(parameters=dict(RESET_SECOND_REPLY=1))))
        self.assertFalse(verdict(good,0,dict(parameters=dict(DROP_SECOND_REPLY=1))))
        reset='PASS APF_PAD polls=3 reports=2 hearts=1 aborts=1 drops=0 resets=1 timeouts=0 words=24 decoded_bits=128 sent_bits=776\n'
        self.assertTrue(verdict(reset,0,dict(parameters=dict(RESET_SECOND_REPLY=1))))
        drop='PASS APF_PAD polls=3 reports=2 hearts=1 aborts=0 drops=1 resets=0 timeouts=1 words=24 decoded_bits=128 sent_bits=768\n'
        self.assertTrue(verdict(drop,0,dict(parameters=dict(DROP_SECOND_REPLY=1))))
        fault=dict(expected_error='PAD_REPORT')
        self.assertTrue(verdict('FATAL PAD_REPORT index=0',1,fault))
        self.assertFalse(verdict('PAD_REPORT',0,fault))
        self.assertFalse(verdict('compile failed',1,fault))
        self.assertFalse(verdict(good+'PAD_REPORT',1,fault))

    def test_profile_matrix_keeps_cold_start_and_unshortened_timeouts(self):
        positive=profiles();self.assertEqual(len(positive),17)
        self.assertEqual(len({p['name'] for p in positive}),17)
        cold=next(p for p in positive if p['name']=='cold-zero-seed')['parameters']
        self.assertEqual([cold[k] for k in ('RX_SEED','POLL_SEED','HEART_SEED')],[0,0,0])
        self.assertEqual(cold['HEART_TARGET'],3)
        self.assertEqual(len(fault_profiles()),6)
        self.assertFalse(any('BITLEN' in p['parameters'] for p in positive))


if __name__=='__main__':unittest.main()
