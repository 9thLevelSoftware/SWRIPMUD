"""Tier 1 (happy-path) E2E tests for Features F10–F18 (55 tests)."""

from __future__ import annotations

import ctypes
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from tests.e2e.framework import E2EContext, test_case
from tests.e2e.static_scanners import DEAD_FILES_TO_REMOVE, parse_makefile
from tests.e2e.telnet_client import DEFAULT_PROMPT_RE, TelnetClient, strip_ansi
from tests.e2e.wsl_env import SwripProcess, allocate_port_block

# ---------------------------------------------------------------------------
# Diagnostic-safe helpers (never let Telnet/socket errors become harness ERROR)
# ---------------------------------------------------------------------------

_NAME_SEQ = 0


def _next_mortal_name(prefix: str = "Mrta") -> str:
    global _NAME_SEQ
    _NAME_SEQ += 1
    n = _NAME_SEQ
    letters: list[str] = []
    while n > 0:
        n, rem = divmod(n - 1, 26)
        letters.append(chr(ord("a") + rem))
    base = "".join(ch for ch in prefix if ch.isascii() and ch.isalpha()) or "Mrt"
    return (base + "".join(reversed(letters)))[:20]


def _safe_start_fresh(ctx: E2EContext, label: str, **kwargs: Any) -> SwripProcess:
    try:
        return ctx.start_fresh_server(label, **kwargs)
    except Exception as exc:
        raise AssertionError(f"Fresh server startup failed ({label}): {exc}") from exc


def _safe_connect(ctx: E2EContext, port: int | None = None, timeout: float = 10.0) -> TelnetClient:
    try:
        return ctx.make_client(port=port, timeout=timeout)
    except Exception as exc:
        raise AssertionError(f"Telnet connection failed (port={port}): {exc}") from exc


def _safe_login(cli: TelnetClient, name: str, password: str, timeout: float = 15.0) -> str:
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
    password: str = "E2eUserPass",
    sex: str = "M",
    race: str = "Human",
    ability: str = "combat",
    timeout: float = 20.0,
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
    timeout: float = 10.0,
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
    timeout: float = 10.0,
) -> str:
    try:
        return strip_ansi(cli.read_until(pattern, timeout=timeout))
    except Exception as exc:
        raise AssertionError(f"Timed out/EOF waiting for {pattern!r}: {exc}") from exc


def _safe_drain(cli: TelnetClient, seconds: float = 0.25) -> str:
    try:
        return strip_ansi(cli.drain(seconds=seconds))
    except Exception as exc:
        raise AssertionError(f"Drain failed: {exc}") from exc


def _login_imm(ctx: E2EContext, port: int | None = None, reset_room: bool = True) -> TelnetClient:
    cli = _safe_connect(ctx, port=port)
    _safe_login(cli, "Immtest", "E2eImmPass1")
    _safe_drain(cli, 0.15)
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
    prefix: str = "Mrta",
) -> tuple[TelnetClient, str]:
    name = _next_mortal_name(prefix)
    cli = _safe_connect(ctx, port=port)
    _safe_create(cli, name, "E2ePass1", race=race, ability=ability)
    _safe_cmd(imm, f"authorize {name}")
    if level > 1:
        _safe_cmd(imm, f"at {name} advance {name} combat {level}")
    if gold is not None:
        _safe_cmd(imm, f"mset {name} credits {gold}")
    if room is not None:
        _safe_cmd(imm, f"transfer {name} {room}")
    _safe_drain(cli, 0.3)
    return cli, name


def _cleanup_client(cli: TelnetClient | None, *, quit_char: bool = False) -> None:
    if cli is None:
        return
    try:
        if quit_char and not cli.eof:
            try:
                cli.send_line("quit")
                deadline = time.monotonic() + 1.2
                while time.monotonic() < deadline and not cli.eof:
                    cli.drain(0.1)
            except Exception:
                pass
    finally:
        cli.close()


def _safe_shutdown_proc(proc: SwripProcess) -> Any:
    try:
        return proc.shutdown_clean()
    except Exception as exc:
        raise AssertionError(f"Clean shutdown failed: {exc}") from exc


# ===========================================================================
# F10 — Force Spells, Standard Skills & SW Skills (6 tests)
# ===========================================================================


@test_case("T1-F10-01", 1, "F10", "Practice list and learning a skill at Academy teacher Domick (#10340)")
def test_t1_f10_01(ctx: E2EContext) -> None:
    imm = _login_imm(ctx)
    mort = None
    try:
        mort, _ = _create_authed_mortal(ctx, imm)
        _safe_cmd(mort, "n")
        _safe_cmd(mort, "e")
        before = _safe_cmd(mort, "practice")
        assert "scan" in before.lower(), f"Expected 'scan' in practice list, got:\n{before}"

        prac_out = _safe_cmd(mort, "practice scan")
        assert "You practice scan." in prac_out, f"Expected 'You practice scan.', got:\n{prac_out}"

        after = _safe_cmd(mort, "practice")
        m = re.search(r"scan\s+(\d+)%", after, re.IGNORECASE)
        assert m and int(m.group(1)) > 0, f"Expected scan > 0% after practicing, got:\n{after}"
    finally:
        _cleanup_client(mort)
        _cleanup_client(imm)


@test_case("T1-F10-02", 1, "F10", "Language learning at Abbigayle (#10394) and speak switching")
def test_t1_f10_02(ctx: E2EContext) -> None:
    imm = _login_imm(ctx)
    mort = None
    try:
        mort, name = _create_authed_mortal(ctx, imm)
        _safe_cmd(mort, "n")
        _safe_cmd(mort, "w")
        learn_out = _safe_cmd(mort, "languages learn huttese")
        assert (
            "lessons in huttese" in learn_out.lower()
            or "speak perfect huttese" in learn_out.lower()
        ), f"Expected huttese lesson feedback, got:\n{learn_out}"

        _safe_cmd(imm, f"sset {name} huttese 100")
        spk_hut = _safe_cmd(mort, "speak huttese")
        assert "You now speak huttese." in spk_hut, f"Expected speak huttese success, got:\n{spk_hut}"

        spk_com = _safe_cmd(mort, "speak common")
        assert "You now speak common." in spk_com, f"Expected speak common success, got:\n{spk_com}"
    finally:
        _cleanup_client(mort)
        _cleanup_client(imm)


@test_case(
    "T1-F10-03",
    1,
    "F10",
    "Force spells: cure light, force spray, and bounded force lightning",
    fresh_server=True,
)
def test_t1_f10_03(ctx: E2EContext) -> None:
    proc = _safe_start_fresh(ctx, "f10-spells")
    imm = _login_imm(ctx, port=proc.port)
    try:
        _safe_cmd(imm, "goto 10308")
        _safe_cmd(imm, "mset Immtest frc 20")
        _safe_cmd(imm, "mset Immtest force 1000")
        _safe_cmd(imm, "restore Immtest")
        _safe_cmd(imm, "mset Immtest hp 2000")
        _safe_cmd(imm, "sset Immtest 'cure light' 100")
        _safe_cmd(imm, "sset Immtest 'force spray' 100")
        _safe_cmd(imm, "sset Immtest 'force lightning' 100")
        _safe_cmd(imm, "minvoke 10340")
        _safe_cmd(imm, "mset domick hp 5000")

        cure_out = ""
        for _ in range(4):
            cure_out = _safe_cmd(imm, "feel 'cure light' Immtest")
            if "concentration" not in cure_out.lower() and "distract" not in cure_out.lower():
                break
        assert (
            "feel better" in cure_out.lower()
            or "light wounds" in cure_out.lower()
            or "cure" in cure_out.lower()
        ), f"Unexpected 'cure light' output:\n{cure_out}"

        spray_out = ""
        for _ in range(4):
            spray_out = _safe_cmd(imm, "feel 'force spray' chadoyn")
            if "concentration" not in spray_out.lower() and "distract" not in spray_out.lower():
                break
        assert "chadoyn" in spray_out.lower() and "huh?" not in spray_out.lower(), (
            f"Unexpected 'force spray' output:\n{spray_out}"
        )

        # Stop fighting chadoyn if needed before lightning test
        _safe_cmd(imm, "peace")
        light_out = ""
        for _ in range(4):
            light_out = _safe_cmd(imm, "feel 'force lightning' domick", timeout=8.0)
            if "concentration" not in light_out.lower() and "distract" not in light_out.lower():
                break
        assert "bolts of electricity" in light_out.lower(), (
            f"Expected bounded 'force lightning' cast output, got:\n{light_out}"
        )
        # Exit combat before verifying server liveness so violence ticks don't race look()
        _safe_cmd(imm, "peace")
        _safe_drain(imm, 0.3)
        # Verify server stayed alive after bounded black_lightning
        ping = _safe_cmd(imm, "look")
        assert "A Dank Pit" in ping, f"Server unstable after force lightning:\n{ping}"
    finally:
        _cleanup_client(imm)


@test_case("T1-F10-04", 1, "F10", "dig without arguments uncovers buried object in outdoor room")
def test_t1_f10_04(ctx: E2EContext) -> None:
    imm = _login_imm(ctx)
    try:
        _safe_cmd(imm, "goto 10401")
        _safe_cmd(imm, "rset 10401 sector 2")
        _safe_cmd(imm, "sset Immtest dig 100")

        uncovered = False
        last_out = ""
        for _ in range(3):
            _safe_cmd(imm, "oinvoke 10423")
            _safe_cmd(imm, "oset key flags burried")
            _safe_cmd(imm, "drop key")
            dig_init = _safe_cmd(imm, "dig")
            assert "What direction is that?" not in dig_init, (
                f"AUD-190 regression: bare 'dig' rejected as direction:\n{dig_init}"
            )
            assert "You begin digging" in dig_init, f"Expected digging start, got:\n{dig_init}"
            done_out = _safe_read_until(imm, r"Your dig uncovered", timeout=12.0)
            last_out = done_out
            if "uncovered a key" in done_out.lower() or "uncovered" in done_out.lower() and "nothing" not in done_out.lower():
                uncovered = True
                break
        assert uncovered, f"Expected dig to uncover buried item, got:\n{last_out}"
    finally:
        try:
            _safe_cmd(imm, "rset 10401 sector 1")
            _safe_cmd(imm, "purge")
            _safe_cmd(imm, "goto 100")
        except Exception:
            pass
        _cleanup_client(imm)


@test_case("T1-F10-05", 1, "F10", "Wired combat & stealth skills: sneak, throw, hitall, murder")
def test_t1_f10_05(ctx: E2EContext) -> None:
    imm = _login_imm(ctx)
    try:
        _safe_cmd(imm, "goto 10308")
        _safe_cmd(imm, "stand")
        _safe_cmd(imm, "sset Immtest sneak 100")
        _safe_cmd(imm, "sset Immtest throw 100")
        _safe_cmd(imm, "sset Immtest hitall 100")

        sneak_out = _safe_cmd(imm, "sneak")
        assert "Huh?" not in sneak_out and "attempt to move silently" in sneak_out.lower(), (
            f"Expected wired sneak skill, got:\n{sneak_out}"
        )

        _safe_cmd(imm, "oinvoke 10424")
        _safe_cmd(imm, "hold comlink")
        assert "comlink" in _safe_cmd(imm, "equipment")
        throw_out = _safe_cmd(imm, "throw comlink")
        assert "Huh?" not in throw_out and "You throw" in throw_out, (
            f"Expected wired throw skill, got:\n{throw_out}"
        )

        _safe_cmd(imm, "minvoke 10345")
        hitall_out = _safe_cmd(imm, "hitall")
        assert "Huh?" not in hitall_out and "You have no idea" not in hitall_out, (
            f"Expected wired hitall skill, got:\n{hitall_out}"
        )
        _safe_cmd(imm, "peace")
        stood = _safe_cmd(imm, "stand")
        assert "to your feet" in stood or "already standing" in stood, stood

        _safe_cmd(imm, "minvoke 10345")
        murder_out = _safe_cmd(imm, "murder chadoyn")
        low_murder = murder_out.lower()
        assert (
            "huh?" not in low_murder
            and "they aren't here." not in low_murder
            and "murder whom?" not in low_murder
            and "chadoyn" in low_murder
            and any(
                tok in low_murder
                for tok in (
                    "hit",
                    "miss",
                    "dodge",
                    "parry",
                    "scratch",
                    "graze",
                    "bruise",
                    "punch",
                    "slash",
                    "strike",
                    "smite",
                    "blast",
                    "kill",
                    "dead",
                    "dying",
                    "wound",
                    "tickle",
                    "points of damage",
                )
            )
        ), f"Expected wired murder combat engagement against chadoyn, got:\n{murder_out}"
        _safe_cmd(imm, "peace")
    finally:
        try:
            _safe_cmd(imm, "peace")
            _safe_cmd(imm, "stand")
            _safe_cmd(imm, "mpurge")
            _safe_cmd(imm, "purge")
            _safe_cmd(imm, "goto 100")
        except Exception:
            pass
        _cleanup_client(imm, quit_char=True)


