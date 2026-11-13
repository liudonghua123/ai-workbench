from __future__ import annotations

import asyncio
import os
import shlex
import sys
import time
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.core.ids import port_available
from app.providers.base import DeployProvider, DeployResult, DeploySpec, LogSink, register_provider
from app.providers.local.static_host import StaticPreviewHost

_PROD_TIMEOUT_SECONDS = 60


@register_provider("deploy", "local")
class LocalDeployProvider(DeployProvider):
    """Two honest modes selected by ``WB_DEPLOY_MODE``:

    * ``simulate`` (default) — emits a realistic build log, no child process. Keeps
      the demo fast, deterministic and free of hanging dev servers.
    * ``process`` — really spawns the project's run command, streams stdout, and waits
      for the port to accept connections before reporting ``running``.

    Preview (``environment="preview"``) is handled separately because a debug
    environment must be *reachable*, not merely reported. It tries the real dev
    command when dependencies exist, and always falls back to a static host rather
    than handing the UI a dead port.
    """

    name = "local"

    def __init__(self) -> None:
        self._procs: dict[str, asyncio.subprocess.Process] = {}
        self._hosts: dict[str, StaticPreviewHost] = {}

    async def deploy(self, spec: DeploySpec, sink: LogSink) -> DeployResult:
        if spec.environment == "preview":
            return await self._deploy_preview(spec, sink)
        if settings.deploy_mode == "process":
            return await self._deploy_process(spec, sink)
        return await self._deploy_simulated(spec, sink)

    # -- preview / debug --------------------------------------------------
    async def _deploy_preview(self, spec: DeploySpec, sink: LogSink) -> DeployResult:
        mode = settings.preview_mode
        if mode == "auto":
            mode = "process" if settings.deploy_mode == "process" else "static"

        await _emit(
            sink,
            f"[preview] env=preview slug={spec.project_slug} port={spec.port} mode={mode}",
        )
        await _emit(sink, f"[preview] url {spec.url}")

        if mode == "process":
            deps, hint = _deps_ready(spec.repo_path, spec.runtime)
            if spec.run_command and deps:
                await _emit(sink, f"[preview] 启动 dev server: {spec.run_command}")
                result = await self._spawn(spec, sink, timeout=settings.preview_timeout)
                if result.status == "running":
                    return result
                await _emit(sink, "[preview] dev server 未就绪，回退静态预览宿主")
            else:
                await _emit(sink, f"[preview] 跳过 dev server：{hint}")

        host = StaticPreviewHost(
            root=Path(spec.repo_path),
            port=spec.port,
            title=spec.project_name or spec.project_slug,
            slug=spec.project_slug,
            project_type=spec.project_type,
            dev_command=spec.run_command,
            health_path=spec.health_path,
            reason=_static_reason(spec),
        )
        try:
            info = await asyncio.to_thread(host.start)
        except RuntimeError as exc:
            await _emit(sink, f"[error] {exc}")
            return DeployResult(status="failed", error=str(exc))

        self._hosts[spec.deployment_id] = host
        await _emit(sink, f"[preview] {info['mode']} -> {info['root']}")
        await _emit(sink, f"[ok] 预览已就绪（{info['mode']}）: {spec.url}")
        return DeployResult(
            status="running",
            url=spec.url,
            detail={"mode": info["mode"], "port": spec.port, "root": info["root"]},
        )

    # -- simulate ---------------------------------------------------------
    async def _deploy_simulated(self, spec: DeploySpec, sink: LogSink) -> DeployResult:
        started = time.perf_counter()
        steps = [
            ("resolving provider", "local/process"),
            ("preparing workspace", str(spec.repo_path)),
            ("runtime", spec.runtime or "static"),
            ("installing dependencies", "cache hit" if spec.runtime else "n/a"),
            ("building", spec.build_command or "no-op"),
            ("running", spec.run_command or "no-op"),
            ("allocating port", str(spec.port)),
            ("binding domain", spec.url),
            ("health check", f"GET {spec.health_path} -> 200 OK"),
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

        if spec.build_command:
            ok = await self._build(spec, sink)
            if not ok:
                return DeployResult(status="failed", error="build failed")

        return await self._spawn(spec, sink, timeout=_PROD_TIMEOUT_SECONDS)

    async def _build(self, spec: DeploySpec, sink: LogSink) -> bool:
        await _emit(sink, f"[build] {spec.build_command}")
        code = await _stream_command(
            spec.build_command, spec.repo_path, {**os.environ, **spec.env}, sink
        )
        if code != 0:
            await _emit(sink, f"[error] build exited with {code}")
            return False
        await _emit(sink, "[build] ok")
        return True

    async def _spawn(self, spec: DeploySpec, sink: LogSink, *, timeout: float) -> DeployResult:
        if not spec.run_command:
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
                env={**os.environ, **spec.env},
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

        deadline = time.monotonic() + timeout
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
        host = self._hosts.pop(spec.deployment_id, None)
        if host is not None:
            await asyncio.to_thread(host.stop)
        proc = self._procs.pop(spec.deployment_id, None)
        if proc is None or proc.returncode is not None:
            return
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=10)
        except TimeoutError:  # pragma: no cover
            proc.kill()

    def runtime_info(self) -> dict[str, Any]:
        return {
            "mode": settings.deploy_mode,
            "preview_mode": settings.preview_mode,
            "running": len(self._procs),
            "previews": len(self._hosts),
        }


def _deps_ready(root: Path, runtime: str) -> tuple[bool, str]:
    """Cheap heuristic: a dev server cannot start without its dependency tree."""
    if not root.is_dir():
        return False, "工作区不存在"
    if (root / "node_modules").is_dir():
        return True, "node_modules 已就绪"
    if (root / ".venv").is_dir() or (root / "backend/.venv").is_dir():
        return True, ".venv 已就绪"
    if "static" in (runtime or ""):
        return False, "静态项目无需进程"
    return False, (
        f"依赖未安装（{root.name} 下没有 node_modules/.venv），运行 build_command 后可启用"
    )


def _static_reason(spec: DeploySpec) -> str:
    return (
        f"这是 {spec.project_type} 项目，预览宿主没有找到可直接托管的构建产物"
        f"（dist/、build/、out/），或源码仍需编译。先执行下面的命令安装依赖，"
        f"预览会切换到真实 dev server。"
    )


async def _stream_command(
    command: str, cwd: Path, env: dict[str, str], sink: LogSink
) -> int:
    cmd = command if sys.platform == "win32" else shlex.split(command)
    proc = await asyncio.create_subprocess_shell(
        cmd,
        cwd=str(cwd),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    assert proc.stdout is not None
    while True:
        line = await proc.stdout.readline()
        if not line:
            break
        await _emit(sink, line.decode(errors="replace").rstrip())
    return await proc.wait()


async def _emit(sink: LogSink, line: str) -> None:
    result = sink(line)
    if asyncio.iscoroutine(result):
        await result
