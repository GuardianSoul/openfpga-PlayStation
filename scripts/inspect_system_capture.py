#!/usr/bin/env python3
"""Render captured PSX 15-bit VRAM and summarize captured stereo PCM locally."""
import argparse
import csv
from array import array
import json
from pathlib import Path
import struct
import sys
import wave
import zlib


def write_png(path, width, height, rows):
    def chunk(tag, data):
        return struct.pack('!I', len(data)) + tag + data + struct.pack('!I', zlib.crc32(tag + data))
    data = b''.join(b'\0' + row for row in rows)
    path.write_bytes(b'\x89PNG\r\n\x1a\n' +
                     chunk(b'IHDR', struct.pack('!IIBBBBB', width, height, 8, 2, 0, 0, 0)) +
                     chunk(b'IDAT', zlib.compress(data)) + chunk(b'IEND', b''))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--width', type=int, default=1024)
    parser.add_argument('--height', type=int, default=512)
    parser.add_argument('--x', type=int, default=0)
    parser.add_argument('--y', type=int, default=0)
    parser.add_argument('--snapshot',type=int,help='Use a completed periodic snapshot and its recorded display metadata')
    parser.add_argument('--rgb24',action='store_true',help='Extract byte-packed RGB24; periodic snapshots select their recorded mode')
    args = parser.parse_args()
    metadata=None;suffix=''
    if args.snapshot is not None:
        if args.snapshot<0:parser.error('Snapshot cycle count must be nonnegative')
        suffix=f'-{args.snapshot:012d}'
        with (args.directory/f'snapshot{suffix}.csv').open() as file:metadata=next(csv.DictReader(file))
        for key in ('width','height','x','y'):setattr(args,key,int(metadata[key]))
        args.rgb24=bool(int(metadata['rgb24']))
    if not (0 < args.width <= 1024 and 0 < args.height <= 512 and
            0 <= args.x < 1024 and 0 <= args.y < 512):
        parser.error('Display region must fit the 1024x512 primary VRAM domain')
    words = [int(line, 16) for line in (args.directory / f'vram{suffix}.hex32').read_text().splitlines()]
    if len(words) != 262144 or any(word < 0 or word > 0xffffffff for word in words):
        parser.error('Expected exactly 262144 captured 32-bit primary VRAM words')
    pixels = array('H', (pixel for word in words for pixel in (word & 65535, word >> 16)))
    raw=b''.join(word.to_bytes(4,'little') for word in words) if args.rgb24 else None
    rows = []
    for y in range(args.height):
        row = bytearray()
        for x in range(args.width):
            if args.rgb24:
                base=((args.y+y)&511)*2048
                byte=(args.x*2+x*3)%2048
                row.extend(raw[base+(byte+channel)%2048] for channel in range(3))
            else:
                pixel = pixels[((args.y + y) & 511) * 1024 + ((args.x + x) & 1023)]
                row.extend((((pixel >> shift) & 31) << 3) | (((pixel >> shift) & 31) >> 2) for shift in (0, 5, 10))
        rows.append(bytes(row))
    write_png(args.directory / f'display{suffix}.png', args.width, args.height, rows)
    pcm = (args.directory / 'audio.pcm').read_bytes()
    if metadata:
        # First periodic schema predates the explicit sample count. Its
        # negative-edge snapshot follows cycles 0..stamp-1, sampled every 768.
        frames=int(metadata.get('audio_samples',(int(metadata['cycles'])+767)//768))
        audio_start=int(metadata.get('audio_start_sample',0))
        if not 0<=audio_start<=frames:parser.error('Invalid restored audio segment bounds')
        needed=(frames-audio_start)*4
        if len(pcm)<needed:parser.error('Snapshot audio prefix has not been flushed yet')
        pcm=pcm[:needed]
    if len(pcm) % 4:
        parser.error('Stereo 16-bit PCM must contain complete frames')
    samples = array('h', pcm)
    if sys.byteorder != 'little':
        samples.byteswap()
    nonzero = sum(left != 0 or right != 0 for left, right in zip(samples[0::2], samples[1::2]))
    with wave.open(str(args.directory / f'audio{suffix}.wav'), 'wb') as file:
        file.setnchannels(2)
        file.setsampwidth(2)
        file.setframerate(44100)
        file.writeframes(pcm)
    report = {'format': 'PSX byte-packed RGB24' if args.rgb24 else 'PSX RGB555 VRAM extraction; bit 15 ignored',
              'snapshot_metadata':metadata,
              'display_region': {'width': args.width, 'height': args.height, 'x': args.x, 'y': args.y},
              'nonzero_vram_words': sum(word != 0 for word in words),
              'audio_sample_rate': 44100, 'audio_frames': len(pcm) // 4,
              'audio_start_frame':int(metadata.get('audio_start_sample',0)) if metadata else 0,
              'audio_nonzero_frames': nonzero, 'audio_duration_seconds': len(pcm) / (4 * 44100),
              'pcm_min': [min(samples[channel::2], default=0) for channel in (0, 1)],
              'pcm_max': [max(samples[channel::2], default=0) for channel in (0, 1)],
              'display_scanout_verified': False, 'audio_correctness_verified': False,
              'game_verified': False, 'hardware_verified': False}
    (args.directory / f'media-inspection{suffix}.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
