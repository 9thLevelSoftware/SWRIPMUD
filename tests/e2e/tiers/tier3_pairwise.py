"""Tier 3 (cross-feature pairwise combinatorial) E2E tests (22 tests across F01Ã¢â‚¬â€œF18)."""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from tests.e2e.framework import E2EContext, test_case
from tests.e2e.static_scanners import parse_makefile
from tests.e2e.telnet_client import (
    DEFAULT_PROMPT_RE,
    DONT,
    TELOPT_COMPRESS2,
    TELOPT_GMCP,
    TelnetClient,
    strip_ansi,
)
from tests.e2e.wsl_env import SwripProcess, sha256_hash

IMM_NAME = "Immtest"
IMM_PASS = "E2eImmPass1"
LEGACY_NAME = "Legacydes"
LEGACY_PASS = "LegacyPass1"
MORT_PASS = "E2ePass1"

# ---------------------------------------------------------------------------
# Diagnostic-safe helpers (bounded 2.0..5.0s socket timeouts, ERROR=0)
# ---------------------------------------------------------------------------

_NAME_SEQ = 0


def _next_mortal_name(prefix: str = "Tthree") -> str:
    global _NAME_SEQ
    _NAME_SEQ += 1
    n = _NAME_SEQ
    letters: list[str] = []
    while n > 0:
        n, rem = divmod(n - 1, 26)
        letters.append(chr(ord("a") + rem))
    base = "".join(ch for ch in prefix if ch.isascii() and ch.isalpha()) or "Tthree"
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
    timeout: float = 5.0,
) -> str:
    try:
        return strip_ansi(
            cli.create_character(name, password, sex=sex, race=race, ability=ability, timeout=timeout)
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
                f"Server process exited unexpectedly while waiting for {regex.pattern!r}; "
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


def _goto_checked(cli: TelnetClient, vnum: int) -> str:
    _safe_drain(cli, 0.1)
    out = _safe_cmd(cli, f"goto {vnum}")
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
    gmcp_supports: list[str] | None = None,
    enable_mccp2: bool = False,
    naws_size: tuple[int, int] | None = None,
) -> str:
    """Log in an existing character while negotiating Telnet options and handling Reconnecting."""
    try:
        transcript: list[str] = []
        transcript.append(cli.read_until(r"(?:Please enter your name:|Name:)\s*$", timeout=timeout))
        if enable_gmcp:
            cli.enable_gmcp()
            if gmcp_supports is not None:
                cli.send_gmcp("Core.Supports.Set", gmcp_supports)
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
    prefix: str = "Tthree",
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
    imm: TelnetClient,
    target_name: str,
    max_wait: float = 12.0,
) -> str:
    """Poll combat output in bounded <=4.0s steps until target dies."""
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


def _discover_non_trainer_pad(imm: TelnetClient) -> tuple[int, str, str, list[tuple[int, str, str]]]:
    """Return `(pad_vnum, land_loc, target_sys, all_systems)` from live starsystems."""
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
    pad_vnum, land_loc, target_sys = found[0]
    return pad_vnum, land_loc, target_sys, found


# ===========================================================================
# Tier 3 Pairwise Tests (T3-01 .. T3-22)
# ===========================================================================


@test_case(
    "T3-01",
    3,
    "F01",
    "F01+F11: OLC redit2/oedit string mutations and abrupt disconnect mid-OLC (hashstr/OlcSession)",
)
def test_t3_01(ctx: E2EContext) -> None:
    cli1 = _login_imm(ctx)
    cli2: TelnetClient | None = None
    try:
        _goto_checked(cli1, 11)
        _safe_cmd(cli1, "oinvoke 10423")
        _safe_cmd(cli1, "oset key name t3key")
        _safe_cmd(cli1, "oset t3key short T3OsetShort")
        ostat = _safe_cmd(cli1, "ostat t3key")
        assert "T3OsetShort" in ostat, f"Expected T3OsetShort in ostat:\n{ostat}"

        o_enter = _safe_cmd(cli1, "oedit t3key", require_prompt=True)
        assert "Object inline editing mode enabled." in o_enter, f"oedit failed:\n{o_enter}"
        _safe_cmd(cli1, "short T3ProtoShort", require_prompt=True)
        o_abort = _safe_cmd(cli1, "stop abort", require_prompt=True)
        assert "Changes discarded." in o_abort, f"stop abort failed:\n{o_abort}"

        _safe_cmd(cli1, "drop t3key")
        _safe_cmd(cli1, "purge t3key")

        r_enter = _safe_cmd(cli1, "redit2", require_prompt=True)
        assert "Room editing mode enabled." in r_enter, f"redit2 failed:\n{r_enter}"
        _safe_cmd(cli1, "name T3DisconnectRoom", require_prompt=True)

        # Abruptly drop TCP connection while CON_EDITING / OlcSession is active (AUD-138)
        cli1.close()
        cli1 = None
        time.sleep(0.35)

        cli2 = _login_imm(ctx)
        _goto_checked(cli2, 11)
        r_enter2 = _safe_cmd(cli2, "redit2", require_prompt=True)
        assert "Room editing mode enabled." in r_enter2 and "already" not in r_enter2.lower(), (
            f"AUD-138 regression: OlcSession leaked/locked on abrupt disconnect:\n{r_enter2}"
        )
        _safe_cmd(cli2, "stop abort", require_prompt=True)
        rstat = _safe_cmd(cli2, "rstat 11")
        assert "T3DisconnectRoom" not in rstat and "Floating in a void" in rstat, (
            f"Aborted/disconnected room edit persisted unexpectedly:\n{rstat}"
        )
        ctx.assert_clean_sanitizers()
    finally:
        _cleanup_client(cli1)
        _cleanup_client(cli2, quit_char=True)


@test_case(
    "T3-02",
    3,
    "F02",
    "F02+F13: Bitvector reset ('B') room/mob flags + BV31 + track BFS across ROOM_NO_MOB",
    fresh_server=True,
)
def test_t3_02(ctx: E2EContext) -> None:
    proc = _safe_start_fresh(ctx, "t3-02-bitreset-track")
    imm = _login_imm(ctx, port=proc.port)
    mort: TelnetClient | None = None
    try:
        mort, mname = _create_authed_mortal(ctx, imm, port=proc.port, room=10301)
        _safe_cmd(imm, f"sset {mname} track 100")

        _goto_checked(imm, 10307)
        _safe_cmd(imm, "mpurge")
        rm = _safe_cmd(imm, "reset mobile 14 1 10307")
        assert "Mobile reset added." in rm, f"reset mobile failed:\n{rm}"
        b1 = _safe_cmd(imm, "reset bit set room 10304 no_mob")
        assert "Bitvector reset created." in b1, f"reset bit room no_mob failed:\n{b1}"
        b2 = _safe_cmd(imm, "reset bit set room 10304 indoors")
        assert "Bitvector reset created." in b2, f"reset bit room indoors failed:\n{b2}"
        b3 = _safe_cmd(imm, "reset bit set mobile cat invisible")
        assert "Bitvector reset created." in b3, f"reset bit mobile invisible failed:\n{b3}"
        _safe_cmd(imm, "rset 10307 flags auction")  # ROOM_AUCTION (BV31)

        ra = _safe_cmd(imm, "reset area")
        assert "Done." in ra, f"reset area failed:\n{ra}"

        rstat_10304 = _safe_cmd(imm, "rstat 10304")
        rstat_10307 = _safe_cmd(imm, "rstat 10307")
        mstat_cat = _safe_cmd(imm, "mstat cat")
        assert "no_mob" in rstat_10304.lower() and "indoors" in rstat_10304.lower(), (
            f"Room 10304 bit resets did not apply cleanly:\n{rstat_10304}"
        )
        assert "auction" in rstat_10307.lower(), f"BV31 ROOM_AUCTION missing in rstat 10307:\n{rstat_10307}"
        assert "invisible" in mstat_cat.lower(), f"Mob invisible bit reset missing:\n{mstat_cat}"

        trk = _safe_cmd(mort, "track cat")
        assert "trail north" in trk.lower(), (
            f"Expected track trail north from #10301 -> #10304 -> #10307:\n{trk}"
        )
        rstat_after = _safe_cmd(imm, "rstat 10304")
        assert "no_mob" in rstat_after.lower() and "indoors" in rstat_after.lower(), (
            f"AUD-002 regression: track clobbered ROOM_NO_MOB on #10304:\n{rstat_after}"
        )
        ctx.assert_clean_sanitizers(proc)
    finally:
        _cleanup_client(mort, quit_char=True)
        _cleanup_client(imm, quit_char=True)
        _safe_stop_proc(proc)


