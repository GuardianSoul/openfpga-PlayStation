// SPDX-License-Identifier: GPL-2.0-or-later
/* Drives Beetle PSX mdec.c (e57860d8, unmodified, included below) with a
   run_mdec_reference vector file (scripts/run_mdec_beetle.py). Each output
   word is read the way dma.c reads it and printed as "RAM-word-index hex". */
#include <stdio.h>
#include <stdlib.h>
int32_t EventCycles = 1 << 30;
void DMA_NoteActivity(void) {}
bool PSX_AnyEventVirtual(void) { return false; }
void PSX_WakeVirtual(bool x) { (void)x; }
#include "mdec.c"
int MDFNSS_StateAction(void *st, int load, int data_only, SFORMAT *sf, const char *name) { return 1; }
static unsigned long outn;
static void drain(void) {
   for (int guard = 0; guard < 100000; guard++) {
      while (OutFIFO.in_count) { uint32_t offs, v = MDEC_DMARead(&offs);   /* dma.c stores it at CurAddr + offs */
         printf("%ld %08x\n", (long)outn + (int32_t)offs, v); outn++; }
      MDEC_Run(1 << 20);
      if (!OutFIFO.in_count && MDEC_IsBlocked()) return;
   }
   fprintf(stderr, "drain did not settle\n"); exit(2);
}
int main(int argc, char **argv) {
   FILE *f = argc == 2 ? fopen(argv[1], "r") : NULL; char kind; unsigned a, v;
   if (!f) { fprintf(stderr, "usage: harness VECTORS\n"); return 2; }
   MDEC_Power();
   while (fscanf(f, " %c %x %x", &kind, &a, &v) == 3) {
      if (kind == 'B' && a == 4) MDEC_Write(0, 4, v);
      else if (kind == 'B' || kind == 'D') {
         drain();
         if (!FastFIFO_CanWrite(&InFIFO)) { fprintf(stderr, "input FIFO full\n"); return 2; }
         MDEC_Write(0, 0, v);
      } else if (kind == 'R') drain();
   }
   drain();
   fprintf(stderr, "%lu words\n", outn);
   return 0;
}
