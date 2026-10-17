#!/usr/bin/env python3
"""Turn a packaged Pocket build into the public release zip.

Takes the output folder of package_pocket_early.py (built with --platform
playstation --disc --memcard-slot) and makes
GuardianSoul.PlayStation_<version>_<date>.zip (Analogue's recommended name):
the core is renamed to <author>.PlayStation, its metadata and info text are
the public ones, and every license the bitstream needs goes into the core
folder. It refuses a build that failed release_timing.py, a debug slot, a
missing memory card slot or any workspace name left in the package.

  python3 scripts/package_release.py build/pocket/early-hw/package --version 0.1.0
"""
import argparse
import datetime
import json
from pathlib import Path
import re
import shutil
import zipfile

from tool_paths import ROOT
import build_chip32_loader
import pocket_image

AUTHOR = 'GuardianSoul'
REPO = f'https://github.com/{AUTHOR}/openfpga-PlayStation'
PLUGIN = f'https://github.com/{AUTHOR}/openfpga-PlayStation-plugin'
PAGES = f'https://{AUTHOR.lower()}.github.io/openfpga-PlayStation/'
SOURCE_AUTHORS = ('GuardianSoul', AUTHOR)   # authors of workspace packages, older and current
PLATFORM = 'playstation'
# A placeholder in the game list: the core only lists .json game files, and
# users who copy .cue/.bin files without making them see no games at all.
# Its missing cue's name is the instruction. make_pocket_instances.py and the
# SD card helper delete it once they write a game file.
HELP_ENTRY = '! How to add games (see README).json'
HELP_CUE = 'Put each game in its own folder in common, then run the PlayStation game files plugin.cue'
LICENSES = (                       # (source, name in the core's licenses folder)
    (ROOT/'LICENSE', 'LICENSE-GPL-2.0.txt'),
    (ROOT/'licenses/MIT.txt', 'MIT.txt'),
    (ROOT/'licenses/dual-clock-fifo-BSD.txt', 'dual-clock-fifo-BSD.txt'),
    (ROOT/'licenses/APF-NOTICE.txt', 'APF-NOTICE.txt'),   # APF template, Intel IP and agg23 MIT notices
    (ROOT/'references/PSX_MiSTer/LICENSE', 'PSX_MiSTer-LICENSE.txt'),
    # Not MIRLO64's NOTICE or Apache-2.0 LICENSE, or the LiteX notice: their memory files
    # are workspace-only and must not be in a GPL-2.0 release bitstream.
)
# One line per paragraph: the Pocket wraps info.txt itself.
INFO = """PlayStation core for Analogue Pocket, by {author}. A general PlayStation core built on the MiSTer PSX core.

You need your own BIOS (for example Assets/playstation/common/scph5501.bin) and your own games; none are included. Put each game in its own folder in Assets/playstation/common, then make the game files with the PlayStation game files plugin in pupdate or Pocket Sync, and run it again whenever you add games. Only for a disc that has no game file yet: press Back in the game list and pick its .cue (not a .bin, which loses CD music).

Hold Select with L/R for L2/R2, with the D-Pad for the left analog stick and with A/B/X/Y for the right one; Enable DualShock (on by default) takes effect when the game starts. Core Settings change all of this.

See the README for setup, controls, multi-disc games and memory cards. Source code, credits and licenses: {repo}. The core is GPL-2.0; see the licenses folder.
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('package', type=Path, help='package_pocket_early.py output folder')
    parser.add_argument('--version', required=True)
    parser.add_argument('--date', default=datetime.date.today().isoformat())
    parser.add_argument('--output', type=Path, default=ROOT/'build/release/package')
    parser.add_argument('--timing', type=Path, default=ROOT/'build/release/timing.json', help='release_timing.py result')
    args = parser.parse_args()
    if not re.fullmatch(r'\d+\.\d+\.\d+', args.version): parser.error('version must look like 0.1.0')
    package = args.package.resolve()
    report = json.loads((package/'build-report.json').read_text())
    # The package's per-clock summary can't tell a clock crossing from a same-clock
    # path, so timing is judged by release_timing.py's per-pair table instead.
    timing = json.loads(args.timing.read_text())
    if not timing.get('passed'): parser.error(f'{args.timing}: release timing did not pass')
    if Path(timing['project']).resolve() != Path(report['project']).resolve():
        parser.error(f'{args.timing} is for {timing["project"]}, the package for {report["project"]}')
    cores = sorted({c for author in SOURCE_AUTHORS for c in (package/'SD/Cores').glob(f'{author}.*')})
    if len(cores) != 1: parser.error(f'expected one core in {package}/SD/Cores, found {len(cores)}')
    if not (package/f'SD/Platforms/{PLATFORM}.json').is_file(): parser.error('package must use --platform playstation')

    out = args.output.resolve(); sd = out/'SD'
    if out.exists(): shutil.rmtree(out)
    core = sd/'Cores'/f'{AUTHOR}.PlayStation'
    shutil.copytree(cores[0], core)
    def load(name): return json.loads((core/name).read_text())
    def write(name, value): (core/name).write_text(json.dumps(value, indent=2)+'\n')

    data = load('data.json')
    slots = {int(str(s['id']), 0): s for s in data['data']['data_slots']}
    if 0x10 not in slots or not slots[0x10].get('nonvolatile'): parser.error('memory card slot missing (--memcard-slot)')
    if 1 in slots: parser.error('boot-log debug slot present; rebuild without --boot-log')
    if 'Disc' not in slots[0]['name'] and 'Game' not in slots[0]['name']: parser.error('disc slot missing (--disc)')
    for slot in data['data']['data_slots']:
        if slot.get('filename') == 'bios.bin': slot['filename'] = 'scph5501.bin'
    write('data.json', data)
    # The BIOS check names the file; rebuild it for the renamed slot.
    if 'chip32_vm' in load('core.json')['core']['framework']:
        extra = lambda s: 0x21 <= int(str(s['id']), 0) <= 0x2f     # --bios-names slots, made again
        names = any(map(extra, data['data']['data_slots']))
        data['data']['data_slots'] = [s for s in data['data']['data_slots'] if not extra(s)]
        write('data.json', data)
        build_chip32_loader.apply(core, PLATFORM, names)

    meta = load('core.json')
    meta['core']['metadata'].update(platform_ids=[PLATFORM], shortname='PlayStation',
        description='PlayStation core built on the MiSTer PSX core', author=AUTHOR, url=REPO,
        version=args.version, date_release=args.date)
    write('core.json', meta)
    # Updaters read updaters.json from the core folder; "plugins" lets pupdate
    # and Pocket Sync suggest the game files plugin with the core (agreed on
    # issue #3; unknown keys pass through until they support it).
    write('updaters.json', {'plugins': [PLUGIN]})
    if (core/'instance-packager.json').is_file():
        packager = load('instance-packager.json')
        packager['output'] = f'Assets/{PLATFORM}/{AUTHOR}.PlayStation'
        write('instance-packager.json', packager)
    (core/'info.txt').write_text(INFO.format(author=AUTHOR, repo=REPO))

    notices = core/'licenses'; shutil.rmtree(notices, ignore_errors=True); notices.mkdir()
    for source, name in LICENSES: shutil.copyfile(source, notices/name)
    # The opt-in pocket_sotn files are never in a release (from 0.5.3), so its row goes too.
    (notices/'THIRD_PARTY_NOTICES.md').write_text(''.join(
        l for l in (ROOT/'THIRD_PARTY_NOTICES.md').read_text().splitlines(keepends=True) if 'pocket_sotn' not in l))

    (sd/'Platforms').mkdir(parents=True)
    (sd/'Platforms/_images').mkdir()
    (sd/'Platforms/_images'/f'{PLATFORM}.bin').write_bytes(
        pocket_image.encode(ROOT/'support/pocket-art/playstation-banner.png', (521, 165)))
    (core/'icon.bin').write_bytes(pocket_image.encode(ROOT/'support/pocket-art/core-icon.png', (36, 36)))
    (sd/'Platforms'/f'{PLATFORM}.json').write_text(json.dumps({'platform': {
        'category': 'Console', 'name': 'PlayStation', 'year': 1994, 'manufacturer': 'Sony Computer Entertainment'}}, indent=2)+'\n')
    assets = sd/'Assets'/PLATFORM/'common'; assets.mkdir(parents=True)
    (assets/'PLACE-BIOS-HERE.txt').write_text(
        'Place your own PlayStation BIOS dump here as scph5501.bin (US console, 524288 bytes).\n'
        'No BIOS or game is included.\n')
    (assets/'HOW-TO-ADD-GAMES.txt').write_text(
        '1. Put each disc here in its own folder, with its .cue and all its .bin files, for example '
        'Assets/playstation/common/Tekken 3 (USA)/Tekken 3 (USA).cue. A European disc with LibCrypt copy protection '
        'also needs its .sbi file from redump.org, named like the .cue, in the same folder before step 2.\n\n'
        '2. Make the game files with the PlayStation game files plugin '
        '(https://github.com/GuardianSoul/openfpga-PlayStation-plugin): in pupdate under '
        'Pocket Extras > Plugins, or in Pocket Sync under Plugins. Run it again whenever you add games, '
        'and after pupdate or Pocket Sync have made game files of their own. '
        f'No pupdate or Pocket Sync? The SD card helper page does the same: {PAGES}\n\n'
        '3. In the core, pick the game.\n\n'
        'Only when a disc has no game file yet: press Back in the game list and pick the disc\'s .cue (not a .bin, which loses CD music). '
        'It gets its own memory card and no disc swapping.\n\n'
        'See the README for details.\n')
    games = sd/'Assets'/PLATFORM/core.name; games.mkdir(parents=True)
    (games/HELP_ENTRY).write_text(json.dumps({'instance': {'magic': 'APF_VER_1', 'data_path': '',
        'data_slots': [{'id': 100, 'filename': HELP_CUE}]}}, indent=2)+'\n')

    names = [a for a in SOURCE_AUTHORS if a != AUTHOR]
    private = re.compile('|'.join([*map(re.escape, names), r'/(?:home|run/media|tmp)/']), re.I)
    for path in sorted(sd.rglob('*')):
        if path.is_file() and path.suffix != '.rbf_r':
            text = path.read_text(errors='replace')
            if private.search(text) or private.search(str(path.relative_to(sd))):
                raise SystemExit(f'workspace name left in {path.relative_to(sd)}')
    archive_path = out/f'{AUTHOR}.PlayStation_{args.version}_{args.date}.zip'
    with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(sd.rglob('*')):
            if path.is_file(): archive.write(path, path.relative_to(sd))
    (out/'release-report.json').write_text(json.dumps(dict(version=args.version, source_package=str(package),
        rbf_r_sha256=report.get('rbf_r_sha256'), fit_summary=report.get('fit_summary'),
        timing_waived=timing['waived']), indent=2)+'\n')
    print(archive_path)


if __name__ == '__main__': main()
