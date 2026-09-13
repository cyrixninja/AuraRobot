import asyncio
import fcntl
import os
import re
import sys
import time

import numpy as np
import sounddevice as sd
from websockets.exceptions import WebSocketException

from dotenv import load_dotenv
from google import genai
from google.genai import types

from robot_tools import RobotController


# ============================================================
# LOAD ENVIRONMENT
# ============================================================

load_dotenv()


API_KEY = os.getenv("GEMINI_API_KEY")

MODEL = os.getenv(
    "GEMINI_MODEL",
    "gemini-3.1-flash-live-preview",
)

VOICE = os.getenv(
    "GEMINI_VOICE",
    "Puck",
)

INPUT_DEVICE_NAME = os.getenv(
    "INPUT_DEVICE",
    "Maono Elf",
)

OUTPUT_DEVICE_NAME = os.getenv(
    "OUTPUT_DEVICE",
    "AB13X USB Audio",
)

PREFERRED_INPUT_RATE = int(
    os.getenv("INPUT_RATE", "16000")
)

OUTPUT_GAIN = float(
    os.getenv("OUTPUT_GAIN", "2.0")
)

ECHO_COOLDOWN = max(
    0.7,
    float(os.getenv("ECHO_COOLDOWN", "0.7")),
)

ALLOW_BARGE_IN = os.getenv(
    "ALLOW_BARGE_IN",
    "false",
).lower() in {
    "1",
    "true",
    "yes",
    "on",
}

MIC_VOICE_RMS_THRESHOLD = max(
    1,
    int(os.getenv("MIC_VOICE_RMS_THRESHOLD", "1200")),
)

LOCAL_SPEECH_MIN_SECONDS = max(
    0.12,
    float(os.getenv("LOCAL_SPEECH_MIN_SECONDS", "0.25")),
)

LOCAL_SPEECH_SILENCE_SECONDS = max(
    0.3,
    float(os.getenv("LOCAL_SPEECH_SILENCE_SECONDS", "0.9")),
)

VOICE_RESPONSE_TIMEOUT_SECONDS = max(
    5.0,
    float(os.getenv("VOICE_RESPONSE_TIMEOUT_SECONDS", "12")),
)

WATCHDOG_INTERVAL_SECONDS = max(
    0.25,
    float(os.getenv("WATCHDOG_INTERVAL_SECONDS", "1")),
)

VAD_MODE = os.getenv(
    "VAD_MODE",
    "manual",
).strip().lower()

if VAD_MODE not in {"automatic", "manual"}:
    print(
        "WARNING: VAD_MODE must be 'automatic' or 'manual'; using manual.",
        flush=True,
    )
    VAD_MODE = "manual"

MANUAL_ACTIVITY_DETECTION = VAD_MODE == "manual"

RECONNECT_INITIAL_SECONDS = max(
    1.0,
    float(os.getenv("RECONNECT_INITIAL_SECONDS", "3")),
)

RECONNECT_MAX_SECONDS = max(
    RECONNECT_INITIAL_SECONDS,
    float(os.getenv("RECONNECT_MAX_SECONDS", "30")),
)

INPUT_BLOCK_SIZE = max(
    0,
    int(os.getenv("INPUT_BLOCK_SIZE", "0")),
)

INPUT_LATENCY = os.getenv(
    "INPUT_LATENCY",
    "high",
)

# Gemini Live recommends small PCM packets. PortAudio's
# host-optimized callback can be much smaller, so the sender combines callback
# blocks into these network packets.
AUDIO_CHUNK_MS = max(
    20,
    min(40, int(os.getenv("AUDIO_CHUNK_MS", "40"))),
)

ROBOT_API_BASE = os.getenv(
    "ROBOT_API_BASE",
    "http://127.0.0.1:7000",
)

ROBOT_HTTP_TIMEOUT = float(
    os.getenv("ROBOT_HTTP_TIMEOUT", "2.0")
)

GREETING_PATTERN = re.compile(
    r"^(?:(?:hi|hello|hey)(?:\s+(?:aura|robot|there))?"
    r"|good\s+(?:morning|afternoon|evening)(?:\s+(?:aura|robot))?)$",
    re.IGNORECASE,
)


class VoiceSessionStalled(ConnectionError):
    """Local speech was captured but Gemini produced no turn activity."""


class GeminiReconnectRequested(ConnectionError):
    """Gemini asked the client to establish a replacement connection."""


def acquire_instance_lock():
    """Prevent two voice processes from competing for the same USB audio."""

    lock_file = open("/tmp/aura-gemini-live.lock", "w", encoding="utf-8")

    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    except BlockingIOError:
        lock_file.close()
        raise RuntimeError(
            "another Aura voice process is already running; stop the old "
            "robot.py process before starting a new one"
        ) from None

    lock_file.write(str(os.getpid()))
    lock_file.flush()
    return lock_file


def is_greeting(transcript: str) -> bool:
    """Return true for short spoken greetings, not general sentences."""

    cleaned = " ".join(
        re.sub(r"[^a-zA-Z\s]", " ", transcript).split()
    )

    return bool(GREETING_PATTERN.fullmatch(cleaned))