@test_case("T1-F10-06", 1, "F10", "SW skills: mine (landmine) and first aid (medpac)")
def test_t1_f10_06(ctx: E2EContext) -> None:
    imm = _login_imm(ctx)
    try:
        _safe_cmd(imm, "goto 10401")
        _safe_cmd(imm, "rset 10401 sector 2")
        _safe_cmd(imm, "sset Immtest mine 100")
        _safe_cmd(imm, "sset Immtest 'first aid' 100")

        _safe_cmd(imm, "oinvoke 46")
        _safe_cmd(imm, "drop landmine")
        mine_out = _safe_cmd(imm, "mine landmine")
        assert "You arm and bury" in mine_out, f"Expected mine success, got:\n{mine_out}"

        _safe_cmd(imm, "oinvoke 32254")
        _safe_cmd(imm, "oset medpack value0 5")
        _safe_cmd(imm, "hold medpack")
        aid_out = _safe_cmd(imm, "first")
        assert "You tend to your wounds." in aid_out, f"Expected first aid success, got:\n{aid_out}"
    finally:
        try:
            _safe_cmd(imm, "rset 10401 sector 1")
            _safe_cmd(imm, "purge")
            _safe_cmd(imm, "goto 100")
        except Exception:
            pass
        _cleanup_client(imm)


# ===========================================================================
# F11 — Modern OLC (olc/olc2), Legacy build & #REPAIRS (6 tests)
# ===========================================================================


@test_case("T1-F11-01", 1, "F11", "olc room / redit2 inline edit with stop abort and stop save")
def test_t1_f11_01(ctx: E2EContext) -> None:
    imm = _login_imm(ctx)
    try:
        _safe_cmd(imm, "goto 10401")
        enter1 = _safe_cmd(imm, "olc room", require_prompt=True)
        assert "Room editing mode enabled." in enter1, f"Expected room edit mode:\n{enter1}"
        _safe_cmd(imm, "name E2E Temp Room Name", require_prompt=True)
        aborted = _safe_cmd(imm, "stop abort", require_prompt=True)
        assert "Changes discarded." in aborted, f"Expected abort confirmation:\n{aborted}"
        rstat1 = _safe_cmd(imm, "rstat")
        assert "E2E Temp Room Name" not in rstat1, f"Aborted name leaked into room:\n{rstat1}"

        enter2 = _safe_cmd(imm, "redit2", require_prompt=True)
        assert "Room editing mode enabled." in enter2, f"Expected redit2 mode:\n{enter2}"
        _safe_cmd(imm, "name E2E Saved Room Name", require_prompt=True)
        saved = _safe_cmd(imm, "stop save", require_prompt=True)
        assert "Changes saved." in saved, f"Expected save confirmation:\n{saved}"
        rstat2 = _safe_cmd(imm, "rstat")
        assert "E2E Saved Room Name" in rstat2, f"Saved room name missing from rstat:\n{rstat2}"
    finally:
        try:
            _safe_cmd(imm, "redit2", require_prompt=True)
            _safe_cmd(imm, "name a quiet place", require_prompt=True)
            _safe_cmd(imm, "stop save", require_prompt=True)
            _safe_cmd(imm, "goto 100")
        except Exception:
            pass
        _cleanup_client(imm)


@test_case("T1-F11-02", 1, "F11", "oedit clean entry (no spurious 'not editing anything') and save")
def test_t1_f11_02(ctx: E2EContext) -> None:
    imm = _login_imm(ctx)
    try:
        _safe_cmd(imm, "oinvoke 10423")
        enter = _safe_cmd(imm, "oedit key", require_prompt=True)
        assert "Object inline editing mode enabled." in enter, f"Expected oedit entry:\n{enter}"
        assert "You are not editing anything." not in enter, (
            f"OLC-035 regression: spurious 'You are not editing anything.' on oedit entry:\n{enter}"
        )
        _safe_cmd(imm, "short an E2E brass key", require_prompt=True)
        saved = _safe_cmd(imm, "stop save", require_prompt=True)
        assert "Changes saved." in saved, f"Expected save confirmation:\n{saved}"
        ostat = _safe_cmd(imm, "ostat key")
        assert "an E2E brass key" in ostat, f"Expected updated short descr in ostat:\n{ostat}"
    finally:
        try:
            _safe_cmd(imm, "stop abort", require_prompt=True)
            _safe_cmd(imm, "drop key")
            _safe_cmd(imm, "purge key")
        except Exception:
            pass
        _cleanup_client(imm)


@test_case("T1-F11-03", 1, "F11", "medit mobile edit and shopset shop edit with stop save")
def test_t1_f11_03(ctx: E2EContext) -> None:
    imm = _login_imm(ctx)
    try:
        _safe_cmd(imm, "minvoke 10345")
        menter = _safe_cmd(imm, "medit chadoyn", require_prompt=True)
        assert "Mobile inline editing mode enabled." in menter, f"Expected medit entry:\n{menter}"
        _safe_cmd(imm, "short the E2E chadoyn", require_prompt=True)
        msave = _safe_cmd(imm, "stop save", require_prompt=True)
        assert "Changes saved." in msave, f"Expected medit save:\n{msave}"
        mstat = _safe_cmd(imm, "mstat chadoyn")
        assert "the E2E chadoyn" in mstat, f"Expected updated mob short descr:\n{mstat}"

        senter = _safe_cmd(imm, "shopset 10370", require_prompt=True)
        assert "Shop inline editing mode enabled." in senter, f"Expected shopset entry:\n{senter}"
        _safe_cmd(imm, "buy 135", require_prompt=True)
        ssave = _safe_cmd(imm, "stop save", require_prompt=True)
        assert "Changes saved." in ssave, f"Expected shopset save:\n{ssave}"
        sstat = _safe_cmd(imm, "shopstat 10370")
        assert "135" in sstat, f"Expected buy profit 135 in shopstat:\n{sstat}"
    finally:
        try:
            _safe_cmd(imm, "shopset 10370", require_prompt=True)
            _safe_cmd(imm, "buy 120", require_prompt=True)
            _safe_cmd(imm, "stop save", require_prompt=True)
            _safe_cmd(imm, "mpurge")
        except Exception:
            pass
        _cleanup_client(imm)


@test_case("T1-F11-04", 1, "F11", "cfedit craft recipe inline edit with stop save and stop abort", fresh_server=True)
def test_t1_f11_04(ctx: E2EContext) -> None:
    proc = _safe_start_fresh(ctx, "f11-cfedit")
    imm = _login_imm(ctx, port=proc.port)
    try:
        c1 = _safe_cmd(imm, "cfedit spice", require_prompt=True)
        assert "Craft inline editing mode enabled." in c1, f"Expected cfedit entry:\n{c1}"
        _safe_cmd(imm, "timer 5", require_prompt=True)
        s1 = _safe_cmd(imm, "stop save", require_prompt=True)
        assert "Changes saved." in s1, f"Expected cfedit save:\n{s1}"

        c2 = _safe_cmd(imm, "cfedit spice", require_prompt=True)
        assert re.search(r"(?i)\btimer\s*[:=]\s*5\b", c2) and not re.search(r"(?i)\btimer\s*[:=]\s*25\b", c2), (
            f"Expected timer=5 persisted in cfedit:\n{c2}"
        )
        _safe_cmd(imm, "timer 25", require_prompt=True)
        a1 = _safe_cmd(imm, "stop abort", require_prompt=True)
        assert "Changes discarded." in a1, f"Expected cfedit abort:\n{a1}"

        c3 = _safe_cmd(imm, "cfedit spice", require_prompt=True)
        assert re.search(r"(?i)\btimer\s*[:=]\s*5\b", c3) and not re.search(r"(?i)\btimer\s*[:=]\s*25\b", c3), (
            f"Expected timer=5 preserved after abort:\n{c3}"
        )
        _safe_cmd(imm, "stop abort", require_prompt=True)
    finally:
        _cleanup_client(imm)


@test_case(
    "T1-F11-05",
    1,
    "F11",
    "foldarea preserves #SHOPS and #REPAIRS across cold server reboot",
    fresh_server=True,
)
def test_t1_f11_05(ctx: E2EContext) -> None:
    proc1 = _safe_start_fresh(ctx, "f11-fold-reboot")
    imm = _login_imm(ctx, port=proc1.port)
    proc2: SwripProcess | None = None
    try:
        mall_check = _safe_cmd(imm, "goto 7312")
        assert "No such location." not in mall_check, (
            f"Expected mall (#7312) loaded in 40-area world, got:\n{mall_check}"
        )

        _safe_cmd(imm, "shopset 10370", require_prompt=True)
        _safe_cmd(imm, "buy 133", require_prompt=True)
        _safe_cmd(imm, "stop save", require_prompt=True)

        _safe_cmd(imm, "shopset 7311", require_prompt=True)
        _safe_cmd(imm, "fix 160", require_prompt=True)
        _safe_cmd(imm, "stop save", require_prompt=True)

        f1 = _safe_cmd(imm, "foldarea newacad.are")
        f2 = _safe_cmd(imm, "foldarea mall")
        assert "Done." in f1 and "Done." in f2, f"Expected foldarea Done.: {f1!r} / {f2!r}"
        _cleanup_client(imm)
        imm = None

        _safe_shutdown_proc(proc1)

        proc2 = SwripProcess(proc1.work_dir)
        ctx._processes.append(proc2)
        proc2.start()
        boot2 = proc2.wait_ready(timeout=30.0)
        assert boot2.ready, f"Server failed to reboot after foldarea:\n{boot2.log_tail}"
        des_bugs = [b for b in boot2.bug_lines if "Room flag not DES" in b or "fread_area" in b.lower()]
        assert not des_bugs, f"AUD-162 foldarea boot errors:\n{des_bugs}"

        imm = _login_imm(ctx, port=proc2.port)
        st1 = _safe_cmd(imm, "shopstat 10370")
        st2 = _safe_cmd(imm, "shopstat 7311")
        assert "133" in st1, f"Shop 10370 buy=133 did not survive reboot:\n{st1}"
        assert "160" in st2 and "fix" in st2.lower(), (
            f"Repair shop 7311 did not survive reboot:\n{st2}"
        )
    finally:
        _cleanup_client(imm)
        if proc2 is not None and proc2.is_running():
            proc2.shutdown_clean()


