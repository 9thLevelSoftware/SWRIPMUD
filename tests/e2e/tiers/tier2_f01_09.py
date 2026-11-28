"""Tier 2 (Boundary, Corner & Negative) E2E Tests for SWRIPMUD Features F01..F09 (45 test cases)."""

from __future__ import annotations

import os
import re
import socket
import struct
import time
from pathlib import Path

from tests.e2e.framework import E2EContext, test_case
from tests.e2e.static_scanners import strip_comments
from tests.e2e.telnet_client import (
    DEFAULT_PROMPT_RE,
    DONT,
    TELOPT_COMPRESS2,
    TELOPT_GMCP,
    TELOPT_LINEMODE,
    WILL,
    GmcpEvent,
    TelnetClient,
    strip_ansi,
)
from tests.e2e.wsl_env import SwripProcess, sha256_hash

IMM_NAME = "Immtest"
IMM_PASS = "E2eImmPass1"


# ---------------------------------------------------------------------------
# Shared Opaque-Box Test Helpers
# ---------------------------------------------------------------------------


def _assert_login(client: TelnetClient, name: str, password: str, timeout: float = 5.0) -> str:
    """Authenticate `name` through nanny, handling both fresh title/MOTD and Reconnecting."""
    transcript: list[str] = []
    try:
        transcript.append(
            client.read_until(r"(?:Please enter your name:|Name:)\s*$", timeout=timeout)
        )
        client.send_line(name)
        transcript.append(client.read_until(r"Password:\s*", timeout=timeout))
        client.send_line(password)

        stage = client.read_until(
            r"(?:Press enter\.\.\.|Welcome to Rise in Power !!|Press \[ENTER\]|Wrong password\.|Reconnecting\.|Already playing)",
            timeout=timeout,
        )
        transcript.append(stage)
        clean_stage = strip_ansi(stage)
        if "Wrong password." in clean_stage:
            raise AssertionError(f"Login failed for {name!r}: Wrong password")
        if "Reconnecting." in clean_stage or "Already playing" in clean_stage:
            if not DEFAULT_PROMPT_RE.search(clean_stage):
                transcript.append(client.read_until(DEFAULT_PROMPT_RE, timeout=timeout))
            return "".join(transcript)

        client.send_line("")
        stage2 = client.read_until(
            re.compile(r"(?:Press \[ENTER\]|Health:\s*\d+/\d+\s+Movement:\s*\d+/\d+\s*>)"),
            timeout=timeout,
        )
        transcript.append(stage2)
        if "Press [ENTER]" in strip_ansi(stage2):
            client.send_line("")
            transcript.append(client.read_until(DEFAULT_PROMPT_RE, timeout=timeout))
        return "".join(transcript)
    except (RuntimeError, TimeoutError) as exc:
        raise AssertionError(f"Expected login for {name!r} to succeed, but failed: {exc}") from exc


def _login_with_pre_options(
    client: TelnetClient,
    name: str,
    password: str,
    *,
    gmcp: bool = False,
    mccp2: bool = False,
    timeout: float = 5.0,
) -> str:
    """Read greeting Name prompt, negotiate pre-login options, then finish login."""
    transcript: list[str] = []
    transcript.append(
        _read_until_assert(
            client, r"(?:Please enter your name:|Name:)\s*$", timeout=timeout, desc="Name prompt"
        )
    )
    if gmcp:
        client.enable_gmcp()
    if mccp2:
        client.enable_mccp2(wait=False)
    client.send_line(name)
    transcript.append(_read_until_assert(client, r"Password:\s*", timeout=timeout, desc="Password prompt"))
    client.send_line(password)
    stage = _read_until_assert(
        client,
        r"(?:Press enter\.\.\.|Welcome to Rise in Power !!|Press \[ENTER\]|Wrong password\.)",
        timeout=timeout,
        desc="title screen",
    )
    transcript.append(stage)
    if "Wrong password." in strip_ansi(stage):
        raise AssertionError(f"Login failed for {name!r}: Wrong password")
    client.send_line("")
    stage2 = _read_until_assert(
        client,
        re.compile(r"(?:Press \[ENTER\]|Health:\s*\d+/\d+\s+Movement:\s*\d+/\d+\s*>)"),
        timeout=timeout,
        desc="MOTD or prompt",
    )
    transcript.append(stage2)
    if "Press [ENTER]" in strip_ansi(stage2):
        client.send_line("")
        transcript.append(_read_until_assert(client, DEFAULT_PROMPT_RE, timeout=timeout, desc="game prompt"))
    return "".join(transcript)


def _authorize_player(imm: TelnetClient, name: str) -> str:
    """Drain async Comm/Monitor new-char broadcasts on Immtest, then authorize `name`."""
    imm.drain(0.2)
    out = imm.cmd(f"authorize {name} yes", timeout=5.0, require_prompt=True)
    assert f"You have authorized {name}." in out, f"authorize {name} failed: {out!r}"
    return out


def _assert_create_character(
    client: TelnetClient,
    name: str,
    password: str,
    *,
    sex: str = "M",
    race: str = "Human",
    ability: str = "combat",
    ansi: str = "A",
    timeout: float = 8.0,
) -> str:
    try:
        return client.create_character(
            name,
            password,
            sex=sex,
            race=race,
            ability=ability,
            ansi=ansi,
            timeout=timeout,
        )
    except (RuntimeError, TimeoutError) as exc:
        raise AssertionError(f"Character creation failed for {name!r}: {exc}") from exc


def _read_until_assert(
    client: TelnetClient,
    pattern: str | re.Pattern[str],
    *,
    timeout: float = 5.0,
    desc: str = "expected output",
) -> str:
    try:
        return client.read_until(pattern, timeout=timeout)
    except TimeoutError as exc:
        raise AssertionError(f"Timed out/EOF waiting for {desc}: {exc}") from exc


def _goto_checked(client: TelnetClient, vnum: int, timeout: float = 5.0) -> str:
    client.drain(0.1)
    out = client.cmd(f"goto {vnum}", timeout=timeout, require_prompt=True)
    if (
        "Log:" in out
        and f"goto {vnum}" not in out
        and "obvious exits:" not in out.lower()
        and "no such location" not in out.lower()
    ):
        try:
            out += strip_ansi(client.read_until(DEFAULT_PROMPT_RE, timeout=timeout))
        except TimeoutError:
            pass
    lower = out.lower()
    if (
        "no such location" in lower
        or "does not exist" in lower
        or "cannot find that" in lower
        or "not a valid vnum" in lower
        or "swirling chaos" in lower
        or "step into a new reality" in lower
    ):
        raise AssertionError(f"Room #{vnum} is not loaded/reachable via goto: {out!r}")
    return out


def _clean_quit(client: TelnetClient | None) -> None:
    if client is None:
        return
    try:
        if client.sock is not None and not client.eof:
            try:
                client.send_line("quit")
            except OSError:
                pass
            deadline = time.monotonic() + 1.2
            while time.monotonic() < deadline and not client.eof:
                client.drain(0.1)
    except Exception:
        pass
    finally:
        client.close()


def _restore_immtest(
    imm: TelnetClient | None,
    *,
    goto_room: int = 100,
    restore_ansi: bool = False,
    disable_pager: bool = False,
    restore_title: bool = False,
    purge_items: tuple[str, ...] = (),
    respawn_chadoyn: bool = False,
    purge_rooms: tuple[int, ...] = (),
) -> None:
    if imm is None or imm.sock is None or imm.eof:
        return
    try:
        for item in purge_items:
            imm.cmd(f"drop {item}", timeout=5.0)
            imm.cmd(f"purge {item}", timeout=5.0)
        for rv in purge_rooms:
            out = imm.cmd(f"goto {rv}", timeout=5.0)
            if "no such location" not in out.lower():
                imm.cmd("purge", timeout=5.0)
        if restore_ansi:
            imm.cmd("config +ansi", timeout=5.0)
        if disable_pager:
            imm.cmd("config -pager", timeout=5.0)
        if restore_title:
            imm.cmd("title Immtest the Immortal", timeout=5.0)
        if respawn_chadoyn:
            out = imm.cmd("goto 10308", timeout=5.0)
            if "no such location" not in out.lower():
                imm.cmd("purge", timeout=5.0)
                imm.cmd("minvoke 10345", timeout=5.0)
        imm.cmd(f"goto {goto_room}", timeout=5.0)
    except Exception:
        pass


def _ensure_chadoyn_present(imm: TelnetClient) -> None:
    _goto_checked(imm, 10308)
    look_out = imm.cmd("look")
    # The room description names a Chadoyn even when the mobile is absent.
    # Its long description is emitted only for an actually present mobile.
    if "the chadoyn is straining its leash" not in look_out.lower():
        imm.cmd("minvoke 10345")


def _latest_gmcp(client: TelnetClient, module: str) -> GmcpEvent | None:
    matches = [ev for ev in client.gmcp_events if ev.module == module]
    return matches[-1] if matches else None


def _assert_all_gmcp_valid(client: TelnetClient) -> None:
    bad = [ev for ev in client.gmcp_events if not ev.json_valid]
    assert not bad, f"Invalid RFC 8259 GMCP JSON events: {bad[:3]}"


def _shutdown_and_assert_clean(ctx: E2EContext, proc: SwripProcess) -> None:
    metrics = proc.shutdown_clean(IMM_NAME, IMM_PASS, timeout=15.0)
    ctx.assert_clean_sanitizers(metrics)
    assert metrics.clean_exit is True, (
        f"Expected clean_exit=True, got exit_code={metrics.exit_code}, "
        f"normal_term={metrics.normal_termination}, fallback_kill={metrics.used_fallback_kill}"
    )


def _safe_stop_proc(proc: SwripProcess | None) -> None:
    if proc is not None and proc.is_running():
        try:
            proc.shutdown_clean()
        except Exception:
            pass


# ===========================================================================
# Feature F01: Allocators & Interned Strings (Tier 2)
# ===========================================================================


