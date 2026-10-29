#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Checks that the game list's vote service takes votes for a new release.

Run it after publishing a release, from any machine with internet:

    python3 scripts/check_game_list.py 0.5.6

It passes when the vote service lists the version as its newest release (the
version the page offers for voting) and answers the vote counts. The service
rechecks GitHub's release feed at most every ten minutes, so right after a
release the first run can fail; it retries for up to fifteen minutes.
"""
import json
import sys
import time
import urllib.request

VOTES = 'https://playstation-game-list.guardiansoul.workers.dev'


def get(path):
    request = urllib.request.Request(f'{VOTES}{path}', headers={'User-Agent': 'check-game-list'})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def check(version):
    try:
        versions = get('/versions').get('versions') or []
        get('/votes')
    except Exception as error:   # older service without /versions, or offline
        return f'the vote service did not answer: {error}'
    if not versions or versions[0] != version:
        return f'the vote service lists {versions[:3]}, not {version} first'
    return ''


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    version = sys.argv[1].lstrip('v')
    for attempt in range(16):
        problem = check(version)
        if not problem:
            print(f'OK: the game list takes votes for {version}.')
            return
        print(f'Not yet: {problem}.', flush=True)
        if attempt < 15:
            time.sleep(60)
    sys.exit(f'FAILED: votes for {version} would not be counted. Check that the release is published '
             '(not a draft or pre-release) and that the vote service runs the current worker/vote.js.')


if __name__ == '__main__':
    main()