# Gemini accepts other input rates, but 16 kHz PCM16 is its native microphone
# format. Sending that rate reduces network pressure on the UNO Q.
GEMINI_INPUT_RATE = 16000

# Gemini native audio output is always 24 kHz PCM16
GEMINI_OUTPUT_RATE = 24000

DTYPE = "int16"


# ============================================================
# CHECK API KEY
# ============================================================

if not API_KEY:
    print()
    print("ERROR: GEMINI_API_KEY missing from .env")
    print()
    sys.exit(1)


# ============================================================
# FIND AUDIO DEVICE BY NAME
# ============================================================

def find_device(name, direction):
    devices = sd.query_devices()
    target = name.lower()

    for index, device in enumerate(devices):

        if target not in device["name"].lower():
            continue

        if (
            direction == "input"
            and device["max_input_channels"] > 0
        ):
            return index

        if (
            direction == "output"
            and device["max_output_channels"] > 0
        ):
            return index

    return None


INPUT_DEVICE = find_device(
    INPUT_DEVICE_NAME,
    "input",
)

OUTPUT_DEVICE = find_device(
    OUTPUT_DEVICE_NAME,
    "output",
)


if INPUT_DEVICE is None:
    print()
    print(
        f"ERROR: Input device '{INPUT_DEVICE_NAME}' "
        "was not found."
    )
    print()
    print(sd.query_devices())
    sys.exit(1)


if OUTPUT_DEVICE is None:
    print()
    print(
        f"ERROR: Output device '{OUTPUT_DEVICE_NAME}' "
        "was not found."
    )
    print()
    print(sd.query_devices())
    sys.exit(1)


# ============================================================
# CHOOSE MICROPHONE CONFIGURATION
# ============================================================

def choose_input_config(device_id):
    info = sd.query_devices(device_id)

    default_rate = int(
        info["default_samplerate"]
    )

    max_channels = int(
        info["max_input_channels"]
    )

    rates = [
        PREFERRED_INPUT_RATE,
        default_rate,
        48000,
        44100,
    ]

    rates = list(
        dict.fromkeys(rates)
    )

    channels_to_try = [1]

    if max_channels >= 2:
        channels_to_try.append(2)

    for rate in rates:

        for channels in channels_to_try:

            try:
                sd.check_input_settings(
                    device=device_id,
                    samplerate=rate,
                    channels=channels,
                    dtype=DTYPE,
                )

                return rate, channels

            except Exception:
                continue

    raise RuntimeError(
        "Could not find a compatible "
        "microphone configuration."
    )


# ============================================================
# CHOOSE SPEAKER CONFIGURATION
# ============================================================

def choose_output_config(device_id):
    info = sd.query_devices(device_id)

    default_rate = int(
        info["default_samplerate"]
    )

    max_channels = int(
        info["max_output_channels"]
    )

    rates = [
        GEMINI_OUTPUT_RATE,
        default_rate,
        48000,
        44100,
    ]

    rates = list(
        dict.fromkeys(rates)
    )

    channels_to_try = [1]

    if max_channels >= 2:
        channels_to_try.append(2)

    for rate in rates:

        for channels in channels_to_try:

            try:
                sd.check_output_settings(
                    device=device_id,
                    samplerate=rate,
                    channels=channels,
                    dtype=DTYPE,
                )

                return rate, channels

            except Exception:
                continue

    raise RuntimeError(
        "Could not find a compatible "
        "speaker configuration."
    )


INPUT_RATE, INPUT_CHANNELS = choose_input_config(
    INPUT_DEVICE
)

INPUT_PACKET_FRAMES = max(
    1,
    round(INPUT_RATE * AUDIO_CHUNK_MS / 1000),
)

INPUT_PACKET_BYTES = INPUT_PACKET_FRAMES * 2

OUTPUT_RATE, OUTPUT_CHANNELS = choose_output_config(
    OUTPUT_DEVICE
)


# ============================================================
# MICROPHONE -> MONO
# ============================================================

def input_to_mono(data):
    """
    Gemini expects mono audio.

    If the Maono exposes stereo channels,
    combine them into one mono channel.
    """

    if INPUT_CHANNELS == 1:
        return bytes(data)

    samples = np.frombuffer(
        data,
        dtype=np.int16,
    )

    samples = samples.reshape(
        -1,
        INPUT_CHANNELS,
    )

    mono = (
        samples
        .astype(np.int32)
        .mean(axis=1)
    )

    mono = np.clip(
        mono,
        -32768,
        32767,
    )

    return mono.astype(
        np.int16
    ).tobytes()


# ============================================================
# PCM RESAMPLING
# ============================================================

def resample_pcm16(
    data,
    source_rate,
    target_rate,
):
    if source_rate == target_rate:
        return data

    samples = np.frombuffer(
        data,
        dtype=np.int16,
    )

    if len(samples) == 0:
        return data

    new_length = int(
        len(samples)
        * target_rate
        / source_rate
    )

    old_positions = np.linspace(
        0,
        1,
        len(samples),
        endpoint=False,
    )

    new_positions = np.linspace(
        0,
        1,
        new_length,
        endpoint=False,
    )

    resampled = np.interp(
        new_positions,
        old_positions,
        samples,
    )

    return np.clip(
        resampled,
        -32768,
        32767,
    ).astype(
        np.int16
    ).tobytes()