@test_case("T1-F11-06", 1, "F11", "appraise & repair at #6603 (hunters.guild), #7311 (mall), and #897 (Ryloth)")
def test_t1_f11_06(ctx: E2EContext) -> None:
    imm = _login_imm(ctx)
    try:
        speech = _safe_cmd(imm, "speak divine")
        assert "You now speak divine." in speech, speech
        _safe_cmd(imm, "oinvoke 10308")
        _safe_cmd(imm, "oset vest cost 1000")

        for label, room_vnum, invoke_mob in (
            ("hunters.guild #6603", 6629, None),
            ("mall #7311", 7312, None),
            ("Ryloth #897", 852, 897),
        ):
            loc = _safe_cmd(imm, f"goto {room_vnum}")
            assert "No such location." not in loc, f"{label} room {room_vnum} not loaded:\n{loc}"
            if invoke_mob is not None:
                _safe_cmd(imm, f"minvoke {invoke_mob}")
            _safe_cmd(imm, "oset vest value0 1")
            app = _safe_cmd(imm, "appraise vest")
            m_cost = re.search(r"cost\s+(\d+)\s+credit", app, re.IGNORECASE)
            assert m_cost and int(m_cost.group(1)) > 0, (
                f"AUD-165: expected >0 repair cost at {label}, got:\n{app}"
            )
            rep = _safe_cmd(imm, "repair vest")
            m_rep = re.search(r"charges you\s+(\d+)\s+credit", rep, re.IGNORECASE)
            assert m_rep and int(m_rep.group(1)) > 0, (
                f"Expected positive repair charge at {label}, got:\n{rep}"
            )
    finally:
        try:
            _safe_cmd(imm, "speak common")
            _safe_cmd(imm, "drop vest")
            _safe_cmd(imm, "purge vest")
            _safe_cmd(imm, "goto 100")
        except Exception:
            pass
        _cleanup_client(imm)


# ===========================================================================
# F12 — Crafting System (craft.c + craft_recipes.dat) (6 tests)
# ===========================================================================


@test_case("T1-F12-01", 1, "F12", "craft list, select, select auto, and build spice at ROOM_FACTORY 10400", fresh_server=True)
def test_t1_f12_01(ctx: E2EContext) -> None:
    proc = _safe_start_fresh(ctx, "f12-spice")
    imm = _login_imm(ctx, port=proc.port)
    mort = None
    try:
        _safe_cmd(imm, "cfedit spice", require_prompt=True)
        _safe_cmd(imm, "timer 1", require_prompt=True)
        _safe_cmd(imm, "stop save", require_prompt=True)

        mort, name = _create_authed_mortal(ctx, imm, port=proc.port, room=10400)
        _safe_cmd(imm, f"sset {name} 'spice refining' 100")
        _safe_cmd(imm, "goto 10400")
        _safe_cmd(imm, "oinvoke 10487")
        _safe_cmd(imm, "oset carsanum type rawspice")
        _safe_cmd(imm, f"give carsanum {name}")
        _safe_drain(mort, 0.25)

        enter = _safe_cmd(mort, "craft")
        assert "You enter crafting mode" in enter, f"Expected craft mode entry:\n{enter}"
        assert "spice" in enter.lower(), f"Expected spice in craft recipe list:\n{enter}"
        sel = _safe_cmd(mort, "select spice")
        assert "Selected recipe" in sel or "Spice" in sel, f"Expected recipe select:\n{sel}"
        auto = _safe_cmd(mort, "select auto")
        assert "automatically assign" in auto.lower() or "carsanum" in auto.lower(), (
            f"Expected select auto output:\n{auto}"
        )

        done = False
        for _ in range(3):
            bld = _safe_cmd(mort, "build")
            if "You cannot figure out how to fit the parts together." in bld:
                continue
            assert "You begin working" in bld, f"Expected craft build start, got:\n{bld}"
            fin = _safe_read_until(mort, r"You finish your work\.", timeout=10.0)
            assert "You finish your work." in fin
            done = True
            break
        assert done, "Spice craft build failed skill check 3 times"
        _safe_cmd(mort, "cancel")
        inv = _safe_cmd(mort, "inventory")
        assert "spice" in inv.lower() or "drug" in inv.lower(), f"Crafted spice missing:\n{inv}"
    finally:
        _cleanup_client(mort)
        _cleanup_client(imm)


@test_case("T1-F12-02", 1, "F12", "blaster build with 2 stacked superconductors (AUD-035 UAF check)", fresh_server=True)
def test_t1_f12_02(ctx: E2EContext) -> None:
    proc = _safe_start_fresh(ctx, "f12-blaster")
    imm = _login_imm(ctx, port=proc.port)
    mort = None
    try:
        _safe_cmd(imm, "cfedit blaster", require_prompt=True)
        _safe_cmd(imm, "timer 1", require_prompt=True)
        _safe_cmd(imm, "stop save", require_prompt=True)

        mort, name = _create_authed_mortal(ctx, imm, port=proc.port, room=10400)
        _safe_cmd(imm, f"mset {name} dex 25")
        _safe_cmd(imm, f"mset {name} str 25")
        _safe_cmd(imm, f"sset {name} makeblaster 100")
        _safe_cmd(mort, "drop all")
        _safe_cmd(imm, "goto 10400")
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
            _safe_cmd(imm, f"give {kw} {name}")
        _safe_drain(mort, 0.3)

        _safe_cmd(mort, "craft")
        _safe_cmd(mort, "select blaster")
        auto_out = _safe_cmd(mort, "select auto")
        assert "automatically assign" in auto_out.lower(), f"Blaster select auto failed:\n{auto_out}"
        u2 = _safe_cmd(mort, "use superconductor superconductor")
        if "already assigned" in u2.lower():
            u2 = _safe_cmd(mort, "use superconductor 2.superconductor")
        assert "You assign" in u2, f"Expected 2nd superconductor assigned:\n{u2}"
        _safe_cmd(mort, "name e2eblaster")
        done = False
        for _ in range(3):
            bld = _safe_cmd(mort, "build")
            if "You cannot figure out how to fit the parts together." in bld:
                continue
            assert "You begin working" in bld, f"Expected blaster build start, got:\n{bld}"
            fin = _safe_read_until(mort, r"(?:You finish your work\.|Your work falls apart)", timeout=15.0)
            if "You finish your work." in fin:
                done = True
                break
        assert done, "Blaster craft build did not finish"
        _safe_cmd(mort, "cancel")
        inv = _safe_cmd(mort, "inventory")
        assert "e2eblaster" in inv.lower(), f"Crafted blaster missing from inventory:\n{inv}"
        assert "superconductor" not in inv.lower(), f"Stacked superconductors not consumed:\n{inv}"
        _cleanup_client(mort)
        mort = None
        _cleanup_client(imm)
        imm = None
        shut = _safe_shutdown_proc(proc)
        assert not shut.sanitizer_report.asan_errors, (
            f"AUD-035 ASan error during stacked superconductor craft: {shut.sanitizer_report.asan_errors}"
        )
    finally:
        _cleanup_client(mort)
        _cleanup_client(imm)


@test_case("T1-F12-03", 1, "F12", "chair furniture crafting with valid ResultVnum (AUD-164)", fresh_server=True)
def test_t1_f12_03(ctx: E2EContext) -> None:
    proc = _safe_start_fresh(ctx, "f12-chair")
    imm = _login_imm(ctx, port=proc.port)
    mort = None
    try:
        _safe_cmd(imm, "cfedit chair", require_prompt=True)
        _safe_cmd(imm, "timer 1", require_prompt=True)
        _safe_cmd(imm, "stop save", require_prompt=True)

        mort, name = _create_authed_mortal(ctx, imm, port=proc.port, room=10400)
        _safe_cmd(imm, f"sset {name} makefurniture 100")
        _safe_cmd(imm, "goto 10400")
        for vnum, kw in ((10490, "toolkit"), (32280, "thread"), (32281, "fabric")):
            _safe_cmd(imm, f"oinvoke {vnum}")
            _safe_cmd(imm, f"give {kw} {name}")
        _safe_cmd(imm, "oinvoke 10423")
        _safe_cmd(imm, "oset key type rare_metal")
        _safe_cmd(imm, f"give key {name}")
        _safe_drain(mort, 0.25)

        _safe_cmd(mort, "craft")
        _safe_cmd(mort, "select chair")
        _safe_cmd(mort, "select auto")
        _safe_cmd(mort, "name e2echair")
        for _ in range(3):
            bld = _safe_cmd(mort, "build")
            if "You cannot figure out how to fit the parts together." in bld:
                continue
            assert "You begin working" in bld, f"Chair build rejected:\n{bld}"
            fin = _safe_read_until(
                mort,
                r"(?:You finish your work\.|Your work falls apart|missing from the database)",
                timeout=15.0,
            )
            assert "missing from the database" not in fin, (
                f"AUD-164 regression: ResultVnum 61 missing:\n{fin}"
            )
            if "You finish your work." in fin:
                break
        _safe_cmd(mort, "cancel")
        inv = _safe_cmd(mort, "inventory")
        assert "e2echair" in inv.lower(), f"Crafted chair missing from inventory:\n{inv}"
    finally:
        _cleanup_client(mort)
        _cleanup_client(imm)


@test_case("T1-F12-04", 1, "F12", "disguise crafting preserves sex and race value[2]/value[3] (AUD-197)", fresh_server=True)
def test_t1_f12_04(ctx: E2EContext) -> None:
    proc = _safe_start_fresh(ctx, "f12-disguise")
    imm = _login_imm(ctx, port=proc.port)
    mort = None
    try:
        _safe_cmd(imm, "cfedit disguise", require_prompt=True)
        _safe_cmd(imm, "timer 1", require_prompt=True)
        _safe_cmd(imm, "stop save", require_prompt=True)

        mort, name = _create_authed_mortal(ctx, imm, port=proc.port, room=10400)
        _safe_cmd(imm, f"sset {name} disguise 100")
        _safe_cmd(imm, "goto 10400")
        _safe_cmd(imm, "oinvoke 32280")
        _safe_cmd(imm, f"give thread {name}")
        _safe_cmd(imm, "oinvoke 10423")
        _safe_cmd(imm, "oset key type disguise_fabric")
        _safe_cmd(imm, f"give key {name}")
        _safe_drain(mort, 0.25)

        _safe_cmd(mort, "craft")
        _safe_cmd(mort, "select disguise")
        _safe_cmd(mort, "select auto")
        _safe_cmd(mort, "name e2edisg")
        _safe_cmd(mort, "set sexrace 2 4")
        done = False
        for _ in range(3):
            bld = _safe_cmd(mort, "build")
            if "You cannot figure out how to fit the parts together." in bld:
                continue
            assert "You begin working" in bld, f"Disguise build rejected:\n{bld}"
            fin = _safe_read_until(mort, r"(?:You finish your work\.|Your work falls apart)", timeout=15.0)
            if "You finish your work." in fin:
                done = True
                break
        assert done, "Disguise build did not complete"
        _safe_cmd(mort, "cancel")
        _safe_cmd(mort, "drop e2edisg")
        _safe_drain(imm, 0.35)
        ostat = _safe_cmd(imm, "ostat e2edisg")
        assert re.search(r"Values:\s*12\s+12\s+4\s+2\b", ostat), (
            f"AUD-197 regression: expected Values: 12 12 4 2, got:\n{ostat}"
        )
    finally:
        _cleanup_client(mort)
        _cleanup_client(imm)


@test_case("T1-F12-05", 1, "F12", "Gsn none craft recipe visible and buildable by untrained mortal (AUD-268)", fresh_server=True)
def test_t1_f12_05(ctx: E2EContext) -> None:
    proc = _safe_start_fresh(ctx, "f12-gsn-none")
    imm = _login_imm(ctx, port=proc.port)
    mort = None
    try:
        _safe_cmd(imm, "cfcreate freebadge", require_prompt=True)
        _safe_cmd(imm, "output create_proto", require_prompt=True)
        _safe_cmd(imm, "resultvnum 10423", require_prompt=True)
        _safe_cmd(imm, "resulttype key", require_prompt=True)
        _safe_cmd(imm, "roomreq factory", require_prompt=True)
        _safe_cmd(imm, "timer 1", require_prompt=True)
        _safe_cmd(imm, "nametemplate {name} badge", require_prompt=True)
        _safe_cmd(imm, "shorttemplate {name}", require_prompt=True)
        _safe_cmd(imm, "desctemplate {name} lies here.", require_prompt=True)
        _safe_cmd(imm, "slot add tool toolkit 1 1 true keep 0", require_prompt=True)
        _safe_cmd(imm, "stop save", require_prompt=True)

        mort, name = _create_authed_mortal(ctx, imm, port=proc.port, room=10400)
        _safe_cmd(imm, "goto 10400")
        _safe_cmd(imm, "oinvoke 10490")
        _safe_cmd(imm, f"give toolkit {name}")
        _safe_drain(mort, 0.25)

        lst = _safe_cmd(mort, "craft")
        assert "freebadge" in lst.lower(), (
            f"AUD-268 regression: Gsn none recipe hidden from mortal craft list:\n{lst}"
        )
        sel = _safe_cmd(mort, "select freebadge")
        assert "do not know how to make" not in sel.lower(), f"Gsn none select blocked:\n{sel}"
        _safe_cmd(mort, "select auto")
        _safe_cmd(mort, "name e2ebadge")
        bld = _safe_cmd(mort, "build")
        assert "You begin working" in bld, f"Gsn none build failed:\n{bld}"
        fin = _safe_read_until(mort, r"You finish your work\.", timeout=10.0)
        assert "You finish your work." in fin
        _safe_cmd(mort, "cancel")
    finally:
        _cleanup_client(mort)
        _cleanup_client(imm)


