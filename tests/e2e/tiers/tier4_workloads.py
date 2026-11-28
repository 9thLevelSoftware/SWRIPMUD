"""Tier 4 (real-world multi-feature application scenarios) E2E tests (9 scenarios T4-01..T4-09)."""

from __future__ import annotations

import re
import socket
import struct
import time
from pathlib import Path
from typing import Any

from tests.e2e.framework import E2EContext, test_case
from tests.e2e.telnet_client import (
    DEFAULT_PROMPT_RE,
    DONT,
    IAC,
    SB,
    SE,
    TELOPT_COMPRESS2,
    TELOPT_GMCP,
    TelnetClient,
    strip_ansi,
)
from tests.e2e.wsl_env import SwripProcess, des_crypt, sha256_hash

IMM_NAME = "Immtest"
IMM_PASS = "E2eImmPass1"
LEGACY_NAME = "Legacydes"
LEGACY_PASS = "LegacyPass1"
MORT_PASS = "E2ePass1"

# ---------------------------------------------------------------------------
# Diagnostic-safe helpers (bounded 2.0..5.0s socket timeouts, ERROR=0)
# ---------------------------------------------------------------------------

_NAME_SEQ = 0


def _next_mortal_name(prefix: str = "Tfour") -> str:
    global _NAME_SEQ
    _NAME_SEQ += 1
    n = _NAME_SEQ
    letters: list[str] = []
    while n > 0:
        n, rem = divmod(n - 1, 26)
        letters.append(chr(ord("a") + rem))
    base = "".join(ch for ch in prefix if ch.isascii() and ch.isalpha()) or "Tfour"
    return (base + "".join(reversed(letters)))[:20]


def _safe_start_fresh(ctx: E2EContext, label: str, **kwargs: Any) -> SwripProcess:
    try:
        return ctx.start_fresh_server(label, **kwargs)
    except Exception as exc:
        raise AssertionError(f"Fresh server startup failed ({label}): {exc}") from exc


def _safe_reboot(ctx: E2EContext, work_dir: Path, ready_timeout: float = 30.0) -> SwripProcess:
    proc = SwripProcess(work_dir)
    ctx._processes.append(proc)
    try:
        proc.start()
        boot = proc.wait_ready(timeout=ready_timeout)
    except Exception as exc:
        raise AssertionError(f"Cold reboot failed ({work_dir}): {exc}") from exc
    if not boot.ready:
        raise AssertionError(f"Cold reboot not ready:\n{boot.log_tail}")
    return proc


def _safe_connect(
    ctx: E2EContext,
    port: int | None = None,
    timeout: float = 5.0,
    **kwargs: Any,
) -> TelnetClient:
    try:
        return ctx.make_client(port=port, timeout=timeout, **kwargs)
    except Exception as exc:
        raise AssertionError(f"Telnet connection failed (port={port}): {exc}") from exc


def _safe_login(cli: TelnetClient, name: str, password: str, timeout: float = 5.0) -> str:
    try:
        transcript: list[str] = []
        transcript.append(cli.read_until(r"(?:Please enter your name:|Name:)\s*$", timeout=timeout))
        cli.send_line(name)
        transcript.append(cli.read_until(r"Password:\s*", timeout=timeout))
        cli.send_line(password)
        stage = cli.read_until(
            r"(?:Press enter\.\.\.|Welcome to Rise in Power !!|Press \[ENTER\]|Reconnecting\.|Wrong password\.)",
            timeout=timeout,
        )
        transcript.append(stage)
        clean_stage = strip_ansi(stage)
        if "Wrong password." in clean_stage:
            raise AssertionError(f"Login failed for {name!r}: Wrong password")
        if "Reconnecting." in clean_stage:
            transcript.append(cli.read_until(DEFAULT_PROMPT_RE, timeout=timeout))
            return strip_ansi("".join(transcript))
        cli.send_line("")
        stage2 = cli.read_until(
            re.compile(r"(?:Press \[ENTER\]|Health:\s*\d+/\d+\s+Movement:\s*\d+/\d+\s*>)"),
            timeout=timeout,
        )
        transcript.append(stage2)
        if "Press [ENTER]" in strip_ansi(stage2):
            cli.send_line("")
            transcript.append(cli.read_until(DEFAULT_PROMPT_RE, timeout=timeout))
        return strip_ansi("".join(transcript))
    except AssertionError:
        raise
    except Exception as exc:
        raise AssertionError(f"Login failed for {name!r}: {exc}") from exc


def _safe_create(
    cli: TelnetClient,
    name: str,
    password: str = MORT_PASS,
    sex: str = "M",
    race: str = "Human",
    ability: str = "combat",
    ansi: str = "A",
    timeout: float = 5.0,
) -> str:
    try:
        return strip_ansi(
            cli.create_character(
                name,
                password,
                sex=sex,
                race=race,
                ability=ability,
                ansi=ansi,
                timeout=timeout,
            )
        )
    except Exception as exc:
        raise AssertionError(f"Character creation failed for {name!r}: {exc}") from exc


def _safe_cmd(
    cli: TelnetClient,
    text: str,
    wait_prompt: bool = True,
    timeout: float = 5.0,
    prompt_pattern: str | re.Pattern[str] = DEFAULT_PROMPT_RE,
    require_prompt: bool | None = True,
) -> str:
    try:
        return strip_ansi(
            cli.cmd(
                text,
                wait_prompt=wait_prompt,
                timeout=timeout,
                prompt_pattern=prompt_pattern,
                require_prompt=require_prompt,
            )
        )
    except Exception as exc:
        raise AssertionError(f"Command {text!r} failed/timed out: {exc}") from exc


def _safe_read_until(
    cli: TelnetClient,
    pattern: str | re.Pattern[str],
    timeout: float = 5.0,
) -> str:
    try:
        return strip_ansi(cli.read_until(pattern, timeout=timeout))
    except Exception as exc:
        raise AssertionError(f"Timed out/EOF waiting for {pattern!r}: {exc}") from exc


def _poll_until(
    cli: TelnetClient,
    pattern: str | re.Pattern[str],
    total_timeout: float = 25.0,
    chunk_timeout: float = 4.0,
    proc: SwripProcess | None = None,
) -> str:
    """Poll `cli.read_until` in bounded <=5.0s socket chunks until `pattern` matches."""
    regex = re.compile(pattern) if isinstance(pattern, str) else pattern
    collected: list[str] = []
    deadline = time.monotonic() + total_timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            tail = "".join(collected)[-400:]
            raise AssertionError(
                f"Timed out after {total_timeout:.1f}s waiting for {regex.pattern!r}; tail={tail!r}"
            )
        if proc is not None and not proc.is_running():
            san = proc.parse_sanitizers()
            raise AssertionError(
                f"Server exited unexpectedly while waiting for {regex.pattern!r}; "
                f"asan={san.asan_errors[:2]} ubsan={san.ubsan_errors[:2]}"
            )
        step = min(chunk_timeout, max(0.5, remaining))
        try:
            chunk = strip_ansi(cli.read_until(regex, timeout=step))
            collected.append(chunk)
            return "".join(collected)
        except TimeoutError as exc:
            if cli.eof:
                raise AssertionError(f"Connection EOF waiting for {regex.pattern!r}: {exc}") from exc
            try:
                collected.append(strip_ansi(cli.drain(0.05)))
            except Exception:
                pass
        except Exception as exc:
            raise AssertionError(f"Error waiting for {regex.pattern!r}: {exc}") from exc


def _safe_drain(cli: TelnetClient, seconds: float = 0.25) -> str:
    try:
        return strip_ansi(cli.drain(seconds=seconds))
    except Exception as exc:
        raise AssertionError(f"Drain failed: {exc}") from exc


def _goto_checked(cli: TelnetClient, vnum: int, *, timeout: float = 5.0) -> str:
    _safe_drain(cli, 0.1)
    out = _safe_cmd(cli, f"goto {vnum}", timeout=timeout)
    low = out.lower()
    if "no such location" in low or "does not exist" in low:
        raise AssertionError(f"goto {vnum} failed:\n{out}")
    return out