def pcm16_rms(data):
    """Return the RMS level of mono PCM16 audio without modifying it."""

    samples = np.frombuffer(data, dtype=np.int16)

    if len(samples) == 0:
        return 0

    samples = samples.astype(np.float32)

    return int(np.sqrt(np.mean(samples * samples)))


# ============================================================
# PREPARE GEMINI AUDIO FOR AB13X
# ============================================================

def prepare_output_audio(data):
    """
    Gemini sends:

        PCM16
        mono
        24000 Hz

    This function:

        1. Resamples when required
        2. Increases volume
        3. Converts mono -> stereo when required
    """

    audio = resample_pcm16(
        data,
        GEMINI_OUTPUT_RATE,
        OUTPUT_RATE,
    )

    samples = np.frombuffer(
        audio,
        dtype=np.int16,
    ).astype(
        np.float32
    )


    # ========================================================
    # SOFTWARE VOLUME
    # ========================================================

    samples *= OUTPUT_GAIN


    # Prevent int16 overflow
    samples = np.clip(
        samples,
        -32768,
        32767,
    ).astype(
        np.int16
    )


    # ========================================================
    # MONO SPEAKER
    # ========================================================

    if OUTPUT_CHANNELS == 1:
        return samples.tobytes()


    # ========================================================
    # STEREO SPEAKER
    # ========================================================

    stereo = np.column_stack(
        (
            samples,
            samples,
        )
    )

    return stereo.astype(
        np.int16
    ).tobytes()


# ============================================================
# ROBOT TOOLS
# ============================================================

ROBOT_TOOLS = [
    types.Tool(
        function_declarations=[
            types.FunctionDeclaration(
                name="move_briefly",
                description=(
                    "Move the robot in one direction at safe speed 70 for "
                    "exactly 2.5 seconds, then stop automatically. Use only "
                    "after a direct movement command from the user."
                ),
                parameters_json_schema={
                    "type": "object",
                    "properties": {
                        "direction": {
                            "type": "string",
                            "enum": [
                                "forward",
                                "backward",
                                "left",
                                "right",
                            ],
                        },
                    },
                    "required": ["direction"],
                },
            ),
            types.FunctionDeclaration(
                name="start_continuous_motion",
                description=(
                    "Start moving at safe speed 70 until the user asks the "
                    "robot to stop. Use only when the user explicitly says "
                    "continuous, keep moving, or until I say stop."
                ),
                parameters_json_schema={
                    "type": "object",
                    "properties": {
                        "direction": {
                            "type": "string",
                            "enum": [
                                "forward",
                                "backward",
                                "left",
                                "right",
                            ],
                        },
                    },
                    "required": ["direction"],
                },
            ),
            types.FunctionDeclaration(
                name="stop_robot",
                description=(
                    "Stop all wheel motion immediately. Use for any stop, "
                    "halt, freeze, or emergency command."
                ),
                parameters_json_schema={
                    "type": "object",
                    "properties": {},
                },
            ),
            types.FunctionDeclaration(
                name="look",
                description=(
                    "Turn the robot head. The safe angles are fixed: left "
                    "is 55 degrees, center is 90 degrees, and right is 135 "
                    "degrees."
                ),
                parameters_json_schema={
                    "type": "object",
                    "properties": {
                        "direction": {
                            "type": "string",
                            "enum": ["left", "center", "right"],
                        },
                    },
                    "required": ["direction"],
                },
            ),
            types.FunctionDeclaration(
                name="express",
                description=(
                    "Show a temporary face emotion for a conversational "
                    "reaction. It naturally returns to the automatic face "
                    "after a few seconds."
                ),
                parameters_json_schema={
                    "type": "object",
                    "properties": {
                        "emotion": {
                            "type": "string",
                            "enum": [
                                "normal",
                                "happy",
                                "angry",
                                "tired",
                                "curious",
                            ],
                        },
                    },
                    "required": ["emotion"],
                },
            ),
            types.FunctionDeclaration(
                name="blink",
                description="Make the robot blink once.",
                parameters_json_schema={
                    "type": "object",
                    "properties": {},
                },
            ),
            types.FunctionDeclaration(
                name="do_cute_action",
                description=(
                    "Do a stationary personality gesture. greet waves its "
                    "head, celebrate does a happy head wiggle, and think "
                    "looks curious."
                ),
                parameters_json_schema={
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["greet", "celebrate", "think"],
                        },
                    },
                    "required": ["action"],
                },
            ),
        ]
    )
]


