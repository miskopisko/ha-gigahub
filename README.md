# Bell Giga Hub for Home Assistant

Connection, fibre, Wi-Fi and speed-test stats from a Bell Giga Hub (Sagemcom 5690), read locally
from the hub's own admin API. No cloud, no Bell account.

The existing Sagemcom integration only tracks who's home. This one is about the connection itself:
how fast it's going right now, how much you used this month, whether the fibre signal is drifting,
what the hub's own speed test says your line can do, and how well every Wi-Fi client is connected.

## What you get

**Internet**
- Download / upload speed (Mbit/s, averaged over each poll)
- Data downloaded / uploaded (total counters, so daily and monthly usage come straight out of
  Home Assistant's statistics)
- Internet connectivity, status, connected since, public IP

**Speed test**
- A *Run speed test* button that starts the hub's built-in test against Bell's servers. It measures
  the fibre line itself, so it isn't capped by a 1 Gbit/s LAN port the way a test from a computer is.
- Download, upload, latency and last-run sensors (the last result is picked up even if the test was
  started from the hub's web page)

**Fibre line**
- Receive and transmit optical power (dBm), module temperature, voltage, laser bias current
- Fibre errors and a *Fibre problem* sensor (link down or any optical alarm)

**Wi-Fi** (per band: 2.4, 5 and 6 GHz)
- Traffic to and from clients, current channel (bandwidth and settings as attributes), noise floor
- Client count, with every client's signal, standard and link rates as attributes
- Weakest client signal, so you can see which device needs a better spot
- Wi-Fi devices on pods: Wi-Fi devices the hub knows about that aren't on its own radios

**Devices and hub**
- Devices connected, wired, Wi-Fi (names as attributes)
- A *New device* event entity that fires when a device the hub has never seen joins
- LAN port link speed and traffic per port (unused ports are added disabled)
- Last boot, memory use, firmware, reboot count, and a restart button

## Install

1. In Home Assistant, go to **Settings → Marketplace**, choose **Add from link**, and enter
   `https://github.com/miskopisko/ha-gigahub`.
2. Install **Bell Giga Hub**.
3. Go to **Settings → Devices & services → Add integration → Bell Giga Hub** and enter the hub's
   address (Bell's default is `192.168.2.1`), `admin`, and the admin password (printed on the hub's
   label unless you changed it).

## Options

**Settings → Devices & services → Bell Giga Hub → Configure**

| Option | Default | Notes |
|---|---|---|
| Traffic and fibre polling interval | 30 s | Speeds are averaged over this window. One small request. |
| Device list polling interval | 300 s | The device list is the hub's heaviest request (a few hundred KB), so it's polled less often. |
| Pause other traffic during speed tests | off | More accurate result; everything else on the network stalls for ~30 s. |

## Dashboard

[`dashboards/network.yaml`](dashboards/network.yaml) is a ready-made **Network** view: live speed
graphs, daily and monthly usage bars, speed-test history, fibre trend, a per-band table of Wi-Fi
clients with signal quality, device counts and a log of new devices. It uses
[mini-graph-card](https://github.com/kalkih/mini-graph-card) for the live graphs.

To add it: open a dashboard, **Edit → ⋮ → Raw configuration editor**, and paste the file's contents
as a new item under `views:`.

## Automation ideas

Get a phone notification when a new device joins:

```yaml
triggers:
  - trigger: state
    entity_id: event.giga_hub_new_device
conditions:
  - condition: template
    # Skip the state restore after a restart or reload.
    value_template: "{{ trigger.from_state.state != 'unavailable' }}"
actions:
  - action: notify.notify
    data:
      title: New device on the network
      message: >-
        {{ trigger.to_state.attributes.name }} ({{ trigger.to_state.attributes.mac }})
        joined over {{ trigger.to_state.attributes.connection }}.
```

Run the hub's speed test every night at 4 a.m.:

```yaml
triggers:
  - trigger: time
    at: "04:00:00"
actions:
  - action: button.press
    target:
      entity_id: button.giga_hub_run_speed_test
```

## Good to know

- **Bell's Wi-Fi app (Plume) may manage your Wi-Fi.** If you have pods, channels and band steering
  are chosen by Bell's cloud, so the channel sensors show what it picked. This integration only reads
  Wi-Fi settings; it never changes them.
- **The first run doesn't announce every device.** The *New device* event seeds itself from the
  hub's own device history, then fires only for MAC addresses it hasn't seen before. Phones with
  private (randomised) Wi-Fi addresses can show up as new when they rotate their address.
- **Counters reset when the hub reboots.** The data totals are `total_increasing`, so Home
  Assistant's statistics handle the reset without a negative day.
- **One login session.** The integration keeps one session open and serialises its requests. Logging
  in to the hub's web page at the same time works fine.
- **Speeds are averages** over the polling interval, so short bursts are smoothed out. Lower the
  interval if you want spikier graphs.

## Development

Parsing lives in `custom_components/gigahub/parse.py` and has no Home Assistant imports, so the tests
run without it:

```sh
uvx pytest
uvx ruff check .
```

The integration icon is drawn in `assets/icon.svg` and exported to
`custom_components/gigahub/brand/icon.png` (256 px) and `icon@2x.png` (512 px), which Home Assistant
2026.3+ picks up on its own.

Built on [python-sagemcom-api](https://github.com/imicknl/python-sagemcom-api). Not affiliated with
Bell Canada or Sagemcom.
