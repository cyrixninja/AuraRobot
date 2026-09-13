# Arduino UNO Q App Lab controller

This App Lab project is split across the UNO Q’s two processors:

- `sketch/sketch.ino` runs on the MCU. It drives the L298N, servo, SH1106 OLED face, timed-move deadline, blinking, and automatic expressions.
- `python/main.py` runs on the UNO Q Linux side. It exposes the WebUI HTTP endpoints and forwards requests over Arduino Bridge to the MCU.
- `assets/index.html` is the manual browser dashboard.

## Deploy

Open this folder as an Arduino App Lab project and click **Run** whenever `sketch.ino` or `main.py` changes. App Lab flashes the MCU and starts the Linux application together.

Before using Gemini, open the dashboard and verify it reports **Connected**. Test with wheels raised first.

The root [mechanical build and electronics guide](../README.md#mechanical-build-and-electronics) documents the supplied circuit diagram and five printable enclosure parts. The pin mapping below matches the reference build.

## Manual Control Dashboard

![Aura Robot dashboard](../assets/dashboard.png)

The App Lab WebUI is the manual control deck for wiring checks, safe demonstrations, and voice-app fallback. It checks `/api/status` every five seconds and shows **Connected** only when the controller responds. It initializes Aura at speed `70`, head center `90°`, and automatic face mode.

| Control | Use |
| --- | --- |
| Drive pad | Press and hold Forward, Back, Left, or Right; release to stop. The control uses direct drive only while a person is actively holding it. |
| Stop | Sends `/api/stop` immediately. Use it before changing any wiring. |
| Wheel Power | Sets speed from 0–70; begin at 70 or lower. |
| Personality | Select Normal, Happy, Angry, Tired, Curious, or Auto OLED behavior. |
| Blink Eyes | Triggers one OLED blink. |
| Head Rotation | Adjusts the head between 55° and 135°; Center returns it to 90°. |

Keyboard shortcuts work while the dashboard is open:

| Keys | Action |
| --- | --- |
| `W` / `A` / `S` / `D` or arrow keys | Hold to drive forward / left / back / right; releasing the key stops. |
| `Space` | Stop immediately. |
| `Q` / `E` / `C` | Head 5° left / 5° right / center at 90°. |
| `1` / `2` / `3` / `4` / `5` / `0` | Normal / Happy / Angry / Tired / Curious / Auto face. |
| `B` | Blink eyes. |

Safe preflight: lift the wheels, verify **Connected**, test Stop, Blink, servo center/left/right, then briefly test each drive direction. Put Aura on the floor only after these checks succeed and keep the Stop button accessible.

## Enforced limits

| Item | Limit |
| --- | --- |
| Wheel speed | 0–70 |
| Head left | 55° |
| Head center | 90° |
| Head right | 135° |
| Timed move | 100–5,000 ms |
| Temporary expression | 500–5,000 ms |

The MCU owns the timed-move deadline. `/api/move` is therefore suitable for short voice movements even if the Linux app stops responding.

## HTTP API

Replace `BASE` with the URL configured as `ROBOT_API_BASE` in `robot/.env`.

| Request | Description |
| --- | --- |
| `GET BASE/api/status` | Controller identity and enforced limits. |
| `GET BASE/api/drive/{left}/{right}` | Manual latched wheel drive. Each side is `-1`, `0`, or `1`; always follow with stop. |
| `GET BASE/api/move/{left}/{right}/{duration_ms}` | MCU-timed wheel move. Each side is `-1`, `0`, or `1`. |
| `GET BASE/api/stop` | Stops both wheels immediately. |
| `GET BASE/api/speed/{value}` | Sets speed, clamped to 0–70. |
| `GET BASE/api/servo/{angle}` | Turns the head, clamped to 55–135°. |
| `GET BASE/api/emotion/{value}` | Persistent mood: normal `0`, happy `1`, angry `2`, tired `3`, curious `4`, automatic `5`. |
| `GET BASE/api/express/{value}/{duration_ms}` | Temporary mood followed by automatic face behavior. |
| `GET BASE/api/blink` | Triggers an eye blink. |

## Quick API test

After App Lab starts the project, run these commands from the UNO Q or another machine that can reach the WebUI server:

```bash
curl http://HOST:PORT/api/status
curl http://HOST:PORT/api/servo/90
curl http://HOST:PORT/api/blink
curl http://HOST:PORT/api/move/1/1/2500
```

Replace `HOST:PORT` with the WebUI address configured in `robot/.env`.

Keep wheels raised for the movement test. The direct `/api/drive` route is for the manual dashboard; Gemini uses `/api/move` so normal voice commands time out on the MCU.

Return to the root [README](../README.md) for the complete setup and safety guide.
