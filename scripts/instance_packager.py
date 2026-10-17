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
"""


def instance_packager(platform, name, *, one_file_disc, cue_mount, libcrypt, multi_track):
    output = f'Assets/{platform}/GuardianSoul.{name}'
    cue = {'id': 100, 'filename': '*.cue', 'sort': 'single', 'required': True, 'as_filename': True}
    bins = {'id': 101, 'filename': '*.bin', 'sort': 'ascending', 'required': True}
    sbi = {'id': 99, 'filename': '*.sbi', 'sort': 'single', 'required': False}
    if one_file_disc:
        # --cue-mount declares 101-104 for a disc's files; otherwise a disc is
        # one .bin and its .trk table.
        return {'output': output, 'platform_id': platform,
                'slot_limit': {'count': 5 if cue_mount else 3,
                               'message': 'Discs with more than 4 .bin files: make their game files with the PlayStation game files plugin.'
                                          if cue_mount else 'Discs need one .bin and its .trk: make their game files with make_pocket_instances.py.'},
                'data_slots': [cue, bins, {'id': 102, 'filename': '*.trk', 'sort': 'single', 'required': False}]
                              + ([sbi] if libcrypt or cue_mount else [])}
    return {'output': output, 'platform_id': platform,
            'slot_limit': {'count': multi_track+1+bool(libcrypt),
                           'message': f'Games with more than {multi_track} track files are not supported yet.'},
            'data_slots': [cue, bins] + ([sbi] if libcrypt else [])}