@test_case("T1-F12-06", 1, "F12", "craft pause, resume, and cancel session lifecycle")
def test_t1_f12_06(ctx: E2EContext) -> None:
    imm = _login_imm(ctx)
    mort = None
    try:
        mort, name = _create_authed_mortal(ctx, imm, room=10400)
        _safe_cmd(imm, f"sset {name} 'spice refining' 100")
        _safe_cmd(imm, "goto 10400")
        _safe_cmd(imm, "oinvoke 10487")
        _safe_cmd(imm, "oset carsanum type rawspice")
        _safe_cmd(imm, f"give carsanum {name}")
        _safe_drain(mort, 0.25)

        _safe_cmd(mort, "craft")
        _safe_cmd(mort, "select spice")
        _safe_cmd(mort, "use rawspice carsanum")
        paused = _safe_cmd(mort, "pause")
        assert "You pause your crafting session." in paused, f"Expected pause message:\n{paused}"

        looked = _safe_cmd(mort, "look")
        assert "test workshop" in looked.lower(), f"Expected normal look outside craft mode:\n{looked}"

        resumed = _safe_cmd(mort, "craft")
        assert "You resume your crafting session." in resumed, f"Expected resume message:\n{resumed}"
        shown = _safe_cmd(mort, "show")
        assert "carsanum" in shown.lower(), f"Expected assigned material preserved:\n{shown}"
        cancelled = _safe_cmd(mort, "cancel")
        assert "You leave the crafting interface." in cancelled, f"Expected cancel message:\n{cancelled}"
    finally:
        try:
            _safe_cmd(imm, "goto 100")
        except Exception:
            pass
        _cleanup_client(mort)
        _cleanup_client(imm)


# ===========================================================================
# F13 — World Handler, Resets & Ticks (6 tests)
# ===========================================================================


@test_case("T1-F13-01", 1, "F13", "Push academy button #10318 in loft #10319 triggers push_prog")
def test_t1_f13_01(ctx: E2EContext) -> None:
    imm = _login_imm(ctx)
    try:
        _safe_cmd(imm, "goto 10319")
        out = _safe_cmd(imm, "push button")
        assert "A small panel opens revealing several articles of clothing." in out, (
            f"Expected push_prog output in #10319, got:\n{out}"
        )
        lk = _safe_cmd(imm, "look")
        assert "vest" in lk.lower() or "boots" in lk.lower() or "uniform" in lk.lower() or "belt" in lk.lower() or "helmet" in lk.lower() or "shirt" in lk.lower() or "pants" in lk.lower() or "jacket" in lk.lower() or "armor" in lk.lower() or "clothing" in lk.lower() or "grey" in lk.lower(), (
            f"Expected spawned items in #10319, got:\n{lk}"
        )
        out2 = _safe_cmd(imm, "push button")
        assert "A small panel opens" in out2, f"Second push failed:\n{out2}"
    finally:
        try:
            _safe_cmd(imm, "purge")
            _safe_cmd(imm, "goto 100")
        except Exception:
            pass
        _cleanup_client(imm)


@test_case("T1-F13-02", 1, "F13", "reset mobile/object add, reset list, and reset area execution", fresh_server=True)
def test_t1_f13_02(ctx: E2EContext) -> None:
    proc = _safe_start_fresh(ctx, "f13-reset")
    imm = _login_imm(ctx, port=proc.port)
    try:
        _safe_cmd(imm, "goto 11")
        _safe_cmd(imm, "mpurge")
        _safe_cmd(imm, "purge")
        rm = _safe_cmd(imm, "reset mobile 14 1 11")
        assert "Mobile reset added." in rm, f"Expected Mobile reset added:\n{rm}"
        ro = _safe_cmd(imm, "reset object 20 1 11")
        assert "Object reset added." in ro, f"Expected Object reset added:\n{ro}"
        rl = _safe_cmd(imm, "reset list")
        assert "14" in rl and "20" in rl, f"Expected added resets in reset list:\n{rl}"
        ra = _safe_cmd(imm, "reset area")
        assert "Done." in ra, f"Expected reset area Done.:\n{ra}"
        lk = _safe_cmd(imm, "look")
        assert "cat" in lk.lower() and "roll" in lk.lower(), (
            f"Expected spawned black cat and fruit roll-up in #11:\n{lk}"
        )
    finally:
        _cleanup_client(imm)


@test_case("T1-F13-03", 1, "F13", "Bitvector reset ('B') on room & mobile applies flags cleanly (AUD-232)", fresh_server=True)
def test_t1_f13_03(ctx: E2EContext) -> None:
    proc = _safe_start_fresh(ctx, "f13-bitreset")
    imm = _login_imm(ctx, port=proc.port)
    try:
        _safe_cmd(imm, "goto 11")
        _safe_cmd(imm, "reset mobile 14 1 11")
        b1 = _safe_cmd(imm, "reset bit set room 11 indoors")
        assert "Bitvector reset created." in b1, f"Expected room bit reset:\n{b1}"
        b2 = _safe_cmd(imm, "reset bit set mobile cat invisible")
        assert "Bitvector reset created." in b2, f"Expected mobile bit reset:\n{b2}"
        _safe_cmd(imm, "reset area")
        rstat = _safe_cmd(imm, "rstat 11")
        mstat = _safe_cmd(imm, "mstat cat")
        assert "indoors" in rstat.lower(), f"Expected indoors flag on room 11:\n{rstat}"
        assert "invisible" in mstat.lower(), (
            f"AUD-232 regression: invisible not set on mobile by BIT_RESET_MOBILE:\n{mstat}"
        )
    finally:
        _cleanup_client(imm)


@test_case("T1-F13-04", 1, "F13", "Room trap reset ('T') preserves charges across reset area (AUD-233)", fresh_server=True)
def test_t1_f13_04(ctx: E2EContext) -> None:
    proc = _safe_start_fresh(ctx, "f13-trapreset")
    imm = _login_imm(ctx, port=proc.port)
    try:
        _safe_cmd(imm, "goto 11")
        _safe_cmd(imm, "purge")
        tr = _safe_cmd(imm, "reset trap room 1 3 room")
        assert "Trap created." in tr, f"Expected Trap created:\n{tr}"
        ra = _safe_cmd(imm, "reset area")
        assert "Done." in ra
        ostat = _safe_cmd(imm, "ostat trap")
        assert "trap" in ostat.lower(), f"Trap #31 not spawned in room 11:\n{ostat}"
        assert re.search(r"Values:\s*3\s+1\b", ostat), (
            f"AUD-233 regression: expected trap charges=3 type=1 in ostat Values, got:\n{ostat}"
        )
    finally:
        _cleanup_client(imm)


@test_case("T1-F13-05", 1, "F13", "obj_update tick progression expires grenade and subsequent items (AUD-230)", fresh_server=True)
def test_t1_f13_05(ctx: E2EContext) -> None:
    proc = _safe_start_fresh(ctx, "f13-objtick")
    imm = _login_imm(ctx, port=proc.port)
    try:
        _safe_cmd(imm, "goto 11")
        _safe_cmd(imm, "purge")
        _safe_cmd(imm, "oinvoke 45")
        _safe_cmd(imm, "oset grenade timer 1")
        _safe_cmd(imm, "drop grenade")
        _safe_cmd(imm, "oinvoke 20")
        _safe_cmd(imm, "oset roll timer 1")
        _safe_cmd(imm, "drop roll")

        deadline = time.monotonic() + 100.0
        lk = ""
        while time.monotonic() < deadline:
            lk = _safe_cmd(imm, "look")
            grenade_gone = ("scraps of a grenade" in lk.lower()) or ("grenade" not in lk.lower())
            if grenade_gone and "roll" not in lk.lower():
                break
            time.sleep(3.0)
        assert ("scraps of a grenade" in lk.lower()) or ("grenade" not in lk.lower()), (
            f"Grenade did not expire within PULSE_TICK:\n{lk}"
        )
        assert "roll" not in lk.lower(), (
            f"AUD-230 regression: explode(grenade); return; aborted obj_update before fruit roll-up expired:\n{lk}"
        )
    finally:
        _cleanup_client(imm)


@test_case("T1-F13-06", 1, "F13", "extract_char cleanup on mortal quit clears who and retell/reply safely")
def test_t1_f13_06(ctx: E2EContext) -> None:
    imm = _login_imm(ctx)
    mort = None
    try:
        mort, name = _create_authed_mortal(ctx, imm, room=10300)
        _safe_cmd(imm, f"tell {name} hello")
        _safe_drain(mort, 0.2)
        _safe_cmd(mort, "retell ok", wait_prompt=False)
        try:
            mort.send_line("quit")
            mort.drain(0.3)
        except Exception:
            pass
        mort.close()
        mort = None

        time.sleep(0.3)
        _safe_drain(imm, 0.4)
        who = _safe_cmd(imm, "who")
        who_body = "\n".join(
            ln for ln in who.splitlines()
            if not ln.lstrip().startswith(("Comm:", "Log:"))
        )
        assert name.lower() not in who_body.lower(), (
            f"Quit character {name} still in who:\n{who}"
        )
        tell_after = _safe_cmd(imm, f"tell {name} ping")
        assert "They can't hear you." in tell_after or "They aren't here." in tell_after, (
            f"Expected clean offline tell response:\n{tell_after}"
        )
    finally:
        _cleanup_client(mort)
        _cleanup_client(imm)


# ===========================================================================
# F14 — MUDProgs (mud_prog/mud_comm), Admin & grub (6 tests)
# ===========================================================================


@test_case("T1-F14-01", 1, "F14", "Academy mprog walkthrough: 10303 greet -> 10318 toy -> 10323 key -> 10324 auth", fresh_server=True)
def test_t1_f14_01(ctx: E2EContext) -> None:
    proc = _safe_start_fresh(ctx, "f14-acadprog")
    imm = _login_imm(ctx, port=proc.port)
    mort = None
    try:
        name = _next_mortal_name("Acad")
        mort = _safe_connect(ctx, port=proc.port)
        _safe_create(mort, name, "E2ePass1")
        # Walk 10300 -> 10301 -> 10303 (Domick all_greet_prog)
        _safe_cmd(mort, "n")
        greet = _safe_cmd(mort, "e")
        assert "ready for your lessons" in greet.lower() or "domick" in greet.lower(), (
            f"Expected Domick greet_prog at 10303:\n{greet}"
        )
        # Walk 10303 -> w(10301) -> n(10304) -> n(10314) -> n(10315) -> n(10317) -> n(10327) -> n(10322) -> d(10318)
        for step in ("w", "n", "n", "n", "n", "n", "open n", "n", "d"):
            _safe_cmd(mort, step)

        s1 = _safe_cmd(mort, "say hello")
        assert "luke skywalker" in s1.lower() or "toy" in s1.lower(), (
            f"Expected #10342 toy speech_prog at 10318:\n{s1}"
        )
        s2 = _safe_cmd(mort, "say yes")
        assert "have it then" in s2.lower() or "gives you" in s2.lower(), (
            f"Expected #10342 give luke at 10318:\n{s2}"
        )

        # Up to 10322 -> n to 10323
        _safe_cmd(mort, "u")
        _safe_cmd(mort, "n")
        g_luke = _safe_cmd(mort, "give luke child")
        assert "thank you" in g_luke.lower(), f"Expected child thank-you prog:\n{g_luke}"
        s_key = _safe_cmd(mort, "say key")
        assert "open it for you" in s_key.lower() or "unlock" in s_key.lower(), (
            f"Expected child key unlock prog:\n{s_key}"
        )
        _safe_cmd(mort, "n")
        # Wait up to 8s for Schoolmaster spec_auth (PULSE_MOBILE = 4s)
        diploma = _safe_read_until(mort, r"gives you a diploma", timeout=10.0)
        assert "diploma" in diploma.lower()
        _safe_drain(mort, 0.2)
        _safe_cmd(mort, "say iamauthed")
        dock_lk = _safe_cmd(mort, "look")
        assert "Academy Docking Bay" in dock_lk or "docking bay" in dock_lk.lower(), (
            f"Expected transfer to #10325 on iamauthed:\n{dock_lk}"
        )
    finally:
        _cleanup_client(mort)
        _cleanup_client(imm)


