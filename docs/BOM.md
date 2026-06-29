# Production Bill of Materials — *Do Baskets Dream*

Parts list + cost model for the production field. Companion to
[`PROJECT_BRIEF.md`](PROJECT_BRIEF.md) (the hardware table / constraints) and the
`power-budget-go-no-go` measurements that justify the battery sizing.

**Scope:** 50 deployed lanterns + 5 spares (**55 nodes**) + 1 conductor + 1 admin host.
**Status:** first costed pass (2026-06-28). Decisions locked except the enclosure
(art-side) and the level-shifter (recommended-optional). Verify every price at
checkout — see the price caveat at the bottom.

---

## Locked decisions feeding this BOM

- **MCU → FireBeetle 2 ESP32-E** (not the DevKitC). Lower quiescent draw shrinks the
  CPU floor that is now the dominant night-power term; firmware already has a
  `firebeetle` build env. DevKitCs stay as bench boards.
- **LED → SK6812 **RGBW** warm-white** (Adafruit ring). RGBW (4-channel) is required —
  the GLOW scene and the entire measured power budget depend on the white channel.
- **Power → per-node 12 V LiFePO4 → buck → 5 V. Batteries, no solar.** Daytime
  deep-sleep (firmware Lever 2) is the calendar-life lever, not the battery.
- **Battery → TalentCell LF120A1, 153.6 Wh.** At the measured ~11.7 Wh/night that's
  **~13 nights night-only** — clears the 10-night target with margin.
- **Admin host → Raspberry Pi Zero 2 W, headless.** Calibration CV runs on a laptop,
  not the field box, so no beefy Pi needed.

---

## Per-node BOM (performer)

Prices are recent-market estimates (Amazon strips live prices from automated
fetches; confirm at checkout). "Ext." = extended cost for the part actually consumed
per node (multipack unit price).