@test_case(
    "T2-F01-01",
    2,
    "F01",
    "Wrong-password disconnect cleanup (0 descriptor/char/string leaks)",
    requires_server=True,
    fresh_server=True,
)
def test_t2_f01_01(ctx: E2EContext) -> None:
    proc = ctx.start_fresh_server("f01_badpass", ready_timeout=15.0)
    clients: list[TelnetClient] = []
    try:
        for _ in range(3):
            c = ctx.make_client(port=proc.port, timeout=5.0)
            clients.append(c)
            _read_until_assert(c, r"(?:Please enter your name:|Name:)\s*$", desc="Name prompt")
            c.send_line(IMM_NAME)
            _read_until_assert(c, r"Password:\s*", desc="Password prompt")
            c.send_line("WrongPass999")
            out = _read_until_assert(c, r"Wrong password\.", timeout=5.0, desc="Wrong password")
            assert "Wrong password." in strip_ansi(out)
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline and not c.eof:
                c.drain(0.15)
            assert c.eof is True, "Expected server to close socket after Wrong password"
            c.close()
        clients.clear()

        _shutdown_and_assert_clean(ctx, proc)
    finally:
        for c in clients:
            c.close()
        _safe_stop_proc(proc)


@test_case(
    "T2-F01-03",
    2,
    "F01",
    "Double-edit, re-entry rejection, and oedit+redit2 abort/save/disconnect-mid-OLC stress",
    requires_server=True,
    fresh_server=True,
)
def test_t2_f01_03(ctx: E2EContext) -> None:
    proc = ctx.start_fresh_server("f01_double_edit", ready_timeout=15.0)
    c: TelnetClient | None = None
    try:
        c = ctx.make_client(port=proc.port, timeout=5.0)
        _assert_login(c, IMM_NAME, IMM_PASS)

        assert "Ok." in c.cmd("oinvoke 10311")
        out1 = c.cmd("oedit glowrod")
        assert "You are not editing anything." not in out1, f"Spurious E2E-008 message: {out1!r}"
        assert "Object inline editing mode enabled." in out1, f"oedit failed: {out1!r}"

        out2 = c.cmd("oedit glowrod")
        assert "Unknown OLC command" in out2, (
            f"Unexpected nested oedit response: {out2!r}"
        )

        c.cmd("short draft one")
        c.cmd("commit")
        c.cmd("short draft two")
        c.cmd("revert")
        abort_out = c.cmd("stop abort")
        assert "Changes discarded." in abort_out, f"stop abort failed: {abort_out!r}"

        out3 = c.cmd("oedit glowrod")
        assert "Object inline editing mode enabled." in out3
        c.cmd("short final glowrod")
        save_out = c.cmd("stop save")
        assert "Changes saved." in save_out
        assert "final glowrod" in c.cmd("ostat glowrod")

        c.cmd("redit2 start")
        c.cmd("name Temp")
        c.cmd("commit")
        # Disconnect abruptly mid-OLC without stop save/abort
        c.close()
        c = None

        time.sleep(0.2)
        _shutdown_and_assert_clean(ctx, proc)
    finally:
        if c is not None:
            c.close()
        _safe_stop_proc(proc)


@test_case(
    "T2-F01-05",
    2,
    "F01",
    "Repeated interned string alloc/free churn + oset/oinvoke/purge LSan stress",
    requires_server=True,
    fresh_server=True,
)
def test_t2_f01_05(ctx: E2EContext) -> None:
    proc = ctx.start_fresh_server("f01_hash_churn", ready_timeout=15.0)
    c: TelnetClient | None = None
    try:
        c = ctx.make_client(port=proc.port, timeout=5.0)
        _assert_login(c, IMM_NAME, IMM_PASS)

        for i in range(6):
            assert "Ok." in c.cmd("oinvoke 10311")
            c.cmd(f"oset glowrod short churn_str_{i}")
            c.cmd(f"oset glowrod name churnkey{i}")
            c.cmd(f"drop churnkey{i}")
            c.cmd(f"purge churnkey{i}")

        assert "Ok." in c.cmd("oinvoke 10312")
        c.cmd("oset vibro short x")
        c.cmd("oset vibro name rename")
        c.cmd("oset rename short y")
        c.cmd("drop rename")
        c.cmd("purge rename")

        _clean_quit(c)
        c = None
        _shutdown_and_assert_clean(ctx, proc)
    finally:
        _clean_quit(c)
        _safe_stop_proc(proc)


# ===========================================================================
# Feature F02: BitSet / FLAG_SET & Flag Tables (Tier 2)
# ===========================================================================


@test_case(
    "T2-F02-01",
    2,
    "F02",
    "oset invalid/negative flag rejection (flags, objflags, wear) without UB or bit corruption",
    requires_server=True,
    fresh_server=False,
)
def test_t2_f02_01(ctx: E2EContext) -> None:
    c: TelnetClient | None = None
    try:
        c = ctx.make_client()
        _assert_login(c, IMM_NAME, IMM_PASS)

        assert "Ok." in c.cmd("oinvoke 10311")
        before = c.cmd("ostat glowrod")
        b_oflags = next((ln for ln in before.splitlines() if "Object Flags:" in ln), "")
        b_wflags = next((ln for ln in before.splitlines() if "Wear flags" in ln), "")
        assert b_oflags and b_wflags, f"Missing flag headers in initial ostat output: {before!r}"

        out1 = c.cmd("oset glowrod flags nonexistent_flag_xyz")
        assert "Unknown flag: nonexistent_flag_xyz" in out1, f"Expected unknown flag error: {out1!r}"

        out2 = c.cmd("oset glowrod wear bad_wear_slot")
        assert "Unknown flag: bad_wear_slot" in out2, f"Expected unknown wear flag error: {out2!r}"

        c.cmd("oset glowrod objflags nonexistent_objflag")

        after = c.cmd("ostat glowrod")
        a_oflags = next((ln for ln in after.splitlines() if "Object Flags:" in ln), "")
        a_wflags = next((ln for ln in after.splitlines() if "Wear flags" in ln), "")
        assert a_oflags and a_wflags, f"Missing flag headers in post-oset ostat output: {after!r}"
        assert a_oflags == b_oflags and a_wflags == b_wflags, (
            f"Flags mutated on invalid input: before=({b_oflags}, {b_wflags}) after=({a_oflags}, {a_wflags})"
        )
        ctx.assert_clean_sanitizers(ctx.shared_server)
    finally:
        _restore_immtest(c, purge_items=("glowrod",))
        _clean_quit(c)


@test_case(
    "T2-F02-02",
    2,
    "F02",
    "rset rejects invalid room flag names and non-numeric sectors without corruption",
    requires_server=True,
    fresh_server=False,
)
def test_t2_f02_02(ctx: E2EContext) -> None:
    c: TelnetClient | None = None
    try:
        c = ctx.make_client()
        _assert_login(c, IMM_NAME, IMM_PASS)
        _goto_checked(c, 102)

        before = c.cmd("rstat 102")
        b_rflags = next((ln for ln in before.splitlines() if "Room flags:" in ln), "")
        assert b_rflags, f"Missing 'Room flags:' header in initial rstat output: {before!r}"

        out1 = c.cmd("rset 102 sector notanumber")
        assert "Value must be numeric." in out1, f"Expected numeric error: {out1!r}"

        unknown_flag = c.cmd("rset 102 flags not_a_room_flag")
        assert "Unknown flag: not_a_room_flag" in unknown_flag, (
            f"Expected deterministic unknown room flag rejection: {unknown_flag!r}"
        )

        for invalid_flag in ("-1", "10000", "999999"):
            result = c.cmd(f"rset 102 flags {invalid_flag}")
            assert f"Unknown flag: {invalid_flag}" in result, (
                f"Expected deterministic unknown room flag rejection for {invalid_flag}: {result!r}"
            )

        after = c.cmd("rstat 102")
        a_rflags = next((ln for ln in after.splitlines() if "Room flags:" in ln), "")
        assert a_rflags, f"Missing 'Room flags:' header in post-rset rstat output: {after!r}"
        assert a_rflags == b_rflags, f"Room flags changed on OOB/negative rset: {b_rflags!r} vs {a_rflags!r}"
        ctx.assert_clean_sanitizers(ctx.shared_server)
    finally:
        _restore_immtest(c)
        _clean_quit(c)


@test_case(
    "T2-F02-03",
    2,
    "F02",
    "High-bit BV31 (ROOM_AUCTION bit 31) and >=32 bit set/toggle/clear without signed-shift UBSan trap",
    requires_server=True,
    fresh_server=False,
)
def test_t2_f02_03(ctx: E2EContext) -> None:
    c: TelnetClient | None = None
    try:
        c = ctx.make_client()
        _assert_login(c, IMM_NAME, IMM_PASS)
        _goto_checked(c, 102)

        c.cmd("rset 102 flags auction")
        r1 = c.cmd("rstat 102")
        rline1 = next((ln for ln in r1.splitlines() if "Room flags:" in ln), "")
        assert rline1, f"Missing 'Room flags:' header in rstat output: {r1!r}"
        assert "auction" in rline1.lower(), f"Expected ROOM_AUCTION bit 31 set: {r1!r}"

        c.cmd("redit2 start")
        c.cmd("flags -auction")
        c.cmd("stop save")
        r2 = c.cmd("rstat 102")
        rline2 = next((ln for ln in r2.splitlines() if "Room flags:" in ln), "")
        assert rline2, f"Missing 'Room flags:' header in post-clear rstat output: {r2!r}"
        assert "auction" not in rline2.lower(), f"Expected auction cleared: {rline2!r}"

        assert "Ok." in c.cmd("oinvoke 10311")
        c.cmd("oedit glowrod")
        c.cmd("flags +LightningBlade")
        c.cmd("stop save")
        oout = c.cmd("ostat glowrod")
        assert "lightningblade" in oout.lower(), f"Expected bit 39 LightningBlade set: {oout!r}"

        ctx.assert_clean_sanitizers(ctx.shared_server)
    finally:
        if c is not None and c.sock is not None and not c.eof:
            try:
                c.cmd("goto 102", timeout=5.0)
                if "auction" in c.cmd("rstat 102", timeout=5.0).lower():
                    c.cmd("redit2 start", timeout=5.0)
                    c.cmd("flags -auction", timeout=5.0)
                    c.cmd("stop save", timeout=5.0)
            except Exception:
                pass
        _restore_immtest(c, purge_items=("glowrod",))
        _clean_quit(c)