@test_case("T1-F14-02", 1, "F14", "Admin 'at' and 'for' execution with original room restoration")
def test_t1_f14_02(ctx: E2EContext) -> None:
    imm = _login_imm(ctx)
    try:
        _safe_cmd(imm, "goto 100")
        at_out = _safe_cmd(imm, "at 10300 look")
        assert "Coruscant Academy" in at_out, f"Expected Coruscant Academy from 'at 10300 look':\n{at_out}"
        back = _safe_cmd(imm, "look")
        assert "Coruscant Academy" not in back, f"Expected Immtest restored to #100:\n{back}"
        for_out = _safe_cmd(imm, "for gods echo F14_FOR_OK")
        assert "F14_FOR_OK" in for_out, f"Expected echo from 'for gods':\n{for_out}"
    finally:
        _cleanup_client(imm)


@test_case("T1-F14-03", 1, "F14", "sedit social create, onoarg field edit, show, and delete (WLD-031)")
def test_t1_f14_03(ctx: E2EContext) -> None:
    imm = _login_imm(ctx)
    soc = "f14soc"
    try:
        try:
            _safe_cmd(imm, f"sedit {soc} delete")
        except Exception:
            pass
        c_out = _safe_cmd(imm, f"sedit {soc} create")
        assert "Social added." in c_out, f"Expected Social added:\n{c_out}"
        e_out = _safe_cmd(imm, f"sedit {soc} onoarg $n tests sedit.")
        assert "Done." in e_out, f"Expected Done. on sedit onoarg:\n{e_out}"
        sh_out = _safe_cmd(imm, f"sedit {soc} show")
        assert "$n tests sedit." in sh_out, (
            f"WLD-031 regression: sedit onoarg cleared field instead of storing text:\n{sh_out}"
        )
        d_out = _safe_cmd(imm, f"sedit {soc} delete")
        assert "Deleted." in d_out, f"Expected Deleted.:\n{d_out}"
    finally:
        try:
            _safe_cmd(imm, f"sedit {soc} delete")
        except Exception:
            pass
        _cleanup_client(imm)


@test_case("T1-F14-04", 1, "F14", "dnd flag toggle (AUD-321) and loadup offline player", fresh_server=True)
def test_t1_f14_04(ctx: E2EContext) -> None:
    proc = _safe_start_fresh(ctx, "f14-dnd-loadup")
    imm = _login_imm(ctx, port=proc.port)
    mort = None
    try:
        mort, name = _create_authed_mortal(ctx, imm, port=proc.port, room=10300)
        _safe_cmd(mort, "save")
        try:
            mort.send_line("quit")
            mort.drain(0.3)
        except Exception:
            pass
        mort.close()
        mort = None
        time.sleep(0.25)
        _safe_drain(imm, 0.25)

        d1 = _safe_cmd(imm, "dnd")
        assert "do not disturb" in d1.lower() and "on" in d1.lower(), (
            f"AUD-321 regression: expected dnd toggle on, got:\n{d1}"
        )
        d2 = _safe_cmd(imm, "dnd")
        assert "do not disturb" in d2.lower() and "off" in d2.lower(), (
            f"Expected dnd toggle off, got:\n{d2}"
        )

        ld = _safe_cmd(imm, f"loadup {name}")
        assert f"Player {name} loaded from room" in ld and "Done." in ld, (
            f"Expected loadup confirmation for {name}, got:\n{ld}"
        )
    finally:
        _cleanup_client(mort)
        _cleanup_client(imm)


@test_case("T1-F14-05", 1, "F14", "grub syntax help, invalid line count, and bounded execution", fresh_server=True)
def test_t1_f14_05(ctx: E2EContext) -> None:
    # Match the native gr_struct layout consumed by gr_read in src/grub.c.
    class GrubRecord(ctypes.Structure):
        _fields_ = [
            ("name", ctypes.c_char * 13),
            ("sex", ctypes.c_char),
            ("character_class", ctypes.c_char),
            ("race", ctypes.c_char),
            ("level", ctypes.c_char),
            ("room", ctypes.c_short),
            ("gold", ctypes.c_long),
            ("clan", ctypes.c_char),
            ("council", ctypes.c_char),
            ("site", ctypes.c_char * 16),
            ("last", ctypes.c_long),
            ("pkill", ctypes.c_char),
        ]

    proc = _safe_start_fresh(ctx, "f14-grub")
    imm = _login_imm(ctx, port=proc.port)
    try:
        g_empty = _safe_cmd(imm, "grub")
        assert "Syntax <max results>" in g_empty, f"Expected grub syntax:\n{g_empty}"
        g_bad = _safe_cmd(imm, "grub foo")
        assert "did not specify the number of display lines" in g_bad.lower(), (
            f"Expected display lines error:\n{g_bad}"
        )
        names = [f"Grubcase{i}" for i in range(1, 8)]
        with (proc.work_dir / "system" / "grub.dat").open("wb") as data:
            for name in names:
                record = GrubRecord(name=name.encode("ascii"), level=b"\x05", room=10300, pkill=b"N")
                data.write(bytes(record))

        g_ok = _safe_cmd(imm, "grub 5")
        assert re.search(r"^Name\s+Lv\b", g_ok, re.MULTILINE), g_ok
        assert re.search(r"\b7 matches in total\b", g_ok), g_ok
        assert re.findall(r"^Grubcase\d+\b", g_ok, re.MULTILINE) == names[:5], g_ok
    finally:
        _cleanup_client(imm)


@test_case("T1-F14-06", 1, "F14", "shutdown mud now by Level 110 Immtest exits cleanly", fresh_server=True)
def test_t1_f14_06(ctx: E2EContext) -> None:
    proc = _safe_start_fresh(ctx, "f14-shutdown")
    imm = _login_imm(ctx, port=proc.port)
    try:
        try:
            imm.send_line("shutdown mud now")
            out = _safe_drain(imm, 1.5)
        except Exception as exc:
            raise AssertionError(f"shutdown mud now telnet send failed: {exc}") from exc
        assert "Shutdown by Immtest." in out, (
            f"Expected shutdown broadcast, got:\n{out}"
        )
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            if not proc.is_running():
                break
            time.sleep(0.1)
        assert not proc.is_running(), "Server did not exit after 'shutdown mud now'"
        rc = proc.proc.poll() if proc.proc else None
        proc._intentionally_stopped = True
        boot_txt = proc.read_boot_log()
        assert "Normal termination of game." in boot_txt, (
            f"Missing 'Normal termination of game.' in boot.log (rc={rc})"
        )
    finally:
        _cleanup_client(imm)


# ===========================================================================
# F15 — Gameplay, Shops, Vendors & Clans/Planets (7 tests)
# ===========================================================================


@test_case("T1-F15-01", 1, "F15", "Academy shop list, buy, value, and sell at #10321 (#10370)")
def test_t1_f15_01(ctx: E2EContext) -> None:
    imm = _login_imm(ctx)
    mort = None
    try:
        mort, _ = _create_authed_mortal(ctx, imm, gold=5000, room=10321)
        lst = _safe_cmd(mort, "list")
        assert "[Price] {ref} Item" in lst and re.search(
            r"\[\s*\d+\]\s+\{[^}]+\}\s+a backpack\b", lst, re.IGNORECASE
        ), (
            f"Expected shop inventory at #10321:\n{lst}"
        )
        bought = _safe_cmd(mort, "buy backpack")
        assert "You buy a backpack." in bought, f"Expected buy backpack:\n{bought}"
        val = _safe_cmd(mort, "value backpack")
        assert "I'll give you" in val and "credits for a backpack" in val, (
            f"Expected value quote:\n{val}"
        )
        sold = _safe_cmd(mort, "sell backpack")
        assert "You sell a backpack for" in sold, f"Expected sell backpack:\n{sold}"
    finally:
        _cleanup_client(mort)
        _cleanup_client(imm)


@test_case("T1-F15-03", 1, "F15", "aquest questmaster list & 500qp reward purchase alignment (AUD-220)", fresh_server=True)
def test_t1_f15_03(ctx: E2EContext) -> None:
    proc = _safe_start_fresh(ctx, "f15-aquest")
    imm = _login_imm(ctx, port=proc.port)
    try:
        _safe_cmd(imm, "goto 11")
        _safe_cmd(imm, "minvoke 14")
        _safe_cmd(imm, "mset cat spec spec_questmaster")
        _safe_cmd(imm, "mset Immtest questpoints 500")
        lst = _safe_cmd(imm, "aquest list")
        assert "500qp" in lst and "10 health points" in lst, f"Expected aquest list:\n{lst}"
        bought = _safe_cmd(imm, "aquest buy 7")
        assert "You feel stronger." in bought, (
            f"AUD-220 regression: aquest buy 7 rejected at 500qp (expects 750qp):\n{bought}"
        )
    finally:
        _cleanup_client(imm)


@test_case("T1-F15-04", 1, "F15", "config +map and lookmap / automap rendering (AUD-294)")
def test_t1_f15_04(ctx: E2EContext) -> None:
    imm = _login_imm(ctx)
    try:
        _safe_cmd(imm, "goto 10300")
        cfg_on = _safe_cmd(imm, "config +map")
        assert "Ok." in cfg_on, f"Expected config +map Ok.:\n{cfg_on}"
        cfg = _safe_cmd(imm, "config")
        assert "+MAP" in cfg, f"Expected [+MAP] in config:\n{cfg}"
        lm = _safe_cmd(imm, "lookmap")
        lk = _safe_cmd(imm, "look")
        assert "+-----------+" in lm or ("Huh?" not in lm and ("|" in lm or "+" in lm)) or "+-----------+" in lk, (
            f"AUD-294 regression: lookmap/automap did not render map:\nlookmap={lm}\nlook={lk}"
        )
    finally:
        try:
            _safe_cmd(imm, "config -map")
            _safe_cmd(imm, "goto 100")
        except Exception:
            pass
        _cleanup_client(imm)


@test_case("T1-F15-05", 1, "F15", "clone in area/cloning 10000..10011 (AUD-152)")
def test_t1_f15_05(ctx: E2EContext) -> None:
    imm = _login_imm(ctx)
    mort = None
    try:
        loc = _safe_cmd(imm, "goto 10001")
        assert "No such location." not in loc, (
            f"AUD-152: area/cloning (#10001) not loaded in area.lst:\n{loc}"
        )
        mort, _ = _create_authed_mortal(ctx, imm, level=1, gold=5000, room=10001)
        cln = _safe_cmd(mort, "clone")
        assert "You pay" in cln and "credits for cloning." in cln, (
            f"AUD-152 regression: clone failed at #10001:\n{cln}"
        )
    finally:
        try:
            _safe_cmd(imm, "goto 100")
        except Exception:
            pass
        _cleanup_client(mort)
        _cleanup_client(imm)


