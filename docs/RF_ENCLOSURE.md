# RF and Buried Enclosure Notes

Working note captured 2026-07-06. Revisit before committing to the final
lantern enclosure and production MCU order.

## Problem

The current physical concept puts the battery and control electronics in a box
buried roughly 6 inches below grade. That is a serious RF risk for ESP-NOW,
because ESP-NOW uses the ESP32's 2.4 GHz Wi-Fi radio.

Expected failure modes:

- Soil attenuates 2.4 GHz strongly; damp soil is much worse than dry soil.
- A below-grade ESP32 antenna will be detuned by the surrounding dirt and box.
- Any metal battery box, foil insulation, metal lid, or dense wiring near the
  antenna can make the link much worse.
- Ground-level antennas are already disadvantaged; below-ground antennas are the
  hard case.

The firmware can free-run through missed beacons, so occasional packet loss is
acceptable. But if buried performers only hear the conductor rarely or not at
all, sync relock, roster/register traffic, layout-table delivery, power
telemetry, pattern changes, and OTA all become unreliable.

Treat **antenna above dirt** as a physical design constraint unless range testing
proves otherwise.

## Existing FireBeetle Order

The pilot order in `receipts/2026-07-03 - ESP32s - DFRobot.pdf` is:

- 6x **FireBeetle 2 ESP32-E IoT Microcontroller with Header**
- Model **DFR0654-F**

This matches the BOM's **FireBeetle 2 ESP32-E (DFR0654)** line. These boards are
still useful for firmware, power, UI/control-plane, and above-ground prototypes,
but they are the onboard-PCB-antenna version. They are not the right production
choice for a buried box with a remote antenna.

Do not plan on attaching a 3 ft antenna cable to DFR0654/DFR0654-F boards. That
would require RF rework/hacking the antenna path and is not a good field-unit
strategy.

## Hardware Direction

For buried electronics, use an external-antenna ESP32 board/module:

- Preferred pilot candidate: **DFRobot FireBeetle 2 ESP32-UE**, SKU **DFR1140**
- Module family: **ESP32-WROOM-32UE** / external-antenna variant
- Antenna connector: u.FL / IPEX MHF1

The intended RF stack for a lantern with roughly a 3 ft antenna run is:

```text
ESP32-UE u.FL
  -> short protected u.FL-to-SMA bulkhead pigtail inside buried box, 10-20 cm
  -> sealed box bulkhead
  -> 3 ft 50-ohm coax extension
  -> 2.4 GHz antenna above grade, hidden in/near lantern structure
```

Do not run 3 ft of tiny u.FL cable directly from the ESP32. Use u.FL only as the
short internal jumper, then transition to sturdier 50-ohm coax for the long run.

## Pilot Buy List

For 3-5 test lanterns, buy:

- 3-5x **FireBeetle 2 ESP32-UE / DFR1140**
- Short **u.FL / IPEX MHF1 to SMA or RP-SMA bulkhead** pigtails, 10-20 cm
- 3 ft **50-ohm coax extensions**:
  - preferred: LMR-100 / LMR-100A / CFD100
  - acceptable: RG316
  - avoid for the full 3 ft run if possible: RG178 and generic ultra-thin u.FL
    antenna leads
- 2.4 GHz outdoor omni antennas:
  - Wi-Fi/Bluetooth band, not 915 MHz, LoRa, cellular, or GPS
  - roughly 2-5 dBi
  - vertical whip, puck, or sealed outdoor antenna
- Waterproofing and mechanical parts:
  - IP67 cable gland or sealed RF bulkhead
  - strain relief inside the buried box
  - silicone boots, self-fusing tape, or equivalent weather sealing for exposed
    RF joints
  - labels for antenna cable/connector orientation

Connector warning: deliberately choose **SMA vs. RP-SMA** and keep the pigtail,
extension cable, and antenna consistent. Avoid building a chain of adapters.

## Test Plan

Before buying production quantities:

1. Build one buried-box prototype with the exact planned box, battery, buck,
   wiring bundle, and lid.
2. Put the antenna above grade in the likely final lantern location.
3. Bury the box to the planned depth and close it normally.
4. Range-test against the conductor using the existing sync diagnostics:
   `LOCKED`, last-beacon age, missed windows, roster stability, and table/OTA
   behavior if practical.
5. Repeat with the conductor elevated, since the field design already prefers a
   centrally placed elevated conductor.
6. If possible, repeat after soil moisture changes. Wet playa/soil may be much
   worse than a dry bench-yard test.

The production decision should be based on the buried-box geometry, not an
open-air bench test.
