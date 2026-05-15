import asyncio
import base64
import json
import os
import subprocess
import time
from typing import Any

import requests
import websockets
from loguru import logger

from actions import get_ram_usage, get_tcp_streams, run_root, stop_roblox


MASTER_URL = os.getenv("AEGIS_MASTER_URL", "http://127.0.0.1:8000").rstrip("/")
DEVICE_ID = os.getenv("AEGIS_DEVICE_ID", os.getenv("HOSTNAME", "node-unknown"))
DEVICE_NAME = os.getenv("AEGIS_DEVICE_NAME", DEVICE_ID)
HEARTBEAT_SEC = int(os.getenv("AEGIS_HEARTBEAT_SEC", "10"))
WATCHDOG_INTERVAL = 30

state = {
    "watchdog_enabled": False,
    "last_watchdog_check": 0,
    "last_start_link": "",
}


def _ws_base() -> str:
    if MASTER_URL.startswith("https://"):
        return "wss://" + MASTER_URL[len("https://"):]
    if MASTER_URL.startswith("http://"):
        return "ws://" + MASTER_URL[len("http://"):]
    return "ws://" + MASTER_URL


def api(path: str, method: str = "GET", payload: dict[str, Any] | None = None) -> dict[str, Any]:
    url = f"{MASTER_URL}{path}"
    try:
        if method == "GET":
            r = requests.get(url, timeout=20)
        elif method == "POST":
            r = requests.post(url, json=payload or {}, timeout=30)
        elif method == "PUT":
            r = requests.put(url, json=payload or {}, timeout=30)
        else:
            raise RuntimeError(f"Unsupported method: {method}")
        return r.json() if r.content else {"ok": r.ok}
    except Exception as e:
        logger.error(f"API {method} {path} failed: {e}")
        return {"ok": False, "error": str(e)}


def register() -> None:
    api("/api/node/register", "POST", {"device_id": DEVICE_ID, "name": DEVICE_NAME})


def heartbeat() -> None:
    tcp_count, _ = get_tcp_streams()
    api(
        "/api/node/heartbeat",
        "POST",
        {
            "device_id": DEVICE_ID,
            "ram": float(get_ram_usage()),
            "tcp_count": int(tcp_count),
            "watchdog_enabled": bool(state["watchdog_enabled"]),
        },
    )


def _format_start_link(raw_link: str) -> str:
    if not raw_link:
        return ""
    if raw_link.startswith("roblox://"):
        return raw_link
    if "privateServerLinkCode=" in raw_link:
        import re
        m = re.search(r"privateServerLinkCode=([\w-]+)", raw_link)
        if m:
            code = m.group(1)
            return f"roblox://placeID=920587237&linkCode={code}"
    return raw_link


def inject_roblox_cookie(device_id: str, cookie_string: str) -> tuple[bool, str]:
    if not cookie_string.strip():
        return False, "empty_cookie"

    pref_path = "/data/data/com.roblox.client/shared_prefs/com.roblox.client.v2.playerprefs.xml"
    escaped = cookie_string.replace("'", "'\\''")
    cmd = (
        "if [ -f {p} ]; then "
        "sed -i \"s#<string name=\\\".ROBLOSECURITY\\\">.*</string>#<string name=\\\".ROBLOSECURITY\\\">{c}</string>#g\" {p}; "
        "else "
        "mkdir -p /data/data/com.roblox.client/shared_prefs; "
        "echo '<map><string name=\".ROBLOSECURITY\">{c}</string></map>' > {p}; "
        "fi"
    ).format(p=pref_path, c=escaped)

    code, out, err = run_root(cmd)
    if code == 0:
        api(
            "/api/testbench/cookie_result",
            "POST",
            {
                "device_id": device_id,
                "check_type": "inject_roblox_cookie",
                "ok": True,
                "details": "Cookie write completed",
            },
        )
        return True, out or "ok"

    api(
        "/api/testbench/cookie_result",
        "POST",
        {
            "device_id": device_id,
            "check_type": "inject_roblox_cookie",
            "ok": False,
            "details": err or "failed",
        },
    )
    return False, err or "failed"


def start_roblox_with_link(link: str) -> tuple[bool, str]:
    formatted = _format_start_link(link)
    if not formatted:
        return False, "empty_link"

    cmd = f"am start -a android.intent.action.VIEW -d '{formatted}' com.roblox.client"
    code, out, err = run_root(cmd)
    if code == 0:
        state["last_start_link"] = formatted
        return True, out or "started"
    return False, err or "start_failed"


def run_console_command(cmd: str) -> tuple[bool, str]:
    code, out, err = run_root(cmd)
    if code == 0:
        return True, out or "ok"
    return False, err or out or "failed"


def read_clipboard_text() -> tuple[bool, str]:
    # Android clipboard binder call; output parsing varies by ROM.
    code, out, err = run_root("service call clipboard 2")
    if code != 0:
        return False, err or "clipboard_read_failed"
    return True, out


def write_clipboard_text(text: str) -> tuple[bool, str]:
    escaped = text.replace("'", "'\\''")
    code, out, err = run_root(f"service call clipboard 3 i32 1 s16 '{escaped}'")
    if code != 0:
        return False, err or "clipboard_write_failed"
    return True, out or "ok"