@test_case("T1-F15-06", 1, "F15", "Clan & Planet creation, capture, and reboot persistence (AUD-180)", fresh_server=True)
def test_t1_f15_06(ctx: E2EContext) -> None:
    proc1 = _safe_start_fresh(ctx, "f15-clan-planet")
    imm = _login_imm(ctx, port=proc1.port)
    proc2: SwripProcess | None = None
    try:
        _safe_cmd(imm, "makeclan EtoClan")
        _safe_cmd(imm, "setclan EtoClan filename etoclan.clan")
        _safe_cmd(imm, "setclan EtoClan leader Immtest")
        _safe_cmd(imm, "setclan EtoClan funds 5000")
        _safe_cmd(imm, "mset Immtest clan EtoClan")
        funds = _safe_cmd(imm, "clanfunds")
        assert "5000 credits" in funds, f"Expected clanfunds 5000:\n{funds}"

        _safe_cmd(imm, "makeplanet EtoPlanet")
        _safe_cmd(imm, "setplanet EtoPlanet filename etoplanet.planet")
        _safe_cmd(imm, "setplanet EtoPlanet base_value 1000")
        _safe_cmd(imm, "aset omni_complex planet EtoPlanet")
        _safe_cmd(imm, "goto 100")
        cap = _safe_cmd(imm, "capture")
        assert "claimed the planet" in cap.lower() and "EtoPlanet" in cap, (
            f"Expected planet capture confirmation:\n{cap}"
        )

        _cleanup_client(imm)
        imm = None
        _safe_shutdown_proc(proc1)

        proc2 = SwripProcess(proc1.work_dir)
        ctx._processes.append(proc2)
        proc2.start()
        boot2 = proc2.wait_ready(timeout=30.0)
        assert boot2.ready, f"Reboot failed after clan/planet creation:\n{boot2.log_tail}"

        imm = _login_imm(ctx, port=proc2.port)
        clans_out = _safe_cmd(imm, "clans")
        planets_out = _safe_cmd(imm, "planets")
        assert "EtoClan" in clans_out, f"AUD-180 regression: clan lost across reboot:\n{clans_out}"
        assert "EtoPlanet" in planets_out, f"AUD-180 regression: planet lost across reboot:\n{planets_out}"
    finally:
        _cleanup_client(imm)
        if proc2 is not None and proc2.is_running():
            proc2.shutdown_clean()


@test_case("T1-F15-07", 1, "F15", "Player vendor lifecycle: buyvendor, placevendor, pricevendor, buy, collectgold", fresh_server=True)
def test_t1_f15_07(ctx: E2EContext) -> None:
    proc = _safe_start_fresh(ctx, "f15-vendor")
    imm = _login_imm(ctx, port=proc.port)
    owner = None
    buyer = None
    try:
        owner, oname = _create_authed_mortal(ctx, imm, port=proc.port, level=55, gold=2000000, room=10321)
        buyer, _ = _create_authed_mortal(ctx, imm, port=proc.port, level=5, gold=10000, room=11)

        _safe_cmd(imm, "rset 11 flags plr_shop")
        _safe_cmd(imm, "goto 10321")
        _safe_cmd(imm, "oinvoke 10311")
        _safe_cmd(imm, f"give glowrod {oname}")
        _safe_drain(owner, 0.3)

        deed_out = _safe_cmd(owner, "buyvendor")
        assert "contract for a vendor" in deed_out.lower(), f"Expected vendor deed:\n{deed_out}"

        _safe_cmd(imm, f"transfer {oname} 11")
        _safe_drain(owner, 0.2)
        place_out = _safe_cmd(owner, "placevendor")
        assert "swirl of smoke" in place_out.lower() or "vendor" in place_out.lower(), (
            f"Expected placevendor confirmation:\n{place_out}"
        )

        _safe_cmd(owner, "give glowrod vendor")
        price_out = _safe_cmd(owner, "pricevendor glowrod 250")
        assert "price has been changed" in price_out.lower(), f"Expected pricevendor update:\n{price_out}"
        _safe_drain(buyer, 0.35)

        v_list = _safe_cmd(buyer, "list")
        assert "250" in v_list and "glowrod" in v_list.lower(), f"Expected glowrod at 250 in vendor list:\n{v_list}"
        v_buy = _safe_cmd(buyer, "buy glowrod")
        assert "You buy a glowrod." in v_buy, f"Expected buyer purchase:\n{v_buy}"
        _safe_drain(owner, 0.35)

        collect = _safe_cmd(owner, "getcredits")
        assert "hands over his earnings" in collect.lower(), (
            f"Expected getcredits earnings payout:\n{collect}"
        )
    finally:
        _cleanup_client(buyer)
        _cleanup_client(owner)
        _cleanup_client(imm)


# ===========================================================================
# F16 — Comms, Aliases, Boards, Arena, Trivia & Bounty (6 tests)
# ===========================================================================


@test_case("T1-F16-01", 1, "F16", "Comms: say, tell, retell, reply, and ooc delivery across two clients")
def test_t1_f16_01(ctx: E2EContext) -> None:
    imm = _login_imm(ctx)
    mort = None
    try:
        mort, mname = _create_authed_mortal(ctx, imm, level=10, room=10300)
        ooc_out = _safe_cmd(mort, "ooc HelloOocToken")
        assert "HelloOocToken" in ooc_out, f"Expected ooc echo:\n{ooc_out}"
        imm_ooc = _safe_drain(imm, 0.3)
        assert "HelloOocToken" in imm_ooc, f"Immtest missed OOC broadcast:\n{imm_ooc}"

        _safe_cmd(imm, "goto 10300")
        _safe_cmd(imm, "speak common")
        _safe_drain(mort, 0.25)
        say_out = _safe_cmd(imm, "say HelloSayToken")
        assert "You say in" in say_out and "HelloSayToken" in say_out
        mort_say = _safe_drain(mort, 0.25)
        assert "HelloSayToken" in mort_say, f"Mortal missed room say:\n{mort_say}"

        tell_out = _safe_cmd(imm, f"tell {mname} HelloTellToken")
        assert "HelloTellToken" in tell_out
        mort_tell = _safe_drain(mort, 0.25)
        assert "HelloTellToken" in mort_tell, f"Mortal missed tell:\n{mort_tell}"

        retell_out = _safe_cmd(imm, "retell HelloRetellToken")
        assert "HelloRetellToken" in retell_out
        mort_retell = _safe_drain(mort, 0.25)
        assert "HelloRetellToken" in mort_retell, f"Mortal missed retell:\n{mort_retell}"

        reply_out = _safe_cmd(mort, "reply HelloReplyToken")
        assert "HelloReplyToken" in reply_out
        imm_reply = _safe_drain(imm, 0.25)
        assert "HelloReplyToken" in imm_reply, f"Immtest missed reply:\n{imm_reply}"
    finally:
        try:
            _safe_cmd(imm, "goto 100")
        except Exception:
            pass
        _cleanup_client(mort)
        _cleanup_client(imm)


@test_case("T1-F16-02", 1, "F16", "Alias lifecycle: create, execute, list, modify, and delete")
def test_t1_f16_02(ctx: E2EContext) -> None:
    imm = _login_imm(ctx)
    try:
        c1 = _safe_cmd(imm, "alias e2ehello say AliasExecToken")
        assert "Created Alias." in c1, f"Expected Created Alias:\n{c1}"
        ex1 = _safe_cmd(imm, "e2ehello")
        assert "AliasExecToken" in ex1, f"Alias execution failed:\n{ex1}"
        lst = _safe_cmd(imm, "alias")
        assert "e2ehello" in lst and "AliasExecToken" in lst, f"Alias missing from list:\n{lst}"

        mod = _safe_cmd(imm, "alias e2ehello say AliasModToken")
        assert "Modified Alias." in mod, f"Expected Modified Alias:\n{mod}"
        ex2 = _safe_cmd(imm, "e2ehello")
        assert "AliasModToken" in ex2, f"Modified alias failed:\n{ex2}"

        dlt = _safe_cmd(imm, "alias e2ehello")
        assert "Deleted Alias." in dlt, f"Expected Deleted Alias:\n{dlt}"
        lst2 = _safe_cmd(imm, "alias")
        assert "e2ehello" not in lst2, f"Deleted alias still listed:\n{lst2}"
    finally:
        _cleanup_client(imm)


@test_case("T1-F16-03", 1, "F16", "Board & note lifecycle + reboot persistence (AUD-029)", fresh_server=True)
def test_t1_f16_03(ctx: E2EContext) -> None:
    proc1 = _safe_start_fresh(ctx, "f16-boards")
    imm = _login_imm(ctx, port=proc1.port)
    proc2: SwripProcess | None = None
    try:
        _safe_cmd(imm, "goto 100")
        _safe_cmd(imm, "makeboard e2ebrd.brd")
        _safe_cmd(imm, "bset e2ebrd.brd vnum 100")
        _safe_cmd(imm, "bset e2ebrd.brd read 0")
        _safe_cmd(imm, "bset e2ebrd.brd post 0")
        _safe_cmd(imm, "bset e2ebrd.brd maxpost 50")

        brds = _safe_cmd(imm, "boards")
        assert "e2ebrd.brd" in brds, f"Created board not listed:\n{brds}"
        assert not re.search(r"Vnum:\s*0\b", brds), (
            f"AUD-029 regression: phantom Vnum: 0 board present:\n{brds}"
        )

        _safe_cmd(imm, "note subject E2eNoteSubj")
        _safe_cmd(imm, "note to all")
        _safe_cmd(imm, "note write", prompt_pattern=r"(?s)Begin entering your text.*?>\s*$")
        _safe_cmd(imm, "E2eBodyLine", prompt_pattern=r">\s*$")
        _safe_cmd(imm, "/s", prompt_pattern=r"Done\.")
        _safe_drain(imm, 0.2)

        post = _safe_cmd(imm, "note post")
        assert "upload your message" in post.lower(), f"Expected note post confirmation:\n{post}"
        nl = _safe_cmd(imm, "note list")
        assert "E2eNoteSubj" in nl, f"Posted note missing from note list:\n{nl}"
        rd = _safe_cmd(imm, "note read 1")
        assert "E2eNoteSubj" in rd and "E2eBodyLine" in rd, f"Note read mismatch:\n{rd}"

        _cleanup_client(imm)
        imm = None
        _safe_shutdown_proc(proc1)

        proc2 = SwripProcess(proc1.work_dir)
        ctx._processes.append(proc2)
        proc2.start()
        boot2 = proc2.wait_ready(timeout=30.0)
        assert boot2.ready, f"Reboot failed after board creation:\n{boot2.log_tail}"

        imm = _login_imm(ctx, port=proc2.port)
        brds2 = _safe_cmd(imm, "boards")
        assert "e2ebrd.brd" in brds2, f"Board lost across reboot:\n{brds2}"
        assert not re.search(r"Vnum:\s*0\b", brds2), (
            f"AUD-029 regression: phantom Vnum: 0 board present after reboot:\n{brds2}"
        )
        nl2 = _safe_cmd(imm, "note list")
        assert "E2eNoteSubj" in nl2, f"Board note missing from note list after reboot:\n{nl2}"
        rd2 = _safe_cmd(imm, "note read 1")
        assert "E2eNoteSubj" in rd2 and "E2eBodyLine" in rd2, (
            f"Board note lost across reboot:\n{rd2}"
        )
    finally:
        _cleanup_client(imm)
        if proc2 is not None and proc2.is_running():
            proc2.shutdown_clean()