def _safe_login_with_options(
    cli: TelnetClient,
    name: str,
    password: str,
    *,
    timeout: float = 5.0,
    enable_gmcp: bool = False,
    gmcp_hello: dict[str, Any] | None = None,
    gmcp_supports_set: list[str] | None = None,
    gmcp_supports_add: list[str] | None = None,
    enable_mccp2: bool = False,
    naws_size: tuple[int, int] | None = None,
) -> str:
    """Log in an existing character while negotiating Telnet options and handling Reconnecting."""
    try:
        transcript: list[str] = []
        transcript.append(cli.read_until(r"(?:Please enter your name:|Name:)\s*$", timeout=timeout))
        if enable_gmcp:
            cli.enable_gmcp()
            if gmcp_hello is not None:
                cli.send_gmcp("Core.Hello", gmcp_hello)
            if gmcp_supports_set is not None:
                cli.send_gmcp("Core.Supports.Set", gmcp_supports_set)
            if gmcp_supports_add is not None:
                cli.send_gmcp("Core.Supports.Add", gmcp_supports_add)
        if enable_mccp2:
            cli.enable_mccp2(wait=False)
        if naws_size is not None:
            cli.send_naws(naws_size[0], naws_size[1])

        cli.send_line(name)
        transcript.append(cli.read_until(r"Password:\s*", timeout=timeout))
        cli.send_line(password)
        stage = cli.read_until(
            r"(?:Press enter\.\.\.|Welcome to Rise in Power !!|Press \[ENTER\]|Reconnecting\.|Wrong password\.)",
            timeout=timeout,
        )
        transcript.append(stage)
        clean_stage = strip_ansi(stage)
        if "Wrong password." in clean_stage:
            raise AssertionError(f"Login failed for {name!r}: Wrong password")
        if "Reconnecting." in clean_stage:
            transcript.append(cli.read_until(DEFAULT_PROMPT_RE, timeout=timeout))
            return strip_ansi("".join(transcript))
        cli.send_line("")
        stage2 = cli.read_until(
            re.compile(r"(?:Press \[ENTER\]|Health:\s*\d+/\d+\s+Movement:\s*\d+/\d+\s*>)"),
            timeout=timeout,
        )
        transcript.append(stage2)
        if "Press [ENTER]" in strip_ansi(stage2):
            cli.send_line("")
            transcript.append(cli.read_until(DEFAULT_PROMPT_RE, timeout=timeout))
        return strip_ansi("".join(transcript))
    except AssertionError:
        raise
    except Exception as exc:
        raise AssertionError(f"Login failed for {name!r}: {exc}") from exc


def _login_imm(ctx: E2EContext, port: int | None = None, reset_room: bool = True) -> TelnetClient:
    cli = _safe_connect(ctx, port=port, timeout=5.0)
    _safe_login(cli, IMM_NAME, IMM_PASS, timeout=5.0)
    _safe_drain(cli, 0.15)
    _safe_cmd(cli, "speak common")
    if reset_room:
        _safe_cmd(cli, "goto 100")
    return cli


def _create_authed_mortal(
    ctx: E2EContext,
    imm: TelnetClient,
    *,
    port: int | None = None,
    level: int = 1,
    gold: int | None = None,
    room: int | None = None,
    race: str = "Human",
    ability: str = "combat",
    prefix: str = "Tfour",
) -> tuple[TelnetClient, str]:
    name = _next_mortal_name(prefix)
    cli = _safe_connect(ctx, port=port, timeout=5.0)
    _safe_create(cli, name, MORT_PASS, race=race, ability=ability, timeout=5.0)
    _safe_drain(imm, 0.2)
    auth_out = _safe_cmd(imm, f"authorize {name} yes")
    assert "usage:" not in auth_out.lower() and "no waiting" not in auth_out.lower(), (
        f"authorize {name} yes failed:\n{auth_out}"
    )
    if level > 1:
        _safe_cmd(imm, f"at {name} advance {name} combat {level}")
    if gold is not None:
        _safe_cmd(imm, f"mset {name} credits {gold}")
    if room is not None:
        _safe_cmd(imm, f"transfer {name} {room}")
    _safe_drain(cli, 0.4)
    return cli, name


def _cleanup_client(cli: TelnetClient | None, *, quit_char: bool = False) -> None:
    if cli is None:
        return
    try:
        if quit_char and not cli.eof:
            try:
                cli.send_line("quit")
                deadline = time.monotonic() + 0.8
                while time.monotonic() < deadline and not cli.eof:
                    cli.drain(0.15)
            except Exception:
                pass
    finally:
        try:
            cli.close()
        except Exception:
            pass


def _safe_stop_proc(proc: SwripProcess | None) -> None:
    if proc is None:
        return
    try:
        if proc.is_running():
            proc.shutdown_clean()
    except Exception:
        pass


def _safe_shutdown_proc(proc: SwripProcess) -> Any:
    try:
        return proc.shutdown_clean()
    except Exception as exc:
        raise AssertionError(f"Clean shutdown failed: {exc}") from exc


def _poll_until_dead(
    killer: TelnetClient,
    target_name: str,
    max_wait: float = 12.0,
) -> str:
    buf: list[str] = []
    deadline = time.monotonic() + max_wait
    while time.monotonic() < deadline:
        buf.append(_safe_drain(killer, 0.4))
        joined = "".join(buf).lower()
        if "is dead!" in joined or "hear the death cry" in joined or "r.i.p." in joined:
            return "".join(buf)
        lk = _safe_cmd(killer, "look", timeout=4.0)
        buf.append(lk)
        if f"corpse of {target_name.lower()}" in lk.lower() or "corpse of" in lk.lower():
            return "".join(buf)
        time.sleep(1.0)
    raise AssertionError(f"Target {target_name!r} did not die within {max_wait:.1f}s:\n{''.join(buf)[-500:]}")


def _parse_credits(score_text: str) -> int:
    m = re.search(r"Credits\s*:\s*([\d,]+)", score_text, re.IGNORECASE)
    if not m:
        m = re.search(r"Gold\s*:\s*([\d,]+)", score_text, re.IGNORECASE)
    assert m, f"Could not parse Credits from score:\n{score_text}"
    return int(m.group(1).replace(",", ""))


def _discover_non_trainer_systems(imm: TelnetClient) -> list[tuple[int, str, str]]:
    so_raw = _safe_cmd(imm, "starsystems")
    assert "huh?" not in so_raw.lower() and "no spaceobjects currently formed" not in so_raw.lower(), (
        f"No spaceobjects loaded:\n{so_raw}"
    )
    so_list = [ln.strip() for ln in so_raw.splitlines() if ln.strip()]
    assert so_list, "No spaceobjects returned by starsystems"
    found: list[tuple[int, str, str]] = []
    for sys_name in so_list:
        info = _safe_cmd(imm, f"showstarsystem {sys_name}")
        if "trainer: yes" in info.lower():
            continue
        m = re.search(r"doca:\s*(\d+)\s+\(([^)]*)\)", info, re.IGNORECASE)
        if m and int(m.group(1)) > 0 and m.group(2).strip():
            found.append((int(m.group(1)), m.group(2).strip(), sys_name))
    assert found, f"No non-trainer spaceobject with valid doca pad found in:\n{so_list}"
    return found


# ===========================================================================
# Tier 4 Real-World Workloads (T4-01 .. T4-09)
# ===========================================================================


