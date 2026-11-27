# Timing: the Pocket core against a real PlayStation

0.6.0 and 0.6.1 against a real PlayStation (a PAL SCPH-5502), on the same test disc. The disc times each operation with the console's own timer. CD-R read retries and the wear of the test console's drive are not copied.

Ratios are Pocket / console: 1.000 is equal, above 1 the Pocket is slower, below 1 faster. Video-clock rows are scaled to the PAL GPU clock: there the Pocket value is shown scaled, with the raw count in brackets, so value / console gives the ratio.

The 0.6.0 column is what 0.6.0 published: its timing scoreboard, the core in simulation against the references known then (its ratio is its value over that reference; the value is shown only where that reference is within 2 % of today's console column). The 0.6.1 column is measured on a Pocket.

## How close, per area

Share of rows within 2 % / 5 % / 10 % of the PlayStation.

| Area | 0.6.0: rows, ≤2 % / ≤5 % / ≤10 % | 0.6.1: rows, ≤2 % / ≤5 % / ≤10 % |
|---|---|---|
| CPU and memory | 606: 72% / 79% / 87% | 1007: 73% / 81% / 92% |
| GPU | 80: 15% / 30% / 48% | 112: 25% / 59% / 93% |
| DMA | 26: 96% / 96% / 100% | 236: 43% / 62% / 74% |
| CD drive | 38: 87% / 92% / 95% | 41: 68% / 76% / 80% |
| Sound (SPU) | 13: 31% / 46% / 46% | 14: 43% / 57% / 64% |
| Geometry (GTE) | 23: 96% / 96% / 96% | 23: 100% / 100% / 100% |
| Timers and interrupts | 58: 97% / 98% / 100% | 102: 75% / 87% / 94% |
| **All** | **844: 70% / 76% / 85%** | **1535: 65% / 77% / 89%** |
| Rows both measured | 844: 70% / 76% / 85% | 844: 69% / 80% / 91% |
| Areas weighted equally | 70% / 77% / 82% | 61% / 75% / 85% |

Exact checks (results that must equal the console's, such as decoded pixels and checksums):

| Area | Checks | 0.6.0 matching | 0.6.1 matching |
|---|---:|---:|---:|
| CPU and memory | 30 | 12 of 12 | 24 of 30 |
| GPU | 24 | 19 of 23 | 24 of 24 |
| DMA | 44 | 0 of 0 | 12 of 44 |
| CD drive | 124 | 0 of 0 | 124 of 124 |
| Sound (SPU) | 6 | 0 of 6 | 0 of 6 |
| Video decoder (MDEC) | 112 | 3 of 31 | 111 of 112 |
| Geometry (GTE) | 37 | 14 of 14 | 37 of 37 |
| Timers and interrupts | 51 | 46 of 46 | 51 of 51 |

## CPU and memory

### CPUMEM2  Uncached CPU and RAM timings (exact checks)

| Check | 0.6.0 | 0.6.1 |
|---|:---:|:---:|
| lw I_STAT, no use | - | differs |
| sw I_STAT | - | differs |

### Peip  Load816Unalign (exact checks)

| Check | 0.6.0 | 0.6.1 |
|---|:---:|:---:|
| SPAD | - | matches |
| DMA | - | matches |
| SIO | - | differs |
| JOY | - | matches |
| IRQ | - | matches |
| SPU | - | matches |
| MEMC | matches | matches |

### Peip  LoadStoreReg (exact checks)

| Check | 0.6.0 | 0.6.1 |
|---|:---:|:---:|
| SPAD | - | matches |
| DMA | - | matches |
| SIO | - | differs |
| JOY | - | matches |
| IRQ | - | matches |
| SPU | - | matches |
| EXP2 | matches | matches |

### Peip  LoadStoreRegUnalign8Bit (exact checks)

| Check | 0.6.0 | 0.6.1 |
|---|:---:|:---:|
| SPAD | - | matches |
| DMA | - | matches |
| SIO | matches | differs |
| JOY | matches | matches |
| IRQ | - | matches |
| SPU | - | matches |
| EXP2 | matches | matches |

### Peip  LoadStoreRegUnalign16Bit (exact checks)

| Check | 0.6.0 | 0.6.1 |
|---|:---:|:---:|
| SPAD | matches | matches |
| DMA | matches | matches |
| SIO | matches | differs |
| JOY | matches | matches |
| IRQ | matches | matches |
| SPU | matches | matches |
| EXP2 | matches | matches |

### CPU/mem: external bus under disc delays

| Measurement | Console | 0.6.0 | 0.6.1 | 0.6.0 / console | 0.6.1 / console |
|---|---:|---:|---:|---:|---:|
| base G4 | 44 | 44 | 44 | 1.000 | 1.000 |
| base G64 | 524 | 524 | 524 | 1.000 | 1.000 |
| CD 8 G4 | 143 | 140 | 140 | 0.979 | 0.979 |
| CD 8 G64 | 542 | 541 | 541 | 0.998 | 0.998 |
| CD 16 G0 | 277 | 276 | 276 | 0.996 | 0.996 |
| CD 16 G4 | 279 | 276 | 276 | 0.989 | 0.989 |
| CD 16 G64 | 542 | 541 | 541 | 0.998 | 0.998 |
| CD 32 G0 | 549 | 548 | 548 | 0.998 | 0.998 |
| CD 32 G64 | 571 | 574 | 573 | 1.005 | 1.004 |
| EXP2 8 G64 | 552 | 551 | 551 | 0.998 | 0.998 |
| BIOS 32 G0 | 269 | 269 | 285 | 1.000 | 1.059 |
| SPU 16 G0 | 221 | 220 | 220 | 0.995 | 0.995 |
| CD 16 isolated | 135 | 135 | 135 | 1.000 | 1.000 |
| BIOS 32 isolated | 135 | 135 | 135 | 1.000 | 1.000 |
| CD16>RAM K4 | 386 | 377 | 377 | 0.977 | 0.977 |
| CD16>ISTAT K0 | 378 | 377 | 377 | 0.997 | 0.997 |
| EXP2b>BIOSb K0 | 332 | 334 | 335 | 1.006 | 1.009 |
| RAM>CD16 K0 | 298 | 301 | 301 | 1.010 | 1.010 |
| ISTAT>CD16 K0 | 290 | 293 | 293 | 1.010 | 1.010 |
| C0 CD 8 G64 | 552 | 551 | 551 | 0.998 | 0.998 |
| C0 CD 16 G0 | 133 | 132 | 146 | 0.992 | 1.098 |
| base G0 [S spacing: 8 x (read; G nops); base = the read as a nop] | 12 | 12 | 12 | 1.000 | 1.000 |
| base G2 | 28 | 28 | 28 | 1.000 | 1.000 |
| base G4 [S spacing: 8 x (read; G nops); base = the read as a nop] | 44 | 44 | 44 | 1.000 | 1.000 |
| base G8 | 76 | 76 | 76 | 1.000 | 1.000 |
| base G16 | 140 | 140 | 140 | 1.000 | 1.000 |
| base G32 | 278 | 278 | 279 | 1.000 | 1.004 |
| base G64 [S spacing: 8 x (read; G nops); base = the read as a nop] | 524 | 524 | 524 | 1.000 | 1.000 |
| CD 8 G0 [S spacing: 8 x (read; G nops); base = the read as a nop] | 141 | 140 | 140 | 0.993 | 0.993 |
| CD 8 G2 | 143 | 140 | 140 | 0.979 | 0.979 |
| CD 8 G4 [S spacing: 8 x (read; G nops); base = the read as a nop] | 143 | 140 | 140 | 0.979 | 0.979 |
| CD 8 G8 | 143 | 140 | 140 | 0.979 | 0.979 |
| CD 8 G16 | 158 | 157 | 157 | 0.994 | 0.994 |
| CD 8 G32 | 296 | 295 | 297 | 0.997 | 1.003 |
| CD 8 G64 [S spacing: 8 x (read; G nops); base = the read as a nop] | 542 | 541 | 541 | 0.998 | 0.998 |
| CD 16 G0 [S spacing: 8 x (read; G nops); base = the read as a nop] | 277 | 276 | 276 | 0.996 | 0.996 |
| CD 16 G2 | 279 | 276 | 276 | 0.989 | 0.989 |
| CD 16 G4 [S spacing: 8 x (read; G nops); base = the read as a nop] | 279 | 276 | 276 | 0.989 | 0.989 |
| CD 16 G8 | 279 | 276 | 276 | 0.989 | 0.989 |
| CD 16 G16 | 288 | 287 | 288 | 0.997 | 1.000 |
| CD 16 G32 | 286 | 285 | 285 | 0.997 | 0.997 |
| CD 16 G64 [S spacing: 8 x (read; G nops); base = the read as a nop] | 542 | 541 | 541 | 0.998 | 0.998 |
| CD 32 G0 [S spacing: 8 x (read; G nops); base = the read as a nop] | 549 | 548 | 548 | 0.998 | 0.998 |
| CD 32 G2 | 551 | 548 | 548 | 0.995 | 0.995 |
| CD 32 G4 | 551 | 548 | 548 | 0.995 | 0.995 |
| CD 32 G8 | 552 | 548 | 551 | 0.993 | 0.998 |
| CD 32 G16 | 551 | 548 | 548 | 0.995 | 0.995 |
| CD 32 G32 | 551 | 548 | 548 | 0.995 | 0.995 |
| CD 32 G64 [S spacing: 8 x (read; G nops); base = the read as a nop] | 571 | 574 | 573 | 1.005 | 1.004 |
| EXP2 8 G0 [S spacing: 8 x (read; G nops); base = the read as a nop] | 186 | 192 | 185 | 1.032 | 0.995 |
| EXP2 8 G2 | 188 | 192 | 185 | 1.021 | 0.984 |
| EXP2 8 G4 | 188 | 192 | 185 | 1.021 | 0.984 |
| EXP2 8 G8 | 188 | 192 | 185 | 1.021 | 0.984 |
| EXP2 8 G16 | 188 | 192 | 185 | 1.021 | 0.984 |
| EXP2 8 G32 | 286 | 285 | 285 | 0.997 | 0.997 |
| EXP2 8 G64 [S spacing: 8 x (read; G nops); base = the read as a nop] | 552 | 551 | 551 | 0.998 | 0.998 |
| EXP2 16 G0 | 378 | 384 | 377 | 1.016 | 0.997 |
| EXP2 16 G2 | 380 | 384 | 377 | 1.011 | 0.992 |
| EXP2 16 G4 | 380 | 384 | 377 | 1.011 | 0.992 |
| EXP2 16 G8 | 380 | 384 | 377 | 1.011 | 0.992 |
| EXP2 16 G16 | 380 | 384 | 377 | 1.011 | 0.992 |
| EXP2 16 G32 | 380 | 384 | 377 | 1.011 | 0.992 |
| EXP2 16 G64 | 552 | 553 | 553 | 1.002 | 1.002 |
| EXP2 32 G0 | 762 | 768 | 761 | 1.008 | 0.999 |
| EXP2 32 G2 | 764 | 768 | 761 | 1.005 | 0.996 |
| EXP2 32 G4 | 764 | 768 | 761 | 1.005 | 0.996 |
| EXP2 32 G8 | 764 | 768 | 761 | 1.005 | 0.996 |
| EXP2 32 G16 | 764 | 768 | 761 | 1.005 | 0.996 |
| EXP2 32 G32 | 764 | 768 | 761 | 1.005 | 0.996 |
| EXP2 32 G64 | 798 | 806 | 799 | 1.010 | 1.001 |
| BIOS 8 G0 | 77 | 77 | 93 | 1.000 | 1.208 |
| BIOS 8 G4 | 93 | 77 | 93 | 0.828 | 1.000 |
| BIOS 8 G16 | 158 | 157 | 157 | 0.994 | 0.994 |
| BIOS 8 G64 | 552 | 551 | 552 | 0.998 | 1.000 |
| BIOS 16 G0 | 141 | 141 | 157 | 1.000 | 1.113 |
| BIOS 16 G4 | 157 | 141 | 157 | 0.898 | 1.000 |
| BIOS 16 G16 | 158 | 157 | 157 | 0.994 | 0.994 |
| BIOS 16 G64 | 552 | 551 | 553 | 0.998 | 1.002 |
| BIOS 32 G0 [S spacing: 8 x (read; G nops); base = the read as a nop] | 269 | 269 | 285 | 1.000 | 1.059 |
| BIOS 32 G4 | 285 | 269 | 285 | 0.944 | 1.000 |
| BIOS 32 G16 | 285 | 269 | 285 | 0.944 | 1.000 |
| BIOS 32 G64 | 542 | 541 | 541 | 0.998 | 0.998 |
| SPU 8 G0 | 221 | 220 | 220 | 0.995 | 0.995 |
| SPU 8 G4 | 223 | 220 | 220 | 0.987 | 0.987 |
| SPU 8 G16 | 231 | 233 | 231 | 1.009 | 1.000 |
| SPU 8 G64 | 542 | 541 | 541 | 0.998 | 0.998 |
| SPU 16 G0 [S spacing: 8 x (read; G nops); base = the read as a nop] | 221 | 220 | 220 | 0.995 | 0.995 |
| SPU 16 G4 | 223 | 220 | 220 | 0.987 | 0.987 |
| SPU 16 G16 | 223 | 220 | 220 | 0.987 | 0.987 |
| SPU 16 G64 | 552 | 555 | 552 | 1.005 | 1.000 |
| SPU 32 G0 | 437 | 436 | 436 | 0.998 | 0.998 |
| SPU 32 G4 | 439 | 436 | 436 | 0.993 | 0.993 |
| SPU 32 G16 | 439 | 436 | 436 | 0.993 | 0.993 |
| SPU 32 G64 | 564 | 572 | 567 | 1.014 | 1.005 |
| base [I isolated: 64 nops, one read, 64 nops] | 133 | 133 | 133 | 1.000 | 1.000 |
| CD 8 isolated | 135 | 135 | 135 | 1.000 | 1.000 |
| CD 16 isolated [I isolated: 64 nops, one read, 64 nops] | 135 | 135 | 135 | 1.000 | 1.000 |
| CD 32 isolated | 135 | 135 | 135 | 1.000 | 1.000 |
| EXP2 8 isolated [I isolated: 64 nops, one read, 64 nops] | 135 | 135 | 135 | 1.000 | 1.000 |
| EXP2 16 isolated | 135 | 135 | 135 | 1.000 | 1.000 |
| EXP2 32 isolated | 164 | 162 | 164 | 0.988 | 1.000 |
| BIOS 8 isolated | 135 | 135 | 135 | 1.000 | 1.000 |
| BIOS 16 isolated | 135 | 135 | 135 | 1.000 | 1.000 |
| BIOS 32 isolated [I isolated: 64 nops, one read, 64 nops] | 135 | 135 | 135 | 1.000 | 1.000 |
| SPU 8 isolated | 145 | 145 | 147 | 1.000 | 1.014 |
| SPU 16 isolated | 135 | 135 | 135 | 1.000 | 1.000 |
| SPU 32 isolated | 135 | 135 | 135 | 1.000 | 1.000 |
| base K0 [M mixed pairs: 4 x (A; K nops; B; 64 nops)] | 268 | 268 | 268 | 1.000 | 1.000 |
| base K2 | 276 | 276 | 276 | 1.000 | 1.000 |
| base K4 | 294 | 294 | 295 | 1.000 | 1.003 |
| base K8 | 300 | 300 | 300 | 1.000 | 1.000 |
| base K16 | 332 | 332 | 332 | 1.000 | 1.000 |
| CD16>RAM K0 [M mixed pairs: 4 x (A; K nops; B; 64 nops)] | 388 | 387 | 387 | 0.997 | 0.997 |
| CD16>RAM K2 | 386 | 377 | 377 | 0.977 | 0.977 |
| CD16>RAM K4 [M mixed pairs: 4 x (A; K nops; B; 64 nops)] | 386 | 377 | 377 | 0.977 | 0.977 |
| CD16>RAM K8 | 386 | 377 | 377 | 0.977 | 0.977 |
| CD16>RAM K16 | 396 | 387 | 388 | 0.977 | 0.980 |
| CD16>ISTAT K0 [M mixed pairs: 4 x (A; K nops; B; 64 nops)] | 378 | 377 | 377 | 0.997 | 0.997 |
| CD16>ISTAT K2 | 386 | 377 | 377 | 0.977 | 0.977 |
| CD16>ISTAT K4 | 396 | 387 | 387 | 0.977 | 0.977 |
| CD16>ISTAT K8 | 386 | 377 | 377 | 0.977 | 0.977 |
| CD16>ISTAT K16 | 386 | 377 | 377 | 0.977 | 0.977 |
| EXP2b>BIOSb K0 [M mixed pairs: 4 x (A; K nops; B; 64 nops)] | 332 | 334 | 335 | 1.006 | 1.009 |
| EXP2b>BIOSb K2 | 330 | 324 | 324 | 0.982 | 0.982 |
| EXP2b>BIOSb K4 | 330 | 324 | 324 | 0.982 | 0.982 |
| EXP2b>BIOSb K8 | 330 | 324 | 324 | 0.982 | 0.982 |
| EXP2b>BIOSb K16 | 362 | 362 | 365 | 1.000 | 1.008 |
| RAM>CD16 K0 [M mixed pairs: 4 x (A; K nops; B; 64 nops)] | 298 | 301 | 301 | 1.010 | 1.010 |
| RAM>CD16 K2 | 306 | 301 | 301 | 0.984 | 0.984 |
| RAM>CD16 K4 | 319 | 313 | 312 | 0.981 | 0.978 |
| RAM>CD16 K8 | 318 | 317 | 317 | 0.997 | 0.997 |
| RAM>CD16 K16 | 350 | 349 | 349 | 0.997 | 0.997 |
| ISTAT>CD16 K0 [M mixed pairs: 4 x (A; K nops; B; 64 nops)] | 290 | 293 | 293 | 1.010 | 1.010 |
| ISTAT>CD16 K2 | 308 | 310 | 305 | 1.006 | 0.990 |
| ISTAT>CD16 K4 | 302 | 301 | 301 | 0.997 | 0.997 |
| ISTAT>CD16 K8 | 318 | 317 | 317 | 0.997 | 0.997 |
| ISTAT>CD16 K16 | 360 | 359 | 360 | 0.997 | 1.000 |
| LOADMIX seq base [M mixed pairs: 4 x (A; K nops; B; 64 nops)] | 328 | 312 | 312 | 0.951 | 0.951 |
| LOADMIX seq [M mixed pairs: 4 x (A; K nops; B; 64 nops)] | 422 | 409 | 409 | 0.969 | 0.969 |
| C0 base G0 [Z spacing with COM_DELAY = 0] | 12 | 12 | 12 | 1.000 | 1.000 |
| C0 base G4 | 44 | 44 | 44 | 1.000 | 1.000 |
| C0 base G16 | 150 | 150 | 151 | 1.000 | 1.007 |
| C0 base G64 | 524 | 524 | 524 | 1.000 | 1.000 |
| C0 CD 8 G0 [Z spacing with COM_DELAY = 0] | 85 | 84 | 98 | 0.988 | 1.153 |
| C0 CD 8 G4 | 101 | 84 | 98 | 0.832 | 0.970 |
| C0 CD 8 G16 | 158 | 157 | 157 | 0.994 | 0.994 |
| C0 CD 8 G64 [Z spacing with COM_DELAY = 0] | 552 | 551 | 551 | 0.998 | 0.998 |
| C0 CD 16 G0 [Z spacing with COM_DELAY = 0] | 133 | 132 | 146 | 0.992 | 1.098 |
| C0 CD 16 G4 | 149 | 132 | 146 | 0.886 | 0.980 |
| C0 CD 16 G16 | 158 | 157 | 157 | 0.994 | 0.994 |
| C0 CD 16 G64 | 552 | 551 | 552 | 0.998 | 1.000 |
| C0 CD 32 G0 | 229 | 228 | 242 | 0.996 | 1.057 |
| C0 CD 32 G4 | 245 | 228 | 242 | 0.931 | 0.988 |
| C0 CD 32 G16 | 245 | 228 | 242 | 0.931 | 0.988 |
| C0 CD 32 G64 | 542 | 541 | 541 | 0.998 | 0.998 |
| C0 EXP2 8 G0 [Z spacing with COM_DELAY = 0] | 109 | 108 | 122 | 0.991 | 1.119 |
| C0 EXP2 8 G4 | 125 | 108 | 122 | 0.864 | 0.976 |
| C0 EXP2 8 G16 | 158 | 157 | 157 | 0.994 | 0.994 |
| C0 EXP2 8 G64 | 552 | 551 | 551 | 0.998 | 0.998 |
| C0 EXP2 16 G0 | 181 | 188 | 202 | 1.039 | 1.116 |
| C0 EXP2 16 G4 | 197 | 188 | 202 | 0.954 | 1.025 |
| C0 EXP2 16 G16 | 197 | 188 | 202 | 0.954 | 1.025 |
| C0 EXP2 16 G64 | 552 | 554 | 555 | 1.004 | 1.005 |
| C0 EXP2 32 G0 | 325 | 348 | 362 | 1.071 | 1.114 |
| C0 EXP2 32 G4 | 341 | 348 | 362 | 1.021 | 1.062 |
| C0 EXP2 32 G16 | 341 | 349 | 362 | 1.023 | 1.062 |
| C0 EXP2 32 G64 | 542 | 541 | 541 | 0.998 | 0.998 |

### CPU/mem: store-burst stride sweep

| Measurement | Console | 0.6.0 | 0.6.1 | 0.6.0 / console | 0.6.1 / console |
|---|---:|---:|---:|---:|---:|
| 2SW 64K | 9 | 9 | 9 | 1.000 | 1.000 |
| 2SW 64K, 4 nop | 15 | 11 | 15 | 0.733 | 1.000 |
| 2SW 64K, lw RAM | 20 | 21 | 22 | 1.050 | 1.100 |
| base (2 timer reads) [STRIDE rows (sysclock, one pass)] | 6 | - | 5 | - | 0.833 |
| base, lw RAM [STRIDE rows (sysclock, one pass)] | 12 | 12 | 12 | 1.000 | 1.000 |
| base, 4 nop | 8 | 8 | 8 | 1.000 | 1.000 |
| 2SW same [STRIDE rows (sysclock, one pass)] | 11 | 10 | 10 | 0.909 | 0.909 |
| 3SW same | 13 | 12 | 12 | 0.923 | 0.923 |
| 4SW same | 15 | 14 | 14 | 0.933 | 0.933 |
| 2SW 4B [STRIDE rows (sysclock, one pass)] | 11 | 10 | 10 | 0.909 | 0.909 |
| 3SW 4B | 13 | 12 | 12 | 0.923 | 0.923 |
| 4SW 4B | 15 | 14 | 14 | 0.933 | 0.933 |
| 2SW 8B | 11 | 10 | 10 | 0.909 | 0.909 |
| 3SW 8B | 13 | 12 | 12 | 0.923 | 0.923 |
| 4SW 8B | 15 | 14 | 14 | 0.933 | 0.933 |
| 2SW 16B | 11 | 10 | 10 | 0.909 | 0.909 |
| 3SW 16B | 13 | 12 | 12 | 0.923 | 0.923 |
| 4SW 16B | 15 | 14 | 14 | 0.933 | 0.933 |
| 2SW 32B | 11 | 10 | 10 | 0.909 | 0.909 |
| 3SW 32B | 13 | 12 | 12 | 0.923 | 0.923 |
| 4SW 32B | 15 | 14 | 14 | 0.933 | 0.933 |
| 2SW 64B | 11 | 10 | 10 | 0.909 | 0.909 |
| 3SW 64B | 13 | 12 | 12 | 0.923 | 0.923 |
| 4SW 64B | 15 | 14 | 14 | 0.933 | 0.933 |
| 2SW 128B | 11 | 10 | 10 | 0.909 | 0.909 |
| 3SW 128B | 13 | 12 | 12 | 0.923 | 0.923 |
| 4SW 128B | 15 | 14 | 14 | 0.933 | 0.933 |
| 2SW 256B | 11 | 10 | 10 | 0.909 | 0.909 |
| 3SW 256B | 13 | 12 | 12 | 0.923 | 0.923 |
| 4SW 256B | 15 | 14 | 14 | 0.933 | 0.933 |
| 2SW 512B | 11 | 10 | 10 | 0.909 | 0.909 |
| 3SW 512B | 11 | 11 | 11 | 1.000 | 1.000 |
| 4SW 512B | 11 | 13 | 11 | 1.182 | 1.000 |
| 2SW 1K [STRIDE rows (sysclock, one pass)] | 9 | 9 | 9 | 1.000 | 1.000 |
| 3SW 1K [STRIDE rows (sysclock, one pass)] | 9 | 11 | 9 | 1.222 | 1.000 |
| 4SW 1K [STRIDE rows (sysclock, one pass)] | 15 | 15 | 15 | 1.000 | 1.000 |
| 2SW 2K [STRIDE rows (sysclock, one pass)] | 9 | 9 | 9 | 1.000 | 1.000 |
| 3SW 2K | 9 | 11 | 9 | 1.222 | 1.000 |
| 4SW 2K | 15 | 15 | 15 | 1.000 | 1.000 |
| 2SW 4K | 9 | 9 | 9 | 1.000 | 1.000 |
| 3SW 4K | 9 | 11 | 9 | 1.222 | 1.000 |
| 4SW 4K | 15 | 15 | 15 | 1.000 | 1.000 |
| 2SW 8K | 9 | 9 | 9 | 1.000 | 1.000 |
| 3SW 8K | 9 | 11 | 9 | 1.222 | 1.000 |
| 4SW 8K | 15 | 15 | 15 | 1.000 | 1.000 |
| 2SW 16K | 9 | 9 | 9 | 1.000 | 1.000 |
| 3SW 16K | 9 | 11 | 9 | 1.222 | 1.000 |
| 4SW 16K | 15 | 15 | 15 | 1.000 | 1.000 |
| 2SW 32K | 9 | 9 | 9 | 1.000 | 1.000 |
| 3SW 32K | 9 | 11 | 9 | 1.222 | 1.000 |
| 4SW 32K | 15 | 15 | 15 | 1.000 | 1.000 |
| 2SW 64K [STRIDE rows (sysclock, one pass)] | 9 | 9 | 9 | 1.000 | 1.000 |
| 3SW 64K | 9 | 11 | 9 | 1.222 | 1.000 |
| 4SW 64K | 15 | 15 | 15 | 1.000 | 1.000 |
| 2SW 128K | 9 | 9 | 9 | 1.000 | 1.000 |
| 3SW 128K | 9 | 11 | 9 | 1.222 | 1.000 |
| 4SW 128K | 15 | 15 | 15 | 1.000 | 1.000 |
| 2SW 256K | 9 | 9 | 9 | 1.000 | 1.000 |
| 3SW 256K | 9 | 11 | 9 | 1.222 | 1.000 |
| 4SW 256K | 15 | 15 | 15 | 1.000 | 1.000 |
| 2SW 512K | 9 | 9 | 9 | 1.000 | 1.000 |
| 2SW same, 4 nop | 12 | 11 | 11 | 0.917 | 0.917 |
| 2SW same, lw RAM [STRIDE rows (sysclock, one pass)] | 20 | 17 | 18 | 0.850 | 0.900 |
| 2SW 4B, 4 nop | 12 | 11 | 11 | 0.917 | 0.917 |
| 2SW 4B, lw RAM | 20 | 17 | 18 | 0.850 | 0.900 |
| 2SW 8B, 4 nop | 12 | 11 | 11 | 0.917 | 0.917 |
| 2SW 8B, lw RAM | 20 | 17 | 18 | 0.850 | 0.900 |
| 2SW 16B, 4 nop | 12 | 11 | 11 | 0.917 | 0.917 |
| 2SW 16B, lw RAM | 20 | 17 | 18 | 0.850 | 0.900 |
| 2SW 32B, 4 nop | 12 | 11 | 11 | 0.917 | 0.917 |
| 2SW 32B, lw RAM | 20 | 17 | 18 | 0.850 | 0.900 |
| 2SW 64B, 4 nop | 12 | 11 | 11 | 0.917 | 0.917 |
| 2SW 64B, lw RAM | 20 | 17 | 18 | 0.850 | 0.900 |
| 2SW 128B, 4 nop | 12 | 11 | 11 | 0.917 | 0.917 |
| 2SW 128B, lw RAM | 20 | 17 | 18 | 0.850 | 0.900 |
| 2SW 256B, 4 nop | 12 | 11 | 11 | 0.917 | 0.917 |
| 2SW 256B, lw RAM | 20 | 17 | 18 | 0.850 | 0.900 |
| 2SW 512B, 4 nop | 12 | 11 | 11 | 0.917 | 0.917 |
| 2SW 512B, lw RAM | 20 | 17 | 18 | 0.850 | 0.900 |
| 2SW 1K, 4 nop [STRIDE rows (sysclock, one pass)] | 15 | 11 | 15 | 0.733 | 1.000 |
| 2SW 1K, lw RAM [STRIDE rows (sysclock, one pass)] | 20 | 21 | 22 | 1.050 | 1.100 |
| 2SW 2K, 4 nop | 15 | 11 | 15 | 0.733 | 1.000 |
| 2SW 2K, lw RAM | 20 | 21 | 22 | 1.050 | 1.100 |
| 2SW 4K, 4 nop | 15 | 11 | 15 | 0.733 | 1.000 |
| 2SW 4K, lw RAM | 20 | 21 | 22 | 1.050 | 1.100 |
| 2SW 8K, 4 nop | 15 | 11 | 15 | 0.733 | 1.000 |
| 2SW 8K, lw RAM | 20 | 21 | 22 | 1.050 | 1.100 |
| 2SW 16K, 4 nop | 15 | 11 | 15 | 0.733 | 1.000 |
| 2SW 16K, lw RAM | 20 | 21 | 22 | 1.050 | 1.100 |
| 2SW 32K, 4 nop | 15 | 11 | 15 | 0.733 | 1.000 |
| 2SW 32K, lw RAM | 20 | 21 | 22 | 1.050 | 1.100 |
| 2SW 64K, 4 nop [STRIDE rows (sysclock, one pass)] | 15 | 11 | 15 | 0.733 | 1.000 |
| 2SW 64K, lw RAM [STRIDE rows (sysclock, one pass)] | 20 | 21 | 22 | 1.050 | 1.100 |
| 2SW 128K, 4 nop | 15 | 11 | 15 | 0.733 | 1.000 |
| 2SW 128K, lw RAM | 20 | 21 | 22 | 1.050 | 1.100 |
| 2SW 256K, 4 nop | 15 | 11 | 15 | 0.733 | 1.000 |
| 2SW 256K, lw RAM | 20 | 21 | 22 | 1.050 | 1.100 |
| 2SW 512K, 4 nop | 15 | 11 | 15 | 0.733 | 1.000 |
| 2SW 512K, lw RAM | 20 | 21 | 22 | 1.050 | 1.100 |
| IF8K: 2048 nops (8 KB, every line a refill) [L latency split: refills and data reads, apart and interleaved (one pass)] | 4683 | - | 4674 | - | 0.998 |
| LWHIT: 128 x (lw same; 3 nop) [L latency split: refills and data reads, apart and interleaved (one pass)] | 1183 | - | 1173 | - | 0.992 |
| LWMISS: 128 x (lw +1028 B; 3 nop) [L latency split: refills and data reads, apart and interleaved (one pass)] | 1173 | - | 1163 | - | 0.991 |
| BIG: 512 x (lw +68 B; 3 ALU), 8 KB [L latency split: refills and data reads, apart and interleaved (one pass)] | 8330 | 7199 | 8117 | 0.864 | 0.974 |

### ACCDLY  Disc-boot bus access (8/16/32-bit)

| Measurement | Console | 0.6.0 | 0.6.1 | 0.6.0 / console | 0.6.1 / console |
|---|---:|---:|---:|---:|---:|
| RAM 8-bit | 5.10 | - | 4.95 | - | 0.971 |
| RAM 16-bit | 5.12 | - | 5.03 | - | 0.982 |
| RAM 32-bit | 5.08 | - | 4.95 | - | 0.974 |
| BIOS 8-bit | 7.02 | - | 7.04 | - | 1.003 |
| BIOS 16-bit | 15.02 | - | 15.08 | - | 1.004 |
| BIOS 32-bit | 31.02 | - | 31.07 | - | 1.002 |
| SCRATCHPAD 8-bit | 1.02 | - | 0.94 | - | 0.922 |
| SCRATCHPAD 16-bit | 1.02 | - | 1.01 | - | 0.990 |
| SCRATCHPAD 32-bit | 1.02 | - | 1.01 | - | 0.990 |
| EXPANSION1 8-bit | 7.02 | - | 6.99 | - | 0.996 |
| EXPANSION1 16-bit | 15.02 | - | 14.99 | - | 0.998 |
| EXPANSION1 32-bit | 31.02 | - | 30.99 | - | 0.999 |
| EXPANSION2 8-bit | 19.93 | - | 19.90 | - | 0.998 |
| EXPANSION2 16-bit | 43.93 | - | 43.90 | - | 0.999 |
| EXPANSION2 32-bit | 91.93 | - | 91.90 | - | 1.000 |
| EXPANSION3 8-bit | 6.02 | - | 5.99 | - | 0.995 |
| EXPANSION3 16-bit | 6.02 | - | 5.99 | - | 0.995 |
| EXPANSION3 32-bit | 10.02 | - | 9.99 | - | 0.997 |
| DMAC_CTRL 8-bit | 3.02 | - | 3.12 | - | 1.033 |
| DMAC_CTRL 16-bit | 3.02 | - | 3.12 | - | 1.033 |
| DMAC_CTRL 32-bit | 3.02 | - | 3.11 | - | 1.030 |
| JOY_STAT 8-bit | 3.02 | - | 3.12 | - | 1.033 |
| JOY_STAT 16-bit | 3.02 | - | 3.11 | - | 1.030 |
| JOY_STAT 32-bit | 3.02 | - | 3.14 | - | 1.040 |
| SIO_STAT 8-bit | 3.02 | - | 3.05 | - | 1.010 |
| SIO_STAT 16-bit | 3.02 | - | 3.12 | - | 1.033 |
| SIO_STAT 32-bit | 3.02 | - | 3.10 | - | 1.026 |
| RAM_SIZE 8-bit | 3.16 | - | 3.12 | - | 0.987 |
| RAM_SIZE 16-bit | 3.07 | - | 3.08 | - | 1.003 |
| RAM_SIZE 32-bit | 3.15 | - | 3.14 | - | 0.997 |
| I_STAT 8-bit | 3.02 | - | 3.12 | - | 1.033 |
| I_STAT 16-bit | 3.02 | - | 3.12 | - | 1.033 |
| I_STAT 32-bit | 3.02 | - | 3.05 | - | 1.010 |
| TIMER0_VAL 8-bit | 3.02 | - | 3.12 | - | 1.033 |
| TIMER0_VAL 16-bit | 3.02 | - | 3.12 | - | 1.033 |
| TIMER0_VAL 32-bit | 3.02 | - | 3.11 | - | 1.030 |
| CDROM_STAT 8-bit | 13.96 | - | 13.93 | - | 0.998 |
| CDROM_STAT 16-bit | 30.96 | - | 30.93 | - | 0.999 |
| CDROM_STAT 32-bit | 64.96 | - | 64.93 | - | 1.000 |
| GPUSTAT 8-bit | 3.02 | - | 3.12 | - | 1.033 |
| GPUSTAT 16-bit | 3.02 | - | 3.11 | - | 1.030 |
| GPUSTAT 32-bit | 3.02 | - | 3.12 | - | 1.033 |
| MDECSTAT 8-bit | 3.02 | - | 3.12 | - | 1.033 |
| MDECSTAT 16-bit | 3.02 | - | 3.12 | - | 1.033 |
| MDECSTAT 32-bit | 3.02 | - | 3.11 | - | 1.030 |
| CACHECTRL 8-bit | 1.02 | - | 1.01 | - | 0.990 |
| CACHECTRL 16-bit | 1.02 | - | 1.01 | - | 0.990 |
| CACHECTRL 32-bit | 1.02 | - | 1.01 | - | 0.990 |
| SPUCNT 8-bit | 23.96 | - | 23.93 | - | 0.999 |
| SPUCNT 16-bit | 23.96 | - | 23.93 | - | 0.999 |
| SPUCNT 32-bit | 51.96 | - | 51.93 | - | 0.999 |

### CPUGTE  Cached CPU and GTE timings

| Measurement | Console | 0.6.0 | 0.6.1 | 0.6.0 / console | 0.6.1 / console |
|---|---:|---:|---:|---:|---:|
| GPUSTAT lw (cyc/iter) | 6.18 | - | 5.31 | - | 0.859 |
| GTE RTPS (op + mfc2, cycles) | 546 | - | 559 | - | 1.024 |
| RAM lw same row (cyc/iter) | 8.37 | - | 9.06 | - | 1.082 |
| GTE RTPT (op + mfc2, cycles) | 802 | - | 803 | - | 1.001 |
| RAM lw 2 rows 2KB (cyc/iter) | 8.25 | - | 9.09 | - | 1.102 |
| GTE NCLIP (op + mfc2, cycles) | 322 | - | 323 | - | 1.003 |
| RAM lw stride 4KB (cyc/iter) | 9.56 | - | 9.34 | - | 0.977 |
| GTE AVSZ3 (op + mfc2, cycles) | 226 | - | 227 | - | 1.004 |
| GTE AVSZ4 (op + mfc2, cycles) | 258 | - | 259 | - | 1.004 |
| MULT small + mflo (cyc/iter) | 11.06 | - | 11.09 | - | 1.003 |
| GTE NCDS (op + mfc2, cycles) | 674 | - | 675 | - | 1.001 |
| DIV + mflo (cyc/iter) | 39.06 | - | 39.09 | - | 1.001 |
| GTE NCDT (op + mfc2, cycles) | 1474 | - | 1475 | - | 1.001 |
| GTE NCCS (op + mfc2, cycles) | 610 | - | 611 | - | 1.002 |
| GTE NCCT (op + mfc2, cycles) | 1314 | - | 1315 | - | 1.001 |
| GTE NCS (op + mfc2, cycles) | 514 | - | 515 | - | 1.002 |
| GTE NCT (op + mfc2, cycles) | 1026 | - | 1027 | - | 1.001 |
| GTE MVMVA (op + mfc2, cycles) | 322 | - | 336 | - | 1.043 |
| GTE DPCS (op + mfc2, cycles) | 322 | - | 323 | - | 1.003 |
| GTE DPCT (op + mfc2, cycles) | 610 | - | 611 | - | 1.002 |
| GTE SQR (op + mfc2, cycles) | 226 | - | 227 | - | 1.004 |
| GTE OP (op + mfc2, cycles) | 258 | - | 256 | - | 0.992 |
| GTE GPF (op + mfc2, cycles) | 226 | - | 227 | - | 1.004 |
| GTE GPL (op + mfc2, cycles) | 226 | - | 227 | - | 1.004 |
| GTE CC (op + mfc2, cycles) | 418 | - | 419 | - | 1.002 |
| GTE CDP (op + mfc2, cycles) | 482 | - | 483 | - | 1.002 |
| GTE INTPL (op + mfc2, cycles) | 322 | - | 323 | - | 1.003 |
| GTE DCPL (op + mfc2, cycles) | 322 | - | 323 | - | 1.003 |
| RAM sw x32 seq (cyc/iter) | 2.21 | - | 2.78 | - | 1.258 |
| RAM sw same addr (cyc/iter) | 2.22 | - | 2.31 | - | 1.041 |
| RAM lw, use next (cyc/iter) | 8.25 | - | 9.06 | - | 1.098 |
| RAM lw + nop (cyc/iter) | 8.25 | - | 7.25 | - | 0.879 |
| SPAD sw (cyc/iter) | 2.18 | - | 2.15 | - | 0.986 |
| SPAD lw, use next (cyc/iter) | 2.18 | - | 2.21 | - | 1.014 |
| SPU sh 1F801DA4 (cyc/iter) | 13.03 | - | 13.46 | - | 1.033 |

### WBUF  Store buffer and load spacing

| Measurement | Console | 0.6.0 | 0.6.1 | 0.6.0 / console | 0.6.1 / console |
|---|---:|---:|---:|---:|---:|
| nop only x1 | 1.09 | 1.09 | 1.09 | 1.000 | 1.000 |
| nop only x2 | 2.09 | 2.09 | 2.10 | 1.000 | 1.005 |
| nop only x4 | 4.09 | 4.09 | 4.09 | 1.000 | 1.000 |
| nop only x8 | 8.09 | 8.10 | 8.09 | 1.001 | 1.000 |
| sw same +0nop | 2.09 | 2.02 | 2.10 | 0.967 | 1.005 |
| sw same +1nop | 2.19 | 2.14 | 2.19 | 0.977 | 1.000 |
| sw same +2nop | 3.11 | 3.11 | 3.11 | 1.000 | 1.000 |
| sw same +4nop | 5.09 | 5.09 | 5.09 | 1.000 | 1.000 |
| sw same +8nop | 9.09 | 9.09 | 9.11 | 1.000 | 1.002 |
| sw seq 4B +0nop | 2.09 | 2.02 | 2.15 | 0.967 | 1.029 |
| sw seq 4B +1nop | 2.17 | 2.14 | 2.21 | 0.986 | 1.018 |
| sw seq 4B +2nop | 3.11 | 3.12 | 3.12 | 1.003 | 1.003 |
| sw seq 4B +4nop | 5.10 | 5.09 | 5.09 | 0.998 | 0.998 |
| sw page 1K +0nop | 6.20 | 6.22 | 6.22 | 1.003 | 1.003 |
| sw page 1K +1nop | 6.22 | 6.22 | 6.23 | 1.000 | 1.002 |
| sw page 1K +2nop | 6.21 | 6.21 | 6.23 | 1.000 | 1.003 |
| sw page 1K +4nop | 6.21 | 6.22 | 6.23 | 1.002 | 1.003 |
| sw page 1K +8nop | 9.09 | 9.09 | 9.11 | 1.000 | 1.002 |
| lw same +1nop | 8.13 | 8.03 | 8.93 | 0.988 | 1.098 |
| lw same +2nop | 9.14 | 9.01 | 9.03 | 0.986 | 0.988 |
| lw page 1K +1nop | 8.13 | 8.04 | 9.01 | 0.989 | 1.108 |
| lw page 1K +2nop | 9.15 | 9 | 9.01 | 0.984 | 0.985 |
| sw,lw same | 14.23 | 14.23 | 14.27 | 1.000 | 1.003 |
| sw,lw page 1K | 14.22 | 14.23 | 14.43 | 1.001 | 1.015 |

### WBUF2  Store bursts and write queue

| Measurement | Console | 0.6.0 | 0.6.1 | 0.6.0 / console | 0.6.1 / console |
|---|---:|---:|---:|---:|---:|
| 1SW same addr | 3 | 3 | 3 | 1.000 | 1.000 |
| 2SW same addr | 5 | 5 | 5 | 1.000 | 1.000 |
| 3SW same addr | 7 | 7 | 7 | 1.000 | 1.000 |
| 4SW same addr | 9 | 9 | 9 | 1.000 | 1.000 |
| 5SW same addr | 11 | 11 | 11 | 1.000 | 1.000 |
| 6SW same addr | 13 | 13 | 13 | 1.000 | 1.000 |
| 7SW same addr | 15 | 15 | 15 | 1.000 | 1.000 |
| 8SW same addr | 17 | 17 | 17 | 1.000 | 1.000 |
| 9SW same addr | 19 | 19 | 19 | 1.000 | 1.000 |
| 10SW same addr | 21 | 21 | 21 | 1.000 | 1.000 |
| 8SW same, 1 nop gap | 17 | 17 | 17 | 1.000 | 1.000 |
| 8SW same, 2 nop gap | 25 | 24 | 24 | 0.960 | 0.960 |
| 8SW same, 3 nop gap | 32 | 32 | 32 | 1.000 | 1.000 |
| 8SW same, 4 nop gap | 41 | 40 | 40 | 0.976 | 0.976 |
| 4SW +4 seq (1 page) | 9 | - | 9 | - | 1.000 |
| 6SW +4 seq (1 page) | 13 | - | 13 | - | 1.000 |
| 8SW +4 seq (1 page) | 17 | - | 17 | - | 1.000 |
| 9SW +4 seq (1 page) | 19 | - | 19 | - | 1.000 |
| 2SW 1KB stride | 3 | 4 | 4 | 1.333 | 1.333 |
| 3SW 1KB stride | 3 | 6 | 4 | 2.000 | 1.333 |
| 4SW 1KB stride | 5 | 10 | 10 | 2.000 | 2.000 |
| 6SW 1KB stride | 15 | 12 | 16 | 0.800 | 1.067 |
| 0SW then lw+use | 8 | 7 | 7 | 0.875 | 0.875 |
| 1SW then lw+use | 14 | 14 | 14 | 1.000 | 1.000 |
| 2SW then lw+use | 16 | 16 | 16 | 1.000 | 1.000 |
| 4SW then lw+use | 20 | 20 | 20 | 1.000 | 1.000 |
| 6SW then lw+use | 24 | 24 | 24 | 1.000 | 1.000 |
| 0SW then KSEG1 lw+use | 8 | 7 | 7 | 0.875 | 0.875 |
| 1SW then KSEG1 lw+use | 14 | 14 | 14 | 1.000 | 1.000 |
| 2SW then KSEG1 lw+use | 16 | 16 | 16 | 1.000 | 1.000 |
| 4SW then KSEG1 lw+use | 20 | 20 | 20 | 1.000 | 1.000 |
| 6SW then KSEG1 lw+use | 24 | 24 | 24 | 1.000 | 1.000 |
| 1SW KSEG1 same addr | 3 | 3 | 3 | 1.000 | 1.000 |
| 4SW KSEG1 same addr | 9 | 9 | 9 | 1.000 | 1.000 |
| 6SW KSEG1 same addr | 13 | 13 | 13 | 1.000 | 1.000 |
| 8SW KSEG1 same addr | 17 | 17 | 17 | 1.000 | 1.000 |
| 1SW, 4 nop, lw+use | 14 | 12 | 12 | 0.857 | 0.857 |
| 1x chain-looping body | 13 | 13 | 13 | 1.000 | 1.000 |
| 4x chain-looping body | 61 | 61 | 61 | 1.000 | 1.000 |
| 1x body, no sw | 11 | 11 | 11 | 1.000 | 1.000 |
| 4x body, no sw | 44 | 44 | 44 | 1.000 | 1.000 |

### WBUF3  Store bursts and page changes

| Measurement | Console | 0.6.0 | 0.6.1 | 0.6.0 / console | 0.6.1 / console |
|---|---:|---:|---:|---:|---:|
| 1SW 1K, lw RAM | 12 | 12 | 12 | 1.000 | 1.000 |
| 2SW 1K, lw RAM | 14 | 16 | 16 | 1.143 | 1.143 |
| 3SW 1K, lw RAM | 14 | 22 | 16 | 1.571 | 1.143 |
| 4SW 1K, lw RAM | 20 | 28 | 22 | 1.400 | 1.100 |
| 6SW 1K, lw RAM | 26 | 40 | 28 | 1.538 | 1.077 |
| 2SW 1K, 4 nop | 9 | 6 | 10 | 0.667 | 1.111 |
| 3SW 1K, 4 nop | 9 | 10 | 10 | 1.111 | 1.111 |
| 4SW 1K, 4 nop | 9 | 10 | 9 | 1.111 | 1.000 |
| 6SW 1K, 4 nop | 21 | 16 | 22 | 0.762 | 1.048 |
| 2SW 1K, 8 nop | 10 | 10 | 11 | 1.000 | 1.100 |
| 4SW 1K, 8 nop | 15 | 12 | 16 | 0.800 | 1.067 |
| 6SW 1K, 8 nop | 21 | 22 | 22 | 1.048 | 1.048 |
| 2SW same, 4 nop | 6 | 6 | 6 | 1.000 | 1.000 |
| 3SW same, 4 nop | 7 | 7 | 7 | 1.000 | 1.000 |
| 4SW same, 4 nop | 9 | 9 | 9 | 1.000 | 1.000 |
| 6SW same, 4 nop | 13 | 13 | 13 | 1.000 | 1.000 |
| 2SW same, 8 nop | 10 | 10 | 10 | 1.000 | 1.000 |
| 4SW same, 8 nop | 12 | 12 | 12 | 1.000 | 1.000 |
| 6SW same, 8 nop | 16 | 16 | 16 | 1.000 | 1.000 |
| 1SW same, lw RAM | 12 | 12 | 12 | 1.000 | 1.000 |
| 2SW same, lw RAM | 14 | 14 | 14 | 1.000 | 1.000 |
| 4SW same, lw RAM | 18 | 18 | 18 | 1.000 | 1.000 |
| 6SW same, lw RAM | 22 | 22 | 22 | 1.000 | 1.000 |
| 2SW 2K stride | 3 | 4 | 4 | 1.333 | 1.333 |
| 3SW 2K stride | 3 | 6 | 4 | 2.000 | 1.333 |
| 4SW 2K stride | 9 | 10 | 10 | 1.111 | 1.111 |
| 6SW 2K stride | 15 | 12 | 16 | 0.800 | 1.067 |
| 2SW 64K stride | 9 | 5 | 10 | 0.556 | 1.111 |
| 3SW 64K stride | 9 | 10 | 10 | 1.111 | 1.111 |
| 4SW 64K stride | 15 | 11 | 16 | 0.733 | 1.067 |

### CPUMEM2  Uncached CPU and RAM timings

| Measurement | Console | 0.6.0 | 0.6.1 | 0.6.0 / console | 0.6.1 / console |
|---|---:|---:|---:|---:|---:|
| lw a2, no use | 3 | 5 | 5 | 1.667 | 1.667 |
| lw a2, use at d1 | 5 | 5 | 5 | 1.000 | 1.000 |
| lw a2, use at d2 | 5 | 5 | 5 | 1.000 | 1.000 |
| lw a2, use at d3 | 3 | 5 | 5 | 1.667 | 1.667 |
| lw KSEG0 RAM, no use | 3 | 5 | 6 | 1.667 | 2.000 |
| lw KSEG0 RAM, use d2 | 5 | 5 | 6 | 1.000 | 1.200 |
| lw scratchpad, no use | -1 | 0 | 0 | -0.000 | 0.000 |
| lw scratchpad, use d2 | -1 | 0 | 0 | -0.000 | 0.000 |
| lw I_STAT, use d2 | 2 | 3 | 3 | 1.500 | 1.500 |
| lw; lw a2, no use | 8 | 10 | 10 | 1.250 | 1.250 |
| sw a2 | 4 | 5 | 5 | 1.250 | 1.250 |
| sw a2, 0 nop, sw a2 | 6 | 10 | 10 | 1.667 | 1.667 |
| sw a2, 1 nop, sw a2 | 9 | 10 | 10 | 1.111 | 1.111 |
| sw a2, 2 nop, sw a2 | 4 | 5 | 5 | 1.250 | 1.250 |
| sw KSEG0 RAM | 4 | 5 | 6 | 1.250 | 1.500 |
| sw scratchpad | -1 | 0 | 0 | -0.000 | 0.000 |
| sw a2, lw a2 | 8 | 10 | 10 | 1.250 | 1.250 |
| lbu RAM +0 nop | 6.12 | 6.01 | 6.08 | 0.982 | 0.993 |
| lbu RAM +1 nop | 5.12 | 5.01 | 5.01 | 0.979 | 0.979 |
| lbu RAM +2 nop | 4.12 | 3.01 | 4 | 0.731 | 0.971 |
| lbu RAM +3 nop | 3.12 | 3.01 | 3.01 | 0.965 | 0.965 |
| lbu RAM +4 nop | 3.06 | 3.06 | 3.03 | 1.000 | 0.990 |
| lbu RAM +5 nop | 3.05 | 3.01 | 2.99 | 0.987 | 0.980 |
| lbu RAM +6 nop | 3.02 | 3.01 | 3.01 | 0.997 | 0.997 |
| lbu RAM +7 nop | 3.02 | 3.01 | 2.96 | 0.997 | 0.980 |
| lbu RAM +8 nop | 3.02 | 3.01 | 3.05 | 0.997 | 1.010 |
| lw RAM +0 nop | 6.07 | 6.01 | 6.02 | 0.990 | 0.992 |
| lw RAM +2 nop | 4.08 | 3.01 | 4 | 0.738 | 0.980 |
| lw RAM +4 nop | 3.06 | 3.01 | 3.01 | 0.984 | 0.984 |
| lbu scratch +0 nop | 1.01 | 1.02 | 1.09 | 1.010 | 1.079 |
| lbu scratch +2 nop | 1.01 | 1 | 1 | 0.990 | 0.990 |

### CPUMEM3  Store, load and external bus sweep

| Measurement | Console | 0.6.0 | 0.6.1 | 0.6.0 / console | 0.6.1 / console |
|---|---:|---:|---:|---:|---:|
| A store bursts: base | 6 | - | 5 | - | 0.833 |
| 1SW 1K, lw RAM | 18 | - | 17 | - | 0.944 |
| 2SW 1K, lw RAM | 20 | - | 21 | - | 1.050 |
| 3SW 1K, lw RAM | 20 | - | 21 | - | 1.050 |
| 4SW 1K, lw RAM | 26 | - | 27 | - | 1.038 |
| 6SW 1K, lw RAM | 32 | - | 33 | - | 1.031 |
| 2SW 1K, 4 nop | 15 | - | 15 | - | 1.000 |
| 3SW 1K, 4 nop | 15 | - | 15 | - | 1.000 |
| 4SW 1K, 4 nop | 15 | - | 15 | - | 1.000 |
| 6SW 1K, 4 nop | 27 | - | 27 | - | 1.000 |
| 2SW 1K, 8 nop | 16 | - | 16 | - | 1.000 |
| 4SW 1K, 8 nop | 21 | - | 21 | - | 1.000 |
| 6SW 1K, 8 nop | 27 | - | 27 | - | 1.000 |
| 2SW same, 4 nop | 12 | - | 11 | - | 0.917 |
| 3SW same, 4 nop | 13 | - | 12 | - | 0.923 |
| 4SW same, 4 nop | 15 | - | 14 | - | 0.933 |
| 6SW same, 4 nop | 19 | - | 18 | - | 0.947 |
| 2SW same, 8 nop | 16 | - | 15 | - | 0.938 |
| 4SW same, 8 nop | 18 | - | 17 | - | 0.944 |
| 6SW same, 8 nop | 22 | - | 21 | - | 0.955 |
| 1SW same, lw RAM | 18 | - | 17 | - | 0.944 |
| 2SW same, lw RAM | 20 | - | 19 | - | 0.950 |
| 4SW same, lw RAM | 24 | - | 23 | - | 0.958 |
| 6SW same, lw RAM | 28 | - | 27 | - | 0.964 |
| 2SW 2K stride | 9 | - | 9 | - | 1.000 |
| 3SW 2K stride | 9 | - | 9 | - | 1.000 |
| 4SW 2K stride | 15 | - | 15 | - | 1.000 |
| 6SW 2K stride | 21 | - | 21 | - | 1.000 |
| 2SW 64K stride | 15 | - | 15 | - | 1.000 |
| 3SW 64K stride | 15 | - | 15 | - | 1.000 |
| 4SW 64K stride | 21 | - | 21 | - | 1.000 |
| B RAM load spacing: base | 6 | - | 5 | - | 0.833 |
| lw a; 0 nop; lw b | 18 | - | 18 | - | 1.000 |
| lw a; 1 nop; lw b | 20 | - | 18 | - | 0.900 |
| lw a; 2 nop; lw b | 20 | - | 21 | - | 1.050 |
| lw a; 3 nop; lw b | 20 | - | 21 | - | 1.050 |
| lw a; 4 nop; lw b | 20 | - | 21 | - | 1.050 |
| lw a; 5 nop; lw b | 20 | - | 21 | - | 1.050 |
| lw a; 6 nop; lw b | 21 | - | 21 | - | 1.000 |
| lw a; 0 nop; lw a (same reg) | 19 | - | 19 | - | 1.000 |
| lw a; 1 nop; lw a (same reg) | 20 | - | 19 | - | 0.950 |
| lw a; 2 nop; lw a (same reg) | 21 | - | 22 | - | 1.048 |
| lw a; 3 nop; lw a (same reg) | 21 | - | 22 | - | 1.048 |
| lw a; 4 nop; lw a (same reg) | 21 | - | 22 | - | 1.048 |
| lw a; 5 nop; lw a (same reg) | 21 | - | 21 | - | 1.000 |
| lw a; 6 nop; lw a (same reg) | 21 | - | 21 | - | 1.000 |
| lw; 0 nop; lw other row | 18 | - | 18 | - | 1.000 |
| lw; 3 nop; lw other row | 20 | - | 21 | - | 1.050 |
| lw; 6 nop; lw other row | 21 | - | 21 | - | 1.000 |
| lw; 0 nop; lw; use | 20 | - | 18 | - | 0.900 |
| lw; 2 nop; lw; use | 22 | - | 21 | - | 0.955 |
| lw; 4 nop; lw; use | 22 | - | 21 | - | 0.955 |
| 3x (lw; 0 nop) | 26 | - | 26 | - | 1.000 |
| 3x (lw; 1 nop) | 30 | - | 26 | - | 0.867 |
| 3x (lw; 2 nop) | 32 | - | 30 | - | 0.938 |
| 3x (lw; 3 nop) | 32 | - | 31 | - | 0.969 |
| C load, then use base | 6 | - | 5 | - | 0.833 |
| RAM lw, no use | 14 | - | 12 | - | 0.857 |
| RAM lw, use d1 | 14 | - | 12 | - | 0.857 |
| RAM lw, use d2 | 17 | - | 16 | - | 0.941 |
| RAM lw, use d3 | 17 | - | 16 | - | 0.941 |
| RAM lw, use d4 | 16 | - | 15 | - | 0.938 |
| I_STAT lw, no use | 13 | - | 12 | - | 0.923 |
| I_STAT lw, use d1 | 13 | - | 12 | - | 0.923 |
| I_STAT lw, use d2 | 15 | - | 14 | - | 0.933 |
| I_STAT lw, use d3 | 15 | - | 14 | - | 0.933 |
| I_STAT lw, use d4 | 14 | - | 13 | - | 0.929 |
| CD stat lbu, no use | 17 | - | 14 | - | 0.824 |
| CD stat lbu, use d1 | 17 | - | 14 | - | 0.824 |
| CD stat lbu, use d2 | 20 | - | 18 | - | 0.900 |
| CD stat lbu, use d3 | 20 | - | 18 | - | 0.900 |
| CD stat lbu, use d4 | 19 | - | 17 | - | 0.895 |
| GPUSTAT lw, no use | 13 | - | 12 | - | 0.923 |
| GPUSTAT lw, use d1 | 13 | - | 12 | - | 0.923 |
| GPUSTAT lw, use d2 | 15 | - | 14 | - | 0.933 |
| GPUSTAT lw, use d3 | 15 | - | 14 | - | 0.933 |
| GPUSTAT lw, use d4 | 14 | - | 13 | - | 0.929 |
| SPAD lw, no use | 11 | - | 10 | - | 0.909 |
| SPAD lw, use d1 | 11 | - | 10 | - | 0.909 |
| SPAD lw, use d2 | 11 | - | 10 | - | 0.909 |
| SPAD lw, use d3 | 11 | - | 10 | - | 0.909 |
| SPAD lw, use d4 | 11 | - | 10 | - | 0.909 |
| F CPUGTE CPU rows base | 6 | - | 5 | - | 0.833 |
| RAM sw x32 seq | 71 | - | 70 | - | 0.986 |
| RAM sw same x32 | 71 | - | 70 | - | 0.986 |
| RAM lw+nop x32 | 264 | - | 272 | - | 1.030 |
| RAM lw, use next x32 | 264 | - | 291 | - | 1.102 |
| RAM lw stride 4KB x32 | 264 | - | 279 | - | 1.057 |
| SPAD sw x32 | 70 | - | 69 | - | 0.986 |
| SPAD lw, use next x32 | 70 | - | 69 | - | 0.986 |
| SPU sh 1F801DA4 x32 | 427 | - | 431 | - | 1.009 |
| GPUSTAT lw x32 | 198 | - | 177 | - | 0.894 |
| 32 sw then lw+use | 82 | - | 82 | - | 1.000 |
| NTSC scanlines per frame | 263 | - | 263 | - | 1.000 |
| NTSC sysclk per scanline x100 | 217248 | - | 217279 (215296) | - | 1.000 |
| PAL scanlines per frame | 314 | - | 314 | - | 1.000 |
| PAL sysclk per scanline x100 | 216760 | - | 218797 (216800) | - | 1.009 |
| 0132C BIOS r1 8-bit | 502 | - | 507 | - | 1.010 |
| 0132C BIOS r1 16-bit | 802 | - | 806 | - | 1.005 |
| 0132C BIOS r1 32-bit | 1402 | - | 1408 | - | 1.004 |
| 0132C BIOS r3 8-bit | 702 | - | 703 | - | 1.001 |
| 0132C BIOS r3 16-bit | 1202 | - | 1206 | - | 1.003 |
| 0132C BIOS r3 32-bit | 2202 | - | 2205 | - | 1.001 |
| 0132C BIOS r7 8-bit | 1102 | - | 1108 | - | 1.005 |
| 0132C BIOS r7 16-bit | 2002 | - | 2007 | - | 1.002 |
| 0132C BIOS r7 32-bit | 3802 | - | 3807 | - | 1.001 |
| 0132C BIOS r15 8-bit | 1902 | - | 1907 | - | 1.003 |
| 0132C BIOS r15 16-bit | 3602 | - | 3610 | - | 1.002 |
| 0132C BIOS r15 32-bit | 7002 | - | 7008 | - | 1.001 |
| 0132C BIOS r3 rec 8-bit | 1296 | - | 1299 | - | 1.002 |
| 0132C BIOS r3 rec 16-bit | 2896 | - | 2901 | - | 1.002 |
| 0132C BIOS r3 rec 32-bit | 6096 | - | 6101 | - | 1.001 |
| 0132C BIOS r3 hold 8-bit | 702 | - | 703 | - | 1.001 |
| 0132C BIOS r3 hold 16-bit | 1202 | - | 1205 | - | 1.002 |
| 0132C BIOS r3 hold 32-bit | 2202 | - | 2208 | - | 1.003 |
| 0132C BIOS r3 float 8-bit | 702 | - | 707 | - | 1.007 |
| 0132C BIOS r3 float 16-bit | 1502 | - | 1504 | - | 1.001 |
| 0132C BIOS r3 float 32-bit | 3102 | - | 3106 | - | 1.001 |
| 0132C BIOS r3 pre 8-bit | 702 | - | 705 | - | 1.004 |
| 0132C BIOS r3 pre 16-bit | 1202 | - | 1206 | - | 1.003 |
| 0132C BIOS r3 pre 32-bit | 2202 | - | 2211 | - | 1.004 |
| 0132C BIOS r3 rec+pre 8-bit | 1296 | - | 1303 | - | 1.005 |
| 0132C BIOS r3 rec+pre 16-bit | 2896 | - | 2898 | - | 1.001 |
| 0132C BIOS r3 rec+pre 32-bit | 6096 | - | 6102 | - | 1.001 |
| 0132C BIOS r3 all 8-bit | 1593 | - | 1596 | - | 1.002 |
| 0132C BIOS r3 all 16-bit | 3593 | - | 3601 | - | 1.002 |
| 0132C BIOS r3 all 32-bit | 7593 | - | 7597 | - | 1.001 |
| 0132C BIOS r3 8bit 8-bit | 702 | - | 704 | - | 1.003 |
| 0132C BIOS r3 8bit 16-bit | 1202 | - | 1204 | - | 1.002 |
| 0132C BIOS r3 8bit 32-bit | 2202 | - | 2210 | - | 1.004 |
| 0132C BIOS r3 16bit 8-bit | 702 | - | 705 | - | 1.004 |
| 0132C BIOS r3 16bit 16-bit | 702 | - | 704 | - | 1.003 |
| 0132C BIOS r3 16bit 32-bit | 1202 | - | 1206 | - | 1.003 |
| 0132C EXP1 r1 8-bit | 502 | - | 500 | - | 0.996 |
| 0132C EXP1 r1 16-bit | 802 | - | 799 | - | 0.996 |
| 0132C EXP1 r1 32-bit | 1402 | - | 1399 | - | 0.998 |
| 0132C EXP1 r3 8-bit | 702 | - | 699 | - | 0.996 |
| 0132C EXP1 r3 16-bit | 1202 | - | 1199 | - | 0.998 |
| 0132C EXP1 r3 32-bit | 2202 | - | 2199 | - | 0.999 |
| 0132C EXP1 r7 8-bit | 1102 | - | 1099 | - | 0.997 |
| 0132C EXP1 r7 16-bit | 2002 | - | 1999 | - | 0.999 |
| 0132C EXP1 r7 32-bit | 3802 | - | 3799 | - | 0.999 |
| 0132C EXP1 r15 8-bit | 1902 | - | 1899 | - | 0.998 |
| 0132C EXP1 r15 16-bit | 3602 | - | 3599 | - | 0.999 |
| 0132C EXP1 r15 32-bit | 7002 | - | 6999 | - | 1.000 |
| 0132C EXP1 r3 rec 8-bit | 1296 | - | 1293 | - | 0.998 |
| 0132C EXP1 r3 rec 16-bit | 2896 | - | 2893 | - | 0.999 |
| 0132C EXP1 r3 rec 32-bit | 6096 | - | 6093 | - | 1.000 |
| 0132C EXP1 r3 hold 8-bit | 702 | - | 699 | - | 0.996 |
| 0132C EXP1 r3 hold 16-bit | 1202 | - | 1199 | - | 0.998 |
| 0132C EXP1 r3 hold 32-bit | 2202 | - | 2199 | - | 0.999 |
| 0132C EXP1 r3 float 8-bit | 702 | - | 699 | - | 0.996 |
| 0132C EXP1 r3 float 16-bit | 1502 | - | 1499 | - | 0.998 |
| 0132C EXP1 r3 float 32-bit | 3102 | - | 3099 | - | 0.999 |
| 0132C EXP1 r3 pre 8-bit | 702 | - | 699 | - | 0.996 |
| 0132C EXP1 r3 pre 16-bit | 1202 | - | 1199 | - | 0.998 |
| 0132C EXP1 r3 pre 32-bit | 2202 | - | 2199 | - | 0.999 |
| 0132C EXP1 r3 rec+pre 8-bit | 1296 | - | 1293 | - | 0.998 |
| 0132C EXP1 r3 rec+pre 16-bit | 2896 | - | 2893 | - | 0.999 |
| 0132C EXP1 r3 rec+pre 32-bit | 6096 | - | 6093 | - | 1.000 |
| 0132C EXP1 r3 all 8-bit | 1593 | - | 1590 | - | 0.998 |
| 0132C EXP1 r3 all 16-bit | 3593 | - | 3590 | - | 0.999 |
| 0132C EXP1 r3 all 32-bit | 7593 | - | 7590 | - | 1.000 |
| 0132C EXP1 r3 8bit 8-bit | 702 | - | 699 | - | 0.996 |
| 0132C EXP1 r3 8bit 16-bit | 1202 | - | 1199 | - | 0.998 |
| 0132C EXP1 r3 8bit 32-bit | 2202 | - | 2199 | - | 0.999 |
| 0132C EXP1 r3 16bit 8-bit | 702 | - | 501 | - | 0.714 |
| 0132C EXP1 r3 16bit 16-bit | 702 | - | 1001 | - | 1.426 |
| 0132C EXP1 r3 16bit 32-bit | 1202 | - | 2001 | - | 1.665 |
| 0132C EXP2 r1 8-bit | 502 | - | 500 | - | 0.996 |
| 0132C EXP2 r1 16-bit | 802 | - | 799 | - | 0.996 |
| 0132C EXP2 r1 32-bit | 1402 | - | 1399 | - | 0.998 |
| 0132C EXP2 r3 8-bit | 702 | - | 699 | - | 0.996 |
| 0132C EXP2 r3 16-bit | 1202 | - | 1199 | - | 0.998 |
| 0132C EXP2 r3 32-bit | 2202 | - | 2199 | - | 0.999 |
| 0132C EXP2 r7 8-bit | 1102 | - | 1099 | - | 0.997 |
| 0132C EXP2 r7 16-bit | 2002 | - | 1999 | - | 0.999 |
| 0132C EXP2 r7 32-bit | 3802 | - | 3799 | - | 0.999 |
| 0132C EXP2 r15 8-bit | 1902 | - | 1899 | - | 0.998 |
| 0132C EXP2 r15 16-bit | 3602 | - | 3599 | - | 0.999 |
| 0132C EXP2 r15 32-bit | 7002 | - | 6999 | - | 1.000 |
| 0132C EXP2 r3 rec 8-bit | 1296 | - | 1293 | - | 0.998 |
| 0132C EXP2 r3 rec 16-bit | 2896 | - | 2893 | - | 0.999 |
| 0132C EXP2 r3 rec 32-bit | 6096 | - | 6093 | - | 1.000 |
| 0132C EXP2 r3 hold 8-bit | 702 | - | 699 | - | 0.996 |
| 0132C EXP2 r3 hold 16-bit | 1202 | - | 1199 | - | 0.998 |
| 0132C EXP2 r3 hold 32-bit | 2202 | - | 2199 | - | 0.999 |
| 0132C EXP2 r3 float 8-bit | 702 | - | 699 | - | 0.996 |
| 0132C EXP2 r3 float 16-bit | 1502 | - | 1499 | - | 0.998 |
| 0132C EXP2 r3 float 32-bit | 3102 | - | 3099 | - | 0.999 |
| 0132C EXP2 r3 pre 8-bit | 702 | - | 699 | - | 0.996 |
| 0132C EXP2 r3 pre 16-bit | 1202 | - | 1199 | - | 0.998 |
| 0132C EXP2 r3 pre 32-bit | 2202 | - | 2199 | - | 0.999 |
| 0132C EXP2 r3 rec+pre 8-bit | 1296 | - | 1293 | - | 0.998 |
| 0132C EXP2 r3 rec+pre 16-bit | 2896 | - | 2893 | - | 0.999 |
| 0132C EXP2 r3 rec+pre 32-bit | 6096 | - | 6093 | - | 1.000 |
| 0132C EXP2 r3 all 8-bit | 1593 | - | 1590 | - | 0.998 |
| 0132C EXP2 r3 all 16-bit | 3593 | - | 3590 | - | 0.999 |
| 0132C EXP2 r3 all 32-bit | 7593 | - | 7590 | - | 1.000 |
| 0132C EXP2 r3 8bit 8-bit | 702 | - | 699 | - | 0.996 |
| 0132C EXP2 r3 8bit 16-bit | 1202 | - | 1199 | - | 0.998 |
| 0132C EXP2 r3 8bit 32-bit | 2202 | - | 2199 | - | 0.999 |
| 0132C EXP2 r3 16bit 8-bit | 702 | - | 697 | - | 0.993 |
| 0132C EXP2 r3 16bit 16-bit | 702 | - | 699 | - | 0.996 |
| 0132C EXP2 r3 16bit 32-bit | 1202 | - | 1199 | - | 0.998 |
| 0132C CD r1 8-bit | 502 | - | 500 | - | 0.996 |
| 0132C CD r1 16-bit | 802 | - | 799 | - | 0.996 |
| 0132C CD r1 32-bit | 1402 | - | 1399 | - | 0.998 |
| 0132C CD r3 8-bit | 702 | - | 699 | - | 0.996 |
| 0132C CD r3 16-bit | 1202 | - | 1199 | - | 0.998 |
| 0132C CD r3 32-bit | 2202 | - | 2199 | - | 0.999 |
| 0132C CD r7 8-bit | 1102 | - | 1099 | - | 0.997 |
| 0132C CD r7 16-bit | 2002 | - | 1999 | - | 0.999 |
| 0132C CD r7 32-bit | 3802 | - | 3799 | - | 0.999 |
| 0132C CD r15 8-bit | 1902 | - | 1899 | - | 0.998 |
| 0132C CD r15 16-bit | 3602 | - | 3599 | - | 0.999 |
| 0132C CD r15 32-bit | 7002 | - | 6999 | - | 1.000 |
| 0132C CD r3 rec 8-bit | 1296 | - | 1293 | - | 0.998 |
| 0132C CD r3 rec 16-bit | 2896 | - | 2893 | - | 0.999 |
| 0132C CD r3 rec 32-bit | 6096 | - | 6093 | - | 1.000 |
| 0132C CD r3 hold 8-bit | 702 | - | 699 | - | 0.996 |
| 0132C CD r3 hold 16-bit | 1202 | - | 1199 | - | 0.998 |
| 0132C CD r3 hold 32-bit | 2202 | - | 2199 | - | 0.999 |
| 0132C CD r3 float 8-bit | 702 | - | 699 | - | 0.996 |
| 0132C CD r3 float 16-bit | 1502 | - | 1499 | - | 0.998 |
| 0132C CD r3 float 32-bit | 3102 | - | 3099 | - | 0.999 |
| 0132C CD r3 pre 8-bit | 702 | - | 699 | - | 0.996 |
| 0132C CD r3 pre 16-bit | 1202 | - | 1199 | - | 0.998 |
| 0132C CD r3 pre 32-bit | 2202 | - | 2199 | - | 0.999 |
| 0132C CD r3 rec+pre 8-bit | 1296 | - | 1293 | - | 0.998 |
| 0132C CD r3 rec+pre 16-bit | 2896 | - | 2893 | - | 0.999 |
| 0132C CD r3 rec+pre 32-bit | 6096 | - | 6093 | - | 1.000 |
| 0132C CD r3 all 8-bit | 1593 | - | 1590 | - | 0.998 |
| 0132C CD r3 all 16-bit | 3593 | - | 3590 | - | 0.999 |
| 0132C CD r3 all 32-bit | 7593 | - | 7590 | - | 1.000 |
| 31125 BIOS r3 rec 8-bit | 702 | - | 705 | - | 1.004 |
| 31125 BIOS r3 rec 16-bit | 1602 | - | 1606 | - | 1.002 |
| 31125 BIOS r3 rec 32-bit | 3402 | - | 3410 | - | 1.002 |
| 31125 BIOS r3 hold 8-bit | 702 | - | 704 | - | 1.003 |
| 31125 BIOS r3 hold 16-bit | 1202 | - | 1205 | - | 1.002 |
| 31125 BIOS r3 hold 32-bit | 2202 | - | 2207 | - | 1.002 |
| 31125 BIOS r3 float 8-bit | 702 | - | 706 | - | 1.006 |
| 31125 BIOS r3 float 16-bit | 1302 | - | 1306 | - | 1.003 |
| 31125 BIOS r3 float 32-bit | 2502 | - | 2504 | - | 1.001 |
| 31125 BIOS r3 pre 8-bit | 702 | - | 705 | - | 1.004 |
| 31125 BIOS r3 pre 16-bit | 1202 | - | 1208 | - | 1.005 |
| 31125 BIOS r3 pre 32-bit | 2202 | - | 2208 | - | 1.003 |
| 31125 BIOS r3 rec+pre 8-bit | 702 | - | 705 | - | 1.004 |
| 31125 BIOS r3 rec+pre 16-bit | 1602 | - | 1607 | - | 1.003 |
| 31125 BIOS r3 rec+pre 32-bit | 3402 | - | 3406 | - | 1.001 |
| 31125 BIOS r3 all 8-bit | 702 | - | 704 | - | 1.003 |
| 31125 BIOS r3 all 16-bit | 1802 | - | 1807 | - | 1.003 |
| 31125 BIOS r3 all 32-bit | 4002 | - | 4008 | - | 1.001 |
| 31125 EXP1 r3 rec 8-bit | 702 | - | 699 | - | 0.996 |
| 31125 EXP1 r3 rec 16-bit | 1602 | - | 1599 | - | 0.998 |
| 31125 EXP1 r3 rec 32-bit | 3402 | - | 3399 | - | 0.999 |
| 31125 EXP1 r3 hold 8-bit | 702 | - | 699 | - | 0.996 |
| 31125 EXP1 r3 hold 16-bit | 1202 | - | 1199 | - | 0.998 |
| 31125 EXP1 r3 hold 32-bit | 2202 | - | 2199 | - | 0.999 |
| 31125 EXP1 r3 float 8-bit | 702 | - | 699 | - | 0.996 |
| 31125 EXP1 r3 float 16-bit | 1302 | - | 1299 | - | 0.998 |
| 31125 EXP1 r3 float 32-bit | 2502 | - | 2499 | - | 0.999 |
| 31125 EXP1 r3 pre 8-bit | 702 | - | 699 | - | 0.996 |
| 31125 EXP1 r3 pre 16-bit | 1202 | - | 1199 | - | 0.998 |
| 31125 EXP1 r3 pre 32-bit | 2202 | - | 2199 | - | 0.999 |
| 31125 EXP1 r3 rec+pre 8-bit | 702 | - | 699 | - | 0.996 |
| 31125 EXP1 r3 rec+pre 16-bit | 1602 | - | 1599 | - | 0.998 |
| 31125 EXP1 r3 rec+pre 32-bit | 3402 | - | 3399 | - | 0.999 |
| 31125 EXP1 r3 all 8-bit | 702 | - | 699 | - | 0.996 |
| 31125 EXP1 r3 all 16-bit | 1802 | - | 1799 | - | 0.998 |
| 31125 EXP1 r3 all 32-bit | 4002 | - | 3999 | - | 0.999 |
| 31125 EXP2 r3 rec 8-bit | 702 | - | 699 | - | 0.996 |
| 31125 EXP2 r3 rec 16-bit | 1602 | - | 1599 | - | 0.998 |
| 31125 EXP2 r3 rec 32-bit | 3402 | - | 3399 | - | 0.999 |
| 31125 EXP2 r3 hold 8-bit | 702 | - | 699 | - | 0.996 |
| 31125 EXP2 r3 hold 16-bit | 1202 | - | 1199 | - | 0.998 |
| 31125 EXP2 r3 hold 32-bit | 2202 | - | 2199 | - | 0.999 |
| 31125 EXP2 r3 float 8-bit | 702 | - | 699 | - | 0.996 |
| 31125 EXP2 r3 float 16-bit | 1302 | - | 1299 | - | 0.998 |
| 31125 EXP2 r3 float 32-bit | 2502 | - | 2499 | - | 0.999 |
| 31125 EXP2 r3 pre 8-bit | 702 | - | 699 | - | 0.996 |
| 31125 EXP2 r3 pre 16-bit | 1202 | - | 1199 | - | 0.998 |
| 31125 EXP2 r3 pre 32-bit | 2202 | - | 2199 | - | 0.999 |
| 31125 EXP2 r3 rec+pre 8-bit | 702 | - | 699 | - | 0.996 |
| 31125 EXP2 r3 rec+pre 16-bit | 1602 | - | 1599 | - | 0.998 |
| 31125 EXP2 r3 rec+pre 32-bit | 3402 | - | 3399 | - | 0.999 |
| 31125 EXP2 r3 all 8-bit | 702 | - | 699 | - | 0.996 |
| 31125 EXP2 r3 all 16-bit | 1802 | - | 1799 | - | 0.998 |
| 31125 EXP2 r3 all 32-bit | 4002 | - | 3999 | - | 0.999 |
| 31125 CD r3 rec 8-bit | 702 | - | 699 | - | 0.996 |
| 31125 CD r3 rec 16-bit | 1602 | - | 1599 | - | 0.998 |
| 31125 CD r3 rec 32-bit | 3402 | - | 3399 | - | 0.999 |
| 31125 CD r3 hold 8-bit | 702 | - | 699 | - | 0.996 |
| 31125 CD r3 hold 16-bit | 1202 | - | 1199 | - | 0.998 |
| 31125 CD r3 hold 32-bit | 2202 | - | 2199 | - | 0.999 |
| 31125 CD r3 float 8-bit | 702 | - | 699 | - | 0.996 |
| 31125 CD r3 float 16-bit | 1302 | - | 1299 | - | 0.998 |
| 31125 CD r3 float 32-bit | 2502 | - | 2499 | - | 0.999 |
| 31125 CD r3 pre 8-bit | 702 | - | 699 | - | 0.996 |
| 31125 CD r3 pre 16-bit | 1202 | - | 1199 | - | 0.998 |
| 31125 CD r3 pre 32-bit | 2202 | - | 2199 | - | 0.999 |
| 31125 CD r3 rec+pre 8-bit | 702 | - | 699 | - | 0.996 |
| 31125 CD r3 rec+pre 16-bit | 1602 | - | 1599 | - | 0.998 |
| 31125 CD r3 rec+pre 32-bit | 3402 | - | 3399 | - | 0.999 |
| 31125 CD r3 all 8-bit | 702 | - | 699 | - | 0.996 |
| 31125 CD r3 all 16-bit | 1802 | - | 1799 | - | 0.998 |
| 31125 CD r3 all 32-bit | 4002 | - | 3999 | - | 0.999 |

### Peip  TimingCPU

| Measurement | Console | 0.6.0 | 0.6.1 | 0.6.0 / console | 0.6.1 / console |
|---|---:|---:|---:|---:|---:|
| IDLE100 | 2 | 2 | 2 | 1.000 | 1.000 |
| IDLE200 | 2 | 2 | 2 | 1.000 | 1.000 |
| IDLE1000 | 2 | 2 | 2 | 1.000 | 1.000 |
| EMPTY | 6 | 6 | 6 | 1.000 | 1.000 |
| NOP | 7 | 7 | 7 | 1.000 | 1.000 |
| ADDIU | 7 | 7 | 7 | 1.000 | 1.000 |
| MULTU 1x1 | 17 | 17 | 17 | 1.000 | 1.000 |
| M 16x1 | 20 | 20 | 20 | 1.000 | 1.000 |
| M 32x1 | 25 | 25 | 25 | 1.000 | 1.000 |
| M 1x16 | 17 | 17 | 17 | 1.000 | 1.000 |
| M 1x32 | 18 | 18 | 18 | 1.000 | 1.000 |
| M 32x32HL | 25 | 25 | 25 | 1.000 | 1.000 |
| M 32x32L | 24 | 24 | 24 | 1.000 | 1.000 |
| DIVU 1/0 | 48 | 48 | 48 | 1.000 | 1.000 |
| D 16/16 | 48 | 48 | 48 | 1.000 | 1.000 |
| D 32/32 | 48 | 48 | 48 | 1.000 | 1.000 |

### Peip  TimingLoadRAM

| Measurement | Console | 0.6.0 | 0.6.1 | 0.6.0 / console | 0.6.1 / console |
|---|---:|---:|---:|---:|---:|
| 10NOP | 16 | 16 | 16 | 1.000 | 1.000 |
| LWNOUSE | 18 | 18 | 18 | 1.000 | 1.000 |
| LWUSE0 | 18 | 18 | 18 | 1.000 | 1.000 |
| LWUSE1 | 22 | 22 | 22 | 1.000 | 1.000 |
| LWUSE2 | 22 | 22 | 22 | 1.000 | 1.000 |
| LWUSE3 | 21 | 21 | 21 | 1.000 | 1.000 |
| LWUSE4 | 20 | 20 | 20 | 1.000 | 1.000 |
| LWUSE5 | 19 | 19 | 19 | 1.000 | 1.000 |
| LWUSE6 | 18 | 18 | 18 | 1.000 | 1.000 |
| LWUSE7 | 18 | 18 | 18 | 1.000 | 1.000 |
| LBUSE1 | 22 | 22 | 22 | 1.000 | 1.000 |
| LW2X | 28 | 28 | 28 | 1.000 | 1.000 |
| LW3X1 | 32 | 32 | 33 | 1.000 | 1.031 |
| LW3X2 | 34 | 34 | 35 | 1.000 | 1.029 |
| LWSPAD1 | 16 | 16 | 16 | 1.000 | 1.000 |

### Peip  TimingLoadReg

| Measurement | Console | 0.6.0 | 0.6.1 | 0.6.0 / console | 0.6.1 / console |
|---|---:|---:|---:|---:|---:|
| SPAD B | 16 | 16 | 16 | 1.000 | 1.000 |
| SPAD H | 16 | 16 | 16 | 1.000 | 1.000 |
| SPAD W | 16 | 16 | 16 | 1.000 | 1.000 |
| RAM B | 22 | 22 | 22 | 1.000 | 1.000 |
| RAM H | 22 | 22 | 22 | 1.000 | 1.000 |
| RAM W | 22 | 22 | 22 | 1.000 | 1.000 |
| DMA B | 20 | 20 | 20 | 1.000 | 1.000 |
| DMA H | 20 | 20 | 20 | 1.000 | 1.000 |
| DMA W | 20 | 20 | 20 | 1.000 | 1.000 |
| JOY B | 20 | 20 | 20 | 1.000 | 1.000 |
| JOY H | 20 | 20 | 20 | 1.000 | 1.000 |
| JOY W | 20 | 20 | 20 | 1.000 | 1.000 |
| IRQ B | 20 | 20 | 20 | 1.000 | 1.000 |
| IRQ H | 20 | 20 | 20 | 1.000 | 1.000 |
| IRQ W | 20 | 20 | 20 | 1.000 | 1.000 |
| TMR B | 20 | 20 | 20 | 1.000 | 1.000 |
| TMR H | 20 | 20 | 20 | 1.000 | 1.000 |
| TMR W | 20 | 20 | 20 | 1.000 | 1.000 |
| CD B | 25 | 25 | 25 | 1.000 | 1.000 |
| CD H | 42 | 42 | 42 | 1.000 | 1.000 |
| CD W | 77 | 76 | 77 | 0.987 | 1.000 |
| GPU B | 20 | 20 | 20 | 1.000 | 1.000 |
| GPU H | 20 | 20 | 20 | 1.000 | 1.000 |
| GPU W | 20 | 20 | 20 | 1.000 | 1.000 |
| MDEC B | 20 | 20 | 20 | 1.000 | 1.000 |
| MDEC H | 20 | 20 | 20 | 1.000 | 1.000 |
| MDEC W | 20 | 20 | 20 | 1.000 | 1.000 |
| SPU B | 35 | 35 | 35 | 1.000 | 1.000 |
| SPU H | 35 | 35 | 35 | 1.000 | 1.000 |
| SPU W | 62 | 62 | 62 | 1.000 | 1.000 |
| SIO B | 20 | 20 | 20 | 1.000 | 1.000 |
| SIO H | 20 | 20 | 20 | 1.000 | 1.000 |
| SIO W | 20 | 20 | 20 | 1.000 | 1.000 |
| BIOS B | 24 | 24 | 25 | 1.000 | 1.042 |
| BIOS H | 32 | 32 | 33 | 1.000 | 1.031 |
| BIOS W | 48 | 48 | 49 | 1.000 | 1.021 |
| CTRL B | 16 | 16 | 16 | 1.000 | 1.000 |
| CTRL H | 16 | 16 | 16 | 1.000 | 1.000 |
| CTRL W | 16 | 16 | 16 | 1.000 | 1.000 |
| EXP1 B | 24 | 24 | 24 | 1.000 | 1.000 |
| EXP1 H | 32 | 32 | 32 | 1.000 | 1.000 |
| EXP1 W | 48 | 48 | 48 | 1.000 | 1.000 |
| EXP2 B | 28 | 28 | 28 | 1.000 | 1.000 |
| EXP2 H | 52 | 52 | 52 | 1.000 | 1.000 |
| EXP2 W | 101 | 101 | 101 | 1.000 | 1.000 |
| EXP3 B | 23 | 23 | 23 | 1.000 | 1.000 |
| EXP3 H | 23 | 23 | 23 | 1.000 | 1.000 |
| EXP3 W | 27 | 27 | 27 | 1.000 | 1.000 |
| DMA7 B | 18 | 18 | 18 | 1.000 | 1.000 |
| DMA7 H | 18 | 18 | 18 | 1.000 | 1.000 |
| DMA7 W | 18 | 18 | 18 | 1.000 | 1.000 |
| SPU7 B | 30 | 30 | 30 | 1.000 | 1.000 |
| SPU7 H | 30 | 30 | 30 | 1.000 | 1.000 |
| SPU7 W | 57 | 57 | 57 | 1.000 | 1.000 |

### Peip  TimingStoreReg

| Measurement | Console | 0.6.0 | 0.6.1 | 0.6.0 / console | 0.6.1 / console |
|---|---:|---:|---:|---:|---:|
| SPAD B | 16 | 16 | 16 | 1.000 | 1.000 |
| SPAD H | 16 | 16 | 16 | 1.000 | 1.000 |
| SPAD W | 16 | 16 | 16 | 1.000 | 1.000 |
| RAM B | 16 | 16 | 16 | 1.000 | 1.000 |
| RAM H | 16 | 16 | 16 | 1.000 | 1.000 |
| RAM W | 16 | 16 | 16 | 1.000 | 1.000 |
| DMA B | 16 | 16 | 16 | 1.000 | 1.000 |
| DMA H | 16 | 16 | 16 | 1.000 | 1.000 |
| DMA W | 16 | 16 | 16 | 1.000 | 1.000 |
| JOY B | 16 | 16 | 16 | 1.000 | 1.000 |
| JOY H | 16 | 16 | 16 | 1.000 | 1.000 |
| JOY W | 16 | 16 | 16 | 1.000 | 1.000 |
| IRQ B | 16 | 16 | 16 | 1.000 | 1.000 |
| IRQ H | 16 | 16 | 16 | 1.000 | 1.000 |
| IRQ W | 16 | 16 | 16 | 1.000 | 1.000 |
| TMR B | 16 | 16 | 16 | 1.000 | 1.000 |
| TMR H | 16 | 16 | 16 | 1.000 | 1.000 |
| TMR W | 16 | 16 | 16 | 1.000 | 1.000 |
| CD B | 16 | 16 | 16 | 1.000 | 1.000 |
| CD H | 32 | 33 | 33 | 1.031 | 1.031 |
| CD W | 63 | 65 | 65 | 1.032 | 1.032 |
| GPU B | 16 | 16 | 16 | 1.000 | 1.000 |
| GPU H | 16 | 16 | 16 | 1.000 | 1.000 |
| GPU W | 16 | 16 | 16 | 1.000 | 1.000 |
| MDEC B | 16 | 16 | 16 | 1.000 | 1.000 |
| MDEC H | 16 | 16 | 16 | 1.000 | 1.000 |
| MDEC W | 16 | 16 | 16 | 1.000 | 1.000 |
| SPU B | 16 | 16 | 16 | 1.000 | 1.000 |
| SPU H | 16 | 16 | 16 | 1.000 | 1.000 |
| SPU W | 28 | 28 | 28 | 1.000 | 1.000 |
| SIO B | 16 | 16 | 16 | 1.000 | 1.000 |
| SIO H | 16 | 16 | 16 | 1.000 | 1.000 |
| SIO W | 16 | 16 | 16 | 1.000 | 1.000 |
| EXP1 B | 17 | 18 | 18 | 1.059 | 1.059 |
| EXP1 H | 34 | 35 | 35 | 1.029 | 1.029 |
| EXP1 W | 67 | 69 | 69 | 1.030 | 1.030 |
| EXP2 B | 22 | 22 | 22 | 1.000 | 1.000 |
| EXP2 H | 44 | 44 | 44 | 1.000 | 1.000 |
| EXP2 W | 89 | 88 | 88 | 0.989 | 0.989 |
| EXP3 B | 16 | 16 | 16 | 1.000 | 1.000 |
| EXP3 H | 16 | 16 | 16 | 1.000 | 1.000 |
| EXP3 W | 16 | 16 | 16 | 1.000 | 1.000 |

### Peip  PipelineInstructionCache

| Measurement | Console | 0.6.0 | 0.6.1 | 0.6.0 / console | 0.6.1 / console |
|---|---:|---:|---:|---:|---:|
| 8 CACHED 0 NOT | 17 | 17 | 17 | 1.000 | 1.000 |
| 7 CACHED 1 NOT | 22 | 22 | 23 | 1.000 | 1.045 |
| 6 CACHED 2 NOT | 22 | 22 | 23 | 1.000 | 1.045 |
| 5 CACHED 3 NOT | 22 | 22 | 24 | 1.000 | 1.091 |
| 4 CACHED 4 NOT | 22 | 22 | 23 | 1.000 | 1.045 |
| 3 CACHED 5 NOT | 27 | 27 | 29 | 1.000 | 1.074 |
| 2 CACHED 6 NOT | 27 | 27 | 29 | 1.000 | 1.074 |
| 1 CACHED 7 NOT | 27 | 27 | 29 | 1.000 | 1.074 |
| 0 CACHED 8 NOT | 27 | 27 | 29 | 1.000 | 1.074 |
| 0 NOT 7 CACHED | 16 | 16 | 16 | 1.000 | 1.000 |
| 3 NOT 4 CACHED | 21 | 21 | 23 | 1.000 | 1.095 |
| 7 NOT 0 CACHED | 26 | 26 | 28 | 1.000 | 1.077 |
| 0 NOT 6 CACHED | 15 | 15 | 15 | 1.000 | 1.000 |
| 2 NOT 4 CACHED | 20 | 20 | 22 | 1.000 | 1.100 |
| 6 NOT 0 CACHED | 26 | 26 | 28 | 1.000 | 1.077 |
| 0 NOT 7 CACHED | 14 | 14 | 14 | 1.000 | 1.000 |
| 1 NOT 4 CACHED | 19 | 19 | 21 | 1.000 | 1.105 |
| 5 NOT 0 CACHED | 26 | 26 | 28 | 1.000 | 1.077 |

### Peip  PipelineInstructionBIOS

| Measurement | Console | 0.6.0 | 0.6.1 | 0.6.0 / console | 0.6.1 / console |
|---|---:|---:|---:|---:|---:|
| NOCACHE RET NOP | 74 | 74 | 76 | 1.000 | 1.027 |
| CACHE RET NOP | 74 | 74 | 76 | 1.000 | 1.027 |
| NOCACHE NOP RET NOP | 107 | 107 | 110 | 1.000 | 1.028 |
| CACHE3 NOP RET NOP | 107 | 107 | 110 | 1.000 | 1.028 |
| CACHE2 NOP RET NOP | 75 | 75 | 77 | 1.000 | 1.027 |

### Peip  PipelineInternalRegs

| Measurement | Console | 0.6.0 | 0.6.1 | 0.6.0 / console | 0.6.1 / console |
|---|---:|---:|---:|---:|---:|
| BASE 9 NOPS | 8 | 8 | 8 | 1.000 | 1.000 |
| SH | 8 | 8 | 8 | 1.000 | 1.000 |
| 2SH | 8 | 8 | 8 | 1.000 | 1.000 |
| 3SH | 8 | 8 | 8 | 1.000 | 1.000 |
| 4SH | 8 | 8 | 8 | 1.000 | 1.000 |
| 5SH | 11 | 11 | 11 | 1.000 | 1.000 |
| 6SH | 12 | 12 | 12 | 1.000 | 1.000 |
| 7SH | 14 | 14 | 14 | 1.000 | 1.000 |
| LH NOP MOVE | 12 | 12 | 12 | 1.000 | 1.000 |
| 2x LH NOP MOVE | 16 | 16 | 16 | 1.000 | 1.000 |
| SH LH NOP MOVE | 13 | 13 | 13 | 1.000 | 1.000 |
| 2SH LH NOP MOVE | 14 | 20 | 14 | 1.429 | 1.000 |
| 3SH LH NOP MOVE | 15 | 15 | 15 | 1.000 | 1.000 |
| 4SH LH NOP MOVE | 16 | 16 | 16 | 1.000 | 1.000 |

### Peip  ExtBusBusWidth

| Measurement | Console | 0.6.0 | 0.6.1 | 0.6.0 / console | 0.6.1 / console |
|---|---:|---:|---:|---:|---:|
| OFF S 1 8B | 9 | 9 | 9 | 1.000 | 1.000 |
| OFF S 1 16B | 9 | 9 | 9 | 1.000 | 1.000 |
| OFF S 1 32B | 9 | 9 | 9 | 1.000 | 1.000 |
| OFF S 2 8B | 12 | 12 | 15 | 1.000 | 1.250 |
| OFF S 2 16B | 14 | 14 | 14 | 1.000 | 1.000 |
| OFF S 2 32B | 18 | 18 | 18 | 1.000 | 1.000 |
| OFF S 3 8B | 15 | 15 | 15 | 1.000 | 1.000 |
| OFF S 3 16B | 19 | 19 | 19 | 1.000 | 1.000 |
| OFF S 3 32B | 27 | 27 | 27 | 1.000 | 1.000 |
| OFF S 4 8B | 18 | 18 | 18 | 1.000 | 1.000 |
| OFF S 4 16B | 24 | 24 | 24 | 1.000 | 1.000 |
| OFF S 4 32B | 36 | 36 | 36 | 1.000 | 1.000 |
| OFF L 1 8B | 12 | 12 | 12 | 1.000 | 1.000 |
| OFF L 1 16B | 14 | 14 | 18 | 1.000 | 1.286 |
| OFF L 1 32B | 18 | 18 | 18 | 1.000 | 1.000 |
| OFF L 2 8B | 18 | 18 | 18 | 1.000 | 1.000 |
| OFF L 2 16B | 22 | 22 | 22 | 1.000 | 1.000 |
| OFF L 2 32B | 30 | 30 | 37 | 1.000 | 1.233 |
| OFF L 3 8B | 24 | 24 | 24 | 1.000 | 1.000 |
| OFF L 3 16B | 30 | 30 | 30 | 1.000 | 1.000 |
| OFF L 3 32B | 42 | 42 | 42 | 1.000 | 1.000 |
| OFF L 4 8B | 30 | 37 | 30 | 1.233 | 1.000 |
| OFF L 4 16B | 38 | 38 | 38 | 1.000 | 1.000 |
| OFF L 4 32B | 54 | 54 | 54 | 1.000 | 1.000 |
| OFF SL 8B | 15 | 15 | 15 | 1.000 | 1.000 |
| OFF SL 16B | 19 | 19 | 19 | 1.000 | 1.000 |
| OFF SL 32B | 27 | 27 | 27 | 1.000 | 1.000 |
| OFF S 1 8B | 9 | 9 | 9 | 1.000 | 1.000 |
| OFF S 1 16B | 9 | 9 | 9 | 1.000 | 1.000 |
| OFF S 1 32B | 9 | 9 | 9 | 1.000 | 1.000 |
| OFF S 2 8B | 12 | 12 | 12 | 1.000 | 1.000 |
| OFF S 2 16B | 12 | 17 | 12 | 1.417 | 1.000 |
| OFF S 2 32B | 14 | 14 | 14 | 1.000 | 1.000 |
| OFF S 3 8B | 15 | 15 | 15 | 1.000 | 1.000 |
| OFF S 3 16B | 15 | 15 | 15 | 1.000 | 1.000 |
| OFF S 3 32B | 19 | 19 | 24 | 1.000 | 1.263 |
| OFF S 4 8B | 18 | 18 | 18 | 1.000 | 1.000 |
| OFF S 4 16B | 18 | 18 | 18 | 1.000 | 1.000 |
| OFF S 4 32B | 24 | 24 | 24 | 1.000 | 1.000 |
| OFF L 1 8B | 12 | 12 | 12 | 1.000 | 1.000 |
| OFF L 1 16B | 12 | 12 | 12 | 1.000 | 1.000 |
| OFF L 1 32B | 14 | 14 | 14 | 1.000 | 1.000 |
| OFF L 2 8B | 18 | 18 | 18 | 1.000 | 1.000 |
| OFF L 2 16B | 18 | 18 | 18 | 1.000 | 1.000 |
| OFF L 2 32B | 22 | 22 | 22 | 1.000 | 1.000 |
| OFF L 3 8B | 24 | 24 | 24 | 1.000 | 1.000 |
| OFF L 3 16B | 24 | 24 | 24 | 1.000 | 1.000 |
| OFF L 3 32B | 30 | 30 | 30 | 1.000 | 1.000 |
| OFF L 4 8B | 30 | 30 | 30 | 1.000 | 1.000 |
| OFF L 4 16B | 30 | 30 | 30 | 1.000 | 1.000 |
| OFF L 4 32B | 38 | 38 | 38 | 1.000 | 1.000 |
| OFF SL 8B | 15 | 16 | 15 | 1.067 | 1.000 |
| OFF SL 16B | 15 | 15 | 15 | 1.000 | 1.000 |
| OFF SL 32B | 19 | 19 | 19 | 1.000 | 1.000 |

## GPU

### GPU correctness: VRAM CRCs (semi, raster, tex...) (exact checks)

| Check | 0.6.0 | 0.6.1 |
|---|:---:|:---:|
| semi flat tri | matches | matches |
| semi gour tri | matches | matches |
| semi tex15 tri | matches | matches |
| semi tex8 tri | matches | matches |
| semi tex4 tri | matches | matches |
| semi rect/sprite | differs | matches |
| semi lines | matches | matches |
| raster fan | matches | matches |
| raster thin | matches | matches |
| raster quads | matches | matches |
| clip offset | matches | matches |
| max size | matches | matches |
| tex window | differs | matches |
| uv wrap/pages | matches | matches |
| tex modulate | matches | matches |
| dither | matches | matches |
| mask set/check | matches | matches |
| mask fill/copy | matches | matches |
| lines | matches | matches |
| fill round | matches | matches |
| copy overlap | matches | matches |
| rect sizes | differs | matches |
| gpustat fields | - | matches |
| gp0 fifo depth | differs | matches |

### GPU G13: 4/8-bit textured triangles

| Measurement | Console | 0.6.0 | 0.6.1 | 0.6.0 / console | 0.6.1 / console |
|---|---:|---:|---:|---:|---:|
| tri 4b 8x8 opaque [G13 textured triangle, legs L, O texels, per call] | 460 | 364 | 467 (463) | 0.791 | 1.016 |
| tri 4b 16x16 opaque [G13 textured triangle, legs L, O texels, per call] | 591 | 460 | 557 (552) | 0.778 | 0.943 |
| tri 4b 32x32 opaque [G13 textured triangle, legs L, O texels, per call] | 1000 | 738 | 1025 (1016) | 0.738 | 1.025 |
| tri 4b 64x64 opaque [G13 textured triangle, legs L, O texels, per call] | 2628 | 1663 | 2668 (2644) | 0.633 | 1.015 |
| tri 4b 8x8 semi0 [G13 textured triangle, legs L, O texels, per call] | 454 | 414 | 446 (442) | 0.912 | 0.983 |
| tri 4b 16x16 semi0 [G13 textured triangle, legs L, O texels, per call] | 589 | 593 | 634 (628) | 1.007 | 1.076 |
| tri 4b 32x32 semi0 [G13 textured triangle, legs L, O texels, per call] | 1048 | 1053 | 1114 (1104) | 1.005 | 1.063 |
| tri 4b 64x64 semi0 [G13 textured triangle, legs L, O texels, per call] | 2795 | 2531 | 2797 (2771) | 0.906 | 1.001 |
| tri 8b 8x8 opaque [G13 textured triangle, legs L, O texels, per call] | 459 | 364 | 461 (457) | 0.793 | 1.005 |
| tri 8b 16x16 opaque [G13 textured triangle, legs L, O texels, per call] | 589 | 456 | 558 (553) | 0.774 | 0.948 |
| tri 8b 32x32 opaque [G13 textured triangle, legs L, O texels, per call] | 1000 | 735 | 1024 (1015) | 0.735 | 1.024 |
| tri 8b 64x64 opaque [G13 textured triangle, legs L, O texels, per call] | 3986 | 3052 | 4026 (3989) | 0.766 | 1.010 |
| tri 8b 8x8 semi0 [G13 textured triangle, legs L, O texels, per call] | 454 | 455 | 459 (455) | 1.002 | 1.011 |
| tri 8b 16x16 semi0 [G13 textured triangle, legs L, O texels, per call] | 589 | 593 | 645 (639) | 1.007 | 1.095 |
| tri 8b 32x32 semi0 [G13 textured triangle, legs L, O texels, per call] | 1049 | 1055 | 1114 (1104) | 1.006 | 1.062 |
| tri 8b 64x64 semi0 [G13 textured triangle, legs L, O texels, per call] | 3716 | 3336 | 4199 (4161) | 0.898 | 1.130 |
| tri 4b 8x8 opaque K [G13 textured triangle batches: per-prim x100 (median), batch min/med/max, K] | 378.75 | - | 363 (360) | 0.748 | 0.959 |
| tri 4b 16x16 opaque K [G13 textured triangle batches: per-prim x100 (median), batch min/med/max, K] | 487.87 | - | 488 (483.62) | 0.758 | 1.000 |
| tri 4b 32x32 opaque K [G13 textured triangle batches: per-prim x100 (median), batch min/med/max, K] | 927.62 | - | 920 (911.62) | 0.690 | 0.992 |
| tri 4b 64x64 opaque K [G13 textured triangle batches: per-prim x100 (median), batch min/med/max, K] | 2559 | - | 2578 (2555) | 0.609 | 1.008 |
| tri 4b 8x8 semi0 K [G13 textured triangle batches: per-prim x100 (median), batch min/med/max, K] | 401.25 | - | 379 (375.12) | 0.908 | 0.943 |
| tri 4b 16x16 semi0 K [G13 textured triangle batches: per-prim x100 (median), batch min/med/max, K] | 537 | - | 549 (543.62) | 1.015 | 1.022 |
| tri 4b 32x32 semi0 K [G13 textured triangle batches: per-prim x100 (median), batch min/med/max, K] | 1003 | - | 1063 (1053) | 1.048 | 1.060 |
| tri 4b 64x64 semi0 K [G13 textured triangle batches: per-prim x100 (median), batch min/med/max, K] | 2758 | - | 2714 (2690) | 0.921 | 0.984 |
| tri 8b 8x8 opaque K [G13 textured triangle batches: per-prim x100 (median), batch min/med/max, K] | 378.75 | - | 364 (360.62) | 0.748 | 0.961 |
| tri 8b 16x16 opaque K [G13 textured triangle batches: per-prim x100 (median), batch min/med/max, K] | 488.12 | - | 488 (483.50) | 0.758 | 1.000 |
| tri 8b 32x32 opaque K [G13 textured triangle batches: per-prim x100 (median), batch min/med/max, K] | 931.25 | - | 919 (910.87) | 0.687 | 0.987 |
| tri 8b 64x64 opaque K [G13 textured triangle batches: per-prim x100 (median), batch min/med/max, K] | 4114 | - | 3946 (3910) | 0.721 | 0.959 |
| tri 8b 8x8 semi0 K [G13 textured triangle batches: per-prim x100 (median), batch min/med/max, K] | 401.12 | - | 379 (375.75) | 0.906 | 0.945 |
| tri 8b 16x16 semi0 K [G13 textured triangle batches: per-prim x100 (median), batch min/med/max, K] | 533.87 | - | 576 (571.12) | 1.022 | 1.080 |
| tri 8b 32x32 semi0 K [G13 textured triangle batches: per-prim x100 (median), batch min/med/max, K] | 1000 | - | 1078 (1068) | 1.049 | 1.078 |
| tri 8b 64x64 semi0 K [G13 textured triangle batches: per-prim x100 (median), batch min/med/max, K] | 4564 | - | 4367 (4327) | 0.870 | 0.957 |
| sprite 4b O opaque K | 2525 | - | 2332 (2311) | 0.915 | 0.924 |
| sprite 4b O semi0 K | 3999 | - | 3738 (3704) | 0.926 | 0.935 |
| quad 4b O opaque K | 4805 | - | 4572 (4530) | 0.610 | 0.952 |
| quad 4b O semi0 K | 5142 | - | 5974 (5920) | 0.962 | 1.162 |
| sprite 8b O opaque K | 8153 | - | 8182 (8107) | 0.773 | 1.004 |
| sprite 8b O semi0 K | 10070 | - | 9995 (9903) | 0.761 | 0.993 |
| quad 8b O opaque K | 9980 | - | 10117 (10025) | 0.764 | 1.014 |
| quad 8b O semi0 K | 11443 | - | 11619 (11513) | 0.821 | 1.015 |
| line semi h 10 rows [G13L semi / checked lines (K back to back)] | 198.40 | - | 195 (192.90) | 0.955 | 0.981 |
| line flat h 10 rows [G13L semi / checked lines (K back to back)] | 198.25 | - | 192 (190.37) | 0.955 | 0.969 |
| line semi gour h 100 [G13L semi / checked lines (K back to back)] | 339.15 | - | 328 (324.84) | 0.958 | 0.967 |
| line semi gour h 300 [G13L semi / checked lines (K back to back)] | 552.53 | - | 537 (532.34) | 0.962 | 0.972 |
| line semi gour v 100 [G13L semi / checked lines (K back to back)] | 1017 | - | 1113 (1103) | 1.085 | 1.095 |
| line chk v 100 [G13L semi / checked lines (K back to back)] | 955.03 | - | 1046 (1036) | 1.088 | 1.095 |
| line chk h 100 [G13L semi / checked lines (K back to back)] | 281.40 | - | 268 (265.21) | 0.832 | 0.951 |
| sprite 15b 8x8 K64 | 200.43 | - | 184 (182.18) | 0.905 | 0.917 |
| sprite 15b 16x16 K64 | 321.29 | - | 323 (320.43) | 0.811 | 1.007 |
| sprite 15b 32x32 K64 | 779.21 | - | 839 (831.10) | 0.824 | 1.076 |

### GPU26: GPUSTAT bit 26 around one primitive

| Measurement | Console | 0.6.0 | 0.6.1 | 0.6.0 / console | 0.6.1 / console |
|---|---:|---:|---:|---:|---:|
| base: GP0 NOP poll-26 med | 38 | - | 39 | - | 1.026 |
| base: E1 draw mode poll-26 med | 38 | - | 39 | - | 1.026 |
| rect 1x1 (68) poll-26 med | 75 | - | 61 (60) | - | 0.807 |
| rect 1x1 (68) bit-26 low | 16 | - | 15 | - | 0.946 |
| rect 1x1 (68) bit-26 high | 89 | - | 74 (73) | - | 0.828 |
| rect 8x8 (70) poll-26 med | 123 | - | 112 (111) | - | 0.911 |
| rect 8x8 (70) bit-26 low | 16 | - | 15 | - | 0.946 |
| rect 8x8 (70) bit-26 high | 137 | - | 128 (127) | - | 0.936 |
| rect 16x16 (78) poll-26 med | 237 | - | 215 (213) | - | 0.907 |
| rect 16x16 (78) bit-26 low | 16 | - | 15 | - | 0.946 |
| rect 16x16 (78) bit-26 high | 249 | - | 231 (229) | - | 0.928 |
| rect var 16x16 (60) poll-26 med | 256 | - | 236 (234) | - | 0.922 |
| rect var 16x16 (60) bit-26 low | 16 | - | 15 | - | 0.946 |
| rect var 16x16 (60) bit-26 high | 286 | - | 251 (249) | - | 0.879 |
| sprite 16x16 4-bit (7C) poll-26 med | 272 | - | 236 (234) | - | 0.868 |
| sprite 16x16 4-bit (7C) bit-26 low | 16 | - | 15 | - | 0.946 |
| sprite 16x16 4-bit (7C) bit-26 high | 271 | - | 249 (247) | - | 0.920 |
| sprite 16x16 15-bit (7C) poll-26 med | 256 | - | 270 (268) | - | 1.057 |
| sprite 16x16 15-bit (7C) bit-26 low | 16 | - | 15 | - | 0.946 |
| sprite 16x16 15-bit (7C) bit-26 high | 270 | - | 273 (271) | - | 1.013 |
| tri 3px flat (20) poll-26 med | 133 | - | 137 (136) | - | 1.032 |
| tri 3px flat (20) bit-26 low | 16 | - | 15 | - | 0.946 |
| tri 3px flat (20) bit-26 high | 147 | - | 150 (149) | - | 1.023 |
| tri 3px tex 4-bit (24) poll-26 med | 292 | - | 304 (301) | - | 1.040 |
| tri 3px tex 4-bit (24) bit-26 low | 16 | - | 15 | - | 0.946 |
| tri 3px tex 4-bit (24) bit-26 high | 306 | - | 317 (314) | - | 1.036 |
| tri 3px tex 15-bit (24) poll-26 med | 292 | - | 304 (301) | - | 1.040 |
| tri 3px tex 15-bit (24) bit-26 low | 16 | - | 15 | - | 0.946 |
| tri 3px tex 15-bit (24) bit-26 high | 306 | - | 317 (314) | - | 1.036 |
| fill 16x16 (02) poll-26 med | 176 | - | 185 (183) | - | 1.049 |
| fill 16x16 (02) bit-26 low | 16 | - | 15 | - | 0.946 |
| fill 16x16 (02) bit-26 high | 190 | - | 198 (196) | - | 1.041 |

### GPUPRIM  Primitive timing by size and format

| Measurement | Console | 0.6.0 | 0.6.1 | 0.6.0 / console | 0.6.1 / console |
|---|---:|---:|---:|---:|---:|
| Sprite 4-bit 16x16 | 220.68 | 188.38 | 210.99 | 0.854 | 0.956 |
| Sprite 4-bit 64x64 | 2323 | 2261 | 2198 | 0.973 | 0.946 |
| Sprite 4-bit 256x240 | 72718 | 60284 | 68465 | 0.829 | 0.942 |
| Sprite 4-bit 64x64 semi | 3692 | 3660 | 3550 | 0.991 | 0.962 |
| Quad tex 4-bit 128 | 38122 | 28348 | 37771 | 0.744 | 0.991 |
| Quad tex 4-bit 128 semi | 43641 | 34089 | 43110 | 0.781 | 0.988 |
| Sprite 8-bit 16x16 | 220.14 | 161.47 | 230.90 | 0.733 | 1.049 |
| Sprite 8-bit 64x64 | 7671 | 6028 | 7977 | 0.786 | 1.040 |
| Sprite 8-bit 256x240 | 113732 | 88273 | 117877 | 0.776 | 1.036 |
| Sprite 8-bit 64x64 semi | 9510 | 7320 | 9777 | 0.770 | 1.028 |
| Quad tex 8-bit 128 | 57435 | 46648 | 57571 | 0.812 | 1.002 |
| Quad tex 8-bit 128 semi | 64554 | 52031 | 64597 | 0.806 | 1.001 |
| Sprite 15-bit 16x16 | 220.14 | 161.47 | 219.60 | 0.733 | 0.998 |
| Sprite 15-bit 64x64 | 12446 | 12164 | 12149 | 0.977 | 0.976 |
| Sprite 15-bit 256x240 | 183920 | 180852 | 180152 | 0.983 | 0.980 |
| Sprite 15-bit 64x64 semi | 14554 | 14210 | 14197 | 0.976 | 0.975 |
| Quad tex 15-bit 128 | 93347 | 91502 | 91474 | 0.980 | 0.980 |
| Quad tex 15-bit 128 semi | 101607 | 99038 | 99282 | 0.975 | 0.977 |
| Tri flat 3px | 69.32 | 69.97 | 66.85 | 1.009 | 0.964 |
| Tri tex 15-bit 3px | 165.45 | 86.12 | 166.96 | 0.521 | 1.009 |
| Tri gouraud 64x64 | 2420 | 2404 | 2405 | 0.993 | 0.994 |
| Rect flat 1x1 | 67.17 | 26.91 | 63.94 | 0.401 | 0.952 |
| Rect flat 8x8 | 93.33 | 75.35 | 89.67 | 0.807 | 0.961 |
| Rect flat 16x16 | 214.22 | 193.77 | 196.78 | 0.905 | 0.919 |
| Fill 16x16 (02h) | 142.95 | 43.06 | 137.14 | 0.301 | 0.959 |
| Line flat 100 horiz | 157.38 | 129.18 | 127.88 | 0.821 | 0.813 |
| Line flat 100 diag | 535.23 | 538.25 | 512.19 | 1.006 | 0.957 |
| Line gouraud 100 | 429.09 | 387.54 | 383.87 | 0.903 | 0.895 |
| Line semi 100 | 489.16 | 527.48 | 525.54 | 1.078 | 1.074 |
| Polyline 8 points | 1137 | 1220 | 1152 | 1.073 | 1.013 |

## DMA

### LOADMIX v1.2: timings under load (MDEC, DMA IRQ fixed) (exact checks)

| Check | 0.6.0 | 0.6.1 |
|---|:---:|:---:|
| LOADMIX v1.2: timings under load (sysclk; 120 frames per config after 10 warm-up / CD_DELAY | - | matches |
| LOADMIX v1.2: timings under load (sysclk; 120 frames per config after 10 warm-up / clock check: IRQ entries 15 | - | differs |
| DICR IRQ edge: 2nd completion while a flag is set (edge rule 1/0/1, core pulse 1 / 1 fresh: | - | matches |
| DICR IRQ edge: 2nd completion while a flag is set (edge rule 1/0/1, core pulse 1 / 2 flag left set: | - | differs |
| DICR IRQ edge: 2nd completion while a flag is set (edge rule 1/0/1, core pulse 1 / 3 after the ack: | - | matches |
| GPU only (no CPU loop) / OTC | - | differs |
| +GPU / OTC | - | differs |
| +CD 2x / interval bins <.9 .9-1.1 1.1-1.9 1.9-2.9 >=2.9: | - | differs |
| +CD 2x / LBA steps 1/2/3+/repeat/back: | - | differs |
| +MDEC / MDEC MD1 before setup | - | matches |
| ALL (2x) / OTC | - | differs |
| ALL (2x) / interval bins <.9 .9-1.1 1.1-1.9 1.9-2.9 >=2.9: | - | differs |
| ALL (2x) / LBA steps 1/2/3+/repeat/back: | - | differs |
| ALL (2x) / MDEC MD1 before setup | - | matches |
| ALL-GPU / interval bins <.9 .9-1.1 1.1-1.9 1.9-2.9 >=2.9: | - | differs |
| ALL-GPU / LBA steps 1/2/3+/repeat/back: | - | differs |
| ALL-GPU / MDEC MD1 before setup | - | matches |
| ALL-CD / OTC | - | differs |
| ALL-CD / MDEC MD1 before setup | - | matches |
| ALL-SPU / OTC | - | differs |
| ALL-SPU / interval bins <.9 .9-1.1 1.1-1.9 1.9-2.9 >=2.9: | - | differs |
| ALL-SPU / LBA steps 1/2/3+/repeat/back: | - | differs |
| ALL-SPU / MDEC MD1 before setup | - | matches |
| ALL-MDEC / OTC | - | differs |
| ALL-MDEC / interval bins <.9 .9-1.1 1.1-1.9 1.9-2.9 >=2.9: | - | differs |
| ALL-MDEC / LBA steps 1/2/3+/repeat/back: | - | differs |
| ALL-load (no RAM sw) / OTC | - | differs |
| ALL-load (no RAM sw) / interval bins <.9 .9-1.1 1.1-1.9 1.9-2.9 >=2.9: | - | differs |
| ALL-load (no RAM sw) / LBA steps 1/2/3+/repeat/back: | - | differs |
| ALL-load (no RAM sw) / MDEC MD1 before setup | - | matches |
| ALL-stores (4 RAM sw) / OTC | - | differs |
| ALL-stores (4 RAM sw) / interval bins <.9 .9-1.1 1.1-1.9 1.9-2.9 >=2.9: | - | differs |
| ALL-stores (4 RAM sw) / LBA steps 1/2/3+/repeat/back: | - | differs |
| ALL-stores (4 RAM sw) / MDEC MD1 before setup | - | matches |
| ALL-tie (DPCR 0x0BBBBBBB) / OTC | - | differs |
| ALL-tie (DPCR 0x0BBBBBBB) / interval bins <.9 .9-1.1 1.1-1.9 1.9-2.9 >=2.9: | - | differs |
| ALL-tie (DPCR 0x0BBBBBBB) / LBA steps 1/2/3+/repeat/back: | - | differs |
| ALL-tie (DPCR 0x0BBBBBBB) / MDEC MD1 before setup | - | matches |
| +CD 1x / interval bins <.9 .9-1.1 1.1-1.9 1.9-2.9 >=2.9: | - | differs |
| +CD 1x / LBA steps 1/2/3+/repeat/back: | - | differs |
| ALL (1x) / OTC | - | differs |
| ALL (1x) / interval bins <.9 .9-1.1 1.1-1.9 1.9-2.9 >=2.9: | - | differs |
| ALL (1x) / LBA steps 1/2/3+/repeat/back: | - | differs |
| ALL (1x) / MDEC MD1 before setup | - | matches |

### LOADMIX v1.2: timings under load (MDEC, DMA IRQ fixed)

| Measurement | Console | 0.6.0 | 0.6.1 | 0.6.0 / console | 0.6.1 / console |
|---|---:|---:|---:|---:|---:|
| GPU only (no CPU loop) / GPU VSync->DMA2 done | 98873 | - | 94598 | - | 0.957 |
| GPU only (no CPU loop) / GPU VSync->idle | 99407 | - | 95081 | - | 0.956 |
| CPU alone / CPU pass length (sysclk x100) | 39.40 | - | 37.66 | - | 0.956 |
| CPU-IO (I_STAT, CD stat) / CPU pass length (sysclk x100) | 64.47 | - | 61.81 | - | 0.959 |
| +GPU / CPU pass length (sysclk x100) | 39.90 | - | 38.27 | - | 0.959 |
| +GPU / GPU VSync->DMA2 done | 99270 | - | 94259 | - | 0.950 |
| +GPU / GPU VSync->idle | 100194 | - | 95057 | - | 0.949 |
| +CD 2x / CPU pass length (sysclk x100) | 49.82 | - | 48.07 | - | 0.965 |
| +CD 2x / CD INT1 interval | 225792 | - | 225792 | - | 1.000 |
| +CD 2x / CD entry->DMA3 done | 46371 | - | 48549 | - | 1.047 |
| +CD 2x / CD DMA3 585 words | 46195 | - | 48397 | - | 1.048 |
| +SPU / CPU pass length (sysclk x100) | 42.26 | - | 40.83 | - | 0.966 |
| +SPU / SPU 4KB block-set | 70778 | - | 70384 | - | 0.994 |
| +MDEC / CPU pass length (sysclk x100) | 42.39 | - | 40.65 | - | 0.959 |
| +MDEC / MDEC 16 MB in+out | 49855 | - | 49444 | - | 0.992 |
| ALL (2x) / CPU pass length (sysclk x100) | 58.17 | - | 60.78 | - | 1.045 |
| ALL (2x) / GPU VSync->DMA2 done | 122071 | - | 102900 | - | 0.843 |
| ALL (2x) / GPU VSync->idle | 123145 | - | 104417 | - | 0.848 |
| ALL (2x) / CD INT1 interval | 225792 | - | 225792 | - | 1.000 |
| ALL (2x) / CD entry->DMA3 done | 46610 | - | 52650 | - | 1.130 |
| ALL (2x) / CD DMA3 585 words | 46410 | - | 52496 | - | 1.131 |
| ALL (2x) / SPU 4KB block-set | 90642 | - | 74952 | - | 0.827 |
| ALL (2x) / MDEC 16 MB in+out | 61861 | - | 56084 | - | 0.907 |
| ALL-GPU / CPU pass length (sysclk x100) | 58.04 | - | 58.04 | - | 1.000 |
| ALL-GPU / CD INT1 interval | 225792 | - | 225792 | - | 1.000 |
| ALL-GPU / CD entry->DMA3 done | 46485 | - | 52099 | - | 1.121 |
| ALL-GPU / CD DMA3 585 words | 46293 | - | 51953 | - | 1.122 |
| ALL-GPU / SPU 4KB block-set | 88093 | - | 74957 | - | 0.851 |
| ALL-GPU / MDEC 16 MB in+out | 61636 | - | 56076 | - | 0.910 |
| ALL-CD / CPU pass length (sysclk x100) | 46.47 | - | 45.28 | - | 0.974 |
| ALL-CD / GPU VSync->DMA2 done | 100657 | - | 96938 | - | 0.963 |
| ALL-CD / GPU VSync->idle | 101683 | - | 97941 | - | 0.963 |
| ALL-CD / SPU 4KB block-set | 72684 | - | 70631 | - | 0.972 |
| ALL-CD / MDEC 16 MB in+out | 49908 | - | 49542 | - | 0.993 |
| ALL-SPU / CPU pass length (sysclk x100) | 54.28 | - | 53.74 | - | 0.990 |
| ALL-SPU / GPU VSync->DMA2 done | 121510 | - | 101897 | - | 0.839 |
| ALL-SPU / GPU VSync->idle | 122561 | - | 102890 | - | 0.840 |
| ALL-SPU / CD INT1 interval | 225792 | - | 225793 | - | 1.000 |
| ALL-SPU / CD entry->DMA3 done | 46572 | - | 50586 | - | 1.086 |
| ALL-SPU / CD DMA3 585 words | 46385 | - | 50438 | - | 1.087 |
| ALL-SPU / MDEC 16 MB in+out | 61838 | - | 56189 | - | 0.909 |
| ALL-MDEC / CPU pass length (sysclk x100) | 54.07 | - | 53.77 | - | 0.994 |
| ALL-MDEC / GPU VSync->DMA2 done | 119323 | - | 106210 | - | 0.890 |
| ALL-MDEC / GPU VSync->idle | 120368 | - | 107138 | - | 0.890 |
| ALL-MDEC / CD INT1 interval | 225792 | - | 225792 | - | 1.000 |
| ALL-MDEC / CD entry->DMA3 done | 46581 | - | 51689 | - | 1.110 |
| ALL-MDEC / CD DMA3 585 words | 46398 | - | 51522 | - | 1.110 |
| ALL-MDEC / SPU 4KB block-set | 89982 | - | 74963 | - | 0.833 |
| ALL-load (no RAM sw) / CPU pass length (sysclk x100) | 49.91 | - | 60.19 | - | 1.206 |
| ALL-load (no RAM sw) / GPU VSync->DMA2 done | 119992 | - | 109483 | - | 0.912 |
| ALL-load (no RAM sw) / GPU VSync->idle | 121940 | - | 111113 | - | 0.911 |
| ALL-load (no RAM sw) / CD INT1 interval | 225792 | - | 225792 | - | 1.000 |
| ALL-load (no RAM sw) / CD entry->DMA3 done | 46550 | - | 53103 | - | 1.141 |
| ALL-load (no RAM sw) / CD DMA3 585 words | 46369 | - | 52935 | - | 1.142 |
| ALL-load (no RAM sw) / SPU 4KB block-set | 90033 | - | 74953 | - | 0.833 |
| ALL-load (no RAM sw) / MDEC 16 MB in+out | 61502 | - | 56083 | - | 0.912 |
| ALL-stores (4 RAM sw) / CPU pass length (sysclk x100) | 31.83 | - | 38.99 | - | 1.225 |
| ALL-stores (4 RAM sw) / GPU VSync->DMA2 done | 122305 | - | 103101 | - | 0.843 |
| ALL-stores (4 RAM sw) / GPU VSync->idle | 123691 | - | 104449 | - | 0.844 |
| ALL-stores (4 RAM sw) / CD INT1 interval | 225792 | - | 225791 | - | 1.000 |
| ALL-stores (4 RAM sw) / CD entry->DMA3 done | 46627 | - | 52835 | - | 1.133 |
| ALL-stores (4 RAM sw) / CD DMA3 585 words | 46444 | - | 52651 | - | 1.134 |
| ALL-stores (4 RAM sw) / SPU 4KB block-set | 90159 | - | 74947 | - | 0.831 |
| ALL-stores (4 RAM sw) / MDEC 16 MB in+out | 61491 | - | 56080 | - | 0.912 |
| ALL-tie (DPCR 0x0BBBBBBB) / CPU pass length (sysclk x100) | 58.69 | - | 59.73 | - | 1.018 |
| ALL-tie (DPCR 0x0BBBBBBB) / GPU VSync->DMA2 done | 121455 | - | 104513 | - | 0.861 |
| ALL-tie (DPCR 0x0BBBBBBB) / GPU VSync->idle | 123046 | - | 106039 | - | 0.862 |
| ALL-tie (DPCR 0x0BBBBBBB) / CD INT1 interval | 225792 | - | 225792 | - | 1.000 |
| ALL-tie (DPCR 0x0BBBBBBB) / CD entry->DMA3 done | 46504 | - | 52714 | - | 1.134 |
| ALL-tie (DPCR 0x0BBBBBBB) / CD DMA3 585 words | 46305 | - | 52561 | - | 1.135 |
| ALL-tie (DPCR 0x0BBBBBBB) / SPU 4KB block-set | 88012 | - | 74950 | - | 0.852 |
| ALL-tie (DPCR 0x0BBBBBBB) / MDEC 16 MB in+out | 61628 | - | 56076 | - | 0.910 |
| +CD 1x / CPU pass length (sysclk x100) | 43.83 | - | 42.15 | - | 0.962 |
| +CD 1x / CD INT1 interval | 451550 | - | 451584 | - | 1.000 |
| +CD 1x / CD entry->DMA3 done | 46365 | - | 48546 | - | 1.047 |
| +CD 1x / CD DMA3 585 words | 46194 | - | 48400 | - | 1.048 |
| ALL (1x) / CPU pass length (sysclk x100) | 52.38 | - | 51.13 | - | 0.976 |
| ALL (1x) / GPU VSync->DMA2 done | 111428 | - | 99807 | - | 0.896 |
| ALL (1x) / GPU VSync->idle | 112559 | - | 100896 | - | 0.896 |
| ALL (1x) / CD INT1 interval | 451495 | - | 451584 | - | 1.000 |
| ALL (1x) / CD entry->DMA3 done | 46644 | - | 52014 | - | 1.115 |
| ALL (1x) / CD DMA3 585 words | 46439 | - | 51841 | - | 1.116 |
| ALL (1x) / SPU 4KB block-set | 81035 | - | 74955 | - | 0.925 |
| ALL (1x) / MDEC 16 MB in+out | 55482 | - | 53160 | - | 0.958 |

### SPUDMA2  DMA arbitration and sound RAM

| Measurement | Console | 0.6.0 | 0.6.1 | 0.6.0 / console | 0.6.1 / console |
|---|---:|---:|---:|---:|---:|
| sw + 0 nops, none | 4006 | - | 4005 | - | 1.000 |
| sw + 0 nops, self 0w | 13207 | - | 13467 | - | 1.020 |
| sw + 0 nops, self 4w | 11048 | - | 10707 | - | 0.969 |
| sw + 2 nops, none | 6004 | - | 6005 | - | 1.000 |
| sw + 2 nops, self 0w | 13202 | - | 13447 | - | 1.019 |
| sw + 2 nops, self 4w | 11048 | - | 10694 | - | 0.968 |
| sw + 4 nops, none | 8004 | - | 8005 | - | 1.000 |
| sw + 4 nops, self 0w | 13202 | - | 13458 | - | 1.019 |
| sw + 4 nops, self 4w | 11048 | - | 10657 | - | 0.965 |
| sw + 8 nops, none | 12004 | - | 12005 | - | 1.000 |
| sw + 8 nops, self 0w | 13215 | - | 14347 | - | 1.086 |
| sw + 8 nops, self 4w | 12011 | - | 12007 | - | 1.000 |
| sw + 12 nops, none | 16004 | - | 16005 | - | 1.000 |
| sw + 12 nops, self 0w | 16008 | - | 16005 | - | 1.000 |
| sw + 12 nops, self 4w | 16009 | - | 16011 | - | 1.000 |
| sw + 16 nops, none | 20004 | - | 20005 | - | 1.000 |
| sw + 16 nops, self 0w | 20010 | - | 20005 | - | 1.000 |
| sw + 16 nops, self 4w | 20004 | - | 20005 | - | 1.000 |
| lw/0 nops/sw, none | 14225 | - | 14229 | - | 1.000 |
| lw/0 nops/sw, self 0w | 25595 | - | 25690 | - | 1.004 |
| lw/0 nops/sw, self 4w | 25575 | - | 25620 | - | 1.002 |
| lw/2 nops/sw, none | 15069 | - | 16230 | - | 1.077 |
| lw/2 nops/sw, self 0w | 25607 | - | 25683 | - | 1.003 |
| lw/2 nops/sw, self 4w | 25575 | - | 25610 | - | 1.001 |
| lw/4 nops/sw, none | 15069 | - | 18289 | - | 1.214 |
| lw/4 nops/sw, self 0w | 25608 | - | 26620 | - | 1.040 |
| lw/4 nops/sw, self 4w | 25575 | - | 25606 | - | 1.001 |
| lw/8 nops/sw, none | 16004 | - | 22331 | - | 1.395 |
| lw/8 nops/sw, self 0w | 25607 | - | 35194 | - | 1.374 |
| lw/8 nops/sw, self 4w | 25575 | - | 25600 | - | 1.001 |
| scratchpad sw, self 4w | 4005 | - | 4006 | - | 1.000 |
| scratchpad sw, none | 4004 | - | 4005 | - | 1.000 |
| sw, none | 4006 | - | 4005 | - | 1.000 |
| sw, SPU BS16 BA16 | 4261 | - | 4199 | - | 0.985 |
| sw, GPU NOPs BS16 BA256 | 9323 | - | 10063 | - | 1.079 |
| sw, OTC 4096 | 4006 | - | 4005 | - | 1.000 |
| lw/sw, none | 14228 | - | 14241 | - | 1.001 |
| lw/sw, SPU BS16 BA16 | 15153 | - | 15200 | - | 1.003 |
| lw/sw, GPU NOPs BS16 BA256 | 18767 | - | 19707 | - | 1.050 |
| lw/sw, OTC 4096 | 14220 | - | 14249 | - | 1.002 |
| ALU, none | 4003 | - | 4003 | - | 1.000 |
| ALU, SPU BS16 BA16 | 3943 | - | 3941 | - | 0.999 |
| ALU, GPU NOPs BS16 BA256 | 4007 | - | 3996 | - | 0.997 |
| ALU, OTC 4096 | 6 | - | 5 | - | 0.833 |
| BA16 BS 1 tight | 976 | - | 978 | - | 1.002 |
| BA16 BS 1 sparse | 992 | - | 996 | - | 1.004 |
| BA16 BS 2 tight | 1912 | - | 1932 | - | 1.010 |
| BA16 BS 2 sparse | 1912 | - | 1968 | - | 1.029 |
| BA16 BS 4 tight | 4244 | - | 4145 | - | 0.977 |
| BA16 BS 4 sparse | 4272 | - | 4134 | - | 0.968 |
| BA16 BS 16 tight | 16616 | - | 16665 | - | 1.003 |
| BA16 BS 16 sparse | 16615 | - | 16686 | - | 1.004 |
| BA1 after mode switch | 276 | - | 1050 | - | 3.804 |
| BA1 after SPUSTAT busy clear | 198 | - | 1019 | - | 5.146 |
| BA1 second, no mode switch | 71 | - | 70 | - | 0.986 |
| BA4 after mode switch | 422 | - | 1330 | - | 3.152 |
| BA4 second, no mode switch | 396 | - | 517 | - | 1.306 |
| 16 empty nodes tight | 223 | - | 277 | - | 1.242 |
| 16 empty nodes sparse | 209 | - | 280 | - | 1.340 |
| 16 nodes x 4 NOPs tight | 388 | - | 352 | - | 0.907 |
| 16 nodes x 4 NOPs sparse | 397 | - | 356 | - | 0.897 |
| OT 1024 tight | 12170 | - | 16366 | - | 1.345 |
| OT 1024 sparse | 11734 | - | 12088 | - | 1.030 |

### SPUDMA  SPU and GPU block/list timing

| Measurement | Console | 0.6.0 | 0.6.1 | 0.6.0 / console | 0.6.1 / console |
|---|---:|---:|---:|---:|---:|
| BA 1 BS 1 | 31 | - | 32 | - | 1.032 |
| BA 1 BS 2 | 35 | - | 36 | - | 1.029 |
| BA 1 BS 4 | 43 | - | 44 | - | 1.023 |
| BA 1 BS 8 | 59 | - | 60 | - | 1.017 |
| BA 1 BS 16 | 91 | - | 92 | - | 1.011 |
| BA 4 BS 1 | 184 | - | 186 | - | 1.011 |
| BA 4 BS 2 | 324 | - | 382 | - | 1.179 |
| BA 4 BS 4 | 867 | - | 854 | - | 0.985 |
| BA 4 BS 8 | 1683 | - | 1686 | - | 1.002 |
| BA 4 BS 16 | 3368 | - | 3398 | - | 1.009 |
| BA 16 BS 1 | 952 | - | 954 | - | 1.002 |
| BA 16 BS 2 | 1860 | - | 1918 | - | 1.031 |
| BA 16 BS 4 | 4220 | - | 4118 | - | 0.976 |
| BA 16 BS 8 | 8088 | - | 8216 | - | 1.016 |
| BA 16 BS 16 | 16592 | - | 16646 | - | 1.003 |
| read BA 1 BS 1 | 209 | - | 1058 | - | 5.062 |
| read BA 1 BS 16 | 282 | - | 1273 | - | 4.514 |
| read BA 16 BS 1 | 1139 | - | 2832 | - | 2.486 |
| read BA 16 BS 16 | 17015 | - | 17409 | - | 1.023 |
| k 0 N 1 | 43 | - | 39 | - | 0.907 |
| k 0 N 2 | 44 | - | 49 | - | 1.114 |
| k 0 N 4 | 64 | - | 73 | - | 1.141 |
| k 0 N 8 | 104 | - | 121 | - | 1.163 |
| k 0 N 16 | 184 | - | 220 | - | 1.196 |
| k 1 N 1 | 46 | - | 42 | - | 0.913 |
| k 1 N 2 | 50 | - | 56 | - | 1.120 |
| k 1 N 4 | 88 | - | 84 | - | 0.955 |
| k 1 N 8 | 146 | - | 140 | - | 0.959 |
| k 1 N 16 | 280 | - | 252 | - | 0.900 |
| k 2 N 1 | 53 | - | 43 | - | 0.811 |
| k 2 N 2 | 52 | - | 58 | - | 1.115 |
| k 2 N 4 | 86 | - | 90 | - | 1.047 |
| k 2 N 8 | 160 | - | 148 | - | 0.925 |
| k 2 N 16 | 290 | - | 269 | - | 0.928 |
| k 4 N 1 | 49 | - | 45 | - | 0.918 |
| k 4 N 2 | 56 | - | 60 | - | 1.071 |
| k 4 N 4 | 104 | - | 96 | - | 0.923 |
| k 4 N 8 | 170 | - | 164 | - | 0.965 |
| k 4 N 16 | 328 | - | 300 | - | 0.915 |
| NOPs BS 1 | 3089 | - | 2397 | - | 0.776 |
| NOPs BS 2 | 1572 | - | 1573 | - | 1.001 |
| NOPs BS 4 | 932 | - | 808 | - | 0.867 |
| NOPs BS 16 | 441 | - | 444 | - | 1.007 |
| NOPs BS 64 | 312 | - | 317 | - | 1.016 |
| A0 image BS 1 | 3089 | - | 2991 | - | 0.968 |
| A0 image BS 2 | 1573 | - | 1634 | - | 1.039 |
| A0 image BS 4 | 968 | - | 951 | - | 0.982 |
| A0 image BS 16 | 471 | - | 478 | - | 1.015 |
| A0 image BS 64 | 345 | - | 393 | - | 1.139 |
| OTC entries 256 | 294 | - | 294 | - | 1.000 |
| OTC entries 1024 | 1107 | - | 1117 | - | 1.009 |
| OT list entries 256 | 2646 | - | 3138 | - | 1.186 |
| OT list entries 1024 | 10500 | - | 12508 | - | 1.191 |

### Peip  DMAGPU

| Measurement | Console | 0.6.0 | 0.6.1 | 0.6.0 / console | 0.6.1 / console |
|---|---:|---:|---:|---:|---:|
| EMPTY 00 | 11 | 11 | 11 | 1.000 | 1.000 |
| SETUP 00 | 22 | 22 | 22 | 1.000 | 1.000 |
| WAITDONE 00 | 31 | 31 | 31 | 1.000 | 1.000 |
| DMA IN LEN 01 | 35 | 35 | 35 | 1.000 | 1.000 |
| DMA IN LEN 02 | 36 | 36 | 36 | 1.000 | 1.000 |
| DMA IN LEN 03 | 37 | 37 | 37 | 1.000 | 1.000 |
| DMA IN LEN 04 | 38 | 38 | 38 | 1.000 | 1.000 |
| DMA IN LEN 05 | 39 | 42 | 39 | 1.077 | 1.000 |
| DMA IN LEN 10 | 44 | 44 | 44 | 1.000 | 1.000 |
| DMA IN LEN 16 | 50 | 50 | 50 | 1.000 | 1.000 |
| DMA OUT LEN 01 | 35 | 35 | 35 | 1.000 | 1.000 |
| DMA OUT LEN 02 | 36 | 36 | 36 | 1.000 | 1.000 |
| DMA OUT LEN 16 | 50 | 50 | 50 | 1.000 | 1.000 |
| DMA IN LEN 01 | 35 | 35 | 35 | 1.000 | 1.000 |
| DMA IN LEN 02 | 40 | 40 | 40 | 1.000 | 1.000 |
| DMA IN LEN 03 | 41 | 41 | 41 | 1.000 | 1.000 |
| DMA OUT LEN 01 | 35 | 35 | 35 | 1.000 | 1.000 |
| DMA OUT LEN 03 | 42 | 42 | 42 | 1.000 | 1.000 |

### Peip  DMAOTCData

| Measurement | Console | 0.6.0 | 0.6.1 | 0.6.0 / console | 0.6.1 / console |
|---|---:|---:|---:|---:|---:|
| EMPTY 00 | 11 | 11 | 11 | 1.000 | 1.000 |
| SETUP 00 | 22 | 22 | 22 | 1.000 | 1.000 |
| WAITDONE 00 | 31 | 31 | 31 | 1.000 | 1.000 |
| OTC LEN 01 | 35 | 35 | 35 | 1.000 | 1.000 |
| OTC LEN 02 | 41 | 41 | 41 | 1.000 | 1.000 |
| OTC LEN 03 | 43 | - | 42 | - | 0.977 |
| OTC LEN 04 | 44.50 | - | 43 | - | 0.966 |
| OTC LEN 05 | 45.50 | - | 44 | - | 0.967 |
| OTC LEN 10 | 51.50 | - | 49 | - | 0.951 |
| OTC LEN 16 | 55.50 | - | 55 | - | 0.991 |
| OTC LEN 31 | 75 | - | 70 | - | 0.933 |
| OTC LEN 64 | 108 | - | 103 | - | 0.954 |
| OTC LEN 0127 | 171 | - | 171 | - | 1.000 |
| OTC LEN 0256 | 305.50 | - | 305 | - | 0.998 |
| OTC LEN 0511 | 577.50 | - | 580 | - | 1.004 |
| OTC LEN 01 | 35 | 35 | 35 | 1.000 | 1.000 |
| OTC LEN 02 | 36 | 36 | 36 | 1.000 | 1.000 |
| OTC LEN 03 | 37 | 37 | 37 | 1.000 | 1.000 |

## CD drive

### CDPHASE  Pause IRQ order and seek phase (exact checks)

| Check | 0.6.0 | 0.6.1 |
|---|:---:|:---:|
| Pause #1 +0 ms INT3 status | - | matches |
| Pause #1 +0 ms INT2 order | - | matches |
| Pause #1 +1 ms INT3 status | - | matches |
| Pause #1 +1 ms GetStat status | - | matches |
| Pause #1 +1 ms INT2 order | - | matches |
| Pause #1 +100 ms INT3 status | - | matches |
| Pause #1 +100 ms GetStat status | - | matches |
| Pause #1 +100 ms INT2 order | - | matches |
| Pause #1 +106 ms INT3 status | - | matches |
| Pause #1 +106 ms GetStat status | - | matches |
| Pause #1 +106 ms INT2 order | - | matches |
| Pause #1 +107 ms INT3 status | - | matches |
| Pause #1 +107 ms GetStat status | - | matches |
| Pause #1 +107 ms INT2 order | - | matches |
| Pause #1 +108 ms INT3 status | - | matches |
| Pause #1 +108 ms GetStat status | - | matches |
| Pause #1 +109 ms INT3 status | - | matches |
| Pause #1 +109 ms GetStat status | - | matches |
| Pause #1 +109 ms INT2 order | - | matches |
| Pause #1 +110 ms INT3 status | - | matches |
| Pause #1 +110 ms GetStat status | - | matches |
| Pause #1 +110 ms INT2 order | - | matches |
| Pause #1 +111 ms INT3 status | - | matches |
| Pause #1 +111 ms GetStat status | - | matches |
| Pause #1 +111 ms INT2 order | - | matches |
| Pause #1 +112 ms INT3 status | - | matches |
| Pause #1 +112 ms GetStat status | - | matches |
| Pause #1 +112 ms INT2 order | - | matches |
| Pause #2 +112 ms INT3 status | - | matches |
| Pause #2 +112 ms GetStat status | - | matches |
| Pause #2 +112 ms INT2 order | - | matches |
| Pause #2 +111 ms INT3 status | - | matches |
| Pause #2 +111 ms GetStat status | - | matches |
| Pause #2 +111 ms INT2 order | - | matches |
| Pause #2 +110 ms INT3 status | - | matches |
| Pause #2 +110 ms GetStat status | - | matches |
| Pause #2 +110 ms INT2 order | - | matches |
| Pause #2 +109 ms INT3 status | - | matches |
| Pause #2 +109 ms GetStat status | - | matches |
| Pause #2 +109 ms INT2 order | - | matches |
| Pause #2 +108 ms INT3 status | - | matches |
| Pause #2 +108 ms GetStat status | - | matches |
| Pause #2 +107 ms INT3 status | - | matches |
| Pause #2 +107 ms GetStat status | - | matches |
| Pause #2 +107 ms INT2 order | - | matches |
| Pause #2 +106 ms INT3 status | - | matches |
| Pause #2 +106 ms GetStat status | - | matches |
| Pause #2 +106 ms INT2 order | - | matches |
| Pause #2 +100 ms INT3 status | - | matches |
| Pause #2 +100 ms GetStat status | - | matches |
| Pause #2 +100 ms INT2 order | - | matches |
| Pause #2 +1 ms INT3 status | - | matches |
| Pause #2 +1 ms GetStat status | - | matches |
| Pause #2 +1 ms INT2 order | - | matches |
| Pause #2 +0 ms INT3 status | - | matches |
| Pause #2 +0 ms INT2 order | - | matches |
| Phase 0 R 8 ACK status | - | matches |
| Phase 0 R 8 done status | - | matches |
| Phase 0 R 8 sector header | - | matches |
| Phase 0 R 24 ACK status | - | matches |
| Phase 0 R 24 done status | - | matches |
| Phase 0 R 24 sector header | - | matches |
| Phase 0 R 40 ACK status | - | matches |
| Phase 0 R 40 done status | - | matches |
| Phase 0 R 40 sector header | - | matches |
| Phase 0 R 80 ACK status | - | matches |
| Phase 0 R 80 done status | - | matches |
| Phase 0 R 80 sector header | - | matches |
| Phase 0 R -40 ACK status | - | matches |
| Phase 0 R -40 done status | - | matches |
| Phase 0 R -40 sector header | - | matches |
| Phase 0 S 40 ACK status | - | matches |
| Phase 0 S 40 done status | - | matches |
| Phase 1 R 8 ACK status | - | matches |
| Phase 1 R 8 done status | - | matches |
| Phase 1 R 8 sector header | - | matches |
| Phase 1 R 24 ACK status | - | matches |
| Phase 1 R 24 done status | - | matches |
| Phase 1 R 24 sector header | - | matches |
| Phase 1 R 40 ACK status | - | matches |
| Phase 1 R 40 done status | - | matches |
| Phase 1 R 40 sector header | - | matches |
| Phase 1 R 80 ACK status | - | matches |
| Phase 1 R 80 done status | - | matches |
| Phase 1 R 80 sector header | - | matches |
| Phase 1 R -40 ACK status | - | matches |
| Phase 1 R -40 done status | - | matches |
| Phase 1 R -40 sector header | - | matches |
| Phase 1 S 40 ACK status | - | matches |
| Phase 1 S 40 done status | - | matches |
| Phase 2 R 8 ACK status | - | matches |
| Phase 2 R 8 done status | - | matches |
| Phase 2 R 8 sector header | - | matches |
| Phase 2 R 24 ACK status | - | matches |
| Phase 2 R 24 done status | - | matches |
| Phase 2 R 24 sector header | - | matches |
| Phase 2 R 40 ACK status | - | matches |
| Phase 2 R 40 done status | - | matches |
| Phase 2 R 40 sector header | - | matches |
| Phase 2 R 80 ACK status | - | matches |
| Phase 2 R 80 done status | - | matches |
| Phase 2 R 80 sector header | - | matches |
| Phase 2 R -40 ACK status | - | matches |
| Phase 2 R -40 done status | - | matches |
| Phase 2 R -40 sector header | - | matches |
| Phase 2 S 40 ACK status | - | matches |
| Phase 2 S 40 done status | - | matches |
| Phase 3 R 8 ACK status | - | matches |
| Phase 3 R 8 done status | - | matches |
| Phase 3 R 8 sector header | - | matches |
| Phase 3 R 24 ACK status | - | matches |
| Phase 3 R 24 done status | - | matches |
| Phase 3 R 24 sector header | - | matches |
| Phase 3 R 40 ACK status | - | matches |
| Phase 3 R 40 done status | - | matches |
| Phase 3 R 40 sector header | - | matches |
| Phase 3 R 80 ACK status | - | matches |
| Phase 3 R 80 done status | - | matches |
| Phase 3 R 80 sector header | - | matches |
| Phase 3 R -40 ACK status | - | matches |
| Phase 3 R -40 done status | - | matches |
| Phase 3 R -40 sector header | - | matches |
| Phase 3 S 40 ACK status | - | matches |
| Phase 3 S 40 done status | - | matches |

### CDTIME  Command ACK and sector timings

| Measurement | Console | 0.6.0 | 0.6.1 | 0.6.0 / console | 0.6.1 / console |
|---|---:|---:|---:|---:|---:|
| Init ACK | 69896 | 64435 | 69920 | 0.922 | 1.000 |
| Init complete | 4369768 | 476299 | 3998092 | 0.109 | 0.915 |
| Nop (GetStat) ACK | 39000 | 39068 | 27077 | 1.002 | 0.694 |
| Mute ACK | 44400 | 44460 | 27304 | 1.001 | 0.615 |
| Demute ACK | 46800 | 46864 | 33522 | 1.001 | 0.716 |
| Setloc ACK | 58000 | 58076 | 39380 | 1.001 | 0.679 |
| Setmode ACK | 44200 | 44274 | 30897 | 1.002 | 0.699 |
| Setloc ACK [2] | 43864 | 48708 | 39380 | 1.110 | 0.898 |
| ReadN ACK | 43568 | 43632 | 33976 | 1.001 | 0.780 |
| First sector | 4030000 | - | 3979460 | - | 0.987 |
| Next sectors | 451240 | 451472 | 606274 | 1.001 | 1.344 |
| Pause ACK | 40200 | 40344 | 40388 | 1.004 | 1.005 |
| Pause complete | 3790000 | 3779860 | 3755868 | 0.997 | 0.991 |
| Setloc ACK [3] | 38116 | 39352 | 39380 | 1.032 | 1.033 |
| ReadN ACK [2] | 60024 | 60128 | 64896 | 1.002 | 1.081 |
| First sector [2] | 19790000 | 19197360 | 19197416 | 0.970 | 0.970 |
| Next sectors [2] | 225510 | 225688 | 225679 | 1.001 | 1.001 |
| Pause ACK [2] | 40200 | 40344 | 40360 | 1.004 | 1.004 |
| Pause complete [2] | 1910000 | 1899912 | 1899872 | 0.995 | 0.995 |

### CDPHASE  Pause IRQ order and seek phase

| Measurement | Console | 0.6.0 | 0.6.1 | 0.6.0 / console | 0.6.1 / console |
|---|---:|---:|---:|---:|---:|
| Pause #1 +0 ms INT2 clocks | 3818768 | - | 3796256 | - | 0.994 |
| Pause #2 +0 ms INT2 clocks | 3796296 | - | 3796232 | - | 1.000 |

### Peip  PipelineCD

| Measurement | Console | 0.6.0 | 0.6.1 | 0.6.0 / console | 0.6.1 / console |
|---|---:|---:|---:|---:|---:|
| MEMCONTROL | 20943 | 20843 | 20943 | 0.995 | 1.000 |
| BASE NO TEST | 7 | 7 | 7 | 1.000 | 1.000 |
| 1SB | 9 | 9 | 9 | 1.000 | 1.000 |
| 2SB | 15 | 15 | 15 | 1.000 | 1.000 |
| 3SB | 32 | 32 | 32 | 1.000 | 1.000 |
| 4SB | 49 | 49 | 49 | 1.000 | 1.000 |
| 1SH | 9 | 9 | 9 | 1.000 | 1.000 |
| 2SH | 31 | 31 | 32 | 1.000 | 1.032 |
| 3SH | 64 | 64 | 64 | 1.000 | 1.000 |
| 4SH | 97 | 97 | 97 | 1.000 | 1.000 |
| 1SW | 9 | 9 | 9 | 1.000 | 1.000 |
| 2SW | 63 | 63 | 63 | 1.000 | 1.000 |
| 3SW | 128 | 128 | 128 | 1.000 | 1.000 |
| 1LB | 16 | 16 | 16 | 1.000 | 1.000 |
| 2LB | 34 | 34 | 34 | 1.000 | 1.000 |
| 1LH | 33 | 33 | 33 | 1.000 | 1.000 |
| 2LH | 68 | 68 | 68 | 1.000 | 1.000 |
| 1LW | 67 | 67 | 67 | 1.000 | 1.000 |
| 2LW | 136 | 136 | 136 | 1.000 | 1.000 |
| 1SHLH | 66 | 66 | 66 | 1.000 | 1.000 |

## Sound (SPU)

### SPU v3: FIFO, IRQ hit, ENVX, capture period, ENDX (exact checks)

| Check | 0.6.0 | 0.6.1 |
|---|:---:|:---:|
| SPU_DELAY | differs | differs |
| FIFO in Stop, Manual Write [1 FIFO across Stop: 4 halfwords A500-A503, step, Manual Write; SPU RAM 0x2000 re] | differs | differs |
| FIFO, Stop again + address [1 FIFO across Stop: 4 halfwords A500-A503, step, Manual Write; SPU RAM 0x2000 re] | differs | differs |
| Manual Write 1 hw, Stop, 3 more [1 FIFO across Stop: 4 halfwords A500-A503, step, Manual Write; SPU RAM 0x2000 re] | differs | differs |
| release lin r12 | differs | differs |
| release exp r12 | differs | differs |

### SPU v3: FIFO, IRQ hit, ENVX, capture period, ENDX

| Measurement | Console | 0.6.0 | 0.6.1 | 0.6.0 / console | 0.6.1 / console |
|---|---:|---:|---:|---:|---:|
| pitch 1000 off 0x00 [2 SPU IRQ: KON -> I_STAT.9, sysclk (x8), min/median/max of 5; sample 0x3000, 4 b] | 608 | 1432 | 656 | 2.355 | 1.079 |
| pitch 1000 off 0x08 [2 SPU IRQ: KON -> I_STAT.9, sysclk (x8), min/median/max of 5; sample 0x3000, 4 b] | 6752 | 11424 | 6824 | 1.692 | 1.011 |
| pitch 1000 off 0x10 [2 SPU IRQ: KON -> I_STAT.9, sysclk (x8), min/median/max of 5; sample 0x3000, 4 b] | 7424 | 1440 | 712 | 0.194 | 0.096 |
| pitch 1000 off 0x20 [2 SPU IRQ: KON -> I_STAT.9, sysclk (x8), min/median/max of 5; sample 0x3000, 4 b] | 8288 | 1440 | 680 | 0.174 | 0.082 |
| pitch 1000 off 0x38 [2 SPU IRQ: KON -> I_STAT.9, sysclk (x8), min/median/max of 5; sample 0x3000, 4 b] | 18224 | 75920 | 680 | 4.166 | 0.037 |
| pitch 0800 off 0x10 [2 SPU IRQ: KON -> I_STAT.9, sysclk (x8), min/median/max of 5; sample 0x3000, 4 b] | 536 | 45200 | 656 | 84.328 | 1.224 |
| pitch 2000 off 0x10 [2 SPU IRQ: KON -> I_STAT.9, sysclk (x8), min/median/max of 5; sample 0x3000, 4 b] | 344 | 12960 | 5320 | 37.674 | 15.465 |
| SPUSTAT.11 toggle period [4 capture buffer half flag (SPUSTAT.11): toggle period, sysclk, 4 periods] | 196608 | 196608 | 196624 | 1.000 | 1.000 |
| blocks 1 end flag 1, pitch [5 ENDX: KON -> ENDX bit 0 set, sysclk (x8), min/median/max of 5] | 17568 | 17568 | 17616 | 1.000 | 1.003 |
| blocks 2 end flag 1, pitch [5 ENDX: KON -> ENDX bit 0 set, sysclk (x8), min/median/max of 5] | 39120 | 39136 | 39120 | 1.000 | 1.000 |
| blocks 4 end flag 1, pitch [5 ENDX: KON -> ENDX bit 0 set, sysclk (x8), min/median/max of 5] | 82112 | 82104 | 82120 | 1.000 | 1.000 |
| PMON off (control) [6 PMON: voice 1 (2 blocks, pitch 1000) modulated by voice 0; KON1 -> ENDX bit 1,] | 39032 | - | 39048 | - | 1.000 |
| PMON, voice 0 +0x2000 [6 PMON: voice 1 (2 blocks, pitch 1000) modulated by voice 0; KON1 -> ENDX bit 1,] | 32896 | 32192 | 32144 | 0.979 | 0.977 |
| PMON, voice 0 -0x2000 [6 PMON: voice 1 (2 blocks, pitch 1000) modulated by voice 0; KON1 -> ENDX bit 1,] | 49784 | 52168 | 52096 | 1.048 | 1.046 |

## Video decoder (MDEC)

### MDEC probe v3: qscale 0, colour cells (hex) (exact checks)

| Check | 0.6.0 | 0.6.1 |
|---|:---:|:---:|
| a2 row 0 | - | matches |
| a2 row d | - | matches |
| a3 row 0 | - | matches |
| a3 row d | - | matches |
| b2 row 0 | - | matches |
| b2 row d | - | matches |
| c2 row 0 | - | matches |
| c2 row d | - | matches |
| c3 row 0 | - | matches |
| c3 row d | - | matches |
| a1 row 0 | - | matches |
| a1 row 1 | - | matches |
| a1 row 2 | - | matches |
| a1 row 3 | - | matches |
| a2 row 0 | - | matches |
| a2 row 1 | - | matches |
| a2 row 2 | - | matches |
| a2 row 3 | - | matches |
| a2 row d | - | matches |
| a3 row 0 | - | matches |
| a3 row 1 | - | matches |
| a3 row 2 | - | matches |
| a3 row 3 | - | matches |
| a3 row d | - | matches |
| b1 row 0 | - | matches |
| b1 row 1 | - | matches |
| b1 row 2 | - | matches |
| b1 row 3 | - | matches |
| b1 row d | - | matches |
| b2 row 0 | - | matches |
| b2 row 1 | - | matches |
| b2 row 2 | - | matches |
| b2 row 3 | - | matches |
| b2 row d | - | matches |
| c1 row 0 | - | matches |
| c1 row 1 | - | matches |
| c1 row 2 | - | matches |
| c1 row 3 | - | matches |
| c1 row d | - | matches |
| c2 row 0 | - | matches |
| c2 row 1 | - | matches |
| c2 row 2 | - | matches |
| c2 row 3 | - | matches |
| c2 row d | - | matches |
| c3 row 0 | - | matches |
| c3 row 1 | - | matches |
| c3 row 2 | - | matches |
| c3 row 3 | - | matches |
| c3 row d | - | matches |
| Bc0 | - | matches |
| Bc1 | differs | matches |
| Bc2 | differs | matches |
| Bc3 | differs | matches |
| Bcd [colour lines (probe v2 seed 1, 24-bit bytes / F 15-bit channels), d = minus wrap] | differs | matches |
| Bf0 | differs | matches |
| Bf1 | differs | matches |
| Bf2 | - | matches |
| Bf3 | differs | matches |
| Bfd [colour lines (probe v2 seed 1, 24-bit bytes / F 15-bit channels), d = minus wrap] | differs | matches |
| Bk0 | differs | matches |
| Bk1 | differs | matches |
| Bk2 | matches | matches |
| Bk3 | matches | matches |
| Bkd [colour lines (probe v2 seed 1, 24-bit bytes / F 15-bit channels), d = minus wrap] | differs | matches |
| Cc0 | differs | matches |
| Cc1 | differs | matches |
| Cc2 | differs | matches |
| Cc3 | differs | matches |
| Ccd [colour lines (probe v2 seed 1, 24-bit bytes / F 15-bit channels), d = minus wrap] | differs | matches |
| Ce0 | differs | matches |
| Ce1 | differs | matches |
| Ce2 | differs | matches |
| Ce3 | differs | matches |
| Ced [colour lines (probe v2 seed 1, 24-bit bytes / F 15-bit channels), d = minus wrap] | differs | differs |
| Ea0 | matches | matches |
| Ea1 | - | matches |
| Ea2 | differs | matches |
| Ea3 | differs | matches |
| Ead [colour lines (probe v2 seed 1, 24-bit bytes / F 15-bit channels), d = minus wrap] | differs | matches |
| Fe0 | - | matches |
| Fe1 | differs | matches |
| Fe2 | differs | matches |
| Fe3 | differs | matches |
| Fed [colour lines (probe v2 seed 1, 24-bit bytes / F 15-bit channels), d = minus wrap] | differs | matches |

### ps1-tests  8bit (exact checks)

| Check | 0.6.0 | 0.6.1 |
|---|:---:|:---:|
| mdec_reset (MDEC_CTRL <- 0x80000000)... ok | - | matches |
| mdec_quantTable(addr=0x80012080, color=1)... ok | - | matches |
| mdec_idctTable(addr=0x80012100)... ok | - | matches |
| mdec_enableDma (MDEC_CTRL <- 0x60000000)... ok | - | matches |
| 8x8 block in 8bit mode: | - | matches |
| 0: 00 ff ff 00 ff ff 04 00 | - | matches |
| 8: c9 ec ef ed ef fc f2 00 | - | matches |
| 10: d5 db fa e8 fe e8 ff 00 | - | matches |
| 18: b7 f3 ec ef eb ff e3 00 | - | matches |
| 20: 00 fb ff f5 f2 ff 03 00 | - | matches |
| 28: 00 05 ff fc ff 08 1a 00 | - | matches |
| 30: 0f 28 1e ff 05 2a 23 00 | - | matches |
| 38: 10 38 40 29 32 32 16 0f | - | matches |
| mdec_reset (MDEC_CTRL <- 0x80000000)... ok | - | matches |

### ps1-tests  4bit (exact checks)

| Check | 0.6.0 | 0.6.1 |
|---|:---:|:---:|
| mdec_reset (MDEC_CTRL <- 0x80000000)... ok | - | matches |
| mdec_quantTable(addr=0x80012080, color=1)... ok | - | matches |
| mdec_idctTable(addr=0x80012100)... ok | - | matches |
| mdec_enableDma (MDEC_CTRL <- 0x60000000)... ok | - | matches |
| 8x8 block in 4bit mode: | - | matches |
| 0: f0 0f ff 00 | - | matches |
| 4: fd ff ff 0f | - | matches |
| 8: ed ff ff 0f | - | matches |
| c: fb ff ff 0e | - | matches |
| 10: f0 ff ff 00 | - | matches |
| 14: 00 ff 1f 02 | - | matches |
| 18: 31 f2 30 02 | - | matches |
| 1c: 41 34 33 11 | - | matches |
| mdec_reset (MDEC_CTRL <- 0x80000000)... ok | - | matches |

## Geometry (GTE)

### GTE digest: CRC of every command (seeded inputs) (exact checks)

| Check | 0.6.0 | 0.6.1 |
|---|:---:|:---:|
| N | matches | matches |
| regs | matches | matches |
| RTPT | matches | matches |
| OP | matches | matches |
| DPCT | matches | matches |
| MVMVA [GTECRC: CRC32 of all 64 GTE registers after each command, N random states (seed] | matches | matches |
| MVMVA [GTECRC: CRC32 of all 64 GTE registers after each command, N random states (seed] | matches | matches |
| NCDS | matches | matches |
| NCCS | matches | matches |
| CDP | matches | matches |
| NCT | matches | matches |
| DCPL | matches | matches |
| GPF | matches | matches |
| GPL | matches | matches |

### GTE: full hardware register replay (exact checks)

| Check | 0.6.0 | 0.6.1 |
|---|:---:|:---:|
| 0x01 RTPS differing registers | - | matches |
| 0x06 NCLIP differing registers | - | matches |
| 0x0c OP differing registers | - | matches |
| 0x10 DPCS differing registers | - | matches |
| 0x11 INTPL differing registers | - | matches |
| 0x12 MVMVA differing registers | - | matches |
| 0x13 NCDS differing registers | - | matches |
| 0x14 CDP differing registers | - | matches |
| 0x16 NCDT differing registers | - | matches |
| 0x1b NCCS differing registers | - | matches |
| 0x1c CC differing registers | - | matches |
| 0x1e NCS differing registers | - | matches |
| 0x20 NCT differing registers | - | matches |
| 0x28 SQR differing registers | - | matches |
| 0x29 DCPL differing registers | - | matches |
| 0x2a DPCT differing registers | - | matches |
| 0x2d AVSZ3 differing registers | - | matches |
| 0x2e AVSZ4 differing registers | - | matches |
| 0x30 RTPT differing registers | - | matches |
| 0x3d GPF differing registers | - | matches |
| 0x3e GPL differing registers | - | matches |
| 0x3f NCCT differing registers | - | matches |
| 0x40 --- differing registers | - | matches |

### Peip  GTETiming

| Measurement | Console | 0.6.0 | 0.6.1 | 0.6.0 / console | 0.6.1 / console |
|---|---:|---:|---:|---:|---:|
| NOPMFC2 | 8 | 8 | 8 | 1.000 | 1.000 |
| RTPS | 23 | 23 | 23 | 1.000 | 1.000 |
| RTPT | 31 | 31 | 31 | 1.000 | 1.000 |
| NCLIP | 16 | 16 | 16 | 1.000 | 1.000 |
| AVSZ3 | 13 | 13 | 13 | 1.000 | 1.000 |
| AVSZ4 | 14 | 14 | 14 | 1.000 | 1.000 |
| MVMVA | 16 | 16 | 16 | 1.000 | 1.000 |
| SQR | 13 | 13 | 13 | 1.000 | 1.000 |
| OP | 14 | 14 | 14 | 1.000 | 1.000 |
| NCS | 22 | 22 | 22 | 1.000 | 1.000 |
| NCT | 38 | 38 | 38 | 1.000 | 1.000 |
| NCCS | 25 | 25 | 25 | 1.000 | 1.000 |
| NCCT | 47 | 47 | 47 | 1.000 | 1.000 |
| NCDS | 27 | 27 | 27 | 1.000 | 1.000 |
| NCDT | 52 | 52 | 52 | 1.000 | 1.000 |
| CC | 19 | 19 | 19 | 1.000 | 1.000 |
| CDP | 21 | 21 | 21 | 1.000 | 1.000 |
| DCPL | 16 | 16 | 16 | 1.000 | 1.000 |
| DPCS | 16 | 16 | 16 | 1.000 | 1.000 |
| DPCT | 25 | 28 | 25 | 1.120 | 1.000 |
| INTPL | 16 | 16 | 16 | 1.000 | 1.000 |
| GPF | 13 | 13 | 13 | 1.000 | 1.000 |
| GPL | 13 | 13 | 13 | 1.000 | 1.000 |

## Timers and interrupts

### Timers: IRQ modes, flags, sync modes (exact checks)

| Check | 0.6.0 | 0.6.1 |
|---|:---:|:---:|
| T0 0058 target, repeat, pulse, reset@tgt | matches | matches |
| T0 0018 target, one-shot, pulse, reset@tgt | matches | matches |
| T0 00D8 target, repeat, toggle, reset@tgt | matches | matches |
| T0 0020 0xFFFF, one-shot, pulse, free | matches | matches |
| T0 0060 0xFFFF, repeat, pulse, free | matches | matches |
| T0 0078 tgt+FFFF, repeat, pulse, reset@tgt | matches | matches |
| T0 [B mode-register flags after the first IRQ: read 1, read 2 at once, read 3 after] | matches | matches |
| T1 0058 target, repeat, pulse, reset@tgt | matches | matches |
| T1 0018 target, one-shot, pulse, reset@tgt | matches | matches |
| T1 00D8 target, repeat, toggle, reset@tgt | matches | matches |
| T1 0020 0xFFFF, one-shot, pulse, free | matches | matches |
| T1 0060 0xFFFF, repeat, pulse, free | matches | matches |
| T1 0078 tgt+FFFF, repeat, pulse, reset@tgt | matches | matches |
| T1 [B mode-register flags after the first IRQ: read 1, read 2 at once, read 3 after] | matches | matches |
| T2 0058 target, repeat, pulse, reset@tgt | matches | matches |
| T2 0018 target, one-shot, pulse, reset@tgt | matches | matches |
| T2 00D8 target, repeat, toggle, reset@tgt | matches | matches |
| T2 0020 0xFFFF, one-shot, pulse, free | matches | matches |
| T2 0060 0xFFFF, repeat, pulse, free | matches | matches |
| T2 0078 tgt+FFFF, repeat, pulse, reset@tgt | matches | matches |
| T2 [B mode-register flags after the first IRQ: read 1, read 2 at once, read 3 after] | matches | matches |
| sync mode 2 [T0 sysclock, window spin(4000)] | matches | matches |
| sync mode 2 [T1 hblank, window spin(40000)] | matches | matches |
| sync mode 3 [T1 hblank, window spin(40000)] | matches | matches |
| sync mode 0 [T2 sysclock/8, window spin(4000)] | matches | matches |
| sync mode 3 [T2 sysclock/8, window spin(4000)] | matches | matches |

### Peip  TimerCalib (exact checks)

| Check | 0.6.0 | 0.6.1 |
|---|:---:|:---:|
| 0 NOP KUSEG | matches | matches |
| 0 NOP KSEG0 | - | matches |
| 0 NOP KSEG1 | matches | matches |
| 1 NOP KUSEG | matches | matches |
| 1 NOP KSEG1 | matches | matches |
| 0 TMR2 DIV8 | matches | matches |
| 1 TMR2 DIV8 | matches | matches |
| NOP LOOP 65537 | matches | matches |

### Peip  TimerSet (exact checks)

| Check | 0.6.0 | 0.6.1 |
|---|:---:|:---:|
| CURRENT 3 RESET 0 | matches | matches |
| CURRENT 3 RESET 1 | matches | matches |
| TARGET 9 CURRENT 9 2 | matches | matches |
| TARGET 9 CURRENT 9 3 | matches | matches |

### Peip  TimerWrap (exact checks)

| Check | 0.6.0 | 0.6.1 |
|---|:---:|:---:|
| TIMERWRAP0 1 | matches | matches |
| TIMERWRAP0 2 | matches | matches |
| TIMERWRAP0 3 | matches | matches |
| TIMERWRAP1 1 | matches | matches |
| TIMERWRAP1 3 | matches | matches |
| TIMERWRAP1 4 | matches | matches |
| TIMERWRAP6 8 | matches | matches |
| TIMERWRAP6 9 | matches | matches |
| TIMERWRAP7 9 | matches | matches |

### ps1-tests  timers (exact checks)

| Check | 0.6.0 | 0.6.1 |
|---|:---:|:---:|
| Reached target | - | matches |
| Reached 0xFFFF | - | matches |
| Counter reset AFTER reaching target | - | matches |
| Target + 1 not reached | - | matches |

### Timers: IRQ modes, flags, sync modes

| Measurement | Console | 0.6.0 | 0.6.1 | 0.6.0 / console | 0.6.1 / console |
|---|---:|---:|---:|---:|---:|
| T0 [A IRQ modes: I_STAT rises over 1,500 x spin(64) (min med max of 9), sysclock sou] | 355 | - | 346 | 1.003 | 0.975 |
| T0 [A IRQ modes: I_STAT rises over 1,500 x spin(64) (min med max of 9), sysclock sou] | 1 | 1 | 1 | 1.000 | 1.000 |
| T0 [A IRQ modes: I_STAT rises over 1,500 x spin(64) (min med max of 9), sysclock sou] | 178 | - | 173 | 1.000 | 0.972 |
| T0 [A IRQ modes: I_STAT rises over 1,500 x spin(64) (min med max of 9), sysclock sou] | 1 | 1 | 1 | 1.000 | 1.000 |
| T0 [A IRQ modes: I_STAT rises over 1,500 x spin(64) (min med max of 9), sysclock sou] | 5 | - | 5 | 1.000 | 1.000 |
| T0 [A IRQ modes: I_STAT rises over 1,500 x spin(64) (min med max of 9), sysclock sou] | 355 | - | 346 | 1.003 | 0.975 |
| T0 [A IRQ modes: I_STAT rises over 1,500 x spin(64) (min med max of 9), sysclock sou] | 178 | - | 173 | 1.000 | 0.972 |
| T1 [A IRQ modes: I_STAT rises over 1,500 x spin(64) (min med max of 9), sysclock sou] | 355 | - | 347 | 1.003 | 0.977 |
| T1 [A IRQ modes: I_STAT rises over 1,500 x spin(64) (min med max of 9), sysclock sou] | 1 | 1 | 1 | 1.000 | 1.000 |
| T1 [A IRQ modes: I_STAT rises over 1,500 x spin(64) (min med max of 9), sysclock sou] | 178 | - | 173 | 1.000 | 0.972 |
| T1 [A IRQ modes: I_STAT rises over 1,500 x spin(64) (min med max of 9), sysclock sou] | 1 | 1 | 1 | 1.000 | 1.000 |
| T1 [A IRQ modes: I_STAT rises over 1,500 x spin(64) (min med max of 9), sysclock sou] | 5 | - | 5 | 1.000 | 1.000 |
| T1 [A IRQ modes: I_STAT rises over 1,500 x spin(64) (min med max of 9), sysclock sou] | 355 | - | 347 | 1.003 | 0.977 |
| T1 [A IRQ modes: I_STAT rises over 1,500 x spin(64) (min med max of 9), sysclock sou] | 178 | - | 173 | 1.000 | 0.972 |
| T2 [A IRQ modes: I_STAT rises over 1,500 x spin(64) (min med max of 9), sysclock sou] | 355 | - | 347 | 1.003 | 0.977 |
| T2 [A IRQ modes: I_STAT rises over 1,500 x spin(64) (min med max of 9), sysclock sou] | 1 | 1 | 1 | 1.000 | 1.000 |
| T2 [A IRQ modes: I_STAT rises over 1,500 x spin(64) (min med max of 9), sysclock sou] | 178 | - | 173 | 1.000 | 0.972 |
| T2 [A IRQ modes: I_STAT rises over 1,500 x spin(64) (min med max of 9), sysclock sou] | 1 | 1 | 1 | 1.000 | 1.000 |
| T2 [A IRQ modes: I_STAT rises over 1,500 x spin(64) (min med max of 9), sysclock sou] | 5 | - | 5 | 1.000 | 1.000 |
| T2 [A IRQ modes: I_STAT rises over 1,500 x spin(64) (min med max of 9), sysclock sou] | 355 | - | 347 | 1.003 | 0.977 |
| T2 [A IRQ modes: I_STAT rises over 1,500 x spin(64) (min med max of 9), sysclock sou] | 178 | - | 173 | 1.000 | 0.972 |
| sync mode 0 [T0 sysclock, window spin(4000)] | 8994 | - | 8868 | - | 0.986 |
| sync mode 1 [T0 sysclock, window spin(4000)] | 709 | - | 957 | - | 1.350 |
| sync mode 3 [T0 sysclock, window spin(4000)] | 10874 | - | 10985 | - | 1.010 |
| no sync [T0 sysclock, window spin(4000)] | 11999 | - | 11998 | - | 1.000 |
| sync mode 0 [T1 hblank, window spin(40000)] | 55 | - | 56 | - | 1.018 |
| sync mode 1 [T1 hblank, window spin(40000)] | 55 | - | 56 | - | 1.018 |
| no sync [T1 hblank, window spin(40000)] | 55 | - | 56 | - | 1.018 |
| sync mode 1 [T2 sysclock/8, window spin(4000)] | 1500 | - | 1500 | - | 1.000 |
| sync mode 2 [T2 sysclock/8, window spin(4000)] | 1500 | - | 1500 | - | 1.000 |
| no sync [T2 sysclock/8, window spin(4000)] | 1500 | - | 1500 | - | 1.000 |
| T0 reset@tgt [D counter after the target: reset@target (0x0058) vs free run (0x0050), target 0] at IRQ | 27 | - | 29 | - | 1.074 |
| T0 reset@tgt [D counter after the target: reset@target (0x0058) vs free run (0x0050), target 0] after spin | 451 | - | 450 | - | 0.998 |
| T0 free run [D counter after the target: reset@target (0x0058) vs free run (0x0050), target 0] at IRQ | 1053 | - | 1053 | - | 1.000 |
| T0 free run [D counter after the target: reset@target (0x0058) vs free run (0x0050), target 0] after spin | 1477 | - | 1475 | - | 0.999 |
| T1 reset@tgt [D counter after the target: reset@target (0x0058) vs free run (0x0050), target 0] at IRQ | 27 | - | 24 | - | 0.889 |
| T1 reset@tgt [D counter after the target: reset@target (0x0058) vs free run (0x0050), target 0] after spin | 451 | - | 448 | - | 0.993 |
| T1 free run [D counter after the target: reset@target (0x0058) vs free run (0x0050), target 0] at IRQ | 1053 | - | 1053 | - | 1.000 |
| T1 free run [D counter after the target: reset@target (0x0058) vs free run (0x0050), target 0] after spin | 1477 | - | 1473 | - | 0.997 |
| T2 reset@tgt [D counter after the target: reset@target (0x0058) vs free run (0x0050), target 0] at IRQ | 27 | - | 21 | - | 0.778 |
| T2 reset@tgt [D counter after the target: reset@target (0x0058) vs free run (0x0050), target 0] after spin | 451 | - | 445 | - | 0.987 |
| T2 free run [D counter after the target: reset@target (0x0058) vs free run (0x0050), target 0] at IRQ | 1053 | - | 1048 | - | 0.995 |
| T2 free run [D counter after the target: reset@target (0x0058) vs free run (0x0050), target 0] after spin | 1477 | - | 1469 | - | 0.995 |

### IRQ latency: I_STAT to handler

| Measurement | Console | 0.6.0 | 0.6.1 | 0.6.0 / console | 0.6.1 / console |
|---|---:|---:|---:|---:|---:|
| I_STAT poll, CPU IRQ off (reference) | 20 | - | 21 | - | 1.050 |
| cached / cached nop loop | 21 | - | 21 | - | 1.000 |
| cached / RAM load loop | 21 | - | 25 | - | 1.190 |
| cached / DIV + MFLO loop | 52 | - | 51 | - | 0.981 |
| cached / GTE nclip loop | 23 | - | 21 | - | 0.913 |
| cached / uncached nop loop | 25 | - | 27 | - | 1.080 |
| uncached handler, cached nop loop | 40 | 40 | 44 | 1.000 | 1.100 |
| uncached handler, RAM load loop | 45 | 45 | 42 | 1.000 | 0.933 |
| uncached handler, DIV + MFLO loop | 76 | - | 72 | 0.972 | 0.947 |
| uncached handler, GTE nclip loop | 40 | 38 | 42 | 0.950 | 1.050 |
| uncached handler, uncached nop loop | 44 | 44 | 49 | 1.000 | 1.114 |

### Peip  TimerCalib

| Measurement | Console | 0.6.0 | 0.6.1 | 0.6.0 / console | 0.6.1 / console |
|---|---:|---:|---:|---:|---:|
| 2 NOP KUSEG | 1 | 1 | 1 | 1.000 | 1.000 |
| 2 NOP KSEG1 | 1 | 1 | 1 | 1.000 | 1.000 |
| 3 NOP KSEG1 | 2 | 2 | 2 | 1.000 | 1.000 |
| 4 NOP KSEG1 | 3 | 3 | 3 | 1.000 | 1.000 |
| 9 NOP KSEG1 | 8 | 8 | 8 | 1.000 | 1.000 |
| 9 TMR2 DIV8 | 1 | 1 | 1 | 1.000 | 1.000 |
| 17 TMR2 DIV8 | 2 | 2 | 2 | 1.000 | 1.000 |
| NOP LOOP 9 | 8 | 8 | 8 | 1.000 | 1.000 |
| NOP LOOP 65536 | 65535 | 65535 | 65535 | 1.000 | 1.000 |
| NOP LOOP 65538 | 1 | 1 | 1 | 1.000 | 1.000 |

### Peip  TimerSet

| Measurement | Console | 0.6.0 | 0.6.1 | 0.6.0 / console | 0.6.1 / console |
|---|---:|---:|---:|---:|---:|
| 9 NOP KSEG1 | 8 | 8 | 8 | 1.000 | 1.000 |
| CURRENT 1 | 9 | 9 | 9 | 1.000 | 1.000 |
| CURRENT 3 | 11 | 11 | 11 | 1.000 | 1.000 |
| RESET CURRENT 1 | 9 | 9 | 9 | 1.000 | 1.000 |
| CURRENT 3 RESET 2 | 1 | 1 | 1 | 1.000 | 1.000 |
| TARGET 7 CURRENT 9 0 | 9 | 9 | 9 | 1.000 | 1.000 |
| TARGET 7 CURRENT 9 1 | 9 | 9 | 9 | 1.000 | 1.000 |
| TARGET 7 CURRENT 9 2 | 10 | 10 | 10 | 1.000 | 1.000 |
| TARGET 7 CURRENT 9 3 | 11 | 11 | 11 | 1.000 | 1.000 |
| TARGET 8 CURRENT 9 0 | 9 | 9 | 9 | 1.000 | 1.000 |
| TARGET 8 CURRENT 9 1 | 9 | 9 | 9 | 1.000 | 1.000 |
| TARGET 8 CURRENT 9 2 | 10 | 10 | 10 | 1.000 | 1.000 |
| TARGET 9 CURRENT 9 0 | 9 | 9 | 9 | 1.000 | 1.000 |
| TARGET 9 CURRENT 9 1 | 9 | 9 | 9 | 1.000 | 1.000 |
| TARGET 9 CURRENT 9 4 | 1 | 1 | 1 | 1.000 | 1.000 |

### Peip  TimerWrap

| Measurement | Console | 0.6.0 | 0.6.1 | 0.6.0 / console | 0.6.1 / console |
|---|---:|---:|---:|---:|---:|
| TIMERWRAP1 2 | 1 | 1 | 1 | 1.000 | 1.000 |
| TIMERWRAP1 5 | 1 | 1 | 1 | 1.000 | 1.000 |
| TIMERWRAP4 9 | 2 | 2 | 2 | 1.000 | 1.000 |
| TIMERWRAP5 9 | 1 | 1 | 1 | 1.000 | 1.000 |
| TIMERWRAP6 7 | 6 | 6 | 6 | 1.000 | 1.000 |
| TIMERWRAP7 8 | 7 | 7 | 7 | 1.000 | 1.000 |
| TIMERWRAP8 9 | 8 | 8 | 8 | 1.000 | 1.000 |

### ps1-tests  timers

| Measurement | Console | 0.6.0 | 0.6.1 | 0.6.0 / console | 0.6.1 / console |
|---|---:|---:|---:|---:|---:|
| Timer0, clock: System clock, 1000 cycles delay: | 1015 | - | 1015 | - | 1.000 |
| Timer0, clock: System clock, 5000 cycles delay: | 5015 | - | 5015 | - | 1.000 |
| Timer0, clock: System clock, frame delay: | 571286 | - | 571384 (566169) | - | 1.000 |
| Timer0, clock: Dot clock, 1000 cycles delay: | 198 | - | 199 (201) | - | 1.006 |
| Timer0, clock: Dot clock, 5000 cycles delay: | 983.50 | - | 983 (992) | - | 0.999 |
| Timer0, clock: Dot clock, frame delay: | 112029 | - | 112023 | - | 1.000 |
| Timer1, clock: System clock, 1000 cycles delay: | 1015 | - | 1015 | - | 1.000 |
| Timer1, clock: System clock, 5000 cycles delay: | 5015 | - | 5015 | - | 1.000 |
| Timer1, clock: System clock, frame delay: | 571286 | - | 571373 (566159) | - | 1.000 |
| Timer1, clock: Hblank, frame delay: | 263 | - | 263 | - | 1.000 |
| Timer2, clock: System clock, 1000 cycles delay: | 1015 | - | 1015 | - | 1.000 |
| Timer2, clock: System clock, 5000 cycles delay: | 5015 | - | 5015 | - | 1.000 |
| Timer2, clock: System clock, frame delay: | 571280 | - | 571387 (566173) | - | 1.000 |
| Timer2, clock: System clock/8, 1000 cycles delay: | 126.50 | - | 127 | - | 1.004 |
| Timer2, clock: System clock/8, 5000 cycles delay: | 626.50 | - | 627 | - | 1.001 |
| Timer2, clock: System clock/8, frame delay: | 71410 | - | 71417 (70766) | - | 1.000 |

*Disc 6196a5bd. Left out: 15 ambiguous Vsync240p sample order, 291 not a timing value or exact check, 18 unscored (summaries, histograms).*
