# Timing: the Pocket core against a real PlayStation

These figures compare the core with a real PlayStation (a PAL SCPH-5502). Our test discs time each operation with
the console's own timer. The console column is what the PlayStation measured, the core column is this release
run cycle by cycle in a simulator.
CD-R read retries and the wear of the test console's drive are not copied.

*Video-clock rows are scaled to the PAL GPU clock; a few rows the console has no figure for use published
measurements of another PlayStation.*

Ratios above 1 mean the core is slower than the console, below 1 faster.

## How close, per area

Share of rows within 2 % / 5 % / 10 % of the PlayStation, on the rows both versions were measured on.

| Area | 0.5.10: rows, ≤2 % / ≤5 % / ≤10 % | 0.6.0: rows, ≤2 % / ≤5 % / ≤10 % |
|---|---|---|
| CPU/mem | 253: 92% / 94% / 95% | 253: 92% / 94% / 95% |
| GPU | 48: 40% / 40% / 42% | 48: 58% / 58% / 67% |
| DMA | 187: 56% / 64% / 75% | 187: 78% / 95% / 99% |
| CD | 40: 55% / 65% / 70% | 40: 90% / 95% / 95% |
| SPU | 34: 100% / 100% / 100% | 34: 94% / 97% / 100% |
| MDEC | 28: 96% / 96% / 100% | 28: 93% / 100% / 100% |
| GTE | 24: 96% / 96% / 96% | 24: 96% / 96% / 96% |
| Timers/IRQ | 105: 95% / 95% / 95% | 105: 90% / 90% / 90% |
| **All** | 719: 78% / 82% / 86% | 719: 86% / 92% / 94% |

## CPU and memory

| Measurement | Console | Core | Core / console |
|---|---:|---:|---:|
| Load from main RAM, same row (cycles per loop) | 8.37 | 8.37 | 1.000 |
| Load from main RAM, then a free slot (cycles per loop) | 8.25 | 7.25 | 0.879 |
| 32 stores to main RAM in a row (cycles per loop) | 2.21 | 2.18 | 0.986 |
| Multiply and read the result (cycles per loop) | 11.06 | 11.09 | 1.003 |
| Divide and read the result (cycles per loop) | 39.06 | 39.25 | 1.005 |
| Read main RAM, 8 / 16 / 32-bit (cycles) | 5.10 5.12 5.08 | 4.96 5.02 5.01 | 0.980 |
| Read a CD drive register, 8 / 16 / 32-bit (cycles) | 13.96 30.96 64.96 | 13.93 30.93 64.90 | 0.999 |
| Read a sound chip register, 8 / 16 / 32-bit (cycles) | 23.96 23.96 51.96 | 23.93 23.93 51.98 | 0.999 |
| Read the GPU status register, 8 / 16 / 32-bit (cycles) | 3.02 3.02 3.02 | 3.15 3.14 3.13 | 1.040 |
| Two stores 64 KB apart (cycles) | 9 | 9 | 1.000 |
| Two stores 1 KB apart, then a load (cycles) | 20 | 21 | 1.050 |

## GPU

| Measurement | Console | Core | Core / console |
|---|---:|---:|---:|
| Shaded triangle 64x64 (CPU cycles per triangle) | 2419.97 | 2404.18 | 0.993 |
| 15-bit sprite 256x240 | 183920.02 | 180852.00 | 0.983 |
| 15-bit textured quad 128x128 | 93346.90 | 91502.50 | 0.980 |
| 4-bit sprite 64x64 | 2323.08 | 2260.65 | 0.973 |
| 4-bit textured see-through triangle 32x32 | 1048 | 1053 | 1.005 |
| 4-bit textured triangle 32x32 | 1000 | 738 | 0.738 |
| 8-bit sprite 64x64 (kept fast) | 7671.13 | 6028.40 | 0.786 |
| Line, 100 pixels diagonal | 535.23 | 538.25 | 1.006 |
| See-through line, 100 pixels | 489.16 | 527.48 | 1.078 |
| Upload 320x240 to video memory (ms, scan lines, MB/s) | 810.92 12808 73 | 821.2620 12958 73 | 1.012 |
| Download 320x240 from video memory | 982.15 15495 61 | 997.6340 15739 60 | 1.016 |
| Copy 320x240 inside video memory | 1272.64 20078 47 | 1214.1080 19158 49 | 0.954 |
| Fill the screen, 4 times (scan lines) | 13 | 13 | 1.000 |

## DMA

