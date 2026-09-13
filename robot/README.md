# Aura voice application

`robot.py` streams microphone audio to Gemini Live, plays Aura’s voice, and handles model-requested robot actions. `robot_tools.py` is the safety boundary: Gemini receives semantic actions, never raw L298N PWM values.

## Install dependencies

Use the Python environment that has access to the USB microphone and speaker:

```bash
python3 -m pip install -r requirements.txt
```

## Configure and run

```bash
cd robot
cp .env.example .env
python3 -B robot.py
```

Set `GEMINI_API_KEY`, `INPUT_DEVICE`, `OUTPUT_DEVICE`, and `ROBOT_API_BASE` in `.env` before starting. To discover audio-device names:

```bash
python3 -c "import sounddevice as sd; print(sd.query_devices())"
```

Start from the `robot` directory so `robot.py` imports the matching `robot_tools.py`.

For an existing `~/gemini-live` installation, copy `robot.py`,
`robot_tools.py`, `requirements.txt`, and the new settings from `.env.example`,
then upgrade the active environment before restarting:

```bash
cd ~/gemini-live
source venv/bin/activate
python -m pip install -r requirements.txt
python -B robot.py
```

Keep your existing API key when updating `.env`.

## Configuration reference

| Variable | Purpose | Default |
| --- | --- | --- |
| `GEMINI_API_KEY` | Gemini API key. Required. | — |
| `GEMINI_MODEL` | Gemini Live model. | `gemini-3.1-flash-live-preview` |
| `GEMINI_VOICE` | Gemini prebuilt voice. | `Puck` |
| `INPUT_DEVICE` | Substring matching the microphone device. | `Maono Elf` |
| `OUTPUT_DEVICE` | Substring matching the speaker device. | `AB13X USB Audio` |
| `INPUT_RATE` | Preferred microphone sample rate. | `16000` |
| `INPUT_BLOCK_SIZE` | PortAudio capture block size; `0` lets the audio host choose. | `0` |
| `INPUT_LATENCY` | PortAudio capture-latency policy. | `high` |
| `AUDIO_CHUNK_MS` | Audio accumulated per Gemini Live packet (clamped to the 20–40 ms recommended range). | `40` ms |
| `MIC_VOICE_RMS_THRESHOLD` | Local voice-detection level used for manual activity detection. | `1200` |
| `LOCAL_SPEECH_MIN_SECONDS` | Minimum sustained local sound before Aura opens a manual speech turn. | `0.25` s |
| `LOCAL_SPEECH_SILENCE_SECONDS` | Local quiet time used to mark an utterance complete. | `0.9` s |
| `VOICE_RESPONSE_TIMEOUT_SECONDS` | Reconnect if locally detected speech gets no Gemini activity. | `12` s |
| `WATCHDOG_INTERVAL_SECONDS` | Voice-health check interval. | `1` s |
| `VAD_MODE` | `manual` sends local speech start/end markers; `automatic` leaves detection to Gemini. | `manual` |
| `ALLOW_BARGE_IN` | Keep listening while Aura speaks; use only with headphones or echo cancellation. | `false` |
| `OUTPUT_GAIN` | Software speaker gain. | `2.0` |
| `ECHO_COOLDOWN` | Delay before re-enabling the mic after Aura speaks; clamped to at least 0.7 s. | `0.7` s |
| `RECONNECT_INITIAL_SECONDS` | First retry delay after a Gemini connection failure. | `3` s |
| `RECONNECT_MAX_SECONDS` | Maximum exponential-retry delay. | `30` s |
| `ROBOT_API_BASE` | App Lab WebUI base URL, without `/api`. | `http://127.0.0.1:7000` |
| `ROBOT_HTTP_TIMEOUT` | Timeout for a WebUI request. | `2.0` s |

## Voice-turn behavior

Aura defaults to manual activity detection: sustained microphone energy opens a Gemini Live turn and local silence closes it. This avoids a noisy USB microphone leaving server-side VAD waiting forever. Set `VAD_MODE=automatic` only if Gemini consistently finalizes your speech in your room; automatic mode uses high start/end sensitivity. During speaker playback, microphone chunks are discarded so Aura does not hear itself. Aura waits 0.7 seconds after playback before listening again to let speaker echo fade.

USB microphones often expose only 44.1 or 48 kHz. Aura captures at the device's supported rate, groups host callback blocks into stable 40 ms packets, then resamples them to Gemini's native 16 kHz mono PCM before sending. Audio starts only after the websocket connects.

The local voice watchdog reports microphone health without printing from the real-time callback. If packets stop, or a locally detected utterance receives no Gemini turn activity, Aura closes the stale session and reconnects. Context compression and session-resumption handles keep long-running conversations usable across planned server connection renewals.

A Linux process lock prevents two copies of `robot.py` from competing for the same USB microphone and speaker. The lock is released automatically when the running process exits, including after most crashes.

| Log | Meaning |
| --- | --- |
| `You: ...` | Gemini finalized what Aura heard. |
| `Aura: ...` | Aura’s spoken response transcript. |
| `[Aura] ...` | A physical action or connection needs attention. Aura retries transient connection failures automatically. |

## Gemini actions

| Action | Constraint |
| --- | --- |
| `move_briefly` | Speed 70, 2.5 seconds, MCU-enforced stop. |
| `start_continuous_motion` | Explicit request only; renewed 1.2-second timed pulses. |
| `stop_robot` | Stops wheels and cancels continuous-motion renewal. |
| `look` | Left 55°, center 90°, right 135°. |
| `express` | Temporary normal, happy, angry, tired, or curious mood. |
| `blink` | One OLED eye blink. |
| `do_cute_action` | Stationary greet, celebrate, or think gesture. |

Short greetings are handled locally from Gemini’s finalized transcript, so the physical greeting does not depend on the model selecting a tool.

## Deployment rule

Always copy `robot.py`, `robot_tools.py`, and `requirements.txt` together. Replacing only one Python source file can cause missing-method errors such as `RobotController has no attribute move_briefly`.

See the root [README](../README.md) for complete setup, safety, and WebUI guidance.
