"""Register the polygon half-edge start coordinate one clock ahead.

With gpu-poly-share, halfCoord = baseCoord + halfDiff * baseStep is formed
combinationally and consumed only in PREPAREHALF, where it feeds xStart/xEnd;
that multiply-add path misses the 2x clock by 1.7 ns on the fitted top. This
variant registers halfCoord every clock. Its inputs are stable for many
clocks before PREPAREHALF on every path except the flat, untextured one,
where CALCBOUNDARY2 sets baseStep and the y differences on the edge before
PREPAREHALF; that path gets two extra states (two more 2x clocks per flat
triangle). The product halfDiff * baseStep and the sum are registered in
separate stages, so neither the multiply nor the 45-bit add shares a cycle
with the other (the single-register version still missed by 0.9 ns). Rendered pixels are unchanged.
"""
import json
from pathlib import Path

if __package__:
    from .prepare_resource_targets import digest
else:
    from prepare_resource_targets import digest


def _replace(text, old, new, count=1):
    if text.count(old) != count: raise ValueError('Pinned gpu_poly source changed near: ' + old.strip()[:60])
    return text.replace(old, new)


def half_pipe_text(text):
    text = _replace(text, '      PREPAREHALF,\n', '      PREPAREHALFWAIT,\n      PREPAREHALFWAIT2,\n      PREPAREHALF,\n')
    text = _replace(text, '   halfDiffBase <= resize(halfDiff * baseStep, 45);\n', '')
    text = _replace(text, '   halfCoord    <= baseCoord + halfDiffBase;\n', '''   -- Product and sum registered two clocks ahead of PREPAREHALF (gpu-half-pipe).
   process (clk2x)
   begin
      if rising_edge(clk2x) then
         halfDiffBase <= resize(halfDiff * baseStep, 45);
         halfCoord    <= baseCoord + halfDiffBase;
      end if;
   end process;
''')
    old = '''                     elsif (rec_texture = '1') then
                        state <= CALCTEXTURE1;
                     else
                        state <= PREPAREHALF;
                     end if;'''
    text = _replace(text, old, old.replace('state <= PREPAREHALF;', 'state <= PREPAREHALFWAIT;'))
    text = _replace(text, '               when PREPAREHALF =>\n',
                    '               when PREPAREHALFWAIT =>\n                  state <= PREPAREHALFWAIT2;\n\n               when PREPAREHALFWAIT2 =>\n                  state <= PREPAREHALF;\n\n               when PREPAREHALF =>\n')
    return text


def apply_gpu_half_pipe(manifest):
    target = manifest['targets']['psx_top']
    index = next(i for i, s in enumerate(target['quartus_sources']) if Path(s['path']).name == 'gpu_poly.vhd')
    previous = Path(target['quartus_sources'][index]['path'])
    directory = Path(target['directory']); out = directory / 'gpu-half-pipe'; out.mkdir(parents=True, exist_ok=True)
    path = out / 'gpu_poly.vhd'; path.write_text(half_pipe_text(previous.read_text()))
    target['quartus_sources'][index] = dict(target['quartus_sources'][index], path=str(path), sha256=digest(path),
        original=str(previous), original_sha256=digest(previous), transform='Registered half-edge start; one wait on flat triangles')
    qsf = directory / (target['top'] + '.qsf'); q = qsf.read_text()
    if q.count(str(previous)) != 1: raise ValueError('Expected one gpu_poly assignment')
    qsf.write_text(q.replace(str(previous), str(path))); target['project_sha256']['.qsf'] = digest(qsf)
    target['gpu_half_pipe'] = True; manifest['gpu_half_pipe'] = True
    (directory.parent / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest
