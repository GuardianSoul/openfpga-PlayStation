#!/usr/bin/env python3
"""Install the pinned official GHDL mcode Linux x86_64 release locally."""
import hashlib
import platform
import shutil
import subprocess
import tarfile
import urllib.request

from tool_paths import ROOT

ARCHIVE = 'ghdl-mcode-6.0.0-ubuntu24.04-x86_64.tar.gz'
SHA256 = '30d6a977b8456d140bbafecbbe64b1947a3d92eeae8f5e6d9f528a174f9566e7'
URL = f'https://github.com/ghdl/ghdl/releases/download/v6.0.0/{ARCHIVE}'


def main():
    if platform.system() != 'Linux' or platform.machine() != 'x86_64':
        raise SystemExit('This optional local package requires Linux x86_64; otherwise install GHDL on PATH.')
    tools = ROOT / 'build/tools'
    tools.mkdir(parents=True, exist_ok=True)
    archive = tools / ARCHIVE
    if not archive.exists():
        temporary = archive.with_suffix('.part')
        with urllib.request.urlopen(URL, timeout=60) as source, temporary.open('wb') as dest:
            shutil.copyfileobj(source, dest)
        if hashlib.sha256(temporary.read_bytes()).hexdigest() != SHA256:
            temporary.unlink()
            raise SystemExit('GHDL release checksum mismatch.')
        temporary.replace(archive)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != SHA256:
        raise SystemExit('Existing GHDL archive checksum mismatch.')
    destination = tools / 'ghdl'
    destination.mkdir(exist_ok=True)
    with tarfile.open(archive) as packed:
        packed.extractall(destination, filter='data')
    binary = destination / ARCHIVE.removesuffix('.tar.gz') / 'bin/ghdl'
    subprocess.run([str(binary), '--version'], check=True, timeout=10)


if __name__ == '__main__':
    main()