@test_case(
    "T2-F02-04",
    2,
    "F02",
    "IN_ARENA & ROOM_ARENA boundary check (arena/bet outside arena & in ROOM_ARENA room 29)",
    requires_server=True,
    fresh_server=False,
)
def test_t2_f02_04(ctx: E2EContext) -> None:
    c: TelnetClient | None = None
    try:
        c = ctx.make_client()
        _assert_login(c, IMM_NAME, IMM_PASS)

        out1 = c.cmd("arena")
        assert "The killing fields are closed right now." in out1, f"Unexpected arena output: {out1!r}"

        out2 = c.cmd("bet Immtest 100")
        assert "Betting is closed until the next arena opens." in out2, (
            f"Unexpected bet output: {out2!r}"
        )

        _goto_checked(c, 29)
        r29 = c.cmd("rstat 29")
        assert "arena" in r29.lower(), f"Expected ROOM_ARENA on room 29: {r29!r}"

        out3 = c.cmd("bet Immtest 100")
        assert any(
            kw in out3.lower() for kw in ("arena players can not make bets", "closed", "no arena")
        ), f"Unexpected bet inside arena: {out3!r}"
        ctx.assert_clean_sanitizers(ctx.shared_server)
    finally:
        _restore_immtest(c)
        _clean_quit(c)


@test_case(
    "T2-F02-05",
    2,
    "F02",
    "track across ROOM_NO_MOB with empty/nonexistent/same-room targets + BitSet preservation",
    requires_server=True,
    fresh_server=True,
)
def test_t2_f02_05(ctx: E2EContext) -> None:
    # The Academy reset places the Chadoyn in 10308. Other cases on the
    # shared server edit or kill that mobile.
    proc = ctx.start_fresh_server("f02_track")
    c: TelnetClient | None = None
    try:
        c = ctx.make_client(port=proc.port)
        _assert_login(c, IMM_NAME, IMM_PASS)
        _goto_checked(c, 10308)
        look = c.cmd("look")
        assert "the chadoyn is straining its leash" in look.lower(), (
            f"Academy reset did not place the Chadoyn in room 10308: {look!r}"
        )

        c.cmd("sset Immtest track 100")
        _goto_checked(c, 10300)

        out_none = c.cmd("track")
        assert "Whom are you trying to track?" in out_none

        out_miss = c.cmd("track ZzzNoSuchTargetNonexist")
        assert "You can't sense a trail from here." in out_miss

        out_self = c.cmd("track Immtest")
        assert "You're already in the same room!" in out_self

        out_cross = c.cmd("track chadoyn")
        assert "You sense a trail north from here" in out_cross, (
            f"Multi-hop BFS across no_mob rooms failed: {out_cross!r}"
        )

        r_pat = re.compile(r"Room flags:.*Health:\s*\d+/\d+\s+Movement:\s*\d+/\d+\s*>", re.DOTALL)
        assert "no_mob" in c.cmd("rstat 10300", timeout=5.0, prompt_pattern=r_pat).lower()
        assert "no_mob" in c.cmd("rstat 10316", timeout=5.0, prompt_pattern=r_pat).lower()
    finally:
        _clean_quit(c)
    _shutdown_and_assert_clean(ctx, proc)


def _ensure_vendor_ownership_guards(ctx: E2EContext) -> None:
    source = (ctx.repo_path / "src" / "shops.c").read_text(encoding="utf-8", errors="replace")
    for command in ("do_pricevendor", "do_collectgold"):
        match = re.search(
            rf"void\s+{command}\s*\([^)]*\)\s*\{{(?P<body>.*?)(?=\nvoid\s+|\Z)",
            source,
            re.DOTALL,
        )
        assert match, f"Could not locate {command} in shops.c"
        guard = re.search(
            r"if\s*\(\s*!vendor->owner\s*\|\|\s*str_cmp\(ch->name,\s*vendor->owner\)\s*\)\s*\{\s*"
            r"send_to_char\(\s*\"This isn't your vendor!\\n\"\s*,\s*ch\s*\)\s*;\s*return\s*;\s*\}",
            match.group("body"),
            re.DOTALL,
        )
        assert guard, f"{command} must return before changing another owner's vendor"


# ===========================================================================
# Feature F03: WILDERNESS_* Removal & GameContext Reentrancy (Tier 2)
# ===========================================================================


@test_case(
    "T2-F03-01",
    2,
    "F03",
    "Retired wilderness runtime scaffolding with stable persisted flag values (AUD-102, R3)",
    requires_server=False,
    fresh_server=False,
)
def test_t2_f03_01(ctx: E2EContext) -> None:
    dead_symbols = (
        "WILDERNESS_SYSTEM",
        "WILDERNESS_DATA",
        "WILD_DELTA_DATA",
        "WILD_SITE_PROTO",
        "WILD_SITE_DATA",
        "WILD_ROOM_CACHE_DATA",
        "WILD_ACTOR_DATA",
        "WILD_VNUM_ALLOC_DATA",
        "wild_cache",
        "is_wilderness",
        "wild_x",
        "wild_y",
        "wild_z",
        "room_is_wilderness",
    )
    src_dir = ctx.repo_path / "src"
    sym_re = re.compile(r"\b(" + "|".join(re.escape(s) for s in dead_symbols) + r")\b")
    wild_symbols_found: list[str] = []
    for p in sorted(list(src_dir.glob("*.h")) + list(src_dir.glob("*.c"))):
        txt = strip_comments(p.read_text(encoding="utf-8", errors="replace"))
        for m in sym_re.finditer(txt):
            wild_symbols_found.append(f"{p.name}:{m.group(1)}")

    cmds_dat = (ctx.repo_path / "system" / "commands.dat").read_text(encoding="utf-8", errors="replace")
    wild_commands_found = re.findall(r"^Name\s+(wild\S*)~", cmds_dat, re.MULTILINE | re.IGNORECASE)

    assert wild_symbols_found == [], f"Dead WILDERNESS_* symbols remain in src/: {wild_symbols_found[:15]}"
    assert wild_commands_found == [], f"Dead wild* commands remain in commands.dat: {wild_commands_found}"

    mud_header = (src_dir / "mud.h").read_text(encoding="utf-8", errors="replace")
    reserved_room_bits = {
        "ROOM_WILDERNESS": 32,
        "ROOM_WILD_TEMP": 33,
        "ROOM_WILD_SITE_EDGE": 34,
        "ROOM_WILD_TRANSITION": 35,
        "ROOM_MAX": 36,
    }
    for name, value in reserved_room_bits.items():
        assert re.search(rf"\b{name}\s*=\s*{value}\b", mud_header), (
            f"Persisted room flag index changed for {name}; expected reserved value {value}"
        )
    assert re.search(r"^#define\s+EX_WILDERNESS\s+BV27\b", mud_header, re.MULTILINE), (
        "Persisted wilderness exit flag bit must remain reserved at BV27"
    )


@test_case(
    "T2-F03-02",
    2,
    "F03",
    "Dead wilderness commands and flags rejected cleanly in-game",
    requires_server=True,
    fresh_server=False,
)
def test_t2_f03_02(ctx: E2EContext) -> None:
    c: TelnetClient | None = None
    try:
        c = ctx.make_client()
        _assert_login(c, IMM_NAME, IMM_PASS)

        assert "Huh?" in c.cmd("wild")
        assert "Huh?" in c.cmd("wilderness")
        retired_flag = c.cmd("rset 100 flags wilderness")
        assert "Unknown flag: wilderness" in retired_flag, (
            f"Expected retired room flag rejection: {retired_flag!r}"
        )

        c.cmd("redit2 start")
        olc_flag = c.cmd("flags +wilderness")
        assert "Invalid flag value." in olc_flag, (
            f"Expected OLC schema to reject retired wilderness flag: {olc_flag!r}"
        )
        c.cmd("stop abort")

        r100 = c.cmd("rstat 100")
        rline = next((ln for ln in r100.splitlines() if "Room flags:" in ln), "")
        assert rline, f"Missing 'Room flags:' header in rstat 100 output: {r100!r}"
        assert "wilderness" not in rline.lower(), f"wilderness flag should not exist: {rline!r}"
    finally:
        _restore_immtest(c)
        _clean_quit(c)


