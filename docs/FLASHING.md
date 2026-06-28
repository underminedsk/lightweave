# Flashing & Bring-up Runbook

Everything we learned getting the first three boards flashed and synced. Read
this before flashing a new board — most of the pain came from non-obvious
gotchas, all captured here.

## TL;DR — flashing a brand-new board

```bash
export PATH="/opt/homebrew/bin:$PATH"

# 1. Find the port
pio device list                      # look for the CP2102 / usbserial entry

# 2. ONE-TIME full erase (new boards ship with factory firmware — see below)
~/.platformio/packages/tool-esptoolpy/esptool.py --port /dev/cu.usbserial-XXXX erase_flash

# 3. Flash the one firmware image (role is set at runtime, not at build)
pio run -e devkitc -t upload --upload-port /dev/cu.usbserial-XXXX

# 4. Provision + watch it run
pio device monitor -p /dev/cu.usbserial-XXXX -b 115200   # Ctrl-A then K to quit
#   then type:  role performer   (or 'role conductor' for the one conductor)
#               id 1 / pos 0 0   as needed
```

Every node runs the **same image**; role lives in NVS (default performer) and is
set over serial with `role conductor|performer`. A healthy performer prints
`LOCKED ... gaps=0` within ~1 s; a conductor prints `[conductor] ... seq=` climbing
once per second.

---

## The gotchas (in the order they bit us)

### 1. New boards ship with factory ESP-AT firmware — erase first
Our DOIT ESP32 DevKit V1 boards came preloaded with **ESP-AT firmware**
(you'll see `at_customize` / `ota_0` / `ota_1` partitions and
`Bin version(Wroom32)` in the boot log). A normal `upload` writes our app but
leaves leftover flash state that **scrambles the clock**, with these symptoms:

- Serial is **garbage at 115200** but **clean at 74880 baud**
- The GPIO2 heartbeat **won't blink** (or blinks at the wrong rate)
- Sync is flaky / `gaps` piles up

**Fix:** a one-time full chip erase before flashing:
```bash
~/.platformio/packages/tool-esptoolpy/esptool.py --port /dev/cu.usbserial-XXXX erase_flash
```
Then flash normally. This is a per-board, one-time step.

> Note: the boards are genuinely identical hardware — confirmed
> `Crystal is 40MHz`, ESP32-D0WD-V3, via
> `esptool.py --port <PORT> flash_id`. The 74880 baud was leftover flash, NOT a
> 26 MHz crystal. If a board ever acts "wrong," `erase_flash` first.

### 2. All boards enumerate as the same serial — names shuffle on replug
Every board reports USB serial `0001` (`VID:PID=10C4:EA60 SER=0001`), so macOS
can't give them stable names. When two are plugged in you'll see e.g.
`/dev/cu.usbserial-0001` and `/dev/cu.usbserial-7`, and **which physical board
is which name changes every time you replug.**

Consequences:
- **Flash deterministically by the current port**, not by which board you think
  it is. Read the port first (`pio device monitor` or the snippet below) to see
  its current role, then flash.
- When flashing several boards, do them **one at a time** and **label each with
  tape** (C, P1, P2) right after flashing.
- A successful "swap" isn't guaranteed — if a board didn't actually get
  reconnected, you can flash the same one twice and leave another untouched
  (this is how we ended up with a board still on factory firmware).

### 3. Exactly ONE conductor
Role is a runtime NVS value (set over serial with `role conductor|performer`),
not a build flag — every node runs the same image. There must be **only one
conductor** powered at a time.

Symptom of two conductors: a performer shows **`gaps ≈ rx`** (almost every
beacon counted as a gap) and its `offset`/`seq` jump between two very different
values — it's ping-ponging between two clocks. Fix: power down the extra
conductor. (With one conductor, a locked performer holds `gaps=0` and a steady
offset.)

### 4. Use a DATA USB cable, install the driver
- Charge-only cables won't enumerate a port — if `pio device list` shows nothing
  new, suspect the cable first.
- These boards use a **CP2102** USB-UART chip. If no port appears, install the
  CP2102 (Silicon Labs) driver. (CH340 on some other boards.)

---

## Reading serial without resetting the board

`pio device monitor` is fine for watching, but **opening the port toggles
DTR/RTS and resets the ESP32**. To peek without resetting (e.g. to check a
running board's role/offset), deassert the control lines after opening. The
platformio-bundled Python has `pyserial`:

```bash
/opt/homebrew/Cellar/platformio/*/libexec/bin/python - <<'EOF'
import time, serial
s = serial.Serial('/dev/cu.usbserial-XXXX', 115200, timeout=1)
time.sleep(0.4); s.setDTR(False); s.setRTS(False)   # don't reset
for _ in range(8):
    print(s.readline().decode(errors='replace').strip())
s.close()
EOF
```

## Reading the diagnostics

**Conductor:**
```
[conductor] t=1338048 us  seq=4  pat=0  bri=48
```
`seq` climbing ~4/sec = beaconing normally.

**Performer:**
```
[performer] LOCKED  offset=6803499 us  last_beacon=83 ms ago  rx=16  gaps=0  seq=42
```
| Field | Healthy | Trouble |
|---|---|---|
| state | `LOCKED` within ~1 s | never locks → no conductor / wrong channel / needs erase |
| `offset` | stable to ~±100 µs | jumping between two values → two conductors |
| `last_beacon` | < 250 ms | climbing → not receiving |
| `rx` | climbing ~4/sec | `0` → no beacons heard |
| `gaps` | `0` (or rare) | `≈ rx` → two conductors |

`FREE-RUN` with `offset=0, rx=0` just means no conductor is in range — the node
keeps rendering off its local clock (this is the intended no-blackout behavior),
and a flashed board still blinks its GPIO2 LED.

---

## Build environments

| Env | Target |
|---|---|
| `devkitc` | DOIT ESP32 DevKit V1 (`esp32dev`) |
| `firebeetle` | FireBeetle 2 ESP32-E |
| `native` | host unit tests (`pio test -e native`) |

One image per board; **role is set at runtime** over serial (`role
conductor|performer`, default performer). Multi-board setup = flash everywhere,
then set exactly one node to `role conductor`.
