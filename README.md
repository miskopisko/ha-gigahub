# Bell Giga Hub for Home Assistant

Connection, fibre, Wi-Fi and speed-test stats from a Bell Giga Hub (Sagemcom 5690), read locally
from the hub's own admin API. No cloud, no Bell account.

The existing Sagemcom integration only tracks who's home. This one is about the connection itself:
how fast it's going right now, how much you used this month, whether the fibre signal is drifting,
what the hub's own speed test says your line can do, and how well every Wi-Fi client is connected.

## What you get

**Internet**
- Live download / upload speed, two ways (see *Fibre vs internet speed* below):
  - *Fibre download / upload speed*: prompt, but includes Fibe TV and voice
  - *Download / upload speed*: internet only, but the hub reports it late and in lumps
- A *Live speed source* selector so a dashboard can switch its live graph between the two
- Data downloaded / uploaded (total counters, so daily and monthly usage come straight out of
  Home Assistant's statistics)
- Internet connectivity, status, connected since, public IP

**Speed test**
- A *Run speed test* button that starts the hub's built-in test against Bell's servers. It measures
  the fibre line itself, so it isn't capped by a 1 Gbit/s LAN port the way a test from a computer is.
- Optional automatic tests every N hours, so it can replace the Speedtest.net integration for
  tracking your line over time
- Download, upload, latency and last-run sensors (the last result is picked up even if the test was
  started from the hub's web page), plus a *Speed test running* sensor

**Fibre line**
- Receive and transmit optical power (dBm), module temperature, voltage, laser bias current
- Fibre errors and a *Fibre problem* sensor (link down or any optical alarm)

**Wi-Fi** (per band: 2.4, 5 and 6 GHz)
- Traffic to and from clients, current channel (bandwidth and settings as attributes), noise floor
- Client count, with every client's signal, standard and link rates as attributes
- Weakest client signal, so you can see which device needs a better spot
- Wi-Fi devices on pods: Wi-Fi devices the hub knows about that aren't on its own radios

**Per-device Wi-Fi traffic**
- *Busiest Wi-Fi device*: the device using the most right now; its attributes list every Wi-Fi
  device on the hub's radios with IP address, download/upload speed (5-minute averages), data used
  since it connected, band and signal
- For every Wi-Fi device you name (see Options): its own device in Home Assistant with download and
  upload speed, data downloaded / uploaded (running totals that survive reconnects and restarts, so
  they get daily and monthly statistics) and Wi-Fi signal
- Devices connected through a Bell Wi-Fi pod aren't included: the hub doesn't count their traffic

**Devices and hub**
- Devices connected, wired, Wi-Fi (names as attributes). *Wired devices* also lists each wired
  device's IP address, LAN port and the port's link speed
- *TV receivers*: Bell TV boxes, found by the hub's hidden TV network or its set-top-box tag,
  with band, signal, link rate and data since connecting as attributes. They're left out of the
  Wi-Fi and wired lists, and named *TV receiver (…)* unless you name them yourself
- Friendly device names: when the hub only knows a MAC address, the device is named after its
  manufacturer (from the MAC prefix), e.g. *Nintendo (12:34:56)*, or *Android device* / *Private
  address* when that's all there is to go on. You can name any device yourself (see Options).
- A *New device* event entity that fires when a device the hub has never seen joins, and a
  *Newest device* sensor that keeps the last 25 arrivals (time, name, maker, connection, IP)
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

**Settings → Devices & services → Bell Giga Hub → Configure** opens a menu with two pages, then
**Save and close**.

**Name devices**: pick a device from the list (it shows each one's MAC address, manufacturer and
connection) and type a name. Named Wi-Fi devices also get their own traffic sensors. Name as many as you like before saving. Names are stored in Home
Assistant, not on the hub; a name set in the hub's own web page is used when you haven't set one
here. Leave the name empty to go back to the automatic name.

**Polling and speed tests**:

| Option | Default | Notes |
|---|---|---|
| Traffic and fibre polling interval | 30 s | Speeds are averaged over this window. One small request. |
| Device list polling interval | 300 s | The device list is the hub's heaviest request (a few hundred KB), so it's polled less often. |
| Run a speed test every | 0 h (off) | Automatic speed tests. The first one runs one interval after Home Assistant starts. |
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

For tests every N hours, use the *Run a speed test every* option instead. To run one at a fixed
time, say 4 a.m.:

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
- **Private Wi-Fi addresses can't be traced to a manufacturer.** Phones, tablets and laptops
  usually use a randomised MAC address per network, so they show as *Private address* until you
  name them. Turning off "private Wi-Fi address" for your home network on the device also works.
- **The first run doesn't announce every device.** The *New device* event seeds itself from the
  hub's own device history, then fires only for MAC addresses it hasn't seen before. Phones with
  private (randomised) Wi-Fi addresses can show up as new when they rotate their address.
- **Speed-test traffic is left out of the data totals, but only for tests this integration
  starts.** The hub counts its own speed test oddly: each test adds roughly 7 GB of *upload* to the
  internet counters but only a fraction of the download. Tests started from the button or the
  automatic schedule are subtracted from *Data downloaded / uploaded* and the live speed graphs.
  Tests started from the hub's own web page aren't, so each of those adds about 7 GB to the upload
  total.
- **Counters reset when the hub reboots.** The data totals are `total_increasing`, so Home
  Assistant's statistics handle the reset without a negative day.
- **One login session.** The integration keeps one session open and serialises its requests. Logging
  in to the hub's web page at the same time works fine.
- **Load on the hub.** The traffic poll is one ~35 KB request answered in ~0.3 s. The device list
  poll is two requests totalling ~8.5 s and ~560 KB, mostly because the hub keeps a long history of
  past devices and Wi-Fi connections that can't be filtered out. That's a few percent of the hub's
  admin web server, and none of its packet forwarding (which runs in hardware). Requests time out
  after 45 s.
- **Speeds are rolling averages**: fibre, port and Wi-Fi speeds over the last ~20 s, internet
  speed over the last ~60 s. They refresh on every poll.

## Fibre vs internet speed

The hub keeps two sets of traffic counters, and they behave very differently:

| | Fibre counter | Internet (PPP) counter |
|---|---|---|
| Counts | everything on the fibre: internet, Fibe TV, home phone, Bell services | internet only |
| Updated | about every 10 s | in irregular batches, 15-25 s late |
| A steady 300 Mbit/s download, sampled every 5 s, looks like | 585, 0, 691, 0, 627, 0 (≈ 300 averaged) | 198, 0, 0, 1252, 0, 0, 0, then 519 *after* the download ended |

So the **fibre** sensors are the better live view (a 20-s rolling average tracks real traffic
closely), and the **internet** sensors are right over a minute or more but lumpy moment to moment.
*Data downloaded / uploaded* use the internet counter, because lateness doesn't matter for totals
and it leaves TV out of your usage.

During one of the hub's own speed tests, the fibre speed shows the test's real ~3 Gbit/s, while
the internet speed and data totals leave it out.

For a properly live graph, set the traffic polling interval to 10 s (the hub's own update rate).
It's a 0.3-s request, so the load on the hub stays small.

## Development

Parsing lives in `custom_components/gigahub/parse.py` and has no Home Assistant imports, so the tests
run without it:

```sh
uvx pytest
uvx ruff check .
```

The integration icon (`custom_components/gigahub/brand/icon.png`) is the Bell Wi-Fi icon the Giga Hub
serves on its own admin page, upscaled from 128 to 256 px. Home Assistant 2026.3+ picks it up on its own.
Bell and the Bell logo are trademarks of Bell Canada, used here only to show which hub this works with.

Built on [python-sagemcom-api](https://github.com/imicknl/python-sagemcom-api). Not affiliated with
Bell Canada or Sagemcom.
