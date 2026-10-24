"""The game list page (docs/compat.js) groups compatibility report issues
into one entry per game and region."""
from pathlib import Path
import json
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which('node')
DOCS = next(p for p in (ROOT/'docs', ROOT/'release/docs') if (p/'compat.js').exists())
GAMES = '''# test list
SCUS-94163\tFinal Fantasy VII (USA) (Disc 1)
SCUS-94163\tFinal Fantasy VII (USA) (Disc 2)
SCUS-94163\tFinal Fantasy VII (USA) (Disc 3)
SCUS-94961\tFinal Fantasy VII (USA) (Interactive Sampler CD)
SCES-00867\tFinal Fantasy VII (Europe) (Disc 1)
SCES-10867\tFinal Fantasy VII (Europe) (Disc 2)
SCUS-94154\tCrash Bandicoot 2 - Cortex Strikes Back (USA)
SLUS-00402\tTekken 3 (USA)
'''


def form(**fields):
    labels = {'game': 'Game', 'serial': 'Disc serial', 'region': 'Region', 'status': 'Status',
              'version': 'Core version', 'notes': 'Notes'}
    return '\n\n'.join(f'### {labels[k]}\n\n{v or "_No response_"}' for k, v in fields.items())


def issue(number, labels=(), state_reason=None, user=None, **fields):
    return {'number': number, 'user': {'login': user or f'tester{number}'}, 'title': f'[Compatibility] {fields["game"]}', 'body': form(**fields),
            'created_at': f'2026-10-{number:02d}T00:00:00Z', 'html_url': f'https://example.invalid/{number}',
            'labels': [{'name': name} for name in ('compatibility', *labels)], 'state_reason': state_reason}