@test_case(
    "T4-01",
    4,
    "F01,F02,F03,F07,F18",
    "Full Build, Static Parity, 40-Area Boot & Clean shutdown mud now LSan Check",
    requires_server=False,
    fresh_server=False,
)
def test_t4_01(ctx: E2EContext) -> None:
    try:
        ctx.ensure_compiled_binary()
    except Exception as exc:
        raise AssertionError(f"Compilation failed: {exc}") from exc
    res = ctx.shared_build_result
    assert res is not None and res.success and res.returncode == 0, (
        f"Build failed: errors={res.errors[:10] if res else 'None'}"
    )
    assert len(res.errors) == 0, f"Build reported errors: {res.errors[:10]}"
    assert len(res.compiled_files) >= 44, (
        f"Expected >= 44 compiled files, got {len(res.compiled_files)}"
    )
    forbidden_cats = (
        "format",
        "format-security",
        "format-nonliteral",
        "format-overflow",
        "format-truncation",
        "format-y2k",
        "shift-overflow",
        "implicit-fallthrough",
        "type-limits",
    )
    bad_warns = {c: res.warnings[c] for c in forbidden_cats if res.warnings.get(c)}
    assert not bad_warns, f"Forbidden compiler warning categories present: {bad_warns}"

    scan = ctx.run_static_scanners(mode="post-remediation")
    failing_scanners = {k: v["summary"] for k, v in scan["scanners"].items() if not v["passed"]}
    assert scan["all_passed"], f"Post-remediation static scanners failed: {failing_scanners}"
    pv_summary = scan["scanners"]["persistence_and_vnum"]["summary"]
    assert pv_summary.get("area_active_files", 0) == 40, (
        f"Expected 40 active area files in persistence_and_vnum, got {pv_summary}"
    )
    assert pv_summary.get("missing_template_rooms_count", -1) == 0, (
        f"Expected 0 missing template rooms, got {pv_summary}"
    )

    proc = _safe_start_fresh(ctx, "t4-01-boot")
    probe: TelnetClient | None = None
    imm: TelnetClient | None = None
    try:
        bm = proc.boot_metrics
        assert bm is not None and bm.ready, "Fresh server failed to report ready"
        assert bm.area_count == 40, f"Expected 40 areas at boot, got {bm.area_count}"
        assert bm.assign_gsn_failures == [], f"ASSIGN_GSN failures: {bm.assign_gsn_failures}"
        assert bm.init_language_failures == [], f"init_language_sn failures: {bm.init_language_failures}"
        assert bm.bug_lines == [], f"Boot BUG lines detected: {bm.bug_lines[:10]}"

        probe = _safe_connect(ctx, port=proc.port, timeout=5.0)
        try:
            mssp = probe.request_mssp(timeout=4.0)
        except Exception as exc:
            raise AssertionError(f"MSSP request failed: {exc}") from exc
        assert mssp.get("NAME") == "SWRIP2", f"Unexpected MSSP NAME: {mssp}"
        assert mssp.get("AREAS") == "40", f"Expected MSSP AREAS=40, got: {mssp}"
        assert mssp.get("PORT") == str(proc.port), f"MSSP PORT mismatch: {mssp}"
        _cleanup_client(probe)
        probe = None

        imm = _login_imm(ctx, port=proc.port)
        mem = _safe_cmd(imm, "memory")
        areas_out = _safe_cmd(imm, "areas")
        zones_out = _safe_cmd(imm, "zones")
        wiz = _safe_cmd(imm, "wizlist")
        assert re.search(r"Areas\s+40\b", mem), f"Expected 'Areas 40' in memory:\n{mem}"
        area_rows = [ln for ln in areas_out.splitlines() if ln.strip() and "area" not in ln.lower()[:8]]
        assert len(area_rows) >= 40, f"Expected >= 40 area rows:\n{areas_out}"
        assert "templates.are" in zones_out.lower(), f"templates.are missing from zones:\n{zones_out}"
        assert "Immtest" in wiz, f"Immtest missing from wizlist:\n{wiz}"
        _safe_cmd(imm, "quit", wait_prompt=False)
        _cleanup_client(imm)
        imm = None

        shut = _safe_shutdown_proc(proc)
        assert shut.clean_exit, f"Expected clean_exit: {shut}"
        assert shut.exit_code == 0, f"Expected exit_code == 0, got {shut.exit_code}"
        assert shut.normal_termination, f"Missing normal termination:\n{shut.shutdown_output}"
        assert not shut.used_fallback_kill, "shutdown mud now required fallback SIGTERM/SIGKILL"
        ctx.assert_clean_sanitizers(shut)
    finally:
        _cleanup_client(probe)
        _cleanup_client(imm)
        _safe_stop_proc(proc)


@test_case(
    "T4-02",
    4,
    "F04,F05,F08,F09,F13,F14",
    "Newbie Academy Graduation -> Combat -> Corpse Loot -> Save & Cold Reconnect (a..z)",
    requires_server=True,
    fresh_server=False,
)
def test_t4_02(ctx: E2EContext) -> None:
    imm = _login_imm(ctx)
    cli_a: TelnetClient | None = None
    cli_m: TelnetClient | None = None
    cli_z: TelnetClient | None = None
    name_a = _next_mortal_name("Awrka")
    name_m = _next_mortal_name("Mwrka")
    name_z = _next_mortal_name("Zwrka")
    try:
        cli_a = _safe_connect(ctx, timeout=5.0)
        _safe_create(cli_a, name_a, MORT_PASS, race="Human", ability="combat", ansi="A", timeout=5.0)

        # Full Academy MUDProg walkthrough -> graduation -> newbie pilot warp
        _safe_cmd(cli_a, "n")
        greet = _safe_cmd(cli_a, "e")
        assert "ready for your lessons" in greet.lower() or "domick" in greet.lower(), (
            f"Domick greet_prog failed at #10303:\n{greet}"
        )
        for step in ("w", "n", "n", "n", "n", "n", "open north", "n", "d"):
            _safe_cmd(cli_a, step)

        s1 = _safe_cmd(cli_a, "say hello")
        assert "luke skywalker" in s1.lower() or "toy" in s1.lower(), f"#10342 toy prog failed:\n{s1}"
        s2 = _safe_cmd(cli_a, "say yes")
        assert "have it then" in s2.lower() or "gives you" in s2.lower(), f"#10342 give luke failed:\n{s2}"
        _safe_cmd(cli_a, "u")
        _safe_cmd(cli_a, "n")
        g_luke = _safe_cmd(cli_a, "give luke child")
        assert "thank you" in g_luke.lower(), f"Child thank-you prog failed:\n{g_luke}"
        s_key = _safe_cmd(cli_a, "say key")
        assert "open it for you" in s_key.lower() or "unlock" in s_key.lower(), (
            f"Child key unlock prog failed:\n{s_key}"
        )
        _safe_cmd(cli_a, "n")
        diploma = _poll_until(cli_a, r"gives you a diploma", total_timeout=12.0, chunk_timeout=4.0)
        assert "diploma" in diploma.lower()
        _safe_drain(cli_a, 0.2)
        _safe_cmd(cli_a, "say iamauthed")
        dock_lk = _safe_cmd(cli_a, "look")
        assert "Academy Docking Bay" in dock_lk or "docking bay" in dock_lk.lower(), (
            f"Graduated player not moved to #10325:\n{dock_lk}"
        )
        _safe_cmd(cli_a, "enter")
        warp = _poll_until(
            cli_a,
            r"(?:After a brief journey|Menari Spaceport|Pluogus)",
            total_timeout=12.0,
            chunk_timeout=4.0,
        )
        assert warp.strip(), "spec_newbie_pilot did not warp graduated player"
        post_warp = _safe_cmd(cli_a, "look")
        assert "limbo" not in post_warp.lower(), f"Player warped to Limbo unexpectedly:\n{post_warp}"

        # Create M and Z characters and authorize them
        cli_m = _safe_connect(ctx, timeout=5.0)
        _safe_create(cli_m, name_m, MORT_PASS, race="Human", ability="combat", ansi="A", timeout=5.0)
        _safe_cmd(imm, f"authorize {name_m} yes")

        cli_z = _safe_connect(ctx, timeout=5.0)
        _safe_create(cli_z, name_z, MORT_PASS, race="Human", ability="combat", ansi="A", timeout=5.0)
        _safe_cmd(imm, f"authorize {name_z} yes")

        # Combat & corpse loot at #10308
        _goto_checked(imm, 10308)
        _safe_cmd(imm, "mpurge")
        _safe_cmd(imm, "minvoke 10345")
        _safe_cmd(imm, f"transfer {name_a} 10308")
        _safe_cmd(imm, f"advance {name_a} combat 25")
        _safe_cmd(imm, f"mset {name_a} hitroll 15")
        _safe_drain(cli_a, 0.25)

        cons = _safe_cmd(cli_a, "consider chadoyn")
        assert "chadoyn" in cons.lower(), f"consider failed:\n{cons}"
        _safe_cmd(cli_a, "kill chadoyn")
        _poll_until_dead(cli_a, "chadoyn", max_wait=12.0)
        lk_corpse = _safe_cmd(cli_a, "look")
        assert "corpse" in lk_corpse.lower(), f"Corpse missing after kill:\n{lk_corpse}"
        loot = _safe_cmd(cli_a, "get all corpse")
        assert "no corpse" not in loot.lower(), f"get all corpse failed:\n{loot}"

        # UTF-8/ANSI + save + cold reconnect across a..z
        for cli, nm in ((cli_a, name_a), (cli_m, name_m), (cli_z, name_z)):
            _safe_drain(cli, 0.15)
            said = _safe_cmd(cli, "say T4-02 UTF-8 é 日本語")
            assert "é" in said and "\ufffd" not in said, f"UTF-8 corrupted on {nm}:\n{said}"
            assert "Ok." in _safe_cmd(cli, "save")
            _cleanup_client(cli, quit_char=True)

        cli_a = cli_m = cli_z = None
        time.sleep(0.3)

        work_dir = ctx.shared_server.work_dir
        for nm in (name_a, name_m, name_z):
            pfile = work_dir / "player" / nm[0].lower() / nm
            assert pfile.is_file() and pfile.stat().st_size > 0, f"Missing pfile for {nm}: {pfile}"

        cli_a = _safe_connect(ctx, timeout=5.0)
        _safe_login(cli_a, name_a, MORT_PASS, timeout=5.0)
        sc_a = _safe_cmd(cli_a, "score")
        inv_a = _safe_cmd(cli_a, "inventory")
        assert name_a in sc_a and ("vibro-blade" in inv_a.lower() or "diploma" in inv_a.lower() or "comlink" in inv_a.lower()), (
            f"Cold reconnect verification failed for {name_a}:\nscore={sc_a}\ninv={inv_a}"
        )

        cli_m = _safe_connect(ctx, timeout=5.0)
        _safe_login(cli_m, name_m, MORT_PASS, timeout=5.0)
        assert name_m in _safe_cmd(cli_m, "score")

        cli_z = _safe_connect(ctx, timeout=5.0)
        _safe_login(cli_z, name_z, MORT_PASS, timeout=5.0)
        assert name_z in _safe_cmd(cli_z, "score")
    finally:
        _cleanup_client(cli_a, quit_char=True)
        _cleanup_client(cli_m, quit_char=True)
        _cleanup_client(cli_z, quit_char=True)
        try:
            _goto_checked(imm, 10308)
            _safe_cmd(imm, "peace")
            _safe_cmd(imm, "mpurge")
            _safe_cmd(imm, "purge")
            _safe_cmd(imm, "minvoke 10345")
            _safe_cmd(imm, "goto 100")
        except Exception:
            pass
        _cleanup_client(imm, quit_char=True)


