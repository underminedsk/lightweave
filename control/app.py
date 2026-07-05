from __future__ import annotations

import asyncio
import contextlib
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .adapters import ConductorAdapter, JsonLineSerialConductor, SerialProtocolError
from .mock_conductor import MockConductor
from .serial_transport import PySerialTransport


STATIC_DIR = Path(__file__).with_name("static")


class PatternUpdate(BaseModel):
    pattern: str = Field(min_length=1)
    brightness: int = Field(ge=0, le=192)
    params: dict[str, int | float | str] = Field(default_factory=dict)


class AssignRequest(BaseModel):
    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)


class ReplaceRequest(BaseModel):
    old_mac: str
    new_mac: str


def create_default_conductor() -> ConductorAdapter:
    mode = os.getenv("CONTROL_CONDUCTOR", "mock").strip().lower()
    if mode in {"mock", ""}:
        return MockConductor()
    if mode != "serial":
        raise RuntimeError(f"unknown CONTROL_CONDUCTOR={mode!r}")

    port = os.getenv("CONTROL_SERIAL_PORT")
    if not port:
        raise RuntimeError("CONTROL_SERIAL_PORT is required when CONTROL_CONDUCTOR=serial")
    baud = int(os.getenv("CONTROL_SERIAL_BAUD", "115200"))
    timeout_s = float(os.getenv("CONTROL_SERIAL_TIMEOUT_S", "1.5"))
    reset_on_open = os.getenv("CONTROL_SERIAL_RESET_ON_OPEN", "0").strip().lower() in {"1", "true", "yes"}
    return JsonLineSerialConductor(
        PySerialTransport(port, baud=baud, reset_on_open=reset_on_open),
        timeout_s=timeout_s,
    )


def create_app(conductor: ConductorAdapter | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async def ticker() -> None:
            while True:
                await asyncio.sleep(5)
                try:
                    await conductor_call("tick")
                    await publish({"type": "state", "action": "tick", "state": await conductor_call("snapshot")})
                except SerialProtocolError:
                    await publish({"type": "error", "action": "tick", "message": "conductor serial timeout"})

        task = asyncio.create_task(ticker())
        app.state.ticker_task = task
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    app = FastAPI(title="Do Baskets Dream Control Plane", lifespan=lifespan)
    app.state.conductor = conductor or create_default_conductor()
    app.state.conductor_lock = asyncio.Lock()
    app.state.ws_clients: set[WebSocket] = set()

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    async def conductor_call(method: str, *args: Any) -> Any:
        async with app.state.conductor_lock:
            return await asyncio.to_thread(getattr(app.state.conductor, method), *args)

    async def publish(event: dict[str, Any]) -> None:
        event = {"ts": time.time(), **event}
        dead: list[WebSocket] = []
        for ws in list(app.state.ws_clients):
            try:
                await ws.send_json(event)
            except (RuntimeError, WebSocketDisconnect):
                dead.append(ws)
        for ws in dead:
            app.state.ws_clients.discard(ws)

    async def publish_state(action: str) -> None:
        try:
            state = await conductor_call("snapshot")
        except SerialProtocolError as error:
            await publish({"type": "error", "action": action, "message": str(error)})
            return
        await publish({"type": "state", "action": action, "state": state})

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/state")
    async def get_state() -> dict[str, Any]:
        try:
            return await conductor_call("snapshot")
        except SerialProtocolError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

    @app.get("/api/lanterns")
    async def get_lanterns() -> list[dict[str, Any]]:
        try:
            return await conductor_call("lanterns")
        except SerialProtocolError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

    @app.post("/api/lanterns/{mac}/identify")
    async def identify(mac: str) -> dict[str, Any]:
        try:
            ack = await conductor_call("identify", mac)
        except SerialProtocolError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        if not ack["ok"]:
            raise HTTPException(status_code=404, detail=ack["error"])
        await publish({"type": "ack", "action": "identify", "mac": mac, "ack": ack})
        return ack

    @app.post("/api/lanterns/{mac}/assign")
    async def assign(mac: str, request: AssignRequest) -> dict[str, Any]:
        try:
            ack = await conductor_call("assign", mac, request.x, request.y)
        except SerialProtocolError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        if not ack["ok"]:
            raise HTTPException(status_code=404, detail=ack["error"])
        await publish_state("assign")
        return ack

    @app.post("/api/lanterns/{mac}/forget")
    async def forget(mac: str) -> dict[str, Any]:
        try:
            ack = await conductor_call("forget", mac)
        except SerialProtocolError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        if not ack["ok"]:
            raise HTTPException(status_code=404, detail=ack["error"])
        await publish_state("forget")
        return ack

    @app.post("/api/lanterns/replace")
    async def replace(request: ReplaceRequest) -> dict[str, Any]:
        try:
            ack = await conductor_call("replace", request.old_mac, request.new_mac)
        except SerialProtocolError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        if not ack["ok"]:
            raise HTTPException(status_code=404, detail=ack["error"])
        await publish_state("replace")
        return ack

    @app.post("/api/show/pattern")
    async def update_pattern(request: PatternUpdate) -> dict[str, Any]:
        try:
            ack = await conductor_call("update_pattern", request.pattern, request.brightness, request.params)
        except SerialProtocolError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        if not ack["ok"]:
            raise HTTPException(status_code=400, detail=ack["error"])
        await publish_state("pattern")
        return ack

    @app.post("/api/show/blackout")
    async def blackout() -> dict[str, Any]:
        try:
            ack = await conductor_call("blackout")
        except SerialProtocolError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        await publish_state("blackout")
        return ack

    @app.websocket("/ws")
    async def websocket(ws: WebSocket) -> None:
        await ws.accept()
        app.state.ws_clients.add(ws)
        try:
            state = await conductor_call("snapshot")
        except SerialProtocolError as error:
            try:
                await ws.send_json({"type": "error", "message": str(error), "ts": time.time()})
            except (RuntimeError, WebSocketDisconnect):
                app.state.ws_clients.discard(ws)
                return
        else:
            try:
                await ws.send_json({"type": "state", "state": state, "ts": time.time()})
            except (RuntimeError, WebSocketDisconnect):
                app.state.ws_clients.discard(ws)
                return
        try:
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            app.state.ws_clients.discard(ws)

    return app


app = create_app()