| Measurement | Console | Core | Core / console |
|---|---:|---:|---:|
| Mixed load: CPU loop alone (cycles per pass x100) | 39.40 | 37.51 | 0.952 |
| Mixed load: CPU loop while the CD streams at double speed | 49.82 | 47.52 | 0.954 |
| Mixed load: CPU loop with everything running | 58.17 | 56.58 | 0.973 |
| Mixed load, everything running: time per CD sector (cycles) | 225792 | 225792 | 1.000 |
| Mixed load, everything running: 4 KB to sound memory (cycles) | 90642 | 89559 | 0.988 |
| Mixed load, everything running: video decoder block in and out (cycles) | 61861 | 62020 | 1.003 |
| Mixed load: video decoder block in and out, alone (cycles) | 49855 | 49481 | 0.992 |
| Mixed load, everything running: GPU frame list sent (cycles) | 122071 | 110405 | 0.904 |

## CD drive

| Measurement | Console | Core | Core / console |
|---|---:|---:|---:|
| Reply to GetStat (CPU cycles) | 39000 | 39068 | 1.002 |
| Reply to Setloc | 58000 | 58076 | 1.001 |
| Reply to ReadN, single speed | 43568 | 43632 | 1.001 |
| First sector after a read command, single speed | 3800000-4260000 | 3979488 | 0.987 |
| Switch to double speed, then the first sector | 19790000 | 19197360 | 0.970 |
| Time per sector, single speed | 451240 | 451472 | 1.001 |
| Time per sector, double speed | 225510 | 225688 | 1.001 |
| Pause while reading, single speed | 3790000 | 3779860 | 0.997 |
| Drive start-up (Init) to ready | 4369768 | 476299 | 0.109 |

## Sound (SPU)

| Measurement | Console | Core | Core / console |
|---|---:|---:|---:|
| Capture buffer half flag period (cycles) | 196608 196608 196624 | 196592 196608 196608 | 1.000 |
| Voice start to end flag, 2 blocks (cycles) | 1000 39088 39120 39120 | 1000 39096 39136 39136 | 1.000 |
| Pitch modulation up (cycles) | 32840 32896 32928 | 32160 32192 32224 | 0.979 |
| Pitch modulation down (cycles) | 49752 49784 49808 | 52168 52168 52208 | 1.048 |
| Sound memory interrupt, voice start to interrupt (cycles) | 488 608 728 | 1416 1432 2208 | 2.355 |

## Geometry (GTE)

| Measurement | Console | Core | Core / console |
|---|---:|---:|---:|
| RTPS, transform one point (cycles, 32 runs) | 546 | 547 | 1.002 |
| RTPT, transform three points | 802 | 803 | 1.001 |
| NCLIP, back-face test | 322 | 323 | 1.003 |
| AVSZ3, average depth | 226 | 227 | 1.004 |
| NCDS, light one colour | 674 | 675 | 1.001 |
| NCDT, light three colours | 1474 | 1475 | 1.001 |
| MVMVA, matrix times vector | 322 | 323 | 1.003 |
| DPCT, depth cue three colours | 610 | 707 | 1.159 |

## Timers and interrupts

| Measurement | Console | Core | Core / console |
|---|---:|---:|---:|
| Timer 0, system clock, after a 1,000-cycle wait (counts) | 1015 | 1015 1015 1015 1015 1015 | 1.000 |
| Timer 0, system clock, after a 5,000-cycle wait | 5015 | 5015 5015 5015 5015 5015 | 1.000 |
| Interrupt to handler, while the CPU runs a loop (cycles) | 40 40 45 | 40 40 41 | 1.000 |
| Interrupt to handler, while the CPU divides | 67 72 75 | 70 70 72 | 0.972 |

## Known differences and why

Where the core and the PlayStation still differ, and why. Where we had to choose, the core errs on the fast side,
so games don't slow down.

**CPU and memory**
- **Shared memory on the Pocket.** A PlayStation gives the CPU, the GPU and the sound chip their own memory; on the
  Pocket they share memory chips. The core schedules their turns so that the mixed-load test stays within about 3 % of the
  console with everything running.
- **Loads that follow each other directly are about 1 cycle short each**, from main memory and from the console's
  registers (for example the GPU status register). This errs fast and is next on the list.
- **A read to another memory page that follows a store starts 2 cycles early.** This was a deliberate trade: with
  everything running, the mixed-load test's CPU loop is now within 3 % of the console. On its own it
  runs about 5 % fast.
