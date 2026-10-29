#!/usr/bin/env python3
"""Package an early full-PSX hardware smoke-test core for the Pocket SD card.

This is for surfacing hardware blind spots (SDRAM/PSRAM, APF loading, video
out, clock crossings) on a real Pocket. Unlike the probe packagers it accepts
a fit with reported timing violations, but only with --allow-timing-failure
or a passing release_timing.py report for the same project (--release-timing,
which waives the reviewed clock crossings), and records every reported slack
in the package. No BIOS or game is included.
"""
import argparse
import json
from pathlib import Path
import re
import shutil
import zipfile

import build_chip32_loader
import pocket_image
from instance_packager import instance_packager
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
    parser.add_argument('--release-timing', type=Path, help='Passing release_timing.py --output report for this project: '
                        'its sign-off replaces the raw slack check')
    parser.add_argument('--memcard-slot', action='store_true', help='Memory card 1 save slot (prepare --memcard-slot)')
    parser.add_argument('--save-states', action='store_true', help='The bitstream has save states (prepare --save-states): core.json sleep_supported, so Sleep and Memories use them')
    parser.add_argument('--bios-check', action='store_true', help='Chip32 loader: a message naming the BIOS file and folder when it is missing, not 512 KB or not a USA BIOS (support/chip32/psx_bios_check.asm)')
    parser.add_argument('--bios-names', action='store_true', help='With --bios-check: also accept other BIOS file names, one slot each from 0x20 (prepare --bios-names)')
    parser.add_argument('--disc-menu', type=int, metavar='DISCS', help='prepare --disc-menu: declare discs 2..DISCS (slots n01, n02, ...; tracks per extra disc as the 32-slot table allows) and the "Swap Discs" menu list')
    parser.add_argument('--libcrypt', action='store_true', help='Declare data slot 99 for a disc\'s LibCrypt .sbi (prepare --libcrypt; make_pocket_instances.py fills it)')
    parser.add_argument('--stick-settings', action='store_true', help='Menu lists "D-Pad" and "SELECT + D-Pad" and the stick options of "SELECT + A/B/X/Y" (prepare --stick-settings; needs --dualshock --shoulder-setting)')
    parser.add_argument('--dualshock', action='store_true', help='Menu checkbox "Enable DualShock" (prepare --dualshock, bridge 0x50000040, on by default) and the analog notes in info.txt')
    parser.add_argument('--pad-combos', action='store_true', help='Explain the Select combos in info.txt (prepare --pad-combos); the Controls list stays plain because combos cannot be remapped')
    parser.add_argument('--boot-log', action='store_true', help='Add the nonvolatile boot-trace slot (prepare --boot-trace)')
    parser.add_argument('--disc', action='store_true', help='Disc slot 0 (.bin from SD, prepare --cd-stream); BIOS moves to slot 0x20')
    parser.add_argument('--cue-mount', action='store_true', help='prepare --cue-mount: the launch list takes a game JSON, .cue or .m3u (no .bin); without --disc-menu, "Load disc" (slot 101, reloadable without a restart) swaps to another .cue or .bin')
    parser.add_argument('--one-file-disc', action='store_true', help='prepare --one-file-disc: disc n is one .bin in slot 100*n+1 and its track table (.trk) in 100*n+2 (make_pocket_instances.py merges track files)')
    parser.add_argument('--multi-track', type=int, default=0, help='Discs as instance JSONs: cue slot 100, track bins 101..100+N (prepare --multi-track N)')
    parser.add_argument('--logo-skip-setting', action='store_true', help='Menu checkbox "Skip BIOS Logo" (prepare --logo-skip-setting)')
    parser.add_argument('--turbo-setting', action='store_true', help='Menu list "Turbo Mode": Off (default) / On (scripts/prepare_turbo_setting.py, bridge 0x50000080)')
    parser.add_argument('--fast-bios-setting', action='store_true', help='Menu checkbox "Fast BIOS Boot" (scripts/prepare_fast_bios.py, bridge 0x50000070)')
    parser.add_argument('--field-swap-setting', action='store_true', help='Menu checkbox to swap the 480i field order (prepare --field-swap-setting)')
    parser.add_argument('--shoulder-setting', action='store_true', help='Menu lists for what L and R press (prepare --shoulder-setting)')
    parser.add_argument('--weave-setting', action='store_true', help='Menu checkbox for 480i weave (prepare --weave-setting)')
    parser.add_argument('--pal288', action='store_true', help='With --slots480: slot 7 is 320x288 for PAL instead of 368x480 (prepare --pal288)')
    parser.add_argument('--video-test-setting', action='store_true', help='Diagnostic Settings list "Video test" (prepare --video-test-setting)')
    parser.add_argument('--wide-lines', action='store_true', help='Scaler slots 640x240/480/288 for stretched lines (prepare --wide-lines)')
    parser.add_argument('--analog-setting', action='store_true', help='With --dualshock: the DualShock checkbox becomes a "Controller Mode" list, Digital (default) / Analog, switched without a restart (prepare --analog-setting)')
    parser.add_argument('--track-sizes', action='store_true', help='Data slot 97 for a game JSON\'s track sizes (<cue name>.tsz, make_pocket_instances.py and the plugin): with --cue-mount the boot mount of a cue whose files are opened by path skips their openfile and size probes')
    parser.add_argument('--cheats', action='store_true', help='Data slot 98 for a game\'s cheat list and the Core Settings "Cheats" checkbox, off at every launch (prepare --cheats; make_pocket_instances.py fills the slot)')
    parser.add_argument('--true-color', action='store_true', help='Core Settings "True Color" checkbox, off by default (prepare --true-color)')
    parser.add_argument('--dither-setting', action='store_true', help='Core Settings "Dithering" checkbox, on by default (prepare --dither-setting)')
    parser.add_argument('--deinterlace-setting', action='store_true', help='Core Settings "Deinterlacing" list (DEINTERLACE below), right under Aspect Ratio (prepare --deinterlace-setting)')
    parser.add_argument('--aspect-setting', action='store_true', help='With --hybrid-slots: Core Settings "Aspect Ratio" list (4:3 = Fit / Fill Screen = Stretch) and its slots: 640/320/256 x 240, 640x480 and 640x288 at 4:3, then 640x240, 640x480 and 640x288 at 10:9 (prepare --aspect-setting)')
    parser.add_argument('--dbl-320', action='store_true', help='With --fit-368: slot 1 is 640x480 at 10:9 instead of 320x240 (prepare --dbl-320)')
    parser.add_argument('--pal-shape', action='store_true', help='prepare --pal-shape: the PAL Fill slot is 640x512')
    parser.add_argument('--analogizer', action='store_true', help='prepare --analogizer: Core Settings "Analogizer Video" and "Analogizer SNAC"')
    parser.add_argument('--fill-blend-panel', action='store_true', help='prepare --fill-blend-panel: Core Settings "Fill Phase"')
    parser.add_argument('--fill-blend', action='store_true', help='prepare --fill-blend: the Fill slots are 800 wide')
    parser.add_argument('--wide-blend', action='store_true', help='prepare --wide-blend: slot 2 is 800x240, Core Settings "Wide Pixels"')
    parser.add_argument('--dock-test', action='store_true', help='prepare --dock-test: Core Settings "Dock test" list')
    parser.add_argument('--weave-576', action='store_true', help='prepare --weave-576 slot table: 640x576 PAL 480i Fit, 640x536 PAL Fill, 640x448 NTSC Fill')
    parser.add_argument('--height-slots', action='store_true', help='With --fill-detect: slots by height only, slot = {Fill, PAL, 480i}: 640x240/480/288/576 at 4:3, 640x224/448/268/536 at 10:9 (prepare --height-slots)')
    parser.add_argument('--fill-detect', action='store_true', help='With --aspect-setting: the Fill Screen slots are 640x224, 640x448 and 640x268 for the overscan crop (prepare --fill-detect)')
    parser.add_argument('--overscan-setting', action='store_true', help='Core Settings "Hide Overscan" checkbox, on by default (prepare --overscan-setting)')
    parser.add_argument('--fit-368', action='store_true', help='With --aspect-setting: slot 6 is 368x240 at 4:3 instead of 640x480 at 10:9 (prepare --fit-368)')
    parser.add_argument('--hybrid-slots', action='store_true', help='With --wide-lines: native 320/256/512 slots plus stretched 640 slots (prepare --hybrid-slots)')
    parser.add_argument('--slot6-368', action='store_true', help='With --pal288: slot 6 is 368x480 instead of 512x480 (prepare --slot6-368)')
    parser.add_argument('--mid-slot-384', action='store_true', help='The 368-wide scaler slots are 384 wide (prepare --mid-slot-384)')
    parser.add_argument('--slots480', action='store_true', help='Add 640/512/368 x 480 scaler slots 5-7 (prepare --slots480)')
    parser.add_argument('--platform', default=PLATFORM, help="Platform id; 'playstation' uses the existing Assets/playstation/common folder")
    parser.add_argument('--bios-name', default='bios.bin')
    parser.add_argument('--variant', default='', help='Core name suffix and log name, for several builds on one card')
    args = parser.parse_args()
    project = args.project.resolve(); out = args.output.resolve()
    if (project/'ABLATED').exists(): parser.error('measurement build (prepare --measure-ablate): ' + (project/'ABLATED').read_text().strip())
    fit = (project/'output_files/apf_top.fit.summary').read_text()
    if 'Fitter Status : Successful' not in fit or 'Total virtual pins : 0' not in fit:
        parser.error('Successful physical fit required')
    timing = (project/'output_files/apf_top.sta.summary').read_text()
    slacks = [(k, float(v)) for k, v in re.findall(r'Type\s*:\s*(.*?)\nSlack\s*:\s*(-?[\d.]+)', timing)]
    if not slacks: parser.error('Timing summary missing')
    failing = [s for s in slacks if s[1] < 0]
    signoff = None
    if args.release_timing:
        signoff = json.loads(args.release_timing.read_text())
        if not signoff.get('passed'): parser.error('release_timing report did not pass')
        if signoff.get('qsf_sha256') != digest(project/'apf_top.qsf'):
            parser.error('release_timing report is for a different apf_top.qsf')
        if (project/'output_files/apf_top.sta.summary').stat().st_mtime > args.release_timing.stat().st_mtime:
            parser.error('release_timing report is older than the timing summary')
    if failing and not args.allow_timing_failure and not signoff:
        parser.error(f'{len(failing)} clocks have negative slack; pass --release-timing or --allow-timing-failure')
    if args.save_states and 'psx_save_exp1.sv' not in (project/'apf_top.qsf').read_text():
        parser.error('--save-states: this project was not prepared with --save-states')
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
    # A core folder without icon.bin makes pupdate and Pocket Sync fail to parse
    # the core inventory (the 2026-10-20 probe2 package hit exactly that), so every
    # packaged core carries the 36x36 RGB565 icon, the same art the release
    # packager writes: 2592 bytes.
    icon_png = ROOT/'support/pocket-art/core-icon.png'
    if not icon_png.is_file(): parser.error(f'core icon art missing: {icon_png}')
    (core/'icon.bin').write_bytes(pocket_image.encode(icon_png, (36, 36)))
    def write(name, value): (core/name).write_text(json.dumps(value, indent=2)+'\n')
    write('core.json', {'core': {'magic': 'APF_VER_1', 'metadata': {
        'platform_ids': [args.platform], 'shortname': name,
        'description': 'PlayStation core for Analogue Pocket' if args.disc else 'PlayStation hardware test: BIOS boot only',
        'author': 'GuardianSoul', 'url': 'https://github.com/GuardianSoul/openfpga-PlayStation',
        'version': args.version, 'date_release': '2026-10-14'},
        'framework': {'target_product': 'Analogue Pocket', 'version_required': '1.1',
            'sleep_supported': args.save_states, 'dock': {'supported': True, 'analog_output': False},
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
    if args.one_file_disc and not args.multi_track: parser.error('--one-file-disc needs --multi-track')
    if args.cue_mount and not args.one_file_disc: parser.error('--cue-mount needs --one-file-disc')
    if args.one_file_disc and args.libcrypt: parser.error('--one-file-disc: the LibCrypt mask is in the track table; drop --libcrypt')
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
        # prepare --one-file-disc: every disc is one .bin of all its tracks and
        # a 1,600-byte track table file (.trk) in the next slot.
        disc_slots = lambda n, label: ([{'name': f'{label}Disc image', 'id': 100*n+1, 'required': False, 'parameters': '0x08', 'deferload': True,
                                         'extensions': ['bin'], 'size_maximum': '0x40000000'},
                                        {'name': f'{label}Track table', 'id': 100*n+2, 'required': False, 'parameters': '0x08', 'deferload': True,
                                         'extensions': ['trk'], 'size_maximum': '0x640'}])
        if args.one_file_disc:
            slots += disc_slots(1, '')
        if args.cue_mount:
            # prepare --cue-mount: the launch list takes a game JSON, a .cue
            # or a .m3u (probe PSXLaunchA: JSONs in the core's folder, Back
            # reaches the loose files; no BIOS screen first). No .bin: a
            # picked track file has no track list (no CD music, some games
            # stop), and a disc without a .cue gets one from the plugin.
            # Game files never name slot 0, so this hides nothing they use.
            # psx_cue_mount sniffs slot 0 before the stream starts. "Load
            # disc" in Core Settings swaps to another .cue or .bin (0x09:
            # user-reloadable, read-only, no core restart; 0x89's restart
            # hung on the SCE screen on hardware). The core opens track files into 103. A
            # .m3u playlist is a multi-disc game with one save (Disc menu).
            # With --disc-menu, Swap Discs is the one disc option: slot 101
            # stays the JSON route's disc image, not user-reloadable.
            slots[0]['extensions'] = ['json', 'cue', 'm3u']
            # A game JSON's slot 100 may hold a .m3u (the multi-disc plugin).
            next(x for x in slots if x['id'] == 100)['extensions'] = ['cue', 'm3u']
            if not args.disc_menu:
                next(x for x in slots if x['id'] == 101).update({'name': 'Load disc', 'parameters': '0x09', 'extensions': ['cue', 'bin', 'm3u']})
            # The cue's track files, opened one at a time by the core.
            slots.append({'name': 'Track file', 'id': 103, 'required': False, 'parameters': '0x08', 'deferload': True,
                          'extensions': ['bin'], 'size_maximum': '0x40000000'})
            # Another disc's cue ("(Disc n)" siblings, for the Disc menu).
            slots.append({'name': 'Disc cue', 'id': 104, 'required': False, 'parameters': '0x08', 'deferload': True,
                          'extensions': ['cue'], 'size_maximum': '0x4000'})
        elif not args.one_file_disc:
            slots += [{'name': f'Track {k}', 'id': 100+k, 'required': False, 'parameters': '0x08', 'deferload': True,
                       'extensions': ['bin'], 'size_maximum': '0x40000000'} for k in range(1, args.multi_track+1)]
        # The disc's LibCrypt .sbi: psx_cd_stream reads it (prepare
        # --libcrypt), or, for a game JSON, the cue reader (prepare
        # --cue-libcrypt; a cue opened by its path reads the .sbi beside it).
        lc_slot = args.libcrypt or args.cue_mount
        write('instance-packager.json', instance_packager(args.platform, name, one_file_disc=args.one_file_disc,
            cue_mount=args.cue_mount, libcrypt=args.libcrypt, multi_track=args.multi_track))
        if args.disc_menu:
            # prepare --disc-menu: discs 2.. of a game in the same game file
            # (make_pocket_instances.py groups "(Disc n)" cues); track k of
            # disc n in slot 100*n+k. APF tables hold 32 slots, so later
            # discs get the tracks that remain (most multi-disc games have one).
            free = 32 - len(slots) - 1 - (1 if args.memcard_slot else 0) - (1 if args.boot_log else 0) - (1 if lc_slot else 0) - (1 if args.cheats else 0)
            per_disc = max(1, free // (args.disc_menu - 1))
            for n in range(2, args.disc_menu + 1):
                if args.one_file_disc: slots += disc_slots(n, f'Disc {n} '); continue
                slots += [{'name': f'Disc {n} track {k}', 'id': 100*n+k, 'required': False, 'parameters': '0x08', 'deferload': True,
                           'extensions': ['bin'], 'size_maximum': '0x40000000'} for k in range(1, per_disc+1)]
        if lc_slot:
            # prepare --libcrypt: psx_cd_stream reads the .sbi (at most 2,352
            # bytes) from this slot at every mount; absent means no LibCrypt.
            # --cue-mount: a game JSON's cue has no path, so the reader takes
            # its .sbi from here.
            slots.append({'name': 'LibCrypt (sbi)', 'id': 99, 'required': False, 'parameters': '0x08', 'deferload': True,
                          'extensions': ['sbi'], 'size_maximum': '0x930'})
        if args.track_sizes:
            # cue_mount.c tsz_word: each FILE's sectors for a cue-only game
            # JSON (at most 3 + 99 words).
            if not args.cue_mount: parser.error('--track-sizes needs --cue-mount')
            slots.append({'name': 'Track sizes', 'id': 97, 'required': False, 'parameters': '0x08', 'deferload': True,
                          'extensions': ['tsz'], 'size_maximum': '0x200'})
        if args.cheats:
            # prepare --cheats: the cue reader's firmware reads the game's
            # GameShark list (plain or libretro .cht) from here at launch.
            if not args.cue_mount: parser.error('--cheats needs --cue-mount')
            slots.append({'name': 'Cheats', 'id': 98, 'required': False, 'parameters': '0x08', 'deferload': True,
                          'extensions': ['cht', 'txt'], 'size_maximum': '0x8000'})
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
        # The card is named after the launch pick: Saves/<platform>/<core>/
        # <json>.sav for a game JSON, Saves/<platform>/common/<folder>/<file>.sav
        # for a .cue or .bin (probe PSXLaunchB).
    if args.cue_mount:
        # A game JSON (0.5.2's layout) may put a track's .bin in 101-104 or
        # n01-n02; 105-119 are not declared (2026-10-17: the slots go
        # to all 16 BIOS names), so 0.5.2 files for games with more than four
        # .bin files need regenerating.
        for x in slots:
            if isinstance(x['id'], int) and (101 <= x['id'] <= 119 or (x['id'] > 200 and x['id'] % 100 in (1, 2))):
                x.update({'size_maximum': '0x40000000', 'extensions': sorted(set(x['extensions']) | {'bin'})})
    if len(slots) > 32: parser.error(f'{len(slots)} data slots; APF takes 32')
    write('data.json', {'data': {'magic': 'APF_VER_1', 'data_slots': slots}})
    if args.bios_names and not args.bios_check: parser.error('--bios-names needs --bios-check')
    if args.bios_check: build_chip32_loader.apply(core, args.platform, args.bios_names)
    # Interlaced BIOS screens arrive as 240-line fields.
    # Slot order matches psx_apf_video SLOT_SELECT: 640, 320, 256, 368, 512
    # (x 240), then with SLOT480 640, 512, 368 x 480.
    if args.wide_lines:
        # prepare --wide-lines: every line is 640 pixels; slots by height only.
        # prepare --fill-detect sends Fill Screen without the overscan: 224
        # lines (448 woven in 480i) and 268 at PAL, so its slots are that high.
        fill = (224, 448, 268) if args.fill_detect else (240, 480, 288)
        # prepare --weave-576: PAL 480i woven in 640x576 (Fit) / 640x536 (Fill);
        # progressive Fill lines doubled into the Fill 480i slots.
        fw = 800 if args.fill_blend else 640   # prepare --fill-blend: Fill lines 800 wide
        w576 = ((640, 240, 4, 3), (fw, 448, 10, 9), (800 if args.wide_blend else 256, 240, 4, 3), (640, 480, 4, 3), (640, 288, 4, 3),
                (640, 576, 4, 3), (368, 240, 4, 3), (fw, 512 if args.pal_shape else 536, 10, 9))
        # prepare --height-slots: slot = {Fill, PAL, 480i}, every line at 640.
        heights = ((240, 4, 3), (480, 4, 3), (288, 4, 3), (576, 4, 3), (224, 10, 9), (448, 10, 9), (268, 10, 9), (536, 10, 9))
        # Fill (10:9, the Pocket's own screen) fills a 16:9 TV when docked.
        write('video.json', {'video': {'magic': 'APF_VER_1', 'scaler_modes': [
            dict({'width': w, 'height': h, 'aspect_w': a, 'aspect_h': b, 'rotation': 0, 'mirror': 0},
                 **({'dock_aspect_w': 16, 'dock_aspect_h': 9} if (a, b) == (10, 9) else {}))
            for w, h, a, b in w576]}} if args.weave_576 else {'video': {'magic': 'APF_VER_1', 'scaler_modes': [
            {'width': 640, 'height': h, 'aspect_w': a, 'aspect_h': b, 'rotation': 0, 'mirror': 0}
            for h, a, b in heights]}} if args.height_slots else {'video': {'magic': 'APF_VER_1', 'scaler_modes': [
            {'width': 640, 'height': h, 'aspect_w': 4, 'aspect_h': 3, 'rotation': 0, 'mirror': 0}
            for h in (240, 480, 288)]}} if not args.hybrid_slots else {'video': {'magic': 'APF_VER_1', 'scaler_modes': [
            {'width': w, 'height': h, 'aspect_w': a, 'aspect_h': b, 'rotation': 0, 'mirror': 0}
            for w, h, a, b in ((640, 240, 4, 3), (640, fill[1], 10, 9) if args.dbl_320 else (320, 240, 4, 3), (256, 240, 4, 3), (640, 480, 4, 3), (640, 288, 4, 3),
                               (640, fill[0], 10, 9), (368, 240, 4, 3) if args.fit_368 else (640, fill[1], 10, 9), (640, fill[2], 10, 9))]}} if args.aspect_setting else {'video': {'magic': 'APF_VER_1', 'scaler_modes': [
            {'width': w, 'height': h, 'aspect_w': 4, 'aspect_h': 3, 'rotation': 0, 'mirror': 0}
            for w, h in ((640, 240), (320, 240), (256, 240), (640, 480), (320, 288), (256, 288), (640, 288), (512, 240))]}})
    else:
         mid = 384 if args.mid_slot_384 else 368
         write('video.json', {'video': {'magic': 'APF_VER_1', 'scaler_modes': [
        {'width': w, 'height': h, 'aspect_w': 4, 'aspect_h': 3, 'rotation': 0, 'mirror': 0}
        for w, h in ((640, 240), (320, 240), (256, 240), (mid, 240), (512, 240))
        + ((((640, 480), (mid if args.slot6_368 else 512, 480), (320, 288)) if args.pal288 else ((640, 480), (512, 480), (mid, 480))) if args.slots480 else ())]}})
    # With --shoulder-setting the Settings pick what L and R send, so the
    # Controls list names the plain buttons.
    # With --shoulder-setting, Core Settings decide what each Pocket button
    # presses, so Controls (which physical button is which) use the Pocket's
    # names throughout: A, B, X, Y, L, R, Select, Start.
    POCKET_NAMES = {'Circle': 'A', 'Cross': 'B', 'Triangle': 'X', 'Square': 'Y', 'L1': 'L', 'R1': 'R'}
    buttons = [(k, POCKET_NAMES.get(n, n) if args.shoulder_setting else n) for k, n in BUTTONS]
    write('input.json', {'input': {'magic': 'APF_VER_1', 'controllers': [{'type': 'default',
        'mappings': [{'id': i, 'name': n, 'key': k} for i, (k, n) in enumerate(buttons)]}]}})
    variables = []
    # prepare --deinterlace-setting: the menu's name and option names live only here
    DEINTERLACE = {'name': 'Deinterlacing', 'id': 43, 'address': '0x50000084', 'options': ('Bob', 'Weave')}
    if args.deinterlace_setting:
        # 0 Bob (default): each 480i field line-doubled, steadied; 1 Weave: the
        # Pocket weaves the fields, as 0.5.6. Taken at the next frame.
        qsf_t = (project/'apf_top.qsf').read_text()
        tops = [Path(c) for c in re.findall(r'"([^"]*/core_top\.sv)"', qsf_t)] + list(project.rglob('core_top.sv'))
        if not any(c.is_file() and 'deint_sync' in c.read_text() for c in tops):
            parser.error('--deinterlace-setting: this project was not prepared with --deinterlace-setting')
        variables.append({'name': DEINTERLACE['name'], 'id': DEINTERLACE['id'], 'type': 'list', 'enabled': True, 'persist': True,
                          'writeonly': True, 'address': DEINTERLACE['address'], 'defaultval': 0,
                          'options': [{'value': i, 'name': n} for i, n in enumerate(DEINTERLACE['options'])]})
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
        variables.append({'name': 'Skip BIOS Logo', 'id': 2, 'type': 'check', 'enabled': True, 'persist': True,
                          'writeonly': True, 'address': '0x50000004', 'defaultval': 0, 'value': 1, 'value_off': 0})
    if args.fast_bios_setting:
        # scripts/prepare_fast_bios.py: on by default (2026-10-22: 10.31 -> 8.36 s
        # from the pick to the Sony screen on hardware); the BIOS start-up before
        # its shell runs at full speed. Off gives the console's own timing.
        variables.append({'name': 'Fast BIOS Boot', 'id': 36, 'type': 'check', 'enabled': True, 'persist': True,
                          'writeonly': True, 'address': '0x50000070', 'defaultval': 1, 'value': 1, 'value_off': 0})
    if args.turbo_setting:
        # prepare --turbo-setting: Off (default) keeps the PS1 timing; On skips PS1
        # RAM/CPU/DMA wait states (MiSTer turbo without the GTE part). Faster than a PS1.
        # Later steps (e.g. prepare_spu_voice_sets.py) re-point core_top/psx_top copies, so look in the
        # compiled core_top.sv as well as for the turbo-setting folder.
        qsf_text = (project/'apf_top.qsf').read_text()
        cores = [Path(p) for p in re.findall(r'SYSTEMVERILOG_FILE "([^"]*/core_top\.sv)"', qsf_text)]
        def local(c):   # a captured project keeps the fit host's absolute paths: rebase them on this project
            if c.is_file(): return c
            parts = c.parts
            return project.joinpath(*parts[parts.index(project.name) + 1:]) if project.name in parts else c
        cores = [local(c) for c in cores]
        if 'turbo_en' not in qsf_text and not (project/'turbo-setting').is_dir() and \
                not any(c.is_file() and 'turbo_en' in c.read_text() for c in cores):
            parser.error('--turbo-setting: this project was not prepared with --turbo-setting')
        variables.append({'name': 'Turbo Mode', 'id': 42, 'type': 'list', 'enabled': True, 'persist': True,
                          'writeonly': True, 'address': '0x50000080', 'defaultval': 0,
                          'options': [{'value': 0, 'name': 'Off'}, {'value': 1, 'name': 'On'}]})
    if args.analog_setting:
        if not args.dualshock: parser.error('--analog-setting needs --dualshock')
        # prepare --analog-setting: always a DualShock; Digital or Analog mode,
        # switched at run time like the controller's Analog button. A new id,
        # so the old checkbox's saved value does not carry over.
        variables.append({'name': 'Controller Mode', 'id': 28, 'type': 'list', 'enabled': True, 'persist': True,
                          'writeonly': True, 'address': '0x50000040', 'defaultval': 0,
                          'options': [{'value': 0, 'name': 'Digital'}, {'value': 1, 'name': 'Analog'}]})
    elif args.dualshock:
        # prepare --dualshock: pad 1 is a DualShock (on, the default) or a digital pad (off).
        variables.append({'name': 'Enable DualShock', 'id': 20, 'type': 'check', 'enabled': True, 'persist': True,
                          'writeonly': True, 'address': '0x50000040', 'defaultval': 1, 'value': 1, 'value_off': 0})
    if args.stick_settings and not (args.dualshock and args.shoulder_setting):
        parser.error('--stick-settings needs --dualshock --shoulder-setting')
    if args.shoulder_setting:
        # prepare --shoulder-setting: what the Pocket's L and R press. Rows,
        # options and addresses come from the preparer.
        from prepare_pocket_early import SHOULDER_ROWS, SHOULDER_COMBOS
        # APF's defaultval is the position in the options list, not a value:
        # every list below gives the value it wants and gets its position.
        def add_list(name, vid, address, default, options):
            options = list(options)
            variables.append({'name': name, 'id': vid, 'type': 'list', 'enabled': True, 'persist': True,
                              'writeonly': True, 'address': f'0x{address:08X}',
                              'defaultval': [v for v, _ in options].index(default),
                              'options': [{'value': v, 'name': t} for v, t in options]})
        for n, (name, address, targets) in enumerate(SHOULDER_ROWS):
            # "L Button" / "R Button": L1/R1 (default), L2/R2 or L3/R3.
            add_list(name, 4+n, address, 0, enumerate(targets))
        from prepare_pocket_stick_settings import STICK_LISTS, SEL_DPAD_ORDER, FACE_NAME, FACE_OPTIONS, FACE_ROW, FACE_STICK, FACE_DEFAULT
        if args.stick_settings:
            # prepare --stick-settings: what the D-Pad and A/B/X/Y send, below
            # R Button (D-Pad first); A/B/X/Y's own option is
            # "Face Buttons".
            # The cross mapping (D-Pad <-> Face Buttons, value 3) is listed second.
            second = lambda options: options[:1] + options[3:] + options[1:3]
            name, address, targets, default = STICK_LISTS[0]
            add_list(name, 22, address, default, second(list(enumerate(targets))))
            name, address, targets, default = FACE_STICK
            add_list(name, 24, address, default, second(list(enumerate(('Face Buttons',) + targets[1:]))))
        # The Select combo lists name what each button presses ("L: L1 | R: R1").
        pair = {'SELECT + L/R': ('L', 'R'), 'SELECT + Y/A': ('Y', 'A')}
        def combo_options(name, targets):
            a, b = pair[name]
            return [(v, t if '/' not in t else f'{a}: {t.split("/")[0]} | {b}: {t.split("/")[1]}') for v, t in enumerate(targets)]
        combos = []
        for n, (name, address, targets, default, _) in enumerate(SHOULDER_COMBOS):
            options = combo_options(name, targets)
            vid = 4+len(SHOULDER_ROWS)+n
            if args.stick_settings and len(SHOULDER_ROWS)+n == FACE_ROW:
                # New id since the 2026-10-17 reorder: a choice saved by
                # menu position under the old order resets to the default
                # instead of landing on another option.
                name, options, default, vid = FACE_NAME, FACE_OPTIONS, FACE_DEFAULT, 26
            combos.append((name, vid, address, default, options))
        if args.stick_settings:
            # prepare --stick-settings: what Select+D-Pad sends, before SELECT + A/B/X/Y
            # (id 25 since the 2026-10-17 reorder, as SELECT + A/B/X/Y above).
            name, address, targets, default = STICK_LISTS[1]
            combos.insert(len(combos) - 1, (name, 25, address, default, [(v, targets[v]) for v in SEL_DPAD_ORDER]))
        for name, vid, address, default, options in combos: add_list(name, vid, address, default, options)
    if args.cheats:
        # prepare --cheats: the game file's cheat list applies while this is
        # on. Not remembered, so cheats are off at every launch.
        variables.append({'name': 'Cheats', 'id': 30, 'type': 'check', 'enabled': True, 'persist': False,
                          'writeonly': True, 'address': '0x50000054', 'defaultval': 0, 'value': 1, 'value_off': 0})
    if args.true_color:
        # prepare --true-color: MiSTer's 24-bit rendering, remembered.
        variables.append({'name': 'True Color', 'id': 31, 'type': 'check', 'enabled': True, 'persist': True,
                          'writeonly': True, 'address': '0x50000058', 'defaultval': 0, 'value': 1, 'value_off': 0})
    if args.dither_setting:
        # prepare --dither-setting: the PS1's dithering (on, as on the console) or off.
        variables.append({'name': 'Dithering', 'id': 29, 'type': 'check', 'enabled': True, 'persist': True,
                          'writeonly': True, 'address': '0x50000050', 'defaultval': 1, 'value': 1, 'value_off': 0})
    if args.fit_368 and not args.aspect_setting: parser.error('--fit-368 needs --aspect-setting')
    if args.dbl_320 and not args.fit_368: parser.error('--dbl-320 needs --fit-368')
    if args.height_slots and (not args.fill_detect or args.fit_368 or args.dbl_320):
        parser.error('--height-slots needs --fill-detect, without --fit-368 or --dbl-320')
    if args.fill_detect and (not args.aspect_setting or args.fit_368 and not args.dbl_320):
        parser.error('--fill-detect needs --aspect-setting, and --dbl-320 with --fit-368 (Stretch 480i then shares the Fit 640x480 slot)')
    if args.aspect_setting:
        if not args.hybrid_slots: parser.error('--aspect-setting needs --wide-lines --hybrid-slots')
        # prepare --aspect-setting: Original (4:3 Fit: bars above and below) or Fill
        # Screen (Stretch: the Pocket's whole 10:9 screen).
        variables.append({'name': 'Aspect Ratio', 'id': 27, 'type': 'list', 'enabled': True, 'persist': True,
                          'writeonly': True, 'address': '0x50000048', 'defaultval': 0,
                          'options': [{'value': 0, 'name': 'Original'}, {'value': 1, 'name': 'Fill Screen'}]})
    if args.overscan_setting:
        # prepare --overscan-setting: Original shows the frame without its
        # overscan lines (black, as Fill Screen crops them) or all of it.
        variables.append({'name': 'Hide Overscan', 'id': 32, 'type': 'check', 'enabled': True, 'persist': True,
                          'writeonly': True, 'address': '0x5000005C', 'defaultval': 1, 'value': 1, 'value_off': 0})
    if args.analogizer:
        # prepare --analogizer: the Analogizer's analog video mode and SNAC port (both off by default).
        variables.append({'name': 'Analogizer Video', 'id': 40, 'type': 'list', 'enabled': True, 'persist': True,
                          'writeonly': True, 'address': '0x50000078', 'defaultval': 0,
                          'options': [{'value': 0, 'name': 'Off'}, {'value': 1, 'name': 'RGBS'},
                                      {'value': 2, 'name': 'RGsB'}, {'value': 3, 'name': 'YPbPr'}]})
        variables.append({'name': 'Analogizer SNAC', 'id': 41, 'type': 'list', 'enabled': True, 'persist': True,
                          'writeonly': True, 'address': '0x5000007C', 'defaultval': 0,
                          'options': [{'value': 0, 'name': 'Off'}, {'value': 1, 'name': 'Port 1'}, {'value': 2, 'name': 'Port 2'}]})
    if args.fill_blend_panel:
        # prepare --fill-blend-panel: the Pocket's 2/3 panel-pixel phase for Fill (test).
        variables.append({'name': 'Fill Phase', 'id': 35, 'type': 'list', 'enabled': True, 'persist': True,
                          'writeonly': True, 'address': '0x50000068', 'defaultval': 0,
                          'options': [{'value': 0, 'name': 'A'}, {'value': 1, 'name': 'B'}]})
    if args.wide_blend:
        # prepare --wide-blend: 368-wide screens (FFVII's menus) in the native
        # slot (Sharp: columns of 4 or 5 panel pixels) or blended at 800 (Even).
        variables.append({'name': 'Wide Pixels', 'id': 34, 'type': 'list', 'enabled': True, 'persist': True,
                          'writeonly': True, 'address': '0x50000064', 'defaultval': 0,
                          'options': [{'value': 0, 'name': 'Sharp'}, {'value': 1, 'name': 'Even'}]})
    if args.dock_test:
        variables.append({'name': 'Dock test', 'id': 33, 'type': 'list', 'enabled': True, 'persist': True,
                          'writeonly': True, 'address': '0x50000060', 'defaultval': 0,
                          'options': [{'value': 0, 'name': 'Normal'}, {'value': 1, 'name': 'Paced pixels'},
                                      {'value': 2, 'name': 'Black tail'}, {'value': 3, 'name': 'Both'}]})
    if args.video_test_setting:
        variables.append({'name': 'Video test', 'id': 8, 'type': 'list', 'enabled': True, 'persist': True,
                          'writeonly': True, 'address': '0x5000004C', 'defaultval': 0,
                          'options': [{'value': 0, 'name': 'A: VS at vblank'}, {'value': 1, 'name': 'B: A + lead line'},
                                      {'value': 2, 'name': 'C: VS at vsync'}, {'value': 3, 'name': 'D: C + lead line'}]})
    if args.disc_menu:
        # prepare --disc-menu: remembered, so a game resumed on disc 2 starts
        # there (swapped before anything reads the disc); a game without that
        # disc falls back to disc 1.
        variables.append({'name': 'Swap Discs', 'id': 21, 'type': 'list', 'enabled': True, 'persist': True,
                          'writeonly': True, 'address': '0x50000044', 'defaultval': 0,
                          'options': [{'value': n, 'name': f'Disc {n+1}'} for n in range(args.disc_menu)]})
    # Core Settings order (by id, so names can change): the game (Swap
    # Discs, Skip BIOS Logo, Cheats), then the picture, then the controller
    # (its mode, then the button lists in their 2026-10-17 order). Ids and
    # values stay as they were, so saved settings and game files still work.
    MENU_ORDER = (21, 2, 36, 42, 30, 27, 43, 35, 32, 34, 29, 31, 1, 3, 28, 20, 4, 5, 22, 24, 6, 25, 26, 8, 33)
    variables.sort(key=lambda v: MENU_ORDER.index(v['id']))
    write('interact.json', {'interact': {'magic': 'APF_VER_1', 'variables': variables, 'messages': []}})
    write('variants.json', {'variants': {'magic': 'APF_VER_1', 'variant_list': []}})
    write('audio.json', {'audio': {'magic': 'APF_VER_1'}})
    # Short About page, one line per paragraph (the Pocket wraps it); the
    # Core Settings menu and README carry the details.
    info = ['PlayStation core for Analogue Pocket, based on the MiSTer PSX core.']
    if not args.disc: info.append('Hardware test: boots your BIOS only, no disc support.')
    elif args.cue_mount:
        info.append('You need your own BIOS and games. Put each game in its own folder in Assets/playstation/common.')
        info.append('Then generate PlayStation game files with the "PlayStation game files" plugin in pupdate or '
                    'Pocket Sync, and again whenever you add games. Start games from their game files (.json). '
                    'Picking a .cue directly is only a fallback: no Swap Discs, and each disc gets its own memory card.')
    else: info.append('Start games from their game files (.json), made with the "PlayStation game files" plugin in pupdate or Pocket Sync.')
    if not args.memcard_slot: info.append('No memory card saves.')
    if args.pad_combos:
        info.append(('Hold Select with L/R for L2/R2, with the D-Pad for the left stick and with A/B/X/Y for the right stick; '
                     'Core Settings change this.' if args.stick_settings else
                     'Hold Select: L = L2, R = R2, Y = L3, A = R3.'))
    if args.dualshock and not args.analog_setting: info.append('A change to Enable DualShock takes effect when the game starts.')
    if args.allow_timing_failure: info.append('Built with known timing violations; glitches are possible.')
    (core/'info.txt').write_text('\n'.join(info) + '\n')
    assets = sd/'Assets'/args.platform/'common'; assets.mkdir(parents=True)
    (assets/'PLACE-BIOS-HERE.txt').write_text('Place your own 524288-byte PSX BIOS dump here as bios.bin.\nNo BIOS or game is included.\n')
    (sd/'Platforms').mkdir()
    (sd/'Platforms'/f'{args.platform}.json').write_text(json.dumps({'platform': {
        'category': 'Console', 'name': 'PlayStation' if args.platform == 'playstation' else 'PlayStation (early test)', 'year': 1994,
        'manufacturer': 'Sony Computer Entertainment'}}, indent=2)+'\n')
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
    if signoff: report['release_timing'] = {k: signoff.get(k) for k in ('passed', 'qsf_sha256', 'waivers_sha256', 'waived')}
    (out/'build-report.json').write_text(json.dumps(report, indent=2)+'\n')
    shutil.copyfile(ROOT/'docs/pocket-early-hardware.md', out/'INSTALL.md')
    with zipfile.ZipFile(out/'PSXEarly.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(sd.rglob('*')):
            if path.is_file(): archive.write(path, path.relative_to(sd))
        archive.write(out/'INSTALL.md', 'PSXEarly-INSTALL.md')
        archive.write(out/'build-report.json', 'PSXEarly-build-report.json')
    print(out/'PSXEarly.zip')


if __name__ == '__main__': main()
