// Minimal headless libretro frontend: loads a core and a disc, plays a
// scripted input sequence, and writes periodic screenshots (PPM).
// Usage: frontend core.so system_dir save_dir game.cue script.txt outdir frames
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <stdint.h>
#include "libretro.h"

static const char *system_dir, *save_dir, *outdir;
static unsigned frame = 0, shot_every = 600;
static unsigned last_w, last_h; static size_t last_pitch; static const void *last_data;
static enum retro_pixel_format pixfmt = RETRO_PIXEL_FORMAT_0RGB1555;

// Script: lines "start end buttons" where buttons are letters
// U D L R a(cross) b(circle) x(triangle) y(square) s(start) e(select) l r
// Within a range the buttons are pressed for 4 frames out of every 16.
// "hold" suffix keeps them held for the whole range.
typedef struct { unsigned start, end; char buttons[32]; int hold; } step_t;
static step_t steps[4096]; static int nsteps;

static void log_cb(enum retro_log_level level, const char *fmt, ...) {
   if (level < RETRO_LOG_WARN) return;
   va_list ap; va_start(ap, fmt); vfprintf(stderr, fmt, ap); va_end(ap);
}

static bool env_cb(unsigned cmd, void *data) {
   switch (cmd) {
   case RETRO_ENVIRONMENT_GET_SYSTEM_DIRECTORY: *(const char **)data = system_dir; return true;
   case RETRO_ENVIRONMENT_GET_SAVE_DIRECTORY: *(const char **)data = save_dir; return true;
   case RETRO_ENVIRONMENT_GET_LOG_INTERFACE: ((struct retro_log_callback *)data)->log = log_cb; return true;
   case RETRO_ENVIRONMENT_SET_PIXEL_FORMAT: pixfmt = *(enum retro_pixel_format *)data; return true;
   case RETRO_ENVIRONMENT_GET_VARIABLE: {
      struct retro_variable *v = data;
      if (!strcmp(v->key, "beetle_psx_cpu_freq_scale") && getenv("PSX_CPU_SCALE")) { v->value = getenv("PSX_CPU_SCALE"); return true; }
      if (!strcmp(v->key, "beetle_psx_cpu_core")) { v->value = "interpreter"; return true; }
      if (!strcmp(v->key, "beetle_psx_renderer")) { v->value = "software"; return true; }
      if (!strcmp(v->key, "beetle_psx_use_mednafen_memcard0_method")) { v->value = "mednafen"; return true; }
      if (!strcmp(v->key, "beetle_psx_enable_memcard1")) { v->value = "enabled"; return true; }
      return false;
   }
   case RETRO_ENVIRONMENT_GET_CAN_DUPE: *(bool *)data = true; return true;
   case RETRO_ENVIRONMENT_GET_VARIABLE_UPDATE: *(bool *)data = false; return true;
   default: return false;
   }
}

static void save_ppm(const char *name) {
   if (!last_data) return;
   char path[1024]; snprintf(path, sizeof path, "%s/%s", outdir, name);
   FILE *f = fopen(path, "wb"); if (!f) return;
   fprintf(f, "P6\n%u %u\n255\n", last_w, last_h);
   for (unsigned y = 0; y < last_h; y++) {
      const uint8_t *row = (const uint8_t *)last_data + y * last_pitch;
      for (unsigned x = 0; x < last_w; x++) {
         uint8_t rgb[3];
         if (pixfmt == RETRO_PIXEL_FORMAT_XRGB8888) {
            uint32_t p = ((const uint32_t *)row)[x];
            rgb[0] = p >> 16; rgb[1] = p >> 8; rgb[2] = p;
         } else {
            uint16_t p = ((const uint16_t *)row)[x];
            if (pixfmt == RETRO_PIXEL_FORMAT_RGB565) { rgb[0] = (p >> 11) << 3; rgb[1] = ((p >> 5) & 63) << 2; rgb[2] = (p & 31) << 3; }
            else { rgb[0] = ((p >> 10) & 31) << 3; rgb[1] = ((p >> 5) & 31) << 3; rgb[2] = (p & 31) << 3; }
         }
         fwrite(rgb, 1, 3, f);
      }
   }
   fclose(f);
}

static void video_cb(const void *data, unsigned w, unsigned h, size_t pitch) {
   if (data) { last_data = data; last_w = w; last_h = h; last_pitch = pitch; }
}
static void audio_cb(int16_t l, int16_t r) { (void)l; (void)r; }
static size_t audio_batch_cb(const int16_t *d, size_t n) { (void)d; return n; }
static void poll_cb(void) {}

