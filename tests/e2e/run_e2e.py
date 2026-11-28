"""Main CLI entrypoint for SWRIPMUD E2E tests and harness self-check."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
import zlib
from dataclasses import asdict
from pathlib import Path
from typing import Any

# Ensure repo root is importable when invoked as `python3 /mnt/d/SWRIPMUD/tests/e2e/run_e2e.py`
_THIS_FILE = Path(__file__).resolve()
_REPO_ROOT = _THIS_FILE.parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _win_to_wsl_path(raw: str) -> str:
    m = re.match(r"^([A-Za-z]):[\\/](.*)$", raw)
    if m:
        drive = m.group(1).lower()
        rest = m.group(2).replace("\\", "/")
        return f"/mnt/{drive}/{rest}"
    return raw.replace("\\", "/")


def _maybe_reexec_in_wsl(argv: list[str]) -> int | None:
    if sys.platform != "win32":
        return None
    wsl_script = _win_to_wsl_path(str(_THIS_FILE))
    translated: list[str] = []
    skip_next = False
    for i, arg in enumerate(argv):
        if skip_next:
            skip_next = False
            continue
        if arg in ("--repo", "--json-out") and i + 1 < len(argv):
            translated.append(arg)
            translated.append(_win_to_wsl_path(argv[i + 1]))
            skip_next = True
        else:
            translated.append(arg)
    cmd = ["wsl", "--", "python3", wsl_script, *translated]
    proc = subprocess.run(cmd, check=False)
    return proc.returncode


def _run_loopback_telnet_selftest() -> dict[str, Any]:
    """Spin up an in-process TCP loopback server exercising Telnet/MSSP/MCCP2/GMCP."""
    from tests.e2e.telnet_client import (
        DO,
        EOR_CMD,
        IAC,
        MSSP_VAL,
        MSSP_VAR,
        SB,
        SE,
        TELOPT_COMPRESS2,
        TELOPT_EOR,
        TELOPT_GMCP,
        TELOPT_MSSP,
        TELOPT_NAWS,
        TELOPT_SGA,
        TELOPT_TTYPE,
        TTYPE_SEND,
        WILL,
        TelnetClient,
    )

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    srv_port = srv.getsockname()[1]

    server_err: list[str] = []

    def _server_worker() -> None:
        try:
            conn, _ = srv.accept()
            with conn:
                conn.settimeout(3.0)
                # Initial handshake: WILL SGA, WILL EOR, DO TTYPE, DO NAWS, WILL GMCP, WILL MSSP
                conn.sendall(
                    bytes(
                        [
                            IAC, WILL, TELOPT_SGA,
                            IAC, WILL, TELOPT_EOR,
                            IAC, DO, TELOPT_TTYPE,
                            IAC, DO, TELOPT_NAWS,
                            IAC, WILL, TELOPT_GMCP,
                            IAC, WILL, TELOPT_MSSP,
                        ]
                    )
                    + b"Please enter your name:\n"
                )
                time.sleep(0.05)
                _ = conn.recv(4096)

                # Send TTYPE SEND
                conn.sendall(bytes([IAC, SB, TELOPT_TTYPE, TTYPE_SEND, IAC, SE]))
                time.sleep(0.05)
                _ = conn.recv(4096)

                # Send MSSP response
                mssp_pkt = (
                    bytes([IAC, SB, TELOPT_MSSP, MSSP_VAR])
                    + b"NAME"
                    + bytes([MSSP_VAL])
                    + b"SWRIP2"
                    + bytes([MSSP_VAR])
                    + b"PLAYERS"
                    + bytes([MSSP_VAL])
                    + b"1"
                    + bytes([IAC, SE])
                )
                conn.sendall(mssp_pkt)

                # Send MCCP2 start + compressed GMCP + compressed text + Z_STREAM_END + plaintext
                comp = zlib.compressobj(level=9)
                gmcp_inner = bytes([IAC, SB, TELOPT_GMCP]) + b'Char.Vitals {"hp":100,"maxhp":100}' + bytes([IAC, SE])
                inner_payload = gmcp_inner + b"Compressed Hello\r\n" + bytes([IAC, EOR_CMD])
                z_bytes = comp.compress(inner_payload) + comp.flush(zlib.Z_FINISH)
                mccp_hdr = bytes([IAC, SB, TELOPT_COMPRESS2, IAC, SE])
                conn.sendall(mccp_hdr + z_bytes + b"Plaintext After MCCP\r\n")
        except Exception as exc:
            server_err.append(f"{type(exc).__name__}: {exc}")
        finally:
            srv.close()

    t = threading.Thread(target=_server_worker, daemon=True)
    t.start()

    with TelnetClient("127.0.0.1", srv_port, timeout=3.0) as cli:
        cli.read_until(r"Please enter your name:", timeout=3.0)
        cli.enable_gmcp()
        cli.send_gmcp("Core.Hello", {"client": "e2e-selfcheck", "version": "1.0"})
        mssp = cli.request_mssp(timeout=3.0)
        txt = cli.drain(seconds=0.3)

    t.join(timeout=2.0)

    assert not server_err, f"Loopback server error: {server_err}"
    assert mssp.get("NAME") == "SWRIP2", f"Unexpected MSSP: {mssp}"
    assert cli.mccp2_activations == 1 and cli.mccp2_deactivations == 1, (
        f"MCCP2 cycle mismatch: act={cli.mccp2_activations}, deact={cli.mccp2_deactivations}"
    )
    assert "Compressed Hello" in txt and "Plaintext After MCCP" in txt, f"Unexpected text: {txt!r}"
    assert cli.eor_count >= 1, "Missing IAC EOR inside compressed stream"
    assert len(cli.gmcp_events) == 1 and cli.gmcp_events[0].json_valid, f"GMCP fail: {cli.gmcp_events}"
    assert cli.gmcp_events[0].data == {"hp": 100, "maxhp": 100}
    assert cli.null_byte_count == 0
    return {
        "mssp": mssp,
        "mccp2_activations": cli.mccp2_activations,
        "mccp2_deactivations": cli.mccp2_deactivations,
        "gmcp_events": len(cli.gmcp_events),
        "eor_count": cli.eor_count,
    }


def _run_self_check(repo: Path, mode: str, keep_workspaces: bool) -> dict[str, Any]:
    from tests.e2e.static_scanners import run_all_scanners
    from tests.e2e.telnet_client import TelnetClient
    from tests.e2e.wsl_env import (
        SwripProcess,
        allocate_port_block,
        build_server,
        des_crypt,
        ensure_sysroot,
        seed_des_legacy_fixture,
        seed_immortal_fixture,
        sha256_hash,
        stage_workspace,
    )

    checks: dict[str, Any] = {}

    # 1. Sysroot
    sysroot = ensure_sysroot()
    checks["sysroot"] = {
        "ok": (sysroot.include_dir / "zlib.h").is_file()
        and (sysroot.include_dir / "openssl" / "ssl.h").is_file()
        and (sysroot.lib_dir / "libz.so").exists(),
        "root": str(sysroot.root),
    }

    # 2. Port allocator
    port_base = allocate_port_block()
    checks["port_block"] = {"ok": port_base > 1024, "base_port": port_base}

    # 3. Password hashes (SHA-256 + DES crypt via libcrypt.so.1)
    sha = sha256_hash("E2eImmPass1")
    des = des_crypt("LegacyPass1", "Da")
    checks["crypto"] = {
        "ok": len(sha) == 64 and len(des) == 13 and des.startswith("Da"),
        "sha256_len": len(sha),
        "des_sample": des,
    }

    # 4. Loopback Telnet/MCCP2/GMCP/MSSP FSM verification
    checks["loopback_telnet"] = {"ok": True, **_run_loopback_telnet_selftest()}

    # 5. Static scanners execution
    scan_mode = "post-remediation" if mode == "gate" else "baseline"
    scan_res = run_all_scanners(repo, mode=scan_mode)
    checks["static_scanners"] = {
        "ok": True if mode == "diagnostic" else scan_res["all_passed"],
        "scan_mode": scan_mode,
        "all_passed": scan_res["all_passed"],
        "summaries": {k: v["summary"] for k, v in scan_res["scanners"].items()},
    }

    # 6. Isolated WSL stage -> GCC 15.2 ASan/UBSan build -> boot -> MSSP -> Immtest shutdown
    ws: Path | None = None
    proc: SwripProcess | None = None
    try:
        ws = stage_workspace(
            f"selfcheck-{int(time.time())}-p{os.getpid()}-{uuid.uuid4().hex[:6]}",
            src_root=repo,
        )
        build_res = build_server(ws)
        seed_immortal_fixture(ws)
        seed_des_legacy_fixture(ws)

        checks["build"] = {
            "ok": build_res.success,
            "returncode": build_res.returncode,
            "duration_s": round(build_res.duration_s, 2),
            "compiled_count": len(build_res.compiled_files),
            "warning_count": build_res.warning_count,
            "error_count": len(build_res.errors),
        }
        if not build_res.success:
            raise RuntimeError(f"Self-check build failed: {build_res.errors[:10]}")

        proc = SwripProcess(ws, port=port_base)
        proc.start()
        boot = proc.wait_ready(timeout=30.0)
        checks["boot"] = {
            "ok": boot.ready,
            "port": boot.port,
            "pid": boot.pid,
            "duration_s": round(boot.duration_s, 2),
            "area_count": boot.area_count,
            "bug_count": len(boot.bug_lines),
            "assign_gsn_failures": len(boot.assign_gsn_failures),
            "init_language_failures": len(boot.init_language_failures),
        }
        if not boot.ready:
            raise RuntimeError(f"Self-check server failed to boot:\n{boot.log_tail}")

        with TelnetClient("127.0.0.1", port_base, timeout=10.0) as cli:
            cli.read_until(r"(?:Please enter your name:|Name:)\s*$", timeout=10.0)
            mssp = cli.request_mssp(timeout=5.0)
            checks["live_telnet_mssp"] = {
                "ok": mssp.get("NAME") == "SWRIP2",
                "mssp_keys": len(mssp),
                "null_byte_count": cli.null_byte_count,
                "will_eor_count": cli.will_eor_count,
            }

        shut = proc.shutdown_clean()
        sr = shut.sanitizer_report
        checks["shutdown_and_sanitizers"] = {
            "ok": (not shut.used_fallback_kill and shut.normal_termination)
            if mode == "diagnostic"
            else shut.clean_exit,
            "clean_exit": shut.clean_exit,
            "normal_termination": shut.normal_termination,
            "exit_code": shut.exit_code,
            "used_fallback_kill": shut.used_fallback_kill,
            "duration_s": round(shut.duration_s, 2),
            "sanitizer_clean": sr.clean,
            "asan_errors": sr.asan_errors,
            "ubsan_errors": sr.ubsan_errors,
            "lsan_leaks": sr.lsan_leaks,
            "leaked_bytes": sr.leaked_bytes,
            "leaked_allocations": sr.leaked_allocations,
        }
    finally:
        if proc is not None and proc.is_running():
            proc.terminate()
        if not keep_workspaces and ws is not None and ws.exists():
            shutil.rmtree(ws, ignore_errors=True)

    overall_ok = all(bool(v.get("ok")) for v in checks.values())
    return {
        "self_check": True,
        "mode": mode,
        "overall_ok": overall_ok,
        "checks": checks,
    }


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    rc_wsl = _maybe_reexec_in_wsl(raw_argv)
    if rc_wsl is not None:
        return rc_wsl

    parser = argparse.ArgumentParser(description="SWRIPMUD Opaque-Box E2E Test Runner")
    parser.add_argument("--tier", choices=("1", "2", "3", "4", "all"), default="all")
    parser.add_argument("--feature", action="append", default=None, help="Filter by feature (e.g. F01)")
    parser.add_argument("--test", action="append", default=None, help="Filter by test ID")
    parser.add_argument("--mode", choices=("diagnostic", "gate"), default="diagnostic")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--static-only", action="store_true", help="Run only static scanners")
    action.add_argument("--self-check", action="store_true", help="Verify E2E harness subsystems end-to-end")
    action.add_argument("--list", action="store_true", help="Verify and list the adopted case manifest")
    parser.add_argument("--json-out", default=None, help="Write JSON report to file")
    parser.add_argument("--repo", default=str(_REPO_ROOT), help="Path to SWRIPMUD repo")
    parser.add_argument("--keep-workspaces", action="store_true", help="Preserve /var/tmp/swrip-e2e-* workspaces")
    args = parser.parse_args(raw_argv)

    repo = Path(args.repo)
    report_payload: dict[str, Any] = {}
    exit_code = 0

    if not (args.static_only or args.list):
        from tests.e2e.wsl_env import require_private_network

        try:
            require_private_network()
        except RuntimeError as exc:
            parser.error(str(exc))

    if args.self_check:
        report_payload = _run_self_check(repo, args.mode, args.keep_workspaces)
        exit_code = 0 if report_payload["overall_ok"] else 1
        print(json.dumps(report_payload, indent=2))
    elif args.static_only:
        from tests.e2e.static_scanners import run_all_scanners

        scan_mode = "post-remediation" if args.mode == "gate" else "baseline"
        report_payload = run_all_scanners(repo, mode=scan_mode)
        for k, v in report_payload["scanners"].items():
            print(f"[{'PASS' if v['passed'] else 'FAIL'}] {k}: {v['summary']}")
        if args.mode == "gate":
            exit_code = 0 if report_payload["all_passed"] else 1
        else:
            exit_code = 0
    else:
        from tests.e2e.framework import E2EContext, get_registered_tests, run_tests
        from tests.e2e.manifest import EXCLUDED_CASES, gate_passed, validate_registry
        from tests.e2e.tiers import load_all_tiers

        try:
            loaded_modules = load_all_tiers()
            specs = get_registered_tests()
            validate_registry(specs)
        except (ImportError, ValueError) as exc:
            parser.error(str(exc))

        for values in (args.feature, args.test):
            if values and len(set(values)) != len(values):
                parser.error("Duplicate test or feature filter")
        if args.tier != "all":
            t_num = int(args.tier)
            specs = [s for s in specs if s.tier == t_num]
        if args.feature:
            feat_set = {f.upper() for f in args.feature}
            specs = [s for s in specs if s.feature.upper() in feat_set]
        if args.test:
            id_set = set(args.test)
            specs = [s for s in specs if s.id in id_set]

        if not specs:
            parser.error("No cases match all requested filters")
        if args.test and set(args.test) - {spec.id for spec in specs}:
            parser.error("One or more requested test IDs do not match all filters")
        if args.feature and {item.upper() for item in args.feature} - {spec.feature.upper() for spec in specs}:
            parser.error("One or more requested features do not match all filters")

        if args.list:
            report_payload = {
                "loaded_tier_modules": loaded_modules,
                "case_ids": [spec.id for spec in specs],
                "excluded_cases": EXCLUDED_CASES,
            }
            print(json.dumps(report_payload, indent=2))
        else:
            ctx = E2EContext(
                repo_path=repo,
                mode=args.mode,
                keep_workspaces=args.keep_workspaces,
            )
            results, meta = run_tests(specs, ctx)
            report_payload = {
                "mode": args.mode,
                "loaded_tier_modules": loaded_modules,
                "selected_case_ids": [spec.id for spec in specs],
                "excluded_cases": EXCLUDED_CASES,
                "summary": meta,
                "results": [asdict(r) for r in results],
            }

            for r in results:
                line = f"[{r.status}] {r.id} ({r.feature} T{r.tier}, {r.duration_s:.2f}s) - {r.title}"
                if r.detail and r.status in ("FAIL", "ERROR"):
                    line += f"\n    {r.detail}"
                print(line)

            c = meta["counts"]
            print(
                f"\nSummary: total={c['total']} PASS={c['PASS']} FAIL={c['FAIL']} "
                f"SKIP={c['SKIP']} ERROR={c['ERROR']} sanitizer_clean={meta['sanitizer_clean']}"
            )
            if args.mode == "gate":
                exit_code = 0 if gate_passed(specs, results, meta) else 1
            else:
                exit_code = 0

    if args.json_out:
        out_path = Path(args.json_out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report_payload, indent=2), encoding="utf-8")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
