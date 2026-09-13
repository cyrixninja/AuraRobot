from arduino.app_utils import App, Bridge
from arduino.app_bricks.web_ui import WebUI


ui = WebUI()


# These limits are also enforced on the MCU. Keeping them here makes the web
# API honest about the safe physical range it accepts.
SAFE_MOTOR_SPEED = 70
SERVO_MIN = 55
SERVO_MAX = 135
MAX_TIMED_MOVE_MS = 5000


# ============================================================
# STATUS
# ============================================================

def status():

    return {
        "success": True,
        "device": "Arduino UNO Q",
        "controller": "L298N",
        "servo": True,
        "oled": True,
        "safe_motor_speed": SAFE_MOTOR_SPEED,
        "servo_min": SERVO_MIN,
        "servo_max": SERVO_MAX,
        "max_timed_move_ms": MAX_TIMED_MOVE_MS
    }


# ============================================================
# DRIVE
# ============================================================

def drive(left: int, right: int):

    left = max(
        -1,
        min(1, left)
    )

    right = max(
        -1,
        min(1, right)
    )

    try:

        Bridge.call(
            "drive",
            left,
            right
        )

        return {
            "success": True,
            "left": left,
            "right": right
        }

    except Exception as e:

        return {
            "success": False,
            "error": str(e)
        }


# ============================================================
# TIMED DRIVE
#
# The MCU owns the stop deadline. This is the endpoint Gemini uses for every
# ordinary movement command.
# ============================================================

def move(left: int, right: int, duration_ms: int):

    left = max(
        -1,
        min(1, left)
    )

    right = max(
        -1,
        min(1, right)
    )

    duration_ms = max(
        100,
        min(MAX_TIMED_MOVE_MS, duration_ms)
    )

    try:

        Bridge.call(
            "move_timed",
            left,
            right,
            duration_ms
        )

        return {
            "success": True,
            "left": left,
            "right": right,
            "duration_ms": duration_ms
        }

    except Exception as e:

        return {
            "success": False,
            "error": str(e)
        }


# ============================================================
# STOP
# ============================================================

def stop():

    try:

        Bridge.call(
            "stop"
        )

        return {
            "success": True
        }

    except Exception as e:

        return {
            "success": False,
            "error": str(e)
        }


# ============================================================
# SPEED
# ============================================================

def speed(value: int):

    value = max(
        0,
        min(
            SAFE_MOTOR_SPEED,
            value
        )
    )

    try:

        Bridge.call(
            "speed",
            value
        )

        return {
            "success": True,
            "speed": value
        }

    except Exception as e:

        return {
            "success": False,
            "error": str(e)
        }


# ============================================================
# SERVO
# ============================================================

def servo(angle: int):

    angle = max(
        SERVO_MIN,
        min(
            SERVO_MAX,
            angle
        )
    )

    try:

        Bridge.call(
            "servo",
            angle
        )

        return {
            "success": True,
            "angle": angle
        }

    except Exception as e:

        return {
            "success": False,
            "error": str(e)
        }


# ============================================================
# EMOTION
#
# 0 normal
# 1 happy
# 2 angry
# 3 tired
# 4 curious
# 5 auto
# ============================================================

def emotion(value: int):

    value = max(
        0,
        min(
            5,
            value
        )
    )


    try:

        Bridge.call(
            "emotion",
            value
        )

        return {
            "success": True,
            "emotion": value
        }

    except Exception as e:

        return {
            "success": False,
            "error": str(e)
        }


# ============================================================
# TEMPORARY EXPRESSION
#
# Allows a spoken reaction to end naturally and returns the face to automatic
# behaviour instead of leaving it in a fixed mood.
# ============================================================

def express(value: int, duration_ms: int):

    value = max(
        0,
        min(4, value)
    )

    duration_ms = max(
        500,
        min(5000, duration_ms)
    )

    try:

        Bridge.call(
            "express",
            value,
            duration_ms
        )

        return {
            "success": True,
            "emotion": value,
            "duration_ms": duration_ms
        }

    except Exception as e:

        return {
            "success": False,
            "error": str(e)
        }


# ============================================================
# BLINK
# ============================================================

def blink():

    try:

        Bridge.call(
            "blink"
        )

        return {
            "success": True
        }

    except Exception as e:

        return {
            "success": False,
            "error": str(e)
        }


# ============================================================
# ROUTES
# ============================================================

ui.expose_api(
    "GET",
    "/api/status",
    status
)

ui.expose_api(
    "GET",
    "/api/drive/{left}/{right}",
    drive
)

ui.expose_api(
    "GET",
    "/api/move/{left}/{right}/{duration_ms}",
    move
)

ui.expose_api(
    "GET",
    "/api/stop",
    stop
)

ui.expose_api(
    "GET",
    "/api/speed/{value}",
    speed
)

ui.expose_api(
    "GET",
    "/api/servo/{angle}",
    servo
)

ui.expose_api(
    "GET",
    "/api/emotion/{value}",
    emotion
)

ui.expose_api(
    "GET",
    "/api/express/{value}/{duration_ms}",
    express
)

ui.expose_api(
    "GET",
    "/api/blink",
    blink
)


print(
    "UNO Q Robot Web Controller starting...",
    flush=True
)


App.run()
