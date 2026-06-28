// Do Baskets Dream — node firmware.
//
// Milestone 1: prove sync. One conductor broadcasts a clock beacon over ESP-NOW;
// performers lock to it and render a synchronized slow pulse. A performer that
// misses a beacon keeps free-running on its last known offset and re-locks on the
// next one — a dropped packet causes at most slight drift, never a blackout.
//
// Role is chosen at build time via NODE_ROLE (see platformio.ini).

#include <Arduino.h>
#include <NeoPixelBus.h>
#include <WiFi.h>
#include <esp_now.h>
#include <esp_wifi.h>
#include <esp_timer.h>

#include "config.h"
#include "beacon.h"
#include "patterns.h"

// 16x SK6812 RGBW ring. RMT channel 0 with SK6812 timing.
NeoPixelBus<NeoGrbwFeature, NeoEsp32Rmt0Sk6812Method> strip(LED_COUNT, LED_PIN);

static const uint8_t BROADCAST_ADDR[6] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};

// ---- Node identity (Milestone 2 reads real (x,y) from NVS) -------------------
static float g_x = 0.0f;
static float g_y = 0.0f;

// ---- Sync state --------------------------------------------------------------
// offset maps local clock -> conductor clock:  synced = esp_timer_get_time() + offset.
// volatile because the most recent values are written from the ESP-NOW recv ISR
// context and read from loop().
static volatile int64_t  g_offset_us      = 0;       // conductor - local
static volatile int64_t  g_last_beacon_us = 0;       // local time of last beacon
static volatile bool     g_locked         = false;   // have we ever locked?
static volatile uint32_t g_last_seq       = 0;
static volatile uint32_t g_beacons_rx     = 0;
static volatile uint32_t g_seq_gaps       = 0;       // missed/out-of-order beacons

// Latest beacon contents (what to render). Copied out of the callback quickly.
static volatile Beacon  g_beacon = {
    BEACON_MAGIC, 0, patterns::PULSE, /*brightness*/ 96, /*palette*/ 0,
    {0, 0, 0, 0}, 0};

static inline int64_t now_us() { return esp_timer_get_time(); }

// Synced clock: conductor renders against its own clock (offset 0); a performer
// adds its locked offset. Free-runs naturally when no beacon arrives — we just
// keep using the last offset.
static inline int64_t synced_us() {
#if IS_CONDUCTOR
  return now_us();
#else
  return now_us() + g_offset_us;
#endif
}

// ---- ESP-NOW receive (performer) ---------------------------------------------
// The recv callback signature changed in Arduino-ESP32 3.x. Support both.
#if !IS_CONDUCTOR
#if ESP_ARDUINO_VERSION >= ESP_ARDUINO_VERSION_VAL(3, 0, 0)
void onBeacon(const esp_now_recv_info_t* info, const uint8_t* data, int len) {
#else
void onBeacon(const uint8_t* mac, const uint8_t* data, int len) {
#endif
  if (len != sizeof(Beacon)) return;
  Beacon b;
  memcpy(&b, data, sizeof(b));
  if (b.magic != BEACON_MAGIC) return;

  int64_t local = now_us();
  // Offset such that local + offset == conductor's epoch at receive. Transmission
  // delay is sub-millisecond and ignored — fine for slow visual patterns.
  g_offset_us = b.epoch_us - local;
  g_last_beacon_us = local;

  // Drop detection: any jump other than +1 in the sequence counts as a gap.
  if (g_locked && b.seq != g_last_seq + 1) g_seq_gaps++;
  g_last_seq = b.seq;
  g_beacons_rx++;
  g_locked = true;

  memcpy((void*)&g_beacon, &b, sizeof(b));
}
#endif

// ---- Radio setup -------------------------------------------------------------
static void radioBegin() {
  WiFi.mode(WIFI_STA);
  WiFi.disconnect();  // we never join an AP; just need the STA interface up

  // Pin the channel explicitly so every node agrees without scanning.
  esp_wifi_set_promiscuous(true);
  esp_wifi_set_channel(WIFI_CHANNEL, WIFI_SECOND_CHAN_NONE);
  esp_wifi_set_promiscuous(false);

  // Modem-sleep is a battery-budget requirement (brief: don't-break list). It is
  // the default in STA mode; set it explicitly so it can't silently regress.
  esp_wifi_set_ps(WIFI_PS_MIN_MODEM);

  if (esp_now_init() != ESP_OK) {
    Serial.println("ESP-NOW init failed");
    return;
  }

  esp_now_peer_info_t peer = {};
  memcpy(peer.peer_addr, BROADCAST_ADDR, 6);
  peer.channel = WIFI_CHANNEL;
  peer.encrypt = false;
  esp_now_add_peer(&peer);

#if !IS_CONDUCTOR
  esp_now_register_recv_cb(onBeacon);
#endif
}

#if IS_CONDUCTOR
static void broadcastBeacon() {
  Beacon b;
  memcpy(&b, (const void*)&g_beacon, sizeof(b));
  b.magic = BEACON_MAGIC;
  b.epoch_us = now_us();
  b.seq = g_last_seq++;
  esp_now_send(BROADCAST_ADDR, (const uint8_t*)&b, sizeof(b));
}
#endif

// ---- Diagnostics -------------------------------------------------------------
// Prints a one-line sync status so boards can be range-walked to find where sync
// drops (brief, Milestone 1).
static void printDiag() {
#if IS_CONDUCTOR
  Serial.printf("[conductor] t=%lld us  seq=%lu  pat=%u  bri=%u\n",
                (long long)now_us(), (unsigned long)g_last_seq,
                g_beacon.pattern_id, g_beacon.brightness);
#else
  int64_t age = now_us() - g_last_beacon_us;
  bool stale = !g_locked || age > BEACON_STALE_US;
  Serial.printf(
      "[performer] %s  offset=%lld us  last_beacon=%lld ms ago  rx=%lu  gaps=%lu  seq=%lu\n",
      stale ? "FREE-RUN" : "LOCKED  ", (long long)g_offset_us,
      (long long)(g_locked ? age / 1000 : -1), (unsigned long)g_beacons_rx,
      (unsigned long)g_seq_gaps, (unsigned long)g_last_seq);
#endif
}

// ---- Arduino entry points ----------------------------------------------------
void setup() {
  setCpuFrequencyMhz(CPU_FREQ_MHZ);
  Serial.begin(115200);
  delay(200);
  Serial.printf("\nDo Baskets Dream — role: %s  channel: %u\n",
                IS_CONDUCTOR ? "CONDUCTOR" : "PERFORMER", WIFI_CHANNEL);

  strip.Begin();
  strip.Show();  // clear

  radioBegin();
}

void loop() {
  int64_t t = now_us();

#if IS_CONDUCTOR
  static int64_t next_beacon = 0;
  if (t >= next_beacon) {
    broadcastBeacon();
    next_beacon = t + BEACON_INTERVAL_US;
  }
#endif

  // Render against synced time. Snapshot the beacon to avoid a torn read mid-frame.
  Beacon b;
  memcpy(&b, (const void*)&g_beacon, sizeof(b));
  patterns::render(strip, b, synced_us(), g_x, g_y);
  strip.Show();

  static int64_t next_diag = 0;
  if (t >= next_diag) {
    printDiag();
    next_diag = t + DIAG_INTERVAL_US;
  }

  delay(16);  // ~60 fps render cap; keeps the CPU mostly idle for modem-sleep
}
