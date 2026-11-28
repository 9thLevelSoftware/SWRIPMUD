"""E2E test registry, isolated execution context, sanitizer assertions, and runner engine."""

from __future__ import annotations

import os
import shutil
import time
import traceback
import uuid
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Literal

from .static_scanners import run_all_scanners
from .telnet_client import TelnetClient
from .wsl_env import (
    BuildResult,
    SanitizerReport,
    ShutdownMetrics,
    SwripProcess,
    build_server,
    seed_des_legacy_fixture,
    seed_immortal_fixture,
    stage_workspace,
)

TestStatus = Literal["PASS", "FAIL", "SKIP", "ERROR"]


class SkippedTest(Exception):
    """Raised inside a test case to mark it SKIP cleanly."""


@dataclass
class TestCaseSpec:
    id: str
    tier: int
    feature: str
    title: str
    fn: Callable[[E2EContext], Any]
    requires_server: bool = True
    fresh_server: bool = False


@dataclass
class TestResult:
    id: str
    tier: int
    feature: str
    title: str
    status: TestStatus
    duration_s: float
    detail: str = ""


_REGISTRY: dict[str, TestCaseSpec] = {}


def test_case(
    id: str,
    tier: int,
    feature: str,
    title: str,
    *,
    requires_server: bool = True,
    fresh_server: bool = False,
) -> Callable[[Callable[[E2EContext], Any]], Callable[[E2EContext], Any]]:
    """Decorator registering an E2E test case into the global suite registry."""

    def _decorator(fn: Callable[[E2EContext], Any]) -> Callable[[E2EContext], Any]:
        if id in _REGISTRY:
            raise ValueError(f"Duplicate E2E case ID: {id}")
        _REGISTRY[id] = TestCaseSpec(
            id=id,
            tier=tier,
            feature=feature,
            title=title,
            fn=fn,
            requires_server=requires_server,
            fresh_server=fresh_server,
        )
        return fn

    return _decorator


def get_registered_tests() -> list[TestCaseSpec]:
    return sorted(_REGISTRY.values(), key=lambda s: (s.tier, s.feature, s.id))


def clear_registry() -> None:
    _REGISTRY.clear()