@test_case("T1-F16-04", 1, "F16", "Arena challenge, accept, awho, and spectator bet", fresh_server=True)
def test_t1_f16_04(ctx: E2EContext) -> None:
    proc = _safe_start_fresh(ctx, "f16-arena")
    imm = _login_imm(ctx, port=proc.port)
    g1 = None
    g2 = None
    bettor = None
    try:
        g1, n1 = _create_authed_mortal(ctx, imm, port=proc.port, level=10, gold=10000, room=10300)
        g2, n2 = _create_authed_mortal(ctx, imm, port=proc.port, level=10, gold=10000, room=10301)
        bettor, _ = _create_authed_mortal(ctx, imm, port=proc.port, level=10, gold=10000, room=10304)
        _safe_drain(g1, 0.25)
        _safe_drain(g2, 0.25)
        _safe_drain(bettor, 0.25)

        ch_out = _safe_cmd(g1, f"challenge {n2}")
        assert "challenge" in ch_out.lower(), f"Expected challenge sent:\n{ch_out}"
        g2_note = _safe_drain(g2, 0.25)
        assert "challenged you" in g2_note.lower(), f"Challenger notification missing:\n{g2_note}"

        acc_out = _safe_cmd(g2, "accept")
        assert "killing fields" in acc_out.lower() or "arena" in acc_out.lower() or "prep" in acc_out.lower() or "accepted" in acc_out.lower(), (
            f"Expected accept arena entry:\n{acc_out}"
        )

        awho = _safe_cmd(imm, "awho")
        assert n1 in awho and n2 in awho, f"Expected both gladiators in awho:\n{awho}"
        _safe_drain(bettor, 0.35)

        bet_out = _safe_cmd(bettor, f"bet {n1} 100")
        assert f"You place 100 credits on {n1}." in bet_out, f"Expected bet placement:\n{bet_out}"
    finally:
        _cleanup_client(bettor)
        _cleanup_client(g2)
        _cleanup_client(g1)
        _cleanup_client(imm)


@test_case("T1-F16-05", 1, "F16", "Full Trivia lifecycle: start, tjoin, tquestion, tanswer, twinner, tscore, end")
def test_t1_f16_05(ctx: E2EContext) -> None:
    imm = _login_imm(ctx)
    player = None

    def trivia_cmd(client: TelnetClient, command: str, response: str) -> str:
        prompt = re.compile(re.escape(response) + r".*?" + DEFAULT_PROMPT_RE.pattern, re.DOTALL | re.MULTILINE)
        return _safe_cmd(client, command, prompt_pattern=prompt)

    try:
        player, pname = _create_authed_mortal(ctx, imm, room=10300)
        st = trivia_cmd(imm, "trivia start 10311", "A new trivia game has begun!")
        assert "Huh?" not in st and "new trivia game has begun" in st.lower(), (
            f"AUD-090: trivia start unavailable or failed:\n{st}"
        )

        jn = trivia_cmd(player, "tjoin", "You have joined the trivia game.")
        assert "joined the trivia game" in jn.lower(), f"Expected tjoin success:\n{jn}"
        tc = trivia_cmd(player, "tchat HelloTrivia", "HelloTrivia")
        assert "HelloTrivia" in tc, f"Expected tchat broadcast:\n{tc}"

        q = trivia_cmd(imm, "tquestion What is 2+2?", "What is 2+2?")
        assert "Question 1" in q and "What is 2+2?" in q, f"Expected tquestion output:\n{q}"

        ans = trivia_cmd(player, "tanswer 4", f"{pname}: 4")
        assert "Answer" in ans and "4" in ans, f"Expected tanswer output:\n{ans}"

        trivia_cmd(imm, f"twinner {pname}", f"{pname} has won question 1!")
        trivia_cmd(imm, "tquestion What is 3+2?", "What is 3+2?")
        trivia_cmd(player, "tanswer 5", f"{pname}: 5")
        trivia_cmd(imm, f"twinner {pname}", f"{pname} has won question 2!")

        sc = trivia_cmd(imm, "tscore", "Trivia Standings")
        assert pname in sc, f"Expected winner in tscore:\n{sc}"

        end = trivia_cmd(imm, "trivia end", "The Trivia game has ended!")
        assert "ended" in end.lower(), f"Expected trivia end confirmation:\n{end}"
    finally:
        try:
            _safe_cmd(imm, "trivia end")
        except Exception:
            pass
        _cleanup_client(player)
        _cleanup_client(imm)


@test_case("T1-F16-06", 1, "F16", "Bounty lifecycle: addbounty, immortal bounties list, and rembounty")
def test_t1_f16_06(ctx: E2EContext) -> None:
    imm = _login_imm(ctx)
    poster = None
    target = None
    try:
        poster, pname = _create_authed_mortal(ctx, imm, gold=20000, room=10300)
        target, tname = _create_authed_mortal(ctx, imm, gold=5000, room=10300)
        _safe_drain(poster, 0.3)

        add_out = _safe_cmd(poster, f"addbounty {tname} 5000")
        assert "5000 credits" in add_out and tname in add_out, f"Expected addbounty confirmation:\n{add_out}"
        t_msg = _safe_drain(target, 0.25)
        assert "bounty on you" in t_msg.lower(), f"Target did not receive bounty notice:\n{t_msg}"

        bl1 = _safe_cmd(imm, "bounties")
        assert tname in bl1 and "5000" in bl1, f"Bounty missing from bounties list:\n{bl1}"

        rem = _safe_cmd(imm, f"rembounty {tname}")
        assert "removed" in rem.lower() or "bounty" in rem.lower() or "done" in rem.lower(), (
            f"Unexpected rembounty response:\n{rem}"
        )
        bl2 = _safe_cmd(imm, "bounties")
        assert tname not in bl2, f"Removed bounty still listed:\n{bl2}"
    finally:
        _cleanup_client(target)
        _cleanup_client(poster)
        _cleanup_client(imm)


# ===========================================================================
# F17 — Space, Ships, Templates (2701..2762) & Planets (6 tests)
# ===========================================================================


@test_case("T1-F17-01", 1, "F17", "World space assets & template prototype rooms 2701..2762 loaded")
def test_t1_f17_01(ctx: E2EContext) -> None:
    imm = _login_imm(ctx)
    try:
        bm = ctx.shared_server.boot_metrics
        assert bm is not None
        space_bugs = [
            b for b in bm.bug_lines
            if any(k in b.lower() for k in ("ship", "spaceobject", "planet", "template"))
        ]
        assert not space_bugs, f"Space/template boot BUG lines found:\n{space_bugs[:10]}"

        for vnum in (2701, 2708, 2762):
            g = _safe_cmd(imm, f"goto {vnum}")
            assert "No such location." not in g, f"Template room #{vnum} missing:\n{g}"

        so = _safe_cmd(imm, "starsystems")
        pl = _safe_cmd(imm, "planets")
        sh = _safe_cmd(imm, "ships")
        ash = _safe_cmd(imm, "allships")
        assert (
            so.strip()
            and "huh?" not in so.lower()
            and "no spaceobjects currently formed" not in so.lower()
            and "none" not in so.lower()
        ), f"Expected populated starsystems (spaceobjects):\n{so}"
        assert pl.strip() and "no planets currently formed" not in pl.lower() and "Planet:" in pl, (
            f"Expected populated planets with Planet: entries:\n{pl}"
        )
        assert "Ship" in sh and "Owner" in sh, f"Expected ships header output:\n{sh}"
        assert ash.strip() and "no ships currently formed" not in ash.lower(), (
            f"Expected populated allships list:\n{ash}"
        )
        ship_rows = [
            ln.strip()
            for ln in ash.splitlines()
            if ln.strip()
            and "the following ships" not in ln.lower()
            and not (ln.strip().startswith("Ship") and "Owner" in ln)
            and "to rent" not in ln.lower()
            and "to buy" not in ln.lower()
        ]
        assert ship_rows, f"Expected actual formed ship rows in allships beyond header:\n{ash}"
    finally:
        try:
            _safe_cmd(imm, "goto 100")
        except Exception:
            pass
        _cleanup_client(imm, quit_char=True)


@test_case("T1-F17-02", 1, "F17", "ordership single-room & multi-room templates (2701..2762) and shipdelete")
def test_t1_f17_02(ctx: E2EContext) -> None:
    imm = _login_imm(ctx)
    try:
        _safe_cmd(imm, "goto 32000")
        _safe_cmd(imm, "ordership 1 EtoTWing")
        _safe_cmd(imm, "ordership 0 EtoShuttle")

        sh1 = _safe_cmd(imm, "showship EtoTWing")
        sh2 = _safe_cmd(imm, "showship EtoShuttle")
        assert "Owner:" in sh1 and "Immtest" in sh1, f"ordership 1 failed:\n{sh1}"
        assert "Owner:" in sh2 and "Immtest" in sh2, f"ordership 0 failed:\n{sh2}"

        _safe_cmd(imm, "open EtoShuttle")
        brd = _safe_cmd(imm, "board EtoShuttle")
        assert (
            re.search(r"you enter (?:jv-7 shuttle|etoshuttle)\.", brd, re.IGNORECASE)
            and "i see no" not in brd.lower()
        ), f"board EtoShuttle failed:\n{brd}"
        _safe_cmd(imm, "leave")

        del1 = _safe_cmd(imm, "shipdelete EtoTWing")
        del2 = _safe_cmd(imm, "shipdelete EtoShuttle")
        assert "Huh?" not in del1 and "Huh?" not in del2, (
            f"AUD-307: shipdelete not wired:\n{del1}\n{del2}"
        )
        gone = _safe_cmd(imm, "showship EtoTWing")
        assert "No such ship." in gone, f"Deleted ship still present:\n{gone}"
    finally:
        try:
            _safe_cmd(imm, "goto 100")
            _safe_cmd(imm, "shipdelete EtoTWing")
            _safe_cmd(imm, "shipdelete EtoShuttle")
        except Exception:
            pass
        _cleanup_client(imm)


@test_case("T1-F17-03", 1, "F17", "ordership, setship, and resetship preserve owner/pilot/copilot (AUD-174)")
def test_t1_f17_03(ctx: E2EContext) -> None:
    imm = _login_imm(ctx)
    try:
        _safe_cmd(imm, "goto 32000")
        _safe_cmd(imm, "ordership 1 ResetWing")
        _safe_cmd(imm, "setship ResetWing pilot PilotAlpha")
        _safe_cmd(imm, "setship ResetWing copilot CopilotBeta")
        _safe_cmd(imm, "setship ResetWing hull 100")
        _safe_cmd(imm, "resetship ResetWing")
        sh = _safe_cmd(imm, "showship ResetWing")
        assert "Immtest" in sh and "PilotAlpha" in sh and "CopilotBeta" in sh, (
            f"AUD-174 regression: resetship wiped owner/pilot/copilot:\n{sh}"
        )
    finally:
        try:
            _safe_cmd(imm, "shipdelete ResetWing")
            _safe_cmd(imm, "goto 100")
        except Exception:
            pass
        _cleanup_client(imm)


