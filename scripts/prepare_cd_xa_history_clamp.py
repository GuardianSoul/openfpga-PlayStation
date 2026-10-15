"""Store the clamped XA-ADPCM sample as filter history (cd-xa-history-clamp).

The pinned cd_xa keeps the unclamped 24-bit sum in AdpcmLast0/1 and only
clamps the value it writes to the FIFO. psx-spx (old=s after MinMax),
DuckStation and Mednafen all keep the clamped 16-bit sample, so after any
clipping sample the pinned decoder's filter state diverges from the
console's and the error rings on through the block. This transform stores
the clamped value instead; the FIFO output is unchanged.
"""
import json
from pathlib import Path

if __package__:
    from .prepare_resource_targets import digest
else:
    from prepare_resource_targets import digest


def _replace(text, old, new, count=1):
    if text.count(old) != count:
        raise ValueError('Pinned cd_xa source changed near: ' + old.strip().splitlines()[0])
    return text.replace(old, new)


def history_clamp_text(text):
    text = _replace(text, '  \nbegin \n\n   RamIn_addrA', '''   -- Filter history keeps the clamped sample (cd-xa-history-clamp).
   function clampHistory(s : signed(23 downto 0)) return signed is
   begin
      if (s < -32768) then return to_signed(-32768, 24);
      elsif (s > 32767) then return to_signed(32767, 24);
      else return s;
      end if;
   end function;
  
begin 

   RamIn_addrA''')
    text = _replace(text, 'AdpcmLast0_R <= sample;', 'AdpcmLast0_R <= clampHistory(sample);')
    text = _replace(text, 'AdpcmLast0_L <= sample;', 'AdpcmLast0_L <= clampHistory(sample);')
    return text


def apply_cd_xa_history_clamp(manifest):
    target = manifest['targets']['psx_top']
    index = next(i for i, s in enumerate(target['quartus_sources']) if Path(s['path']).name == 'cd_xa.vhd')
    previous = Path(target['quartus_sources'][index]['path'])
    directory = Path(target['directory']); out = directory / 'cd-xa-history-clamp'; out.mkdir(parents=True, exist_ok=True)
    path = out / 'cd_xa.vhd'; path.write_text(history_clamp_text(previous.read_text()))
    target['quartus_sources'][index] = dict(target['quartus_sources'][index], path=str(path), sha256=digest(path),
        original=str(previous), original_sha256=digest(previous), transform='XA filter history keeps the clamped sample')
    qsf = directory / (target['top'] + '.qsf'); q = qsf.read_text()
    if q.count(str(previous)) != 1: raise ValueError('Expected one cd_xa assignment')
    qsf.write_text(q.replace(str(previous), str(path))); target['project_sha256']['.qsf'] = digest(qsf)
    target['cd_xa_history_clamp'] = True; manifest['cd_xa_history_clamp'] = True
    (directory.parent / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest
