from __future__ import annotations

import asyncio
import contextlib
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Response, WebSocket, WebSocketDisconnect
from fastapi import Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .adapters import ConductorAdapter, JsonLineSerialConductor, SerialProtocolError
from .mock_conductor import MockConductor
from .ota_store import OtaArtifactError, OtaArtifactStore
from .pattern_store import PatternStore, PatternStoreError
from .preview import parse_params, render_preview_data, render_preview_frames, render_preview_png, review_preview
from .serial_transport import PySerialTransport


STATIC_DIR = Path(__file__).with_name("static")
OTA_CHUNK_RETRIES = 3
OTA_STATUS_FRESH_S = 60
OTA_PROGRESS_POLL_CHUNKS = 64
OTA_CHUNK_RETRYABLE_ERRORS = {
    "bad ota chunk data",
    "ota chunk length mismatch",
    "ota chunk offset mismatch",
    "ota chunk exceeds image size",
}


class PatternUpdate(BaseModel):
    pattern: str = Field(min_length=1)
    brightness: int = Field(ge=0, le=192)
    params: dict[str, int | float | str] = Field(default_factory=dict)


class PatternLibraryEntry(BaseModel):
    name: str = Field(min_length=1)
    pattern: str = Field(min_length=1)
    brightness: int = Field(ge=0, le=192)
    params: dict[str, int | float | str] = Field(default_factory=dict)


class AssignRequest(BaseModel):
    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)


class ReplaceRequest(BaseModel):
    old_mac: str
    new_mac: str


class PowerPolicyUpdate(BaseModel):
    light_sleep_check_s: int = Field(ge=1, le=300)
    deep_sleep_check_min: int = Field(ge=1, le=1440)
    led_on_start_min: int = Field(ge=0, le=1439)
    led_on_end_min: int = Field(ge=0, le=1439)
    schedule_enabled: bool
    force_awake: bool
    current_min: int = Field(ge=0, le=1439)
    current_epoch_s: int = Field(ge=0, le=4_294_967_295)


class OtaModeUpdate(BaseModel):
    enabled: bool


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