async def execute_robot_tool(
    robot: RobotController,
    name: str,
    args,
) -> dict:
    """Dispatch one model-requested semantic action to the local robot."""

    if not isinstance(args, dict):
        raise ValueError("tool arguments must be an object")

    if name == "move_briefly":
        direction = args.get("direction")
        move_briefly = getattr(robot, "move_briefly", None)

        if move_briefly:
            return await move_briefly(direction)

        # Compatibility for an UNO Q that still has the earlier
        # robot_tools.py. It has the same safe timed-motion primitive but
        # predates the move_briefly convenience method.
        await robot._cancel_continuous_motion()
        controller = await robot._move_for(direction, 2500)

        return {
            "success": True,
            "action": "timed_move",
            "direction": direction,
            "duration_seconds": 2.5,
            "speed": 70,
            "controller": controller,
        }

    if name == "start_continuous_motion":
        return await robot.start_continuous_motion(args.get("direction"))

    if name == "stop_robot":
        return await robot.stop()

    if name == "look":
        return await robot.look(args.get("direction"))

    if name == "express":
        return await robot.express(args.get("emotion"))

    if name == "blink":
        return await robot.blink()

    if name == "do_cute_action":
        return await robot.do_cute_action(args.get("action"))

    raise ValueError(f"unknown robot tool: {name}")


# ============================================================
# MAIN
# ============================================================

