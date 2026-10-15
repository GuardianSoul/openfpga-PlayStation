"""Single-track raw-disc layout for the pinned MiSTer host interface.

Protocol inspected in Main_MiSTer 2ec7c0b74f6bf83d177f41e4238187ed37d55386,
psx.cpp send_cue_and_metadata/psx_read_cd, and pinned PSX cd_top.vhd.
This original adapter reuses the checked local CUE reader; it does not copy
ARM/HPS host implementation code or emulate mechanical/decoder behavior.
"""
from pathlib import Path
import json

if __package__:
    from .disc_image import DiscImage,RAW_SECTOR_SIZE
else:
    from disc_image import DiscImage,RAW_SECTOR_SIZE

PREGAP=150


def bcd(value):
    if type(value) is not int or value<0 or value>99:raise ValueError('BCD value outside two digits')
    return (value//10)*16+value%10


def metadata_words(sectors,region=2):
    if type(sectors) is not int or sectors<=0 or sectors+PREGAP>524288:
        raise ValueError('Disc does not fit the controller\'s 19-bit physical LBA domain')
    if type(region) is not int or region not in (0,1,2,3):raise ValueError('Invalid host region code')
    end=sectors+PREGAP
    minutes,seconds=divmod(end//75,60)
    words=[0]*400
    words[:8]=[0x0101,end,(bcd(minutes)<<8)|bcd(seconds),region<<16,
        0,end-1,0x0002,0]
    return words


def multitrack_metadata_words(track_sectors,region=2):
    """Track table for files played back to back (psx_cd_stream MAX_TRACKS>1).

    Track 1 starts at physical LBA 150 (pregap not in its file); every later
    file starts with its own 150-sector pregap, so its INDEX 01 is 150 sectors
    in. Tracks 2+ are audio. One track gives exactly metadata_words.
    """
    if not track_sectors or len(track_sectors)>99:raise ValueError('1 to 99 tracks')
    if any(type(n) is not int or n<=0 for n in track_sectors[:1]) or any(type(n) is not int or n<=PREGAP for n in track_sectors[1:]):
        raise ValueError('Track sizes: track 1 at least one sector, later tracks longer than their pregap')
    first=[PREGAP]
    for n in track_sectors[:-1]:first.append(first[-1]+n)
    end=first[-1]+track_sectors[-1]
    words=metadata_words(sum(track_sectors),region)
    count=len(track_sectors)
    words[0]=(bcd(count)<<8)|count
    for k in range(count):
        index01=PREGAP if k==0 else first[k]+PREGAP
        minutes,seconds=divmod(index01//75,60)
        last=(first[k+1] if k+1<count else end)-1
        words[4*k+4:4*k+8]=[0 if k==0 else first[k],last,((k!=0)<<16)|(bcd(minutes)<<8)|bcd(seconds),0]
    return words


class DiscHost:
    def __init__(self,cue):
        self.disc=DiscImage(cue)
        try:self.metadata=metadata_words(self.disc.track.sectors)
        except ValueError:
            self.disc.file.close()
            raise

    def __enter__(self):return self
    def __exit__(self,*args):return self.disc.__exit__(*args)

    def read_physical(self,lba):
        if type(lba) is not int or lba<0 or lba>0xffffffff:raise ValueError('Invalid physical LBA')
        if lba<PREGAP:return bytes(RAW_SECTOR_SIZE)
        if lba>=PREGAP+self.disc.track.sectors:return bytes([0xaa])*RAW_SECTOR_SIZE
        return self.disc.read_raw(lba-PREGAP)

    def prepare(self,directory):
        directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
        metadata=directory/'trackinfo.hex32'
        metadata.write_text(''.join(f'{word:08x}\n' for word in self.metadata))
        report={'cue':str(self.disc.track.cue),'image':str(self.disc.track.image),
            'index_sector':self.disc.track.index_sector,'sectors':self.disc.track.sectors,
            'physical_first_data_lba':PREGAP,'physical_last_data_lba':self.metadata[1]-1,
            'metadata_words':400,'host_region':2,'core_region':0,
            'protocol_source':'Main_MiSTer 2ec7c0b74f6bf83d177f41e4238187ed37d55386; pinned PSX cd_top',
            'scope':'US fixture, one MODE2/2352 data track; no CHD/audio tracks, SBI, or disc swaps',
            'game_verified':False,'hardware_verified':False}
        (directory/'disc-host.json').write_text(json.dumps(report,indent=2)+'\n')
        return {**report,'metadata':metadata}