| # | Part | Selected product | Source / link | Unit est. | Per-node |
|---|---|---|---|---|---|
| 1 | MCU | FireBeetle 2 ESP32-E (DFR0654) | **DFRobot direct** ([product-2195](https://www.dfrobot.com/product-2195.html)) — $8.30 @10+ | $8.30 | $8.30 |
| 2 | LED ring | NeoPixel Ring 16× SK6812 **RGBW Warm White ~3000K** (#2854) | **Adafruit direct** ([2854](https://www.adafruit.com/product/2854)) — $10.76 @10–99, $9.56 @100+ | $10.76 | $10.76 |
| 3 | Buck 12→5 V | UCTRONICS 9–36 V→5 V 5 A, **fixed 5 V**, screw terminals (2-pack) | Amazon [B07XXWQ49N](https://www.amazon.com/dp/B07XXWQ49N) — ~$13/2pk | $6.50 | $6.50 |
| 4 | **Battery** | TalentCell **LF120A1** 12.8 V 12 Ah **153.6 Wh** LiFePO4 | Amazon [B07JF56C7L](https://www.amazon.com/dp/B07JF56C7L) — ~$44 +tax | $48.00 | $48.00 |
| 5 | Level shifter | 74AHCT125 DIP-14 (3.3→5 V data) — *recommended-optional* | Amazon 10-pk [B08GJF43N3](https://www.amazon.com/dp/B08GJF43N3) | ~$1.00 | $1.00 |
| 6 | Resistors | 470 Ω data + 47 k/10 k divider (from assortment) | Amazon kit [B07L851T3V](https://www.amazon.com/dp/B07L851T3V) — ~$14/1280pc | — | $0.20 |
| 7 | Bulk cap | 1000 µF 16 V radial across ring 5 V/GND | Amazon 10-pk [B07FYSD8TD](https://www.amazon.com/dp/B07FYSD8TD) | $0.70 | $0.70 |
| 8 | Dusk sensor | PT334-6C 5 mm phototransistor (analog, ADC1/GPIO34) | Amazon 20-pk [B00M1PMHO4](https://www.amazon.com/dp/B00M1PMHO4) | $0.35 | $0.35 |
| 9 | Carrier PCB | simple 2-layer node board (consolidates 5–8 + dividers) | JLCPCB @ qty | ~$2.00 | $2.00 |
| 10 | Connectors | JST-SM pre-crimped pigtails (2-pin batt, 3-pin ring) | Amazon kit [B09SX68S6Y](https://www.amazon.com/dp/B09SX68S6Y) | — | $1.00 |
| 11 | Fuse | Inline ATC blade holder + fuse (12 V line) | Amazon kit [B07FQCBSJ5](https://www.amazon.com/dp/B07FQCBSJ5) — 10 holders +50 fuses | $1.60 | $1.60 |
| 12 | Switch | SPST mini toggle on/off | Amazon 8-pk [B09WQKTN2L](https://www.amazon.com/dp/B09WQKTN2L) | $1.10 | $1.10 |
| 13 | Misc | hookup wire, heatshrink, standoffs | — | — | ~$2.00 |
| 14 | Enclosure | basket / lantern + diffuser | **art-side, TBD** | TBD | TBD |

**Per-node electronics (excl. battery & enclosure): ≈ $35.51**
**Per-node with battery (excl. enclosure): ≈ $83.51**

The battery is ~57% of the costed per-node total. The next-largest levers are the
LED ring and the buck (the fixed-vs-adjustable choice, below).

---

## Shared infrastructure (one-time)

| Part | Selected product | Source / link | Est. |
|---|---|---|---|
| Conductor board | Lonely Binary ESP32-**WROOM-32UE**, external 2.4 GHz antenna **included** — max-range broadcast | Amazon [B0GR4Q23JV](https://www.amazon.com/dp/B0GR4Q23JV) | ~$15 |
| Conductor power | 1× TalentCell LF120A1 + UCTRONICS buck (or wall power — it beacons 4 Hz continuously, exempt from radio duty-cycle) | — | ~$55 |
| Admin host | Raspberry Pi **Zero 2 W**, headless (AP + web UI + USB-serial bridge) | kit [B0D5CYJ7V3](https://www.amazon.com/dp/B0D5CYJ7V3) + SD + 5 V PSU + micro-USB-OTG→USB-A | ~$45 all-in |
| Calibration | drone for the fly-over survey; CV runs on a laptop | rent/borrow — out of BOM | — |

---

## Field roll-up (50 lanterns + 5 spares = 55 nodes, + conductor + admin host)

| Bucket | Qty | Est. total |
|---|---|---|
| Node electronics | 55 builds | ~$1,953 |
| Batteries | 55 | ~$2,640 |
| Conductor (board + power) | 1 | ~$70 |
| Admin host (Pi Zero 2 W) | 1 | ~$45 |
| **Subtotal — electronics + batteries** | | **≈ $4,710** |
| Enclosures / diffusers | 55 | **TBD (art-side)** |

Enclosures are the remaining unknown and could rival the battery line depending on
how the baskets are built — owned on the art side, left as a placeholder here.

---

## Sourcing notes & open tradeoffs

- **FireBeetle is off-Amazon.** No dependable Amazon US listing — buy **direct from
  DFRobot** (~$8.30/board at 10+). Amazon fallback if you must stay on one cart: a
  plain ESP32 WROOM-32 DevKit 3-pack (~$5–6/board), but you lose the lower quiescent
  draw that made the FireBeetle the production pick.
- **LED & level-shifter are cheaper manufacturer-direct** than Amazon resellers
  (Adafruit $10.76 vs reseller markup; DFRobot/Adafruit beat third-party ASINs).
- **Buck — fixed vs. adjustable:** the UCTRONICS is genuinely fixed 5 V with screw
  terminals → **no per-unit calibration**. Cheap LM2596/MP1584 multipacks are
  ~$1/unit but each of 60 needs its trimpot set to 5.0 V and metered, with a
  knocked-off-voltage risk in the field. Recommendation: pay ~$300 more for the
  UCTRONICS and skip 60 calibrations.
- **Wrong-part traps to avoid:** Adafruit [B00KBXT9I0](https://www.amazon.com/dp/B00KBXT9I0)
  / #1463 is the **RGB** ring (no white channel); most Amazon "16-LED rings" are
  WS2812B RGB. Confirm "SK6812 **RGBW**" + a stated white temperature.
- **Pi Zero 2 W stock** is intermittently constrained (packaging, not the RAM
  shortage). Watch rpilocator.com; the Pi 3 B+ is the in-stock fallback (and adds
  real USB-A ports, skipping the OTG adapter) at ~$40–55.

## Open items before ordering

1. **Enclosure / diffuser** approach + per-unit cost (art-side).
2. **Dust / weatherproofing** for BRC — sealed enclosure, gasketed battery door,
   conformal-coat on boards. Not yet costed; belongs here once the enclosure is known.
3. **Level shifter** — confirm whether to populate it on every node (reliability) or
   omit (the bench rig ran direct 3.3 V data through the 470 Ω resistor and worked).
4. **Carrier PCB** — design + fab the node board so 6–13 consolidate onto it; this
   is what makes a 60-unit build assemblable.

## Price caveat

Amazon renders prices via JavaScript and blocks automated price reads, so the dollar
figures above are recent typical-market estimates, **not** live quotes. Every ASIN,
title, and `/dp/` link was verified to resolve to a real listing. The two hard
anchors are the battery (~$44 +tax, from the listing the user pulled) and the
manufacturer-direct LED/FireBeetle quantity pricing. Confirm all totals at checkout.