@test_case(
    "T4-03",
    4,
    "F04,F05,F06,F08",
    "Legacy DES crypt(3) Login -> Automatic SHA-256 Upgrade -> MCCP2 + GMCP + UTF-8/Color Session",
    requires_server=True,
    fresh_server=True,
)
def test_t4_03(ctx: E2EContext) -> None:
    proc = _safe_start_fresh(ctx, "t4-03-des", seed_immortal=True, seed_legacy_des=True)
    pfile = proc.work_dir / "player" / "l" / LEGACY_NAME
    assert pfile.is_file(), f"Missing seeded legacy DES pfile: {pfile}"
    pre_txt = pfile.read_text(encoding="utf-8", errors="replace")
    expected_des = des_crypt(LEGACY_PASS, "Da")
    assert f"Password     {expected_des}~" in pre_txt, f"Pre-login DES fixture mismatch:\n{pre_txt[:400]}"

    c1: TelnetClient | None = None
    c2: TelnetClient | None = None
    try:
        c1 = _safe_connect(ctx, port=proc.port, timeout=5.0, naws_size=(100, 35))
        try:
            mssp = c1.request_mssp(timeout=4.0)
            assert mssp.get("NAME") == "SWRIP2", f"Unexpected MSSP NAME: {mssp}"

            _safe_read_until(c1, r"(?:Please enter your name:|Name:)\s*$", timeout=5.0)
            c1.enable_gmcp()
            c1.send_gmcp("Core.Hello", {"client": "SwripE2E", "version": "1.0"})
            c1.send_gmcp("Core.Supports.Add", ["Char.Stats 1"])
            c1.enable_mccp2(wait=False)
            c1.send_naws(100, 35)

            c1.send_line(LEGACY_NAME)
            _safe_read_until(c1, r"Password:\s*", timeout=5.0)
            c1.send_line(LEGACY_PASS)
            stage = _safe_read_until(
                c1,
                r"(?:Press enter\.\.\.|Welcome to Rise in Power !!|Press \[ENTER\]|Wrong password\.)",
                timeout=5.0,
            )
            assert "Wrong password." not in stage, f"AUD-094: DES login rejected:\n{stage}"
            c1.send_line("")
            st2 = _safe_read_until(
                c1,
                re.compile(r"(?:Press \[ENTER\]|Health:\s*\d+/\d+\s+Movement:\s*\d+/\d+\s*>)"),
                timeout=5.0,
            )
            if "Press [ENTER]" in st2:
                c1.send_line("")
                _safe_read_until(c1, DEFAULT_PROMPT_RE, timeout=5.0)
        except AssertionError:
            raise
        except Exception as exc:
            raise AssertionError(f"DES/MCCP2/GMCP handshake failed: {exc}") from exc

        # MCCP2 becomes active in the loop after the descriptor enters play.
        # The first visible game prompt can arrive before the compression header.
        deadline = time.monotonic() + 5.0
        while not c1.mccp2_active and not c1.eof and time.monotonic() < deadline:
            _safe_drain(c1, min(0.2, max(0.01, deadline - time.monotonic())))
        assert c1.mccp2_active and c1.mccp2_activations == 1, (
            f"MCCP2 state invalid: active={c1.mccp2_active}, act={c1.mccp2_activations}"
        )
        assert c1.null_byte_count == 0, f"NUL bytes present in decompressed output: {c1.null_byte_count}"
        assert c1.will_eor_count == 1, f"Expected WILL EOR count == 1, got {c1.will_eor_count}"
        # The header can precede the first compressed payload counted by mccpstat.
        _safe_cmd(c1, "look")
        mstat_on = _safe_cmd(c1, "mccpstat")
        assert "mccp stats:" in mstat_on.lower(), f"Expected MCCP stats:\n{mstat_on}"

        # UTF-8 + ANSI color verification
        _safe_cmd(c1, "config +ansi")
        raw_before = len(c1.all_text)
        ansi_say = _safe_cmd(c1, "say é 日本語 🚀 &rRed ^bBlue && ^^ &xEnd")
        raw_slice = c1.all_text[raw_before:]
        assert "é" in ansi_say and "日本語" in ansi_say and "\ufffd" not in ansi_say, (
            f"UTF-8 multi-byte characters damaged:\n{ansi_say}"
        )
        assert "\x1b[" in raw_slice, "Expected ANSI SGR escape sequences when +ansi enabled"

        _safe_cmd(c1, "config -ansi")
        raw_plain_before = len(c1.all_text)
        _safe_cmd(c1, "say &rPlain")
        raw_plain_slice = c1.all_text[raw_plain_before:]
        assert "\x1b[" not in raw_plain_slice, "ANSI SGR leaked when -ansi set"
        _safe_cmd(c1, "config +ansi")

        # Validate GMCP telemetry
        _safe_drain(c1, 0.3)
        assert c1.gmcp_events, "Expected GMCP events during session"
        invalid = [ev for ev in c1.gmcp_events if not ev.json_valid]
        assert not invalid, f"RFC 8259 invalid GMCP events: {invalid[:3]}"
        mods = {ev.module for ev in c1.gmcp_events}
        for req_mod in ("Room.Info", "Char.Vitals", "Char.Status", "Char.Stats"):
            assert req_mod in mods, f"Missing GMCP module {req_mod}; saw {sorted(mods)}"
        st_evs = [ev for ev in c1.gmcp_events if ev.module == "Char.Status" and isinstance(ev.data, dict)]
        assert any(ev.data.get("name") == LEGACY_NAME for ev in st_evs), (
            f"Char.Status missing name={LEGACY_NAME}: {st_evs}"
        )

        # Cleanly exit MCCP2 stream via DONT COMPRESS2
        try:
            c1.disable_mccp2(wait=True, timeout=3.0)
        except Exception as exc:
            raise AssertionError(f"MCCP2 disable failed: {exc}") from exc
        assert c1.mccp2_deactivations == 1, f"Expected mccp2_deactivations == 1, got {c1.mccp2_deactivations}"
        mstat_off = _safe_cmd(c1, "mccpstat")
        assert "not active" in mstat_off.lower(), f"Expected MCCP inactive:\n{mstat_off}"

        assert "Ok." in _safe_cmd(c1, "save")
        _safe_cmd(c1, "quit", wait_prompt=False)
        _cleanup_client(c1)
        c1 = None
        time.sleep(0.25)

        post_txt = pfile.read_text(encoding="utf-8", errors="replace")
        expected_sha = sha256_hash(LEGACY_PASS)
        m_post = re.search(r"^Password\s+(\S+)~", post_txt, re.MULTILINE)
        assert m_post and m_post.group(1) == expected_sha, (
            f"AUD-094: DES->SHA-256 upgrade missing on disk: "
            f"{m_post.group(1) if m_post else None!r} != {expected_sha!r}"
        )

        c2 = _safe_connect(ctx, port=proc.port, timeout=5.0)
        _safe_login(c2, LEGACY_NAME, LEGACY_PASS, timeout=5.0)
        assert LEGACY_NAME in _safe_cmd(c2, "score")
        _safe_cmd(c2, "quit", wait_prompt=False)
        _cleanup_client(c2)
        c2 = None

        shut = _safe_shutdown_proc(proc)
        ctx.assert_clean_sanitizers(shut)
    finally:
        _cleanup_client(c1)
        _cleanup_client(c2)
        _safe_stop_proc(proc)


