#!/usr/bin/env python3
"""Turn a packaged Pocket build into the public release zip.

Takes the output folder of package_pocket_early.py (built with --platform
playstation --disc --memcard-slot) and makes PlayStation-<version>.zip:
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

AUTHOR = 'GuardianSoul'
REPO = f'https://github.com/{AUTHOR}/openfpga-PlayStation'
SOURCE_AUTHORS = ('GuardianSoul', AUTHOR)   # authors of workspace packages, older and current
PLATFORM = 'playstation'
LICENSES = (                       # (source, name in the core's licenses folder)
    (ROOT/'LICENSE', 'LICENSE-GPL-2.0.txt'),
    (ROOT/'licenses/MIT.txt', 'MIT.txt'),
    (ROOT/'licenses/dual-clock-fifo-BSD.txt', 'dual-clock-fifo-BSD.txt'),
    (ROOT/'references/MIRLO64/NOTICE', 'MIRLO64-NOTICE.txt'),   # agg23 / openfpga-litex MIT attributions
)
INFO = """PlayStation core for Analogue Pocket, by {author}.
Built on the MiSTer PSX core. Early release: Final Fantasy VII is the
reference game. See the README for compatibility, controls and memory cards.

You need your own BIOS (Assets/playstation/common/scph5501.bin) and your own
games. None are included.

Source code, credits and licenses: {repo}
The core is GPL-2.0; see the licenses folder.
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

    meta = load('core.json')
    meta['core']['metadata'].update(platform_ids=[PLATFORM], shortname='PlayStation',
        description='PlayStation core built on the MiSTer PSX core', author=AUTHOR, url=REPO,
        version=args.version, date_release=args.date)
    write('core.json', meta)
    if (core/'instance-packager.json').is_file():
        packager = load('instance-packager.json')
        packager['output'] = f'Assets/{PLATFORM}/{AUTHOR}.PlayStation'
        write('instance-packager.json', packager)
    (core/'info.txt').write_text(INFO.format(author=AUTHOR, repo=REPO))

    notices = core/'licenses'; shutil.rmtree(notices, ignore_errors=True); notices.mkdir()
    for source, name in LICENSES: shutil.copyfile(source, notices/name)
    shutil.copyfile(ROOT/'THIRD_PARTY_NOTICES.md', notices/'THIRD_PARTY_NOTICES.md')

    (sd/'Platforms').mkdir(parents=True)
    (sd/'Platforms'/f'{PLATFORM}.json').write_text(json.dumps({'platform': {
        'category': 'Console', 'name': 'PlayStation', 'year': 1994, 'manufacturer': 'Sony'}}, indent=2)+'\n')
    assets = sd/'Assets'/PLATFORM/'common'; assets.mkdir(parents=True)
    (assets/'PLACE-BIOS-HERE.txt').write_text(
        'Place your own PlayStation BIOS dump here as scph5501.bin (US console, 524288 bytes).\n'
        'Put each game disc (.cue and .bin files) here in its own folder, then make the\n'
        'game files with pupdate or Pocket Sync (see the README). No BIOS or game is included.\n')

    names = [a for a in SOURCE_AUTHORS if a != AUTHOR]
    private = re.compile('|'.join([*map(re.escape, names), r'/(?:home|run/media|tmp)/']), re.I)
    for path in sorted(sd.rglob('*')):
        if path.is_file() and path.suffix != '.rbf_r':
            text = path.read_text(errors='replace')
            if private.search(text) or private.search(str(path.relative_to(sd))):
                raise SystemExit(f'workspace name left in {path.relative_to(sd)}')
    archive_path = out/f'PlayStation-{args.version}.zip'
    with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(sd.rglob('*')):
            if path.is_file(): archive.write(path, path.relative_to(sd))
    (out/'release-report.json').write_text(json.dumps(dict(version=args.version, source_package=str(package),
        rbf_r_sha256=report.get('rbf_r_sha256'), fit_summary=report.get('fit_summary'),
        timing_waived=timing['waived']), indent=2)+'\n')
    print(archive_path)


if __name__ == '__main__': main()