@test_case(
    "T3-03",
    3,
    "F03",
    "F03+F08: file_ver & save_equipment reentrancy across storeroom #10556, corpse & Version-3 pfile",
    fresh_server=True,
)
def test_t3_03(ctx: E2EContext) -> None:
    proc = _safe_start_fresh(ctx, "t3-03-save-reentrancy")
    imm = _login_imm(ctx, port=proc.port)
    mort: TelnetClient | None = None

    def command_prompt(response: str) -> re.Pattern[str]:
        return re.compile(response + r".*?" + DEFAULT_PROMPT_RE.pattern, re.MULTILINE | re.DOTALL)

    equipment_prompt = command_prompt(r"You are using:")
    try:
        _goto_checked(imm, 10556)
        mort, mname = _create_authed_mortal(ctx, imm, port=proc.port, level=20, gold=5000, room=10556)
        # Random Human attributes can leave the player unable to wield the E-11 or carry the full kit.
        _safe_cmd(imm, f"mset {mname} str 15")
        _safe_cmd(imm, f"mset {mname} dex 20")
        _safe_cmd(imm, f"mset {mname} hp 500")
        _safe_cmd(imm, f"restore {mname}")
        _safe_cmd(imm, f"mset {mname} hitroll 20")

        # Equip vest (#10308), wield blaster (#50), and pack backpack (#10317) with blaster + glowrod (#10311)
        _safe_cmd(imm, "oinvoke 10308")
        _safe_cmd(imm, f"give vest {mname}")
        _safe_cmd(imm, "oinvoke 50")
        _safe_cmd(imm, f"give blaster {mname}")
        _safe_cmd(imm, "oinvoke 10317")
        _safe_cmd(imm, f"give backpack {mname}")
        _safe_cmd(imm, "oinvoke 50")
        _safe_cmd(imm, f"give blaster {mname}")
        _safe_cmd(imm, "oinvoke 10311")
        _safe_cmd(imm, f"give glowrod {mname}", prompt_pattern=command_prompt(r"You give a glowrod to "))
        _safe_drain(mort, 0.3)

        _safe_cmd(mort, "wear vest")
        _safe_cmd(mort, "remove vibro-blade", prompt_pattern=command_prompt(r"You stop using [^\r\n]+vibro-blade\."))
        _safe_cmd(mort, "wield blaster", prompt_pattern=command_prompt(r"You wield [^\r\n]+blaster rifle\."))
        gear = _safe_cmd(mort, "equipment", prompt_pattern=equipment_prompt)
        assert "vest" in gear.lower() and "blaster rifle" in gear.lower(), gear
        _safe_cmd(mort, "put blaster backpack", prompt_pattern=command_prompt(r"You put [^\r\n]+blaster rifle in a backpack\."))
        packed_glowrod = command_prompt(r"You put a glowrod in a backpack\.")
        _safe_cmd(mort, "put glowrod backpack", prompt_pattern=packed_glowrod)

        # Trigger storeroom persistence (save_storeroom)
        _safe_cmd(mort, "get glowrod backpack", prompt_pattern=command_prompt(r"You get a glowrod from a backpack"))
        _safe_cmd(mort, "drop glowrod", prompt_pattern=command_prompt(r"You drop a glowrod\."))
        storeroom = (proc.work_dir / "storeroom" / "10556").read_text(encoding="utf-8")
        assert re.search(r"(?m)^Vnum\s+10311\b", storeroom), storeroom
        _safe_cmd(mort, "get glowrod", prompt_pattern=command_prompt(r"You get a glowrod\."))
        _safe_cmd(mort, "put glowrod backpack", prompt_pattern=packed_glowrod)

        # Trigger corpse persistence (write_corpses)
        _goto_checked(imm, 10308)
        _safe_cmd(imm, "mpurge")
        _safe_cmd(imm, "minvoke 10345")
        _safe_cmd(imm, "mset chadoyn hp 1")
        _safe_cmd(imm, "restore chadoyn")
        _safe_cmd(imm, f"transfer {mname} 10308")
        _safe_drain(mort, 0.2)
        _safe_cmd(mort, "kill chadoyn")
        _poll_until_dead(mort, imm, "chadoyn", max_wait=12.0)

        _safe_cmd(mort, "save", prompt_pattern=command_prompt(r"^Ok\."))
        _safe_cmd(imm, "peace")
        _safe_cmd(imm, f"transfer {mname} 122")
        _safe_drain(mort, 0.25)
        mort.send_line("quit")
        _safe_read_until(mort, r"Your surroundings begin to fade", timeout=5.0)
        deadline = time.monotonic() + 5.0
        while not mort.eof and time.monotonic() < deadline:
            _safe_drain(mort, 0.2)
        assert mort.eof, "Hotel quit did not close the player connection"
        _cleanup_client(mort)
        mort = _safe_connect(ctx, port=proc.port, timeout=5.0)
        login = _safe_login(mort, mname, MORT_PASS, timeout=5.0)
        assert "Reconnecting." not in login, login
        eq = _safe_cmd(mort, "equipment", prompt_pattern=equipment_prompt)
        ex_bp = _safe_cmd(mort, "examine backpack")
        assert "vest" in eq.lower() and "blaster" in eq.lower(), (
            f"Equipped gear lost after interleaved storeroom/corpse/pfile save:\n{eq}"
        )
        assert "glowrod" in ex_bp.lower() and "blaster" in ex_bp.lower(), (
            f"Container contents lost across reentrant save:\n{ex_bp}"
        )
        _safe_cmd(mort, "remove blaster")
        _safe_cmd(mort, "drop blaster")
        _goto_checked(imm, 10308)
        ostat = _safe_cmd(imm, "ostat blaster")
        assert not re.search(r"Object Values:\s*\d+\s+\d+\s+\d+\s+0\b", ostat), (
            f"AUD-167/AUD-303 regression: blaster value[3] zeroed:\n{ostat}"
        )
    finally:
        _cleanup_client(mort, quit_char=True)
        _cleanup_client(imm, quit_char=True)
        _safe_stop_proc(proc)


@test_case(
    "T3-04",
    3,
    "F04",
    "F04+F05: Early MCCP2 + NAWS + TTYPE negotiation during Legacydes DES crypt(3) -> SHA-256 upgrade",
    fresh_server=True,
)
def test_t3_04(ctx: E2EContext) -> None:
    proc = _safe_start_fresh(ctx, "t3-04-des-mccp2", seed_legacy_des=True)
    pfile = proc.work_dir / "player" / "l" / LEGACY_NAME
    assert pfile.is_file(), f"Missing seeded legacy pfile: {pfile}"
    pre_txt = pfile.read_text(encoding="utf-8", errors="replace")
    m_pre = re.search(r"^Password\s+(\S+)~", pre_txt, re.MULTILINE)
    assert m_pre and len(m_pre.group(1)) == 13, (
        f"Expected 13-char DES password pre-login, got: {m_pre.group(1) if m_pre else None!r}"
    )

    cli1: TelnetClient | None = None
    cli2: TelnetClient | None = None
    try:
        cli1 = _safe_connect(ctx, port=proc.port, timeout=5.0, naws_size=(100, 35))
        _safe_read_until(cli1, r"(?:Please enter your name:|Name:)\s*$", timeout=5.0)
        try:
            cli1.enable_mccp2(wait=False)
            cli1.send_naws(100, 35)
            cli1.send_line(LEGACY_NAME)
            _safe_read_until(cli1, r"Password:\s*", timeout=5.0)
            cli1.send_line(LEGACY_PASS)
            stage = _safe_read_until(
                cli1,
                r"(?:Press enter\.\.\.|Welcome to Rise in Power !!|Press \[ENTER\]|Wrong password\.)",
                timeout=5.0,
            )
            assert "Wrong password." not in stage, f"AUD-094: DES login rejected for {LEGACY_NAME}:\n{stage}"
            cli1.send_line("")
            stage2 = _safe_read_until(
                cli1,
                re.compile(r"(?:Press \[ENTER\]|Health:\s*\d+/\d+\s+Movement:\s*\d+/\d+\s*>)"),
                timeout=5.0,
            )
            if "Press [ENTER]" in stage2:
                cli1.send_line("")
                _safe_read_until(cli1, DEFAULT_PROMPT_RE, timeout=5.0)
        except AssertionError:
            raise
        except Exception as exc:
            raise AssertionError(f"Early MCCP2/NAWS DES login handshake failed: {exc}") from exc

        # The server queues the negotiation until the character enters CON_PLAYING.
        sc1 = _safe_cmd(cli1, "score")
        assert cli1.mccp2_activations >= 1, "Early MCCP2 request did not activate once playing"
        assert cli1.null_byte_count == 0, f"NUL bytes found in decompressed output: {cli1.null_byte_count}"
        assert cli1.will_eor_count == 1, f"Expected single WILL EOR, got {cli1.will_eor_count}"

        assert LEGACY_NAME in sc1, f"score inside MCCP2 stream failed:\n{sc1}"

        try:
            cli1.disable_mccp2(wait=True, timeout=3.0)
        except Exception as exc:
            raise AssertionError(f"MCCP2 disable (DONT COMPRESS2) failed: {exc}") from exc
        assert cli1.mccp2_deactivations >= 1, "MCCP2 Z_STREAM_END deactivation not observed"

        assert "Ok." in _safe_cmd(cli1, "save")
        admin = _login_imm(ctx, port=proc.port)
        try:
            _safe_cmd(admin, f"transfer {LEGACY_NAME} 122")
        finally:
            _cleanup_client(admin, quit_char=True)
        _safe_drain(cli1, 0.25)
        _safe_cmd(cli1, "quit", wait_prompt=False)
        _cleanup_client(cli1)
        cli1 = None
        time.sleep(0.25)

        post_txt = pfile.read_text(encoding="utf-8", errors="replace")
        m_post = re.search(r"^Password\s+(\S+)~", post_txt, re.MULTILINE)
        expected_sha = sha256_hash(LEGACY_PASS)
        assert m_post and m_post.group(1) == expected_sha, (
            f"AUD-094 regression: DES password not upgraded to SHA-256 on disk: "
            f"{m_post.group(1) if m_post else None!r} != {expected_sha!r}"
        )

        cli2 = _safe_connect(ctx, port=proc.port, timeout=5.0)
        _safe_login(cli2, LEGACY_NAME, LEGACY_PASS, timeout=5.0)
        sc2 = _safe_cmd(cli2, "score")
        assert LEGACY_NAME in sc2, f"Cold reconnect with upgraded SHA-256 failed:\n{sc2}"
    finally:
        _cleanup_client(cli1)
        _cleanup_client(cli2, quit_char=True)
        _safe_stop_proc(proc)


@test_case(
    "T3-05",
    3,
    "F05",
    "F05+F06: Simultaneous MCCP2 zlib stream + UTF-8/ANSI color title/say + GMCP RFC 8259 JSON",
)
def test_t3_05(ctx: E2EContext) -> None:
    imm = _login_imm(ctx)
    mort: TelnetClient | None = None
    try:
        mort_init, mname = _create_authed_mortal(ctx, imm, level=10, room=10300)
        assert "Ok." in _safe_cmd(mort_init, "save"), "save before reconnect failed"
        _cleanup_client(mort_init, quit_char=True)
        time.sleep(0.25)

        mort = _safe_connect(ctx, timeout=5.0)
        _safe_login_with_options(
            mort,
            mname,
            MORT_PASS,
            timeout=5.0,
            enable_gmcp=True,
            gmcp_supports=["Char 1", "Room 1"],
            enable_mccp2=True,
        )

        _safe_cmd(imm, f'mset {mname} title &RT3 "UTF8" CafÃƒÂ©\\\\Test')
        _safe_drain(mort, 0.2)
        _safe_cmd(mort, "look")
        _safe_cmd(mort, 'say CafÃƒÂ© "quoted" \\\\ test')
        _safe_drain(mort, 0.4)

        try:
            mort.disable_mccp2(wait=True, timeout=3.0)
        except Exception as exc:
            raise AssertionError(f"MCCP2 clean exit failed while GMCP active: {exc}") from exc
        lk_plain = _safe_cmd(mort, "look")
        assert "Coruscant" in lk_plain or "Academy" in lk_plain, f"Plaintext look post-MCCP2 failed:\n{lk_plain}"

        assert mort.mccp2_activations >= 1 and mort.mccp2_deactivations >= 1, (
            f"Expected MCCP2 activation & deactivation, got act={mort.mccp2_activations} deact={mort.mccp2_deactivations}"
        )
        assert mort.null_byte_count == 0, f"NUL bytes in decompressed output: {mort.null_byte_count}"
        assert mort.gmcp_events, "Expected GMCP events while MCCP2 active"
        invalid = [ev for ev in mort.gmcp_events if not ev.json_valid]
        assert not invalid, f"RFC 8259 invalid GMCP events: {invalid[:3]}"

        statuses = [ev for ev in mort.gmcp_events if ev.module == "Char.Status" and isinstance(ev.data, dict)]
        assert statuses, f"Missing Char.Status GMCP event: {[e.module for e in mort.gmcp_events]}"
        fullnames = " ".join(str(ev.data.get("fullname", "")) for ev in statuses)
        assert "CafÃƒÂ©" in fullnames and "\ufffd" not in fullnames, (
            f"UTF-8/JSON escaping damaged in Char.Status fullname: {fullnames!r}"
        )
    finally:
        _cleanup_client(mort, quit_char=True)
        _cleanup_client(imm, quit_char=True)