@test_case(
    "T4-04",
    4,
    "F08,F11,F12,F15",
    "Crafting Pipeline -> Shop Buy/Sell -> #REPAIRS Repair -> Player Vendor Lifecycle -> Reboot",
    requires_server=True,
    fresh_server=True,
)
def test_t4_04(ctx: E2EContext) -> None:
    proc1 = _safe_start_fresh(ctx, "t4-04-econ")
    imm = _login_imm(ctx, port=proc1.port)
    owner: TelnetClient | None = None
    buyer: TelnetClient | None = None
    proc2: SwripProcess | None = None
    try:
        _goto_checked(imm, 7312)
        _goto_checked(imm, 10630)

        owner, oname = _create_authed_mortal(ctx, imm, port=proc1.port, level=55, gold=2000000, room=10321)
        buyer, bname = _create_authed_mortal(ctx, imm, port=proc1.port, level=5, gold=50000, room=10630)

        # Shop buy/sell at #10321
        assert "backpack" in _safe_cmd(owner, "list").lower()
        assert "You buy a backpack." in _safe_cmd(owner, "buy backpack")
        assert "I'll give you" in _safe_cmd(owner, "value backpack")
        assert "You sell a backpack for" in _safe_cmd(owner, "sell backpack")

        # Craft blaster at ROOM_FACTORY #10400
        _safe_cmd(imm, "cfedit blaster", require_prompt=True)
        _safe_cmd(imm, "timer 1", require_prompt=True)
        _safe_cmd(imm, "stop save", require_prompt=True)
        _safe_cmd(imm, f"mset {oname} dex 25")
        _safe_cmd(imm, f"mset {oname} str 25")
        _safe_cmd(imm, f"sset {oname} makeblaster 100")
        _safe_cmd(imm, f"transfer {oname} 10400")
        _goto_checked(imm, 10400)
        for vnum, kw in (
            (10490, "toolkit"),
            (10493, "oven"),
            (10491, "durastuff"),
            (10488, "ammo"),
            (10497, "circuit"),
            (10489, "lens"),
            (10492, "battery"),
            (10498, "superconductor"),
            (10498, "superconductor"),
        ):
            _safe_cmd(imm, f"oinvoke {vnum}")
            _safe_cmd(imm, f"give {kw} {oname}")
        _safe_drain(owner, 0.25)

        _safe_cmd(owner, "craft")
        _safe_cmd(owner, "select blaster")
        _safe_cmd(owner, "select auto")
        u2 = _safe_cmd(owner, "use superconductor superconductor")
        if "already assigned" in u2.lower():
            _safe_cmd(owner, "use superconductor 2.superconductor")
        _safe_cmd(owner, "name t4blaster")
        done = False
        for _ in range(3):
            bld = _safe_cmd(owner, "build")
            if "cannot figure out" in bld.lower():
                continue
            assert "You begin working" in bld, f"Blaster craft start failed:\n{bld}"
            fin = _poll_until(owner, r"(?:You finish your work\.|Your work falls apart)", total_timeout=12.0, proc=proc1)
            if "You finish your work." in fin:
                done = True
                break
        assert done, "Blaster craft did not complete"
        _safe_cmd(owner, "cancel")
        assert "t4blaster" in _safe_cmd(owner, "inventory").lower()

        # #REPAIRS at #7312 (#7311 Repairman)
        _goto_checked(imm, 7312)
        _safe_cmd(imm, f"transfer {oname} 7312")
        _safe_drain(owner, 0.2)
        _safe_cmd(owner, "drop t4blaster")
        _safe_cmd(imm, "oset t4blaster value0 5")
        _safe_cmd(imm, "oset t4blaster cost 1000")
        _safe_cmd(owner, "get t4blaster")
        app = _safe_cmd(owner, "appraise t4blaster")
        assert re.search(r"cost\s+[1-9]\d*\s+credit", app, re.IGNORECASE), f"Appraise failed:\n{app}"
        rep = _safe_cmd(owner, "repair t4blaster")
        assert re.search(r"charges you\s+[1-9]\d*\s+credit", rep, re.IGNORECASE), f"Repair failed:\n{rep}"

        # Player vendor lifecycle (#10321 -> #10630)
        _safe_cmd(imm, f"transfer {oname} 10321")
        _safe_drain(owner, 0.2)
        deed = _safe_cmd(owner, "buyvendor")
        assert "contract for a vendor" in deed.lower(), f"buyvendor failed:\n{deed}"
        _safe_cmd(imm, f"transfer {oname} 10630")
        _safe_drain(owner, 0.2)
        _safe_cmd(owner, "placevendor")
        _safe_cmd(owner, "give t4blaster vendor")
        _safe_cmd(owner, "pricevendor t4blaster 500")
        _safe_cmd(owner, "save")
        _safe_drain(buyer, 0.3)

        vl = _safe_cmd(buyer, "list")
        assert "500" in vl and "t4blaster" in vl.lower(), f"Vendor list missing t4blaster:\n{vl}"
        vb = _safe_cmd(buyer, "buy t4blaster")
        assert "You buy" in vb and "t4blaster" in vb.lower(), f"Vendor buy failed:\n{vb}"
        assert "t4blaster" in _safe_cmd(buyer, "inventory").lower()
        assert "Ok." in _safe_cmd(buyer, "save")
        _safe_drain(owner, 0.3)

        before_creds = _parse_credits(_safe_cmd(owner, "score"))
        col = _safe_cmd(owner, "getcredits")
        assert "hands over his earnings" in col.lower(), f"getcredits failed:\n{col}"
        after_creds = _parse_credits(_safe_cmd(owner, "score"))
        assert after_creds - before_creds >= 475, (
            f"Expected >= 475 credits payout, delta={after_creds - before_creds}"
        )
        assert "Ok." in _safe_cmd(owner, "save")

        _cleanup_client(buyer, quit_char=True)
        buyer = None
        _cleanup_client(owner, quit_char=True)
        owner = None
        _cleanup_client(imm, quit_char=True)
        imm = None
        _safe_shutdown_proc(proc1)

        # Reboot verification
        proc2 = _safe_reboot(ctx, proc1.work_dir)
        buyer = _safe_connect(ctx, port=proc2.port, timeout=5.0)
        _safe_login(buyer, bname, MORT_PASS, timeout=5.0)
        lk2 = _safe_cmd(buyer, "look")
        inv2 = _safe_cmd(buyer, "inventory")
        assert "vendor" in lk2.lower(), f"Vendor did not persist across reboot at #10630:\n{lk2}"
        assert "t4blaster" in inv2.lower(), f"Purchased crafted blaster lost across reboot:\n{inv2}"
        _cleanup_client(buyer, quit_char=True)
        buyer = None

        shut2 = _safe_shutdown_proc(proc2)
        ctx.assert_clean_sanitizers(shut2)
    finally:
        _cleanup_client(buyer)
        _cleanup_client(owner)
        _cleanup_client(imm)
        _safe_stop_proc(proc1)
        _safe_stop_proc(proc2)