class E2EContext:
    """Per-run execution context providing isolated workspaces, servers, and Telnet clients."""

    def __init__(
        self,
        repo_path: str | Path = "/mnt/d/SWRIPMUD",
        mode: str = "diagnostic",
        keep_workspaces: bool = False,
        tag_prefix: str = "",
    ) -> None:
        self.repo_path = Path(repo_path)
        self.mode = mode
        self.keep_workspaces = keep_workspaces
        self.tag_prefix = tag_prefix or f"r{int(time.time())}-p{os.getpid()}-{uuid.uuid4().hex[:6]}"

        self._seq = 0
        self._workspaces: list[Path] = []
        self._processes: list[SwripProcess] = []
        self._clients: list[TelnetClient] = []

        self._compiled_binary: Path | None = None
        self.shared_build_result: BuildResult | None = None
        self._shared_server: SwripProcess | None = None
        self._shared_work_dir: Path | None = None
        self.shared_shutdown_metrics: ShutdownMetrics | None = None
        self._shared_server_shut_down: bool = False
        self._process_sanitizer_reports: dict[int, SanitizerReport] = {}
        self._unexpected_crashes: list[str] = []
        self._recorded_crash_proc_ids: set[int] = set()
        self.aggregated_sanitizer_report: SanitizerReport = SanitizerReport(clean=True)
        self.process_count_audited: int = 0
        self.fixture_shutdowns: list[dict[str, Any]] = []
        self.cleanup_errors: list[str] = []

    @staticmethod
    def _merge_error_lists(prev_items: list[str], curr_items: list[str]) -> list[str]:
        if not prev_items:
            return list(curr_items)
        if not curr_items:
            return list(prev_items)
        prev_counts = Counter(prev_items)
        curr_counts = Counter(curr_items)
        target_counts = {
            k: max(prev_counts.get(k, 0), curr_counts.get(k, 0))
            for k in dict.fromkeys([*prev_items, *curr_items])
        }
        merged: list[str] = []
        seen: Counter[str] = Counter()
        for item in [*prev_items, *curr_items]:
            if seen[item] < target_counts[item]:
                merged.append(item)
                seen[item] += 1
        return merged

    @classmethod
    def _merge_same_process_report(
        cls,
        prev: SanitizerReport | None,
        curr: SanitizerReport,
    ) -> SanitizerReport:
        if prev is None:
            return curr
        asan = cls._merge_error_lists(prev.asan_errors, curr.asan_errors)
        ubsan = cls._merge_error_lists(prev.ubsan_errors, curr.ubsan_errors)
        lsan = cls._merge_error_lists(prev.lsan_leaks, curr.lsan_leaks)
        leaked_bytes = max(prev.leaked_bytes, curr.leaked_bytes)
        leaked_allocs = max(prev.leaked_allocations, curr.leaked_allocations)
        raw_logs = list(dict.fromkeys([*prev.raw_logs, *curr.raw_logs]))
        clean = (
            prev.clean
            and curr.clean
            and not asan
            and not ubsan
            and not lsan
            and leaked_bytes == 0
            and leaked_allocs == 0
        )
        return SanitizerReport(
            clean=clean,
            asan_errors=asan,
            ubsan_errors=ubsan,
            lsan_leaks=lsan,
            leaked_bytes=leaked_bytes,
            leaked_allocations=leaked_allocs,
            raw_logs=raw_logs,
        )

    def _should_refresh_process(self, proc: SwripProcess) -> bool:
        work_dir = getattr(proc, "work_dir", None)
        work_dir_exists = work_dir is not None and Path(work_dir).exists()
        return (id(proc) not in self._process_sanitizer_reports) or work_dir_exists

    def _capture_process_report(self, proc: SwripProcess, *, force_refresh: bool = False) -> SanitizerReport:
        pid_key = id(proc)
        prev = self._process_sanitizer_reports.get(pid_key)
        if not force_refresh and prev is not None:
            return prev
        curr = proc.parse_sanitizers()
        merged = self._merge_same_process_report(prev, curr)
        self._process_sanitizer_reports[pid_key] = merged
        return merged

    def _record_crash(self, proc: SwripProcess, msg: str) -> None:
        pid_key = id(proc)
        if pid_key not in self._recorded_crash_proc_ids:
            self._recorded_crash_proc_ids.add(pid_key)
            self._unexpected_crashes.append(msg)

    @staticmethod
    def _merge_sanitizer_reports(reports: Iterable[SanitizerReport]) -> SanitizerReport:
        asan: list[str] = []
        ubsan: list[str] = []
        lsan: list[str] = []
        raw_logs: list[str] = []
        leaked_bytes = 0
        leaked_allocs = 0
        clean = True
        for rep in reports:
            if not rep.clean:
                clean = False
            asan.extend(rep.asan_errors)
            ubsan.extend(rep.ubsan_errors)
            lsan.extend(rep.lsan_leaks)
            raw_logs.extend(rep.raw_logs)
            leaked_bytes += rep.leaked_bytes
            leaked_allocs += rep.leaked_allocations
        return SanitizerReport(
            clean=clean,
            asan_errors=asan,
            ubsan_errors=ubsan,
            lsan_leaks=lsan,
            leaked_bytes=leaked_bytes,
            leaked_allocations=leaked_allocs,
            raw_logs=raw_logs,
        )

    def _next_tag(self, label: str) -> str:
        self._seq += 1
        return f"{self.tag_prefix}-{self._seq:03d}-{label}"

    def ensure_compiled_binary(self) -> Path:
        """Build the ASan/UBSan server binary once per run and cache it for staging."""
        if self._compiled_binary is not None and self._compiled_binary.is_file():
            return self._compiled_binary
        build_ws = stage_workspace(self._next_tag("build"), src_root=self.repo_path)
        self._workspaces.append(build_ws)
        res = build_server(build_ws)
        self.shared_build_result = res
        if not res.success or res.binary_path is None:
            raise RuntimeError(
                f"Server build failed (rc={res.returncode}):\n" + "\n".join(res.errors[:20])
            )
        self._compiled_binary = res.binary_path
        return self._compiled_binary

    def new_temp_workspace(
        self,
        label: str = "ws",
        *,
        copy_binary: bool = True,
        seed_immortal: bool = True,
        seed_legacy_des: bool = False,
    ) -> Path:
        ws = stage_workspace(self._next_tag(label), src_root=self.repo_path)
        self._workspaces.append(ws)
        if copy_binary:
            bin_src = self.ensure_compiled_binary()
            bin_dst = ws / "bin" / "swrip"
            bin_dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(bin_src, bin_dst)
            bin_dst.chmod(0o755)
        if seed_immortal:
            seed_immortal_fixture(ws)
        if seed_legacy_des:
            seed_des_legacy_fixture(ws)
        return ws

    def start_fresh_server(
        self,
        label: str = "fresh",
        *,
        seed_immortal: bool = True,
        seed_legacy_des: bool = False,
        ready_timeout: float = 30.0,
    ) -> SwripProcess:
        ws = self.new_temp_workspace(
            label,
            copy_binary=True,
            seed_immortal=seed_immortal,
            seed_legacy_des=seed_legacy_des,
        )
        proc = SwripProcess(ws)
        proc.start()
        self._processes.append(proc)
        metrics = proc.wait_ready(timeout=ready_timeout)
        if not metrics.ready:
            self._capture_process_report(proc, force_refresh=True)
            raise RuntimeError(
                f"Fresh server failed to become ready on port {proc.port} "
                f"(exit={metrics.exit_code}):\n{metrics.log_tail}"
            )
        return proc

    @property
    def shared_server(self) -> SwripProcess:
        if self._shared_server is not None and not self._shared_server.is_running():
            prev = self._shared_server
            self._capture_process_report(prev, force_refresh=True)
            if not self._shared_server_shut_down:
                rc = prev.proc.poll() if getattr(prev, "proc", None) is not None else None
                self._record_crash(
                    prev,
                    f"shared_server died unexpectedly (port={getattr(prev, 'port', '?')}, exit_code={rc})",
                )
            self._shared_server = None
            self._shared_work_dir = None

        if self._shared_server is None:
            ws = self.new_temp_workspace("shared", copy_binary=True, seed_immortal=True)
            proc = SwripProcess(ws)
            proc.start()
            self._processes.append(proc)
            metrics = proc.wait_ready(timeout=30.0)
            if not metrics.ready:
                self._capture_process_report(proc, force_refresh=True)
                raise RuntimeError(
                    f"Shared server failed to become ready on port {proc.port} "
                    f"(exit={metrics.exit_code}):\n{metrics.log_tail}"
                )
            self._shared_server = proc
            self._shared_work_dir = ws
            self._shared_server_shut_down = False
        return self._shared_server

    def make_client(
        self,
        port: int | None = None,
        host: str = "127.0.0.1",
        timeout: float = 10.0,
        **kwargs: Any,
    ) -> TelnetClient:
        target_port = port if port is not None else self.shared_server.port
        client = TelnetClient(host=host, port=target_port, timeout=timeout, **kwargs)
        self._clients.append(client)
        return client

    def run_static_scanners(self, checks: list[str] | None = None, mode: str | None = None) -> dict[str, Any]:
        scan_mode = mode or ("post-remediation" if self.mode == "gate" else "baseline")
        return run_all_scanners(self.repo_path, mode=scan_mode, checks=checks)

    def assert_clean_sanitizers(
        self,
        target: SwripProcess | ShutdownMetrics | SanitizerReport | None = None,
    ) -> SanitizerReport:
        if target is None:
            if self._shared_server is not None:
                self._capture_process_report(
                    self._shared_server,
                    force_refresh=self._should_refresh_process(self._shared_server),
                )
            for proc in self._processes:
                self._capture_process_report(
                    proc,
                    force_refresh=self._should_refresh_process(proc),
                )
            reports = list(self._process_sanitizer_reports.values())
            report = self._merge_sanitizer_reports(reports) if reports else SanitizerReport(clean=True)
            if report.clean and self._unexpected_crashes:
                raise AssertionError(
                    "Unexpected server crash detected: " + "; ".join(self._unexpected_crashes)
                )
        elif isinstance(target, SwripProcess):
            report = self._capture_process_report(target, force_refresh=True)
        elif isinstance(target, ShutdownMetrics):
            report = target.sanitizer_report
        else:
            report = target

        if not report.clean:
            parts: list[str] = []
            if report.asan_errors:
                parts.append(f"ASan({len(report.asan_errors)}): {report.asan_errors[0]}")
            if report.ubsan_errors:
                parts.append(f"UBSan({len(report.ubsan_errors)}): {report.ubsan_errors[0]}")
            if report.lsan_leaks or report.leaked_bytes:
                parts.append(
                    f"LSan({report.leaked_bytes} bytes in {report.leaked_allocations} allocs)"
                )
            if target is None and self._unexpected_crashes:
                parts.append(f"Crashes({len(self._unexpected_crashes)}): {self._unexpected_crashes[0]}")
            raise AssertionError("Sanitizer violation detected: " + "; ".join(parts))
        return report

    def shutdown_shared_server(self) -> ShutdownMetrics | None:
        if self._shared_server is None:
            return None
        if not self._shared_server.is_running() and not self._shared_server_shut_down:
            rc = (
                self._shared_server.proc.poll()
                if getattr(self._shared_server, "proc", None) is not None
                else None
            )
            self._record_crash(
                self._shared_server,
                f"shared_server died before shutdown (port={getattr(self._shared_server, 'port', '?')}, exit_code={rc})",
            )
        metrics = self._shared_server.shutdown_clean()
        self._shared_server_shut_down = True
        pid_key = id(self._shared_server)
        self._process_sanitizer_reports[pid_key] = self._merge_same_process_report(
            self._process_sanitizer_reports.get(pid_key),
            metrics.sanitizer_report,
        )
        self.shared_shutdown_metrics = metrics
        return metrics

    def cleanup(self) -> None:
        for client in self._clients:
            try:
                client.close()
            except Exception:
                pass
        self._clients.clear()

        for proc in self._processes:
            try:
                if proc.is_running():
                    proc.shutdown_clean()
                metrics = proc.shutdown_metrics
                if metrics is None and proc._intentionally_stopped:
                    metrics = proc.shutdown_clean()
                self._capture_process_report(proc, force_refresh=True)
                rc = proc.proc.poll() if proc.proc is not None else None
                clean_exit = metrics is not None and metrics.clean_exit
                self.fixture_shutdowns.append({
                    "workspace": str(proc.work_dir),
                    "boot_log_path": str(proc.boot_log_path),
                    "port": proc.port,
                    "exit_code": rc,
                    "clean_exit": clean_exit,
                    "shutdown_output_tail": metrics.shutdown_output[-1000:] if metrics and not clean_exit else "",
                })
                if not clean_exit:
                    self._record_crash(proc, f"Fixture did not shut down cleanly (port={proc.port}, exit_code={rc})")
            except Exception as exc:
                self.cleanup_errors.append(f"Fixture {proc.port}: {type(exc).__name__}: {exc}")
            finally:
                if proc.is_running():
                    try:
                        proc.terminate()
                    except Exception as exc:
                        self.cleanup_errors.append(f"Cannot terminate fixture {proc.port}: {exc}")

        self.process_count_audited = len(self._process_sanitizer_reports)
        self.aggregated_sanitizer_report = self._merge_sanitizer_reports(
            self._process_sanitizer_reports.values()
        )
        self._processes.clear()

        if not self.keep_workspaces:
            for ws in self._workspaces:
                try:
                    if ws.exists():
                        shutil.rmtree(ws)
                except OSError as exc:
                    self.cleanup_errors.append(f"Workspace {ws}: {exc}")
        self._workspaces.clear()