@test_case(
    "T3-06",
    3,
    "F06",
    "F06+F09: Live combat (kill & flee) driving GMCP Char.Vitals & Room.Info deltas + mid-combat disconnect",
)
def test_t3_06(ctx: E2EContext) -> None:
    imm = _login_imm(ctx)
    mort: TelnetClient | None = None
    try:
        mort_init, mname = _create_authed_mortal(ctx, imm, level=2, room=10308)
        assert "Ok." in _safe_cmd(mort_init, "save"), "save before reconnect failed"
        _cleanup_client(mort_init, quit_char=True)
        time.sleep(0.25)

        _goto_checked(imm, 10308)
        _safe_cmd(imm, "mpurge")
        _safe_cmd(imm, "minvoke 10345")
        _safe_cmd(imm, "mset chadoyn hp 200")
        _safe_cmd(imm, "restore chadoyn")

        mort = _safe_connect(ctx, timeout=5.0)
        _safe_login_with_options(
            mort,
            mname,
            MORT_PASS,
            timeout=5.0,
            enable_gmcp=True,
            gmcp_supports=["Char 1", "Room 1"],
        )

        _safe_cmd(imm, f"mset {mname} hp 120")
        _safe_cmd(imm, f"restore {mname}")
        _safe_cmd(mort, "look")
        _safe_drain(mort, 0.3)

        ev_start = len(mort.gmcp_events)
        _safe_cmd(mort, "kill chadoyn")
        for _ in range(4):
            _safe_drain(mort, 0.5)
            time.sleep(0.5)
        flee_out = _safe_cmd(mort, "flee")
        _safe_drain(mort, 0.3)

        room_nums = [
            ev.data.get("num")
            for ev in mort.gmcp_events[ev_start:]
            if ev.module == "Room.Info" and isinstance(ev.data, dict)
        ]
        if "flee" in flee_out.lower() and "failed" not in flee_out.lower():
            assert any(n != 10308 for n in room_nums), (
                f"Expected Room.Info delta away from 10308 on flee, got {room_nums}"
            )

        _safe_cmd(imm, "peace")
        _safe_cmd(imm, f"transfer {mname} 10308")
        _safe_cmd(imm, "mset chadoyn hp 1")
        _safe_cmd(imm, "restore chadoyn")
        _safe_cmd(imm, f"mset {mname} hitroll 25")
        _safe_drain(mort, 0.2)
        _safe_cmd(mort, "kill chadoyn")
        _poll_until_dead(mort, imm, "chadoyn", max_wait=12.0)
        _safe_drain(mort, 0.3)

        vitals = [
            ev.data
            for ev in mort.gmcp_events
            if ev.module == "Char.Vitals" and isinstance(ev.data, dict)
        ]
        assert vitals, "Expected Char.Vitals GMCP events during combat"
        assert any(int(v.get("mkills", 0)) >= 1 for v in vitals), (
            f"Expected mkills >= 1 in Char.Vitals after kill: {vitals[-3:]}"
        )
        invalid = [ev for ev in mort.gmcp_events if not ev.json_valid]
        assert not invalid, f"Invalid GMCP JSON during combat: {invalid[:3]}"

        # Start second combat and abruptly drop TCP socket mid-combat (AUD-049/AUD-128)
        _safe_cmd(imm, "minvoke 10345")
        _safe_cmd(imm, "mset chadoyn hp 200")
        _safe_cmd(imm, "restore chadoyn")
        _safe_cmd(mort, "kill chadoyn")
        mort.close()
        mort = None
        time.sleep(0.35)
        _safe_cmd(imm, "peace")
        assert "10308" in _safe_cmd(imm, "rstat 10308") or "Dank" in _safe_cmd(imm, "look")
        ctx.assert_clean_sanitizers()
    finally:
        try:
            _goto_checked(imm, 10308)
            _safe_cmd(imm, "peace")
            _safe_cmd(imm, "mpurge")
            _safe_cmd(imm, "purge")
            _safe_cmd(imm, "minvoke 10345")
            _safe_cmd(imm, "goto 100")
        except Exception:
            pass
        _cleanup_client(mort, quit_char=True)
        _cleanup_client(imm, quit_char=True)


@test_case(
    "T3-07",
    3,
    "F07",
    "F07+F17: 40-area world (area.lst) cross-referenced with space.lst, ship.lst, and templates 2701..2762",
)
def test_t3_07(ctx: E2EContext) -> None:
    imm = _login_imm(ctx)
    try:
        bm = ctx.shared_server.boot_metrics
        assert bm is not None and bm.ready
        if bm.area_count is not None:
            assert bm.area_count >= 40, f"Expected >= 40 loaded areas, got {bm.area_count}"
        areas_out = _safe_cmd(imm, "areas")
        area_lines = [ln for ln in areas_out.splitlines() if ln.strip() and "area" not in ln.lower()[:8]]
        assert len(area_lines) >= 40, f"Expected >= 40 areas in 'areas' output, got {len(area_lines)}"

        pad_vnum, _, _, systems = _discover_non_trainer_pad(imm)
        for pv, _, sname in systems[:3]:
            _goto_checked(imm, pv)
            rs = _safe_cmd(imm, f"rstat {pv}")
            assert "can_land" in rs.lower(), (
                f"Spaceobject {sname} doca pad #{pv} lacks ROOM_CAN_LAND:\n{rs}"
            )

        allships_out = _safe_cmd(imm, "allships")
        assert "no ships currently formed" not in allships_out.lower(), f"No ships loaded:\n{allships_out}"
        ship_names: list[str] = []
        for ln in allships_out.splitlines():
            s = ln.strip()
            if (
                not s
                or "the following ships" in s.lower()
                or (s.startswith("Ship") and "Owner" in s)
                or "to rent" in s.lower()
                or "to buy" in s.lower()
            ):
                continue
            ship_names.append(s.split()[0])
        assert ship_names, f"No ship rows parsed from allships:\n{allships_out}"
        for sname in ship_names[:3]:
            sh = _safe_cmd(imm, f"showship {sname}")
            m = re.search(r"Firstroom:\s*(\d+)", sh)
            if m and int(m.group(1)) > 0:
                _goto_checked(imm, int(m.group(1)))

        for tv in (2701, 2708, 2732, 2751, 2762):
            _goto_checked(imm, tv)

        _goto_checked(imm, 32000)
        _safe_cmd(imm, "ordership 1 T3CrossWing")
        _safe_cmd(imm, "ordership 0 T3CrossShuttle")
        sh1 = _safe_cmd(imm, "showship T3CrossWing")
        sh2 = _safe_cmd(imm, "showship T3CrossShuttle")
        assert "Firstroom:" in sh1 and "No such ship." not in sh1, f"ordership 1 failed:\n{sh1}"
        assert "Firstroom:" in sh2 and "No such ship." not in sh2, f"ordership 0 failed:\n{sh2}"
        m_r = re.search(r"Firstroom:\s*(\d+)", sh2)
        assert m_r and int(m_r.group(1)) > 0, f"Invalid Firstroom on T3CrossShuttle:\n{sh2}"
        _goto_checked(imm, int(m_r.group(1)))
    finally:
        try:
            _goto_checked(imm, 100)
            _safe_cmd(imm, "shipdelete T3CrossWing")
            _safe_cmd(imm, "shipdelete T3CrossShuttle")
        except Exception:
            pass
        _cleanup_client(imm, quit_char=True)