@test_case(
    "T4-05",
    4,
    "F01,F02,F07,F11,F12",
    "OLC Room/Obj/Mob/Shop/Craft Edit -> foldarea/savearea -> Reboot Reload Verification",
    requires_server=True,
    fresh_server=True,
)
def test_t4_05(ctx: E2EContext) -> None:
    proc1 = _safe_start_fresh(ctx, "t4-05-olc")
    imm = _login_imm(ctx, port=proc1.port)
    proc2: SwripProcess | None = None
    try:
        _goto_checked(imm, 7312)
        _goto_checked(imm, 10401)
        _safe_cmd(imm, "redit2", require_prompt=True)
        _safe_cmd(imm, "name T4 OLC Room", require_prompt=True)
        _safe_cmd(imm, "flags +no_magic", require_prompt=True)
        _safe_cmd(imm, "stop save", require_prompt=True)
        rs = _safe_cmd(imm, "rstat 10401")
        assert "T4 OLC Room" in rs and "no_magic" in rs.lower(), f"Room OLC edit failed:\n{rs}"

        # Object & Mobile OLC safety
        _safe_cmd(imm, "oinvoke 10311")
        _safe_cmd(imm, "oedit glowrod", require_prompt=True)
        _safe_cmd(imm, "short a T4 edited glowrod", require_prompt=True)
        _safe_cmd(imm, "stop save", require_prompt=True)
        assert "a T4 edited glowrod" in _safe_cmd(imm, "ostat glowrod")
        _safe_cmd(imm, "drop glowrod")
        _safe_cmd(imm, "purge glowrod")

        _safe_cmd(imm, "minvoke 10345")
        _safe_cmd(imm, "medit chadoyn", require_prompt=True)
        _safe_cmd(imm, "short the T4 chadoyn", require_prompt=True)
        _safe_cmd(imm, "stop save", require_prompt=True)
        assert "the T4 chadoyn" in _safe_cmd(imm, "mstat chadoyn")
        _safe_cmd(imm, "mpurge")

        # Shop & Repair & Craft OLC
        _safe_cmd(imm, "shopset 10370", require_prompt=True)
        _safe_cmd(imm, "buy 137", require_prompt=True)
        _safe_cmd(imm, "stop save", require_prompt=True)

        _goto_checked(imm, 7312)
        _safe_cmd(imm, "shopset 7311", require_prompt=True)
        _safe_cmd(imm, "fix 165", require_prompt=True)
        _safe_cmd(imm, "stop save", require_prompt=True)

        _safe_cmd(imm, "cfedit spice", require_prompt=True)
        _safe_cmd(imm, "timer 7", require_prompt=True)
        _safe_cmd(imm, "stop save", require_prompt=True)

        assert "Done." in _safe_cmd(imm, "foldarea newacad.are")
        assert "Done." in _safe_cmd(imm, "foldarea mall")
        sa = _safe_cmd(imm, "savearea newacad.are")
        assert sa.strip(), "savearea returned empty response"

        _cleanup_client(imm, quit_char=True)
        imm = None
        _safe_shutdown_proc(proc1)

        proc2 = _safe_reboot(ctx, proc1.work_dir)
        assert proc2.boot_metrics is not None and proc2.boot_metrics.bug_lines == [], (
            f"Boot bugs after OLC foldarea reboot: {proc2.boot_metrics.bug_lines if proc2.boot_metrics else None}"
        )

        imm = _login_imm(ctx, port=proc2.port)
        rs2 = _safe_cmd(imm, "rstat 10401")
        assert "T4 OLC Room" in rs2 and "no_magic" in rs2.lower(), (
            f"Room OLC edit lost across reboot:\n{rs2}"
        )
        assert "137" in _safe_cmd(imm, "shopstat 10370")
        st2 = _safe_cmd(imm, "shopstat 7311")
        assert "165" in st2 and "fix" in st2.lower(), f"Shop 7311 lost across reboot:\n{st2}"
        cf2 = _safe_cmd(imm, "cfedit spice", require_prompt=True)
        assert re.search(r"(?i)\btimer\s*[:=]\s*7\b", cf2), f"cfedit timer=7 lost across reboot:\n{cf2}"
        _safe_cmd(imm, "stop abort", require_prompt=True)

        _cleanup_client(imm, quit_char=True)
        imm = None
        shut2 = _safe_shutdown_proc(proc2)
        ctx.assert_clean_sanitizers(shut2)
    finally:
        _cleanup_client(imm)
        _safe_stop_proc(proc1)
        _safe_stop_proc(proc2)


@test_case(
    "T4-06",
    4,
    "F07,F08,F15,F16",
    "Clan Creation -> Planet Capture -> Board Creation & Note Posting -> Reboot Persistence",
    requires_server=True,
    fresh_server=True,
)
def test_t4_06(ctx: E2EContext) -> None:
    proc1 = _safe_start_fresh(ctx, "t4-06-social")
    imm = _login_imm(ctx, port=proc1.port)
    mbr: TelnetClient | None = None
    proc2: SwripProcess | None = None
    try:
        _safe_cmd(imm, "makeclan TfourClan")
        _safe_cmd(imm, "setclan TfourClan filename tfourclan.clan")
        _safe_cmd(imm, "setclan TfourClan leader Immtest")
        _safe_cmd(imm, "setclan TfourClan funds 8500")
        _safe_cmd(imm, "mset Immtest clan TfourClan")
        assert "8500 credits" in _safe_cmd(imm, "clanfunds")

        _safe_cmd(imm, "makeplanet TfourPlanet")
        _safe_cmd(imm, "setplanet TfourPlanet filename tfourplanet.planet")
        _safe_cmd(imm, "setplanet TfourPlanet base_value 1000")
        _safe_cmd(imm, "aset omni_complex planet TfourPlanet")
        _goto_checked(imm, 100)
        cap = _safe_cmd(imm, "capture")
        assert "claimed the planet" in cap.lower() and "TfourPlanet" in cap, f"capture failed:\n{cap}"

        _safe_cmd(imm, "makeboard tfourbrd.brd")
        _safe_cmd(imm, "bset tfourbrd.brd vnum 100")
        _safe_cmd(imm, "bset tfourbrd.brd read 0")
        _safe_cmd(imm, "bset tfourbrd.brd post 0")
        _safe_cmd(imm, "bset tfourbrd.brd maxpost 50")
        brds = _safe_cmd(imm, "boards")
        assert "tfourbrd.brd" in brds, f"Created board missing:\n{brds}"
        assert not re.search(r"Vnum:\s*0\b", brds), f"AUD-029 phantom Vnum: 0 board present:\n{brds}"

        _safe_cmd(imm, "note subject T4NoteSubj")
        _safe_cmd(imm, "note to all")
        _safe_cmd(imm, "note write", prompt_pattern=r"(?s)Begin entering your text.*?>\s*$")
        _safe_cmd(imm, "T4NoteBody", prompt_pattern=r">\s*$")
        _safe_cmd(imm, "/s", prompt_pattern=r"Done\.")
        _safe_drain(imm, 0.2)
        assert "upload your message" in _safe_cmd(imm, "note post").lower()
        assert "T4NoteSubj" in _safe_cmd(imm, "note list")
        assert "T4NoteBody" in _safe_cmd(imm, "note read 1")

        mbr, mname = _create_authed_mortal(ctx, imm, port=proc1.port, level=5, room=100)
        _safe_cmd(imm, f"mset {mname} clan TfourClan")
        assert "T4NoteBody" in _safe_cmd(mbr, "note read 1")
        assert "Ok." in _safe_cmd(mbr, "save")

        _cleanup_client(mbr, quit_char=True)
        mbr = None
        _cleanup_client(imm, quit_char=True)
        imm = None
        _safe_shutdown_proc(proc1)

        proc2 = _safe_reboot(ctx, proc1.work_dir)
        imm = _login_imm(ctx, port=proc2.port)
        assert "TfourClan" in _safe_cmd(imm, "clans")
        assert "TfourPlanet" in _safe_cmd(imm, "planets")
        brds2 = _safe_cmd(imm, "boards")
        assert "tfourbrd.brd" in brds2 and not re.search(r"Vnum:\s*0\b", brds2)
        assert "T4NoteSubj" in _safe_cmd(imm, "note list")
        assert "T4NoteBody" in _safe_cmd(imm, "note read 1")

        mbr = _safe_connect(ctx, port=proc2.port, timeout=5.0)
        _safe_login(mbr, mname, MORT_PASS, timeout=5.0)
        sc_m = _safe_cmd(mbr, "score")
        assert mname in sc_m and "TfourClan" in sc_m, f"Clan membership lost on cold reconnect:\n{sc_m}"

        _cleanup_client(mbr, quit_char=True)
        mbr = None
        _cleanup_client(imm, quit_char=True)
        imm = None
        shut2 = _safe_shutdown_proc(proc2)
        ctx.assert_clean_sanitizers(shut2)
    finally:
        _cleanup_client(mbr)
        _cleanup_client(imm)
        _safe_stop_proc(proc1)
        _safe_stop_proc(proc2)