static int16_t input_cb(unsigned port, unsigned device, unsigned index, unsigned id) {
   if (port != 0 || device != RETRO_DEVICE_JOYPAD) return 0;
   for (int i = 0; i < nsteps; i++) {
      step_t *s = &steps[i];
      if (frame < s->start || frame >= s->end) continue;
      if (!s->hold && (frame % 16) >= 4) continue;
      for (const char *c = s->buttons; *c; c++) {
         unsigned want = 99;
         switch (*c) {
         case 'U': want = RETRO_DEVICE_ID_JOYPAD_UP; break; case 'D': want = RETRO_DEVICE_ID_JOYPAD_DOWN; break;
         case 'L': want = RETRO_DEVICE_ID_JOYPAD_LEFT; break; case 'R': want = RETRO_DEVICE_ID_JOYPAD_RIGHT; break;
         case 'a': want = RETRO_DEVICE_ID_JOYPAD_B; break;  // cross
         case 'b': want = RETRO_DEVICE_ID_JOYPAD_A; break;  // circle
         case 'x': want = RETRO_DEVICE_ID_JOYPAD_X; break;  // triangle
         case 'y': want = RETRO_DEVICE_ID_JOYPAD_Y; break;  // square
         case 's': want = RETRO_DEVICE_ID_JOYPAD_START; break; case 'e': want = RETRO_DEVICE_ID_JOYPAD_SELECT; break;
         case 'l': want = RETRO_DEVICE_ID_JOYPAD_L; break; case 'r': want = RETRO_DEVICE_ID_JOYPAD_R; break;
         }
         if (want == id) return 1;
      }
   }
   return 0;
}

#define SYM(name) do { *(void **)&p_##name = dlsym(core, #name); if (!p_##name) { fprintf(stderr, "missing %s\n", #name); return 1; } } while (0)

int main(int argc, char **argv) {
   if (argc < 8) { fprintf(stderr, "usage\n"); return 2; }
   system_dir = argv[2]; save_dir = argv[3]; outdir = argv[6];
   unsigned frames = atoi(argv[7]); if (argc > 8) shot_every = atoi(argv[8]);
   FILE *sf = fopen(argv[5], "r");
   if (sf) {
      char line[256];
      while (fgets(line, sizeof line, sf) && nsteps < 4096) {
         step_t *s = &steps[nsteps]; char hold[16] = "";
         if (line[0] == '#') continue;
         if (sscanf(line, "%u %u %31s %15s", &s->start, &s->end, s->buttons, hold) >= 3) { s->hold = !strcmp(hold, "hold"); nsteps++; }
      }
      fclose(sf);
   }
   void *core = dlopen(argv[1], RTLD_NOW); if (!core) { fprintf(stderr, "%s\n", dlerror()); return 1; }
   void (*p_retro_set_environment)(retro_environment_t); void (*p_retro_set_video_refresh)(retro_video_refresh_t);
   void (*p_retro_set_audio_sample)(retro_audio_sample_t); void (*p_retro_set_audio_sample_batch)(retro_audio_sample_batch_t);
   void (*p_retro_set_input_poll)(retro_input_poll_t); void (*p_retro_set_input_state)(retro_input_state_t);
   void (*p_retro_init)(void); bool (*p_retro_load_game)(const struct retro_game_info *); void (*p_retro_run)(void);
   void (*p_retro_deinit)(void); void (*p_retro_unload_game)(void);
   void (*p_retro_set_controller_port_device)(unsigned, unsigned);
   SYM(retro_set_environment); SYM(retro_set_video_refresh); SYM(retro_set_audio_sample); SYM(retro_set_audio_sample_batch);
   SYM(retro_set_input_poll); SYM(retro_set_input_state); SYM(retro_init); SYM(retro_load_game); SYM(retro_run);
   SYM(retro_deinit); SYM(retro_unload_game); SYM(retro_set_controller_port_device);
   p_retro_set_environment(env_cb); p_retro_init();
   p_retro_set_video_refresh(video_cb); p_retro_set_audio_sample(audio_cb); p_retro_set_audio_sample_batch(audio_batch_cb);
   p_retro_set_input_poll(poll_cb); p_retro_set_input_state(input_cb);
   struct retro_game_info info = { argv[4], NULL, 0, NULL };
   if (!p_retro_load_game(&info)) { fprintf(stderr, "load failed\n"); return 1; }
   p_retro_set_controller_port_device(0, RETRO_DEVICE_JOYPAD);
   for (frame = 0; frame < frames; frame++) {
      p_retro_run();
      if (frame % shot_every == 0) { char n[64]; snprintf(n, sizeof n, "f%06u.ppm", frame); save_ppm(n); }
   }
   save_ppm("final.ppm");
   p_retro_unload_game(); p_retro_deinit();
   return 0;
}
