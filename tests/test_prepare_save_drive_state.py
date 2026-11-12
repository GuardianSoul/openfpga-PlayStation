"""prepare_save_drive_state.py and prepare_save_drive_state_routine.py: the cd_top and routine transforms."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import prepare_save_drive_state as step
import prepare_save_drive_state_routine as routine

# the lines of the E4c cd_top the step anchors on
CD = ('begin\n\n   save_copy_busy <= \'0\' when (copyState = COPY_IDLE) else \'1\';\n' + step.BUSY_OLD +
      '                    std_logic_vector(to_unsigned(currentLBA, 24)) & XaFilterChannel & XaFilterFile & modeReg & internalStatus;\n')

# the cd_resume / msf lines of routine g the routine step anchors on
ROUTINE = ('cd_resume:\n        move    $s5, $ra\n        lbu     $t0, PAGE+4*40($k0)     # stat at the save\n'
           '        lw      $s6, PAGE+4*41($k0)     # LBA in bits 23:0\n' + routine.RESUME_OLD +
           '        lbu     $t0, PAGE+4*42($k0)     # IRQENA\n\n' + routine.MSF_OLD + '        li      $t1, 75\n')


class CdTop(unittest.TestCase):
    def test_transform_and_its_inverse(self):
        new = step.cd_text(CD)
        self.assertEqual(step.restore(new), CD)
        self.assertIn('pendingDriveIRQ(4)) or setLocActive) &', new)
        self.assertEqual(new.count('setLocActive'), 1)
        self.assertEqual(len(new.splitlines()) - len(CD.splitlines()), 1)   # the comment line

    def test_refuses_a_second_run(self):
        with self.assertRaises(ValueError):
            step.cd_text(step.cd_text(CD))

    def test_every_fault_changes_the_text(self):
        new = step.cd_text(CD)
        for name in step.FAULTS:
            self.assertNotEqual(step.fault_text(new, name), new, name)


class Routine(unittest.TestCase):
    def test_transform(self):
        new = routine.firmware_text(ROUTINE)
        self.assertNotIn('addiu   $t0, $t0, 150', new)
        self.assertIn('li      $s3, 0x15               # SeekL', new)
        self.assertIn("jal     cd_wait                 # SeekL's second response", new)
        with self.assertRaises(ValueError):
            routine.firmware_text(new)

    def test_every_fault_changes_the_text(self):
        new = routine.firmware_text(ROUTINE)
        for name in routine.FAULTS:
            self.assertNotEqual(routine.fault_text(new, name), new, name)
        self.assertIn('addiu   $t0, $t0, 150', routine.fault_text(new, 'plus150'))


if __name__ == '__main__':
    unittest.main()