def run_tests(
    specs: list[TestCaseSpec],
    ctx: E2EContext,
) -> tuple[list[TestResult], dict[str, Any]]:
    """Execute all selected test cases without letting individual failures crash the runner."""
    results: list[TestResult] = []
    shutdown_metrics: ShutdownMetrics | None = None
    try:
        for spec in specs:
            t0 = time.monotonic()
            try:
                if spec.requires_server and not spec.fresh_server:
                    _ = ctx.shared_server
                spec.fn(ctx)
                results.append(
                    TestResult(
                        id=spec.id,
                        tier=spec.tier,
                        feature=spec.feature,
                        title=spec.title,
                        status="PASS",
                        duration_s=time.monotonic() - t0,
                    )
                )
            except SkippedTest as exc:
                results.append(
                    TestResult(
                        id=spec.id,
                        tier=spec.tier,
                        feature=spec.feature,
                        title=spec.title,
                        status="SKIP",
                        duration_s=time.monotonic() - t0,
                        detail=str(exc),
                    )
                )
            except AssertionError as exc:
                results.append(
                    TestResult(
                        id=spec.id,
                        tier=spec.tier,
                        feature=spec.feature,
                        title=spec.title,
                        status="FAIL",
                        duration_s=time.monotonic() - t0,
                        detail=str(exc) or traceback.format_exc(limit=4),
                    )
                )
            except Exception as exc:
                results.append(
                    TestResult(
                        id=spec.id,
                        tier=spec.tier,
                        feature=spec.feature,
                        title=spec.title,
                        status="ERROR",
                        duration_s=time.monotonic() - t0,
                        detail=f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=4)}",
                    )
                )

        shutdown_metrics = ctx.shutdown_shared_server()
    finally:
        ctx.cleanup()

    counts = {
        "total": len(results),
        "PASS": sum(1 for r in results if r.status == "PASS"),
        "FAIL": sum(1 for r in results if r.status == "FAIL"),
        "SKIP": sum(1 for r in results if r.status == "SKIP"),
        "ERROR": sum(1 for r in results if r.status == "ERROR"),
    }
    agg = ctx.aggregated_sanitizer_report
    crashes = list(ctx._unexpected_crashes)
    shared_clean_exit = True if shutdown_metrics is None else shutdown_metrics.clean_exit
    sanitizer_clean = agg.clean and shared_clean_exit and not crashes and not ctx.cleanup_errors

    sanitizer_summary: dict[str, Any] | None = None
    if shutdown_metrics is not None:
        sr = shutdown_metrics.sanitizer_report
        sanitizer_summary = {
            "clean_exit": shutdown_metrics.clean_exit,
            "exit_code": shutdown_metrics.exit_code,
            "used_fallback_kill": shutdown_metrics.used_fallback_kill,
            "sanitizer_clean": sr.clean,
            "asan_errors": sr.asan_errors,
            "ubsan_errors": sr.ubsan_errors,
            "lsan_leaks": sr.lsan_leaks,
            "leaked_bytes": sr.leaked_bytes,
            "leaked_allocations": sr.leaked_allocations,
        }

    aggregated_summary = {
        "process_count": ctx.process_count_audited,
        "clean": agg.clean,
        "unexpected_crashes": crashes,
        "asan_errors": agg.asan_errors,
        "ubsan_errors": agg.ubsan_errors,
        "lsan_leaks": agg.lsan_leaks,
        "leaked_bytes": agg.leaked_bytes,
        "leaked_allocations": agg.leaked_allocations,
    }

    meta = {
        "counts": counts,
        "sanitizer_clean": sanitizer_clean,
        "shared_server_shutdown": sanitizer_summary,
        "fixture_shutdowns": ctx.fixture_shutdowns,
        "cleanup_errors": ctx.cleanup_errors,
        "aggregated_sanitizers": aggregated_summary,
    }
    return results, meta