@unittest.skipUnless(NODE, 'needs node')
class CompatPage(unittest.TestCase):
    def run_page(self, issues, seeds, games=GAMES, released=None, votes=None, core=None):
        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp/'issues.json').write_text(json.dumps(issues))
            (tmp/'seeds.json').write_text(json.dumps(seeds))
            args = [NODE, str(ROOT/'tests/compat_page.cjs'), str(tmp/'issues.json'), str(tmp/'seeds.json')]
            if votes is not None:
                (tmp/'votes.json').write_text(json.dumps(votes))
                args.append('--votes=' + str(tmp/'votes.json'))
            if released is not None:
                (tmp/'released.json').write_text(json.dumps(released))
                args.append('--released=' + str(tmp/'released.json'))
            if core is not None:
                args.append('--core=' + core)
            if games is not None:
                (tmp/'games.tsv').write_text(games)
                args.append(str(tmp/'games.tsv'))
            out = json.loads(subprocess.run(args, check=True, capture_output=True, text=True).stdout)
        return {(e['name'], e['region']): e for e in out['entries']}, out['search']

    def test_groups_by_serial_name_and_disc(self):
        entries, search = self.run_page([
            issue(1, game='FF7', serial='scus 94164', region='USA', status='Minor issues (glitches)', version='0.5.3'),
            issue(2, game='Final Fantasy 7', serial='', region='USA', status='Playable (fine)', version='0.5.3'),
            issue(3, game='Final Fantasy VII', serial='SCES-10867', region='USA', status='Playable', version='0.5.2'),
            issue(4, game='Crash Bandicoot 2', serial='', region='USA', status='Playable', version='0.5.3'),
            issue(5, game='Spam', serial='', region='USA', status='Playable', version='1', labels=('invalid',)),
            issue(6, game='Tekken 3', serial='', region='USA', status='Playable', version='1', state_reason='not_planned'),
            issue(7, game='Unlisted Game', serial='SLPS-99999', region='USA', status="Doesn't boot (black)", version='0.5.3'),
            issue(8, game='Unlisted game', serial='', region='USA', status='Boots', version='0.5.3'),
        ], [
            {'game': 'Final Fantasy VII', 'serial': 'SCUS-94163', 'status': 'Playable', 'tested': '0.5.0', 'version': '0.5.0'},
            {'game': 'Crash Bandicoot 2: Cortex Strikes Back', 'status': 'Playable', 'tested': 'user report', 'version': '0.5'},
        ])
        ff7 = entries[('Final Fantasy VII', 'USA')]
        self.assertEqual(ff7['numbers'], [2, 1, None])   # disc 2's serial and the "7" spelling join it
        self.assertEqual(ff7['status'], 'Minor issues')    # worst on the newest version
        self.assertEqual(ff7['serials'], ['SCUS-94163', 'SCUS-94164'])
        # The serial decides the region over the dropdown.
        self.assertEqual(entries[('Final Fantasy VII', 'Europe')]['numbers'], [3])
        # A seed without a region joins the game's only regional entry.
        self.assertEqual(entries[('Crash Bandicoot 2 - Cortex Strikes Back', 'USA')]['numbers'], [4, None])
        self.assertNotIn(('Spam', 'USA'), entries)
        self.assertNotIn(('Tekken 3', 'USA'), entries)
        # Same name, but a serial puts it in another region.
        self.assertEqual(entries[('Unlisted game', 'USA')]['numbers'], [8])
        self.assertEqual(entries[('Unlisted Game', 'Japan')]['status'], "Doesn't boot")
        self.assertEqual(search[0], 'Final Fantasy VII')

    def test_one_account_cannot_sway_a_game(self):
        entries, _ = self.run_page([
            issue(1, game='Tekken 3', serial='SLUS-00402', region='USA', status='Playable', version='0.5.3'),
            issue(2, game='Tekken 3', serial='SLUS-00402', region='USA', status='Playable', version='0.5.3'),
            issue(3, game='Tekken 3', serial='', region='USA', status="Doesn't boot", version='0.5.3', user='troll'),
            issue(4, game='Tekken 3', serial='', region='USA', status="Doesn't boot", version='0.5.3', user='Troll'),
            issue(5, game='Tekken 3', serial='', region='USA', status="Doesn't boot", version='9.9', user='other'),
        ], [], released=['0.5.3', '0.5.2'])
        tekken = entries[('Tekken 3', 'USA')]
        self.assertEqual(tekken['numbers'], [4, 2, 1, 5])   # troll's newest only; 9.9 ranks last
        self.assertEqual(tekken['status'], 'Playable')       # two against one

    def test_votes_decide(self):
        backed = issue(1, game='Tekken 3', serial='SLUS-00402', region='USA', status='Minor issues', version='0.5.3')
        backed['reactions'] = {'+1': 2, '-1': 0}
        rejected = issue(2, game='Tekken 3', serial='', region='USA', status="Doesn't boot", version='0.5.3')
        rejected['reactions'] = {'+1': 0, '-1': 2}
        entries, _ = self.run_page([
            backed, rejected,
            issue(3, game='Tekken 3', serial='', region='USA', status='Playable', version='0.5.3'),
            issue(4, game='Tekken 3', serial='', region='USA', status='Playable', version='0.5.3'),
        ], [])
        tekken = entries[('Tekken 3', 'USA')]
        self.assertNotIn(2, tekken['numbers'])
        self.assertEqual(tekken['status'], 'Minor issues')   # 1 + 2 votes against 2

    def test_votes_on_the_list(self):
        entries, _ = self.run_page([
            issue(1, game='Final Fantasy VII', serial='SCUS-94163', region='USA', status="Doesn't boot", version='0.5.2'),
        ], [{'game': 'Tekken 3', 'region': 'USA', 'status': 'Playable', 'version': '0.5.0'}], votes={
            'finalfantasy7|USA|0.5.3': {'Playable': 3, 'Minor issues': 1},
            'crashbandicoot2cortexstrikesback|USA|0.5.3': {'Boots': 2},
            'notalistedgame|USA|0.5.3': {'Playable': 5},
            'tekken3|USA|0.5.3': {'Minor issues': 1},
        })
        ff7 = entries[('Final Fantasy VII', 'USA')]
        self.assertEqual((ff7['status'], ff7['version']), ('Playable', '0.5.3'))   # the newer version's votes decide
        self.assertEqual(ff7['tally'], {'Playable': 3, 'Minor issues': 1})
        crash = entries[('Crash Bandicoot 2 - Cortex Strikes Back', 'USA')]  # votes alone make an entry
        self.assertEqual((crash['status'], crash['numbers']), ('Boots', []))
        self.assertNotIn(('notalistedgame', 'USA'), entries)
        self.assertEqual(entries[('Tekken 3', 'USA')]['status'], 'Minor issues')

    def test_status_on_one_core_version(self):
        reports = [issue(1, game='Final Fantasy VII', serial='SCUS-94163', region='USA', status="Doesn't boot", version='0.5.2')]
        votes = {'finalfantasy7|USA|0.5.3': {'Playable': 3}, 'tekken3|USA|0.5.3': {'Boots': 1}}
        entries, _ = self.run_page(reports, [], released=['0.5.3', '0.5.2'], votes=votes, core='0.5.2')
        ff7, tekken = entries[('Final Fantasy VII', 'USA')], entries[('Tekken 3', 'USA')]
        self.assertEqual((ff7['status'], ff7['version']), ("Doesn't boot", '0.5.2'))   # the newer votes don't count
        self.assertEqual((tekken['status'], tekken['version']), ('', ''))              # nobody rated it on 0.5.2
        entries, _ = self.run_page(reports, [], released=['0.5.3', '0.5.2'], votes=votes, core='0.5.1')
        self.assertEqual(entries[('Final Fantasy VII', 'USA')]['status'], '')

    def test_regional_entry_replaces_one_without_region(self):
        entries, _ = self.run_page([], [
            {'game': 'Crash Bandicoot 2: Cortex Strikes Back', 'status': 'Playable', 'version': '0.5'},
            {'game': 'Final Fantasy VII', 'status': 'Boots', 'version': '0.5'},
        ], votes={
            'crashbandicoot2cortexstrikesback|USA|0.5.3': {'Minor issues': 2},
            'finalfantasy7|USA|0.5.3': {'Playable': 1},
            'finalfantasy7|Europe|0.5.3': {'Major issues': 1},
        })
        self.assertEqual(sorted(k for k in entries if k[0].startswith(('Crash', 'Final'))), [
            ('Crash Bandicoot 2 - Cortex Strikes Back', 'USA'), ('Final Fantasy VII', 'Europe'), ('Final Fantasy VII', 'USA')])
        crash = entries[('Crash Bandicoot 2 - Cortex Strikes Back', 'USA')]
        self.assertEqual((crash['status'], crash['version'], len(crash['numbers'])), ('Minor issues', '0.5.3', 1))
        self.assertEqual(entries[('Final Fantasy VII', 'Europe')]['status'], 'Major issues')

    def test_game_in_one_region_takes_it(self):
        entries, _ = self.run_page([], [{'game': 'Tekken 3', 'status': 'Playable', 'version': '0.5'},
                                        {'game': 'Final Fantasy VII', 'status': 'Boots', 'version': '0.5'}])
        self.assertEqual(entries[('Tekken 3', 'USA')]['status'], 'Playable')   # only listed for USA
        self.assertIn(('Final Fantasy VII', ''), entries)                     # USA and Europe: the player picks

    def test_resolved_problem_no_longer_sets_status(self):
        fixed = {**issue(1, game='Tekken 3', serial='SLUS-00402', region='USA', status='Major issues', version='0.5.3'),
                 'state': 'closed', 'state_reason': 'completed'}
        entries, _ = self.run_page([fixed], [], votes={'tekken3|USA|0.5.3': {'Playable': 1}})
        tekken = entries[('Tekken 3', 'USA')]
        self.assertEqual((tekken['status'], tekken['tally'], tekken['numbers']), ('Playable', {'Playable': 1}, [1]))

    def test_works_without_the_game_list(self):
        entries, _ = self.run_page([
            issue(1, game='Tekken 3', serial='SLUS-00402', region='USA', status='Playable', version='0.5.3'),
            issue(2, game='tekken 3', serial='', region='USA', status='Boots', version='0.5.3'),
        ], [], games=None)
        self.assertEqual(entries[('Tekken 3', 'USA')]['numbers'], [2, 1])

    def test_shipped_files_load(self):
        seeds = json.loads((DOCS/'compatibility.json').read_text())
        entries, search = self.run_page([], seeds, (DOCS/'games.tsv').read_text())
        self.assertGreaterEqual(len(entries), 30)
        self.assertEqual(search[0], 'Final Fantasy VII')

    def test_shipped_entries_can_take_votes(self):
        # Each old entry matches a listed game, so a region can be picked to vote on it.
        script = '''
          const C = require(process.argv[1]), fs = require('fs');
          const games = C.loadGames(fs.readFileSync(process.argv[2], 'utf8'));
          const seeds = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
          const lost = C.build([], seeds, games, [], {}).filter((e) => e.key ? !games.byKey.has(e.key)
            : !C.REGIONS.some((r) => games.byKey.has(`${C.nameKey(e.name)}|${r}`)));
          console.log(JSON.stringify(lost.map((e) => e.name)));'''
        out = subprocess.run([NODE, '-e', script, str(DOCS/'compat.js'), str(DOCS/'games.tsv'),
                              str(DOCS/'compatibility.json')], check=True, capture_output=True, text=True).stdout
        self.assertEqual(json.loads(out), [])

    def fetch_versions(self, releases, tags, service=None):
        # service: the vote service's /versions answer; None = it can't be reached.
        script = f"""
const Compat = require({json.dumps(str(DOCS/'compat.js'))});
const answers = {{ releases: {json.dumps(releases)}, tags: {json.dumps(tags)}, versions: {json.dumps(service)} }};
const fake = async (url) => {{
  const answer = answers[url.split('/').pop().split('?')[0]];
  if (answer === null) throw new Error('unreachable');
  return {{ ok: true, json: async () => answer }};
}};
Compat.fetchVersions(fake).then((v) => console.log(JSON.stringify(v)), (e) => console.log(JSON.stringify(null)));
"""
        return json.loads(subprocess.run([NODE, '-e', script], check=True, capture_output=True, text=True).stdout)

    def test_versions_from_releases_or_tags(self):
        release = lambda tag, pre=False: {'tag_name': tag, 'draft': False, 'prerelease': pre}
        self.assertEqual(self.fetch_versions([release('v0.5.4'), release('v0.5.10'), release('v0.6.0', True)], []),
                         ['0.5.10', '0.5.4'])
        # GitHub's signed-out release list can come back empty; the tags stand in.
        self.assertEqual(self.fetch_versions([], [{'name': 'v0.5.5'}, {'name': 'v0.5.4'}, {'name': 'test6'}]),
                         ['0.5.5', '0.5.4'])
        self.assertIsNone(self.fetch_versions([], []))
        # The vote service's list wins, so the page offers what the service accepts.
        self.assertEqual(self.fetch_versions([release('v0.5.4')], [], {'versions': ['0.5.5', '0.5.6']}), ['0.5.6', '0.5.5'])
        self.assertEqual(self.fetch_versions([release('v0.5.4')], [], {'versions': []}), ['0.5.4'])

if __name__ == '__main__':
    unittest.main()