def capture_screenshot_b64() -> tuple[bool, str]:
    tmp_path = "/data/local/tmp/aegis_screen.png"
    cmd = f"screencap -p {tmp_path} && cat {tmp_path}"
    code, out, err = run_root(cmd)
    run_root(f"rm -f {tmp_path}")
    if code != 0:
        return False, err or "screenshot_failed"

    try:
        raw = out.encode("latin1", errors="ignore")
        return True, base64.b64encode(raw).decode("ascii")
    except Exception as e:
        return False, str(e)


def watchdog_tick() -> None:
    if not state["watchdog_enabled"]:
        return

    now = time.time()
    if now - state["last_watchdog_check"] < WATCHDOG_INTERVAL:
        return

    state["last_watchdog_check"] = now
    tcp_count, _ = get_tcp_streams()
    if int(tcp_count) < 5 and state["last_start_link"]:
        stop_roblox()
        time.sleep(2)
        start_roblox_with_link(state["last_start_link"])
        logger.warning(f"Watchdog restart triggered, tcp={tcp_count}")


async def handle_command(item: dict[str, Any], ws: websockets.WebSocketClientProtocol | None = None) -> None:
    cmd_id = int(item.get("id", 0))
    action = str(item.get("action", ""))

    payload_raw = item.get("payload", {})
    if isinstance(payload_raw, str):
        try:
            payload = json.loads(payload_raw or "{}")
        except Exception:
            payload = {}
    else:
        payload = payload_raw

    ok = True
    output = ""

    if action == "set_watchdog":
        state["watchdog_enabled"] = bool(payload.get("enabled", False))
        output = f"watchdog={'on' if state['watchdog_enabled'] else 'off'}"

    elif action == "inject_cookie":
        ok, output = inject_roblox_cookie(DEVICE_ID, str(payload.get("cookie", "")))

    elif action == "start":
        ok, output = start_roblox_with_link(str(payload.get("link", "")))

    elif action == "stop":
        ok = stop_roblox()
        output = "stopped" if ok else "stop_failed"

    elif action == "clipboard_read":
        ok, output = read_clipboard_text()
        if ok:
            api(f"/api/node/clipboard_result/{DEVICE_ID}", "POST", {"text": output})

    elif action == "clipboard_write":
        ok, output = write_clipboard_text(str(payload.get("text", "")))

    elif action == "screenshot":
        ok, output = capture_screenshot_b64()
        if ok:
            api(f"/api/node/screenshot_result/{DEVICE_ID}", "POST", {"image_b64": output})
            output = "screenshot_sent"

    elif action == "console_exec":
        ok, output = run_console_command(str(payload.get("cmd", "")))

    elif action == "cookie_check":
        ok, output = run_console_command("ls -la /data/data/com.roblox.client/shared_prefs")
        api(
            "/api/testbench/cookie_result",
            "POST",
            {"device_id": DEVICE_ID, "check_type": "cookie_check", "ok": ok, "details": output[:1000]},
        )

    elif action == "login_verification":
        ok, output = run_console_command("dumpsys activity top | grep -i roblox")
        api(
            "/api/testbench/cookie_result",
            "POST",
            {"device_id": DEVICE_ID, "check_type": "login_verification", "ok": ok, "details": output[:1000]},
        )

    else:
        ok = False
        output = f"unknown_action:{action}"

    api(
        "/api/node/command_result",
        "POST",
        {
            "device_id": DEVICE_ID,
            "command_id": cmd_id,
            "ok": ok,
            "output": output[:3500],
        },
    )

    if ws is not None:
        try:
            await ws.send(json.dumps({"type": "command_done", "id": cmd_id, "ok": ok}))
        except Exception:
            pass


async def poll_commands_loop() -> None:
    while True:
        try:
            data = api(f"/api/node/{DEVICE_ID}/commands", "GET")
            for item in data.get("items", []):
                await handle_command(item, None)
        except Exception as e:
            logger.error(f"poll_commands_loop error: {e}")
        await asyncio.sleep(3)


async def heartbeat_loop() -> None:
    while True:
        try:
            heartbeat()
            watchdog_tick()
        except Exception as e:
            logger.error(f"heartbeat_loop error: {e}")
        await asyncio.sleep(HEARTBEAT_SEC)


async def ws_node_loop() -> None:
    url = f"{_ws_base()}/ws/node/{DEVICE_ID}"
    while True:
        try:
            async with websockets.connect(url, ping_interval=20, ping_timeout=30, max_size=2**22) as ws:
                await ws.send(json.dumps({"type": "hello", "name": DEVICE_NAME}))
                while True:
                    raw = await ws.recv()
                    msg = json.loads(raw)
                    if msg.get("type") == "command":
                        await handle_command(msg, ws)
        except Exception as e:
            logger.warning(f"ws_node_loop reconnect: {e}")
            await asyncio.sleep(2)


async def main() -> None:
    logger.info(f"AEGIS node agent starting | device_id={DEVICE_ID} | master={MASTER_URL}")
    register()
    await asyncio.gather(
        ws_node_loop(),
        poll_commands_loop(),
        heartbeat_loop(),
    )


if __name__ == "__main__":
    asyncio.run(main())