def create_app(
    conductor: ConductorAdapter | None = None,
    ota_store: OtaArtifactStore | None = None,
    pattern_store: PatternStore | None = None,
) -> FastAPI:
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
    app.state.ota_store = ota_store or OtaArtifactStore()
    app.state.pattern_store = pattern_store or PatternStore()
    app.state.ota_install = {"running": False, "complete": False, "error": None}
    app.state.ota_mode_started_at = None
    app.state.conductor_lock = asyncio.Lock()
    app.state.ws_clients: set[WebSocket] = set()

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    def ota_install_progress(install: dict[str, Any]) -> dict[str, Any]:
        progress = dict(install)
        started_at = progress.get("started_at")
        if not isinstance(started_at, (int, float)) or started_at <= 0:
            return progress
        ended_at = progress.get("completed_at")
        now = float(ended_at) if isinstance(ended_at, (int, float)) else time.time()
        elapsed_s = max(0.0, now - float(started_at))
        bytes_sent = max(0, int(progress.get("bytes_sent") or 0))
        size = max(0, int(progress.get("size") or 0))
        bytes_per_s = bytes_sent / elapsed_s if elapsed_s > 0 else 0.0
        remaining = max(0, size - bytes_sent)
        eta_s = int(round(remaining / bytes_per_s)) if bytes_per_s > 0 and remaining > 0 else 0
        progress.update({
            "elapsed_s": int(round(elapsed_s)),
            "bytes_per_s": bytes_per_s,
            "eta_s": eta_s,
        })
        return progress

    def apply_ota_progress(progress: dict[str, Any], artifact: Any) -> list[dict[str, Any]]:
        updates: dict[str, Any] = {}
        if progress.get("ok") is True:
            written = int(progress.get("written") or 0)
            if 0 <= written <= int(artifact.size):
                updates["bytes_sent"] = max(int(app.state.ota_install.get("bytes_sent") or 0), written)
                updates["chunks_sent"] = max(
                    int(app.state.ota_install.get("chunks_sent") or 0),
                    min((written + artifact.chunk_size - 1) // artifact.chunk_size, artifact.chunks),
                )
            nodes = fresh_ota_nodes(progress.get("nodes") or [])
            if nodes:
                updates["nodes"] = nodes
        if updates:
            app.state.ota_install.update(updates)
        return list(app.state.ota_install.get("nodes") or [])

    def recovery_summary(state: dict[str, Any]) -> dict[str, Any]:
        lanterns = state.get("lanterns") or []
        ota = state.get("ota") or {}
        firmware = (state.get("summary") or {}).get("firmware") or {}
        missing = [
            {"mac": item.get("mac"), "label": item.get("label"), "reason": "not seen"}
            for item in lanterns
            if item.get("status") == "missing" and item.get("position") == "Set"
        ]
        mismatched = [
            {
                "mac": item.get("mac"),
                "label": item.get("label"),
                "reason": "firmware mismatch",
                "firmware": item.get("firmware"),
            }
            for item in lanterns
            if item.get("attention") == "Firmware mismatch"
        ]
        failed_ota = []
        for node in ota.get("nodes") or []:
            if node.get("phase") != "failed":
                continue
            mac = node.get("mac")
            lantern = next((item for item in lanterns if item.get("mac") == mac), None)
            failed_ota.append({
                "mac": mac,
                "label": (lantern or {}).get("label") or mac or "node",
                "reason": node.get("error") or "ota failed",
                "phase": node.get("phase"),
            })
        ready = not missing and not mismatched and not failed_ota and firmware.get("consistent") is True
        if failed_ota:
            status = "ota_failed"
            title = "Firmware update needs recovery"
            action = "Keep maintenance mode open. Power-cycle the listed lanterns, wait for them to check in, then rerun the same staged firmware."
        elif mismatched:
            status = "mixed_firmware"
            title = "Mixed firmware detected"
            action = "Enter maintenance mode and reinstall the staged firmware across the whole field. Do not run the show with mixed firmware."
        elif missing:
            status = "missing_nodes"
            title = "Placed lanterns are missing"
            action = "Wake or power-cycle the listed lanterns. If a lantern is physically gone, replace it with an awake unpositioned spare."
        else:
            status = "ready"
            title = "No recovery needed"
            action = "Field firmware is consistent and all placed lanterns are healthy."
        return {
            "status": status,
            "ready": ready,
            "title": title,
            "action": action,
            "missing": missing,
            "mismatched": mismatched,
            "failed_ota": failed_ota,
        }

    def enrich_state(state: dict[str, Any]) -> dict[str, Any]:
        state["recovery"] = recovery_summary(state)
        return state

    def ota_ready_for_install(state: dict[str, Any]) -> bool:
        ota = state.get("ota") or {}
        if ota.get("ready") is True:
            return True
        recovery = state.get("recovery") or recovery_summary(state)
        if recovery.get("status") not in {"mixed_firmware", "ota_failed"}:
            return False
        return (
            ota.get("enabled") is True
            and int(ota.get("expected") or 0) > 0
            and int(ota.get("missing") or 0) == 0
        )

    async def conductor_call(method: str, *args: Any) -> Any:
        async with app.state.conductor_lock:
            result = await asyncio.to_thread(getattr(app.state.conductor, method), *args)
        if method == "snapshot" and isinstance(result, dict):
            return enrich_state(result)
        return result

    async def pattern_store_call(method: str, *args: Any) -> Any:
        return await asyncio.to_thread(getattr(app.state.pattern_store, method), *args)

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

    async def infer_ota_complete_nodes(size: int, crc32: int) -> list[dict[str, Any]]:
        for _ in range(4):
            await asyncio.sleep(3)
            try:
                state = await conductor_call("snapshot")
            except SerialProtocolError:
                continue
            summary = state.get("summary") or {}
            firmware = summary.get("firmware") or {}
            if summary.get("alive") != summary.get("total"):
                continue
            if firmware.get("consistent") is not True:
                continue
            nodes = []
            for lantern in state.get("lanterns") or []:
                if lantern.get("status") != "alive" or lantern.get("position") != "Set":
                    continue
                nodes.append({
                    "mac": lantern.get("mac"),
                    "phase": "complete",
                    "error": "none",
                    "offset": size,
                    "crc32": crc32,
                    "source": "post_reboot_state",
                })
            if nodes:
                return nodes
        return []

    def expected_ota_lanterns(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return {
            str(lantern.get("mac")): lantern
            for lantern in state.get("lanterns") or []
            if lantern.get("status") == "alive"
            and lantern.get("position") == "Set"
            and lantern.get("mac")
        }

    def expected_ota_macs(state: dict[str, Any]) -> set[str]:
        return set(expected_ota_lanterns(state))

    def append_unverified_ota_failures(
        nodes: list[dict[str, Any]],
        expected: dict[str, dict[str, Any]],
        verified_macs: set[str],
    ) -> list[dict[str, Any]]:
        existing_macs = {str(node.get("mac")) for node in nodes if node.get("mac")}
        augmented = list(nodes)
        for mac, lantern in expected.items():
            if mac in verified_macs or mac in existing_macs:
                continue
            augmented.append({
                "mac": mac,
                "label": lantern.get("label") or mac,
                "phase": "failed",
                "error": "post-reboot verification missing",
                "offset": 0,
                "crc32": 0,
                "source": "post_reboot_verification",
            })
        return augmented

    async def wait_for_maintenance_settle(state: dict[str, Any]) -> None:
        started_at = app.state.ota_mode_started_at
        if not isinstance(started_at, (int, float)):
            return
        power = state.get("power") or {}
        check_s = int(power.get("light_sleep_check_s") or 4)
        settle_s = max(2, min(check_s + 2, 30))
        elapsed = time.time() - started_at
        if elapsed < settle_s:
            await asyncio.sleep(settle_s - elapsed)

    def fresh_ota_nodes(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        fresh = []
        for node in nodes:
            age = node.get("last_seen_s")
            if isinstance(age, (int, float)) and age > OTA_STATUS_FRESH_S:
                continue
            fresh.append(node)
        return fresh

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

    @app.get("/api/patterns")
    async def list_patterns() -> dict[str, Any]:
        try:
            return {"patterns": await pattern_store_call("list")}
        except PatternStoreError as error:
            raise HTTPException(status_code=500, detail=str(error)) from error

    @app.post("/api/patterns")
    async def create_pattern(request: PatternLibraryEntry) -> dict[str, Any]:
        try:
            pattern = await pattern_store_call(
                "create",
                request.name,
                request.pattern,
                request.brightness,
                request.params,
            )
        except PatternStoreError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return {"ok": True, "pattern": pattern}

    @app.get("/api/patterns/{pattern_id}")
    async def get_pattern(pattern_id: str) -> dict[str, Any]:
        try:
            pattern = await pattern_store_call("get", pattern_id)
        except PatternStoreError as error:
            raise HTTPException(status_code=500, detail=str(error)) from error
        if not pattern:
            raise HTTPException(status_code=404, detail="unknown pattern")
        return {"pattern": pattern}

    @app.put("/api/patterns/{pattern_id}")
    async def update_pattern_library_entry(pattern_id: str, request: PatternLibraryEntry) -> dict[str, Any]:
        try:
            pattern = await pattern_store_call(
                "update",
                pattern_id,
                request.name,
                request.pattern,
                request.brightness,
                request.params,
            )
        except PatternStoreError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        if not pattern:
            raise HTTPException(status_code=404, detail="unknown pattern")
        return {"ok": True, "pattern": pattern}

    @app.delete("/api/patterns/{pattern_id}")
    async def delete_pattern(pattern_id: str) -> dict[str, Any]:
        try:
            deleted = await pattern_store_call("delete", pattern_id)
        except PatternStoreError as error:
            raise HTTPException(status_code=500, detail=str(error)) from error
        if not deleted:
            raise HTTPException(status_code=404, detail="unknown pattern")
        return {"ok": True, "message": "pattern deleted"}

    @app.post("/api/patterns/{pattern_id}/broadcast")
    async def broadcast_pattern_library_entry(pattern_id: str) -> dict[str, Any]:
        try:
            pattern = await pattern_store_call("get", pattern_id)
        except PatternStoreError as error:
            raise HTTPException(status_code=500, detail=str(error)) from error
        if not pattern:
            raise HTTPException(status_code=404, detail="unknown pattern")
        try:
            ack = await conductor_call(
                "update_pattern",
                pattern["pattern"],
                pattern["brightness"],
                pattern["params"],
            )
        except SerialProtocolError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        if not ack["ok"]:
            raise HTTPException(status_code=400, detail=ack["error"])
        await publish_state("pattern")
        return {"ok": True, "message": ack.get("message", "pattern broadcast"), "pattern": pattern, "ack": ack}

    @app.get("/preview")
    async def preview(
        pattern: str,
        brightness: int = Query(default=48, ge=0, le=192),
        params: str | None = None,
        t: int = Query(default=0, ge=0),
        width: int = Query(default=640, ge=80, le=2000),
        height: int = Query(default=480, ge=80, le=2000),
        hue: int | None = None,
        saturation: int | None = None,
        period: int | None = None,
        spatial: int | None = None,
        wavelength: int | None = None,
    ) -> Response:
        try:
            state = await conductor_call("snapshot")
            decoded_params = parse_params(params, {
                "hue": hue,
                "saturation": saturation,
                "period": period,
                "spatial": spatial,
                "wavelength": wavelength,
            })
            png = await asyncio.to_thread(
                render_preview_png,
                state,
                pattern,
                brightness,
                decoded_params,
                t,
                width,
                height,
            )
        except SerialProtocolError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return Response(content=png, media_type="image/png")

    @app.get("/preview.json")
    async def preview_json(
        pattern: str,
        brightness: int = Query(default=48, ge=0, le=192),
        params: str | None = None,
        t: int = Query(default=0, ge=0),
        hue: int | None = None,
        saturation: int | None = None,
        period: int | None = None,
        spatial: int | None = None,
        wavelength: int | None = None,
    ) -> dict[str, Any]:
        try:
            state = await conductor_call("snapshot")
            decoded_params = parse_params(params, {
                "hue": hue,
                "saturation": saturation,
                "period": period,
                "spatial": spatial,
                "wavelength": wavelength,
            })
            return await asyncio.to_thread(render_preview_data, state, pattern, brightness, decoded_params, t)
        except SerialProtocolError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.get("/preview/frames.json")
    async def preview_frames_json(
        pattern: str,
        brightness: int = Query(default=48, ge=0, le=192),
        params: str | None = None,
        duration_ms: int = Query(default=8000, ge=500, le=60000),
        fps: int = Query(default=4, ge=1, le=24),
        hue: int | None = None,
        saturation: int | None = None,
        period: int | None = None,
        spatial: int | None = None,
        wavelength: int | None = None,
    ) -> dict[str, Any]:
        try:
            state = await conductor_call("snapshot")
            decoded_params = parse_params(params, {
                "hue": hue,
                "saturation": saturation,
                "period": period,
                "spatial": spatial,
                "wavelength": wavelength,
            })
            return await asyncio.to_thread(
                render_preview_frames,
                state,
                pattern,
                brightness,
                decoded_params,
                duration_ms,
                fps,
            )
        except SerialProtocolError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.get("/review")
    async def review(
        pattern: str,
        brightness: int = Query(default=48, ge=0, le=192),
        params: str | None = None,
        duration_ms: int = Query(default=8000, ge=500, le=60000),
        fps: int = Query(default=4, ge=1, le=24),
        hue: int | None = None,
        saturation: int | None = None,
        period: int | None = None,
        spatial: int | None = None,
        wavelength: int | None = None,
    ) -> dict[str, Any]:
        try:
            state = await conductor_call("snapshot")
            decoded_params = parse_params(params, {
                "hue": hue,
                "saturation": saturation,
                "period": period,
                "spatial": spatial,
                "wavelength": wavelength,
            })
            return await asyncio.to_thread(
                review_preview,
                state,
                pattern,
                brightness,
                decoded_params,
                duration_ms,
                fps,
            )
        except SerialProtocolError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.get("/api/patterns/{pattern_id}/preview")
    async def preview_pattern_library_entry(
        pattern_id: str,
        t: int = Query(default=0, ge=0),
        width: int = Query(default=640, ge=80, le=2000),
        height: int = Query(default=480, ge=80, le=2000),
    ) -> Response:
        try:
            item = await pattern_store_call("get", pattern_id)
            if not item:
                raise HTTPException(status_code=404, detail="unknown pattern")
            state = await conductor_call("snapshot")
            png = await asyncio.to_thread(
                render_preview_png,
                state,
                item["pattern"],
                item["brightness"],
                item["params"],
                t,
                width,
                height,
            )
        except HTTPException:
            raise
        except SerialProtocolError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        except PatternStoreError as error:
            raise HTTPException(status_code=500, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return Response(content=png, media_type="image/png")

    @app.get("/api/patterns/{pattern_id}/preview.json")
    async def preview_pattern_library_entry_json(
        pattern_id: str,
        t: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        try:
            item = await pattern_store_call("get", pattern_id)
            if not item:
                raise HTTPException(status_code=404, detail="unknown pattern")
            state = await conductor_call("snapshot")
            return await asyncio.to_thread(
                render_preview_data,
                state,
                item["pattern"],
                item["brightness"],
                item["params"],
                t,
            )
        except HTTPException:
            raise
        except SerialProtocolError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        except PatternStoreError as error:
            raise HTTPException(status_code=500, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.get("/api/patterns/{pattern_id}/preview/frames.json")
    async def preview_pattern_library_entry_frames_json(
        pattern_id: str,
        duration_ms: int = Query(default=8000, ge=500, le=60000),
        fps: int = Query(default=4, ge=1, le=24),
    ) -> dict[str, Any]:
        try:
            item = await pattern_store_call("get", pattern_id)
            if not item:
                raise HTTPException(status_code=404, detail="unknown pattern")
            state = await conductor_call("snapshot")
            return await asyncio.to_thread(
                render_preview_frames,
                state,
                item["pattern"],
                item["brightness"],
                item["params"],
                duration_ms,
                fps,
            )
        except HTTPException:
            raise
        except SerialProtocolError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        except PatternStoreError as error:
            raise HTTPException(status_code=500, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.get("/api/patterns/{pattern_id}/review")
    async def review_pattern_library_entry(
        pattern_id: str,
        duration_ms: int = Query(default=8000, ge=500, le=60000),
        fps: int = Query(default=4, ge=1, le=24),
    ) -> dict[str, Any]:
        try:
            item = await pattern_store_call("get", pattern_id)
            if not item:
                raise HTTPException(status_code=404, detail="unknown pattern")
            state = await conductor_call("snapshot")
            return await asyncio.to_thread(
                review_preview,
                state,
                item["pattern"],
                item["brightness"],
                item["params"],
                duration_ms,
                fps,
            )
        except HTTPException:
            raise
        except SerialProtocolError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        except PatternStoreError as error:
            raise HTTPException(status_code=500, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

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

    @app.post("/api/operations/power-policy")
    async def update_power_policy(request: PowerPolicyUpdate) -> dict[str, Any]:
        try:
            ack = await conductor_call("update_power_policy", request.model_dump())
        except SerialProtocolError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        if not ack["ok"]:
            raise HTTPException(status_code=400, detail=ack["error"])
        await publish_state("power-policy")
        return ack

    @app.post("/api/operations/ota-mode")
    async def update_ota_mode(request: OtaModeUpdate) -> dict[str, Any]:
        try:
            ack = await conductor_call("set_ota_mode", request.enabled)
        except SerialProtocolError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        if not ack["ok"]:
            raise HTTPException(status_code=400, detail=ack["error"])
        app.state.ota_mode_started_at = time.time() if request.enabled else None
        await publish_state("ota-mode")
        return ack

    @app.get("/api/operations/ota-artifact")
    async def get_ota_artifact() -> dict[str, Any]:
        return {"artifact": app.state.ota_store.current()}

    @app.put("/api/operations/ota-artifact")
    async def stage_ota_artifact(request: Request, filename: str = "firmware.bin") -> dict[str, Any]:
        data = await request.body()
        try:
            artifact = await asyncio.to_thread(app.state.ota_store.stage, filename, data)
        except OtaArtifactError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        await publish({"type": "ack", "action": "ota-artifact", "artifact": artifact})
        return {"ok": True, "message": "firmware staged", "artifact": artifact}

    @app.get("/api/operations/ota-install")
    async def get_ota_install() -> dict[str, Any]:
        install = app.state.ota_install
        if install.get("complete") is True and not install.get("nodes") and install.get("size"):
            nodes = await infer_ota_complete_nodes(int(install["size"]), int(install.get("crc32") or 0))
            if nodes:
                install.update({"nodes": nodes})
        return {"install": ota_install_progress(app.state.ota_install)}

    @app.post("/api/operations/ota-install")
    async def install_ota_artifact() -> dict[str, Any]:
        artifact = app.state.ota_store.artifact()
        if artifact is None:
            raise HTTPException(status_code=400, detail="no firmware staged")
        try:
            state = await conductor_call("snapshot")
        except SerialProtocolError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        ota = state.get("ota") or {}
        if not ota_ready_for_install(state):
            blockers = ", ".join(ota.get("blocked") or ["field is not OTA-ready"])
            raise HTTPException(status_code=400, detail=f"OTA not ready: {blockers}")
        expected_lanterns = expected_ota_lanterns(state)
        expected_macs = set(expected_lanterns)
        await wait_for_maintenance_settle(state)

        data = await asyncio.to_thread(artifact.path.read_bytes)
        app.state.ota_install = {
            "running": True,
            "complete": False,
            "error": None,
            "filename": artifact.filename,
            "size": artifact.size,
            "crc32": artifact.crc32,
            "bytes_sent": 0,
            "chunks_sent": 0,
            "chunks_total": artifact.chunks,
            "started_at": time.time(),
        }
        ack: dict[str, Any] | None = None
        try:
            async with app.state.conductor_lock:
                conductor = app.state.conductor
                ack = await asyncio.to_thread(conductor.ota_begin, artifact.size, artifact.crc32)
                if not ack["ok"]:
                    app.state.ota_install.update({"running": False, "error": ack["error"]})
                    raise HTTPException(status_code=400, detail=ack["error"])
                offset = 0
                while offset < len(data):
                    chunk = data[offset : offset + artifact.chunk_size]
                    last_error: SerialProtocolError | None = None
                    for attempt in range(OTA_CHUNK_RETRIES + 1):
                        try:
                            ack = await asyncio.to_thread(conductor.ota_chunk, offset, chunk)
                        except SerialProtocolError as error:
                            last_error = error
                            app.state.ota_install.update({
                                "last_retry": {
                                    "offset": offset,
                                    "attempt": attempt + 1,
                                    "error": str(error),
                                }
                            })
                            if attempt >= OTA_CHUNK_RETRIES:
                                raise
                            continue
                        if not ack["ok"] and ack.get("error") in OTA_CHUNK_RETRYABLE_ERRORS:
                            app.state.ota_install.update({
                                "last_retry": {
                                    "offset": offset,
                                    "attempt": attempt + 1,
                                    "error": ack["error"],
                                }
                            })
                            progress = await asyncio.to_thread(conductor.ota_progress)
                            written = int(progress.get("written") or 0) if progress.get("ok") else 0
                            if (
                                progress.get("ok")
                                and progress.get("active") is True
                                and 0 <= written <= len(data)
                                and written != offset
                            ):
                                if written != len(data) and written % artifact.chunk_size != 0:
                                    error = f"ota write offset is not chunk-aligned: {written}"
                                    app.state.ota_install.update({"running": False, "error": error})
                                    raise HTTPException(status_code=503, detail=error)
                                offset = written
                                app.state.ota_install.update({
                                    "bytes_sent": offset,
                                    "chunks_sent": min((offset + artifact.chunk_size - 1) // artifact.chunk_size, artifact.chunks),
                                    "last_retry": {
                                        "offset": offset,
                                        "attempt": attempt + 1,
                                        "error": f"resynced after {ack['error']}",
                                    },
                                })
                                break
                            if attempt < OTA_CHUNK_RETRIES:
                                continue
                        break
                    else:
                        assert last_error is not None
                        raise last_error
                    if ack.get("ok") is not True and app.state.ota_install.get("bytes_sent") == offset:
                        continue
                    if not ack["ok"]:
                        app.state.ota_install.update({"running": False, "error": ack["error"]})
                        raise HTTPException(status_code=400, detail=ack["error"])
                    app.state.ota_install.update({
                        "bytes_sent": offset + len(chunk),
                        "chunks_sent": (offset // artifact.chunk_size) + 1,
                    })
                    offset += len(chunk)
                    chunks_sent = int(app.state.ota_install.get("chunks_sent") or 0)
                    if chunks_sent == artifact.chunks or chunks_sent % OTA_PROGRESS_POLL_CHUNKS == 0:
                        progress = await asyncio.to_thread(conductor.ota_progress)
                        nodes = apply_ota_progress(progress, artifact)
                        if any(node.get("phase") == "failed" for node in nodes):
                            error = "ota node failure"
                            app.state.ota_install.update({
                                "running": False,
                                "complete": False,
                                "error": error,
                                "nodes": nodes,
                                "completed_at": time.time(),
                            })
                            raise HTTPException(status_code=400, detail=error)
                try:
                    ack = await asyncio.to_thread(conductor.ota_end)
                except SerialProtocolError as error:
                    if int(app.state.ota_install.get("bytes_sent") or 0) < artifact.size:
                        raise
                    app.state.ota_install.update({
                        "last_finalize_error": str(error),
                    })
                    ack = {
                        "ok": True,
                        "message": "ota end ack timed out; verifying post-reboot state",
                        "nodes": [],
                        "post_reboot_verify": True,
                    }
        except asyncio.CancelledError:
            app.state.ota_install.update({"running": False, "error": "ota install cancelled"})
            raise
        except HTTPException:
            raise
        except SerialProtocolError as error:
            app.state.ota_install.update({"running": False, "error": str(error)})
            raise HTTPException(status_code=503, detail=str(error)) from error
        assert ack is not None
        if not ack["ok"]:
            app.state.ota_install.update({"running": False, "error": ack["error"]})
            raise HTTPException(status_code=400, detail=ack["error"])
        nodes = [] if ack.get("post_reboot_verify") else fresh_ota_nodes(
            ack.get("nodes") or app.state.ota_install.get("nodes") or []
        )
        failed_nodes = [node for node in nodes if node.get("phase") == "failed"]
        if failed_nodes:
            error = "ota node failure"
            app.state.ota_install.update({
                "running": False,
                "complete": False,
                "error": error,
                "nodes": nodes,
                "completed_at": time.time(),
            })
            raise HTTPException(status_code=400, detail=error)
        completed_macs = {
            str(node.get("mac"))
            for node in nodes
            if node.get("phase") == "complete" and node.get("mac")
        }
        if not expected_macs or not expected_macs.issubset(completed_macs):
            inferred_nodes = await infer_ota_complete_nodes(artifact.size, artifact.crc32)
            if inferred_nodes:
                nodes = inferred_nodes
        verified_macs = {
            str(node.get("mac"))
            for node in nodes
            if node.get("phase") == "complete" and node.get("mac")
        }
        if expected_macs and not expected_macs.issubset(verified_macs):
            error = "ota post-reboot verification failed"
            nodes = append_unverified_ota_failures(nodes, expected_lanterns, verified_macs)
            app.state.ota_install.update({
                "running": False,
                "complete": False,
                "error": error,
                "nodes": nodes,
                "completed_at": time.time(),
            })
            raise HTTPException(status_code=503, detail=error)
        app.state.ota_install.update({
            "running": False,
            "complete": True,
            "error": None,
            "nodes": nodes,
            "completed_at": time.time(),
        })
        await publish({"type": "ack", "action": "ota-install", "artifact": artifact.as_dict(), "ack": ack})
        return {"ok": True, "message": ack.get("message", "ota install complete"), "artifact": artifact.as_dict()}

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
