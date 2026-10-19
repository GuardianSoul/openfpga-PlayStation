"""The core's instance-packager.json, which pupdate (Update All) and Pocket
Sync (openfpga-instance-packager) read to make a game file for each folder in
Assets/<platform>/common. tests/test_instance_packager.py runs both builders'
rules on it.

- pupdate (CoresService.Download.cs BuildInstanceJson) reads
  slot_limit["count"] unconditionally: without it every folder fails ("Unable
  to build", 0.5.3). It numbers a slot's files id, id+1, ... whatever the sort.
- Pocket Sync skips a folder silently when a required "single" slot matches
  more than one file, so the .bin slot is "ascending" (0.5.3's "single" left
  every multi-track disc without a game file there).
- A game file naming a slot the core doesn't declare fails on the Pocket, so
  slot_limit is the cue plus the declared track slots.
- A cue-reader core (--cue-mount) gets the cue (and .sbi) only: the core
  opens the .bin files by the cue's folder, so a disc with any number of them
  gets a game file. With the .bin files in 101-104 (the 4 the core
  declares), discs with more were refused; Dead or Alive (29 files) and Mad
  Panic Coaster (24) got no game file until merged (2026-10-18), and their
  cue-only game files boot on hardware.
"""


def instance_packager(platform, name, *, one_file_disc, cue_mount, libcrypt, multi_track):
    output = f'Assets/{platform}/GuardianSoul.{name}'
    cue = {'id': 100, 'filename': '*.cue', 'sort': 'single', 'required': True, 'as_filename': True}
    bins = {'id': 101, 'filename': '*.bin', 'sort': 'ascending', 'required': True}
    sbi = {'id': 99, 'filename': '*.sbi', 'sort': 'single', 'required': False}
    if one_file_disc and cue_mount:
        return {'output': output, 'platform_id': platform,
                'slot_limit': {'count': 2, 'message': 'A disc needs one .cue (and at most one .sbi).'},
                'data_slots': [cue, sbi]}
    if one_file_disc:
        # a disc is one .bin and its .trk table
        return {'output': output, 'platform_id': platform,
                'slot_limit': {'count': 3,
                               'message': 'Discs need one .bin and its .trk: make their game files with make_pocket_instances.py.'},
                'data_slots': [cue, bins, {'id': 102, 'filename': '*.trk', 'sort': 'single', 'required': False}]
                              + ([sbi] if libcrypt else [])}
    return {'output': output, 'platform_id': platform,
            'slot_limit': {'count': multi_track+1+bool(libcrypt),
                           'message': f'Games with more than {multi_track} track files are not supported yet.'},
            'data_slots': [cue, bins] + ([sbi] if libcrypt else [])}