@test_case(
    "T3-08",
    3,
    "F08",
    "F08+F12: Crafted disguise (WEAR_DISGUISE=25) and blaster (value[3..5]) persist across save and cold reconnect",
    fresh_server=True,
)
def test_t3_08(ctx: E2EContext) -> None:
    proc = _safe_start_fresh(ctx, "t3-08-craft-save")
    imm = _login_imm(ctx, port=proc.port)
    mort: TelnetClient | None = None
    mname = ""
    try:
        for recipe in ("disguise", "blaster"):
            _safe_cmd(imm, f"cfedit {recipe}", require_prompt=True)
            _safe_cmd(imm, "timer 1", require_prompt=True)
            _safe_cmd(imm, "stop save", require_prompt=True)

        mort, mname = _create_authed_mortal(ctx, imm, port=proc.port, room=10400)
        _safe_cmd(imm, f"mset {mname} str 25")
        _safe_cmd(imm, f"mset {mname} dex 25")
        _safe_cmd(imm, f"sset {mname} disguise 100")
        _safe_cmd(imm, f"sset {mname} makeblaster 100")
        _safe_cmd(mort, "drop all")
        _goto_checked(imm, 10400)

        # Craft disguise
        _safe_cmd(imm, "oinvoke 32280")
        _safe_cmd(imm, f"give thread {mname}")
        _safe_cmd(imm, "oinvoke 10423")
        _safe_cmd(imm, "oset key type disguise_fabric")
        _safe_cmd(imm, f"give key {mname}")
        _safe_drain(mort, 0.25)

        _safe_cmd(mort, "craft")
        _safe_cmd(mort, "select disguise")
        _safe_cmd(mort, "select auto")
        _safe_cmd(mort, "name t3disg")
        sr = _safe_cmd(mort, "set sexrace 2 4")
        assert "usage" not in sr.lower(), f"set sexrace 2 4 failed:\n{sr}"
        done_d = False
        for _ in range(3):
            bld = _safe_cmd(mort, "build")
            if "cannot figure out" in bld.lower():
                continue
            assert "You begin working" in bld, f"Disguise build start failed:\n{bld}"
            fin = _poll_until(mort, r"(?:You finish your work\.|Your work falls apart)", total_timeout=12.0, proc=proc)
            if "You finish your work." in fin:
                done_d = True
                break
        assert done_d, "Disguise craft did not finish"
        _safe_cmd(mort, "cancel")

        # Craft blaster
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
            _safe_cmd(imm, f"give {kw} {mname}")
        _safe_drain(mort, 0.25)

        _safe_cmd(mort, "craft")
        _safe_cmd(mort, "select blaster")
        _safe_cmd(mort, "select auto")
        u2 = _safe_cmd(mort, "use superconductor superconductor")
        if "already assigned" in u2.lower():
            _safe_cmd(mort, "use superconductor 2.superconductor")
        _safe_cmd(mort, "name t3blaster")
        done_b = False
        for _ in range(3):
            bld = _safe_cmd(mort, "build")
            if "cannot figure out" in bld.lower():
                continue
            assert "You begin working" in bld, f"Blaster build start failed:\n{bld}"
            fin = _poll_until(mort, r"(?:You finish your work\.|Your work falls apart)", total_timeout=12.0, proc=proc)
            if "You finish your work." in fin:
                done_b = True
                break
        assert done_b, "Blaster craft did not finish"
        _safe_cmd(mort, "cancel")

        _safe_cmd(mort, "wear t3disg")
        _safe_cmd(mort, "remove vibro-blade")
        _safe_cmd(mort, "wield t3blaster")
        assert "t3blaster" in _safe_cmd(mort, "equipment").lower()
        assert "Ok." in _safe_cmd(mort, "save")
        _goto_checked(imm, 122)
        _safe_cmd(imm, f"transfer {mname} 122")
        _safe_drain(mort, 0.2)
        _safe_cmd(mort, "quit", wait_prompt=False)
        _cleanup_client(mort)
        mort = None
        time.sleep(0.25)

        mort = _safe_connect(ctx, port=proc.port, timeout=5.0)
        _safe_login(mort, mname, MORT_PASS, timeout=5.0)
        eq = _safe_cmd(mort, "equipment")
        assert "t3disg" in eq.lower() and "t3blaster" in eq.lower(), (
            f"AUD-168 regression: crafted disguise/blaster lost across cold reconnect:\n{eq}"
        )
        _safe_cmd(mort, "remove t3disg")
        _safe_cmd(mort, "remove t3blaster")
        _safe_cmd(mort, "drop t3disg")
        _safe_cmd(mort, "drop t3blaster")
        _safe_drain(imm, 0.25)
        item_stats = r"(?s)Object Values:.*?Health:[^>]*>"
        od = _safe_cmd(imm, "ostat t3disg", prompt_pattern=item_stats)
        ob = _safe_cmd(imm, "ostat t3blaster", prompt_pattern=item_stats)
        assert re.search(r"Values:\s*12\s+12\s+4\s+2\b", od), f"Disguise values corrupted:\n{od}"
        assert re.search(r"Values:\s*12\s+\d+\s+\d+\s+\d+\s+[1-9]\d*\s+2000\b", ob), (
            f"AUD-167 regression: blaster values corrupted:\n{ob}"
        )
    finally:
        _cleanup_client(mort, quit_char=True)
        _cleanup_client(imm, quit_char=True)
        _safe_stop_proc(proc)


@test_case(
    "T3-09",
    3,
    "F09",
    "F09+F10: Force spells (force lightning) and wired combat skills (throw, hitall, murder) through raw_kill",
)
def test_t3_09(ctx: E2EContext) -> None:
    imm = _login_imm(ctx)
    mort: TelnetClient | None = None
    try:
        _goto_checked(imm, 10308)
        _safe_cmd(imm, "mpurge")
        _safe_cmd(imm, "minvoke 10345")
        _safe_cmd(imm, "minvoke 10345")
        _safe_cmd(imm, "mset chadoyn hp 500")
        _safe_cmd(imm, "restore chadoyn")
        _safe_cmd(imm, "mset 2.chadoyn hp 500")
        _safe_cmd(imm, "restore 2.chadoyn")

        mort, mname = _create_authed_mortal(ctx, imm, level=20, room=10308)
        _safe_cmd(imm, f"mset {mname} frc 20")
        _safe_cmd(imm, f"mset {mname} force 1000")
        _safe_cmd(imm, f"mset {mname} hp 1000")
        _safe_cmd(imm, f"restore {mname}")
        _safe_drain(mort, 0.15)
        _safe_cmd(imm, f"mset {mname} hitroll 25")
        for sk in ("'force lightning'", "throw", "hitall", "murder"):
            _safe_cmd(imm, f"sset {mname} {sk} 100")

        _safe_cmd(imm, "oinvoke 10423")
        _safe_cmd(imm, f"give key {mname}")
        _safe_drain(mort, 0.2)
        _safe_cmd(mort, "hold key")
        assert "vibro-blade" in _safe_cmd(mort, "equipment").lower()

        thr = _safe_cmd(mort, "throw vibro-blade")
        assert "Huh?" not in thr and "You throw" in thr, f"AUD-195 throw failed:\n{thr}"

        ha = _safe_cmd(mort, "hitall")
        assert "Huh?" not in ha and "You have no idea" not in ha, f"AUD-195 hitall failed:\n{ha}"

        light = ""
        for _ in range(4):
            light = _safe_cmd(mort, "feel 'force lightning' chadoyn", timeout=5.0)
            if "concentration" not in light.lower() and "distract" not in light.lower():
                break
        assert "Huh?" not in light and "bolts of electricity" in light.lower(), (
            f"AUD-015 force lightning failed:\n{light}"
        )

        _safe_cmd(imm, "peace")
        _safe_cmd(imm, "mset chadoyn hp 1")
        _safe_cmd(imm, "restore chadoyn")
        m_out = _safe_cmd(mort, "murder chadoyn")
        assert "Huh?" not in m_out, f"AUD-195 murder not wired:\n{m_out}"
        _poll_until_dead(mort, imm, "chadoyn", max_wait=12.0)

        loot = _safe_cmd(mort, "get all corpse")
        assert "no corpse" not in loot.lower(), f"Expected corpse loot after raw_kill:\n{loot}"
        _safe_cmd(imm, "peace")
    finally:
        try:
            _goto_checked(imm, 10308)
            _safe_cmd(imm, "peace")
            _safe_cmd(imm, "mpurge")
            _safe_cmd(imm, "purge")
            _safe_cmd(imm, "minvoke 10345")
            _safe_cmd(imm, "goto 100")
        except Exception:
            pass
        _cleanup_client(mort, quit_char=True)
        _cleanup_client(imm, quit_char=True)


@test_case(
    "T3-10",
    3,
    "F10",
    "F10+F15: Language learning (huttese at #10394) and speak switching vs multi-listener scramble and #10370 shop",
)
def test_t3_10(ctx: E2EContext) -> None:
    imm = _login_imm(ctx)
    speaker: TelnetClient | None = None
    listener: TelnetClient | None = None
    try:
        speaker, sname = _create_authed_mortal(ctx, imm, gold=5000, room=10306)
        listener, lname = _create_authed_mortal(ctx, imm, gold=200, room=10321)

        learn = _safe_cmd(speaker, "languages learn huttese")
        assert "lessons in huttese" in learn.lower() or "speak perfect huttese" in learn.lower(), (
            f"AUD-091 language learn failed at #10394:\n{learn}"
        )
        _safe_cmd(imm, f"sset {sname} huttese 100")
        spk = _safe_cmd(speaker, "speak huttese")
        assert "You now speak huttese." in spk, f"speak huttese failed:\n{spk}"

        _safe_cmd(imm, f"transfer {sname} 10321")
        _safe_drain(speaker, 0.25)
        _safe_drain(listener, 0.25)

        _safe_cmd(speaker, "say HuttSecretAlpha")
        _safe_drain(speaker, 0.15)
        lst_scrambled = _safe_drain(listener, 0.3)
        assert "HuttSecretAlpha" not in lst_scrambled, (
            f"Unskilled listener heard huttese unscrambled:\n{lst_scrambled}"
        )

        # Shop trade works while speaking huttese
        bought = _safe_cmd(speaker, "buy backpack")
        sold = _safe_cmd(speaker, "sell backpack")
        assert "You buy a backpack." in bought and "You sell a backpack for" in sold, (
            f"Shop trade failed while speaking huttese:\nbought={bought}\nsold={sold}"
        )

        _safe_cmd(imm, f"transfer {lname} 10306")
        _safe_drain(listener, 0.25)
        learn_l = _safe_cmd(listener, "languages learn huttese")
        assert "lessons in huttese" in learn_l.lower() or "speak perfect huttese" in learn_l.lower(), (
            f"Listener language learn failed at #10394:\n{learn_l}"
        )
        _safe_cmd(imm, f"sset {lname} huttese 100")
        _safe_cmd(imm, f"transfer {lname} 10321")
        _safe_drain(listener, 0.25)
        _safe_drain(speaker, 0.25)
        _safe_cmd(speaker, "say HuttSecretBeta")
        _safe_drain(speaker, 0.15)
        lst_clear = _safe_drain(listener, 0.3)
        assert "HuttSecretBeta" in lst_clear, (
            f"Skilled listener did not understand huttese:\n{lst_clear}"
        )
        assert "You now speak common." in _safe_cmd(speaker, "speak common")
    finally:
        _cleanup_client(listener, quit_char=True)
        _cleanup_client(speaker, quit_char=True)
        _cleanup_client(imm, quit_char=True)


