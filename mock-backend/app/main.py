"""FastAPI transport for the local, entirely simulated backend."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
import hashlib
import json
import os
import time
from typing import Annotated

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .models import CommandRequest, CommandResponse, Snapshot, SpeedRequest
from .simulation import Simulation


def create_app(*, time_scale: float = 1.0) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.simulation = Simulation(time_scale=time_scale)
        app.state.idempotency = {}
        app.state.request_lock = asyncio.Lock()
        yield
        pending = [entry["task"] for entry in app.state.idempotency.values() if not entry["task"].done()]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        await app.state.simulation.close()
        app.state.idempotency.clear()

    app = FastAPI(title="Kleos Mock Backend", version="0.1.0", description="All engineering, purchasing, printing, worker and connector operations are simulated. No external service is called.", lifespan=lifespan)
    ui_port = os.environ.get("KLEOS9_UI_PORT", "5180")
    origins = [f"http://127.0.0.1:{ui_port}", f"http://localhost:{ui_port}"]
    origins.extend(origin.strip() for origin in os.environ.get("KLEOS9_UI_ORIGINS", "").split(",") if origin.strip())
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=True, allow_methods=["GET", "POST", "OPTIONS"], allow_headers=["Content-Type", "Idempotency-Key"])

    @app.middleware("http")
    async def local_request_limits(request: Request, call_next):
        if request.method == "POST":
            if not request.headers.get("content-type", "").lower().startswith("application/json"):
                return JSONResponse(status_code=415, content={"message": "Send a JSON request"})
            if request.headers.get("origin") and request.headers["origin"] not in origins:
                return JSONResponse(status_code=403, content={"message": "Origin is not allowed"})
            if len(await request.body()) > 1024 * 1024:
                return JSONResponse(status_code=413, content={"message": "Request is too large"})
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request: Request, error: RequestValidationError):
        errors = [{"loc": list(item["loc"]), "msg": item["msg"], "type": item["type"]} for item in error.errors()]
        return JSONResponse(status_code=422, content={"message": errors[0]["msg"] if errors else "Invalid request", "errors": errors})

    @app.exception_handler(HTTPException)
    async def http_error(_request: Request, error: HTTPException):
        return JSONResponse(status_code=error.status_code, content={"message": str(error.detail)})

    @app.get("/api/health")
    async def health():
        return {"status": "ok", "backend": "fastapi", "mode": "mock", "realIntegrations": False}

    @app.get("/api/state", response_model=Snapshot)
    async def state(request: Request):
        return request.app.state.simulation.snapshot()

    @app.post("/api/commands", response_model=CommandResponse)
    async def command(request: Request, command: CommandRequest, idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)]):
        fingerprint = hashlib.sha256(json.dumps(command.model_dump(), sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        cache = request.app.state.idempotency
        async with request.app.state.request_lock:
            for key, item in list(cache.items()):
                if time.monotonic() - item["created"] > 3600 and item["task"].done():
                    del cache[key]
            entry = cache.get(idempotency_key)
            if entry and entry["fingerprint"] != fingerprint:
                raise HTTPException(status_code=409, detail="Idempotency key was already used for a different command")
            if entry is None:
                if len(cache) >= 1000:
                    raise HTTPException(status_code=503, detail="Request cache is full; reset the local demo or retry later")
                task = asyncio.create_task(request.app.state.simulation.execute(command.type, command.payload))
                entry = {"fingerprint": fingerprint, "task": task, "created": time.monotonic()}
                cache[idempotency_key] = entry
        try:
            return deepcopy(await asyncio.shield(entry["task"]))
        except ValueError as error:
            async with request.app.state.request_lock:
                if cache.get(idempotency_key) is entry:
                    del cache[idempotency_key]
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.post("/api/demo/reset", response_model=Snapshot)
    async def reset(request: Request):
        snapshot = await request.app.state.simulation.reset()
        async with request.app.state.request_lock:
            request.app.state.idempotency.clear()
        return snapshot

    @app.post("/api/demo/speed", response_model=Snapshot)
    async def speed(request: Request, payload: SpeedRequest):
        return await request.app.state.simulation.set_speed(payload.speed)

    return app


app = create_app()
