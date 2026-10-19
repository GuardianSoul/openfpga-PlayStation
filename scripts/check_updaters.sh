#!/bin/bash
# Release check: run the real game-file builders on a packaged core.
# usage: scripts/check_updaters.sh <package SD dir> <core folder name>
#   PUPDATE=path/to/pupdate (5.8.0)  OIP=path/to/openfpga_instance_packager
#   (cargo build --release in neil-morrison44/openfpga-instance-packager)
#   PUPDATE_SETTINGS=pupdate_settings.json to copy (credentials are dropped)
# Builds cue + 1 bin, cue + 4 bins, cue + bin + sbi and cue + 9 bins, then
# prints each game file's slots. A --cue-mount core's game files hold the cue
# (and .sbi) only, so the 9-bin disc gets one too.
set -u
SD=$1; CORE=$2; S=$(mktemp -d)
mk() { # card dir
  C=$1; mkdir -p "$C"; cp -r "$SD"/* "$C"/
  python3 - "$C/Assets/playstation/common" <<'PY'
import sys; from pathlib import Path
g=Path(sys.argv[1])
def game(name, nbins, sbi=False):
    d=g/name; d.mkdir(parents=True, exist_ok=True)
    bins=[f'{name}.bin'] if nbins==1 else [f'{name} (Track {i}).bin' for i in range(1,nbins+1)]
    for b in bins: (d/b).write_bytes(b'\0'*2352*4)
    (d/f'{name}.cue').write_text(''.join(f'FILE "{b}" BINARY\n  TRACK {i:02} {"MODE2/2352" if i==1 else "AUDIO"}\n    INDEX 01 00:00:00\n' for i,b in enumerate(bins,1)))
    if sbi: (d/f'{name}.sbi').write_bytes(b'SBI\0'+bytes([3,8,5,1])+bytes(10))
game('Cue One Bin',1); game('Cue Four Bins',4); game('Cue Bin Sbi',1,True); game('Cue Nine Bins',9)
PY
}
show() { C=$1; for g in 'Cue One Bin' 'Cue Four Bins' 'Cue Bin Sbi' 'Cue Nine Bins'; do f="$C/Assets/playstation/$CORE/$g.json"; if [ -f "$f" ]; then printf '  %-14s ' "$g"; python3 -c "import json,sys; print([(s['id'],s['filename']) for s in json.load(open(sys.argv[1]))['instance']['data_slots']])" "$f"; else printf '  %-14s NO GAME FILE\n' "$g"; fi; done; }
P=$S/upd-pupdate; O=$S/upd-oip; rm -rf -- "${P:?}" "${O:?}"; mk $P; mk $O
cp "${PUPDATE_SETTINGS:?}" $P/ && python3 -c "
import json,sys; p=sys.argv[1]; d=json.load(open(p)); d['config']['download_new_cores']='no'; d['credentials']=None; json.dump(d,open(p,'w'))" $P/pupdate_settings.json
echo "pupdate 5.8.0:"; (cd $S && timeout 180 script -qec "${PUPDATE:?} instance-generator -p $P" /dev/null < /dev/null 2>&1 | grep -E "Unable|Saving|Discs" | sed 's/^/  log: /'); show $P
echo "openfpga_instance_packager -a:"; timeout 180 "${OIP:?}" -a $O 2>&1 | tail -6 | sed 's/^/  log: /'; show $O