@test_case(
    "T3-11",
    3,
    "F11",
    "F11+F15: OLC shopset (fix/buy/itypes) unified SHOP_DATA -> live appraise/repair/buy/sell -> foldarea reboot",
    fresh_server=True,
)
def test_t3_11(ctx: E2EContext) -> None:
    proc1 = _safe_start_fresh(ctx, "t3-11-shop-repairs")
    imm = _login_imm(ctx, port=proc1.port)
    mort: TelnetClient | None = None
    proc2: SwripProcess | None = None
    try:
        _goto_checked(imm, 10321)
        _safe_cmd(imm, "shopset 10370", require_prompt=True)
        _safe_cmd(imm, "stype +fix", require_prompt=True)
        _safe_cmd(imm, "fix 150", require_prompt=True)
        _safe_cmd(imm, "itypes +armor", require_prompt=True)
        _safe_cmd(imm, "stop save", require_prompt=True)

        mort, mname = _create_authed_mortal(ctx, imm, port=proc1.port, gold=5000, room=10321)
        _safe_cmd(imm, "oinvoke 10308")
        _safe_cmd(imm, "oset vest cost 1000")
        _safe_cmd(imm, "oset vest value0 1")
        _safe_cmd(imm, f"give vest {mname}")
        _safe_drain(mort, 0.25)

        app = _safe_cmd(mort, "appraise vest")
        m_cost = re.search(r"cost\s+(\d+)\s+credit", app, re.IGNORECASE)
        assert m_cost and int(m_cost.group(1)) > 0, (
            f"AUD-165: expected >0 repair appraise on unified shop #10370:\n{app}"
        )
        rep = _safe_cmd(mort, "repair vest")
        assert re.search(r"charges you\s+\d+\s+credit", rep, re.IGNORECASE), (
            f"Expected repair charge on unified shop #10370:\n{rep}"
        )
        bought = _safe_cmd(mort, "buy backpack")
        sold = _safe_cmd(mort, "sell backpack")
        assert "You buy a backpack." in bought and "You sell a backpack for" in sold, (
            f"Regular shop buy/sell broken after adding SHOP_FIX:\nbuy={bought}\nsell={sold}"
        )

        _cleanup_client(mort, quit_char=True)
        mort = None
        _goto_checked(imm, 100)
        _safe_drain(imm, 0.25)
        f_out = _safe_cmd(imm, "foldarea newacad.are")
        assert "Done." in f_out, f"foldarea newacad.are failed:\n{f_out}"
        _cleanup_client(imm, quit_char=True)
        imm = None

        shut1 = _safe_shutdown_proc(proc1)
        assert shut1.clean_exit, f"Expected clean_exit on proc1: {shut1}"
        ctx.assert_clean_sanitizers(shut1)
        proc2 = _safe_reboot(ctx, proc1.work_dir)
        boot2 = proc2.boot_metrics
        assert boot2 is not None
        des_bugs = [b for b in boot2.bug_lines if "Room flag not DES" in b]
        assert not des_bugs, f"AUD-162 foldarea boot bugs:\n{des_bugs}"

        imm = _login_imm(ctx, port=proc2.port)
        st = _safe_cmd(imm, "shopstat 10370")
        assert "buy" in st.lower() and "fix" in st.lower() and "150" in st, (
            f"Unified SHOP_DATA buy+fix flags did not survive foldarea reboot:\n{st}"
        )
        _goto_checked(imm, 10321)
        _safe_cmd(imm, "oinvoke 10308")
        _safe_cmd(imm, "oset vest cost 1000")
        _safe_cmd(imm, "oset vest value0 1")
        app2 = _safe_cmd(imm, "appraise vest")
        assert re.search(r"cost\s+[1-9]\d*\s+credit", app2, re.IGNORECASE), (
            f"Post-reboot appraise failed on #10370:\n{app2}"
        )
    finally:
        _cleanup_client(mort, quit_char=True)
        _cleanup_client(imm, quit_char=True)
        _safe_stop_proc(proc1)
        _safe_stop_proc(proc2)


@test_case(
    "T3-12",
    3,
    "F12",
    "F12+F15: Crafted spice sold at NPC shop (#10370) and merchandised through a Player Vendor",
    fresh_server=True,
)
def test_t3_12(ctx: E2EContext) -> None:
    proc = _safe_start_fresh(ctx, "t3-12-craft-economy")
    imm = _login_imm(ctx, port=proc.port)
    crafter: TelnetClient | None = None
    buyer: TelnetClient | None = None
    try:
        _safe_cmd(imm, "cfedit spice", require_prompt=True)
        _safe_cmd(imm, "timer 1", require_prompt=True)
        _safe_cmd(imm, "stop save", require_prompt=True)
        _safe_cmd(imm, "rset 11 flags plr_shop")  # ROOM_PLR_SHOP
        _safe_cmd(imm, "shopset 10370", require_prompt=True)
        _safe_cmd(imm, "itypes +spice", require_prompt=True)
        _safe_cmd(imm, "stop save", require_prompt=True)

        crafter, cname = _create_authed_mortal(ctx, imm, port=proc.port, level=55, gold=2000000, room=10400)
        buyer, _ = _create_authed_mortal(ctx, imm, port=proc.port, level=5, gold=10000, room=11)
        _safe_cmd(imm, f"sset {cname} 'spice refining' 100")

        _goto_checked(imm, 10400)
        for _ in range(2):
            _safe_cmd(imm, "oinvoke 10487")
            _safe_cmd(imm, "oset carsanum type rawspice")
            _safe_cmd(imm, f"give carsanum {cname}")
        _safe_drain(crafter, 0.25)

        for _ in range(2):
            _safe_cmd(crafter, "craft")
            _safe_cmd(crafter, "select spice")
            _safe_cmd(crafter, "select auto")
            done = False
            for _ in range(3):
                bld = _safe_cmd(crafter, "build")
                if "cannot figure out" in bld.lower():
                    continue
                assert "You begin working" in bld, f"Spice craft build start failed:\n{bld}"
                _poll_until(crafter, r"You finish your work\.", total_timeout=10.0, proc=proc)
                done = True
                break
            assert done, "Spice craft failed 3 times"
            _safe_cmd(crafter, "cancel")

        _safe_cmd(imm, f"transfer {cname} 10321")
        _safe_drain(crafter, 0.2)
        val = _safe_cmd(crafter, "value carsanum")
        assert "I'll give you" in val, f"Shop did not quote crafted spice:\n{val}"
        sold = _safe_cmd(crafter, "sell carsanum")
        assert "You sell" in sold, f"Shop did not buy crafted spice:\n{sold}"

        deed = _safe_cmd(crafter, "buyvendor")
        assert "contract for a vendor" in deed.lower(), f"buyvendor failed:\n{deed}"
        _safe_cmd(imm, f"transfer {cname} 11")
        _safe_drain(crafter, 0.2)
        _safe_cmd(crafter, "placevendor")
        _safe_cmd(crafter, "give carsanum vendor")
        pr = _safe_cmd(crafter, "pricevendor carsanum 300")
        assert "price has been changed" in pr.lower(), f"pricevendor failed:\n{pr}"
        _safe_drain(buyer, 0.3)

        vl = _safe_cmd(buyer, "list")
        assert "300" in vl and "carsanum" in vl.lower(), f"Crafted spice missing from vendor list:\n{vl}"
        vb = _safe_cmd(buyer, "buy carsanum")
        assert "You buy" in vb, f"Vendor purchase of crafted spice failed:\n{vb}"
        _safe_drain(crafter, 0.3)
        col = _safe_cmd(crafter, "getcredits")
        assert "hands over his earnings" in col.lower(), f"getcredits failed:\n{col}"
    finally:
        _cleanup_client(buyer, quit_char=True)
        _cleanup_client(crafter, quit_char=True)
        _cleanup_client(imm, quit_char=True)
        _safe_stop_proc(proc)


@test_case(
    "T3-14",
    3,
    "F14",
    "F14+F16: sedit custom social + alias execution + dnd toggle + multi-comment post/read all (AUD-264)",
)
def test_t3_14(ctx: E2EContext) -> None:
    imm = _login_imm(ctx)
    mort: TelnetClient | None = None
    soc = "t3soc"
    try:
        _safe_cmd(imm, f"sedit {soc} delete", prompt_pattern=r"(?:Deleted|Social not found)\.")
        mort, mname = _create_authed_mortal(ctx, imm, room=10300)
        _goto_checked(imm, 10300)

        # Room programs can emit a prompt before the command response arrives.
        _safe_cmd(imm, f"sedit {soc} create", prompt_pattern=r"Social added\.")
        _safe_cmd(
            imm, f"sedit {soc} cnoarg You flash a T3_SOC_TOKEN signal.", prompt_pattern=r"Done\."
        )
        _safe_cmd(
            imm, f"sedit {soc} onoarg $n flashes a T3_SOC_OTHER signal.", prompt_pattern=r"Done\."
        )

        _safe_cmd(mort, f"alias t3call {soc}", prompt_pattern=r"Created Alias\.")
        _safe_cmd(mort, "t3call", prompt_pattern=r"You flash a T3_SOC_TOKEN signal\.")
        _safe_read_until(imm, rf"{re.escape(mname)} flashes a T3_SOC_OTHER signal\.")

        _safe_cmd(imm, "dnd", prompt_pattern=r"Your 'do not disturb' flag is now on\.")
        _safe_cmd(imm, "dnd", prompt_pattern=r"Your 'do not disturb' flag is now off\.")

        for subj, body in (("T3CmtOne", "BodyOneLine"), ("T3CmtTwo", "BodyTwoLine")):
            _safe_cmd(imm, f"comment subject {subj}", prompt_pattern=r"Ok\.")
            _safe_cmd(imm, "comment to all", prompt_pattern=r"Ok\.")
            _safe_cmd(imm, "comment write", prompt_pattern=r"(?s)Begin entering your text.*?\n> ")
            # The editor prints a bare prompt after a body line. List the buffer
            # to check that the line was stored even when room output follows it.
            imm.send_line(body)
            _safe_cmd(imm, "/l", prompt_pattern=rf"\n *1> {re.escape(body)}\r?\n")
            _safe_cmd(imm, "/s", prompt_pattern=r"Done\.")
            _safe_cmd(imm, f"comment post {mname}", prompt_pattern=r"Ok\.")

        _safe_cmd(
            imm, f"comment list {mname}", prompt_pattern=r"(?s)\A(?=.*T3CmtOne)(?=.*T3CmtTwo)"
        )
        _safe_cmd(
            imm, f"comment read {mname} all", prompt_pattern=r"(?s)\A(?=.*BodyOneLine)(?=.*BodyTwoLine)"
        )
    finally:
        try:
            _safe_cmd(imm, f"sedit {soc} delete", prompt_pattern=r"(?:Deleted|Social not found)\.")
            _safe_cmd(imm, "goto 100")
        except Exception:
            pass
        _cleanup_client(mort, quit_char=True)
        _cleanup_client(imm, quit_char=True)


