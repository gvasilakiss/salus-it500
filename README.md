# Salus iT500 for Home Assistant

[![Quality checks](https://github.com/gvasilakiss/salus/actions/workflows/ci.yml/badge.svg)](https://github.com/gvasilakiss/salus/actions/workflows/ci.yml)

A custom integration for the Salus iT500 internet thermostat (and the RT310i /
RT510 / XT500 heads that pair with the same wiring centre).

Salus has never published an API for the iT500. This integration uses the same
undocumented cloud backend the official mobile app talks to (Arrayent/"App
API"), and can fall back to the consumer web portal (`salus-it500.com`) if
that backend is unreachable. Internally, everything is Celsius; multi-zone
(CH1, CH2, hot water) is detected from the device's own attributes rather than
assumed.

---

## What you get

| Platform | Entities |
|---|---|
| `climate` | One thermostat per wired heating zone (CH1, and CH2 if present). Off / Follow schedule / Manual hold, current room temperature, setpoint, live boiler-call state, and (App API) boost as a preset. |
| `water_heater` | Hot water channel, only created if your system actually has one: Follow schedule / Once / Always on / Always off. |
| `sensor` | Room temperature, setpoint, scheduled temperature now, boost countdown, frost setpoint, heating mode, today's/weekly schedule, and diagnostics (backend, system type, firmware, last update, last update duration, last error). |
| `binary_sensor` | Heating relay, hot water relay, frost protection active, holiday mode, boost active, thermostat battery, connectivity. |
| `switch` | Hot water override (always on/off), holiday mode. |
| `select` | Heating mode (all four Salus modes, not just the three HVAC ones), hot water mode. |
| `number` | Frost protection temperature, temperature calibration, switching differential. |
| `button` | Boost 1h / 2h / 3h + cancel per zone, advance, skip next scheduled change, cancel override. |

Entities appear only if your system and connection method actually support
them - a CH1-only system gets no hot water or CH2 entities; the website
backend hides schedules, boost, calibration, differential and CH2 rather than
showing dead controls. See [Capability matrix](#capability-matrix).

### Services

| Service | Notes |
|---|---|
| `salus_it500.set_schedule` | Write up to six switching points to one or more days. Validates and reads back after writing. |
| `salus_it500.get_schedule` | Returns the decoded weekly program (a response service). |
| `salus_it500.boost` / `cancel_boost` | Timed override that reverts on its own. |
| `salus_it500.advance` | Jump a heating zone to its next scheduled temperature immediately. |
| `salus_it500.skip` | Skip a heating zone's next scheduled change, applying the one after it instead. |
| `salus_it500.set_holiday` / `clear_holiday` | Drop every zone to frost protection. |
| `salus_it500.refresh` | Force an immediate, un-debounced refresh from Salus. |

Only services the active backend/device actually supports are usable; others
raise a clear validation error rather than silently doing nothing.

---

## Installation

### HACS (custom repository)

1. HACS → Integrations → three-dot menu → **Custom repositories**
2. Add `https://github.com/gvasilakiss/salus`, category **Integration**
3. Install, then restart Home Assistant

### Manual

Copy `custom_components/salus_it500/` from this repository into your Home
Assistant `config/custom_components/` directory so you end up with:

```
config/
└── custom_components/
    └── salus_it500/
        ├── __init__.py
        ├── api/
        └── ...
```

Restart Home Assistant.

### Setup (config flow, no YAML)

**Settings → Devices & Services → Add Integration → Salus iT500**

1. Enter the email address and password you use for the iT500 app or
   `salus-it500.com`, and pick a connection method (Automatic is fine for
   almost everyone).
2. The integration signs in and, where the backend supports it, lists the
   devices on your account by name - pick yours from the dropdown.
3. If nothing was found (typical for the website backend), or you'd rather not
   wait for discovery, type the device ID (or paste the whole control-page
   URL) manually instead:

   ```
   https://salus-it500.com/public/control.php?devId=33591764
                                                     ^^^^^^^^
   ```

---

## Options

**Settings → Devices & Services → Salus iT500 → Configure**

- **Polling interval** — default 120 s, minimum 60 s.
- **Connection method** — Automatic (recommended), App API only, or Website only.

> **On polling:** Salus throttles hard and has historically IP-blocked people
> who hammered the web endpoint. There is no push or webhook here, so polling is
> the only option — but don't drop below 60 s without a reason. A user action
> (changing the temperature, pressing boost, etc.) triggers its own short,
> debounced refresh without touching this interval.

---

## The two connection methods

### App API (preferred)

`sal-emea-p01-api.arrayent.com` — the Arrayent IoT platform Salus built the
iT500 service on. This is what your phone talks to.

- Login posts the username and an MD5 digest of the password, and gets back a
  `userId` and `securityToken`. Tokens are cached and refreshed automatically;
  a rejected token forces exactly one re-login, never an unbounded retry loop.
- Everything after that hits `/zdk/services/zamapi/<method>` with that token and
  answers in XML.
- `getDeviceAttributesWithValues` returns the complete attribute map;
  `setMultiDeviceAttributes2` writes up to three attributes per call - writes
  that need more than three attributes are chunked automatically.

Gives you everything: both heating zones, hot water, schedules, boost,
advance/skip, calibration, switching differential, holiday mode.

### Website (fallback)

`salus-it500.com` — form login, scrape a hidden CSRF token out of the control
page, poll `ajax_device_values.php`, write through `includes/set.php`.

Only ever tested/enabled for what the portal's own JSON actually exposes: zone
1 temperature, on/off, hot water on/off and the frost setpoint. No schedules,
no boost, no calibration, no switching differential, no second zone - these
are not silently faked; the entities simply aren't created in this mode.

On **Automatic**, the API is tried first and the website is used only if the
API is unreachable (network/service failure). A rejected password does *not*
trigger a fallback - both routes use the same credentials, so there'd be no
point, and Automatic recovers back to the App API on its own once it's
reachable again. The active backend is visible as `sensor.<device>_backend`.

---

## Capability matrix

| Feature | App API | Website |
|---|---|---|
| Room temperature | Yes | Yes |
| Setpoint | Yes | Yes |
| Heating relay state | Yes | Yes |
| Heating mode (4-way) | Yes | Partial (on/off only) |
| Hot water state/on-off | Yes | Yes (if wired) |
| Frost protection | Yes | Yes |
| Schedule read/write | Yes | No |
| Boost / advance / skip | Yes | No |
| Temperature calibration | Yes | No |
| Switching differential | Yes | No |
| Holiday mode | Yes | No |
| CH2 (second zone) | Yes | No |
| Battery status | If reported by the device | If reported by the device |

"No" means the entity/service is not created rather than shown non-functional.

---

## Multi-zone behaviour

System type (`S06`) is read on every refresh, not assumed:

- **CH1 only** — one `climate` entity, no hot water entities.
- **CH1 + CH2** — a second `climate.<device>_zone_2` and its own sensors/select/buttons.
- **CH1 + hot water** — `water_heater`, hot water `select`/`switch`/`sensor`/`binary_sensor`/buttons appear.

Hot water entities are gated on the wiring centre actually reporting a hot
water attribute (`C45`), not just on the system type flag, so a
mis-reported/unusual configuration can't conjure a hot water control that
doesn't correspond to real hardware.

---

## Attribute reference

Useful if you're debugging or extending this. Zone prefixes are `A` (heating
1), `B` (heating 2), `C` (hot water). Temperatures are integers scaled by 100,
in whichever unit `S07` selects (0 = Celsius, 1 = Fahrenheit); conversion to
Celsius happens once, at the read boundary, in `api/model.py`. Only
Celsius-mode devices have been verified against real hardware.

| Attribute | Meaning |
|---|---|
| `S06` | System type: 0 = CH1, 1 = CH1+CH2, 2 = CH1+hot water |
| `S07` | Temperature unit: 0 = Celsius, 1 = Fahrenheit |
| `S09` | Frost protection setpoint |
| `S10` / `S11` / `S12` | Holiday enabled / start / end |
| `S13` | Thermostat firmware version |
| `S15` | Switching differential |
| `S17` | Temperature calibration (display offset) |
| `x00`–`x06` | Weekly program, Monday through Sunday (`x` = zone prefix) |
| `A84` / `A85` | Current room temperature / current setpoint |
| `A87` | Relay state (is the boiler being called) |
| `A88` / `A89` / `A92` | Temp-hold flag / off flag / manual flag |
| `A91` | Boost hours remaining |
| `C42` / `C43` / `C45` | Hot water mode / boost hours / relay state |

The three heating flags combine into one mode:

| `A89` off | `A92` manual | `A88` hold | Mode |
|---|---|---|---|
| 0 | 0 | 0 | Follow schedule |
| 0 | 0 | 1 | Temporary hold (until next scheduled change) |
| 0 | 1 | 0 | Permanent hold (schedule ignored) |
| 1 | 0 | 0 | Off |

### Schedule encoding

Each switching point is four characters, and every character carries
`ord(c) - 48`. So `'0'` is 0, `':'` is 10, `'X'` is 40. Up to six switching
points per day, per zone; the integration validates times (no duplicates,
`HH:MM` only), temperatures (0-50°C, 0.5° steps) and the six-entry limit
*before* anything is sent to Salus.

Heating: `HH MM TT tt` — hour, minute, whole degrees, tenths.

```
"5XE0"  ->  '5'=5, 'X'=40, 'E'=21, '0'=0  ->  05:40 at 21.0 °C
```

Hot water: `ON_H ON_M OFF_H OFF_M`, no temperature.

### Advance / skip / cancel override

The iT500 protocol has no dedicated "advance" or "skip" command - both are
built from the same documented temporary-hold mechanism (`A88`/setpoint) that
manual temperature changes already use, following the pattern used by the
community `salus-it500-extended` project:

- **Advance** jumps straight to the next scheduled temperature by holding it
  early - a one-shot write.
- **Skip** holds the *current* temperature through what would have been the
  next change, so the entry after it applies instead. Since a plain hold can
  revert on its own the moment the skipped slot's time arrives, the
  coordinator re-asserts the hold on each poll until the target time, then
  explicitly hands control back to the schedule. This is a Home Assistant-side
  emulation - it does not survive a Home Assistant restart, and a missed check
  simply means the zone falls back to its normal schedule, never anything
  unsafe.
- **Cancel override** returns a zone to following its schedule immediately,
  clearing any pending advance/skip.

---

## Example automations

Boost the hot water for an hour when someone gets home:

```yaml
automation:
  - alias: Hot water boost on arrival
    triggers:
      - trigger: state
        entity_id: person.you
        to: home
    actions:
      - action: salus_it500.boost
        data:
          device_id: !input salus_device
          zone: hw
          hours: 1
```

Rewrite the weekday heating program:

```yaml
- action: salus_it500.set_schedule
  data:
    device_id: !input salus_device
    zone: ch1
    day: [monday, tuesday, wednesday, thursday, friday]
    entries:
      - time: "06:30"
        temperature: 20.5
      - time: "08:30"
        temperature: 16
      - time: "17:00"
        temperature: 21
      - time: "22:30"
        temperature: 15
```

Read the program back:

```yaml
- action: salus_it500.get_schedule
  data:
    device_id: !input salus_device
    zone: ch1
  response_variable: program
```

Advance to the next scheduled temperature from a script or voice command:

```yaml
- action: salus_it500.advance
  data:
    device_id: !input salus_device
    zone: ch1
```

---

## Dashboard

A ready-to-adapt Lovelace dashboard is in
[`examples/dashboards/salus_full_dashboard.yaml`](examples/dashboards/salus_full_dashboard.yaml):
overview, heating controls (boost/advance/skip/cancel), today's and the weekly
schedule, hot water, and system/diagnostics. It uses only stock Lovelace card
types - no HACS frontend cards required. Update the device ID in the entity
IDs to match yours (Settings → Devices & Services → your Salus device), and
delete the CH2/hot water sections if your system doesn't have them.

---

## Troubleshooting

Turn on debug logging:

```yaml
logger:
  default: warning
  logs:
    custom_components.salus_it500: debug
```

**Everything is "unavailable".** The integration marks the device offline when
the thermostat reports a room temperature of zero, which is how the iT500
signals a lost RF link between the head and the wiring centre. Check the
thermostat's own display first. The connectivity/backend/last-error sensors
stay available even then, so you can see what the last poll actually did.

**"Salus is rate limiting this address."** Raise the polling interval and wait.
If you were also logged into the app or website while testing, that counts
against the same account. Retries use bounded, iterative backoff (1s/2s/4s/8s)
for genuine network hiccups - a 429 is never retried locally, it's surfaced
immediately so polling can back off across cycles instead of adding load.

**Commands seem to do nothing, then work.** Expected. The cloud takes anywhere
from a few seconds to a minute to push a change down to the thermostat. The
integration writes, then optimistically reflects the change once the write is
acknowledged, then confirms shortly after with a real read - so the card may
briefly show a value the thermostat hasn't fully applied yet.

**Setup fails but the app works.** Try forcing **Website only** in the options.
If that works, the Arrayent endpoint has probably moved.

### Reporting a problem

Download diagnostics from the device page. It includes capability flags, the
last update's timing/error, and the raw attribute map with credentials, the
device ID and unique IDs redacted — the raw map is the single most useful
thing if the protocol has shifted.

---

## Migration from an earlier install

Entity naming is unchanged for anything that already existed: `climate`,
`sensor`, `binary_sensor`, `select`, `number`, `switch` and `water_heater`
entity IDs are derived the same way as before
(`<domain>.salus_it500_<device_id>_<function>`), so upgrading in place should
not create duplicate entities. Everything new in this release - diagnostic
sensors, boost/advance/skip buttons, calibration/differential numbers - is
additive.

If your account has no hot water wired up, no hot water entities are created
regardless of what an older, simpler integration may have shown; that's the
capability-detection behaviour described above, not a regression.

If you do see a duplicate/orphaned entity after upgrading, remove it from
Settings → Devices & Services → Entities - it will not be recreated once the
underlying unique ID no longer resolves.

---

## A word of caution

This talks to an undocumented API that Salus does not support and can change or
withdraw at any time. It is not affiliated with or endorsed by Salus Controls.
The iT500 is a heating control on a boiler: keep the physical thermostat
accessible, and don't build anything safety-critical on top of a cloud service
that a vendor could switch off.

Protocol details were worked out from the community projects that came before
this one — `floringhimie/salusfy`, `adamjez/salus-controls` and
`spawnegit/salus-it500-extended`. Credit to them for the reverse engineering.

## Development

Tests (pure-Python schedule/model/transport tests plus a real Home Assistant
harness for entity/coordinator tests):

```bash
python3 -m venv .venv-ha && source .venv-ha/bin/activate
pip install pytest-homeassistant-custom-component aioresponses ruff
pytest
ruff check .
ruff format --check .
```

`python3 -m compileall custom_components/salus_it500` should always succeed.

## Licence

MIT.