@test_case(
    "T2-F03-03",
    2,
    "F03",
    "Reentrant file_ver & save_equipment isolation across Version 1 vs Version 3 corpses/storerooms",
    requires_server=True,
    fresh_server=True,
)
def test_t2_f03_03(ctx: E2EContext) -> None:
    proc = ctx.start_fresh_server("f03_reentrant_ver")
    proc2: SwripProcess | None = None
    c: TelnetClient | None = None
    v1c: TelnetClient | None = None
    c2: TelnetClient | None = None
    try:
        ws = proc.work_dir
        c = ctx.make_client(port=proc.port)
        _assert_login(c, IMM_NAME, IMM_PASS)

        # Room #122 has ROOM_CLANSTOREROOM; store nested container + weapon inside storeroom/122
        _goto_checked(c, 122)
        assert "Ok." in c.cmd("oinvoke 10317", require_prompt=True)
        assert "Ok." in c.cmd("oinvoke 10312", require_prompt=True)
        c.cmd("put vibro backpack", require_prompt=True)
        c.cmd("drop backpack", require_prompt=True)
        assert "Ok." in c.cmd("save", require_prompt=True)
        _clean_quit(c)
        c = None
        _shutdown_and_assert_clean(ctx, proc)

        # Seed a Version 1 pfile (Verone) and a corpse (Testcorpse) before reboot
        v_dir = ws / "player" / "v"
        v_dir.mkdir(parents=True, exist_ok=True)
        verone_pwd = sha256_hash("VeronePass1")
        (v_dir / "Verone").write_text(
            "\n".join(
                [
                    "#PLAYER",
                    "Version      1",
                    "Name         Verone~",
                    "Sex          1",
                    "Race         0",
                    "MainAbility  0",
                    "Languages    -1 1",
                    "Toplevel     5",
                    "Played       0",
                    "Room         100",
                    "HpManaMove   500 500 0 0 500 500",
                    "Gold         100",
                    "Position     8",
                    f"Password     {verone_pwd}~",
                    "Title        Verone the Traveler~",
                    "AuthedBy     System~",
                    "FlagsEx      -1",
                    "AttrPerm     12 12 12 12 12 12 12",
                    "AttrMod      0 0 0 0 0 0 0",
                    "Condition    0 48 48 10",
                    "Tongue       100 'common'",
                    "End",
                    "",
                    "#OBJECT",
                    "Nest         0",
                    "WearLoc      3",
                    "Vnum         10308",
                    "End",
                    "",
                    "#END",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        c_dir = ws / "corpses"
        c_dir.mkdir(parents=True, exist_ok=True)
        (c_dir / "Testcorpse").write_text(
            "\n".join(
                [
                    "#CORPSE",
                    "Nest         0",
                    "Name         corpse Testcorpse~",
                    "ShortDescr   the corpse of Testcorpse~",
                    "Description  The corpse of Testcorpse lies here.~",
                    "Vnum         11",
                    "Room         100",
                    "Timer        10",
                    "End",
                    "",
                    "#OBJECT",
                    "Nest         1",
                    "Vnum         10311",
                    "End",
                    "",
                    "#END",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        proc2 = SwripProcess(ws)
        proc2.start()
        ctx._processes.append(proc2)
        bm2 = proc2.wait_ready(15.0)
        assert bm2.ready is True, f"Reboot failed: {bm2.log_tail}"

        v1c = ctx.make_client(port=proc2.port)
        _assert_login(v1c, "Verone", "VeronePass1")
        assert "Verone" in v1c.cmd("score")

        c2 = ctx.make_client(port=proc2.port)
        _assert_login(c2, IMM_NAME, IMM_PASS)
        eq2 = c2.cmd("eq")
        assert "Nothing." in eq2.splitlines(), f"Leaked save_equipment onto Immtest: {eq2!r}"

        look_100 = c2.cmd("look")
        assert "corpse of Testcorpse" in look_100, f"Testcorpse missing in room 100: {look_100!r}"

        _goto_checked(c2, 122)
        look_122 = c2.cmd("look", require_prompt=True)
        assert "backpack" in look_122.lower(), f"Backpack missing from storeroom 122: {look_122!r}"
        c2.cmd("get backpack", require_prompt=True)
        exa_out = c2.cmd("exa backpack", require_prompt=True)
        assert "vibro-blade" in exa_out.lower(), f"Nested vibro-blade lost from backpack: {exa_out!r}"

        _clean_quit(v1c)
        v1c = None
        _clean_quit(c2)
        c2 = None
        _shutdown_and_assert_clean(ctx, proc2)
    finally:
        _clean_quit(c)
        _clean_quit(v1c)
        _clean_quit(c2)
        _safe_stop_proc(proc)
        _safe_stop_proc(proc2)


@test_case(
    "T2-F03-04",
    2,
    "F03",
    "Two concurrent SwripProcess instances (GameContext isolation on distinct workspaces/ports)",
    requires_server=True,
    fresh_server=True,
)
def test_t2_f03_04(ctx: E2EContext) -> None:
    proc_a = ctx.start_fresh_server("f03_ctx_a")
    proc_b = ctx.start_fresh_server("f03_ctx_b")
    imm_a: TelnetClient | None = None
    imm_b: TelnetClient | None = None
    plr_a: TelnetClient | None = None
    plr_b: TelnetClient | None = None
    try:
        assert proc_a.port != proc_b.port
        assert proc_a.work_dir != proc_b.work_dir

        imm_a = ctx.make_client(port=proc_a.port)
        _assert_login(imm_a, IMM_NAME, IMM_PASS)
        imm_b = ctx.make_client(port=proc_b.port)
        _assert_login(imm_b, IMM_NAME, IMM_PASS)

        plr_a = ctx.make_client(port=proc_a.port)
        _assert_create_character(plr_a, "Instalpha", "InstPassA")
        _authorize_player(imm_a, "Instalpha")
        plr_b = ctx.make_client(port=proc_b.port)
        _assert_create_character(plr_b, "Instbeta", "InstPassB")
        _authorize_player(imm_b, "Instbeta")
        plr_a.drain(0.2)
        plr_b.drain(0.2)
        assert "Ok." in plr_a.cmd("save")
        assert "Ok." in plr_b.cmd("save")

        who_a = imm_a.cmd("who")
        who_b = imm_b.cmd("who")
        assert "Instalpha" in who_a and "Instbeta" not in who_a
        assert "Instbeta" in who_b and "Instalpha" not in who_b

        assert (proc_a.work_dir / "player" / "i" / "Instalpha").is_file()
        assert not (proc_a.work_dir / "player" / "i" / "Instbeta").exists()
        assert (proc_b.work_dir / "player" / "i" / "Instbeta").is_file()

        _clean_quit(plr_a)
        plr_a = None
        _clean_quit(plr_b)
        plr_b = None
        _clean_quit(imm_a)
        imm_a = None
        _clean_quit(imm_b)
        imm_b = None

        _shutdown_and_assert_clean(ctx, proc_a)
        _shutdown_and_assert_clean(ctx, proc_b)
    finally:
        _clean_quit(plr_a)
        _clean_quit(plr_b)
        _clean_quit(imm_a)
        _clean_quit(imm_b)
        _safe_stop_proc(proc_a)
        _safe_stop_proc(proc_b)


@test_case(
    "T2-F03-05",
    2,
    "F03",
    "4-client concurrent save/storeroom interleaving and clean shutdown soak",
    requires_server=True,
    fresh_server=True,
)
def test_t2_f03_05(ctx: E2EContext) -> None:
    proc = ctx.start_fresh_server("f03_concurrent_soak")
    imm: TelnetClient | None = None
    soak_clients: list[TelnetClient] = []
    try:
        imm = ctx.make_client(port=proc.port)
        _assert_login(imm, IMM_NAME, IMM_PASS)
        _goto_checked(imm, 122)

        names = [("Soakone", "SoakPass1"), ("Soaktwo", "SoakPass2"), ("Soakthree", "SoakPass3"), ("Soakfour", "SoakPass4")]
        for name, pwd in names:
            cli = ctx.make_client(port=proc.port)
            soak_clients.append(cli)
            _assert_create_character(cli, name, pwd)
            _authorize_player(imm, name)
            cli.drain(0.15)

        for _ in range(3):
            for cli in soak_clients:
                cli.send_line("save")
                cli.send_line("who")
            assert "Ok." in imm.cmd("oinvoke 10311", timeout=5.0, require_prompt=True)
            imm.cmd("drop glowrod", timeout=5.0, require_prompt=True)
            assert "Ok." in imm.cmd("save", timeout=5.0, require_prompt=True)
            imm.cmd("get glowrod", timeout=5.0, require_prompt=True)
            imm.cmd("drop glowrod", timeout=5.0, require_prompt=True)
            imm.cmd("purge glowrod", timeout=5.0, require_prompt=True)
            for cli in soak_clients:
                _read_until_assert(cli, r"Ok\.", timeout=5.0, desc="soak save Ok.")
                cli.drain(0.1)

        for cli in soak_clients:
            _clean_quit(cli)
        soak_clients.clear()

        for name, pwd in names[:2]:
            rc = ctx.make_client(port=proc.port)
            soak_clients.append(rc)
            _assert_login(rc, name, pwd)
            assert name in rc.cmd("score")
            _clean_quit(rc)
        soak_clients.clear()

        _clean_quit(imm)
        imm = None
        _shutdown_and_assert_clean(ctx, proc)
    finally:
        for cli in soak_clients:
            _clean_quit(cli)
        _clean_quit(imm)
        _safe_stop_proc(proc)


# ===========================================================================
# Feature F04: TCP Socket Loop, nanny Auth, DES->SHA-256 & id.c (Tier 2)
# ===========================================================================


@test_case(
    "T2-F04-01",
    2,
    "F04",
    "Wrong password rejects login and closes descriptor cleanly",
    requires_server=True,
    fresh_server=False,
)
def test_t2_f04_01(ctx: E2EContext) -> None:
    c: TelnetClient | None = None
    try:
        c = ctx.make_client()
        _read_until_assert(c, r"(?:Please enter your name:|Name:)\s*$", desc="Name prompt")
        c.send_line(IMM_NAME)
        _read_until_assert(c, r"Password:\s*", desc="Password prompt")
        c.send_line("DefinitelyWrongPass")
        msg = _read_until_assert(c, r"Wrong password\.", timeout=5.0, desc="Wrong password")
        assert "Wrong password." in strip_ansi(msg)
        deadline = time.monotonic() + 1.5
        while time.monotonic() < deadline and not c.eof:
            c.drain(0.15)
        assert c.eof is True, "Server did not close socket after Wrong password"
    finally:
        if c is not None:
            c.close()


@test_case(
    "T2-F04-02",
    2,
    "F04",
    "check_parse_name and badnames.lst reject invalid names at boundaries",
    requires_server=True,
    fresh_server=False,
)
def test_t2_f04_02(ctx: E2EContext) -> None:
    c: TelnetClient | None = None
    try:
        c = ctx.make_client()
        _read_until_assert(c, r"(?:Please enter your name:|Name:)\s*$", desc="Name prompt")

        for bad in ("ab", "abcdefghijklmnopqrstu", "Imm2e", "Illil", "god"):
            c.send_line(bad)
            _read_until_assert(
                c, r"Illegal name, try another\.\s*Name:\s*", timeout=5.0, desc=f"Illegal name for {bad!r}"
            )

        c.send_line("Vader")
        _read_until_assert(
            c, r"That name is unacceptable, please choose another\.\s*Name:\s*", timeout=5.0, desc="badnames reject"
        )

        c.send_line("Qqx")
        _read_until_assert(c, r"Did I get that right, Qqx \(Y/N\)\?", timeout=5.0, desc="3-char valid confirm")
        c.send_line("N")
        _read_until_assert(c, r"Ok, what IS it, then\?", timeout=5.0, desc="re-prompt on N")
    finally:
        if c is not None:
            c.close()


# ===========================================================================
# Feature F05: Telnet FSM, MCCP2, UTF-8/Color, Pager & MSSP (Tier 2)
# ===========================================================================


@test_case(
    "T2-F05-02",
    2,
    "F05",
    "Early-connect MCCP2 negotiation activates on CON_PLAYING and compress command toggles cleanly",
    requires_server=True,
    fresh_server=False,
)
def test_t2_f05_02(ctx: E2EContext) -> None:
    c: TelnetClient | None = None
    try:
        c = ctx.make_client(timeout=5.0)
        try:
            _login_with_pre_options(c, IMM_NAME, IMM_PASS, mccp2=True)
            c.drain(0.3)
            assert c.mccp2_active is True, "MCCP2 did not auto-start on CON_PLAYING"

            off_out = c.cmd("compress", timeout=5.0)
            c.drain(0.3)
            assert "Terminating compression." in off_out, f"Unexpected compress off output: {off_out!r}"
            assert c.mccp2_active is False

            ev_idx = len(c.iac_events)
            on_out = c.cmd("compress", timeout=5.0)
            c.drain(0.3)
        except RuntimeError as exc:
            raise AssertionError(f"Server emitted invalid MCCP2 zlib stream: {exc}") from exc
        assert "Initiating compression." in on_out, f"Unexpected compress on output: {on_out!r}"
        assert (WILL, TELOPT_COMPRESS2) in c.iac_events[ev_idx:], (
            f"compress command failed to emit IAC WILL COMPRESS2: {c.iac_events[ev_idx:]}"
        )
    finally:
        _clean_quit(c)


@test_case(
    "T2-F05-03",
    2,
    "F05",
    "UTF-8 backspace/DEL erases whole runes and invalid UTF-8 bytes do not drop trailing text",
    requires_server=True,
    fresh_server=False,
)
def test_t2_f05_03(ctx: E2EContext) -> None:
    c: TelnetClient | None = None
    try:
        c = ctx.make_client()
        _assert_login(c, IMM_NAME, IMM_PASS)

        cur1 = len(c.all_text)
        c.send_raw(b"say hi" + "本".encode("utf-8") + b"\x08!\r\n")
        _read_until_assert(c, DEFAULT_PROMPT_RE, timeout=5.0, desc="UTF-8 backspace prompt")
        slice1 = c.all_text[cur1:]
        assert "hi!" in strip_ansi(slice1) and "\ufffd" not in slice1, f"Rune backspace failed: {slice1!r}"

        cur2 = len(c.all_text)
        c.send_raw(b"say ok" + "🚀".encode("utf-8") + b"\x7fend\r\n")
        _read_until_assert(c, DEFAULT_PROMPT_RE, timeout=5.0, desc="UTF-8 DEL prompt")
        slice2 = c.all_text[cur2:]
        assert "okend" in strip_ansi(slice2) and "\ufffd" not in slice2, f"Rune DEL failed: {slice2!r}"

        cur3 = len(c.all_text)
        c.send_raw(b"say pre\xfe\x80post\r\n")
        _read_until_assert(c, DEFAULT_PROMPT_RE, timeout=5.0, desc="invalid UTF-8 prompt")
        slice3 = strip_ansi(c.all_text[cur3:])
        assert "post" in slice3, f"utf8_safe_len truncated trailing text after 0xFE: {slice3!r}"
    finally:
        _clean_quit(c)


@test_case(
    "T2-F05-04",
    2,
    "F05",
    "Extended color codes (&z/&p/&P/&O) and ^ background wrapping render SGR intact",
    requires_server=True,
    fresh_server=False,
)
def test_t2_f05_04(ctx: E2EContext) -> None:
    c: TelnetClient | None = None
    try:
        c = ctx.make_client(naws_size=(40, 24))
        _assert_login(c, IMM_NAME, IMM_PASS)

        c.cmd("config +ansi")
        cur = len(c.all_text)
        out = c.cmd("say &zGrey &pPurp &PBrPurp &OOrng ^gGreenBg alphabravo charliedelta&x")
        raw = c.all_text[cur:]

        assert "Grey" in out and "Purp" in out and "BrPurp" in out and "Orng" in out
        assert "alphabravo" in out and "charliedelta" in out, f"wrap_text_ex dropped trailing word: {out!r}"

        for code_word, sgr_hint in (("Grey", "30"), ("Purp", "35"), ("Orng", "33")):
            seg_m = re.search(rf"(\x1b\[[0-9;]*m)[^\x1b]*{code_word}", raw)
            assert seg_m is not None and sgr_hint in seg_m.group(1), (
                f"Missing extended color SGR ({sgr_hint}m) before {code_word}: {raw!r}"
            )
    finally:
        _restore_immtest(c, restore_ansi=True)
        _clean_quit(c)


@test_case(
    "T2-F05-05",
    2,
    "F05",
    "Pager refresh/back/quit edge cases and missing help topic recovery",
    requires_server=True,
    fresh_server=False,
)
def test_t2_f05_05(ctx: E2EContext) -> None:
    c: TelnetClient | None = None
    try:
        c = ctx.make_client()
        _assert_login(c, IMM_NAME, IMM_PASS)
        c.cmd("config +pager")

        miss = c.cmd("help zzznonexistenttopic")
        assert "No help on 'zzznonexistenttopic' found." in miss

        c.send_line("help prompt")
        _read_until_assert(c, r"\(C\)ontinue.*\[C\]", timeout=5.0, desc="pager initial")

        c.send_line("r")
        _read_until_assert(c, r"\(C\)ontinue.*\[C\]", timeout=5.0, desc="pager refresh")

        c.send_line("b")
        _read_until_assert(c, r"\(C\)ontinue.*\[C\]", timeout=5.0, desc="pager back")

        c.send_line("q")
        q_out = _read_until_assert(c, DEFAULT_PROMPT_RE, timeout=5.0, desc="pager quit")
        q_plain = strip_ansi(q_out)
        assert len(q_plain) < 250 and "See HELP COLORS" not in q_plain and "Immortal:" not in q_plain, (
            f"Pager quit 'q' failed to suppress remaining paged output: {q_plain!r}"
        )
    finally:
        _restore_immtest(c, disable_pager=True)
        _clean_quit(c)


# ===========================================================================
# Feature F06: GMCP JSON Telemetry (Tier 2)
# ===========================================================================


@test_case(
    "T2-F06-01",
    2,
    "F06",
    "GMCP escapes quotes, backslashes, and UTF-8 strictly per RFC 8259",
    requires_server=True,
    fresh_server=False,
)
def test_t2_f06_01(ctx: E2EContext) -> None:
    c: TelnetClient | None = None
    try:
        c = ctx.make_client()
        _login_with_pre_options(c, IMM_NAME, IMM_PASS, gmcp=True)
        c.drain(0.4)

        c.cmd('title "Q"\\B é 日本語')
        c.drain(0.4)

        _assert_all_gmcp_valid(c)
        cs = _latest_gmcp(c, "Char.Status")
        assert cs is not None and isinstance(cs.data, dict)
        assert '"Q"\\B é 日本語' in cs.data.get("fullname", ""), (
            f"Escaped/UTF-8 title corrupted in Char.Status: {cs.data}"
        )
    finally:
        _restore_immtest(c, restore_title=True)
        _clean_quit(c)


@test_case(
    "T2-F06-02",
    2,
    "F06",
    "GMCP Char.Status.wizinvis reflects true PLR_WIZINVIS polarity",
    requires_server=True,
    fresh_server=False,
)
def test_t2_f06_02(ctx: E2EContext) -> None:
    c: TelnetClient | None = None
    is_invis = False
    try:
        c = ctx.make_client()
        _login_with_pre_options(c, IMM_NAME, IMM_PASS, gmcp=True)
        c.drain(0.4)

        c.cmd("invis 105")
        invis_out = c.cmd("invis")
        if "slowly fade back into existence" in invis_out:
            # Was already invis somehow; toggle back ON
            invis_out = c.cmd("invis")
        assert "You slowly vanish into thin air." in invis_out, f"invis ON failed: {invis_out!r}"
        is_invis = True

        c.cmd("title Immtest InvisCheck")
        c.drain(0.4)
        ev_on = _latest_gmcp(c, "Char.Status")
        assert ev_on is not None
        status_on = ev_on.data["wizinvis"]

        vis_out = c.cmd("invis")
        assert "You slowly fade back into existence." in vis_out, f"invis OFF failed: {vis_out!r}"
        is_invis = False

        c.cmd("title Immtest VisCheck")
        c.drain(0.4)
        ev_off = _latest_gmcp(c, "Char.Status")
        assert ev_off is not None
        status_off = ev_off.data["wizinvis"]

        assert status_on == 105, f"Expected wizinvis==105 when PLR_WIZINVIS ON, got {status_on}"
        assert status_off == 0, f"Expected wizinvis==0 when PLR_WIZINVIS OFF, got {status_off}"
    finally:
        if c is not None and c.sock is not None and not c.eof and is_invis:
            try:
                c.cmd("invis", timeout=5.0)
            except Exception:
                pass
        _restore_immtest(c, restore_title=True)
        _clean_quit(c)


# NOTE: T2-F06-03 & T2-F06-04 trigger ASan SEGV / TelnetState UBSan hang on the
# unfixed binary (verified in diagnostic run), so fresh_server=True is required
# to isolate shared_server from process crashes/hangs.


@test_case(
    "T2-F06-05",
    2,
    "F06",
    "GMCP suppressed when unnegotiated and safe during mid-disconnect quit",
    requires_server=True,
    fresh_server=False,
)
def test_t2_f06_05(ctx: E2EContext) -> None:
    c_no: TelnetClient | None = None
    c_peer: TelnetClient | None = None
    c_drop: TelnetClient | None = None
    try:
        c_no = ctx.make_client()
        _assert_login(c_no, IMM_NAME, IMM_PASS)
        _goto_checked(c_no, 10300)
        _goto_checked(c_no, 100)
        _clean_quit(c_no)
        assert len(c_no.gmcp_events) == 0, f"GMCP emitted without negotiation: {c_no.gmcp_events}"
        c_no = None

        c_peer = ctx.make_client()
        _login_with_pre_options(c_peer, IMM_NAME, IMM_PASS, gmcp=True)

        c_drop = ctx.make_client()
        _read_until_assert(c_drop, r"(?:Please enter your name:|Name:)\s*$", desc="Name prompt")
        c_drop.enable_gmcp()
        c_drop.send_line("Dropgmcp")
        _read_until_assert(c_drop, r"Did I get that right,.*\?\s*", desc="confirm Dropgmcp")
        c_drop.send_line("Y")
        _read_until_assert(c_drop, r"Pick a good password for .*?:\s*", desc="password")
        c_drop.send_line("ValidPass1")
        _read_until_assert(c_drop, r"Please retype the password to confirm:\s*", desc="retype")
        c_drop.send_line("ValidPass1")
        _read_until_assert(c_drop, r"What is your sex \(M/F/N\)\?\s*", desc="sex")
        c_drop.send_line("M")
        _read_until_assert(c_drop, r":\s*$", desc="race")
        c_drop.send_line("Human")
        _read_until_assert(c_drop, r":\s*$", desc="ability")
        c_drop.send_line("combat")
        _read_until_assert(c_drop, r"Are these stats OK\?", desc="stats")
        c_drop.send_line("Y")
        _read_until_assert(c_drop, r"Would you like ANSI or no graphic/color support, \(A/N\)\?\s*", desc="ansi")
        c_drop.send_line("A")
        _read_until_assert(c_drop, r"(?:Press enter\.\.\.|Welcome to Rise in Power !!|Press \[ENTER\])", desc="title")
        c_drop.send_line("")
        st2 = _read_until_assert(c_drop, re.compile(r"(?:Press \[ENTER\]|Health:\s*\d+/\d+\s+Movement:\s*\d+/\d+\s*>)"), desc="motd")
        if "Press [ENTER]" in strip_ansi(st2):
            c_drop.send_line("")
            _read_until_assert(c_drop, DEFAULT_PROMPT_RE, desc="prompt")
        c_drop.send_line("quit")
        c_drop.close()
        c_drop = None

        c_peer.drain(0.4)
        peer_look = c_peer.cmd("look", require_prompt=True)
        assert len(peer_look.strip()) > 0
        ctx.assert_clean_sanitizers(ctx.shared_server)
    finally:
        _clean_quit(c_no)
        _clean_quit(c_drop)
        _clean_quit(c_peer)


# ===========================================================================
# Feature F07: Core DB, Boot, const.c/tables.c & 40-Area area/ (Tier 2)
# ===========================================================================


@test_case(
    "T2-F07-01",
    2,
    "F07",
    "Static scanners persistence_and_vnum and command_skill_wiring pass post-remediation",
    requires_server=False,
    fresh_server=False,
)
def test_t2_f07_01(ctx: E2EContext) -> None:
    res = ctx.run_static_scanners(
        checks=["persistence_and_vnum", "command_skill_wiring"], mode="post-remediation"
    )
    pv = res["scanners"]["persistence_and_vnum"]
    cw = res["scanners"]["command_skill_wiring"]
    assert pv["passed"] is True, f"persistence_and_vnum failed: {pv['summary']}"
    assert cw["passed"] is True, f"command_skill_wiring failed: {cw['summary']}"


@test_case(
    "T2-F07-02",
    2,
    "F07",
    "Area VNUM uniqueness and critical system rooms exist at runtime",
    requires_server=True,
    fresh_server=False,
)
def test_t2_f07_02(ctx: E2EContext) -> None:
    c: TelnetClient | None = None
    try:
        c = ctx.make_client()
        _assert_login(c, IMM_NAME, IMM_PASS)

        critical_vnums = (100, 10300, 273, 10630, 10556, 2701, 2762, 32000, 32149, 10000, 1800)
        for vnum in critical_vnums:
            _goto_checked(c, vnum)
    finally:
        _restore_immtest(c)
        _clean_quit(c)


@test_case(
    "T2-F07-03",
    2,
    "F07",
    "Invalid help, commands prefix, and slist level boundary handling",
    requires_server=True,
    fresh_server=False,
)
def test_t2_f07_03(ctx: E2EContext) -> None:
    c: TelnetClient | None = None
    try:
        c = ctx.make_client()
        _assert_login(c, IMM_NAME, IMM_PASS)

        out_h = c.cmd("help zzznonexistenttopic999")
        assert "No help on 'zzznonexistenttopic999' found." in out_h

        out_c = c.cmd("commands zzznoncmd999")
        assert "No command found under zzznoncmd999." in out_c

        out_s_inv = c.cmd("slist 200 10")
        out_s_zero = c.cmd("slist 0 0")
        assert re.search(r"SPELL\s+(?:&\s+)?SKILL\s+LIST", out_s_inv) and re.search(
            r"SPELL\s+(?:&\s+)?SKILL\s+LIST", out_s_zero
        ), f"Unexpected slist output: inv={out_s_inv[:120]!r} zero={out_s_zero[:120]!r}"
    finally:
        _clean_quit(c)


@test_case(
    "T2-F07-04",
    2,
    "F07",
    "const.c race_table and ability_name boundaries at character creation",
    requires_server=True,
    fresh_server=False,
)
def test_t2_f07_04(ctx: E2EContext) -> None:
    c: TelnetClient | None = None
    try:
        c = ctx.make_client()
        _read_until_assert(c, r"(?:Please enter your name:|Name:)\s*$", desc="Name prompt")
        c.send_line("Consttst")
        _read_until_assert(c, r"Did I get that right,.*\?\s*", desc="confirm prompt")
        c.send_line("Y")
        _read_until_assert(c, r"Pick a good password for .*?:\s*", desc="pick password")
        c.send_line("ConstPass1")
        _read_until_assert(c, r"Please retype the password to confirm:\s*", desc="retype password")
        c.send_line("ConstPass1")
        _read_until_assert(c, r"What is your sex \(M/F/N\)\?\s*", desc="sex prompt")
        c.send_line("M")
        _read_until_assert(c, r":\s*$", desc="race list prompt")

        c.send_line("God")
        _read_until_assert(c, r"What IS your race\?\s*", timeout=5.0, desc="reject God race")
        c.send_line("Zzzbogusrace")
        _read_until_assert(c, r"What IS your race\?\s*", timeout=5.0, desc="reject bogus race")

        c.send_line("Human")
        _read_until_assert(c, r":\s*$", timeout=5.0, desc="ability list prompt")

        c.send_line("force")
        _read_until_assert(c, r"What IS it going to be\?\s*", timeout=5.0, desc="reject force class")
        c.send_line("zzznoclass")
        _read_until_assert(c, r"What IS it going to be\?\s*", timeout=5.0, desc="reject bogus class")

        c.send_line("combat")
        _read_until_assert(c, r"Are these stats OK\?", timeout=5.0, desc="stats ok prompt")
    finally:
        if c is not None:
            c.close()


@test_case(
    "T2-F07-05",
    2,
    "F07",
    "Mortal privilege boundary on memory, zones, commands and MSSP/memory parity",
    requires_server=True,
    fresh_server=False,
)
def test_t2_f07_05(ctx: E2EContext) -> None:
    plr: TelnetClient | None = None
    imm: TelnetClient | None = None
    try:
        plr = ctx.make_client()
        _assert_create_character(plr, "Fsevchk", "FsevPass1")
        assert "Huh?" in plr.cmd("memory")
        assert "Huh?" in plr.cmd("zones")
        assert "Huh?" in plr.cmd("commands")

        mssp = plr.request_mssp(timeout=5.0)
        _clean_quit(plr)
        plr = None

        imm = ctx.make_client()
        _assert_login(imm, IMM_NAME, IMM_PASS)
        mem = imm.cmd("memory")

        def _mem_num(label: str) -> int:
            m = re.search(rf"{label}\s+(\d+)", mem)
            assert m is not None, f"Missing {label} in memory: {mem!r}"
            return int(m.group(1))

        assert int(mssp["AREAS"]) == _mem_num("Areas")
        assert int(mssp["ROOMS"]) == _mem_num("Rooms")
        assert int(mssp["MOBILES"]) == _mem_num("IdxMobs")
        assert int(mssp["OBJECTS"]) == _mem_num("IdxObjs")
        assert int(mssp["SKILLS"]) == _mem_num("Topsn")
    finally:
        _clean_quit(plr)
        _clean_quit(imm)


# ===========================================================================
# Feature F08: Player Save/Load (a..z), Corpses, Storerooms & Vendors (Tier 2)
# ===========================================================================


@test_case(
    "T2-F08-01",
    2,
    "F08",
    "UNAUTHED new character save is skipped and cannot reconnect",
    requires_server=True,
    fresh_server=False,
)
def test_t2_f08_01(ctx: E2EContext) -> None:
    c: TelnetClient | None = None
    c2: TelnetClient | None = None
    try:
        c = ctx.make_client(timeout=5.0)
        _assert_create_character(c, "Unauthtst", "UnauthPass1")
        c.cmd("save", timeout=5.0)
        _clean_quit(c)
        c = None

        assert ctx._shared_work_dir is not None
        pfile = ctx._shared_work_dir / "player" / "u" / "Unauthtst"
        assert not pfile.exists(), f"UNAUTHED character should not save pfile: {pfile}"

        c2 = ctx.make_client(timeout=5.0)
        _read_until_assert(c2, r"(?:Please enter your name:|Name:)\s*$", desc="Name prompt")
        c2.send_line("Unauthtst")
        prompt = _read_until_assert(
            c2, r"Did I get that right, Unauthtst \(Y/N\)\?", timeout=5.0, desc="new-char confirm"
        )
        assert "Did I get that right" in strip_ansi(prompt)
        c2.close()
        c2 = None

        ctx.assert_clean_sanitizers(ctx.shared_server)
    finally:
        _clean_quit(c)
        _clean_quit(c2)


@test_case(
    "T2-F08-02",
    2,
    "F08",
    "do_save error reporting when player file write fails (post-remediation)",
    requires_server=True,
    fresh_server=True,
)
def test_t2_f08_02(ctx: E2EContext) -> None:
    proc = ctx.start_fresh_server("f08-savefail")
    imm: TelnetClient | None = None
    plr: TelnetClient | None = None
    s_dir = proc.work_dir / "player" / "s"
    s_file = s_dir / "Savefail"
    chmod_applied = False
    try:
        imm = ctx.make_client(port=proc.port)
        _assert_login(imm, IMM_NAME, IMM_PASS)

        plr = ctx.make_client(port=proc.port)
        _assert_create_character(plr, "Savefail", "SaveFail1")
        _authorize_player(imm, "Savefail")
        plr.drain(0.2)
        assert "Ok." in plr.cmd("save")
        assert s_file.is_file(), f"Initial save failed to create {s_file}"

        os.chmod(s_file, 0o444)
        os.chmod(s_dir, 0o555)
        chmod_applied = True

        out = plr.cmd("save")
        assert "Ok." not in out, f"do_save falsely reported Ok. on write failure: {out!r}"
        assert any(kw in out.lower() for kw in ("fail", "error", "unable", "could not")), (
            f"Expected save failure message, got: {out!r}"
        )
    finally:
        if chmod_applied:
            try:
                if s_dir.exists():
                    os.chmod(s_dir, 0o755)
                if s_file.exists():
                    os.chmod(s_file, 0o644)
            except OSError:
                pass
        _clean_quit(plr)
        _clean_quit(imm)
        if proc.is_running():
            _shutdown_and_assert_clean(ctx, proc)
        _safe_stop_proc(proc)


@test_case(
    "T2-F08-03",
    2,
    "F08",
    "buyvendor negative boundaries (no keeper, low level, insufficient credits, duplicate shop)",
    requires_server=True,
    fresh_server=False,
)
def test_t2_f08_03(ctx: E2EContext) -> None:
    imm: TelnetClient | None = None
    plr: TelnetClient | None = None
    placed_shop = False
    try:
        imm = ctx.make_client()
        _assert_login(imm, IMM_NAME, IMM_PASS)

        plr = ctx.make_client()
        _assert_create_character(plr, "Vendneg", "VendNeg1")
        _authorize_player(imm, "Vendneg")
        plr.drain(0.2)

        assert "There is no one to buy that from!" in plr.cmd("buyvendor", require_prompt=True)

        _goto_checked(imm, 10321)
        imm.cmd("transfer Vendneg 10321")
        plr.drain(0.25)
        assert "You are too poor!" in plr.cmd("buyvendor", require_prompt=True)

        imm.cmd("mset Vendneg credits 1500000")
        plr.drain(0.2)
        assert "you must be at least 50 level." in plr.cmd("buyvendor", require_prompt=True)

        imm.cmd("advance Vendneg combat 55")
        plr.drain(0.25)
        assert "contract for a vendor" in plr.cmd("buyvendor", require_prompt=True).lower()

        _goto_checked(imm, 10630)
        imm.cmd("transfer Vendneg 10630")
        plr.drain(0.25)
        pv = plr.cmd("placevendor", require_prompt=True)
        if "swirl of smoke" in pv.lower():
            placed_shop = True

        imm.cmd("transfer Vendneg 10321")
        plr.drain(0.25)
        dup = plr.cmd("buyvendor", require_prompt=True)
        assert "You already have a shop!" in dup, f"Expected duplicate shop rejection: {dup!r}"
    finally:
        _clean_quit(plr)
        _restore_immtest(imm, purge_rooms=(10630,) if placed_shop else ())
        _clean_quit(imm)


@test_case(
    "T2-F08-04",
    2,
    "F08",
    "placevendor restrictions and owner-only vendor pricing/collection",
    requires_server=True,
    fresh_server=False,
)
def test_t2_f08_04(ctx: E2EContext) -> None:
    imm: TelnetClient | None = None
    vb: TelnetClient | None = None
    placed_shop = False
    try:
        imm = ctx.make_client()
        _assert_login(imm, IMM_NAME, IMM_PASS)

        vb = ctx.make_client()
        _assert_create_character(vb, "Vendbad", "VendBad1")
        _authorize_player(imm, "Vendbad")
        vb.drain(0.2)

        assert "You do not have a deed!." in vb.cmd("placevendor", require_prompt=True)

        _goto_checked(imm, 10300)
        assert "Ok." in imm.cmd("oinvoke 4", require_prompt=True)
        imm.cmd("give deed Vendbad", require_prompt=True)
        vb.drain(0.25)
        assert "You need to find a free shop." in vb.cmd("placevendor", require_prompt=True)

        _goto_checked(imm, 273)
        imm.cmd("transfer Vendbad 273", require_prompt=True)
        vb.drain(0.25)
        pv = vb.cmd("placevendor", require_prompt=True)
        assert "swirl of smoke" in pv.lower(), f"placevendor in 273 failed: {pv!r}"
        placed_shop = True

        imm.drain(0.1)
        assert "Ok." in imm.cmd("oinvoke 10311", require_prompt=True)
        imm.cmd("give glowrod Vendbad", require_prompt=True)
        vb.drain(0.25)
        vb.cmd("give glowrod vendor", require_prompt=True)

        assert "Usage: pricevendor <item> <cost>" in vb.cmd("pricevendor", require_prompt=True)
        assert "Your vendor doesn't have that item." in vb.cmd("pricevendor nonexistent 100", require_prompt=True)
        assert "The price has been changed." in vb.cmd("pricevendor glowrod 100", require_prompt=True)
        earnings = vb.cmd("getcredits", require_prompt=True)
        assert "Your vendor has no earnings to collect." in earnings, earnings

        _ensure_vendor_ownership_guards(ctx)
    finally:
        _clean_quit(vb)
        _restore_immtest(imm, purge_items=("deed", "glowrod"), purge_rooms=(273,) if placed_shop else ())
        _clean_quit(imm)


@test_case(
    "T2-F08-05",
    2,
    "F08",
    "Non-storeroom cleanup and Name length boundary (3-char and 20-char pfiles)",
    requires_server=True,
    fresh_server=True,
)
def test_t2_f08_05(ctx: E2EContext) -> None:
    proc = ctx.start_fresh_server("f08-bounds")
    proc2: SwripProcess | None = None
    imm: TelnetClient | None = None
    c_min: TelnetClient | None = None
    c_max: TelnetClient | None = None
    try:
        imm = ctx.make_client(port=proc.port)
        _assert_login(imm, IMM_NAME, IMM_PASS)

        min_name = "Abc"
        max_name = "Abcdefghijklmnopqrst"
        assert len(min_name) == 3 and len(max_name) == 20

        c_min = ctx.make_client(port=proc.port)
        _assert_create_character(c_min, min_name, "MinPass1")
        _authorize_player(imm, min_name)
        c_min.drain(0.2)
        assert "Ok." in c_min.cmd("save")
        min_pfile = proc.work_dir / "player" / "a" / min_name
        assert min_pfile.is_file(), f"3-char player file missing on disk: {min_pfile}"
        _clean_quit(c_min)
        c_min = None

        c_max = ctx.make_client(port=proc.port)
        _assert_create_character(c_max, max_name, "MaxPass1")
        _authorize_player(imm, max_name)
        c_max.drain(0.2)
        assert "Ok." in c_max.cmd("save")
        max_pfile = proc.work_dir / "player" / "a" / max_name
        assert max_pfile.is_file(), f"20-char player file missing on disk: {max_pfile}"

        _clean_quit(c_max)
        c_max = None
        _clean_quit(imm)
        imm = None
        _shutdown_and_assert_clean(ctx, proc)

        # Write synthetic non-storeroom file 10300 (ROOM_CLANSTOREROOM not set) before reboot
        stale_store = proc.work_dir / "storeroom" / "10300"
        stale_store.parent.mkdir(parents=True, exist_ok=True)
        stale_store.write_text("#END\n", encoding="utf-8")

        proc2 = SwripProcess(proc.work_dir)
        proc2.start()
        ctx._processes.append(proc2)
        bm2 = proc2.wait_ready(15.0)
        assert bm2.ready is True, f"Reboot failed: {bm2.log_tail}"
        assert not stale_store.exists(), "load_storerooms did not remove non-ROOM_CLANSTOREROOM file 10300"

        c_min = ctx.make_client(port=proc2.port)
        _assert_login(c_min, min_name, "MinPass1")
        assert min_name in c_min.cmd("score")
        _clean_quit(c_min)
        c_min = None

        c_max = ctx.make_client(port=proc2.port)
        _assert_login(c_max, max_name, "MaxPass1")
        assert max_name in c_max.cmd("score")

        _clean_quit(c_min)
        c_min = None
        _clean_quit(c_max)
        c_max = None
        _shutdown_and_assert_clean(ctx, proc2)
    finally:
        _clean_quit(c_min)
        _clean_quit(c_max)
        _clean_quit(imm)
        _safe_stop_proc(proc)
        _safe_stop_proc(proc2)


# ===========================================================================
# Feature F09: Combat, Blasters, Corpses, Tracking & special.c (Tier 2)
# ===========================================================================


@test_case(
    "T2-F09-01",
    2,
    "F09",
    "Combat negative boundaries (nonexistent target, PC kill gate, safe room, flee out of combat)",
    requires_server=True,
    fresh_server=False,
)
def test_t2_f09_01(ctx: E2EContext) -> None:
    imm: TelnetClient | None = None
    plr: TelnetClient | None = None
    try:
        imm = ctx.make_client()
        _assert_login(imm, IMM_NAME, IMM_PASS)
        _goto_checked(imm, 10300)

        plr = ctx.make_client()
        _assert_create_character(plr, "Fcbtneg", "CbtNeg1")
        _authorize_player(imm, "Fcbtneg")
        plr.drain(0.2)

        assert "They aren't here." in plr.cmd("kill zzznobody", require_prompt=True)
        assert "They're not here." in plr.cmd("consider zzznobody", require_prompt=True)
        assert "You aren't fighting anyone." in plr.cmd("flee", require_prompt=True)
        assert "You must MURDER a player." in plr.cmd("kill Immtest", require_prompt=True)

        assert "Ok." in imm.cmd("minvoke 10345")
        plr.drain(0.25)
        safe_out = plr.cmd("kill chadoyn", require_prompt=True)
        assert "You'll have to do that elsewhere." in safe_out, f"ROOM_SAFE failed: {safe_out!r}"
    finally:
        _clean_quit(plr)
        _restore_immtest(imm, purge_rooms=(10300,))
        _clean_quit(imm)


@test_case(
    "T2-F09-02",
    2,
    "F09",
    "Blaster empty-ammo click, non-blaster reload/setblaster/snipe rejection",
    requires_server=True,
    fresh_server=False,
)
def test_t2_f09_02(ctx: E2EContext) -> None:
    imm: TelnetClient | None = None
    plr: TelnetClient | None = None
    try:
        imm = ctx.make_client(timeout=5.0)
        _assert_login(imm, IMM_NAME, IMM_PASS)

        plr = ctx.make_client(timeout=5.0)
        _assert_create_character(plr, "Fblastneg", "BlastNeg1")
        _authorize_player(imm, "Fblastneg")
        plr.drain(0.2)

        _goto_checked(imm, 10308)
        imm.cmd("transfer Fblastneg 10308", timeout=5.0)
        imm.cmd("mset Fblastneg str 18", timeout=5.0)
        imm.cmd("sset Fblastneg blasters 100", timeout=5.0)
        imm.cmd("sset Fblastneg snipe 100", timeout=5.0)
        assert "Ok." in imm.cmd("oinvoke 50 1", timeout=5.0)
        imm.cmd("oset blaster weapontype blaster", timeout=5.0)
        imm.cmd("oset blaster v4 0", timeout=5.0)
        assert "Ok." in imm.cmd("oinvoke 10312 1", timeout=5.0)
        imm.cmd("give blaster Fblastneg", timeout=5.0)
        imm.cmd("give vibro Fblastneg", timeout=5.0)
        plr.drain(0.25)

        plr.cmd("remove vibro", timeout=5.0, require_prompt=True)
        assert "You don't seem to be holding a weapon." in plr.cmd("ammo", timeout=5.0, require_prompt=True)

        wield_out = plr.cmd("wield blaster", timeout=5.0, require_prompt=True)
        assert "You wield" in wield_out or "blaster" in wield_out.lower(), f"wield blaster failed: {wield_out!r}"
        assert "You don't seem to have any ammo to reload your blaster with." in plr.cmd("ammo", timeout=5.0, require_prompt=True)
        assert "Usage: setblaster <full|high|normal|half|low|stun>" in plr.cmd("setblaster bogusarg", timeout=5.0, require_prompt=True)

        _ensure_chadoyn_present(imm)
        plr.drain(0.2)
        plr.send_line("kill chadoyn")
        click_msg = _read_until_assert(
            plr, r"your blaster needs a new ammunition cell!", timeout=5.0, desc="empty blaster click"
        )
        assert "*CLICK*" in strip_ansi(click_msg)
        for _ in range(6):
            plr.send_line("flee")
            flee_out = _read_until_assert(
                plr,
                r"(?:You run for cover!|You fled|PANIC!|You attempt to run for cover!|You aren't fighting anyone\.)",
                timeout=5.0,
                desc="flee after click",
            )
            clean_flee = strip_ansi(flee_out)
            if "PANIC!" not in clean_flee and "You attempt to run for cover!" not in clean_flee:
                break
        plr.drain(0.2)

        plr.cmd("remove blaster", timeout=5.0, require_prompt=True)
        plr.cmd("wield vibro", timeout=5.0, require_prompt=True)
        assert "You don't seem to be wielding a blaster." in plr.cmd("setblaster normal", timeout=5.0, require_prompt=True)
        assert "You don't seem to be holding a blaster" in plr.cmd("snipe east chadoyn", timeout=5.0, require_prompt=True)
    finally:
        _clean_quit(plr)
        _restore_immtest(imm, purge_items=("blaster", "vibro"), respawn_chadoyn=True)
        _clean_quit(imm)


@test_case(
    "T2-F09-03",
    2,
    "F09",
    "Tracking negative boundaries (unlearned, empty arg, nonexistent mob, cross-area mob)",
    requires_server=True,
    fresh_server=False,
)
def test_t2_f09_03(ctx: E2EContext) -> None:
    imm: TelnetClient | None = None
    plr: TelnetClient | None = None
    try:
        imm = ctx.make_client()
        _assert_login(imm, IMM_NAME, IMM_PASS)
        _ensure_chadoyn_present(imm)

        plr = ctx.make_client(timeout=5.0)
        _assert_create_character(plr, "Ftrkneg", "TrkNeg1", ability="bounty hunting")
        _authorize_player(imm, "Ftrkneg")
        plr.drain(0.2)
        imm.cmd("advance Ftrkneg bounty 5", timeout=5.0)
        plr.drain(0.2)

        unlearned_out = plr.cmd("track chadoyn", timeout=5.0, require_prompt=True)
        assert "Huh?" in unlearned_out, (
            f"Expected 'Huh?' for unlearned track skill via check_skill, got: {unlearned_out!r}"
        )

        imm.cmd("sset Ftrkneg track 100", timeout=5.0)
        plr.drain(0.25)
        empty_out = plr.cmd("track", timeout=5.0, require_prompt=True)
        assert "Whom are you trying to track?" in empty_out, f"Unexpected empty track: {empty_out!r}"
        miss_out = plr.cmd("track zzznonexistent", timeout=5.0, require_prompt=True)
        assert "You can't sense a trail from here." in miss_out, f"Unexpected miss track: {miss_out!r}"

        _goto_checked(imm, 100)
        assert "Ok." in imm.cmd("minvoke 4", timeout=5.0)
        plr.drain(0.2)
        cross_out = plr.cmd("track vendor", timeout=5.0, require_prompt=True)
        assert "You can't sense a trail from here." in cross_out, f"Unexpected cross-area track: {cross_out!r}"
    finally:
        _clean_quit(plr)
        _restore_immtest(imm, purge_rooms=(100,))
        _clean_quit(imm)


@test_case(
    "T2-F09-04",
    2,
    "F09",
    "spec_newbie_pilot non-Human race graduation and no-diploma safety",
    requires_server=True,
    fresh_server=False,
)
def test_t2_f09_04(ctx: E2EContext) -> None:
    imm: TelnetClient | None = None
    p_no: TelnetClient | None = None
    p_wook: TelnetClient | None = None
    p_mcal: TelnetClient | None = None
    try:
        imm = ctx.make_client()
        _assert_login(imm, IMM_NAME, IMM_PASS)

        # Test A: No diploma -> must remain in 10326
        p_no = ctx.make_client(timeout=5.0)
        _assert_create_character(p_no, "Fnodipl", "NoDipl1", race="Human")
        _authorize_player(imm, "Fnodipl")
        p_no.drain(0.15)
        imm.cmd("transfer Fnodipl 10326", timeout=5.0)
        time.sleep(4.5)
        p_no.drain(0.2)
        at_no = imm.cmd("at Fnodipl rstat", timeout=5.0)
        assert "10326" in at_no, f"Character without diploma was warped out of 10326: {at_no!r}"
        _clean_quit(p_no)
        p_no = None

        # Test B: Non-Human race graduation -> valid non-Limbo destinations
        p_wook = ctx.make_client(timeout=5.0)
        _assert_create_character(p_wook, "Fwook", "WookPass1", race="Wookiee")
        _authorize_player(imm, "Fwook")
        imm.cmd("transfer Fwook 10324", timeout=5.0)
        p_wook.drain(0.2)
        _read_until_assert(p_wook, r"gives you a diploma", timeout=6.0, desc="Fwook diploma")
        p_wook.cmd("say iamauthed", timeout=5.0, require_prompt=True)
        p_wook.drain(0.2)
        p_wook.cmd("enter", timeout=5.0, require_prompt=True)
        _read_until_assert(p_wook, r"After a brief journey|steps out|Kashyyyk|Pluogus", timeout=6.0, desc="Fwook warp")
        look_wook = p_wook.cmd("look", timeout=5.0, require_prompt=True)
        assert "Limbo" not in look_wook and "You are floating in a void" not in look_wook, (
            f"Wookiee graduate stranded in Limbo (#2): {look_wook!r}"
        )
        at_wook = imm.cmd("at Fwook rstat", timeout=5.0)
        assert (
            not re.search(r"\bVnum:\s*2\b", at_wook)
            and "Name: Limbo" not in at_wook
            and "10326" not in at_wook
        ), f"Fwook bad destination: {at_wook!r}"
        _clean_quit(p_wook)
        p_wook = None

        p_mcal = ctx.make_client(timeout=5.0)
        _assert_create_character(p_mcal, "Fmcal", "McalPass1", race="Mon Calamari")
        _authorize_player(imm, "Fmcal")
        imm.cmd("transfer Fmcal 10324", timeout=5.0)
        p_mcal.drain(0.2)
        _read_until_assert(p_mcal, r"gives you a diploma", timeout=6.0, desc="Fmcal diploma")
        p_mcal.cmd("say iamauthed", timeout=5.0, require_prompt=True)
        p_mcal.drain(0.2)
        p_mcal.cmd("enter", timeout=5.0, require_prompt=True)
        _read_until_assert(p_mcal, r"After a brief journey|steps out|Mon Calamari|Pluogus", timeout=6.0, desc="Fmcal warp")
        look_mcal = p_mcal.cmd("look", timeout=5.0, require_prompt=True)
        assert "Limbo" not in look_mcal and "You are floating in a void" not in look_mcal, (
            f"Mon Calamari graduate stranded in Limbo (#2): {look_mcal!r}"
        )
        at_mcal = imm.cmd("at Fmcal rstat", timeout=5.0)
        assert (
            not re.search(r"\bVnum:\s*2\b", at_mcal)
            and "Name: Limbo" not in at_mcal
            and "10326" not in at_mcal
        ), f"Mon Calamari graduate stranded in Limbo (#2) or 10326: {at_mcal!r}"
        _clean_quit(p_mcal)
        p_mcal = None
    finally:
        _clean_quit(p_no)
        _clean_quit(p_wook)
        _clean_quit(p_mcal)
        _restore_immtest(imm)
        _clean_quit(imm)


@test_case(
    "T2-F09-05",
    2,
    "F09",
    "Corpse loot negative boundaries and multi-victim violence_update safety",
    requires_server=True,
    fresh_server=False,
)
def test_t2_f09_05(ctx: E2EContext) -> None:
    imm: TelnetClient | None = None
    plr: TelnetClient | None = None
    try:
        imm = ctx.make_client(timeout=5.0)
        _assert_login(imm, IMM_NAME, IMM_PASS)
        _goto_checked(imm, 10316)

        plr = ctx.make_client(timeout=5.0)
        _assert_create_character(plr, "Fcbtadv", "CbtAdv1")
        _authorize_player(imm, "Fcbtadv")
        plr.drain(0.2)

        imm.cmd("transfer Fcbtadv 10316", timeout=5.0)
        imm.cmd("advance Fcbtadv combat 25", timeout=5.0)
        imm.cmd("mset Fcbtadv hitroll 15", timeout=5.0)
        plr.drain(0.2)
        assert "I see no zzznocorpse here." in plr.cmd("get all zzznocorpse", timeout=5.0, require_prompt=True)

        assert "Ok." in imm.cmd("minvoke 10345", timeout=5.0)
        assert "Ok." in imm.cmd("minvoke 10345", timeout=5.0)
        plr.drain(0.2)

        plr.send_line("kill chadoyn")
        _read_until_assert(plr, r"is DEAD!", timeout=8.0, desc="first kill in 10316")
        plr.drain(0.2)
        noitem_out = plr.cmd("get zzznoitem corpse", timeout=5.0, require_prompt=True)
        assert "I see nothing like that in" in noitem_out, f"Unexpected get missing item output: {noitem_out!r}"

        plr.send_line("kill chadoyn")
        _read_until_assert(plr, r"is DEAD!", timeout=8.0, desc="second kill in 10316")
        plr.drain(0.2)

        imm.cmd("transfer Fcbtadv 10324", timeout=5.0)
        plr.drain(0.2)
        plr.send_line("kill schoolmaster")
        safe_school = _read_until_assert(
            plr, r"You'll have to do that elsewhere\.", timeout=5.0, desc="safe room block"
        )
        assert "You'll have to do that elsewhere." in strip_ansi(safe_school), (
            f"Expected safe room block: {safe_school!r}"
        )

        ctx.assert_clean_sanitizers(ctx.shared_server)
    finally:
        _clean_quit(plr)
        _restore_immtest(imm, purge_rooms=(10316,))
        _clean_quit(imm)