@test_case(
    "T3-15",
    3,
    "F15",
    "F15+F17: Clan & planet creation + capture + empower pilot -> clan-owned ship flight authorization",
    fresh_server=True,
)
def test_t3_15(ctx: E2EContext) -> None:
    proc = _safe_start_fresh(ctx, "t3-15-clan-ship")
    imm = _login_imm(ctx, port=proc.port)
    leader: TelnetClient | None = None
    pilot: TelnetClient | None = None
    try:
        pad_vnum, _, _, _ = _discover_non_trainer_pad(imm)

        _safe_cmd(imm, "makeclan T3Clan")
        _safe_cmd(imm, "setclan T3Clan filename t3clan.clan")
        _safe_cmd(imm, "setclan T3Clan funds 5000")
        _safe_cmd(imm, "makeplanet T3Planet")
        _safe_cmd(imm, "setplanet T3Planet filename t3planet.planet")
        _safe_cmd(imm, "setplanet T3Planet base_value 1000")
        _safe_cmd(imm, "aset omni_complex planet T3Planet")

        leader, lname = _create_authed_mortal(ctx, imm, port=proc.port, level=10, room=100)
        pilot, pname = _create_authed_mortal(ctx, imm, port=proc.port, level=10, room=pad_vnum)
        _safe_cmd(imm, f"mset {lname} clan T3Clan")
        _safe_cmd(imm, f"mset {pname} clan T3Clan")
        _safe_cmd(imm, f"setclan T3Clan leader {lname}")
        _safe_drain(leader, 0.25)
        _safe_cmd(imm, f"sset {pname} starfighters 100")
        _safe_cmd(imm, f"sset {pname} 'ship systems' 100")

        _goto_checked(imm, 32000)
        _safe_cmd(imm, "ordership 1 T3ClanWing")
        _safe_cmd(imm, "setship T3ClanWing owner T3Clan")
        _safe_cmd(imm, f"setship T3ClanWing shipyard {pad_vnum}")
        _safe_cmd(imm, "setship T3ClanWing energy 5000")
        _safe_cmd(imm, "setship T3ClanWing speed 150")
        _safe_cmd(imm, "resetship T3ClanWing")

        # Unempowered clan member must be rejected before empower
        _safe_drain(pilot, 0.25)
        denied = _safe_cmd(
            pilot, "openhatch T3ClanWing",
            prompt_pattern=r"(?is)(?:don't have permission|no T3ClanWing here).*?Health:[^>]*>",
        )
        assert "don't have permission" in denied.lower(), denied

        _safe_drain(leader, 0.25)
        emp = _safe_cmd(leader, f"empower {pname} pilot")
        assert "ability to fly clan ships" in emp.lower(), f"empower pilot failed:\n{emp}"
        cap = _safe_cmd(leader, "capture")
        assert "claimed the planet" in cap.lower() and "T3Planet" in cap, f"planet capture failed:\n{cap}"
        pl_out = _safe_cmd(imm, "planets")
        assert "T3Planet" in pl_out and "T3Clan" in pl_out, f"Planet ownership missing:\n{pl_out}"

        _safe_drain(pilot, 0.25)
        oh = _safe_cmd(
            pilot, "openhatch T3ClanWing",
            prompt_pattern=r"(?is)(?:open the hatch|already open).*?Health:[^>]*>",
        )
        assert "open" in oh.lower(), f"Empowered pilot openhatch failed:\n{oh}"
        brd = _safe_cmd(pilot, "board T3ClanWing")
        assert "enter" in brd.lower() or "T3ClanWing" in brd, f"Empowered pilot board failed:\n{brd}"
        lnch = _safe_cmd(pilot, "launch")
        assert "launch" in lnch.lower() or "flies into space" in lnch.lower(), f"launch failed:\n{lnch}"
        _poll_until(pilot, r"Launch complete\.", total_timeout=25.0, proc=proc)
    finally:
        _cleanup_client(pilot, quit_char=True)
        _cleanup_client(leader, quit_char=True)
        _cleanup_client(imm, quit_char=True)
        _safe_stop_proc(proc)


@test_case(
    "T3-16",
    3,
    "F16",
    "F16+F09: Bounty + ROOM_ARENA (#29) non-lethal KO vs non-hunter & hunter-clan raw_kill claim",
    fresh_server=True,
)
def test_t3_16(ctx: E2EContext) -> None:
    proc = _safe_start_fresh(ctx, "t3-16-bounty-arena")
    imm = _login_imm(ctx, port=proc.port)
    poster: TelnetClient | None = None
    victim: TelnetClient | None = None
    nonhunter: TelnetClient | None = None
    hunter: TelnetClient | None = None
    listing_prompt = re.compile(
        r"^Bounty[ \t]+Amount[ \t]+Poster[ \t]*\r?\n.*?" + DEFAULT_PROMPT_RE.pattern,
        re.MULTILINE | re.DOTALL,
    )

    def listed_bounties(client: TelnetClient) -> dict[str, int]:
        listing = _safe_cmd(client, "bounties", prompt_pattern=listing_prompt)
        rows = re.findall(r"^(\S+)[ \t]+(\d+)[ \t]+\S+[ \t]*\r?$", listing, re.MULTILINE)
        return {name: int(amount) for name, amount in rows}

    try:
        poster, _ = _create_authed_mortal(ctx, imm, port=proc.port, gold=20000, room=10300)
        victim, vname = _create_authed_mortal(ctx, imm, port=proc.port, level=10, gold=3000, room=10001)
        cloned = _safe_cmd(victim, "clone")
        assert "You pay 2000 credits for cloning." in cloned, cloned
        _safe_cmd(imm, f"transfer {vname} 10300")
        nonhunter, nhname = _create_authed_mortal(ctx, imm, port=proc.port, level=25, gold=100, room=11)
        hunter, hname = _create_authed_mortal(ctx, imm, port=proc.port, level=25, gold=100, room=10301)
        membership = _safe_cmd(imm, f"mset {hname} clan The Bounty Hunter's Guild")
        assert "Done." in membership, membership
        _safe_cmd(imm, f"mset {nhname} hitroll 25")
        _safe_cmd(imm, f"mset {nhname} damroll 65")
        _safe_cmd(imm, "oinvoke 10422")
        _safe_cmd(imm, "oset prototype name t3nonhuntblade")
        _safe_cmd(imm, "oset t3nonhuntblade value0 12")
        _safe_cmd(imm, "oset t3nonhuntblade value1 800")
        _safe_cmd(imm, "oset t3nonhuntblade value2 800")
        _safe_cmd(imm, f"at {nhname} give t3nonhuntblade {nhname}")
        _safe_drain(nonhunter, 0.25)
        _safe_cmd(nonhunter, "remove vibro-blade")
        _safe_cmd(nonhunter, "wield t3nonhuntblade")
        equipped = _safe_cmd(nonhunter, "equipment", prompt_pattern=r"(?is)You are using:.*?Health:[^>]*>")
        assert "prototype vibro-blade" in equipped.lower(), equipped
        _safe_cmd(imm, f"mset {hname} hitroll 25")
        _safe_cmd(imm, f"mset {hname} damroll 65")
        _safe_cmd(imm, "oinvoke 10422")
        _safe_cmd(imm, "oset prototype name t3huntblade")
        _safe_cmd(imm, "oset t3huntblade value0 12")
        _safe_cmd(imm, "oset t3huntblade value1 800")
        _safe_cmd(imm, "oset t3huntblade value2 800")
        _safe_cmd(imm, f"at {hname} give t3huntblade {hname}")
        _safe_drain(hunter, 0.25)
        _safe_cmd(hunter, "remove vibro-blade")
        _safe_cmd(hunter, "wield t3huntblade")
        equipped = _safe_cmd(hunter, "equipment", prompt_pattern=r"(?is)You are using:.*?Health:[^>]*>")
        assert "prototype vibro-blade" in equipped.lower(), equipped
        _safe_drain(poster, 0.3)

        b_out = _safe_cmd(poster, f"addbounty {vname} 5000")
        assert "5000 credits" in b_out, f"addbounty failed:\n{b_out}"
        _safe_drain(hunter, 0.2)
        listing = listed_bounties(hunter)
        assert listing.get(vname) == 5000, f"Hunter could not see the posted bounty: {listing}"

        # Phase A: Arena KO in ROOM_ARENA (#29) preserves bounty & leaves no corpse (AUD-184)
        _safe_cmd(hunter, f"challenge {vname}")
        _safe_drain(victim, 0.25)
        _safe_cmd(victim, "accept")
        _safe_drain(poster, 0.2)
        _safe_cmd(poster, f"bet {hname} 100")
        _safe_cmd(imm, f"transfer {hname} 29")
        _safe_cmd(imm, f"transfer {vname} 29")
        _safe_cmd(imm, f"mset {vname} hp 1")
        _safe_cmd(imm, f"restore {vname}")
        _safe_drain(hunter, 0.25)
        _safe_cmd(hunter, f"murder {vname}")
        time.sleep(1.5)
        _safe_drain(hunter, 0.4)
        _safe_drain(victim, 0.4)

        lk_29 = _safe_cmd(imm, "at 29 look")
        assert f"corpse of {vname.lower()}" not in lk_29.lower(), (
            f"AUD-184 regression: corpse created inside ROOM_ARENA #29:\n{lk_29}"
        )
        bl_a = listed_bounties(imm)
        assert bl_a.get(vname) == 5000, f"Arena KO did not preserve the bounty: {bl_a}"

        # Phase B: Non-hunter kill in #11 preserves bounty (AUD-183)
        _safe_cmd(imm, f"transfer {vname} 11")
        _safe_cmd(imm, f"mset {vname} hp 1")
        _safe_cmd(imm, f"restore {vname}")
        _safe_drain(nonhunter, 0.25)
        _safe_cmd(nonhunter, f"murder {vname}")
        _poll_until_dead(nonhunter, imm, vname, max_wait=25.0)
        bl_b = listed_bounties(imm)
        assert bl_b.get(vname) == 5000, f"AUD-183: non-hunter kill did not preserve the bounty: {bl_b}"

        # Phase C: Respawn the same target so the hunter can claim the original bounty.
        _safe_drain(victim, 0.4)
        assert victim.eof, "Non-hunter kill did not close the original target connection"
        _cleanup_client(victim)
        victim = _safe_connect(ctx, port=proc.port, timeout=5.0)
        _safe_login(victim, vname, MORT_PASS, timeout=5.0)
        _safe_cmd(imm, f"transfer {vname} 11")
        _safe_cmd(imm, f"transfer {hname} 11")
        _safe_cmd(imm, f"mset {vname} hp 1")
        _safe_cmd(imm, f"restore {vname}")
        _safe_drain(hunter, 0.25)
        hunter.send_line(f"murder {vname}")
        _poll_until(
            hunter,
            rf"You receive \d+ experience and 5000 credits,\s+from the bounty on {re.escape(vname)}\b",
            total_timeout=25.0,
            proc=proc,
        )
        bl_c = listed_bounties(hunter)
        assert vname not in bl_c, f"Claimed bounty still listed: {bl_c}"
    finally:
        _cleanup_client(hunter, quit_char=True)
        _cleanup_client(nonhunter, quit_char=True)
        _cleanup_client(victim, quit_char=True)
        _cleanup_client(poster, quit_char=True)
        _cleanup_client(imm, quit_char=True)
        _safe_stop_proc(proc)


