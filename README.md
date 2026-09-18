# Salus iT500 for Home Assistant

A custom integration for the Salus iT500 internet thermostat (and the RT310i /
RT510 / XT500 heads that pair with the same wiring centre).

Salus has never published an API for the iT500. This integration uses the same
undocumented cloud backend the official mobile app talks to, and falls back to
the consumer web portal if that backend is unreachable.

---

## What you get

| Platform | Entities |
|---|---|
| `climate` | Thermostat per heating zone. Off / Follow schedule / Hold, with current room temperature, setpoint and live boiler-call state. |
| `water_heater` | Hot water channel: Follow schedule / Once / Always on / Always off. |
| `sensor` | Room temperature, setpoint, scheduled temperature now, boost countdown, frost setpoint, heating mode, today's program. |
| `binary_sensor` | Heating relay, hot water relay, frost protection active, holiday mode, boost active, thermostat battery. |
| `switch` | Hot water override, holiday mode. |
| `select` | Heating mode (all four Salus modes, not just the three HVAC ones), hot water mode. |
| `number` | Frost protection temperature, temperature calibration, switching differential. |
| `button` | Boost 1h / 2h / 3h per zone, cancel boost, skip next scheduled change. |

Entities appear only if your system actually has them. A CH1-only system gets
no hot water entities; a CH1+CH2 system gets a second thermostat.

### Services

| Service | Notes |
|---|---|
| `salus_it500.set_schedule` | Write up to six switching points to one or more days. |
| `salus_it500.get_schedule` | Returns the decoded weekly program (a response service). |
| `salus_it500.boost` / `cancel_boost` | Timed override that reverts on its own. |
| `salus_it500.set_holiday` / `clear_holiday` | Drop every zone to frost protection. |

---

## Installation

### HACS (custom repository)

1. HACS → Integrations → three-dot menu → **Custom repositories**
2. Add the repository URL, category **Integration**
3. Install, then restart Home Assistant

### Manual

Copy `custom_components/salus_it500/` into your Home Assistant `config/custom_components/`
directory so you end up with:

```
config/
└── custom_components/
    └── salus_it500/
        ├── __init__.py
        ├── api/
        └── ...
```

Restart Home Assistant.

### Setup

**Settings → Devices & Services → Add Integration → Salus iT500**

Enter the email address and password you use for the iT500 app, plus the
device ID for the thermostat. Sign in at `salus-it500.com`, open your device,
and copy the number from the address bar — you can paste the whole URL:

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
> the only option — but don't drop below 60 s without a reason. The thermostat
> itself only reports every minute or so anyway, so faster polling buys nothing.

---

## The two connection methods

### App API (preferred)

`sal-emea-p01-api.arrayent.com` — the Arrayent IoT platform Salus built the
iT500 service on. This is what your phone talks to.

- Login posts the username and an MD5 digest of the password, and gets back a
  `userId` and `securityToken`.
- Everything after that hits `/zdk/services/zamapi/<method>` with that token and
  answers in XML.
- `getDeviceAttributesWithValues` returns the complete attribute map;
  `setMultiDeviceAttributes2` writes up to three attributes at a time.

Gives you everything: both heating zones, hot water, schedules, boost,
calibration, holiday mode.

### Website (fallback)

`salus-it500.com` — form login, scrape a hidden CSRF token out of the control
page, poll `ajax_device_values.php`, write through `includes/set.php`.

Limited to zone 1 temperature, on/off, hot water on/off and the frost setpoint.
No schedules, no boost, no calibration, no second zone. The integration hides
the entities it can't drive rather than showing you dead controls.

On **Automatic**, the API is tried first and the website is used only if the API
is unreachable. A rejected password does *not* trigger a fallback — both routes
use the same credentials, so there'd be no point.

---

## Attribute reference

Useful if you're debugging or extending this. Zone prefixes are `A` (heating 1),
`B` (heating 2), `C` (hot water). Temperatures are integers scaled by 100.

| Attribute | Meaning |
|---|---|
| `S06` | System type: 0 = CH1, 1 = CH1+CH2, 2 = CH1+hot water |
| `S09` | Frost protection setpoint |
| `S13` | Thermostat firmware version |
| `S15` / `S17` | Switching differential / temperature calibration |
| `S10` / `S11` / `S12` | Holiday enabled / start / end |
| `x00`–`x06` | Weekly program, Monday through Sunday |
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
`ord(c) - 48`. So `'0'` is 0, `':'` is 10, `'X'` is 40.

Heating: `HH MM TT tt` — hour, minute, whole degrees, tenths.

```
"5XE0"  ->  '5'=5, 'X'=40, 'E'=21, '0'=0  ->  05:40 at 21.0 °C
```

Hot water: `ON_H ON_M OFF_H OFF_M`, no temperature.

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
thermostat's own display first.

**"Salus is rate limiting this address."** Raise the polling interval and wait.
If you were also logged into the app or website while testing, that counts
against the same account.

**Commands seem to do nothing, then work.** Expected. The cloud takes anywhere
from a few seconds to a minute to push a change down to the thermostat. The
integration updates the UI optimistically and then confirms with a read, so the
card may briefly show a value the thermostat hasn't applied yet.

**Setup fails but the app works.** Try forcing **Website only** in the options.
If that works, the Arrayent endpoint has probably moved.

### Reporting a problem

Download diagnostics from the device page. It includes the raw attribute map
with your credentials and device ID redacted — that's the single most useful
thing if the protocol has shifted.

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

## Licence

MIT.
