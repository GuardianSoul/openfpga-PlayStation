"""The game list's vote service (worker/vote.js): one vote per address,
game and released version, rate limited, addresses stored only hashed."""
from pathlib import Path
import json
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which('node')


def node_has_sqlite():
    return NODE and subprocess.run([NODE, '-e', "require('node:sqlite')"], capture_output=True).returncode == 0


@unittest.skipUnless(node_has_sqlite(), 'needs node with node:sqlite')
class VoteWorker(unittest.TestCase):
    def test_votes(self):
        out = json.loads(subprocess.run([NODE, str(ROOT/'tests/vote_worker.mjs')], check=True,
                                        capture_output=True, text=True).stdout)
        self.assertEqual(out['first']['status'], 200)
        self.assertEqual(out['votes']['body'], {'tekken3|USA|0.5.3': {'Minor issues': 1, 'Playable': 1}})
        # Newest vote per game and version: only versions that still have a vote.
        self.assertEqual(list(out['latest']['body']), ['tekken3|USA|0.5.3'])
        self.assertIsInstance(out['latest']['body']['tekken3|USA|0.5.3'], int)
        self.assertEqual(out['mine']['body'], {'tekken3|USA|0.5.3': 'Minor issues'})
        for refused in ('badVersion', 'unreleased', 'badGame', 'badStatus'):
            self.assertEqual(out[refused]['status'], 400, refused)
        self.assertEqual(out['newRelease']['status'], 200)
        # The page reads the version list from the service, newest first.
        self.assertEqual(out['versions']['body'], {'versions': ['0.6.0', '0.5.3', '0.5.2']})
        self.assertEqual(out['limitedAt'], 30)
        self.assertEqual(out['votes']['cors'], 'https://guardiansoul.github.io')
        self.assertEqual(out['missing']['status'], 404)
        self.assertEqual(out['note']['status'], 200)
        for refused in ('noteLink', 'noteLong', 'noteHow', 'notePlace', 'noteNoVote'):
            self.assertEqual(out[refused]['status'], 400, refused)
        listed = out['notes']['body']['tekken3|USA|0.5.3']
        self.assertEqual([(n['status'], n['how'], n['place'], n['text']) for n in listed],
                         [('Playable', 'cue', '', ''), ('Minor issues', 'json', 'dock', 'Music stops in stage 2')])
        self.assertEqual(out['mynotes']['body'],
                         {'tekken3|USA|0.5.3': {'how': 'json', 'place': 'dock', 'text': 'Music stops in stage 2'}})
        self.assertEqual(len(out['notesAfter']['body']['tekken3|USA|0.5.3']), 1)
        self.assertEqual(out['afterVote'], {'Boots': 1, 'Minor issues': 1, 'Playable': 1})
        self.assertEqual((out['downPlain'], out['downJunk']), (200, 400))
        for voter in out['voters']:   # addresses only as a salted hash
            self.assertRegex(voter, '^[0-9a-f]{64}$')

if __name__ == '__main__':
    unittest.main()