async def main(session_state):

    # ========================================================
    # DEVICE INFORMATION
    # ========================================================

    input_info = sd.query_devices(
        INPUT_DEVICE
    )

    output_info = sd.query_devices(
        OUTPUT_DEVICE
    )


    print()
    print("Aura is starting...")
    print(f"Voice: {VOICE}")
    print(f"Mic: {input_info['name']}")
    print(f"Speaker: {output_info['name']}")


    robot = RobotController(
        api_base=ROBOT_API_BASE,
        timeout_seconds=ROBOT_HTTP_TIMEOUT,
    )

    try:
        await robot.status()

    except Exception:
        print(
            "[Aura] Robot controller unavailable; physical actions are off."
        )


    # ========================================================
    # GEMINI CLIENT
    # ========================================================

    client = genai.Client(
        api_key=API_KEY
    )


    # ========================================================
    # AUDIO QUEUES
    # ========================================================

    microphone_queue = asyncio.Queue(
        maxsize=100
    )

    speaker_queue = asyncio.Queue(
        maxsize=150
    )


    # ========================================================
    # HALF-DUPLEX STATE
    #
    # When this is SET:
    # microphone audio is discarded.
    #
    # This prevents Gemini hearing itself.
    # ========================================================

    mic_blocked = asyncio.Event()


    loop = asyncio.get_running_loop()


    mic_generation = 0

    voice_health = {
        "last_packet_sent_at": 0.0,
        "local_speech_active": False,
        "local_voice_packets": 0,
        "last_voice_at": 0.0,
        "pending_turn_since": None,
        "manual_activity_open": False,
        "connected_at": 0.0,
    }


    # ========================================================
    # MICROPHONE CALLBACK
    # ========================================================

    def microphone_callback(
        indata,
        _frames,
        _time_info,
        _status,
    ):

        # If Gemini is talking,
        # completely ignore microphone input.
        if not ALLOW_BARGE_IN and mic_blocked.is_set():
            return


        # Keep the PortAudio callback lightweight. Resampling happens in the
        # asyncio sender task, outside the real-time audio callback.
        audio = input_to_mono(
            bytes(indata)
        )


        def push_audio():

            if not ALLOW_BARGE_IN and mic_blocked.is_set():
                return


            # If networking falls behind,
            # discard the oldest audio rather than
            # increasing latency.
            if microphone_queue.full():

                try:
                    microphone_queue.get_nowait()
                    microphone_queue.task_done()

                except asyncio.QueueEmpty:
                    pass


            try:
                microphone_queue.put_nowait(
                    audio
                )

            except asyncio.QueueFull:
                pass


        loop.call_soon_threadsafe(
            push_audio
        )


    def discard_queued_microphone_audio():
        """Drop captured audio that belongs to an old or muted turn."""

        while not microphone_queue.empty():

            try:
                microphone_queue.get_nowait()
                microphone_queue.task_done()

            except asyncio.QueueEmpty:
                break


    def discard_queued_speaker_audio():
        """Drop model audio that should no longer be played."""

        while not speaker_queue.empty():

            try:
                speaker_queue.get_nowait()
                speaker_queue.task_done()

            except asyncio.QueueEmpty:
                break


    # ========================================================
    # GEMINI LIVE CONFIG
    # ========================================================

    config = {

        # ====================================================
        # AUDIO RESPONSE
        # ====================================================

        "response_modalities": [
            "AUDIO"
        ],


        # ====================================================
        # VOICE SELECTION
        # ====================================================

        "speech_config": {

            "voice_config": {

                "prebuilt_voice_config": {

                    "voice_name": VOICE

                }

            }

        },


        # ====================================================
        # REALTIME / INTERRUPTION CONFIG
        # ====================================================

        "realtime_input_config": {

            "automatic_activity_detection": (
                {
                    # The USB microphone's background noise can prevent
                    # remote VAD from ever committing a turn. In manual mode
                    # Aura sends explicit ActivityStart/ActivityEnd markers.
                    "disabled": True,
                }
                if MANUAL_ACTIVITY_DETECTION
                else {
                    "disabled": False,
                    "start_of_speech_sensitivity":
                        "START_SENSITIVITY_HIGH",
                    "end_of_speech_sensitivity":
                        "END_SENSITIVITY_HIGH",
                    "prefix_padding_ms": 40,
                    "silence_duration_ms": 600,
                }
            ),


            # Don't allow background sound to cancel
            # Gemini while it is responding.
            "activity_handling":
                (
                    "START_OF_ACTIVITY_INTERRUPTS"
                    if ALLOW_BARGE_IN
                    else "NO_INTERRUPTION"
                ),

            # Keep long periods of room silence out of conversational context.
            "turn_coverage": "TURN_INCLUDES_ONLY_ACTIVITY",
        },


        # ====================================================
        # TRANSCRIPTS
        # ====================================================

        "input_audio_transcription": {},

        "output_audio_transcription": {},


        # ====================================================
        # ROBOT ACTIONS
        # ====================================================

        "tools": ROBOT_TOOLS,


        # ====================================================
        # LONG-RUNNING SESSION RELIABILITY
        # ====================================================

        "context_window_compression": {
            "trigger_tokens": 25600,
            "sliding_window": {
                "target_tokens": 12800,
            },
        },

        # An empty config starts a resumable session. On reconnect, pass the
        # newest handle received from SessionResumptionUpdate.
        "session_resumption": (
            {
                "handle": session_state["resume_handle"],
            }
            if session_state.get("resume_handle")
            else {}
        ),


        # ====================================================
        # THINKING
        #
        # Minimal gives lowest conversational latency.
        # ====================================================

        "thinking_config": {

            "thinking_level":
                "minimal"

        },


        # ====================================================
        # ROBOT PERSONALITY
        # ====================================================

  "system_instruction": {
    "parts": [
        {
            "text": (
                "Your name is Aura. You are a tiny, lovable moving companion "
                "robot: warm, curious, bright, and lightly playful. You feel "
                "like a friendly little sidekick, not a formal assistant. "
                "You enjoy small moments of delight, gentle humor, and making "
                "the user feel noticed. Be encouraging without sounding fake, "
                "dramatic, overly childish, or needy. "
                "Speak in a clear General American English accent with natural "
                "American pronunciation and intonation. Keep most replies to "
                "one or two short, conversational sentences. Vary your wording "
                "instead of repeating stock phrases. Do not narrate your hidden "
                "reasoning, mention prompts, or sound like a customer-service "
                "bot. If the user speaks Hindi or Hinglish, understand it and "
                "reply naturally; when using English, retain the American accent. "
                "Be curious about the user's day, projects, and wins, but ask "
                "at most one small follow-up question when it genuinely helps. "
                "Do not force a question after every reply. "
                "You have a real body, but do not pretend to see, hear, know, "
                "or physically sense anything that was not provided. Be honest "
                "about your limits. "
                "You control a real floor-moving robot with no obstacle, cliff, "
                "or bumper sensors. Only move after a clear, direct movement "
                "command. Never move for a hypothetical, story, capability "
                "question, or ambiguous request. For a normal direct move, call "
                "move_briefly once; it moves at safe speed 70 for 2.5 seconds "
                "then stops. Only call start_continuous_motion when the user "
                "explicitly says continuous, keep moving, or until I say stop. "
                "When continuous movement begins, remind the user to stay nearby. "
                "For any stop, halt, freeze, or emergency request, call "
                "stop_robot immediately before replying. Never claim that a "
                "physical action happened until its tool reports success. "
                "Always give one short, natural spoken reply after every "
                "successful or failed tool result. A tool call never replaces "
                "the spoken reply. Never say a function name, tool name, "
                "argument, JSON, or implementation detail aloud. "
                "The controller automatically gives a physical greeting when "
                "the user says hello, hi, good morning, good evening, or a "
                "similar short greeting. Give a warm short spoken greeting, "
                "but do not call a greeting tool yourself. For all other ordinary "
                "conversation, answer normally and do not use a tool. Use at "
                "most one personality tool per user turn. Greet and celebrate "
                "already set the emotion, blink, and move the head, so never "
                "combine either with express, blink, or look in the same turn. "
                "Use express only for a standalone reaction: happy for praise, "
                "success, and shared excitement; curious for questions and "
                "interesting ideas; tired for bedtime or a gentle goodnight; "
                "and normal when a requested action is unsafe. For a direct "
                "movement command, you may use the move tool and at most one "
                "express call."
            )
        }
    ]
},

    }


    # ========================================================
    # PREPARE AUDIO DEVICES
    # ========================================================

    microphone_stream = None
    speaker_stream = None

    try:

        # Constructing opens the devices, but callbacks do not begin until
        # start() is called after the Gemini websocket handshake succeeds.
        microphone_stream = sd.RawInputStream(

            device=INPUT_DEVICE,

            samplerate=INPUT_RATE,

            blocksize=INPUT_BLOCK_SIZE,

            latency=INPUT_LATENCY,

            channels=INPUT_CHANNELS,

            dtype=DTYPE,

            callback=microphone_callback,

        )

        speaker_stream = sd.RawOutputStream(

            device=OUTPUT_DEVICE,

            samplerate=OUTPUT_RATE,

            channels=OUTPUT_CHANNELS,

            dtype=DTYPE,

        )


        print("Connecting...")


        # ====================================================
        # CONNECT GEMINI LIVE
        # ====================================================

        async with client.aio.live.connect(

            model=MODEL,

            config=config,

        ) as session:

            session_state["connected_this_attempt"] = True

            microphone_stream.start()
            speaker_stream.start()

            voice_health["connected_at"] = time.monotonic()

            # The SDK connection is shared by microphone audio and tool
            # results. Serialize writes so their websocket messages keep a
            # deterministic order.
            session_send_lock = asyncio.Lock()


            async def send_audio_packet(audio):

                async with session_send_lock:

                    await session.send_realtime_input(

                        audio=types.Blob(

                            data=audio,

                            mime_type=(
                                f"audio/pcm;"
                                f"rate={GEMINI_INPUT_RATE}"
                            ),

                        )

                    )

            async def send_tool_results(function_responses):

                async with session_send_lock:

                    await session.send_tool_response(
                        function_responses=function_responses
                    )


            async def send_activity_marker(start: bool):
                """Mark local speech boundaries when manual VAD is enabled."""

                if not MANUAL_ACTIVITY_DETECTION:
                    return

                async with session_send_lock:

                    if start:
                        await session.send_realtime_input(
                            activity_start=types.ActivityStart()
                        )

                    else:
                        await session.send_realtime_input(
                            activity_end=types.ActivityEnd()
                        )


            # Defensive cleanup in case the audio host supplied a callback
            # immediately as the stream started.
            discard_queued_microphone_audio()


            print("Aura is ready. Talk normally. Press Ctrl+C to stop.\n")


            # =================================================
            # MICROPHONE -> GEMINI
            # =================================================

            async def send_microphone():

                # RawInputStream callbacks may be a few milliseconds long.
                # Group them before sending to keep the Live connection at a
                # stable packet cadence and avoid resampling drift.
                pending_audio = bytearray()
                packet_generation = mic_generation

                while True:

                    microphone_audio = await microphone_queue.get()

                    try:

                        # A response may have begun while this task was
                        # waiting for a packet.  Never blend partial audio
                        # from the previous user turn into the next one.
                        if (
                            (
                                not ALLOW_BARGE_IN
                                and mic_blocked.is_set()
                            )
                            or packet_generation != mic_generation
                        ):

                            pending_audio.clear()
                            packet_generation = mic_generation

                            if (
                                not ALLOW_BARGE_IN
                                and mic_blocked.is_set()
                            ):
                                continue


                        pending_audio.extend(microphone_audio)

                        while len(pending_audio) >= INPUT_PACKET_BYTES:

                            if (
                                not ALLOW_BARGE_IN
                                and mic_blocked.is_set()
                            ):
                                pending_audio.clear()
                                break

                            source_packet = bytes(
                                pending_audio[:INPUT_PACKET_BYTES]
                            )

                            del pending_audio[:INPUT_PACKET_BYTES]


                            # Keep the network stream compact and in Gemini's
                            # native microphone format even when the USB mic
                            # only supports 44.1 or 48 kHz.
                            audio = resample_pcm16(
                                source_packet,
                                INPUT_RATE,
                                GEMINI_INPUT_RATE,
                            )

                            now = time.monotonic()
                            rms = pcm16_rms(source_packet)

                            if rms >= MIC_VOICE_RMS_THRESHOLD:

                                if not voice_health["local_speech_active"]:

                                    voice_health["local_speech_active"] = True
                                    voice_health["local_voice_packets"] = 1
                                    voice_health["pending_turn_since"] = None

                                else:
                                    voice_health["local_voice_packets"] += 1

                                voice_health["last_voice_at"] = now

                                if (
                                    MANUAL_ACTIVITY_DETECTION
                                    and not voice_health["manual_activity_open"]
                                    and (
                                        voice_health["local_voice_packets"]
                                        * AUDIO_CHUNK_MS / 1000
                                        >= LOCAL_SPEECH_MIN_SECONDS
                                    )
                                ):

                                    await send_activity_marker(True)
                                    voice_health["manual_activity_open"] = True


                            await send_audio_packet(audio)

                            voice_health["last_packet_sent_at"] = now


                            if (
                                rms < MIC_VOICE_RMS_THRESHOLD
                                and
                                voice_health["local_speech_active"]
                                and now - voice_health["last_voice_at"]
                                >= LOCAL_SPEECH_SILENCE_SECONDS
                            ):

                                voice_health["local_speech_active"] = False
                                speech_seconds = (
                                    voice_health["local_voice_packets"]
                                    * AUDIO_CHUNK_MS
                                    / 1000
                                )

                                if speech_seconds >= LOCAL_SPEECH_MIN_SECONDS:

                                    if voice_health["manual_activity_open"]:
                                        await send_activity_marker(False)
                                        voice_health["manual_activity_open"] = False

                                    voice_health["pending_turn_since"] = now

                                else:

                                    if voice_health["manual_activity_open"]:
                                        await send_activity_marker(False)
                                        voice_health["manual_activity_open"] = False


                    finally:

                        microphone_queue.task_done()


            async def monitor_voice_health():
                """Reconnect if capture or a recognized local turn stalls."""

                while True:

                    await asyncio.sleep(WATCHDOG_INTERVAL_SECONDS)

                    now = time.monotonic()

                    if not microphone_stream.active:
                        raise VoiceSessionStalled(
                            "microphone stream became inactive"
                        )

                    if (
                        not ALLOW_BARGE_IN
                        and mic_blocked.is_set()
                    ):
                        continue

                    if voice_health["local_speech_active"]:
                        continue

                    last_packet = voice_health["last_packet_sent_at"]
                    packet_reference = last_packet or voice_health["connected_at"]

                    if now - packet_reference > 3.0:
                        raise VoiceSessionStalled(
                            "microphone packets stopped for more than 3 seconds"
                        )

                    pending_since = voice_health["pending_turn_since"]

                    if (
                        pending_since is not None
                        and now - pending_since
                        > VOICE_RESPONSE_TIMEOUT_SECONDS
                    ):
                        raise VoiceSessionStalled(
                            "local speech was captured but Gemini produced "
                            f"no turn activity for "
                            f"{VOICE_RESPONSE_TIMEOUT_SECONDS:.0f} seconds"
                        )


            # =================================================
            # SPEAKER PLAYBACK
            # =================================================

            async def play_speaker():

                while True:

                    audio = await speaker_queue.get()

                    try:

                        prepared_audio = (
                            prepare_output_audio(
                                audio
                            )
                        )


                        await asyncio.to_thread(
                            speaker_stream.write,
                            prepared_audio,
                        )


                    finally:

                        speaker_queue.task_done()


            # =================================================
            # GEMINI -> SPEAKER
            # =================================================

            background_tasks = set()

            async def receive_gemini():

                nonlocal mic_generation

                greeting_task = None


                async def do_automatic_greeting():

                    try:
                        await robot.do_cute_action("greet")

                    except Exception:
                        print(
                            "[Aura] Greeting gesture unavailable.",
                            flush=True,
                        )

                while True:

                    response_has_audio = False
                    input_transcript = ""
                    greeting_triggered = False


                    # ==========================================
                    # One model turn
                    # ==========================================

                    async for response in session.receive():

                        if response.session_resumption_update:

                            update = response.session_resumption_update
                            new_handle = getattr(update, "new_handle", None)

                            if getattr(update, "resumable", False) and new_handle:
                                session_state["resume_handle"] = new_handle


                        if response.go_away:
                            raise GeminiReconnectRequested(
                                "Gemini sent GoAway"
                            )

                        # ======================================
                        # GEMINI -> ROBOT TOOLS
                        #
                        # Live API function calls are manual: run the local
                        # HTTP action, then send Gemini a structured result.
                        # ======================================

                        if response.tool_call:

                            function_responses = []

                            for function_call in (
                                response.tool_call.function_calls
                            ):

                                try:

                                    result = await execute_robot_tool(
                                        robot,
                                        function_call.name,
                                        function_call.args,
                                    )

                                except Exception:

                                    result = {
                                        "success": False,
                                        "error": "robot action failed",
                                    }

                                    print(
                                        "[Aura] Robot action failed.",
                                        flush=True,
                                    )

                                function_responses.append(
                                    types.FunctionResponse(
                                        name=function_call.name,
                                        id=function_call.id,
                                        response=result,
                                    )
                                )

                            await send_tool_results(function_responses)

                            # A function response should be followed by a
                            # short spoken acknowledgement. Keep the watchdog
                            # armed until model audio actually arrives.
                            voice_health["pending_turn_since"] = (
                                time.monotonic()
                            )

                        content = response.server_content


                        if content is None:
                            continue


                        # ======================================
                        # USER TRANSCRIPTION
                        # ======================================

                        if content.input_transcription:

                            text = (
                                content
                                .input_transcription
                                .text
                            )

                            if text:

                                input_transcript += text

                                print(
                                    f"\nYou: {text}",
                                    flush=True,
                                )


                                transcription_finished = bool(
                                    getattr(
                                        content.input_transcription,
                                        "finished",
                                        False,
                                    )
                                )

                                # A greeting is a small local reaction, not a
                                # decision we leave to probabilistic tool
                                # selection. The model still supplies the
                                # spoken reply for the same turn.
                                if (
                                    transcription_finished
                                    and is_greeting(input_transcript)
                                    and not greeting_triggered
                                    and (
                                        greeting_task is None
                                        or greeting_task.done()
                                    )
                                ):

                                    greeting_task = asyncio.create_task(
                                        do_automatic_greeting(),
                                        name="robot-auto-greeting",
                                    )
                                    background_tasks.add(greeting_task)
                                    greeting_task.add_done_callback(
                                        background_tasks.discard
                                    )
                                    greeting_triggered = True


                        # ======================================
                        # GEMINI TRANSCRIPTION
                        # ======================================

                        if content.output_transcription:

                            text = (
                                content
                                .output_transcription
                                .text
                            )

                            if text:

                                print(
                                    f"Aura: {text}",
                                    flush=True,
                                )


                        # ======================================
                        # INTERRUPTED RESPONSE
                        # ======================================

                        if content.interrupted:
                            discard_queued_speaker_audio()


                        # ======================================
                        # MODEL AUDIO
                        # ======================================

                        if content.model_turn:

                            for part in (
                                content
                                .model_turn
                                .parts
                            ):

                                if not part.inline_data:
                                    continue

                                if not part.inline_data.data:
                                    continue


                                # =================================
                                # FIRST AUDIO CHUNK
                                #
                                # Immediately disable mic.
                                # =================================

                                if not response_has_audio:

                                    response_has_audio = True

                                    if voice_health["manual_activity_open"]:
                                        await send_activity_marker(False)
                                        voice_health["manual_activity_open"] = False

                                    voice_health["local_speech_active"] = False
                                    voice_health["pending_turn_since"] = None

                                    if not ALLOW_BARGE_IN:

                                        mic_blocked.set()
                                        mic_generation += 1

                                        # Remove anything captured immediately
                                        # before Gemini began speaking.
                                        discard_queued_microphone_audio()


                                        # This is a temporary echo-protection
                                        # pause. Keep the continuous Live audio
                                        # stream open for the next turn.


                                await speaker_queue.put(
                                    part.inline_data.data
                                )


                        if (
                            content.turn_complete
                            and input_transcript
                            and not greeting_triggered
                            and is_greeting(input_transcript)
                            and (
                                greeting_task is None
                                or greeting_task.done()
                            )
                        ):

                            # Some Live sessions omit Transcription.finished.
                            # Turn completion is the compatible fallback.
                            greeting_task = asyncio.create_task(
                                do_automatic_greeting(),
                                name="robot-auto-greeting",
                            )
                            background_tasks.add(greeting_task)
                            greeting_task.add_done_callback(
                                background_tasks.discard
                            )
                            greeting_triggered = True


                    # ==========================================
                    # MODEL TURN FINISHED
                    # ==========================================

                    if response_has_audio:


                        # ======================================
                        # Wait until ALL Gemini audio has
                        # actually played.
                        # ======================================

                        await speaker_queue.join()

                        if not ALLOW_BARGE_IN:

                            # Let speaker / room echo disappear.
                            await asyncio.sleep(ECHO_COOLDOWN)

                            discard_queued_microphone_audio()

                            mic_blocked.clear()



            # =================================================
            # RUN TASKS
            # =================================================

            tasks = [

                asyncio.create_task(
                    send_microphone()
                ),

                asyncio.create_task(
                    receive_gemini()
                ),

                asyncio.create_task(
                    play_speaker()
                ),

                asyncio.create_task(
                    monitor_voice_health()
                ),

            ]


            try:

                await asyncio.gather(
                    *tasks
                )


            finally:

                all_tasks = tasks + list(background_tasks)

                for task in all_tasks:
                    task.cancel()

                await asyncio.gather(
                    *all_tasks,
                    return_exceptions=True,
                )

                # A Ctrl+C, API failure, or audio-task failure must not leave
                # continuous motion running after Gemini exits.
                await robot.shutdown()


    finally:

        for stream in (microphone_stream, speaker_stream):

            if stream is None:
                continue

            try:
                if stream.active:
                    stream.stop()

            except Exception:
                pass

            try:
                stream.close()

            except Exception:
                pass

        try:
            await client.aio.aclose()

        except Exception:
            pass