@test_case(
    "T4-07",
    4,
    "F04,F09,F10,F16",
    "Multi-Client Arena Tournament, Betting, Bounty Hunt (Non-Hunter & Hunter Kill) & Trivia Night",
    requires_server=True,
    fresh_server=True,
)
def test_t4_07(ctx: E2EContext) -> None:
    bounty_table_pattern = re.compile(
        r"^Bounty[ \t]+Amount[ \t]+Poster[ \t]*[\r\n]+(?P<rows>.*?)" + DEFAULT_PROMPT_RE.pattern,
        re.DOTALL | re.MULTILINE,
    )
    bounty_row_pattern = re.compile(r"([A-Za-z]+)[ \t]+(\d+)[ \t]+([A-Za-z]+)[ \t]*")

    def bounty_rows(client: TelnetClient) -> set[tuple[str, int, str]]:
        output = _safe_cmd(client, "bounties", prompt_pattern=bounty_table_pattern)
        table = bounty_table_pattern.search(output)
        assert table is not None, f"Bounty table missing:\n{output}"
        rows = set()
        for line in table.group("rows").splitlines():
            row = bounty_row_pattern.fullmatch(line)
            if row:
                target, amount, posted_by = row.groups()
                rows.add((target, int(amount), posted_by))
        return rows

    def trivia_cmd(client: TelnetClient, command: str, response: str) -> str:
        prompt = re.compile(re.escape(response) + r".*?" + DEFAULT_PROMPT_RE.pattern, re.DOTALL | re.MULTILINE)
        return _safe_cmd(client, command, prompt_pattern=prompt)

    proc = _safe_start_fresh(ctx, "t4-07-events")
    imm = _login_imm(ctx, port=proc.port)
    g1: TelnetClient | None = None
    g2: TelnetClient | None = None
    spec: TelnetClient | None = None
    poster: TelnetClient | None = None
    hunter: TelnetClient | None = None
    v1: TelnetClient | None = None
    v2: TelnetClient | None = None
    try:
        g1, n1 = _create_authed_mortal(ctx, imm, port=proc.port, level=10, gold=10000, room=11)
        g2, n2 = _create_authed_mortal(ctx, imm, port=proc.port, level=10, gold=10000, room=10301)
        spec, _ = _create_authed_mortal(ctx, imm, port=proc.port, level=10, gold=10000, room=10304)
        poster, poster_name = _create_authed_mortal(ctx, imm, port=proc.port, level=5, gold=30000, room=10300)
        hunter, hn = _create_authed_mortal(ctx, imm, port=proc.port, level=25, gold=1000, room=11)
        v1, vn1 = _create_authed_mortal(ctx, imm, port=proc.port, level=1, room=11)
        v2, vn2 = _create_authed_mortal(ctx, imm, port=proc.port, level=1, room=11)

        # Force & stealth skills (F10)
        _safe_drain(g1, 0.25)
        _safe_drain(hunter, 0.25)
        _safe_cmd(imm, f"at {n1} advance {n1} force 20")
        _safe_cmd(imm, f"sset {n1} 'cure light' 100")
        _safe_cmd(imm, f"mset {n1} frc 20")
        _safe_cmd(imm, f"mset {n1} force 500")
        _safe_cmd(imm, f"mset {n1} hp 500")
        _safe_cmd(imm, f"restore {n1}")
        _safe_cmd(imm, f"mset {n1} hp 600")
        _safe_cmd(imm, f"sset {hn} sneak 100")
        _safe_drain(g1, 0.25)
        cure = ""
        for _ in range(4):
            cure = _safe_cmd(g1, f"feel 'cure light' {n1}")
            if "concentration" not in cure.lower() and "distract" not in cure.lower():
                break
        assert "light wounds mend" in cure.lower() or "feel better" in cure.lower() or "cure" in cure.lower(), (
            f"cure light failed:\n{cure}"
        )
        snk = _safe_cmd(hunter, "sneak")
        assert "attempt to move silently" in snk.lower(), f"sneak failed:\n{snk}"

        # Arena tournament & spectator betting (F04, F16)
        _safe_cmd(imm, "chaos 1 50 1 5 500")
        _safe_drain(g1, 0.3)
        _safe_drain(g2, 0.3)
        g1.send_line("arena")
        a1 = _poll_until(
            g1,
            r"You have been taken to the killing fields",
            total_timeout=8.0,
            chunk_timeout=4.0,
            proc=proc,
        )
        _safe_drain(g1, 0.3)
        _safe_drain(g2, 0.3)
        g2.send_line("arena")
        a2 = _poll_until(
            g2,
            r"You have been taken to the killing fields",
            total_timeout=8.0,
            chunk_timeout=4.0,
            proc=proc,
        )
        _safe_drain(g2, 0.3)
        assert "taken to the killing fields" in a1.lower(), f"g1 arena join failed:\n{a1}"
        assert "taken to the killing fields" in a2.lower(), f"g2 arena join failed:\n{a2}"
        _safe_drain(spec, 0.4)
        _safe_drain(imm, 0.4)
        bet = _safe_cmd(spec, f"bet {n1} 200")
        assert f"You place 200 credits on {n1}." in bet, f"bet failed:\n{bet}"
        _safe_drain(imm, 0.3)
        awho = _safe_cmd(imm, "awho")
        assert n1 in awho and n2 in awho, f"Gladiators missing from awho:\n{awho}"

        _poll_until(g1, r"The floor falls out from below", total_timeout=35.0, chunk_timeout=4.0, proc=proc)
        _safe_drain(g2, 0.3)
        _safe_cmd(imm, f"transfer {n1} 29")
        _safe_cmd(imm, f"transfer {n2} 29")
        _safe_cmd(imm, f"mset {n1} hitroll 25")
        _safe_cmd(imm, f"mset {n2} hp 1")
        _safe_cmd(imm, f"restore {n2}")
        _safe_drain(g1, 0.25)
        attack = _safe_cmd(g1, f"murder {n2}")
        assert "too nice" not in attack.lower() and "aren't here" not in attack.lower(), attack
        # The arena awards the result on its 30-second update pulse.
        win_pattern = r"(?:declared the winner|awarded)"
        win_out = attack
        if not re.search(win_pattern, attack):
            win_out = _poll_until(
                g1, win_pattern, total_timeout=40.0, chunk_timeout=4.0, proc=proc
            )
        assert win_out.strip()
        spec_msg = _safe_drain(spec, 0.5)
        assert "400 credits" in spec_msg or "won" in spec_msg.lower(), (
            f"Bet payout missing on spectator:\n{spec_msg}"
        )
        assert n1 in _safe_cmd(imm, "ahall"), f"Winner {n1} missing from ahall"

        # Bounty Hunt: non-hunter vs hunter-clan kill (F09, F16)
        assigned = _safe_cmd(imm, f"mset {hn} clan The Bounty Hunter's Guild")
        assert "Done." in assigned, assigned

        # Mortal targets are stunned below zero and only die at -500 HP.
        # Use local fixture blades that can produce a lethal hit in both branches.
        _goto_checked(imm, 11)
        _safe_cmd(imm, f"transfer {n1} 11")
        for fighter, fighter_name, weapon_name in (
            (g1, n1, "fixturebladeone"), (hunter, hn, "fixturebladetwo")
        ):
            _safe_cmd(imm, f"mset {fighter_name} hitroll 25")
            _safe_drain(fighter, 0.2)
            removed = _safe_cmd(fighter, "remove vibro-blade")
            assert "you stop using" in removed.lower(), removed
            _safe_cmd(imm, "oinvoke 10312 1")
            _safe_cmd(imm, f"oset vibro-blade name {weapon_name}")
            _safe_cmd(imm, f"oset {weapon_name} v1 650")
            _safe_cmd(imm, f"oset {weapon_name} v2 650")
            _safe_cmd(imm, f"give {weapon_name} {fighter_name}")
            _safe_drain(fighter, 0.2)
            wielded = _safe_cmd(fighter, f"wield {weapon_name}")
            assert "you wield" in wielded.lower(), wielded

        _safe_drain(poster, 0.25)
        _safe_cmd(poster, f"addbounty {vn1} 5000")
        nonhunter_bounty = (vn1, 5000, poster_name)
        rows = bounty_rows(imm)
        assert nonhunter_bounty in rows, f"Non-hunter target bounty was not posted: {rows}"
        _safe_cmd(imm, f"transfer {n1} 11")
        _safe_cmd(imm, f"at {n1} advance {n1} combat 25")
        _safe_cmd(imm, f"mset {vn1} hp 1")
        _safe_cmd(imm, f"restore {vn1}")
        _safe_drain(g1, 0.25)
        g1.send_line(f"murder {vn1}")
        _poll_until(g1, rf"{vn1} is DEAD!", total_timeout=12.0, proc=proc)
        rows = bounty_rows(imm)
        assert nonhunter_bounty in rows, f"AUD-183: non-hunter kill cleared bounty: {rows}"

        _safe_drain(poster, 0.2)
        _safe_cmd(poster, f"addbounty {vn2} 5000")
        _safe_drain(hunter, 0.25)
        rows = bounty_rows(hunter)
        assert (vn2, 5000, poster_name) in rows, f"Hunter could not see bounty: {rows}"
        _safe_cmd(imm, f"mset {vn2} hp 1")
        _safe_cmd(imm, f"restore {vn2}")
        before_h = _parse_credits(_safe_cmd(hunter, "score"))
        hunter.send_line(f"murder {vn2}")
        h_msg = _poll_until(hunter, rf"{vn2} is DEAD!", total_timeout=12.0, proc=proc)
        h_msg += _safe_drain(hunter, 0.4)
        after_h = _parse_credits(_safe_cmd(hunter, "score"))
        assert "from the bounty on" in h_msg.lower() and (after_h - before_h >= 5000), (
            f"Hunter bounty claim failed: delta={after_h - before_h}\n{h_msg}"
        )
        rows = bounty_rows(imm)
        assert all(target != vn2 for target, _, _ in rows), f"Claimed bounty still active: {rows}"
        assert nonhunter_bounty in rows, f"Unclaimed bounty missing after hunter kill: {rows}"

        # Trivia Night (F16)
        trivia_cmd(imm, "trivia start 10311", "A new trivia game has begun!")
        _safe_drain(g1, 0.2)
        _safe_drain(spec, 0.2)
        trivia_cmd(g1, "tjoin", "You have joined the trivia game.")
        trivia_cmd(spec, "tjoin", "You have joined the trivia game.")
        trivia_cmd(g1, "tchat TriviaReady", f"<<TChat>> {n1}: TriviaReady")
        trivia_cmd(imm, "tquestion Capital of Coruscant?", "<<Question 1>> Capital of Coruscant?")
        _safe_drain(g1, 0.2)
        trivia_cmd(g1, "tanswer Galactic City", f"<<Answer>> {n1}: Galactic City")
        trivia_cmd(imm, f"twinner {n1}", f"<<Winner>> {n1} has won question 1!")
        standings = trivia_cmd(imm, "tscore", "Trivia Standings")
        assert re.search(rf"(?m)^ *1\. {re.escape(n1)}[ \t\r]*$", standings), standings
        trivia_cmd(imm, "trivia end", "The Trivia game has ended!")

        for c in (v2, v1, hunter, poster, spec, g2, g1, imm):
            _cleanup_client(c, quit_char=True)
        v2 = v1 = hunter = poster = spec = g2 = g1 = imm = None

        shut = _safe_shutdown_proc(proc)
        ctx.assert_clean_sanitizers(shut)
    finally:
        for c in (v2, v1, hunter, poster, spec, g2, g1, imm):
            _cleanup_client(c)
        _safe_stop_proc(proc)