@test_case(
    "T3-17",
    3,
    "F17",
    "F17+F06: Ship boarding, launch, hyperspace, and landing driving GMCP Room.Info & prompt tokens",
    fresh_server=True,
)
def test_t3_17(ctx: E2EContext) -> None:
    proc = _safe_start_fresh(ctx, "t3-17-space-gmcp")
    imm = _safe_connect(ctx, port=proc.port, timeout=5.0)
    try:
        _safe_login_with_options(
            imm,
            IMM_NAME,
            IMM_PASS,
            timeout=5.0,
            enable_gmcp=True,
            gmcp_supports=["Char 1", "Room 1"],
        )
        _safe_drain(imm, 0.15)
        _safe_cmd(imm, "speak common")

        pad_vnum, land_loc, target_sys, _ = _discover_non_trainer_pad(imm)
        for sk in ("starfighters", "midships", "navigation", "'ship systems'"):
            _safe_cmd(imm, f"sset {IMM_NAME} {sk} 100")

        _goto_checked(imm, 32000)
        _safe_cmd(imm, "ordership 1 T3GmcpWing")
        for fld, val in (
            ("speed", 200),
            ("hyperspeed", 100),
            ("energy", 10000),
            ("hull", 1000),
            ("sensor", 50),
            ("astroarray", 300),
            ("shipyard", pad_vnum),
        ):
            _safe_cmd(imm, f"setship T3GmcpWing {fld} {val}")
        _safe_cmd(imm, "resetship T3GmcpWing")
        _goto_checked(imm, pad_vnum)
        custom_prompt = re.compile(r"\d+/\d+\s+\d+/\d+>")
        p_out = _safe_cmd(imm, "prompt %h/%H %m/%M>", prompt_pattern=custom_prompt)
        assert custom_prompt.search(p_out), f"Custom prompt tokens did not render:\n{p_out}"
        _safe_drain(imm, 0.25)

        ev_idx = len(imm.gmcp_events)
        _safe_cmd(imm, "openhatch T3GmcpWing", prompt_pattern=custom_prompt)
        _safe_cmd(imm, "board T3GmcpWing", prompt_pattern=custom_prompt)
        _safe_drain(imm, 0.25)

        lnch = _safe_cmd(imm, "launch", prompt_pattern=custom_prompt)
        assert "launch" in lnch.lower() or "flies into space" in lnch.lower(), f"launch failed:\n{lnch}"
        _poll_until(imm, r"Launch complete\.", total_timeout=25.0, proc=proc)

        calc = _safe_cmd(imm, f"calculate {target_sys}", prompt_pattern=custom_prompt)
        assert "Hyperspace course set" in calc, f"calculate failed:\n{calc}"
        # Calculate applies two combat pulses (six seconds) of command lag.
        jump = _safe_cmd(imm, "hyperspace", timeout=12.0, prompt_pattern=custom_prompt)
        assert "you enter hyperspace" in jump.lower(), f"hyperspace did not engage:\n{jump}"
        if "Hyperjump complete." not in jump:
            _poll_until(imm, r"Hyperjump complete\.", total_timeout=30.0, proc=proc)

        landing = _safe_cmd(imm, f"land {land_loc}", prompt_pattern=custom_prompt)
        landed = r"Landing sequence complete\."
        if not re.search(landed, landing):
            _poll_until(imm, landed, total_timeout=30.0, proc=proc)
        _safe_cmd(imm, "openhatch", prompt_pattern=custom_prompt)
        leave_out = _safe_cmd(imm, "leave", prompt_pattern=custom_prompt)
        assert custom_prompt.search(leave_out), f"Custom prompt lost across ship transitions:\n{leave_out}"
        _safe_drain(imm, 0.25)

        invalid = [ev for ev in imm.gmcp_events if not ev.json_valid]
        assert not invalid, f"Invalid GMCP JSON during ship flight: {invalid[:3]}"
        room_nums = [
            ev.data.get("num")
            for ev in imm.gmcp_events[ev_idx:]
            if ev.module == "Room.Info" and isinstance(ev.data, dict)
        ]
        assert room_nums, "No Room.Info GMCP events emitted across board/leave"
        assert any(n != pad_vnum for n in room_nums) and room_nums[-1] == pad_vnum, (
            f"Expected Room.Info transition pad({pad_vnum}) -> ship -> pad({pad_vnum}), got {room_nums}"
        )
    finally:
        _cleanup_client(imm, quit_char=True)
        _safe_stop_proc(proc)


@test_case(
    "T3-18",
    3,
    "F18",
    "F18+F07: Static scanners + Makefile -DMCCP parity verified against live commands, slist, areas & MSSP",
)
def test_t3_18(ctx: E2EContext) -> None:
    scan_res = ctx.run_static_scanners(mode="post-remediation")
    failing = {k: v["summary"] for k, v in scan_res["scanners"].items() if not v["passed"]}
    assert scan_res["all_passed"], f"Post-remediation static scanners failed: {failing}"

    mk = parse_makefile(ctx.repo_path)
    assert mk.has_d_mccp, "src/Makefile missing -DMCCP"
    assert not mk.has_d_requests, "src/Makefile still defines -DREQUESTS"

    cli = _safe_connect(ctx, timeout=5.0)
    try:
        try:
            mssp = cli.request_mssp(timeout=4.0)
        except Exception as exc:
            raise AssertionError(f"MSSP request failed: {exc}") from exc
        assert mssp.get("PORT") == str(ctx.shared_server.port), (
            f"MSSP PORT mismatch: {mssp.get('PORT')} != {ctx.shared_server.port}"
        )
        assert int(mssp.get("AREAS", "0")) >= 40, f"Expected MSSP AREAS >= 40, got {mssp}"

        _safe_login(cli, IMM_NAME, IMM_PASS, timeout=5.0)
        try:
            cli.enable_mccp2(wait=True, timeout=3.0)
        except Exception as exc:
            raise AssertionError(f"MCCP2 parity check failed against -DMCCP build: {exc}") from exc
        assert cli.mccp2_active, "MCCP2 did not activate despite -DMCCP in Makefile"

        for public in ("murder", "openhatch", "lookmap"):
            cmds = _safe_cmd(cli, f"commands {public}")
            assert public in cmds.lower().split(), f"Command {public!r} missing from public listing: {cmds}"
        for staff in ("dnd", "shipdelete", "restoreship", "checkareaships", "maketemplateship", "trivia"):
            registration = _safe_cmd(cli, f"cedit {staff} show")
            assert re.search(rf"^Command:[ \t]+{staff}\s*$", registration, re.MULTILINE), registration
            assert re.search(rf"^Code:[ \t]+do_{staff}\s*$", registration, re.MULTILINE), registration
        skills = (ctx.repo_path / "system" / "skills.dat").read_text(encoding="utf-8")
        for skill in ("hitall", "throw", "sneak"):
            assert re.search(
                rf"(?m)^Name\s+{skill}~\n(?:(?!#SKILL).|\n)*?^Code\s+do_{skill}$", skills
            ), f"Missing {skill} skill handler in skills.dat"
        # Wave 4 retired the unwired pardon stub rather than exposing it.
        for retired in ("pardon", "reserved"):
            registration = _safe_cmd(cli, f"cedit {retired} show")
            assert "Command not found." in registration, registration
    finally:
        try:
            if cli.mccp2_active:
                cli.send_iac(DONT, TELOPT_COMPRESS2)
        except Exception:
            pass
        _cleanup_client(cli, quit_char=True)


@test_case(
    "T3-19",
    3,
    "F02",
    "F02+F11: Multi-word BitSet flags (ROOM_AUCTION=31, ITEM_HIDDEN=281) -> foldarea -> cold reboot reload",
    fresh_server=True,
)
def test_t3_19(ctx: E2EContext) -> None:
    proc1 = _safe_start_fresh(ctx, "t3-19-bitset-fold")
    imm = _login_imm(ctx, port=proc1.port)
    proc2: SwripProcess | None = None
    try:
        _goto_checked(imm, 11)
        _safe_cmd(imm, "redit2", require_prompt=True)
        _safe_cmd(imm, "flags +auction +plr_shop", require_prompt=True)
        _safe_cmd(imm, "stop save", require_prompt=True)

        _safe_cmd(imm, "oinvoke 10423")
        _safe_cmd(imm, "oset key flags prototype")
        _safe_cmd(imm, "oedit key", require_prompt=True)
        _safe_cmd(imm, "flags +Hidden +Returning", require_prompt=True)
        _safe_cmd(imm, "stop save", require_prompt=True)

        assert "Done." in _safe_cmd(imm, "foldarea limbo.are")
        assert "Done." in _safe_cmd(imm, "foldarea newacad.are")
        _cleanup_client(imm, quit_char=True)
        imm = None

        shut1 = _safe_shutdown_proc(proc1)
        assert shut1.clean_exit, f"Expected clean_exit on proc1: {shut1}"
        ctx.assert_clean_sanitizers(shut1)
        proc2 = _safe_reboot(ctx, proc1.work_dir)
        boot2 = proc2.boot_metrics
        assert boot2 is not None
        des_bugs = [b for b in boot2.bug_lines if "Room flag not DES" in b]
        assert not des_bugs, f"AUD-162 foldarea boot error:\n{des_bugs}"

        imm = _login_imm(ctx, port=proc2.port)
        rstat = _safe_cmd(imm, "rstat 11")
        assert "auction" in rstat.lower() and "plr_shop" in rstat.lower(), (
            f"Room BitSet flags (BV31/BV28) lost across foldarea reboot:\n{rstat}"
        )
        _safe_cmd(imm, "oinvoke 10423")
        ostat = _safe_cmd(imm, "ostat key")
        assert "hidden" in ostat.lower() and "returning" in ostat.lower(), (
            f"AUD-100/AUD-242 regression: multi-word BitSet obj flags (>32 bits) lost:\n{ostat}"
        )
    finally:
        _cleanup_client(imm, quit_char=True)
        _safe_stop_proc(proc1)
        _safe_stop_proc(proc2)


