from __future__ import annotations

import asyncio
import contextlib
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .mock_conductor import MockConductor


STATIC_DIR = Path(__file__).with_name("static")


class RecipeUpdate(BaseModel):
    pattern: str = Field(min_length=1)
    brightness: int = Field(ge=0, le=192)
    params: dict[str, int | float | str] = Field(default_factory=dict)


class AssignRequest(BaseModel):
    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)


class ReplaceRequest(BaseModel):
    old_mac: str
    new_mac: str


def create_app(conductor: MockConductor | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async def ticker() -> None:
            while True:
                await asyncio.sleep(5)
                app.state.conductor.tick()
                await publish({"type": "state", "action": "tick", "state": app.state.conductor.snapshot()})

        task = asyncio.create_task(ticker())
        app.state.ticker_task = task
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    app = FastAPI(title="Do Baskets Dream Control Plane", lifespan=lifespan)
    app.state.conductor = conductor or MockConductor()
    app.state.ws_clients: set[WebSocket] = set()

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

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

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/state")
    async def get_state() -> dict[str, Any]:
        return app.state.conductor.snapshot()

    @app.get("/api/lanterns")
    async def get_lanterns() -> list[dict[str, Any]]:
        return app.state.conductor.lanterns()

    @app.post("/api/lanterns/{mac}/identify")
    async def identify(mac: str) -> dict[str, Any]:
        ack = app.state.conductor.identify(mac)
        if not ack["ok"]:
            raise HTTPException(status_code=404, detail=ack["error"])
        await publish({"type": "ack", "action": "identify", "mac": mac, "ack": ack})
        return ack

    @app.post("/api/lanterns/{mac}/assign")
    async def assign(mac: str, request: AssignRequest) -> dict[str, Any]:
        ack = app.state.conductor.assign(mac, request.x, request.y)
        if not ack["ok"]:
            raise HTTPException(status_code=404, detail=ack["error"])
        await publish({"type": "state", "action": "assign", "state": app.state.conductor.snapshot()})
        return ack

    @app.post("/api/lanterns/{mac}/forget")
    async def forget(mac: str) -> dict[str, Any]:
        ack = app.state.conductor.forget(mac)
        if not ack["ok"]:
            raise HTTPException(status_code=404, detail=ack["error"])
        await publish({"type": "state", "action": "forget", "state": app.state.conductor.snapshot()})
        return ack

    @app.post("/api/lanterns/replace")
    async def replace(request: ReplaceRequest) -> dict[str, Any]:
        ack = app.state.conductor.replace(request.old_mac, request.new_mac)
        if not ack["ok"]:
            raise HTTPException(status_code=404, detail=ack["error"])
        await publish({"type": "state", "action": "replace", "state": app.state.conductor.snapshot()})
        return ack

    @app.post("/api/show/recipe")
    async def update_recipe(request: RecipeUpdate) -> dict[str, Any]:
        ack = app.state.conductor.update_recipe(request.pattern, request.brightness, request.params)
        await publish({"type": "state", "action": "recipe", "state": app.state.conductor.snapshot()})
        return ack

    @app.post("/api/show/blackout")
    async def blackout() -> dict[str, Any]:
        ack = app.state.conductor.blackout()
        await publish({"type": "state", "action": "blackout", "state": app.state.conductor.snapshot()})
        return ack

    @app.websocket("/ws")
    async def websocket(ws: WebSocket) -> None:
        await ws.accept()
        app.state.ws_clients.add(ws)
        await ws.send_json({"type": "state", "state": app.state.conductor.snapshot(), "ts": time.time()})
        try:
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            app.state.ws_clients.discard(ws)

    return app


app = create_app()