@test_case("T1-F17-04", 1, "F17", "Full flight loop: openhatch -> board -> launch -> radar -> calculate -> hyperspace -> land", fresh_server=True)
def test_t1_f17_04(ctx: E2EContext) -> None:
    proc = _safe_start_fresh(ctx, "f17-flight")
    imm = _login_imm(ctx, port=proc.port)
    try:
        for sk in ("starfighters", "midships", "navigation", "'ship systems'"):
            _safe_cmd(imm, f"sset Immtest {sk} 100")

        so_raw = _safe_cmd(imm, "starsystems")
        assert "huh?" not in so_raw.lower() and "no spaceobjects currently formed" not in so_raw.lower(), (
            f"No spaceobjects loaded in space.lst to launch from:\n{so_raw}"
        )
        so_list = [ln.strip() for ln in so_raw.splitlines() if ln.strip()]
        assert so_list, "No spaceobjects loaded in space.lst to launch from"

        pad_vnum: int | None = None
        land_loc: str | None = None
        target_sys: str | None = None
        for sys_name in so_list:
            info = _safe_cmd(imm, f"showstarsystem {sys_name}")
            if "trainer: yes" in info.lower():
                continue
            m = re.search(r"doca:\s*(\d+)\s+\(([^)]*)\)", info, re.IGNORECASE)
            if m and int(m.group(1)) > 0 and m.group(2).strip():
                pad_vnum = int(m.group(1))
                land_loc = m.group(2).strip()
                target_sys = sys_name
                break
        assert pad_vnum and land_loc and target_sys, (
            f"No non-trainer spaceobject with valid doca pad and landing location found in:\n{so_list}"
        )

        _safe_cmd(imm, "goto 32000")
        _safe_cmd(imm, "ordership 1 FlyWing")
        sh_chk = _safe_cmd(imm, "showship FlyWing")
        assert "No such ship." not in sh_chk, (
            f"ordership 1 FlyWing failed (templates.are 2701..2762 missing?):\n{sh_chk}"
        )
        for fld, val in (
            ("speed", 200),
            ("hyperspeed", 100),
            ("maxenergy", 10000),
            ("energy", 10000),
            ("maxhull", 1000),
            ("hull", 1000),
            ("sensor", 50),
            ("astroarray", 300),
            ("shipyard", pad_vnum),
        ):
            _safe_cmd(imm, f"setship FlyWing {fld} {val}")
        _safe_cmd(imm, "resetship FlyWing")
        _safe_cmd(imm, f"goto {pad_vnum}")

        _safe_cmd(imm, "openhatch FlyWing")
        _safe_cmd(imm, "board FlyWing")
        l_out = _safe_cmd(imm, "launch")
        assert "Launch sequence initiated" in l_out or "flies into space" in l_out, (
            f"Launch failed at pad {pad_vnum}:\n{l_out}"
        )
        _safe_read_until(imm, r"Launch complete\.", timeout=25.0)

        rad = _safe_cmd(imm, "radar")
        assert "Your Coordinates:" in rad, f"Expected radar coordinates:\n{rad}"

        calc = _safe_cmd(imm, f"calculate {target_sys}")
        assert "Hyperspace course set" in calc, f"Expected calculate course set:\n{calc}"

        hyp = _safe_cmd(imm, "hyperspace")
        assert "hyperjump" in hyp.lower() or "lightspeed" in hyp.lower() or "hyperspace" in hyp.lower(), (
            f"Hyperspace initiation failed:\n{hyp}"
        )
        _safe_read_until(imm, r"Hyperjump complete\.", timeout=30.0)

        land_out = _safe_cmd(imm, f"land {land_loc}")
        land_low = land_out.lower()
        assert ("landing" in land_low or "land" in land_low) and "cannot land" not in land_low, (
            f"Land failed:\n{land_out}"
        )
        _safe_read_until(imm, r"(?:Landing sequence complete\.|Landed|touches down|Landing complete)", timeout=30.0)
        _safe_cmd(imm, "openhatch")
        _safe_cmd(imm, "leave")
    finally:
        _cleanup_client(imm)


@test_case("T1-F17-05", 1, "F17", "Multi-seat recharge (cockpit/coseat/navseat AUD-287) and loadcargo/unloadcargo", fresh_server=True)
def test_t1_f17_05(ctx: E2EContext) -> None:
    proc = _safe_start_fresh(ctx, "f17-seats-cargo")
    imm = _login_imm(ctx, port=proc.port)
    try:
        for sk in ("starfighters", "midships", "navigation", "'ship systems'"):
            _safe_cmd(imm, f"sset Immtest {sk} 100")

        so_raw = _safe_cmd(imm, "starsystems")
        assert "huh?" not in so_raw.lower() and "no spaceobjects currently formed" not in so_raw.lower(), (
            f"No spaceobjects loaded in space.lst:\n{so_raw}"
        )
        so_list = [ln.strip() for ln in so_raw.splitlines() if ln.strip()]
        assert so_list, "No spaceobjects loaded in space.lst"

        pad_vnum: int | None = None
        target_sys: str | None = None
        for sys_name in so_list:
            info = _safe_cmd(imm, f"showstarsystem {sys_name}")
            if "trainer: yes" in info.lower():
                continue
            m = re.search(r"doca:\s*(\d+)\s+\(([^)]*)\)", info, re.IGNORECASE)
            if m and int(m.group(1)) > 0 and m.group(2).strip():
                pad_vnum = int(m.group(1))
                target_sys = sys_name
                break
        assert pad_vnum and target_sys, (
            f"No non-trainer spaceobject with valid doca cargo pad found in:\n{so_list}"
        )

        _safe_cmd(imm, "goto 32000")
        _safe_cmd(imm, "ordership 0 SeatShuttle")
        sh_info = _safe_cmd(imm, "showship SeatShuttle")
        assert "No such ship." not in sh_info, (
            f"ordership 0 SeatShuttle failed (templates.are 2701..2762 missing?):\n{sh_info}"
        )
        m_rooms = re.search(r"Firstroom:\s*(\d+)\s+Lastroom:\s*(\d+)", sh_info)
        assert m_rooms, f"Could not parse Firstroom/Lastroom from showship:\n{sh_info}"
        r_first = int(m_rooms.group(1))
        r_last = int(m_rooms.group(2))
        coseat_r = min(r_first + 1, r_last)
        navseat_r = min(r_first + 2, r_last)

        for fld, val in (
            ("coseat", coseat_r),
            ("navseat", navseat_r),
            ("speed", 150),
            ("maxenergy", 10000),
            ("energy", 10000),
            ("maxhull", 1000),
            ("hull", 1000),
            ("maxshield", 200),
            ("shield", 200),
            ("maxcargo", 20),
            ("shipyard", pad_vnum),
        ):
            _safe_cmd(imm, f"setship SeatShuttle {fld} {val}")
        _safe_cmd(imm, "resetship SeatShuttle")
        _safe_cmd(imm, f"goto {pad_vnum}")

        # Cargo check/load/unload at docked planet pad
        _safe_cmd(imm, "openhatch SeatShuttle")
        _safe_cmd(imm, "board SeatShuttle")
        _safe_cmd(imm, f"goto {r_first}")
        cc = _safe_cmd(imm, "checkcargo")
        if "no cargo available here" in cc.lower():
            _safe_cmd(imm, f"cargo planet {target_sys} randomize")
            cc = _safe_cmd(imm, "checkcargo")
        m_cargo = re.search(r"\b(\d+)\s+\d+\s+\(", cc)
        assert m_cargo, f"checkcargo returned no cargo rows:\n{cc}"
        ctype = m_cargo.group(1)
        ld = _safe_cmd(imm, f"loadcargo {ctype} 2")
        assert "cargo request has been delivered" in ld.lower(), f"loadcargo failed:\n{ld}"
        ul = _safe_cmd(imm, f"unloadcargo {ctype} 1")
        assert "cargo request has been delivered" in ul.lower(), (
            f"unloadcargo failed:\n{ul}"
        )

        # Launch and test the co-pilot requirement on a multi-room ship
        l_out = _safe_cmd(imm, "launch")
        assert "Launch sequence initiated" in l_out or "flies into space" in l_out, (
            f"Shuttle launch failed:\n{l_out}"
        )
        _safe_read_until(imm, r"Launch complete\.", timeout=25.0)

        assert coseat_r != navseat_r, "The fixture must have distinct co-pilot and navigation rooms"
        _safe_cmd(imm, f"goto {coseat_r}")
        rec = _safe_cmd(imm, "recharge")
        assert "Recharging shields.." in rec, f"Recharge failed from co-pilot seat:\n{rec}"

        _safe_cmd(imm, f"goto {navseat_r}")
        rec = _safe_cmd(imm, "recharge")
        assert "co-pilot seat" in rec and "Recharging shields.." not in rec, rec
    finally:
        _cleanup_client(imm)


@test_case("T1-F17-06", 1, "F17", "Zero 'Not implemented yet!' responses across space commands (AUD-306)")
def test_t1_f17_06(ctx: E2EContext) -> None:
    imm = _login_imm(ctx)
    try:
        for cmd_text in (
            "fly",
            "bomb",
            "unload",
            "refuel",
            "upgradeship",
            "degradeship",
            "cargo search overprice",
            "cargo search underprice",
        ):
            out = _safe_cmd(imm, cmd_text)
            assert "not implemented yet" not in out.lower(), (
                f"AUD-306 regression: {cmd_text!r} emitted 'Not implemented yet!':\n{out}"
            )
    finally:
        _cleanup_client(imm)


# ===========================================================================
# F18 — Build & Toolchain (Makefile, bin/startup, dead files) (6 tests)
# ===========================================================================


@test_case("T1-F18-01", 1, "F18", "GCC 15.2 ASan/UBSan + -Wall -Wextra -Wformat=2 -Wshift-overflow=2 -DMCCP clean build", requires_server=False)
def test_t1_f18_01(ctx: E2EContext) -> None:
    ctx.ensure_compiled_binary()
    res = ctx.shared_build_result
    assert res is not None, "Expected shared_build_result after ensure_compiled_binary()"
    assert res.success and res.returncode == 0, f"Build failed: {res.errors[:10]}"
    assert res.binary_path is not None and res.binary_path.is_file()
    assert len(res.errors) == 0, f"Build had errors: {res.errors}"
    assert len(res.compiled_files) >= 44, (
        f"Expected >= 44 compiled C/C++ files, got {len(res.compiled_files)}"
    )


@test_case("T1-F18-02", 1, "F18", "Zero target compiler bug warnings (-Wformat=2, -Wshift-overflow=2, -Wimplicit-fallthrough, -Wtype-limits)", requires_server=False)
def test_t1_f18_02(ctx: E2EContext) -> None:
    ctx.ensure_compiled_binary()
    res = ctx.shared_build_result
    assert res is not None
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
    found: dict[str, list[str]] = {
        cat: res.warnings[cat] for cat in forbidden_cats if res.warnings.get(cat)
    }
    assert not found, f"Target bug-warning categories present: {found}"


@test_case("T1-F18-03", 1, "F18", "src/Makefile enables -DMCCP, compiler/sanitizer flags, omits -DREQUESTS & requests.c, links -lz -lm -lssl -lcrypto", requires_server=False)
def test_t1_f18_03(ctx: E2EContext) -> None:
    mk = parse_makefile(ctx.repo_path)
    raw = (ctx.repo_path / "src" / "Makefile").read_text(encoding="utf-8", errors="replace")
    assert mk.has_d_mccp, "Expected -DMCCP in src/Makefile"
    assert not mk.has_d_requests, "Expected -DREQUESTS removed from src/Makefile"
    assert "requests.c" not in mk.c_files, "Expected requests.c removed from C_FILES in src/Makefile"
    for flag in ("-Wall", "-Wextra", "-Wformat=2", "-Wshift-overflow=2", "-fsanitize=address,undefined"):
        assert flag in raw, f"Expected compiler/sanitizer flag {flag} in src/Makefile"
    for lib in ("-lz", "-lm", "-lssl", "-lcrypto"):
        assert lib in raw, f"Expected link library {lib} in src/Makefile"


@test_case("T1-F18-04", 1, "F18", "bin/startup uses flock & PID grep (no sed '2,$d')", requires_server=False)
def test_t1_f18_04(ctx: E2EContext) -> None:
    startup_path = ctx.repo_path / "bin" / "startup"
    script_txt = startup_path.read_text(encoding="utf-8", errors="replace")
    assert 'sed "2,$d"' not in script_txt and "sed '2,$d'" not in script_txt, (
        "AUD-097 regression: broken sed '2,$d' still present in bin/startup"
    )
    assert "flock -n" in script_txt, "Expected flock -n guard in bin/startup"
    assert "PID: [0-9]+" in script_txt, "Expected grep -oE 'PID: [0-9]+' in bin/startup"


@test_case("T1-F18-05", 1, "F18", "Dead/uncompiled legacy files absent post-Wave 4", requires_server=False)
def test_t1_f18_05(ctx: E2EContext) -> None:
    present = [rel for rel in DEAD_FILES_TO_REMOVE if (ctx.repo_path / rel).exists()]
    assert not present, f"Dead legacy files still present in repo: {present}"


@test_case("T1-F18-06", 1, "F18", "swrip binary boots and shuts down cleanly under LSan with 0 bytes leaked", requires_server=False)
def test_t1_f18_06(ctx: E2EContext) -> None:
    proc = _safe_start_fresh(ctx, "f18-lsan-boot")
    assert proc.boot_metrics is not None and proc.boot_metrics.ready
    shut = _safe_shutdown_proc(proc)
    assert shut.normal_termination, f"Expected normal termination:\n{shut.shutdown_output}"
    assert shut.exit_code == 0, f"Expected exit_code == 0, got {shut.exit_code}"
    ctx.assert_clean_sanitizers(shut)