- **Reading the console's internal registers** (interrupts, timers, GPU status and the like) takes about 3.1 cycles
  instead of 3.02. This comes from the store timing, which follows the console closely for runs of writes to main
  memory; that matters more to games.
- **The first byte read from the BIOS chip or an expansion port is 2 cycles short.** Every further byte costs exactly
  what the console charges.
- **A few one-off test rows move by a few cycles between builds.** In these rows a single measurement can land on
  the Pocket memory's refresh slot, depending on how the build lines up. They are exact on builds where it misses.

**GPU**

Of the 148 GPU measurements, 68 are within 2 % of the console, 72 are faster and 8 are slower.

Most GPU differences are on the fast side:
- **Very small shapes and short lines finish sooner.** The console spends a fixed setup time on every drawing command,
  and the core doesn't copy it yet. Larger shapes aren't affected.
- **4-bit and 8-bit textured drawing is kept at 0.5.10's speed, faster than the console.** With the console's exact
  timing some games' frames ran late (Bloody Roar II). See-through textured shapes already match the console, because
  their video-memory reads set the pace. Very large sprites are also a little fast.
- **Copies inside video memory run about 5 % fast**, within the copy model's accuracy.
- **In the mixed-load test a frame's drawing list is done 5-10 % sooner**, in line with the faster drawing above.

The slower ones:
- **See-through (semi-transparent) lines and rectangles are about 6-10 % slow.** They read and rewrite every pixel
  in video memory, and the Pocket's video memory is slightly slower at that than the console's. This is a
  memory-speed limit, not a timing setting.
- **Sloped lines are charged slightly too much per row** (8.4 GPU clocks instead of 8.0), about 6 % on a polyline.
  The fix is ready for the next version.

**DMA**
- **Some DMA rows measured on another console differ by a few cycles.** A handful of very short DMA transfers
  (1-16 words) were measured on a different PlayStation, not on our reference console. They now take 2-7 cycles
  longer, a side effect of the memory-timing change above. The longer transfers that games use are unaffected.
- **"Chopping" DMA mode with the smallest CPU window is 3-9 % slow.** In this mode a GPU transfer pauses
  regularly to let the CPU run. Our reference console has no measurement of it yet; the only data comes from another
  console and doesn't fit one consistent cost. A later test disc will measure it.

**CD drive**
- **After Stop, the core doesn't yet model the disc motor spinning back up** (about 2 seconds on a PlayStation). A
  read or Init right after Stop therefore starts too early.
- **Drive start-up (Init) is much faster than on the console** (about 0.5 million cycles instead of 4.4 million),
  and the first command after a disc is loaded is answered faster too: the core skips the drive's start-up work.
- **Some command replies use one average time.** The console answers GetlocP faster at single speed and slower at
  double speed, and answers Setloc about 10 % faster while paused; the core uses one average for each.
- **Switching to double speed and reading the first sector comes about 3 % early.** The speed change is modelled
  from the average of the console's measurements.
- **CD reading on a burned disc.** The reference console retries some reads on its burned test discs, and its drive
  is old. Those retries and the wear aren't copied: the CD rows use clean samples. In the mixed-load test the drive
  also stalled at double speed, so those rows are rescaled to a steady double-speed stream: the CPU time the console
  loses per sector (measured) times the sectors per second of a clean stream. Measured with the stalls, the console's
  "CPU loop while the CD streams at double speed" read 47.13 instead of 49.82.

**Sound (SPU)**
- **SPU memory interrupts fire later than on the console.** The console reads sample memory ahead of where it plays;
  the core doesn't yet. Games that wait for this interrupt may react slightly late.
- **Pitch modulation depth differs by up to about 5 %.** The console modulates less than the documented formula;
  why is not yet known.

**Geometry (GTE)**
- **One geometry operation, DPCT (depth cue of three colours), takes 3 cycles longer** than on the console (28
  instead of 25). The core works out its three colours one after another where the console overlaps part of the
  work. The test repeats it 32 times, so its row shows about 16 % slow. Games rarely use DPCT. The fix is ready and
  goes in when there is room in the chip.

**Timers and interrupts**
- **The time from an interrupt to its handler differs by a few cycles either way.** It depends on what the CPU is
  doing when the interrupt arrives, for example a geometry operation or a memory load in progress.
- **Timer-reset test: the start lands one cycle off.** The loop itself runs at the console's speed. A one-cycle
  difference in where it starts makes the test's histogram land on the neighbouring values, so five rows of that
  test differ while the timing they measure is right. A later test disc will measure the step behind it.

