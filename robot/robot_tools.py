"""Safe, semantic HTTP controls for the UNO Q companion robot.

Gemini never receives the raw L298N API.  It can only use the functions in
this module, which keep wheel speed, head travel, and timed motion bounded.
"""

import asyncio
import json
from urllib.error import HTTPError, URLError
from urllib.request import urlopen


SAFE_SPEED = 70
NORMAL_MOVE_MS = 2500
CONTINUOUS_PULSE_MS = 1200
CONTINUOUS_RENEW_SECONDS = 0.8
EXPRESSION_MS = 4000


DIRECTIONS = {
    "forward": (1, 1),
    "backward": (-1, -1),
    "left": (-1, 1),
    "right": (1, -1),
}


LOOK_ANGLES = {
    "left": 55,
    "center": 90,
    "right": 135,
}


EMOTIONS = {
    "normal": 0,
    "happy": 1,
    "angry": 2,
    "tired": 3,
    "curious": 4,
}


class RobotApiError(RuntimeError):
    """The UNO Q web controller was unreachable or rejected a command."""


class RobotController:
    """A safety-focused client for the UNO Q web-controller endpoints."""

    def __init__(self, api_base: str, timeout_seconds: float = 2.0):
        self.api_base = api_base.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._continuous_task = None

    async def _api(self, path: str) -> dict:
        """GET one local controller endpoint and validate its JSON response."""

        url = f"{self.api_base}{path}"

        def request() -> dict:
            try:
                with urlopen(url, timeout=self.timeout_seconds) as response:
                    body = response.read().decode("utf-8")

            except HTTPError as error:
                raise RobotApiError(
                    f"controller returned HTTP {error.code} for {path}"
                ) from error

            except URLError as error:
                raise RobotApiError(
                    f"could not reach controller at {self.api_base}: {error.reason}"
                ) from error

            except TimeoutError as error:
                raise RobotApiError(
                    f"controller timed out for {path}"
                ) from error

            try:
                payload = json.loads(body)

            except json.JSONDecodeError as error:
                raise RobotApiError(
                    f"controller returned invalid JSON for {path}"
                ) from error

            if not isinstance(payload, dict):
                raise RobotApiError(
                    f"controller returned an invalid response for {path}"
                )

            if not payload.get("success"):
                raise RobotApiError(
                    payload.get("error", f"controller rejected {path}")
                )

            return payload

        return await asyncio.to_thread(request)

    @staticmethod
    def _wheel_values(direction: str) -> tuple[int, int]:
        try:
            return DIRECTIONS[direction]

        except KeyError as error:
            raise ValueError(f"unsupported direction: {direction}") from error

    async def _move_for(self, direction: str, duration_ms: int) -> dict:
        left, right = self._wheel_values(direction)

        return await self._api(
            f"/api/move/{left}/{right}/{duration_ms}"
        )

    async def _cancel_continuous_motion(self) -> None:
        task = self._continuous_task
        self._continuous_task = None

        if task and not task.done():
            task.cancel()

            try:
                await task

            except asyncio.CancelledError:
                pass

    async def status(self) -> dict:
        """Return the App Lab controller status and enforced limits."""

        return await self._api("/api/status")

    async def move_briefly(self, direction: str) -> dict:
        """Start a bounded 2.5-second move; the MCU stops it by itself."""

        await self._cancel_continuous_motion()
        result = await self._move_for(direction, NORMAL_MOVE_MS)

        return {
            "success": True,
            "action": "timed_move",
            "direction": direction,
            "duration_seconds": NORMAL_MOVE_MS / 1000,
            "speed": SAFE_SPEED,
            "controller": result,
        }

    async def _renew_continuous_motion(self, direction: str) -> None:
        """Renew short MCU-timed pulses until the user tells the robot to stop."""

        try:
            while True:
                await asyncio.sleep(CONTINUOUS_RENEW_SECONDS)
                await self._move_for(direction, CONTINUOUS_PULSE_MS)

        except asyncio.CancelledError:
            raise

        except Exception as error:
            # A failed renewal must fail safe. The active pulse expires on the
            # MCU even if this best-effort stop cannot reach the web server.
            print(
                f"[Robot] Continuous-motion renewal failed: {error}",
                flush=True,
            )

            try:
                await self._api("/api/stop")

            except RobotApiError:
                pass

            self._continuous_task = None

    async def start_continuous_motion(self, direction: str) -> dict:
        """Move until stop, implemented as short fail-safe MCU-timed pulses."""

        await self._cancel_continuous_motion()
        await self._api("/api/stop")
        await self._move_for(direction, CONTINUOUS_PULSE_MS)

        self._continuous_task = asyncio.create_task(
            self._renew_continuous_motion(direction),
            name="robot-continuous-motion",
        )

        return {
            "success": True,
            "action": "continuous_move_started",
            "direction": direction,
            "speed": SAFE_SPEED,
            "stop_instruction": "Say stop to stop the robot.",
        }

    async def stop(self) -> dict:
        await self._cancel_continuous_motion()
        result = await self._api("/api/stop")

        return {
            "success": True,
            "action": "stopped",
            "controller": result,
        }

    async def look(self, direction: str) -> dict:
        try:
            angle = LOOK_ANGLES[direction]

        except KeyError as error:
            raise ValueError(f"unsupported look direction: {direction}") from error

        result = await self._api(f"/api/servo/{angle}")

        return {
            "success": True,
            "direction": direction,
            "angle": angle,
            "controller": result,
        }

    async def express(self, emotion: str) -> dict:
        try:
            mood = EMOTIONS[emotion]

        except KeyError as error:
            raise ValueError(f"unsupported emotion: {emotion}") from error

        result = await self._api(
            f"/api/express/{mood}/{EXPRESSION_MS}"
        )

        return {
            "success": True,
            "emotion": emotion,
            "duration_seconds": EXPRESSION_MS / 1000,
            "controller": result,
        }

    async def blink(self) -> dict:
        result = await self._api("/api/blink")

        return {
            "success": True,
            "action": "blinked",
            "controller": result,
        }

    async def do_cute_action(self, action: str) -> dict:
        """Perform a small, stationary personality gesture."""

        if action == "greet":
            await self.express("happy")
            await self.blink()
            await self.look("right")
            await asyncio.sleep(0.25)
            await self.look("left")
            await asyncio.sleep(0.25)
            await self.look("center")

        elif action == "celebrate":
            await self.express("happy")
            await self.look("left")
            await asyncio.sleep(0.2)
            await self.look("right")
            await asyncio.sleep(0.2)
            await self.blink()
            await self.look("center")

        elif action == "think":
            await self.express("curious")
            await self.look("right")

        else:
            raise ValueError(f"unsupported cute action: {action}")

        return {
            "success": True,
            "action": action,
        }

    async def shutdown(self) -> None:
        """Cancel background motion and make one last best-effort stop call."""

        await self._cancel_continuous_motion()

        try:
            await self._api("/api/stop")

        except RobotApiError as error:
            print(f"[Robot] Final stop failed: {error}", flush=True)