@test_case(
    "T4-08",
    4,
    "F07,F17",
    "ordership (2701..2762) -> launch -> Realspace radar/calculate/recharge/loadcargo -> hyperspace -> land",
    requires_server=True,
    fresh_server=True,
)
def test_t4_08(ctx: E2EContext) -> None:
    proc = _safe_start_fresh(ctx, "t4-08-space")
    imm = _login_imm(ctx, port=proc.port)
    try:
        for tv in (2701, 2708, 2762):
            _goto_checked(imm, tv)
        systems = _discover_non_trainer_systems(imm)
        pad1, loc1, sys1 = systems[0]
        assert len(systems) >= 2, "Expected distinct origin and destination cargo ports"
        _, dest_loc, dest_sys = systems[1]
        cargo_type = 0
        cargo_price = 300
        for system in (sys1, dest_sys):
            market = f'cargo planet "{system}"'
            randomized = _safe_cmd(imm, f"{market} randomize")
            assert "randomized." in randomized and "error" not in randomized.lower(), randomized
            for field, value in (("type", cargo_type), ("price", cargo_price)):
                configured = _safe_cmd(imm, f"{market} set 1 {field} {value}")
                assert "Done." in configured, configured

        for sk in ("starfighters", "midships", "navigation", "'ship systems'"):
            _safe_cmd(imm, f"sset {IMM_NAME} {sk} 100")

        _goto_checked(imm, 32000)
        _safe_cmd(imm, "ordership 0 T4Shuttle")
        sh = _safe_cmd(imm, "showship T4Shuttle")
        assert "No such ship." not in sh, f"ordership 0 failed:\n{sh}"
        m_rooms = re.search(r"Firstroom:\s*(\d+)\s+Lastroom:\s*(\d+)", sh)
        assert m_rooms, f"Missing Firstroom/Lastroom in showship:\n{sh}"
        r_first = int(m_rooms.group(1))
        r_last = int(m_rooms.group(2))
        coseat_r = min(r_first + 1, r_last)
        navseat_r = min(r_first + 2, r_last)

        for fld, val in (
            ("coseat", coseat_r),
            ("navseat", navseat_r),
            ("speed", 150),
            ("hyperspeed", 100),
            ("energy", 10000),
            ("hull", 1000),
            ("shield", 200),
            ("sensor", 50),
            ("astroarray", 300),
            ("maxcargo", 20),
            ("shipyard", pad1),
        ):
            _safe_cmd(imm, f"setship T4Shuttle {fld} {val}")
        _safe_cmd(imm, "resetship T4Shuttle")
        _goto_checked(imm, pad1)

        # Docked cargo load
        _safe_cmd(imm, "openhatch T4Shuttle")
        _safe_cmd(imm, "board T4Shuttle")
        _goto_checked(imm, r_first)
        market_row = rf"\b{cargo_type}\s+{cargo_price}\s+\("
        cc = _safe_cmd(imm, "checkcargo")
        assert re.search(market_row, cc), f"Origin market is missing the fixture listing:\n{cc}"
        ld = _safe_cmd(imm, f"loadcargo {cargo_type} 2")
        assert "cargo request has been delivered" in ld.lower(), f"loadcargo failed:\n{ld}"

        # Launch -> Realspace -> co-pilot-only recharge (AUD-287) -> Calculate -> Hyperspace -> Land
        lnch = _safe_cmd(imm, "launch")
        assert "launch" in lnch.lower() or "flies into space" in lnch.lower(), f"launch failed:\n{lnch}"
        _poll_until(imm, r"Launch complete\.", total_timeout=25.0, chunk_timeout=4.0, proc=proc)

        assert "Your Coordinates:" in _safe_cmd(imm, "radar")
        _goto_checked(imm, coseat_r)
        rec = _safe_cmd(imm, "recharge")
        assert "Recharging shields.." in rec, f"AUD-287 recharge failed from co-pilot seat:\n{rec}"
        _goto_checked(imm, navseat_r)
        nav_rec = _safe_cmd(imm, "recharge")
        assert "You don't seem to be in the co-pilot seat!" in nav_rec, (
            f"AUD-287 recharge should reject navigator seat:\n{nav_rec}"
        )

        _goto_checked(imm, navseat_r)
        calc = _safe_cmd(imm, f"calculate {dest_sys}")
        assert "Hyperspace course set" in calc, f"calculate {dest_sys} failed:\n{calc}"
        # The course calculation queues subsequent commands for six seconds.
        _goto_checked(imm, r_first, timeout=12.0)
        jump = _safe_cmd(imm, "hyperspace")
        assert "you enter hyperspace" in jump.lower(), f"hyperspace did not engage:\n{jump}"
        if "Hyperjump complete." not in jump:
            _poll_until(imm, r"Hyperjump complete\.", total_timeout=30.0, chunk_timeout=4.0, proc=proc)

        land_out = _safe_cmd(imm, f"land {dest_loc}")
        assert "cannot land" not in land_out.lower(), f"land {dest_loc} failed:\n{land_out}"
        if "Landing sequence complete." not in land_out:
            _poll_until(
                imm, r"Landing sequence complete\.", total_timeout=30.0, chunk_timeout=4.0, proc=proc
            )

        cc = _safe_cmd(imm, "checkcargo")
        assert re.search(market_row, cc), f"Destination market is missing the fixture listing:\n{cc}"
        ul = _safe_cmd(imm, f"unloadcargo {cargo_type} 1")
        assert "cargo request has been delivered" in ul.lower(), f"unloadcargo failed:\n{ul}"
        _safe_cmd(imm, "openhatch")
        _safe_cmd(imm, "leave")
        _safe_cmd(imm, "shipdelete T4Shuttle")

        _cleanup_client(imm, quit_char=True)
        imm = None
        shut = _safe_shutdown_proc(proc)
        ctx.assert_clean_sanitizers(shut)
    finally:
        _cleanup_client(imm)
        _safe_stop_proc(proc)
