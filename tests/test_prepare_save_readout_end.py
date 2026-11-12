"""prepare_save_readout_end.py and prepare_save_readout_end_routine.py: the RTL and routine transforms."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import prepare_save_readout_end as step
import prepare_save_readout_end_routine as routine

EX = ''.join(o for o, _ in step.EX)
CM = ''.join(o for o, _ in step.CM)
CT = ''.join(o + '\n' for o, _ in step.CT)
RT = 'x\n' + routine.FIN_OLD + 'rh_hold:\n' + routine.LOOP_OLD + 'y\n'


class Rtl(unittest.TestCase):
    def test_transforms_and_inverses(self):
        for text, fn, pairs in ((EX + 'reg rd_seen\n', step.ex_text, step.EX), (CM, step.cm_text, step.CM), (CT, step.ct_text, step.CT)):
            new = fn(text)
            self.assertEqual(step.restore(new, pairs), text)
            with self.assertRaises(ValueError):
                fn(new)

    def test_bits_and_wiring(self):
        ex = step.ex_text(EX + 'reg rd_seen\n')
        self.assertIn("{4'd0, cue_busy_s[1], rd_last, rd_seen, spu_quiet}", ex)
        self.assertIn("{1'b0, up_addr[19:9]} + 12'd1 == size_pages", ex)
        cm = step.cm_text(CM)
        self.assertIn("{15'd0,save_hold_s[1],16'd0}", cm)
        self.assertLess(cm.index('reg [1:0] save_hold_s'), cm.index('wire [31:0] cheat_q'))
        ct = step.ct_text(CT)
        self.assertIn('.save_hold(save_cd_freeze)', ct)
        self.assertIn('.cue_busy(cue_cmd_busy)', ct)
        sdc = step.sdc_text('')
        self.assertIn('save_hold_s[0]', sdc)
        for kind in ('set_max_delay 13 ', 'set_min_delay -13 '):
            line = next(l for l in sdc.splitlines() if l.startswith(kind) and 'rd_last' in l)
            self.assertIn('req_we_74', line); self.assertIn('req_addr_74[*]', line)

    def test_needs_readout_hold(self):
        with self.assertRaises(ValueError):
            step.ex_text(EX.replace('rd_seen', 'rd_xxxx'))

    def test_every_fault_applies(self):
        texts = {'ex': step.ex_text(EX + 'reg rd_seen\n'), 'cm': step.cm_text(CM)}
        for name, (kind, _, _) in step.FAULTS.items():
            self.assertNotEqual(step.fault_text(texts[kind], name), texts[kind], name)


class Routine(unittest.TestCase):
    def test_transform(self):
        new = routine.firmware_text(RT)
        self.assertIn('andi    $t5, $t0, 4', new)
        self.assertIn('rh_out:', new)
        self.assertLess(new.index('re_cue:'), new.index('sb      $t0, FINISH($k0)'))
        with self.assertRaises(ValueError):
            routine.firmware_text(new)
        with self.assertRaises(ValueError):
            routine.firmware_text('no hold here')

    def test_every_fault_changes_the_text(self):
        new = routine.firmware_text(RT)
        for name in routine.FAULTS:
            self.assertNotEqual(routine.fault_text(new, name), new, name)


if __name__ == '__main__':
    unittest.main()
