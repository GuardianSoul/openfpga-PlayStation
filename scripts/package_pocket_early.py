#!/usr/bin/env python3
"""Package an early full-PSX hardware smoke-test core for the Pocket SD card.

This is for surfacing hardware blind spots (SDRAM/PSRAM, APF loading, video
out, clock crossings) on a real Pocket. Unlike the probe packagers it accepts
a fit with reported timing violations, but only with --allow-timing-failure,
and records every reported slack in the package. No BIOS or game is included.
"""
import argparse
import json
from pathlib import Path
import re
import shutil
import zipfile

from prepare_resource_targets import digest
from tool_paths import ROOT

PLATFORM = 'psxearly'
CORE = 'GuardianSoul.PSXEarly'
# APF cont1_key bits as wired by psx_pocket_pad / core_top.
BUTTONS = [('pad_btn_a', 'Circle'), ('pad_btn_b', 'Cross'), ('pad_btn_x', 'Triangle'),
    ('pad_btn_y', 'Square'), ('pad_trig_l', 'L1'), ('pad_trig_r', 'R1'),
    ('pad_btn_select', 'Select'), ('pad_btn_start', 'Start')]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project', type=Path)
    parser.add_argument('--output', type=Path, default=ROOT/'build/pocket/early-hw/package')
    parser.add_argument('--version', default='0.0.1')
    parser.add_argument('--allow-timing-failure', action='store_true')
    parser.add_argument('--memcard-slot', action='store_true', help='Memory card 1 save slot (prepare --memcard-slot)')
    parser.add_argument('--disc-menu', type=int, metavar='DISCS', help='prepare --disc-menu: declare discs 2..DISCS (slots n01, n02, ...; tracks per extra disc as the 32-slot table allows) and the "Disc" menu list')
    parser.add_argument('--libcrypt', action='store_true', help='Declare data slot 99 for a disc\'s LibCrypt .sbi (prepare --libcrypt; make_pocket_instances.py fills it)')
    parser.add_argument('--dualshock', action='store_true', help='Menu checkbox "DualShock" (prepare --dualshock, bridge 0x50000040, on by default) and the analog notes in info.txt')
    parser.add_argument('--pad-combos', action='store_true', help='Explain the Select combos in info.txt (prepare --pad-combos); the Controls list stays plain because combos cannot be remapped')
    parser.add_argument('--boot-log', action='store_true', help='Add the nonvolatile boot-trace slot (prepare --boot-trace)')
    parser.add_argument('--disc', action='store_true', help='Disc slot 0 (.bin from SD, prepare --cd-stream); BIOS moves to slot 0x20')
    parser.add_argument('--multi-track', type=int, default=0, help='Discs as instance JSONs: cue slot 100, track bins 101..100+N (prepare --multi-track N)')
    parser.add_argument('--logo-skip-setting', action='store_true', help='Menu checkbox to skip the BIOS logo (prepare --logo-skip-setting)')
    parser.add_argument('--field-swap-setting', action='store_true', help='Menu checkbox to swap the 480i field order (prepare --field-swap-setting)')
    parser.add_argument('--shoulder-setting', action='store_true', help='Menu lists for what L and R press (prepare --shoulder-setting)')
    parser.add_argument('--weave-setting', action='store_true', help='Menu checkbox for 480i weave (prepare --weave-setting)')
    parser.add_argument('--pal288', action='store_true', help='With --slots480: slot 7 is 320x288 for PAL instead of 368x480 (prepare --pal288)')
    parser.add_argument('--video-test-setting', action='store_true', help='Diagnostic Settings list "Video test" (prepare --video-test-setting)')
    parser.add_argument('--slots480', action='store_true', help='Add 640/512/368 x 480 scaler slots 5-7 (prepare --slots480)')
    parser.add_argument('--platform', default=PLATFORM, help="Platform id; 'playstation' uses the existing Assets/playstation/common folder")
    parser.add_argument('--bios-name', default='bios.bin')
    parser.add_argument('--variant', default='', help='Core name suffix and log name, for several builds on one card')
    args = parser.parse_args()
    project = args.project.resolve(); out = args.output.resolve()
    fit = (project/'output_files/apf_top.fit.summary').read_text()
    if 'Fitter Status : Successful' not in fit or 'Total virtual pins : 0' not in fit:
        parser.error('Successful physical fit required')
    timing = (project/'output_files/apf_top.sta.summary').read_text()
    slacks = [(k, float(v)) for k, v in re.findall(r'Type\s*:\s*(.*?)\nSlack\s*:\s*(-?[\d.]+)', timing)]
    if not slacks: parser.error('Timing summary missing')
    failing = [s for s in slacks if s[1] < 0]
    if failing and not args.allow_timing_failure:
        parser.error(f'{len(failing)} clocks have negative slack; pass --allow-timing-failure')
    rbf = project/'output_files/apf_top.rbf'
    if not rbf.is_file() or rbf.stat().st_size < 10000: parser.error('Assembler RBF missing')
    if rbf.stat().st_mtime < (project/'output_files/apf_top.fit.summary').stat().st_mtime:
        parser.error('RBF is older than the fit')
    if out.exists(): shutil.rmtree(out)
    name = 'PSXEarly' + args.variant
    sd = out/'SD'; core = sd/'Cores'/f'GuardianSoul.{name}'; core.mkdir(parents=True)
    original = rbf.read_bytes(); table = bytes(int(f'{b:08b}'[::-1], 2) for b in range(256))
    converted = original.translate(table)
    if converted.translate(table) != original: raise ValueError('Bit reversal round trip failed')
    (core/'bitstream.rbf_r').write_bytes(converted)
    def write(name, value): (core/name).write_text(json.dumps(value, indent=2)+'\n')
    write('core.json', {'core': {'magic': 'APF_VER_1', 'metadata': {
        'platform_ids': [args.platform], 'shortname': name,
        'description': 'Early PlayStation core' if args.disc else 'Early PlayStation hardware test: BIOS boot only',
        'author': 'GuardianSoul', 'url': 'https://github.com/GuardianSoul/openfpga-PlayStation',
        'version': args.version, 'date_release': '2026-10-14'},
        'framework': {'target_product': 'Analogue Pocket', 'version_required': '1.1',
            'sleep_supported': False, 'dock': {'supported': True, 'analog_output': False},
            'hardware': {'link_port': False, 'cartridge_adapter': -1}},
        'cores': [{'name': 'default', 'id': 0, 'filename': 'bitstream.rbf_r'}]}})
    # psx_bios_loader accepts slot id 0 at bridge address 0, exactly 512 KiB.
    slots = [{'name': 'BIOS', 'id': 0, 'required': True, 'parameters': '0x008', 'deferload': False,
        'filename': args.bios_name, 'extensions': ['bin'], 'size_exact': 524288, 'address': '0x00000000'}]
    if args.disc:
        # support/pocket_psx_data.json: deferred, user-reloadable disc in slot 0.
        slots = [{'name': 'Disc', 'id': 0, 'required': True, 'parameters': '0x09', 'deferload': True,
                  'extensions': ['bin'], 'size_maximum': '0x40000000', 'address': '0x24000000'},
                 dict(slots[0], id='0x20')]
    if args.libcrypt and not args.multi_track: parser.error('--libcrypt needs --multi-track')
    if args.multi_track:
        if not args.disc: parser.error('--multi-track needs --disc')
        # The PC Engine CD core's Pocket convention: an instance JSON (made by
        # scripts/make_pocket_instances.py, or Pocket Sync from
        # instance-packager.json) puts the cue in slot 100 and the track
        # files in 101, 102, ... Declared here so their sizes reach the core.
        # Slot 0 takes the instance JSON (parameters 0x13: reloadable,
        # core-specific, Instance JSON), as the PC Engine CD core's JSON-era
        # releases did; the JSON fills the cue and track slots.
        # With --disc-menu, discs change from the Disc setting and a runtime
        # game-file reload goes black on hardware, so the slot is not
        # user-reloadable (bit 0 clear): no "Load game" entry in Core Settings.
        slots = [{'name': 'Game', 'id': 0, 'required': True, 'parameters': '0x12' if args.disc_menu else '0x13', 'extensions': ['json']},
                 {'name': 'Disc (cue)', 'id': 100, 'required': False, 'parameters': '0x08', 'deferload': True,
                  'extensions': ['cue'], 'size_maximum': '0x4000'}, slots[1]]
        slots += [{'name': f'Track {k}', 'id': 100+k, 'required': False, 'parameters': '0x08', 'deferload': True,
                   'extensions': ['bin'], 'size_maximum': '0x40000000'} for k in range(1, args.multi_track+1)]
        write('instance-packager.json', {'output': f'Assets/{args.platform}/GuardianSoul.{name}', 'platform_id': args.platform,
            'slot_limit': {'count': args.multi_track+1+bool(args.libcrypt),
                           'message': f'Games with more than {args.multi_track} track files are not supported yet.'},
            'data_slots': [{'id': 100, 'filename': '*.cue', 'sort': 'single', 'required': True, 'as_filename': True},
                           {'id': 101, 'filename': '*.bin', 'sort': 'ascending', 'required': True}]
                          + ([{'id': 99, 'filename': '*.sbi', 'sort': 'single', 'required': False}] if args.libcrypt else [])})
        if args.disc_menu:
            # prepare --disc-menu: discs 2.. of a game in the same game file
            # (make_pocket_instances.py groups "(Disc n)" cues); track k of
            # disc n in slot 100*n+k. APF tables hold 32 slots, so later
            # discs get the tracks that remain (most multi-disc games have one).
            free = 32 - len(slots) - 1 - (1 if args.memcard_slot else 0) - (1 if args.boot_log else 0) - (1 if args.libcrypt else 0)
            per_disc = max(1, free // (args.disc_menu - 1))
            for n in range(2, args.disc_menu + 1):
                slots += [{'name': f'Disc {n} track {k}', 'id': 100*n+k, 'required': False, 'parameters': '0x08', 'deferload': True,
                           'extensions': ['bin'], 'size_maximum': '0x40000000'} for k in range(1, per_disc+1)]
        if args.libcrypt:
            # prepare --libcrypt: psx_cd_stream reads the .sbi (at most 2,352
            # bytes) from this slot at every mount; absent means no LibCrypt.
            slots.append({'name': 'LibCrypt (sbi)', 'id': 99, 'required': False, 'parameters': '0x08', 'deferload': True,
                          'extensions': ['sbi'], 'size_maximum': '0x930'})
    if args.boot_log:
        # psx_boot_trace: the Pocket saves this range when the core quits.
        # Second in the list: the core writes its size into data-table entry 3
        # (pairs of id, size in data.json order).
        slots.insert(1, {'name': 'Boot log', 'id': 1, 'required': False, 'parameters': '0x00',
            'nonvolatile': True, 'filename': f'boot-log{args.variant.lower()}.bin', 'extensions': ['bin'],
            'size_maximum': 28672, 'address': '0x40000000'})
    if args.memcard_slot:
        # Memory card 1 (prepare --memcard-slot): 128 KiB raw card image,
        # loaded into the core at start and saved back when it quits, as
        # pocket_sotn's PSXPort does (parameters 0xA4: nonvolatile, created on
        # first start, core restarted after a reload). Second in the list:
        # the core writes its size into data-table entry 3, the boot log's
        # into entry 5.
        slots.insert(1, {'name': 'Memory Card 1', 'id': 0x10, 'required': False, 'parameters': '0xA4',
            'nonvolatile': True, 'extensions': ['sav'], 'size_maximum': '0x20000', 'address': '0x20000000'})
    write('data.json', {'data': {'magic': 'APF_VER_1', 'data_slots': slots}})
    # Interlaced BIOS screens arrive as 240-line fields.
    # Slot order matches psx_apf_video SLOT_SELECT: 640, 320, 256, 368, 512
    # (x 240), then with SLOT480 640, 512, 368 x 480.
    write('video.json', {'video': {'magic': 'APF_VER_1', 'scaler_modes': [
        {'width': w, 'height': h, 'aspect_w': 4, 'aspect_h': 3, 'rotation': 0, 'mirror': 0}
        for w, h in ((640, 240), (320, 240), (256, 240), (368, 240), (512, 240))
        + ((((640, 480), (512, 480), (320, 288)) if args.pal288 else ((640, 480), (512, 480), (368, 480))) if args.slots480 else ())]}})
    # With --shoulder-setting the Settings pick what L and R send, so the
    # Controls list names the plain buttons.
    buttons = [(k, {'L1': 'L Buttons', 'R1': 'R Buttons'}.get(n, n) if args.shoulder_setting else n) for k, n in BUTTONS]
    write('input.json', {'input': {'magic': 'APF_VER_1', 'controllers': [{'type': 'default',
        'mappings': [{'id': i, 'name': n, 'key': k} for i, (k, n) in enumerate(buttons)]}]}})
    variables = []
    if args.weave_setting:
        # prepare --weave-setting: 480i as woven full frames (on) or plain fields.
        variables.append({'name': '480i weave', 'id': 1, 'type': 'check', 'enabled': True, 'persist': True,
                          'writeonly': True, 'address': '0x50000000', 'defaultval': 1, 'value': 1, 'value_off': 0})
    if args.field_swap_setting:
        # prepare --field-swap-setting: run-time flip of the 480i field order.
        variables.append({'name': 'Swap 480i fields', 'id': 3, 'type': 'check', 'enabled': True, 'persist': True,
                          'writeonly': True, 'address': '0x50000008', 'defaultval': 0, 'value': 1, 'value_off': 0})
    if args.logo_skip_setting:
        # prepare --logo-skip-setting: off by default (the PlayStation logo shows).
        variables.append({'name': 'Skip BIOS logo', 'id': 2, 'type': 'check', 'enabled': True, 'persist': True,
                          'writeonly': True, 'address': '0x50000004', 'defaultval': 0, 'value': 1, 'value_off': 0})
    if args.dualshock:
        # prepare --dualshock: pad 1 is a DualShock (on) or a digital pad (off).
        variables.append({'name': 'DualShock', 'id': 20, 'type': 'check', 'enabled': True, 'persist': True,
                          'writeonly': True, 'address': '0x50000040', 'defaultval': 1, 'value': 1, 'value_off': 0})
    if args.shoulder_setting:
        # prepare --shoulder-setting: what the Pocket's L and R press. Rows,
        # options and addresses come from the preparer.
        from prepare_pocket_early import SHOULDER_ROWS, SHOULDER_COMBOS
        for n, (name, address, targets) in enumerate(SHOULDER_ROWS):
            variables.append({'name': name, 'id': 4+n, 'type': 'list', 'enabled': True, 'persist': True,
                              'writeonly': True, 'address': f'0x{address:08X}', 'defaultval': 0,
                              'options': [{'value': v, 'name': t} for v, t in enumerate(targets)]})
        for n, (name, address, targets, default, _) in enumerate(SHOULDER_COMBOS):
            variables.append({'name': name, 'id': 4+len(SHOULDER_ROWS)+n, 'type': 'list', 'enabled': True, 'persist': True,
                              'writeonly': True, 'address': f'0x{address:08X}', 'defaultval': default,
                              'options': [{'value': v, 'name': t} for v, t in enumerate(targets)]})
    if args.video_test_setting:
        variables.append({'name': 'Video test', 'id': 8, 'type': 'list', 'enabled': True, 'persist': True,
                          'writeonly': True, 'address': '0x5000004C', 'defaultval': 0,
                          'options': [{'value': 0, 'name': 'A: VS at vblank'}, {'value': 1, 'name': 'B: A + lead line'},
                                      {'value': 2, 'name': 'C: VS at vsync'}, {'value': 3, 'name': 'D: C + lead line'}]})
    if args.disc_menu:
        # prepare --disc-menu: remembered, so a game resumed on disc 2 starts
        # there (swapped before anything reads the disc); a game without that
        # disc falls back to disc 1.
        variables.append({'name': 'Disc', 'id': 21, 'type': 'list', 'enabled': True, 'persist': True,
                          'writeonly': True, 'address': '0x50000044', 'defaultval': 0,
                          'options': [{'value': n, 'name': f'Disc {n+1}'} for n in range(args.disc_menu)]})
    # The Disc list comes first in Core Settings.
    variables.sort(key=lambda v: v['name'] != 'Disc')
    write('interact.json', {'interact': {'magic': 'APF_VER_1', 'variables': variables, 'messages': []}})
    write('variants.json', {'variants': {'magic': 'APF_VER_1', 'variant_list': []}})
    write('audio.json', {'audio': {'magic': 'APF_VER_1'}})
    info = ['Early PlayStation core.']
    if args.disc:
        info.append('* Discs: ' + ('pick a game instance (.json from scripts/make_pocket_instances.py; '
                    '.chd and single-.bin images need its --convert first)' if args.multi_track else 'one .bin image'))
    else: info.append('* Boots your BIOS only; no disc support')
    info.append('* Memory card 1 saves to SD' if args.memcard_slot else '* No memory card saves')
    if args.pad_combos:
        info.append('* Hold Select: L = L2, R = R2, Square (Y) = L3, Circle (A) = R3. A Select tap still works as '
                    'Select; Select held alone for one second passes through. Docked controllers keep their own L2/R2/L3/R3.')
    if args.shoulder_setting:
        info.append('* Settings > "L Button" / "R Button": what L Button and R Button press in the game (L1, L2 or L3 / '
                    'R1, R2 or R3). Controls > "L Buttons" / "R Buttons" only chooses which physical buttons those are. '
                    '"SELECT + L/R" and "SELECT + Y/A" pick what those combos press: None, L1/R1, L2/R2 or L3/R3 '
                    '(defaults L2/R2 and L3/R3).')
    if args.disc_menu:
        info.append('* Multi-disc games: one game file holds every disc (one memory card). Change discs with Core '
                    'Settings > Disc when the game asks; the core opens and closes the lid.')
    if args.allow_timing_failure: info.append('* Built with known timing violations; glitches are possible')
    (core/'info.txt').write_text('\n'.join(info) + '\n')
    assets = sd/'Assets'/args.platform/'common'; assets.mkdir(parents=True)
    (assets/'PLACE-BIOS-HERE.txt').write_text('Place your own 524288-byte PSX BIOS dump here as bios.bin.\nNo BIOS or game is included.\n')
    (sd/'Platforms').mkdir()
    (sd/'Platforms'/f'{args.platform}.json').write_text(json.dumps({'platform': {
        'category': 'Console', 'name': 'PlayStation' if args.platform == 'playstation' else 'PlayStation (early test)', 'year': 1994,
        'manufacturer': 'Sony'}}, indent=2)+'\n')
    notices = core/'licenses'; notices.mkdir()
    for src, name in ((ROOT/'THIRD_PARTY_NOTICES.md', 'THIRD_PARTY_NOTICES.md'),
            (ROOT/'references/MIRLO64/LICENSE', 'MIRLO64-LICENSE.txt'),
            (ROOT/'references/MIRLO64/NOTICE', 'MIRLO64-NOTICE.txt'),
            (ROOT/'references/PSX_MiSTer/LICENSE', 'PSX_MiSTer-LICENSE.txt'),
            (ROOT/'licenses/LiteX-BSD-2-Clause.txt', 'LiteX-BSD-2-Clause.txt'),
            (ROOT/'LICENSE', 'PSX_Pocket-LICENSE.txt')):
        if src.is_file(): shutil.copyfile(src, notices/name)
    report = dict(project=str(project), rbf_sha256=digest(rbf), rbf_r_sha256=digest(core/'bitstream.rbf_r'),
        bytes=len(original), fit_summary=fit, slacks=dict(slacks), timing_failures=dict(failing),
        hardware_verified=False, private_assets_included=False)
    (out/'build-report.json').write_text(json.dumps(report, indent=2)+'\n')
    shutil.copyfile(ROOT/'docs/pocket-early-hardware.md', out/'INSTALL.md')
    with zipfile.ZipFile(out/'PSXEarly.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(sd.rglob('*')):
            if path.is_file(): archive.write(path, path.relative_to(sd))
        archive.write(out/'INSTALL.md', 'PSXEarly-INSTALL.md')
        archive.write(out/'build-report.json', 'PSXEarly-build-report.json')
    print(out/'PSXEarly.zip')


if __name__ == '__main__': main()
