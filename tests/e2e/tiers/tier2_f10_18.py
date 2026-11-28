"""Tier 2 (boundary, corner, and negative) E2E tests for Features F10–F18 (54 tests)."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from tests.e2e.framework import E2EContext, test_case
from tests.e2e.static_scanners import run_all_scanners
from tests.e2e.telnet_client import DEFAULT_PROMPT_RE, TelnetClient, strip_ansi
from tests.e2e.wsl_env import SwripProcess, allocate_port_block, seed_des_legacy_fixture, seed_immortal_fixture

# ---------------------------------------------------------------------------
# Diagnostic-safe helpers
# ---------------------------------------------------------------------------

_NAME_SEQ = 0


def _next_mortal_name(prefix: str = "Mrtb") -> str:
    global _NAME_SEQ
    _NAME_SEQ += 1
    n = _NAME_SEQ
    letters: list[str] = []
    while n > 0:
        n, rem = divmod(n - 1, 26)
        letters.append(chr(ord("a") + rem))
    base = "".join(ch for ch in prefix if ch.isascii() and ch.isalpha()) or "Mrtb"
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
            t_tail = cli.read_until(DEFAULT_PROMPT_RE, timeout=timeout)
            transcript.append(t_tail)
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


def _safe_drain(cli: TelnetClient, seconds: float = 0.25) -> str:
    try:
        return strip_ansi(cli.drain(seconds=seconds))
    except Exception as exc:
        raise AssertionError(f"Drain failed: {exc}") from exc


def _safe_read_until(
    cli: TelnetClient,
    pattern: str | re.Pattern[str],
    timeout: float = 10.0,
) -> str:
    try:
        return strip_ansi(cli.read_until(pattern, timeout=timeout))
    except Exception as exc:
        raise AssertionError(f"Timed out/EOF waiting for {pattern!r}: {exc}") from exc


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
    prefix: str = "Mrtb",
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


def _cleanup_client(cli: TelnetClient | None) -> None:
    if cli is not None:
        cli.close()


def _safe_shutdown_proc(proc: SwripProcess) -> Any:
    try:
        return proc.shutdown_clean()
    except Exception as exc:
        raise AssertionError(f"Clean shutdown failed: {exc}") from exc


def _safe_new_workspace(ctx: E2EContext, label: str, **kwargs: Any) -> Path:
    try:
        return ctx.new_temp_workspace(label, **kwargs)
    except Exception as exc:
        raise AssertionError(f"Temp workspace staging failed ({label}): {exc}") from exc


def _safe_start_proc(
    ctx: E2EContext,
    ws: Path,
    *,
    seed_name: str | None = None,
    seed_password: str | None = None,
    seed_level: int = 110,
    ready_timeout: float = 30.0,
) -> SwripProcess:
    try:
        if seed_name is not None:
            seed_immortal_fixture(
                ws,
                name=seed_name,
                password=seed_password or "E2eUserPass",
                level=seed_level,
            )
        proc = SwripProcess(ws)
        ctx._processes.append(proc)
        proc.start()
        boot = proc.wait_ready(timeout=ready_timeout)
        assert boot.ready, f"Server failed to boot:\n{boot.log_tail}"
        return proc
    except AssertionError:
        raise
    except Exception as exc:
        raise AssertionError(f"Server startup failed: {exc}") from exc


def _safe_run_subprocess(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(cmd, **kwargs)
        output = (result.stdout or "") + (result.stderr or "")
        assert not any(marker in output for marker in (
            "AddressSanitizer:", "LeakSanitizer:", "UndefinedBehaviorSanitizer:", "runtime error:",
        )), f"Sanitizer diagnostic from CLI fixture:\n{output}"
        return result
    except (subprocess.SubprocessError, OSError) as exc:
        raise AssertionError(f"Subprocess {cmd!r} failed/timed out: {exc}") from exc


# ===========================================================================
# F10 — Force Spells, Standard Skills & SW Skills (6 boundary/negative tests)
# ===========================================================================


@test_case("T2-F10-01", 2, "F10", "practice boundary rejections (no teacher, unknown skill, level too low, wrong teacher)")
def test_t2_f10_01(ctx: E2EContext) -> None:
    imm = _login_imm(ctx)
    mort = None
    try:
        mort, _ = _create_authed_mortal(ctx, imm, room=10300)
        no_t = _safe_cmd(mort, "practice scan")
        assert "You can't do that here." in no_t, f"Expected no-teacher error at 10300:\n{no_t}"

        _safe_cmd(mort, "n")
        _safe_cmd(mort, "e")  # 10303 Domick (#10340)
        unk = _safe_cmd(mort, "practice nonexistent")
        assert "never heard of that one" in unk.lower(), f"Expected unknown skill error:\n{unk}"

        low = _safe_cmd(mort, "practice sabotage")
        assert "not ready to learn that yet" in low.lower(), f"Expected minlevel rejection:\n{low}"

        wrong = _safe_cmd(mort, "practice beg")
        assert "not know how to teach" in wrong.lower() or "know not" in wrong.lower(), (
            f"Expected wrong-teacher rejection:\n{wrong}"
        )
    finally:
        _cleanup_client(mort)
        _cleanup_client(imm)


@test_case("T2-F10-02", 2, "F10", "languages learn & speak negative, race body-part, and credit boundaries")
def test_t2_f10_02(ctx: E2EContext) -> None:
    imm = _login_imm(ctx)
    mort = None
    try:
        mort, name = _create_authed_mortal(ctx, imm, room=10306)
        ryl = _safe_cmd(mort, "speak ryl")
        assert "body parts" in ryl.lower(), f"Expected Twi'lek body-part check on Human:\n{ryl}"

        unk_spk = _safe_cmd(mort, "speak nonexistent")
        assert "do not know that language" in unk_spk.lower(), f"Expected unknown speak error:\n{unk_spk}"

        unk_lrn = _safe_cmd(mort, "languages learn nonexistent")
        assert "that is not a language" in unk_lrn.lower(), f"Expected non-language error:\n{unk_lrn}"

        div = _safe_cmd(mort, "languages learn divine")
        assert "cannot be learned" in div.lower(), f"Expected divine unlearnable error:\n{div}"

        _safe_cmd(imm, f"mset {name} credits 10")
        poor = _safe_cmd(mort, "languages learn huttese")
        assert "don't have enough" in poor.lower() or "25 credits" in poor.lower(), (
            f"Expected <25 credits rejection:\n{poor}"
        )
    finally:
        _cleanup_client(mort)
        _cleanup_client(imm)


@test_case("T2-F10-03", 2, "F10", "feel negative boundaries (untrained mortal, unknown spell, ROOM_NO_MAGIC)")
def test_t2_f10_03(ctx: E2EContext) -> None:
    imm = _login_imm(ctx)
    mort = None
    try:
        mort, _ = _create_authed_mortal(ctx, imm, room=10300)
        m_cast = _safe_cmd(mort, "feel 'cure light'")
        assert "You can't do that." in m_cast, f"Expected untrained mortal cast denial:\n{m_cast}"

        unk = _safe_cmd(imm, "feel 'nonexistentspell'")
        assert "We didn't create that yet..." in unk, f"Expected unknown spell error:\n{unk}"

        _safe_cmd(imm, "goto 10401")
        _safe_cmd(imm, "redit2", require_prompt=True)
        _safe_cmd(imm, "flags no_magic", require_prompt=True)
        _safe_cmd(imm, "stop save", require_prompt=True)
        nomag = _safe_cmd(imm, "feel 'cure light'")
        assert "You failed." in nomag, f"Expected ROOM_NO_MAGIC rejection:\n{nomag}"
    finally:
        try:
            _safe_cmd(imm, "redit2", require_prompt=True)
            _safe_cmd(imm, "flags no_magic", require_prompt=True)
            _safe_cmd(imm, "stop save", require_prompt=True)
            _safe_cmd(imm, "goto 100")
        except Exception:
            pass
        _cleanup_client(mort)
        _cleanup_client(imm)


@test_case("T2-F10-04", 2, "F10", "dig in indoor room (SECT_INSIDE) and hitall in ROOM_SAFE")
def test_t2_f10_04(ctx: E2EContext) -> None:
    imm = _login_imm(ctx)
    try:
        _safe_cmd(imm, "goto 10300")
        _safe_cmd(imm, "sset Immtest dig 100")
        _safe_cmd(imm, "sset Immtest hitall 100")

        dig_in = _safe_cmd(imm, "dig")
        assert "too hard to dig through" in dig_in.lower(), (
            f"AUD-190 regression: expected indoor dig rejection, got:\n{dig_in}"
        )

        hit_safe = _safe_cmd(imm, "hitall")
        assert "You cannot do that here." in hit_safe, (
            f"AUD-195 regression: expected ROOM_SAFE hitall rejection, got:\n{hit_safe}"
        )
    finally:
        try:
            _safe_cmd(imm, "goto 100")
        except Exception:
            pass
        _cleanup_client(imm)


@test_case("T2-F10-05", 2, "F10", "Untrained SW skill and missing/invalid item boundaries (mine, first aid)")
def test_t2_f10_05(ctx: E2EContext) -> None:
    imm = _login_imm(ctx)
    mort = None
    try:
        mort, _ = _create_authed_mortal(ctx, imm, room=10300)
        assert "Huh?" in _safe_cmd(mort, "mine landmine")
        assert "Huh?" in _safe_cmd(mort, "first")

        _safe_cmd(imm, "goto 10401")
        _safe_cmd(imm, "rset 10401 sector 2")
        _safe_cmd(imm, "sset Immtest mine 100")
        _safe_cmd(imm, "sset Immtest 'first aid' 100")

        _safe_cmd(imm, "oinvoke 10423")
        _safe_cmd(imm, "drop key")
        non_mine = _safe_cmd(imm, "mine key")
        assert "That's not a landmine!" in non_mine, f"Expected non-landmine rejection:\n{non_mine}"

        _safe_cmd(imm, "remove medpack")
        no_med = _safe_cmd(imm, "first")
        assert "holding a medpac" in no_med.lower(), f"Expected missing-medpac rejection:\n{no_med}"

        _safe_cmd(imm, "oinvoke 32254")
        _safe_cmd(imm, "oset medpack value0 0")
        _safe_cmd(imm, "hold medpack")
        empty_med = _safe_cmd(imm, "first")
        assert "medpac seems to be empty" in empty_med.lower(), (
            f"Expected empty-medpac rejection:\n{empty_med}"
        )
    finally:
        try:
            _safe_cmd(imm, "rset 10401 sector 1")
            _safe_cmd(imm, "purge")
            _safe_cmd(imm, "goto 100")
        except Exception:
            pass
        _cleanup_client(mort)
        _cleanup_client(imm)


@test_case("T2-F10-06", 2, "F10", "Retired do_make* commands and dead SMAUG spell_* stubs removed (AUD-237, AUD-302)")
def test_t2_f10_06(ctx: E2EContext) -> None:
    imm = _login_imm(ctx)
    try:
        mk_spice = _safe_cmd(imm, "makespice")
        assert "Huh?" in mk_spice, (
            f"AUD-302 regression: retired do_makespice still wired:\n{mk_spice}"
        )

        sl_furn = _safe_cmd(imm, "slookup makefurniture")
        assert "makefurniture" in sl_furn.lower() and "do_say" not in sl_furn, (
            f"AUD-164/302 regression: makefurniture missing or still mapped to do_say:\n{sl_furn}"
        )

        for dead_spell in ("gate", "astral walk"):
            sp = _safe_cmd(imm, f"feel '{dead_spell}'")
            assert "We didn't create that yet..." in sp, (
                f"AUD-237 regression: dead SMAUG spell {dead_spell!r} still active:\n{sp}"
            )
    finally:
        _cleanup_client(imm)


# ===========================================================================
# F11 — Modern OLC (olc/olc2), Legacy build & #REPAIRS (6 boundary tests)
# ===========================================================================


@test_case("T2-F11-01", 2, "F11", "Mortal blocked from all OLC and building commands")
def test_t2_f11_01(ctx: E2EContext) -> None:
    imm = _login_imm(ctx)
    mort = None
    try:
        mort, _ = _create_authed_mortal(ctx, imm, room=10300)
        for cmd in (
            "olc room",
            "redit2",
            "oedit key",
            "medit chadoyn",
            "shopset 10370",
            "cfedit spice",
            "savearea",
            "foldarea newacad.are",
        ):
            out = _safe_cmd(mort, cmd)
            assert "Huh?" in out, f"Mortal allowed to run privileged command {cmd!r}:\n{out}"
    finally:
        _cleanup_client(mort)
        _cleanup_client(imm)


@test_case("T2-F11-04", 2, "F11", "OLC invalid VNUM, nonexistent target, and non-shop mobile rejections")
def test_t2_f11_04(ctx: E2EContext) -> None:
    imm = _login_imm(ctx)
    try:
        assert "Mobile not found." in _safe_cmd(imm, "shopset 999999")
        assert "doesn't keep a shop" in _safe_cmd(imm, "shopset 10340").lower()
        assert "Craft recipe not found." in _safe_cmd(imm, "cfedit nonexistent_recipe")
        assert "Object not found." in _safe_cmd(imm, "oedit nonexistent_obj_xyz")
        assert "No one like that in all the realms." in _safe_cmd(imm, "medit nonexistent_mob_xyz")
        assert "Unknown OLC command" in _safe_cmd(imm, "olc unknownmode")
    finally:
        _cleanup_client(imm)


@test_case("T2-F11-05", 2, "F11", "repair/appraise boundaries (no fixer, undamaged item, non-repairable, 0 credits)")
def test_t2_f11_05(ctx: E2EContext) -> None:
    imm = _login_imm(ctx)
    mort = None
    try:
        _safe_cmd(imm, "goto 10300")
        _safe_cmd(imm, "oinvoke 10423")
        assert "You can't do that here." in _safe_cmd(imm, "appraise key")
        assert "You can't do that here." in _safe_cmd(imm, "repair key")

        loc = _safe_cmd(imm, "goto 7312")
        assert "No such location." not in loc, f"mall #7312 not loaded:\n{loc}"

        _safe_cmd(imm, "oinvoke 10308")
        fine = _safe_cmd(imm, "appraise vest")
        assert "looks fine to me" in fine.lower(), f"Expected undamaged vest message:\n{fine}"

        bad_type = _safe_cmd(imm, "appraise key")
        assert "can't do anything with" in bad_type.lower(), (
            f"Expected non-repairable type rejection:\n{bad_type}"
        )

        mort, mname = _create_authed_mortal(ctx, imm, gold=0, room=7312)
        _safe_cmd(imm, "oset vest value0 1")
        _safe_cmd(imm, "oset vest cost 1000")
        _safe_cmd(imm, f"give vest {mname}")
        _safe_drain(mort, 0.2)
        poor_rep = _safe_cmd(mort, "repair vest")
        assert "can't afford" in poor_rep.lower(), (
            f"Expected insufficient-credits repair rejection:\n{poor_rep}"
        )
    finally:
        try:
            _safe_cmd(imm, "goto 100")
        except Exception:
            pass
        _cleanup_client(mort)
        _cleanup_client(imm)


@test_case("T2-F11-06", 2, "F11", "Duplicate OLC entry rejection & savearea unassigned-area guard")
def test_t2_f11_06(ctx: E2EContext) -> None:
    imm = _login_imm(ctx)
    try:
        sa = _safe_cmd(imm, "savearea")
        assert "don't have an assigned area to save" in sa.lower(), (
            f"Expected unassigned savearea guard:\n{sa}"
        )
        _safe_cmd(imm, "redit2", require_prompt=True)
        dup = _safe_cmd(imm, "medit 10345", require_prompt=True)
        assert "already editing something" in dup.lower(), (
            f"Expected duplicate OLC entry rejection:\n{dup}"
        )
        abt = _safe_cmd(imm, "stop abort", require_prompt=True)
        assert "Changes discarded." in abt
    finally:
        try:
            _safe_cmd(imm, "stop abort", require_prompt=True)
        except Exception:
            pass
        _cleanup_client(imm)


# ===========================================================================
# F12 — Crafting System (6 boundary/corner/negative tests)
# ===========================================================================


@test_case("T2-F12-02", 2, "F12", "Non-factory room rejection when crafting recipe requiring ROOM_FACTORY")
def test_t2_f12_02(ctx: E2EContext) -> None:
    imm = _login_imm(ctx)
    mort = None
    try:
        mort, name = _create_authed_mortal(ctx, imm, room=10300, prefix="Dals")
        _safe_cmd(imm, f"sset {name} 'spice refining' 100")
        _safe_cmd(imm, "goto 10300")
        _safe_cmd(imm, "oinvoke 10487")
        _safe_cmd(imm, f"give carsanum {name}")

        _safe_cmd(mort, "craft")
        s1 = _safe_cmd(mort, "select spice")
        s2 = _safe_cmd(mort, "select auto")
        s3 = _safe_cmd(mort, "build")
        _safe_cmd(mort, "cancel")
        combined = s1 + "\n" + s2 + "\n" + s3
        assert "factory or workshop" in combined.lower(), (
            f"Expected ROOM_FACTORY rejection at #10300:\n{combined}"
        )
    finally:
        try:
            _safe_cmd(imm, "goto 100")
        except Exception:
            pass
        _cleanup_client(mort)
        _cleanup_client(imm)


@test_case("T2-F12-03", 2, "F12", "Missing material, wrong item type, and full-slot rejection in craft")
def test_t2_f12_03(ctx: E2EContext) -> None:
    imm = _login_imm(ctx)
    mort = None
    try:
        mort, name = _create_authed_mortal(ctx, imm, room=10400)
        _safe_cmd(imm, f"sset {name} makeblaster 100")
        _safe_cmd(imm, "goto 10400")
        _safe_cmd(imm, "oinvoke 10423")
        _safe_cmd(imm, f"give key {name}")
        _safe_cmd(imm, "oinvoke 10490")
        _safe_cmd(imm, f"give toolkit {name}")
        _safe_cmd(imm, "oinvoke 10490")
        _safe_cmd(imm, f"give toolkit {name}")
        _safe_drain(mort, 0.25)

        _safe_cmd(mort, "craft")
        _safe_cmd(mort, "select blaster")
        bad_type = _safe_cmd(mort, "use toolkit key")
        assert "does not fit" in bad_type.lower(), f"Expected wrong-type rejection:\n{bad_type}"

        _safe_cmd(mort, "use toolkit toolkit")
        full_slot = _safe_cmd(mort, "use toolkit toolkit")
        assert "already full" in full_slot.lower(), f"Expected full-slot rejection:\n{full_slot}"

        miss = _safe_cmd(mort, "build")
        assert "You begin working" not in miss, f"Build started with missing slots:\n{miss}"
        _safe_cmd(mort, "cancel")
    finally:
        try:
            _safe_cmd(imm, "goto 100")
        except Exception:
            pass
        _cleanup_client(mort)
        _cleanup_client(imm)


@test_case("T2-F12-05", 2, "F12", "craft info on lightsaber/blaster/bowcaster avoids obj_flag_table OOB (OLC-002)", fresh_server=True)
def test_t2_f12_05(ctx: E2EContext) -> None:
    proc = _safe_start_fresh(ctx, "f12-info-oob")
    imm = _login_imm(ctx, port=proc.port)
    try:
        _safe_cmd(imm, "goto 10400")
        _safe_cmd(imm, "craft")
        for rcp in ("lightsaber", "blaster", "bowcaster"):
            inf = _safe_cmd(imm, f"info {rcp}")
            assert rcp in inf.lower(), f"craft info {rcp} failed:\n{inf}"
        _safe_cmd(imm, "cancel")
        _cleanup_client(imm)
        imm = None
        shut = _safe_shutdown_proc(proc)
        assert not shut.sanitizer_report.asan_errors, (
            f"OLC-002 ASan OOB on craft info: {shut.sanitizer_report.asan_errors}"
        )
    finally:
        _cleanup_client(imm)


@test_case("T2-F12-06", 2, "F12", "disguise sexrace invalid bounds and unknown craft recipe/subcommand rejections")
def test_t2_f12_06(ctx: E2EContext) -> None:
    imm = _login_imm(ctx)
    mort = None
    try:
        mort, name = _create_authed_mortal(ctx, imm, room=10400)
        _safe_cmd(imm, f"sset {name} disguise 100")

        _safe_cmd(mort, "craft")
        no_rcp = _safe_cmd(mort, "select nonexistent")
        assert "No such recipe." in no_rcp, f"Expected unknown recipe error:\n{no_rcp}"

        _safe_cmd(mort, "select disguise")
        bad_sr = _safe_cmd(mort, "set sexrace 9 99")
        assert "Usage: set sexrace" in bad_sr, f"Expected sexrace usage bounds:\n{bad_sr}"

        bad_fld = _safe_cmd(mort, "set unknownfield 1")
        assert "not used by this recipe" in bad_fld.lower(), (
            f"Expected unknown craft field error:\n{bad_fld}"
        )

        _safe_cmd(mort, "cancel")
        bad_cmd = _safe_cmd(mort, "craft badcraftcmd")
        assert "not a crafting command" in bad_cmd.lower(), (
            f"Expected bad craft command error:\n{bad_cmd}"
        )
        _safe_cmd(mort, "cancel")
    finally:
        _cleanup_client(mort)
        _cleanup_client(imm)


# ===========================================================================
# F13 — World Handler, Resets & Ticks (6 boundary/negative tests)
# ===========================================================================


@test_case("T2-F13-01", 2, "F13", "push invalid, missing, and non-lever object rejections")
def test_t2_f13_01(ctx: E2EContext) -> None:
    imm = _login_imm(ctx)
    try:
        _safe_cmd(imm, "goto 10319")
        assert "Push what?" in _safe_cmd(imm, "push")
        assert "I see no nosuchthing here." in _safe_cmd(imm, "push nosuchthing")
        _safe_cmd(imm, "oinvoke 20")
        _safe_cmd(imm, "drop roll")
        assert "You can't push that!" in _safe_cmd(imm, "push roll")
    finally:
        try:
            _safe_cmd(imm, "purge")
            _safe_cmd(imm, "goto 100")
        except Exception:
            pass
        _cleanup_client(imm)


@test_case("T2-F13-02", 2, "F13", "Mortal blocked from reset/rreset; invalid mobile/object/room vnums rejected")
def test_t2_f13_02(ctx: E2EContext) -> None:
    imm = _login_imm(ctx)
    mort = None
    try:
        mort, _ = _create_authed_mortal(ctx, imm, room=10300)
        assert "Huh?" in _safe_cmd(mort, "reset list")
        assert "Huh?" in _safe_cmd(mort, "rreset list")

        _safe_cmd(imm, "goto 11")
        assert "Mobile does not exist." in _safe_cmd(imm, "reset mobile 99999 1 11")
        assert "Object does not exist." in _safe_cmd(imm, "reset object 99999 1 11")
        assert "Room does not exist." in _safe_cmd(imm, "reset mobile 14 1 99999")
    finally:
        try:
            _safe_cmd(imm, "goto 100")
        except Exception:
            pass
        _cleanup_client(mort)
        _cleanup_client(imm)


@test_case("T2-F13-03", 2, "F13", "reset trap and reset bit invalid type, charges, and flag boundaries")
def test_t2_f13_03(ctx: E2EContext) -> None:
    imm = _login_imm(ctx)
    try:
        _safe_cmd(imm, "goto 11")
        assert "invalid trap type" in _safe_cmd(imm, "reset trap room 99 1 room").lower()
        assert "invalid trap charges" in _safe_cmd(imm, "reset trap room 1 -5 room").lower()
        assert "bad flag" in _safe_cmd(imm, "reset bit set room 11 bogusflag").lower()
    finally:
        try:
            _safe_cmd(imm, "goto 100")
        except Exception:
            pass
        _cleanup_client(imm)


@test_case("T2-F13-04", 2, "F13", "reset on/done and rreset on/rreset repeat-mode exit safety (WLD-001)", fresh_server=True)
def test_t2_f13_04(ctx: E2EContext) -> None:
    proc = _safe_start_fresh(ctx, "f13-reset-on-done")
    imm = _login_imm(ctx, port=proc.port)
    try:
        _safe_cmd(imm, "goto 11")
        _safe_cmd(imm, "aassign limbo.are")
        on1 = _safe_cmd(imm, "reset on")
        assert "Reset mode on." in on1, f"Expected reset on:\n{on1}"
        _safe_cmd(imm, "list")
        off1 = _safe_cmd(imm, "done")
        assert "Reset mode off." in off1, f"Expected reset done:\n{off1}"

        on2 = _safe_cmd(imm, "rreset on")
        assert "Reset mode on." in on2, f"Expected rreset on:\n{on2}"
        _safe_cmd(imm, "rreset")
        rs = _safe_cmd(imm, "rstat 11")
        assert "11" in rs

        _cleanup_client(imm)
        imm = None
        shut = _safe_shutdown_proc(proc)
        assert not shut.sanitizer_report.asan_errors, (
            f"WLD-001 ASan error on reset on/done: {shut.sanitizer_report.asan_errors}"
        )
    finally:
        _cleanup_client(imm)


@test_case("T2-F13-05", 2, "F13", "aggr_update ignores Immortals and ROOM_SAFE")
def test_t2_f13_05(ctx: E2EContext) -> None:
    imm = _login_imm(ctx)
    try:
        _safe_cmd(imm, "goto 10300")
        _safe_cmd(imm, "minvoke 10")
        _safe_cmd(imm, "mset wolf flags aggressive")
        time.sleep(2.0)
        lk = _safe_cmd(imm, "look")
        assert "fighting" not in lk.lower() and "wolf" in lk.lower(), (
            f"Aggressive mob attacked Immortal in ROOM_SAFE:\n{lk}"
        )
    finally:
        try:
            _safe_cmd(imm, "mpurge")
            _safe_cmd(imm, "goto 100")
        except Exception:
            pass
        _cleanup_client(imm)


@test_case("T2-F13-06", 2, "F13", "gain_addiction tick safety on spiced player (AUD-229)", fresh_server=True)
def test_t2_f13_06(ctx: E2EContext) -> None:
    proc = _safe_start_fresh(ctx, "f13-addiction")
    imm = _login_imm(ctx, port=proc.port)
    mort = None
    try:
        mort, name = _create_authed_mortal(ctx, imm, port=proc.port, room=10300)
        _safe_cmd(imm, "goto 10300")
        _safe_cmd(imm, "oinvoke 10487")
        _safe_cmd(imm, "oset carsanum type spice")
        _safe_cmd(imm, "oset carsanum value0 0")
        _safe_cmd(imm, "oset carsanum value1 5")
        _safe_cmd(imm, f"give carsanum {name}")
        _safe_drain(mort, 0.2)
        _safe_cmd(mort, "use carsanum")

        # Wait through one PULSE_TICK (52.5..87.5s) and verify server/player healthy
        deadline = time.monotonic() + 95.0
        while time.monotonic() < deadline:
            _safe_drain(mort, 0.2)
            sc = _safe_cmd(mort, "score")
            if "descends to the material plane" in sc.lower():
                sc = _safe_cmd(mort, "score")
            assert "level" in sc.lower() or "credits" in sc.lower() or name.lower() in sc.lower(), (
                f"Player score failed during tick wait:\n{sc}"
            )
            if time.monotonic() + 5.0 >= deadline:
                break
            time.sleep(5.0)
        _cleanup_client(mort)
        mort = None
        _cleanup_client(imm)
        imm = None
        shut = _safe_shutdown_proc(proc)
        assert not shut.sanitizer_report.asan_errors and not shut.sanitizer_report.ubsan_errors, (
            f"Sanitizer error during gain_addiction tick: {shut.sanitizer_report}"
        )
    finally:
        _cleanup_client(mort)
        _cleanup_client(imm)


# ===========================================================================
# F14 — MUDProgs, Admin & grub (6 boundary/negative tests)
# ===========================================================================


@test_case("T2-F14-02", 2, "F14", "shutdown permission boundaries (mortal, level 106 imm, shutdow prefix, bad syntax)", fresh_server=True)
def test_t2_f14_02(ctx: E2EContext) -> None:
    ws = _safe_new_workspace(ctx, "f14-shutdown-perm", copy_binary=True, seed_immortal=True)
    proc = _safe_start_proc(
        ctx,
        ws,
        seed_name="Immlo",
        seed_password="E2eUserPass",
        seed_level=106,
    )
    imm = None
    immlo = None
    mort = None
    try:
        imm = _login_imm(ctx, port=proc.port)
        mort, _ = _create_authed_mortal(ctx, imm, port=proc.port, room=10300)
        assert "Huh?" in _safe_cmd(mort, "shutdown mud now")

        immlo = _safe_connect(ctx, port=proc.port)
        _safe_login(immlo, "Immlo", "E2eUserPass")
        assert "Huh?" in _safe_cmd(immlo, "shutdown mud now")
        _safe_drain(imm, 0.35)

        sh_p = _safe_cmd(imm, "shutdow")
        assert "spell it out" in sh_p.lower(), f"Expected shutdow warning, got:\n{sh_p}"
        assert "Syntax: 'shutdown mud now'" in _safe_cmd(imm, "shutdown")
        assert "Syntax: 'shutdown mud now'" in _safe_cmd(imm, "shutdown later")
    finally:
        _cleanup_client(immlo)
        _cleanup_client(mort)
        _cleanup_client(imm)
        if proc.is_running():
            proc.shutdown_clean()


@test_case("T2-F14-06", 2, "F14", "sedit invalid social & Immortal say iamauthed at #10324")
def test_t2_f14_06(ctx: E2EContext) -> None:
    imm = _login_imm(ctx)
    try:
        _safe_cmd(imm, "goto 10324")
        auth = _safe_cmd(imm, "say iamauthed")
        assert "trans yourself" in auth.lower(), (
            f"Expected #10324 Immortal iamauthed response:\n{auth}"
        )
        assert "Social not found." in _safe_cmd(imm, "sedit nosuchsocial show")
    finally:
        try:
            _safe_cmd(imm, "goto 100")
        except Exception:
            pass
        _cleanup_client(imm)


# ===========================================================================
# F15 — Gameplay, Shops, Vendors & Clans/Planets (6 boundary tests)
# ===========================================================================


@test_case("T2-F15-01", 2, "F15", "Shop negative quantity buy -5, unaffordable buy, and ITEM_NODROP sell (AUD-222)")
def test_t2_f15_01(ctx: E2EContext) -> None:
    imm = _login_imm(ctx)
    mort = None
    try:
        mort, mname = _create_authed_mortal(ctx, imm, gold=0, room=10321)
        neg = _safe_cmd(mort, "buy -5 backpack")
        assert "You buy" not in neg, f"AUD-222 regression: negative quantity 'buy -5' accepted:\n{neg}"

        poor = _safe_cmd(mort, "buy backpack")
        assert "can't afford" in poor.lower(), f"Expected unaffordable rejection:\n{poor}"

        _safe_cmd(imm, "goto 10321")
        _safe_cmd(imm, "oinvoke 10317")
        _safe_cmd(imm, "oset backpack flags nodrop")
        _safe_cmd(imm, f"give backpack {mname}")
        _safe_drain(mort, 0.2)
        nd = _safe_cmd(mort, "sell backpack")
        assert "You can't let go of it!" in nd, f"Expected ITEM_NODROP sell rejection:\n{nd}"
    finally:
        try:
            _safe_cmd(imm, "goto 100")
        except Exception:
            pass
        _cleanup_client(mort)
        _cleanup_client(imm)


@test_case("T2-F15-02", 2, "F15", "PLR_BOUGHT_PET one-pet-per-level limit enforcement (AUD-222)", fresh_server=True)
def test_t2_f15_02(ctx: E2EContext) -> None:
    proc = _safe_start_fresh(ctx, "f15-pet-limit")
    imm = _login_imm(ctx, port=proc.port)
    try:
        _safe_cmd(imm, "goto 11")
        _safe_cmd(imm, "rset 11 flags pet_shop")
        _safe_cmd(imm, "goto 12")
        _safe_cmd(imm, "mpurge")
        _safe_cmd(imm, "minvoke 14")
        _safe_cmd(imm, "mset cat flags pet")
        _safe_cmd(imm, "goto 11")

        b1 = _safe_cmd(imm, "buy cat")
        assert "Enjoy your pet." in b1, f"1st pet purchase failed:\n{b1}"
        _safe_cmd(imm, "goto 12")
        _safe_cmd(imm, "minvoke 10")
        _safe_cmd(imm, "mset wolf flags pet")
        _safe_cmd(imm, "goto 11")

        b2 = _safe_cmd(imm, "buy wolf")
        assert "already bought one pet" in b2.lower(), (
            f"AUD-222 regression: allowed buying a 2nd pet in same level:\n{b2}"
        )
    finally:
        _cleanup_client(imm)


@test_case("T2-F15-03", 2, "F15", "Player vendor owner pricing and collectgold access control", fresh_server=True)
def test_t2_f15_03(ctx: E2EContext) -> None:
    proc = _safe_start_fresh(ctx, "f15-vendor-auth")
    imm = _login_imm(ctx, port=proc.port)
    owner = None
    thief = None
    try:
        owner, oname = _create_authed_mortal(ctx, imm, port=proc.port, level=55, gold=2000000, room=10321)
        thief, _ = _create_authed_mortal(ctx, imm, port=proc.port, level=10, gold=5000, room=11)
        _safe_cmd(imm, "rset 11 flags plr_shop")
        _safe_cmd(imm, "goto 10321")
        _safe_cmd(imm, "oinvoke 10311")
        _safe_cmd(imm, f"give glowrod {oname}")
        _safe_drain(owner, 0.2)
        _safe_cmd(owner, "buyvendor")
        _safe_cmd(imm, f"transfer {oname} 11")
        _safe_drain(owner, 0.2)
        _safe_cmd(owner, "placevendor")
        _safe_cmd(owner, "give glowrod vendor")
        _safe_drain(thief, 0.25)

        price = _safe_cmd(owner, "pricevendor glowrod 500")
        assert "The price has been changed." in price, price

        shops = (ctx.repo_path / "src" / "shops.c").read_text(encoding="utf-8")
        pricing = shops.split("void do_pricevendor(", 1)[1].split("void do_collectgold(", 1)[0]
        owner_guard = re.search(
            r"if\s*\(\s*!vendor->owner\s*\|\|\s*str_cmp\(ch->name, vendor->owner\)\s*\)"
            r"\s*\{[^}]*\breturn;",
            pricing,
        )
        assert owner_guard and owner_guard.start() < pricing.index("obj->cost =")

        cg = _safe_cmd(thief, "getcredits")
        assert "isn't your vendor" in cg.lower() or "steal" in cg.lower(), (
            f"Expected non-owner getcredits rejection:\n{cg}"
        )
    finally:
        _cleanup_client(thief)
        _cleanup_client(owner)
        _cleanup_client(imm)


@test_case("T2-F15-05", 2, "F15", "clone Immortal, insufficient-credits, and clones >= 2 degradation boundaries (AUD-152)")
def test_t2_f15_05(ctx: E2EContext) -> None:
    imm = _login_imm(ctx)
    poor = None
    deg = None
    try:
        loc = _safe_cmd(imm, "goto 10001")
        assert "No such location." not in loc, f"area/cloning (#10001) not loaded:\n{loc}"
        imm_cln = _safe_cmd(imm, "clone")
        assert "You can't clone an imm!" in imm_cln, f"Expected Immortal clone rejection:\n{imm_cln}"

        poor, _ = _create_authed_mortal(ctx, imm, level=1, gold=10, room=10001)
        poor_out = _safe_cmd(poor, "clone")
        assert "don't have enough credits" in poor_out.lower(), (
            f"Expected insufficient credits clone rejection:\n{poor_out}"
        )

        # Clone generation advances in the persisted clone, which takes effect on death.
        dname = _next_mortal_name()
        pfile = seed_des_legacy_fixture(ctx.shared_server.work_dir, dname, "E2ePass1", level=1)
        saved = pfile.read_text(encoding="utf-8")
        saved = saved.replace("Room         10300", "Room         10001")
        saved = saved.replace("Played       100", "Clones       2\nPlayed       100")
        pfile.write_text(saved, encoding="utf-8")
        deg = _safe_connect(ctx)
        _safe_login(deg, dname, "E2ePass1")
        third = _safe_cmd(deg, "clone")
        assert "too far degraded" in third.lower(), (
            f"Expected clones >= 2 degradation rejection:\n{third}"
        )
    finally:
        try:
            _safe_cmd(imm, "goto 100")
        except Exception:
            pass
        _cleanup_client(deg)
        _cleanup_client(poor)
        _cleanup_client(imm)


# ===========================================================================
# F16 — Comms, Aliases, Boards, Arena, Trivia & Bounty (6 boundary tests)
# ===========================================================================


@test_case("T2-F16-03", 2, "F16", "Comms: online retell, extraction reference cleanup (AUD-025), and missing comlink boundaries (AUD-259)")
def test_t2_f16_03(ctx: E2EContext) -> None:
    imm = _login_imm(ctx)
    vic = None
    mute = None
    try:
        vic, vname = _create_authed_mortal(ctx, imm, room=10300)
        language = _safe_cmd(imm, "speak all")
        assert "understood by all" in language, language
        tell = _safe_cmd(imm, f"tell {vname} ping")
        assert "ping" in tell, tell
        _safe_read_until(vic, r"\bping\b")
        retell = _safe_cmd(imm, "retell secondping")
        assert vname in retell and "secondping" in retell, retell
        _safe_read_until(vic, r"\bsecondping\b")

        handler = (ctx.repo_path / "src" / "handler.c").read_text(encoding="utf-8")
        extraction = handler.split("void extract_char(", 1)[1]
        cleanup = extraction.split("olc_invalidate_target", 1)[0]
        assert re.search(r"if\s*\(\s*wch->retell\s*==\s*ch\s*\)\s*wch->retell\s*=\s*NULL;", cleanup)

        mute, mname = _create_authed_mortal(ctx, imm, gold=137, room=10300)
        _safe_cmd(mute, "drop comlink")
        t_nocom = _safe_cmd(mute, "tell Immtest hi")
        assert "need a comlink" in t_nocom.lower(), f"Expected comlink error on tell:\n{t_nocom}"
        c_nocom = _safe_cmd(mute, "chat hi")
        assert "need a comlink" in c_nocom.lower(), f"Expected comlink error on chat:\n{c_nocom}"
        sc = _safe_cmd(mute, "score")
        assert re.search(r"(?i)\bcredits\s*[:=]?\s*137\b", sc), (
            f"AUD-259 regression: chat without comlink charged gold:\n{sc}"
        )
    finally:
        _cleanup_client(mute)
        _cleanup_client(vic)
        _cleanup_client(imm)


@test_case("T2-F16-06", 2, "F16", "Bounty <5000 / NPC / non-hunter rejections & non-hunter kill preserves bounty (AUD-183)", fresh_server=True)
def test_t2_f16_06(ctx: E2EContext) -> None:
    proc = _safe_start_fresh(ctx, "f16-bounty-neg")
    imm = _login_imm(ctx, port=proc.port)
    poster = None
    victim = None
    killer = None
    try:
        poster, _ = _create_authed_mortal(ctx, imm, port=proc.port, gold=10000, room=10300)
        vname = _next_mortal_name()
        pfile = seed_des_legacy_fixture(proc.work_dir, vname, "E2ePass1", level=1)
        saved = pfile.read_text(encoding="utf-8")
        saved = saved.replace("Room         10300", "Room         10308")
        saved = saved.replace("HpManaMove   500 500 0 0 500 500", "HpManaMove   1 1 0 0 500 500")
        pfile.write_text(saved, encoding="utf-8")
        victim = _safe_connect(ctx, port=proc.port)
        login = _safe_login(victim, vname, "E2ePass1")
        assert "Health:1/1" in login, login
        killer, _ = _create_authed_mortal(ctx, imm, port=proc.port, level=25, gold=100, room=10308)
        _safe_drain(poster, 0.25)
        _safe_drain(killer, 0.25)

        assert "Only hunters can access" in _safe_cmd(poster, "bounties")
        assert "at least 5000 credits" in _safe_cmd(poster, f"addbounty {vname} 4999")
        assert "at least 5000 credits" in _safe_cmd(poster, f"addbounty {vname} -5000")
        assert "not mobs" in _safe_cmd(poster, "addbounty chadoyn 5000").lower()
        assert "don't have that many credits" in _safe_cmd(poster, f"addbounty {vname} 20000").lower()

        ok = _safe_cmd(poster, f"addbounty {vname} 5000")
        assert "5000 credits" in ok

        combat = _safe_cmd(killer, f"murder {vname}")
        assert "huh?" not in combat.lower(), combat
        # Use the seeded non-hunter administrator to reach the normal death hook
        # without waiting for bare-handed combat to reach the PC death threshold.
        _safe_cmd(imm, "goto 10308")
        death = _safe_cmd(imm, f"slay {vname}")
        assert f"You are now wanted for the murder of {vname}." in death, death

        bl = _safe_cmd(imm, "bounties")
        assert vname in bl, (
            f"AUD-183 regression: bounty erased when killed by non-hunter:\n{bl}"
        )
    finally:
        _cleanup_client(killer)
        _cleanup_client(victim)
        _cleanup_client(poster)
        _cleanup_client(imm)


# ===========================================================================
# F17 — Space, Ships, Templates & Planets (6 boundary/negative tests)
# ===========================================================================


@test_case("T2-F17-01", 2, "F17", "ordership negative boundaries: non-shipyard, wrong faction shipyard, invalid template, insufficient gold")
def test_t2_f17_01(ctx: E2EContext) -> None:
    imm = _login_imm(ctx)
    poor = None
    try:
        _safe_cmd(imm, "goto 10300")
        non_yard = _safe_cmd(imm, "ordership 1 BadLoc")
        assert "not a shipyard that takes orders" in non_yard.lower(), non_yard

        _safe_cmd(imm, "goto 32050")
        imp = _safe_cmd(imm, "ordership 17 ImpAtKuat")
        assert "imperial shipyard" in imp.lower(), (
            f"Expected Imperial shipyard restriction at 32050:\n{imp}"
        )
        bad_num = _safe_cmd(imm, "ordership abc BadNum")
        assert "Usage: ordership <templatetype> <newshipname>" in bad_num, bad_num
        bad_tmpl = _safe_cmd(imm, "ordership 999 BadTmpl")
        assert "Usage: ordership <templatetype> <newshipname>" in bad_tmpl, bad_tmpl

        poor, _ = _create_authed_mortal(ctx, imm, level=10, gold=100, room=32050)
        p_out = _safe_cmd(poor, "ordership 1 TooExpensive")
        assert "do not have enough credits to purchase this ship" in p_out.lower(), (
            f"Expected insufficient gold ordership rejection:\n{p_out}"
        )
    finally:
        try:
            _safe_cmd(imm, "goto 100")
        except Exception:
            pass
        _cleanup_client(poor)
        _cleanup_client(imm)


@test_case("T2-F17-03", 2, "F17", "Closed hatch board, non-owner openhatch/launch, and wrong-seat rejection")
def test_t2_f17_03(ctx: E2EContext) -> None:
    imm = _login_imm(ctx)
    stranger = None
    try:
        _safe_cmd(imm, "goto 32050")
        _safe_cmd(imm, "ordership 0 OwnerShuttle")
        sh = _safe_cmd(imm, "showship OwnerShuttle")
        assert "No such ship." not in sh, (
            "ordership OwnerShuttle failed (templates.are 2701..2762 not loaded)"
        )
        m_ent = re.search(r"Entrance:\s*(\d+)", sh)
        m_cp = re.search(r"Cockpit:\s*(\d+)", sh)

        stranger, sname = _create_authed_mortal(ctx, imm, level=10, gold=5000, room=32050)
        closed_b = _safe_cmd(stranger, "board OwnerShuttle")
        assert "hatch is closed" in closed_b.lower(), f"Expected closed hatch rejection:\n{closed_b}"

        unowned_o = _safe_cmd(stranger, "openhatch OwnerShuttle")
        assert "opens the hatch" not in unowned_o.lower(), (
            f"Non-owner opened hatch:\n{unowned_o}"
        )

        _safe_cmd(imm, "openhatch OwnerShuttle")
        _safe_drain(stranger, 0.25)
        _safe_cmd(stranger, "board OwnerShuttle")
        if m_cp:
            _safe_cmd(imm, f"transfer {sname} {m_cp.group(1)}")
            _safe_drain(stranger, 0.25)
        unowned_l = _safe_cmd(stranger, "launch")
        assert "Launch sequence initiated" not in unowned_l, (
            f"Non-owner/non-pilot launched ship:\n{unowned_l}"
        )

        if m_ent and m_cp and m_ent.group(1) != m_cp.group(1):
            _safe_cmd(imm, f"goto {m_ent.group(1)}")
            seat_l = _safe_cmd(imm, "launch")
            assert "Launch sequence initiated" not in seat_l, (
                f"Launched ship from entrance instead of cockpit:\n{seat_l}"
            )
    finally:
        try:
            _safe_cmd(imm, "goto 100")
            _safe_cmd(imm, "shipdelete OwnerShuttle")
        except Exception:
            pass
        _cleanup_client(stranger)
        _cleanup_client(imm)


def _launch_test_ship(
    pilot: TelnetClient, name: str, *, pad: int = 32000,
    module: str | None = None, **properties: int
) -> tuple[int, int, int]:
    _safe_cmd(pilot, "goto 32000")
    _safe_cmd(pilot, f"ordership 1 {name}")
    ship = _safe_cmd(pilot, f"showship {name}")
    seats = re.search(r"Pilotseat:\s*(\d+)\s+Coseat:\s*(\d+)\s+Navseat:\s*(\d+)", ship)
    assert seats, ship
    pilot_seat, copilot_seat, nav_seat = map(int, seats.groups())
    _safe_cmd(pilot, f"setship {name} shipyard {pad}")
    _safe_cmd(pilot, f"setship {name} energy 10000")
    _safe_cmd(pilot, f"resetship {name}")
    for field, value in properties.items():
        _safe_cmd(pilot, f"setship {name} {field} {value}")
    _safe_cmd(pilot, f"goto {pad}")
    if module is not None:
        installed = _safe_cmd(pilot, f"install_module {module} {name}")
        assert "You install " in installed, installed
    _safe_cmd(pilot, f"openhatch {name}")
    _safe_cmd(pilot, f"board {name}")
    _safe_cmd(pilot, f"goto {pilot_seat}")
    launch = _safe_cmd(pilot, "launch")
    assert "Launch sequence initiated" in launch or "flies into space" in launch, launch
    if "Launch complete." not in launch:
        _safe_read_until(pilot, r"Launch complete\.", timeout=25.0)
    _safe_drain(pilot)
    return pilot_seat, copilot_seat, nav_seat


@test_case("T2-F17-04", 2, "F17", "Positive-power gravity well interdicts an approaching ship in hyperspace (AUD-209)", fresh_server=True)
def test_t2_f17_04(ctx: E2EContext) -> None:
    proc = _safe_start_fresh(ctx, "f17-gravwell")
    imm = _login_imm(ctx, port=proc.port)
    try:
        for skill in ("starfighters", "navigation", "'ship maintenance'"):
            _safe_cmd(imm, f"sset Immtest {skill} 100")

        # Repurpose a builder-spawned item in this private world as a fighter projector.
        _safe_cmd(imm, "oinvoke 10487")
        _safe_cmd(imm, "oset carsanum type fightercomp")
        _safe_cmd(imm, "oset carsanum value0 100")  # Condition
        _safe_cmd(imm, "oset carsanum value1 19")   # MOD_GRAVITY_PROJ
        _safe_cmd(imm, "oset carsanum value2 1")    # Module slot size
        _safe_cmd(imm, "oset carsanum value3 1000")  # Maximum power
        _, copilot_seat, _ = _launch_test_ship(
            imm, "GravShuttle", pad=10252, module="carsanum", speed=1,
        )
        _safe_cmd(imm, f"goto {copilot_seat}")
        activated = _safe_cmd(imm, "gravityprojector 200")
        assert "activate the gravity projectors at partial power" in activated.lower(), activated
        projector = _safe_cmd(imm, "showship GravShuttle")
        assert re.search(r"Gravity Projector:\s*200/1000\b", projector), projector

        pilot_seat, _, nav_seat = _launch_test_ship(
            imm, "JumpWing", speed=200, hyperspeed=100, astroarray=255,
        )
        _safe_cmd(imm, f"goto {nav_seat}")
        course = _safe_cmd(imm, "calculate Coruscant")
        assert "Hyperspace course set" in course, course
        _safe_cmd(imm, f"goto {pilot_seat}")
        jump = _safe_cmd(imm, "hyperspace")
        assert "makes the jump to lightspeed" in jump, jump
        interdiction = "An artificial gravity well surrounds you!"
        if interdiction not in jump:
            arrival = _safe_read_until(imm, re.escape(interdiction), timeout=30)
            assert interdiction in arrival, arrival
    finally:
        _cleanup_client(imm)


@test_case("T2-F17-05", 2, "F17", "calculate missing hyperdrive and unknown system")
def test_t2_f17_05(ctx: E2EContext) -> None:
    imm = _login_imm(ctx)
    try:
        for skill in ("starfighters", "navigation"):
            _safe_cmd(imm, f"sset Immtest {skill} 100")
        _, _, nav_seat = _launch_test_ship(imm, "NoHyper", hyperspeed=0)
        _safe_cmd(imm, f"goto {nav_seat}")

        no_hd = _safe_cmd(imm, "calculate Coruscant")
        assert "not equipped with a hyperdrive" in no_hd.lower(), no_hd

        _safe_cmd(imm, "setship NoHyper hyperspeed 100")
        unk = _safe_cmd(imm, "calculate NoSuchSystem999")
        assert "enter a charted destination or three coordinates" in unk.lower(), unk
    finally:
        try:
            _safe_cmd(imm, "goto 100")
            _safe_cmd(imm, "shipdelete NoHyper")
        except Exception:
            pass
        _cleanup_client(imm)


# ===========================================================================
# F18 — Build & Toolchain (6 boundary/negative tests, requires_server=False)
# ===========================================================================


@test_case("T2-F18-01", 2, "F18", "swrip CLI rejects ports <= 1024 (1024, 0, -1) with exit code 1", requires_server=False)
def test_t2_f18_01(ctx: E2EContext) -> None:
    ws = _safe_new_workspace(ctx, "f18-cli-lowport", copy_binary=True)
    bin_path = ws / "bin" / "swrip"
    area_dir = ws / "area"
    try:
        for bad_port in ("1024", "0", "-1"):
            proc = _safe_run_subprocess(
                [str(bin_path), bad_port],
                cwd=str(area_dir),
                capture_output=True,
                text=True,
                timeout=5.0,
                check=False,
            )
            assert proc.returncode == 1, f"Expected rc=1 for port {bad_port}, got {proc.returncode}"
            assert "Port number must be above 1024." in (proc.stderr + proc.stdout), (
                f"Missing port error message for {bad_port}: {proc.stderr!r}"
            )
    finally:
        shutil.rmtree(ws, ignore_errors=True)


@test_case("T2-F18-02", 2, "F18", "swrip CLI rejects non-numeric port arguments (abc, 12ab, '')", requires_server=False)
def test_t2_f18_02(ctx: E2EContext) -> None:
    ws = _safe_new_workspace(ctx, "f18-cli-badarg", copy_binary=True)
    bin_path = ws / "bin" / "swrip"
    area_dir = ws / "area"
    try:
        for bad_arg in ("abc", "12ab", ""):
            proc = _safe_run_subprocess(
                [str(bin_path), bad_arg],
                cwd=str(area_dir),
                capture_output=True,
                text=True,
                timeout=5.0,
                check=False,
            )
            assert proc.returncode == 1, f"Expected rc=1 for arg {bad_arg!r}, got {proc.returncode}"
            assert "Usage:" in (proc.stderr + proc.stdout), (
                f"Missing Usage error for {bad_arg!r}: {proc.stderr!r}"
            )
    finally:
        shutil.rmtree(ws, ignore_errors=True)


@test_case("T2-F18-04", 2, "F18", "bin/startup rejects invalid ports (1024, 65536, 0, notaport) before launch", requires_server=False)
def test_t2_f18_04(ctx: E2EContext) -> None:
    ws = _safe_new_workspace(ctx, "f18-startup-badport", copy_binary=True)
    try:
        for arg in ("1024", "65536", "0", "notaport"):
            res = _safe_run_subprocess(
                ["bash", str(ws / "bin" / "startup"), arg],
                cwd=str(ws / "area"),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=5.0,
                check=False,
            )
            assert res.returncode == 1, f"Expected rc=1 for startup port {arg!r}, got {res.returncode}"
            assert not (ws / "log" / "startup.pid").exists(), (
                f"startup.pid should not exist for invalid port {arg!r}"
            )
    finally:
        shutil.rmtree(ws, ignore_errors=True)


@test_case("T2-F18-05", 2, "F18", "bin/startup rejects missing bin/swrip or missing area/ cleanly", requires_server=False)
def test_t2_f18_05(ctx: E2EContext) -> None:
    ws = _safe_new_workspace(ctx, "f18-startup-missing", copy_binary=True)
    try:
        (ws / "bin" / "swrip").unlink()
        r_bin = _safe_run_subprocess(
            ["bash", str(ws / "bin" / "startup"), "17999", str(ws)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5.0,
            check=False,
        )
        assert r_bin.returncode == 1 and "not found" in (r_bin.stderr + r_bin.stdout).lower(), (
            f"Expected missing binary error, got rc={r_bin.returncode}: {r_bin.stderr!r}"
        )

        shutil.rmtree(ws / "area")
        r_area = _safe_run_subprocess(
            ["bash", str(ws / "bin" / "startup"), "17999", str(ws)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5.0,
            check=False,
        )
        assert r_area.returncode == 1 and "does not exist" in (r_area.stderr + r_area.stdout).lower(), (
            f"Expected missing area error, got rc={r_area.returncode}: {r_area.stderr!r}"
        )
    finally:
        shutil.rmtree(ws, ignore_errors=True)


@test_case("T2-F18-06", 2, "F18", "static_scanners const_and_format & command_skill_wiring post-remediation checks", requires_server=False)
def test_t2_f18_06(ctx: E2EContext) -> None:
    try:
        rep = run_all_scanners(
            ctx.repo_path,
            mode="post-remediation",
            checks=["const_and_format", "command_skill_wiring"],
        )
        mud_h = (ctx.repo_path / "src" / "mud.h").read_text(encoding="utf-8", errors="replace")
        cf = rep["scanners"]["const_and_format"]
        cw = rep["scanners"]["command_skill_wiring"]
        cf_summary = cf["summary"]
        cw_summary = cw["summary"]
        cf_passed = cf["passed"]
        cw_passed = cw["passed"]
        bug_fmt = bool(
            re.search(
                r"void\s+bug\s*(?:args\s*)?\([^;]*\)\s*__attribute__\s*\(\(\s*format\s*\(\s*printf",
                mud_h,
            )
        )
    except Exception as exc:
        raise AssertionError(f"Static scanner or src/mud.h check failed: {exc}") from exc
    assert "#ifndef MCCP" in mud_h, "Expected #ifndef MCCP guard in src/mud.h"
    assert bug_fmt is True, (
        "Expected __attribute__((format(printf,...))) on bug() in src/mud.h"
    )
    assert cf_passed is True, f"const_and_format post-remediation violations: {cf_summary}"
    assert cw_passed is True, f"command_skill_wiring post-remediation violations: {cw_summary}"
