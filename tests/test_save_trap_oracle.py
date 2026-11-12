"""A CD-busy retry is legal; an unexercised GPU-busy case must still fail."""
from contextlib import redirect_stdout
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import run_save_trap_cpu as runner


class SaveTrapOracleTest(unittest.TestCase):
    def reference(self):
        return True, dict(cycles='119377', log_stores='400'), 'same guest state', ''

    def save(self, retries='1'):
        # The actual release-order run completed with one quiet-point retry.
        return True, dict(cycles='585863', log_stores='400', saves='1', loads='0',
                          handler_fetches='31546', retries=retries), 'same guest state', ''

    def test_plain_save_can_retry(self):
        with patch.object(runner, 'simulate', side_effect=[self.reference(), self.save()]), redirect_stdout(io.StringIO()):
            self.assertEqual(runner.scenarios(Path('/unused'), only=('save',)), [])

    def test_busy_gpu_must_exercise_retry(self):
        with patch.object(runner, 'simulate', side_effect=[self.reference(), self.save(), self.save('0')]), redirect_stdout(io.StringIO()):
            failures = runner.scenarios(Path('/unused'), only=('save GPU busy',))
        self.assertEqual(failures, ['save GPU busy: retries 0'])


if __name__ == '__main__':
    unittest.main()
