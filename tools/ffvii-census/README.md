# FFVII hardware census

Logs which PS1 hardware FFVII exercises, using Beetle PSX (Mednafen) with
counters added. Nothing here runs on the Pocket.

```sh
git clone https://github.com/libretro/beetle-psx-libretro beetle
git -C beetle checkout e57860d82faa953dc0fc10f201fd583739bc2f7d
git -C beetle apply ../beetle-census.patch
make -C beetle -j
gcc -O2 -o frontend frontend.c -Ibeetle/libretro-common/include -ldl
mkdir -p sys save out && cp <path>/scph5501.bin sys/
PSX_CENSUS=census.json ./frontend beetle/mednafen_psx_libretro.so sys save \
  "<FFVII Disc 1>.cue" ffvii1.txt out 60000 2000
```

`ffvii1.txt` is the scripted input (frame ranges and buttons; see
`frontend.c`). The frontend runs the interpreter CPU and software renderer
headless at several hundred frames per second and writes PPM screenshots.
`census-session1.json` is the result of the first session. See
`docs/ffvii-hardware-census.md` for the findings.

## SPU trace and RTL replay

Set `PSX_SPU_TRACE=spu.bin` to record, per output sample, every SPU register
write, DMA SPU-RAM write, the CD audio input and Mednafen's final output.
Optional switches: `PSX_SPU_VOICE=n` adds one voice's interpolated sample,
envelope and volumes; `PSX_SPU_CNT_CLEAR=0x80` masks SPUCNT bits (0x80 turns
reverb off in both); `PSX_SPU_NODELAY=1` removes Mednafen's 4-sample key-on
play delay. Replay into the RTL SPU with:

```sh
python3 scripts/run_spu_replay.py spu.bin --samples 90000 [--voice n]
```

## GPU trace and RTL replay

Set `PSX_GPU_TRACE=gpu.bin` to record every accepted GP0 word, every GP1
write and every VRAM-to-CPU read, plus 1 MiB VRAM snapshots at idle frame
ends. `PSX_GPU_FIRST` is the first frame (recording starts at the first idle
frame end from there), `PSX_GPU_FRAMES` the length (default 600) and
`PSX_GPU_EVERY` the snapshot interval (default 30). `PSX_GPU_PROV=1` also
writes, per snapshot, which GP0 command (index of its first recorded word)
last wrote each pixel. The internal resolution must be 1x. Replay into the
pinned MiSTer GPU with:

```sh
python3 scripts/run_gpu_replay.py gpu.bin
```

## CPU speed

`PSX_CPU_SCALE=70%` sets Beetle's CPU frequency scale (50% to 750%), to
see how a game looks on a core slower than a real PSX
(`docs/tekken3-graphics.md`).

`beetle-census.patch` modifies Beetle PSX and is GPL-2.0, like Beetle PSX.