@test_case(
    "T3-20",
    3,
    "F04",
    "F04+F16: Abrupt TCP socket close mid-trivia and mid-tell/retell (extract_char + free_desc safety)",
)
def test_t3_20(ctx: E2EContext) -> None:
    imm = _login_imm(ctx)
    leaver: TelnetClient | None = None
    survivor: TelnetClient | None = None
    try:
        leaver, lname = _create_authed_mortal(ctx, imm, room=10300)
        survivor, sname = _create_authed_mortal(ctx, imm, room=10300)

        st = _safe_cmd(imm, "trivia start 10311")
        assert "new trivia game has begun" in st.lower(), f"trivia start failed:\n{st}"
        _safe_cmd(leaver, "tjoin")
        _safe_cmd(survivor, "tjoin")

        _safe_cmd(imm, f"tell {lname} T3TellSetup")
        _safe_drain(leaver, 0.2)
        _safe_cmd(imm, "retell T3RetellBefore")
        _safe_cmd(survivor, f"tell {lname} T3SurvTell")

        # Hard close leaver TCP socket without quit (linkdead while in g_trivia)
        leaver.close()
        leaver = None
        time.sleep(0.4)

        _safe_cmd(imm, "tquestion What is 1+1?")
        _safe_cmd(survivor, "tanswer 2")
        tw = _safe_cmd(imm, f"twinner {sname}")
        assert f"{sname} has won" in tw, f"twinner failed with linkdead participant (AUD-008):\n{tw}"
        ts = _safe_cmd(imm, "tscore")
        assert sname in ts, f"tscore failed with linkdead participant:\n{ts}"

        # Verify the reconnected player receives ordinary messages, then logs out at a hotel.
        leaver = _safe_connect(ctx, timeout=5.0)
        _safe_login(leaver, lname, MORT_PASS, timeout=5.0)
        _safe_drain(imm, 0.25)
        _safe_cmd(imm, f"tell {lname} T3BackOnline")
        heard = _poll_until(leaver, r"T3BackOnline", total_timeout=5.0)
        assert "T3BackOnline" in heard
        _safe_cmd(imm, f"transfer {lname} 122")
        _safe_drain(leaver, 0.25)
        _safe_cmd(leaver, "quit", wait_prompt=False)
        deadline = time.monotonic() + 3.0
        while not leaver.eof and time.monotonic() < deadline:
            _safe_drain(leaver, 0.2)
        assert leaver.eof, "Hotel logout did not close the connection"
        ctx.assert_clean_sanitizers()
    finally:
        try:
            _safe_cmd(imm, "trivia end")
        except Exception:
            pass
        _cleanup_client(survivor, quit_char=True)
        _cleanup_client(leaver, quit_char=True)
        _cleanup_client(imm, quit_char=True)


@test_case(
    "T3-21",
    3,
    "F08",
    "F08+F13: Player clone at #10001 -> combat death (raw_kill corpse) -> loot -> quit extract_char cleanup",
)
def test_t3_21(ctx: E2EContext) -> None:
    imm = _login_imm(ctx)
    mort: TelnetClient | None = None
    killer: TelnetClient | None = None
    try:
        _goto_checked(imm, 10001)
        mort, mname = _create_authed_mortal(ctx, imm, level=1, gold=5000, room=10001)
        killer, kname = _create_authed_mortal(ctx, imm, level=25, gold=100, room=10308)
        _safe_cmd(imm, f"mset {kname} hitroll 25")
        _safe_cmd(imm, f"mset {kname} damroll 65")
        _safe_cmd(imm, "oinvoke 10422")
        _safe_cmd(imm, "oset prototype name t3cloneblade")
        _safe_cmd(imm, "oset t3cloneblade value0 12")
        _safe_cmd(imm, "oset t3cloneblade value1 800")
        _safe_cmd(imm, "oset t3cloneblade value2 800")
        _safe_cmd(imm, f"at {kname} give t3cloneblade {kname}")
        _safe_drain(killer, 0.25)
        _safe_cmd(killer, "remove vibro-blade")
        _safe_cmd(killer, "wield t3cloneblade")
        equipped = _safe_cmd(killer, "equipment", prompt_pattern=r"(?is)You are using:.*?Health:[^>]*>")
        assert "prototype vibro-blade" in equipped.lower(), equipped

        cln = _safe_cmd(mort, "clone")
        assert "You pay" in cln and "credits for cloning." in cln, (
            f"AUD-152 clone failed at #10001:\n{cln}"
        )

        _goto_checked(imm, 10308)
        _safe_cmd(imm, "oinvoke 10423")
        _safe_cmd(imm, f"give key {mname}")
        _safe_cmd(imm, f"transfer {mname} 10308")
        _safe_cmd(imm, f"mset {mname} hp 1")
        _safe_cmd(imm, f"restore {mname}")
        _safe_drain(mort, 0.2)

        _safe_cmd(killer, f"murder {mname}")
        _poll_until_dead(killer, imm, mname, max_wait=25.0)

        lk = _safe_cmd(killer, "look")
        assert "corpse" in lk.lower(), f"Expected corpse of cloned player in #10308:\n{lk}"
        loot = _safe_cmd(killer, "get all corpse")
        assert "no corpse" not in loot.lower(), f"Loot failed:\n{loot}"

        _safe_drain(mort, 0.3)
        assert mort.eof, "Lethal combat did not close the original player connection"
        _cleanup_client(mort)
        mort = _safe_connect(ctx, timeout=5.0)
        _safe_login(mort, mname, MORT_PASS, timeout=5.0)
        _safe_cmd(imm, f"transfer {mname} 122")
        _safe_drain(mort, 0.25)
        _safe_cmd(mort, "quit", wait_prompt=False)
        _cleanup_client(mort)
        mort = None
        time.sleep(0.3)

        who = _safe_cmd(imm, "who")
        who_body = "\n".join(ln for ln in who.splitlines() if not ln.lstrip().startswith(("Comm:", "Log:")))
        assert mname.lower() not in who_body.lower(), f"Quit character {mname} still in who:\n{who}"
    finally:
        try:
            _goto_checked(imm, 10308)
            _safe_cmd(imm, "peace")
            _safe_cmd(imm, "purge")
            _safe_cmd(imm, "goto 100")
        except Exception:
            pass
        _cleanup_client(killer, quit_char=True)
        _cleanup_client(mort, quit_char=True)
        _cleanup_client(imm, quit_char=True)


@test_case(
    "T3-22",
    3,
    "F14",
    "F14+F17: Admin ship lifecycle (maketemplateship, checkareaships, at <firstroom>, shipdelete -> restoreship)",
    fresh_server=True,
)
def test_t3_22(ctx: E2EContext) -> None:
    proc = _safe_start_fresh(ctx, "t3-22-ship-admin")
    imm = _login_imm(ctx, port=proc.port)
    try:
        mt = _safe_cmd(imm, "maketemplateship 0 t3tmpl.ship T3TmplShip")
        assert "Huh?" not in mt, f"AUD-307 maketemplateship not wired:\n{mt}"

        _goto_checked(imm, 32000)
        _safe_cmd(imm, "ordership 0 T3AdminShuttle")
        sh = _safe_cmd(imm, "showship T3AdminShuttle")
        assert "No such ship." not in sh, f"ordership 0 T3AdminShuttle failed:\n{sh}"
        m_room = re.search(r"Firstroom:\s*(\d+)", sh)
        m_file = re.search(r"Filename:\s*(\S+)", sh)
        assert m_room and int(m_room.group(1)) > 0, f"Could not parse Firstroom:\n{sh}"
        firstroom = int(m_room.group(1))
        ship_fname = m_file.group(1).strip() if m_file else ""

        at_in = _safe_cmd(imm, f"at {firstroom} look")
        assert "No such location." not in at_in, f"at {firstroom} look failed inside ship:\n{at_in}"

        chk = _safe_cmd(imm, "checkareaships space.are")
        assert "Huh?" not in chk and "rooms found." in chk.lower(), (
            f"AUD-307 checkareaships failed:\n{chk}"
        )
        gr = _safe_cmd(imm, "grub 5")
        assert "Huh?" not in gr, f"grub 5 failed:\n{gr}"

        sd = _safe_cmd(imm, "shipdelete T3AdminShuttle")
        assert "Huh?" not in sd, f"AUD-307 shipdelete not wired:\n{sd}"
        assert "No such ship." in _safe_cmd(imm, "showship T3AdminShuttle"), (
            "Deleted ship still present in showship"
        )
        at_gone = _safe_cmd(imm, f"at {firstroom} look")
        assert "No such location." in at_gone, (
            f"AUD-073 regression: shipdelete left dangling room #{firstroom}:\n{at_gone}"
        )

        if not ship_fname:
            backup_dir = proc.work_dir / "space" / "backup"
            if backup_dir.is_dir():
                cands = sorted(p.name for p in backup_dir.iterdir() if p.is_file())
                if cands:
                    ship_fname = cands[-1]
        assert ship_fname, "Could not determine backed-up filename after shipdelete"

        rs = _safe_cmd(imm, f"restoreship {ship_fname}")
        assert "Huh?" not in rs and "not found" not in rs.lower(), (
            f"AUD-307 restoreship {ship_fname!r} failed:\n{rs}"
        )
        sh_restored = _safe_cmd(imm, "showship T3AdminShuttle")
        assert "No such ship." not in sh_restored, (
            f"restoreship did not restore T3AdminShuttle:\n{sh_restored}"
        )
    finally:
        _cleanup_client(imm, quit_char=True)
        _safe_stop_proc(proc)
