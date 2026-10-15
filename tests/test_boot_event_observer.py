"""Keep accepted GP1 events bounded to the completed pre-rising snapshot."""
from pathlib import Path
import tempfile
import unittest

from scripts.compare_boot_events import rtl_gpu_events


class BootEventObserverTests(unittest.TestCase):
    def test_snapshot_excludes_next_rising_edge_and_mutable_tail(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'run.log'
            before = 'EVENT GP1 cycles=99 data=03000000\n'
            path.write_text(before + 'PROGRESS cycles=100 pc=800110c0 sectors=1\n' +
                'EVENT GP1 cycles=100 data=08000001\n' + 'EVENT GP1 cycles=101 data=05')
            first = rtl_gpu_events(path, 100)
            self.assertTrue(first['log_flushed_through_snapshot'])
            self.assertEqual(first['accepted_GP1_writes'], [
                {'cycle_before_rising_edge': 99, 'command': '03000000', 'opcode': 3}])
            with path.open('a') as log: log.write('000000\nPROGRESS cycles=500 pc=0 sectors=1\n')
            self.assertEqual(first, rtl_gpu_events(path, 100))

    def test_unflushed_log_cannot_claim_complete_timeline(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'run.log'
            path.write_text('EVENT GP1 cycles=99 data=03000000\n')
            self.assertFalse(rtl_gpu_events(path, 100)['log_flushed_through_snapshot'])


if __name__ == '__main__': unittest.main()