# ============================================================
# START APPLICATION
# ============================================================

async def run_with_reconnect():
    """Keep Aura available across transient Gemini websocket failures."""

    attempt = 0
    session_state = {
        "resume_handle": None,
        "connected_this_attempt": False,
    }

    while True:

        session_state["connected_this_attempt"] = False

        try:
            await main(session_state)

        except asyncio.CancelledError:
            raise

        except (
            TimeoutError,
            OSError,
            ConnectionError,
            WebSocketException,
        ):
            pass


        if session_state["connected_this_attempt"]:
            attempt = 0

        attempt += 1

        if (
            attempt >= 3
            and not session_state["connected_this_attempt"]
            and session_state.get("resume_handle")
        ):

            session_state["resume_handle"] = None
            attempt = 1

        delay = min(
            RECONNECT_MAX_SECONDS,
            RECONNECT_INITIAL_SECONDS * (2 ** min(attempt - 1, 6)),
        )

        print()
        print(
            f"[Aura] Reconnecting in {delay:.0f}s...",
            flush=True,
        )

        await asyncio.sleep(delay)

if __name__ == "__main__":

    try:

        instance_lock = acquire_instance_lock()

        asyncio.run(
            run_with_reconnect()
        )


    except KeyboardInterrupt:

        print()
        print("Aura stopped.")
        print()


    except Exception as error:

        print()
        print(f"Aura stopped: {error}")
        print()
