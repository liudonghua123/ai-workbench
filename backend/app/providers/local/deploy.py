from __future__ import annotations

import asyncio
import shlex
import sys
import time
from typing import Any

from app.core.config import settings
from app.core.ids import port_available
from app.providers.base import DeployProvider, DeployResult, DeploySpec, LogSink, register_provider

_TIMEOUT_SECONDS = 60


@register_provider("deploy", "local")
class LocalDeployProvider(DeployProvider):
    """Two honest modes selected by ``WB_DEPLOY_MODE``:

    * ``simulate`` (default) — emits a realistic build log, no child process. Keeps
      the demo fast, deterministic and free of hanging dev servers.
    * ``process`` — really spawns the project's run command, streams stdout, and waits
      for the port to accept connections before reporting ``running``.
    """

    name = "local"

    def __init__(self) -> None:
        self._procs: dict[str, asyncio.subprocess.Process] = {}

    async def deploy(self, spec: DeploySpec, sink: LogSink) -> DeployResult:
        if settings.deploy_mode == "process":
            return await self._deploy_process(spec, sink)
        return await self._deploy_simulated(spec, sink)

    # -- simulate ---------------------------------------------------------
    async def _deploy_simulated(self, spec: DeploySpec, sink: LogSink) -> DeployResult:
        started = time.perf_counter()
        steps = [
            ("resolving provider", "local/process"),
            ("preparing workspace", str(spec.repo_path)),
            ("runtime", spec.runtime or "static"),
            ("installing dependencies", "cache hit" if spec.runtime else "n/a"),
            ("building", spec.run_command or "no-op"),
            ("allocating port", str(spec.port)),
            ("binding domain", spec.url),
            ("health check", "GET / -> 200 OK"),
        ]
        for label, detail in steps:
            await _emit(sink, f"[{label}] {detail}")
            await asyncio.sleep(0.35)
        elapsed = round(time.perf_counter() - started, 2)
        await _emit(sink, f"[ok] deployment {spec.deployment_id} is live in {elapsed}s")
        return DeployResult(
            status="running",
            url=spec.url,
            detail={"mode": "simulate", "elapsed": elapsed, "port": spec.port},
        )

    # -- real process -----------------------------------------------------
    async def _deploy_process(self, spec: DeploySpec, sink: LogSink) -> DeployResult:
        if not spec.run_command:
            await _emit(sink, "[error] 项目没有配置 run_command，无法以 process 模式部署")
            return DeployResult(status="failed", error="missing run_command")

        if not port_available(spec.port):
            await _emit(sink, f"[error] 端口 {spec.port} 已被占用")
            return DeployResult(status="failed", error=f"port {spec.port} busy")

        await _emit(sink, f"[run] {spec.run_command}  (cwd={spec.repo_path})")
        cmd = spec.run_command if sys.platform == "win32" else shlex.split(spec.run_command)
        try:
            proc = await asyncio.create_subprocess_shell(
                cmd,
                cwd=str(spec.repo_path),
                env={**__import__("os").environ, **spec.env},
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except OSError as exc:  # pragma: no cover - platform specific
            await _emit(sink, f"[error] 启动失败: {exc}")
            return DeployResult(status="failed", error=str(exc))

        self._procs[spec.deployment_id] = proc

        async def pump() -> None:
            assert proc.stdout is not None
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                await _emit(sink, line.decode(errors="replace").rstrip())

        pump_task = asyncio.create_task(pump())

        deadline = time.monotonic() + _TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if proc.returncode is not None:
                await pump_task
                return DeployResult(
                    status="failed",
                    error=f"process exited with {proc.returncode}",
                )
            if not port_available(spec.port):
                await _emit(sink, f"[ok] port {spec.port} is up -> {spec.url}")
                return DeployResult(
                    status="running",
                    url=spec.url,
                    detail={"mode": "process", "pid": proc.pid, "port": spec.port},
                )
            await asyncio.sleep(0.5)

        await _emit(sink, "[error] health check timeout")
        return DeployResult(status="failed", error="timeout")

    async def stop(self, spec: DeploySpec) -> None:
        proc = self._procs.pop(spec.deployment_id, None)
        if proc is None or proc.returncode is not None:
            return
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=10)
        except TimeoutError:  # pragma: no cover
            proc.kill()

    def runtime_info(self) -> dict[str, Any]:
        return {"mode": settings.deploy_mode, "running": len(self._procs)}


async def _emit(sink: LogSink, line: str) -> None:
    result = sink(line)
    if asyncio.iscoroutine(result):
        await result
