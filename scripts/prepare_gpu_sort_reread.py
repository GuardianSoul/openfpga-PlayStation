"""Sort polygon vertices by re-reading the vertex RAM in sorted order.

With gpu-vertex-ram the three vertices of a triangle are loaded from small
block RAMs over three clocks. SORTVERTICES then permuted the 3x62-bit working
set in place (six permutations per field), which Quartus builds either as
shared selectors or, after unrelated edits elsewhere in the core, as
per-field multiplexers about 250 ALUTs larger. Here SORTVERTICES only sorts
the three RAM addresses with the same comparisons; four more states read the
vertices again in that order, so every vt register has one data source
(readVertex). The flat-colour rule follows the RAM address as before.
Pixels, memory traffic and every later state are unchanged; each triangle
starts four gpu clocks later. Opt-in (approved tested, reversible
latency changes for gpu_poly on 2026-10-13).
"""
import re


def _replace(text, old, new, count=1):
    if text.count(old) != count:
        raise ValueError(f'gpu_poly source changed ({text.count(old)} matches): ' + old.strip()[:70])
    return text.replace(old, new)


PERMUTATIONS = ((0, 2, 1), (1, 0, 2), (1, 2, 0), (2, 0, 1), (2, 1, 0))


def sort_reread_text(text):
    text = _replace(text, '      SORTVERTICES,\n',
                    '      SORTVERTICES,\n      SORT_ADDR,\n      SORT_READ1,\n      SORT_READ2,\n      SORT_READ3,\n')
    text = _replace(text, '   signal loadSecond         : std_logic := \'0\';\n',
                    '   signal loadSecond         : std_logic := \'0\';\n'
                    '   -- gpu-sort-reread: vertex RAM addresses of the triangle, as loaded and sorted by y.\n'
                    '   type tVertexIndex is array(0 to 2) of integer range 0 to 3;\n'
                    '   signal triIdx             : tVertexIndex := (others => 0);\n'
                    '   signal sortIdx            : tVertexIndex := (others => 0);\n')
    # Read address: the sorted addresses in the re-read states.
    text = _replace(text, '   process (state, loadSecond, ramRaIndex, rec_quad, quadFirst)\n',
                    '   process (state, loadSecond, ramRaIndex, rec_quad, quadFirst, sortIdx)\n')
    text = _replace(text, '''      elsif (state = ISSUE_READ2) then
         if (loadSecond = '1') then ramRa <= 3; else ramRa <= 2; end if;
      end if;''', '''      elsif (state = ISSUE_READ2) then
         if (loadSecond = '1') then ramRa <= 3; else ramRa <= 2; end if;
      elsif (state = SORT_ADDR) then
         ramRa <= sortIdx(0);
      elsif (state = SORT_READ1) then
         ramRa <= sortIdx(1);
      elsif (state = SORT_READ2) then
         ramRa <= sortIdx(2);
      end if;''')
    for k in range(3):
        nxt = ('ISSUE_READ2', 'ISSUE_READ3', 'SORTVERTICES')[k]
        text = _replace(text, f'''               when ISSUE_READ{k+1} =>
                  vt({k}) <= readVertex;
                  state <= {nxt};''', f'''               when ISSUE_READ{k+1} =>
                  vt({k}) <= readVertex;
                  triIdx({k}) <= ramRaIndex;
                  state <= {nxt};''')
    text = _replace(text, '''               when SORTVERTICES =>
                  state     <= CALCBOUNDARY1;
                  firsthalf <= '1';
''', '''               when SORTVERTICES =>
                  state     <= SORT_ADDR;
                  firsthalf <= '1';
                  sortIdx   <= triIdx;   -- 0 1 2 requires no resorting
''')
    for p in PERMUTATIONS:
        old = ''.join(f'                     vt({k}) <= vt({p[k]});\n' for k in range(3))
        new = ''.join(f'                     sortIdx({k}) <= triIdx({p[k]});\n' for k in range(3))
        text = _replace(text, old, new)
    text = _replace(text, '''               when CALCBOUNDARY1 =>
''', '''               when SORT_ADDR =>
                  state <= SORT_READ1;

               when SORT_READ1 =>
                  vt(0) <= readVertex;
                  state <= SORT_READ2;

               when SORT_READ2 =>
                  vt(1) <= readVertex;
                  state <= SORT_READ3;

               when SORT_READ3 =>
                  vt(2) <= readVertex;
                  state <= CALCBOUNDARY1;

               when CALCBOUNDARY1 =>
''')
    if re.search(r'vt\(\d\) <= vt\(', text):
        raise ValueError('an in-place vertex permutation is left')
    return text
