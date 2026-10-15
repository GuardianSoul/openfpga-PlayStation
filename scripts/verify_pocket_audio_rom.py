"""Check Quartus's emitted coefficient ROM against the authored integer bank.

Verifies every initialization bit, including the unity sentinel, after the
mapper's hexadecimal-width warnings. Read-only; no vendor/project export.
"""
from pathlib import Path
import re

from prepare_resource_targets import digest


def verify(project,coefficients):
    project,coefficients=Path(project),Path(coefficients)
    candidates=[p for p in (project/'db').glob('*.hdl.mif')
                if '-- psx_audio_sinc(COEFFICIENT_FILE' in p.read_text()]
    if len(candidates)!=1:raise ValueError('Expected exactly one emitted audio coefficient ROM')
    return verify_mif(candidates[0], coefficients, 18)


def verify_mif(mif, coefficients, width):
    mif, coefficients = Path(mif), Path(coefficients)
    text=mif.read_text()
    expected=[int(line,16) for line in coefficients.read_text().splitlines()]
    if not re.search(r'\bWIDTH\s*=\s*'+str(width)+r'\s*;',text) or not re.search(r'\bDEPTH\s*=\s*'+str(len(expected))+r'\s*;',text):
        raise ValueError('Mapped audio coefficient ROM geometry changed')
    if 'ADDRESS_RADIX=UNS;' not in text or 'DATA_RADIX=BIN;' not in text:
        raise ValueError('Mapped audio coefficient ROM radix changed')
    body=text.split('CONTENT BEGIN',1)[1].split('END;',1)[0]
    values={}
    for line in body.splitlines():
        if not line.strip():continue
        match=re.fullmatch(r'\s*(?:\[(\d+)\.\.(\d+)\]|(\d+))\s*:\s*([01]{'+str(width)+r'})\s*;\s*',line)
        if not match:raise ValueError('Unrecognized mapped audio ROM row')
        first=int(match[1] or match[3]);last=int(match[2] or match[3])
        for address in range(first,last+1):
            if address in values or not 0<=address<len(expected):raise ValueError('Mapped audio ROM address duplicate/range')
            values[address]=int(match[4],2)
    if len(values)!=len(expected) or any(values[i]!=v for i,v in enumerate(expected)):
        raise ValueError('Mapped audio coefficient initialization differs')
    return {'passed':True,'words':len(expected),'bits_per_word':width,
            'mif_path':str(mif.resolve()),'mif_sha256':digest(mif),
            'authored_path':str(coefficients.resolve()),'authored_sha256':digest(coefficients),
            'all_initialization_bits_equal':True,'hardware_verified':False}


def verify_packed(project, files):
    """Verify every Quartus-emitted constant in all four exact-width banks."""
    project = Path(project)
    candidates = [p for p in (project/'db').glob('*.hdl.mif')
                  if '-- psx_audio_sinc(COEFFICIENT_FILE' in p.read_text()]
    if len(candidates) != 4 or len(files) != 4:
        raise ValueError('Expected four emitted packed audio coefficient ROMs')
    result = []
    for width, expected in zip((14, 20, 24, 36), files):
        matches = [p for p in candidates if re.search(r'\bWIDTH\s*=\s*'+str(width)+r'\s*;', p.read_text())]
        if len(matches) != 1:
            raise ValueError('Packed mapped audio ROM geometry changed')
        result.append(verify_mif(matches[0], expected, width))
    return dict(passed=True, banks=result, words=sum(r['words'] for r in result),
                all_initialization_bits_equal=True, hardware_verified=False)
