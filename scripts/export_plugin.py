#!/usr/bin/env python3
"""Export the Pocket Sync / pupdate plugin (tools/pocket-plugin) as its own
repository.

The public export is the tracked crate as it is, and it refuses to finish if
any file names the workspace or a local path. --private adds the AI working
notes (docs/pocket-plugin/AGENTS.md and notes.md) for the private copy.
With --git the export becomes a fresh one-commit repository; the public one
is authored and committed as the public author. Nothing is pushed.

  python3 scripts/export_plugin.py build/plugin-public --git
  python3 scripts/export_plugin.py build/plugin-private --private --git
"""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

from export_release import LEAKS

ROOT = Path(__file__).resolve().parents[1]
CRATE = 'tools/pocket-plugin/'
PRIVATE = {'docs/pocket-plugin/AGENTS.md': 'AGENTS.md', 'docs/pocket-plugin/notes.md': 'docs/notes.md'}
AUTHOR = ('GuardianSoul', '425591+GuardianSoul@users.noreply.github.com')


def tracked(prefix):
    out = subprocess.run(['git', 'ls-files', '--', prefix], cwd=ROOT, check=True, capture_output=True, text=True).stdout
    return out.splitlines()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('output', type=Path)
    parser.add_argument('--private', action='store_true', help='add the AI working notes (private copy)')
    parser.add_argument('--git', action='store_true', help='make the export a fresh repository with one commit')
    args = parser.parse_args()
    out = args.output.resolve()
    if out.exists(): parser.error(f'{out} exists')
    files = {path: path[len(CRATE):] for path in tracked(CRATE)}
    if args.private: files.update(PRIVATE)
    problems = []
    for src, dest in files.items():
        data = (ROOT/src).read_bytes()
        if not args.private:
            for n, line in enumerate(data.decode('utf-8', 'replace').splitlines(), 1):
                if LEAKS.search(line): problems.append(f'{dest}:{n}: {line.strip()[:100]}')
        (out/dest).parent.mkdir(parents=True, exist_ok=True)
        (out/dest).write_bytes(data)
        shutil.copymode(ROOT/src, out/dest)
    if problems:
        shutil.rmtree(out)
        sys.exit('workspace names in the public export:\n  ' + '\n  '.join(problems))
    if args.git:
        env = dict(os.environ)
        if not args.private:
            env.update(GIT_AUTHOR_NAME=AUTHOR[0], GIT_AUTHOR_EMAIL=AUTHOR[1],
                       GIT_COMMITTER_NAME=AUTHOR[0], GIT_COMMITTER_EMAIL=AUTHOR[1])
        git = lambda *a: subprocess.run(['git', *a], cwd=out, check=True, env=env, capture_output=True, text=True)
        git('init', '-q', '-b', 'main')
        if not args.private:
            git('config', 'user.name', AUTHOR[0]); git('config', 'user.email', AUTHOR[1])
        git('add', '-A')
        git('commit', '-q', '-m', 'PlayStation game files plugin')
        print(git('log', '--format=%h %an <%ae> | %cn <%ce>').stdout.strip())
    print(f'{len(files)} files in {out}')


if __name__ == '__main__':
    main()
