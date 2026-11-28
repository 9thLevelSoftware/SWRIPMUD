"""Tier 1 (Happy-Path) E2E Tests for SWRIPMUD Features F01..F09 (45 test cases)."""

from __future__ import annotations

import re
import time
from pathlib import Path

from tests.e2e.framework import E2EContext, test_case
from tests.e2e.telnet_client import (
    DEFAULT_PROMPT_RE,
    TELOPT_COMPRESS2,
    TELOPT_EOR,
    TELOPT_GMCP,
    TELOPT_NAWS,
    TELOPT_SGA,
    TELOPT_TTYPE,
    TTYPE_SEND,
    WILL,
    GmcpEvent,
    TelnetClient,
    strip_ansi,
)
from tests.e2e.wsl_env import SwripProcess, des_crypt, sha256_hash

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
    gmcp_supports: list[str] | None = None,
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
        if gmcp_supports is not None:
            client.send_gmcp("Core.Supports.Add", gmcp_supports)
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


def _goto_checked(client: TelnetClient, vnum: int) -> str:
    client.drain(0.1)
    out = client.cmd(f"goto {vnum}", timeout=5.0, require_prompt=True)
    if (
        "Log:" in out
        and f"goto {vnum}" not in out
        and "obvious exits:" not in out.lower()
        and "no such location" not in out.lower()
    ):
        try:
            out += strip_ansi(client.read_until(DEFAULT_PROMPT_RE, timeout=5.0))
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
    unwear_items: tuple[str, ...] = (),
    purge_items: tuple[str, ...] = (),
    respawn_chadoyn: bool = False,
) -> None:
    if imm is None or imm.sock is None or imm.eof:
        return
    try:
        for item in unwear_items:
            imm.cmd(f"remove {item}", timeout=5.0)
        for item in purge_items:
            imm.cmd(f"drop {item}", timeout=5.0)
            imm.cmd(f"purge {item}", timeout=5.0)
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
    if "chadoyn" not in look_out.lower():
        imm.cmd("minvoke 10345")


def _latest_gmcp(client: TelnetClient, module: str) -> GmcpEvent | None:
    matches = [ev for ev in client.gmcp_events if ev.module == module]
    return matches[-1] if matches else None


def _count_gmcp(client: TelnetClient, module: str) -> int:
    return sum(1 for ev in client.gmcp_events if ev.module == module)


def _assert_all_gmcp_valid(client: TelnetClient) -> None:
    bad = [ev for ev in client.gmcp_events if not ev.json_valid]
    assert not bad, f"Invalid RFC 8259 GMCP JSON events: {bad[:3]}"


def _cmd_no_crash(proc: SwripProcess, client: TelnetClient, cmd_text: str, timeout: float = 5.0) -> str:
    try:
        return client.cmd(cmd_text, timeout=timeout)
    except TimeoutError as exc:
        rep = proc.parse_sanitizers()
        if not proc.is_running() or not rep.clean:
            raise AssertionError(
                f"Server crashed or triggered sanitizer error on {cmd_text!r}: "
                f"running={proc.is_running()}, asan={rep.asan_errors[:2]}, ubsan={rep.ubsan_errors[:2]}"
            ) from exc
        raise AssertionError(f"Command {cmd_text!r} timed out/EOF unexpectedly: {exc}") from exc


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
# Feature F01: Allocators & Interned Strings
# ===========================================================================


@test_case(
    "T1-F01-01",
    1,
    "F01",
    "Static allocator_mismatch scanner parity (0 conflicts, STRFREE nulls ptr)",
    requires_server=False,
    fresh_server=False,
)
def test_t1_f01_01(ctx: E2EContext) -> None:
    res = ctx.run_static_scanners(checks=["allocator_mismatch"], mode="post-remediation")
    r = res["scanners"]["allocator_mismatch"]
    s = r["summary"]
    assert s["strfree_nulls_pointer"] is True, f"STRFREE must null pointer: {s}"
    assert s["non_1_create_count"] == 0, f"non_1_create_count={s['non_1_create_count']}"
    assert s["strfree_reuse_without_null_count"] == 0, (
        f"strfree_reuse_without_null_count={s['strfree_reuse_without_null_count']}"
    )
    assert s["strfree_dangling_member_exit_count"] == 0, (
        f"strfree_dangling_member_exit_count={s['strfree_dangling_member_exit_count']}"
    )
    assert s["non_pod_mem_ops_count"] == 0, f"non_pod_mem_ops_count={s['non_pod_mem_ops_count']}"
    assert s["field_regime_conflicts_count"] == 0, (
        f"field_regime_conflicts_count={s['field_regime_conflicts_count']}: "
        f"{[f['field'] for f in r['findings']['field_regime_conflicts']]}"
    )
    assert s["local_alloc_free_mismatches_count"] == 0, (
        f"local_alloc_free_mismatches_count={s['local_alloc_free_mismatches_count']}"
    )
    assert r["passed"] is True, f"allocator_mismatch failed: {s}"


@test_case(
    "T1-F01-02",
    1,
    "F01",
    "Clean connect, oedit/redit2/medit edits, quit and shutdown mud now with 0 bytes leaked",
    requires_server=True,
    fresh_server=True,
)
def test_t1_f01_02(ctx: E2EContext) -> None:
    proc = ctx.start_fresh_server("f01_olc_clean")
    c: TelnetClient | None = None
    try:
        c = ctx.make_client(port=proc.port)
        _assert_login(c, IMM_NAME, IMM_PASS)

        inv_out = c.cmd("oinvoke 10311")
        assert "Ok." in inv_out, f"oinvoke 10311 failed: {inv_out!r}"

        oe_out = c.cmd("oedit glowrod")
        assert "Object inline editing mode enabled." in oe_out, f"oedit failed: {oe_out!r}"
        c.cmd("short an e2e edited glowrod")
        c.cmd("cost 42")
        save_out = c.cmd("stop save")
        assert "Changes saved." in save_out, f"oedit stop save failed: {save_out!r}"

        re_out = c.cmd("redit2 start")
        assert "Room" in re_out or "editing" in re_out.lower(), f"redit2 start failed: {re_out!r}"
        c.cmd("name E2E Room Draft")
        abort_r = c.cmd("stop abort")
        assert "Changes discarded." in abort_r, f"redit2 stop abort failed: {abort_r!r}"

        _goto_checked(c, 10308)
        me_out = c.cmd("medit chadoyn")
        assert "Mobile" in me_out or "editing" in me_out.lower(), f"medit failed: {me_out!r}"
        c.cmd("short a tested chadoyn")
        abort_m = c.cmd("stop abort")
        assert "Changes discarded." in abort_m, f"medit stop abort failed: {abort_m!r}"

        _goto_checked(c, 100)
        c.cmd("drop glowrod")
        c.cmd("purge glowrod")
        _clean_quit(c)
        c = None

        _shutdown_and_assert_clean(ctx, proc)
    finally:
        _clean_quit(c)
        _safe_stop_proc(proc)


@test_case(
    "T1-F01-03",
    1,
    "F01",
    "oset non-prototype string mutation (short/name/actiondesc) without UAF",
    requires_server=True,
    fresh_server=False,
)
def test_t1_f01_03(ctx: E2EContext) -> None:
    c: TelnetClient | None = None
    try:
        c = ctx.make_client()
        _assert_login(c, IMM_NAME, IMM_PASS)
        inv = c.cmd("oinvoke 10311")
        assert "Ok." in inv, f"oinvoke failed: {inv!r}"

        c.cmd("oset glowrod short a renamed e2e glowrod")
        ostat1 = c.cmd("ostat glowrod")
        assert "a renamed e2e glowrod" in ostat1, f"short_descr not updated: {ostat1!r}"
        assert "rename" in ostat1, f"rename keyword missing: {ostat1!r}"

        c.cmd("oset rename actiondesc E2E shines brightly")
        ostat2 = c.cmd("ostat rename")
        assert "E2E shines brightly" in ostat2, f"action_desc not updated: {ostat2!r}"
    finally:
        _restore_immtest(c, purge_items=("rename", "glowrod"))
        _clean_quit(c)


@test_case(
    "T1-F01-04",
    1,
    "F01",
    "Interned string sharing and freeing via memory hash and oinvoke/purge",
    requires_server=True,
    fresh_server=True,
)
def test_t1_f01_04(ctx: E2EContext) -> None:
    proc = ctx.start_fresh_server("f01_hashstr")
    c: TelnetClient | None = None
    try:
        c = ctx.make_client(port=proc.port)
        _assert_login(c, IMM_NAME, IMM_PASS)

        h1 = c.cmd("memory hash")
        assert "Hash strings allocated:" in h1 and "Bytes saved  :" in h1, f"Unexpected memory hash: {h1!r}"

        for _ in range(5):
            assert "Ok." in c.cmd("oinvoke 10311")

        chk = c.cmd("memory check a glowrod")
        assert "Links:" in chk and "Length:" in chk, f"Expected interned 'a glowrod': {chk!r}"
        m_links = re.search(r"Links:\s*(\d+)", chk)
        assert m_links and int(m_links.group(1)) >= 5, f"Expected Links >= 5, got: {chk!r}"

        c.cmd("drop all")
        c.cmd("purge")
        h2 = c.cmd("memory hash")
        assert "Hash strings allocated:" in h2

        _clean_quit(c)
        c = None
        _shutdown_and_assert_clean(ctx, proc)
    finally:
        _clean_quit(c)
        _safe_stop_proc(proc)


@test_case(
    "T1-F01-05",
    1,
    "F01",
    "New character creation, authorize, save, quit, and free_char LSan cleanliness",
    requires_server=True,
    fresh_server=True,
)
def test_t1_f01_05(ctx: E2EContext) -> None:
    proc = ctx.start_fresh_server("f01_char_free")
    imm: TelnetClient | None = None
    plr: TelnetClient | None = None
    try:
        imm = ctx.make_client(port=proc.port)
        _assert_login(imm, IMM_NAME, IMM_PASS)

        plr = ctx.make_client(port=proc.port)
        _assert_create_character(plr, "Hashalpha", "PassAlpha1")

        _authorize_player(imm, "Hashalpha")
        plr.drain(0.3)

        save_out = plr.cmd("save")
        assert "Ok." in save_out, f"save failed: {save_out!r}"
        pfile = proc.work_dir / "player" / "h" / "Hashalpha"
        assert pfile.is_file(), f"Missing saved player file {pfile} (save_char_obj failed)"

        _clean_quit(plr)
        plr = None
        _clean_quit(imm)
        imm = None

        _shutdown_and_assert_clean(ctx, proc)
    finally:
        _clean_quit(plr)
        _clean_quit(imm)
        _safe_stop_proc(proc)


# ===========================================================================
# Feature F02: BitSet / FLAG_SET & Flag Tables
# ===========================================================================


@test_case(
    "T1-F02-01",
    1,
    "F02",
    "Static flag_domain scanner parity (0 BitSet/bitmask mismatches, 0 signed shift UB)",
    requires_server=False,
    fresh_server=False,
)
def test_t1_f02_01(ctx: E2EContext) -> None:
    res = ctx.run_static_scanners(checks=["flag_domain"], mode="post-remediation")
    r = res["scanners"]["flag_domain"]
    s = r["summary"]
    assert s["active_bitset_passed_bitmask_count"] == 0, (
        f"active_bitset_passed_bitmask_count={s['active_bitset_passed_bitmask_count']}"
    )
    assert s["active_legacy_bitop_on_bitset_count"] == 0, (
        f"active_legacy_bitop_on_bitset_count={s['active_legacy_bitop_on_bitset_count']}"
    )
    assert s["active_signed_shift_ub_count"] == 0, (
        f"active_signed_shift_ub_count={s['active_signed_shift_ub_count']}"
    )
    assert s["structural_issues_count"] == 0, (
        f"structural_issues_count={s['structural_issues_count']}: {r['findings']['structural_issues']}"
    )
    assert r["passed"] is True, f"flag_domain failed: {s}"


@test_case(
    "T1-F02-02",
    1,
    "F02",
    "oset and rset valid flag set/toggle reflected in ostat/rstat",
    requires_server=True,
    fresh_server=False,
)
def test_t1_f02_02(ctx: E2EContext) -> None:
    c: TelnetClient | None = None
    try:
        c = ctx.make_client()
        _assert_login(c, IMM_NAME, IMM_PASS)

        assert "Ok." in c.cmd("oinvoke 10311")
        c.cmd("oset glowrod flags hum")
        o1 = c.cmd("ostat glowrod")
        assert "hum" in o1.lower(), f"Expected Hum flag set: {o1!r}"

        c.cmd("oset glowrod flags hum")
        o2 = c.cmd("ostat glowrod")
        flags_line = next((ln for ln in o2.splitlines() if "Object Flags:" in ln), "")
        assert flags_line, f"Missing 'Object Flags:' header in ostat output: {o2!r}"
        assert "hum" not in flags_line.lower(), f"Expected Hum flag toggled off: {flags_line!r}"

        _goto_checked(c, 102)
        c.cmd("rset 102 flags pet_shop")
        r1 = c.cmd("rstat 102")
        rflags_line = next((ln for ln in r1.splitlines() if "Room flags:" in ln), "")
        assert rflags_line, f"Missing 'Room flags:' header in rstat output: {r1!r}"
        assert "pet_shop" in rflags_line.lower(), f"Expected pet_shop bit 12 set: {r1!r}"

        c.cmd("redit2 start")
        c.cmd("flags -pet_shop")
        c.cmd("stop save")
        r2 = c.cmd("rstat 102")
        rflags_line2 = next((ln for ln in r2.splitlines() if "Room flags:" in ln), "")
        assert rflags_line2, f"Missing 'Room flags:' header in rstat output: {r2!r}"
        assert "pet_shop" not in rflags_line2.lower(), f"Expected pet_shop cleared: {rflags_line2!r}"
    finally:
        if c is not None and c.sock is not None and not c.eof:
            try:
                c.cmd("goto 102", timeout=5.0)
                rchk = c.cmd("rstat 102", timeout=5.0)
                if "pet_shop" in rchk.lower():
                    c.cmd("redit2 start", timeout=5.0)
                    c.cmd("flags -pet_shop", timeout=5.0)
                    c.cmd("stop save", timeout=5.0)
            except Exception:
                pass
        _restore_immtest(c, purge_items=("glowrod",))
        _clean_quit(c)


@test_case(
    "T1-F02-03",
    1,
    "F02",
    "track traverses ROOM_NO_MOB rooms without corrupting no_mob flag",
    requires_server=True,
    fresh_server=False,
)
def test_t1_f02_03(ctx: E2EContext) -> None:
    c: TelnetClient | None = None
    try:
        c = ctx.make_client()
        _assert_login(c, IMM_NAME, IMM_PASS)
        _ensure_chadoyn_present(c)

        c.cmd("sset Immtest track 100")
        _goto_checked(c, 10300)
        pre_stat = c.cmd(
            "rstat 10300",
            timeout=5.0,
            prompt_pattern=re.compile(r"Room flags:.*Health:\s*\d+/\d+\s+Movement:\s*\d+/\d+\s*>", re.DOTALL),
        )
        assert "no_mob" in pre_stat.lower(), f"Expected no_mob on 10300: {pre_stat!r}"

        _goto_checked(c, 10316)
        tout = c.cmd("track chadoyn", timeout=5.0, require_prompt=True)
        assert "You sense a trail east from here" in tout, f"track across no_mob failed: {tout!r}"

        post_stat_10300 = c.cmd(
            "rstat 10300",
            timeout=5.0,
            prompt_pattern=re.compile(r"Room flags:.*Health:\s*\d+/\d+\s+Movement:\s*\d+/\d+\s*>", re.DOTALL),
        )
        post_stat_10316 = c.cmd(
            "rstat 10316",
            timeout=5.0,
            prompt_pattern=re.compile(r"Room flags:.*Health:\s*\d+/\d+\s+Movement:\s*\d+/\d+\s*>", re.DOTALL),
        )
        assert "no_mob" in post_stat_10300.lower(), f"no_mob corrupted on 10300: {post_stat_10300!r}"
        assert "no_mob" in post_stat_10316.lower(), f"no_mob corrupted on 10316: {post_stat_10316!r}"
    finally:
        _restore_immtest(c)
        _clean_quit(c)


@test_case(
    "T1-F02-04",
    1,
    "F02",
    "compare detects wear-slot overlap above bit 0 via intersects_excluding(..., ITEM_TAKE)",
    requires_server=True,
    fresh_server=False,
)
def test_t1_f02_04(ctx: E2EContext) -> None:
    c: TelnetClient | None = None
    try:
        c = ctx.make_client()
        _assert_login(c, IMM_NAME, IMM_PASS)

        assert "Ok." in c.cmd("oinvoke 10308")
        c.cmd("wear vest")
        assert "Ok." in c.cmd("oinvoke 10308")
        assert "Ok." in c.cmd("oinvoke 10301")

        out_match = c.cmd("compare vest")
        assert "You aren't wearing anything comparable." not in out_match, (
            f"1-arg compare failed to match worn body armor above bit 0: {out_match!r}"
        )
        assert "vest" in out_match.lower(), f"Unexpected compare vest output: {out_match!r}"

        out_nomatch = c.cmd("compare boots")
        assert "You aren't wearing anything comparable." in out_nomatch, (
            f"compare boots falsely matched worn body armor on ITEM_TAKE bit 0: {out_nomatch!r}"
        )
    finally:
        _restore_immtest(c, unwear_items=("vest",), purge_items=("vest", "vest", "boots"))
        _clean_quit(c)


@test_case(
    "T1-F02-05",
    1,
    "F02",
    "alinks prints cross-area room flags (no_mob, private, solitary) above bit 0",
    requires_server=True,
    fresh_server=False,
)
def test_t1_f02_05(ctx: E2EContext) -> None:
    c: TelnetClient | None = None
    try:
        c = ctx.make_client()
        _assert_login(c, IMM_NAME, IMM_PASS)

        out = c.cmd("alinks limbo.are")
        assert "Listing all links from" in out and "Found" in out, f"Unexpected alinks output: {out!r}"
        assert any(tok in out.lower() for tok in ("no_mob", "private", "solitary")), (
            f"Expected bit>0 room flags in alinks output: {out!r}"
        )
    finally:
        _clean_quit(c)


# ===========================================================================
# Feature F03: WILDERNESS_* Removal & GameContext Reentrancy
# ===========================================================================


@test_case(
    "T1-F03-01",
    1,
    "F03",
    "Clean server boot with zero wilderness errors or uninitialized pointer traps",
    requires_server=True,
    fresh_server=True,
)
def test_t1_f03_01(ctx: E2EContext) -> None:
    proc = ctx.start_fresh_server("f03_boot_wild")
    try:
        bm = proc.boot_metrics
        assert bm is not None and bm.ready is True, f"Server failed to boot cleanly: {bm}"
        boot_text = proc.read_boot_log().lower()
        assert "wilderness" not in boot_text and "wild_cache" not in boot_text, (
            f"Unexpected wilderness log output during boot: {boot_text[-500:]!r}"
        )
        _shutdown_and_assert_clean(ctx, proc)
    finally:
        _safe_stop_proc(proc)


@test_case(
    "T1-F03-02",
    1,
    "F03",
    "Multi-player concurrent login, independent state, and save across distinct initials",
    requires_server=True,
    fresh_server=False,
)
def test_t1_f03_02(ctx: E2EContext) -> None:
    imm: TelnetClient | None = None
    c1: TelnetClient | None = None
    c2: TelnetClient | None = None
    c1b: TelnetClient | None = None
    c2b: TelnetClient | None = None
    try:
        imm = ctx.make_client()
        _assert_login(imm, IMM_NAME, IMM_PASS)

        c1 = ctx.make_client()
        _assert_create_character(c1, "Mplrone", "PassOne1")
        _authorize_player(imm, "Mplrone")
        c2 = ctx.make_client()
        _assert_create_character(c2, "Zplrtwo", "PassTwo2")
        _authorize_player(imm, "Zplrtwo")
        c1.drain(0.2)
        c2.drain(0.2)

        c1.send_line("save")
        c2.send_line("save")
        _read_until_assert(c1, r"Ok\.", timeout=8.0, desc="Mplrone save Ok.")
        _read_until_assert(c2, r"Ok\.", timeout=8.0, desc="Zplrtwo save Ok.")

        who_out = imm.cmd("who")
        assert "Mplrone" in who_out and "Zplrtwo" in who_out, f"Missing players in who: {who_out!r}"

        assert ctx._shared_work_dir is not None
        assert (ctx._shared_work_dir / "player" / "m" / "Mplrone").is_file(), (
            "player/m/Mplrone missing on disk"
        )
        assert (ctx._shared_work_dir / "player" / "z" / "Zplrtwo").is_file(), (
            "player/z/Zplrtwo missing on disk"
        )

        _clean_quit(c1)
        c1 = None
        _clean_quit(c2)
        c2 = None

        c1b = ctx.make_client()
        _assert_login(c1b, "Mplrone", "PassOne1")
        s1 = c1b.cmd("score")
        assert "Mplrone" in s1, f"Unexpected Mplrone score: {s1!r}"

        c2b = ctx.make_client()
        _assert_login(c2b, "Zplrtwo", "PassTwo2")
        s2 = c2b.cmd("score")
        assert "Zplrtwo" in s2, f"Unexpected Zplrtwo score: {s2!r}"
    finally:
        _clean_quit(c1)
        _clean_quit(c2)
        _clean_quit(c1b)
        _clean_quit(c2b)
        _clean_quit(imm)


@test_case(
    "T1-F03-03",
    1,
    "F03",
    "Storeroom (ROOM_CLANSTOREROOM) save and reboot reload (save_storeroom/load_storerooms)",
    requires_server=True,
    fresh_server=True,
)
def test_t1_f03_03(ctx: E2EContext) -> None:
    proc = ctx.start_fresh_server("f03_storeroom")
    proc2: SwripProcess | None = None
    c: TelnetClient | None = None
    c2: TelnetClient | None = None
    try:
        ws = proc.work_dir
        c = ctx.make_client(port=proc.port)
        _assert_login(c, IMM_NAME, IMM_PASS)

        # Room #122 in omni_complex has ROOM_CLANSTOREROOM (8913160 = HOTEL|CLANSTOREROOM|INDOORS)
        _goto_checked(c, 122)
        assert "Ok." in c.cmd("oinvoke 10311", require_prompt=True)
        drop_out = c.cmd("drop glowrod", require_prompt=True)
        assert "glowrod" in drop_out.lower(), f"drop failed: {drop_out!r}"
        assert "Ok." in c.cmd("save", require_prompt=True)

        assert (ws / "storeroom" / "122").is_file(), "storeroom/122 was not written on drop"

        _clean_quit(c)
        c = None
        _shutdown_and_assert_clean(ctx, proc)

        proc2 = SwripProcess(ws)
        proc2.start()
        ctx._processes.append(proc2)
        bm2 = proc2.wait_ready(15.0)
        assert bm2.ready is True, f"Reboot failed: {bm2.log_tail}"
        assert not any("storeroom" in b.lower() for b in bm2.bug_lines), (
            f"Storeroom BUG lines on reboot: {bm2.bug_lines}"
        )

        c2 = ctx.make_client(port=proc2.port)
        _assert_login(c2, IMM_NAME, IMM_PASS)
        _goto_checked(c2, 122)
        look_out = c2.cmd("look", require_prompt=True)
        assert "glowrod" in look_out.lower(), f"glowrod missing from storeroom after reboot: {look_out!r}"

        _clean_quit(c2)
        c2 = None
        _shutdown_and_assert_clean(ctx, proc2)
    finally:
        _clean_quit(c)
        _clean_quit(c2)
        _safe_stop_proc(proc)
        _safe_stop_proc(proc2)


@test_case(
    "T1-F03-04",
    1,
    "F03",
    "PC corpse write and reboot reload (write_corpses / load_corpses)",
    requires_server=True,
    fresh_server=True,
)
def test_t1_f03_04(ctx: E2EContext) -> None:
    proc = ctx.start_fresh_server("f03_corpse")
    proc2: SwripProcess | None = None
    imm: TelnetClient | None = None
    vic: TelnetClient | None = None
    c2: TelnetClient | None = None
    try:
        ws = proc.work_dir
        imm = ctx.make_client(port=proc.port)
        _assert_login(imm, IMM_NAME, IMM_PASS)

        vic = ctx.make_client(port=proc.port)
        _assert_create_character(vic, "Corpsea", "CorpsePass1")
        _authorize_player(imm, "Corpsea")
        vic.drain(0.2)

        _goto_checked(imm, 10308)
        imm.cmd("transfer Corpsea 10308")
        slay_out = _cmd_no_crash(proc, imm, "slay Corpsea")
        assert "You slay Corpsea in cold blood!" in slay_out, f"slay failed: {slay_out!r}"
        vic.close()
        vic = None

        assert (ws / "corpses" / "Corpsea").is_file(), "corpses/Corpsea was not written"

        _clean_quit(imm)
        imm = None
        _shutdown_and_assert_clean(ctx, proc)

        proc2 = SwripProcess(ws)
        proc2.start()
        ctx._processes.append(proc2)
        bm2 = proc2.wait_ready(15.0)
        assert bm2.ready is True, f"Reboot failed: {bm2.log_tail}"
        assert not any("corpse" in b.lower() for b in bm2.bug_lines), (
            f"Corpse BUG lines on reboot: {bm2.bug_lines}"
        )

        c2 = ctx.make_client(port=proc2.port)
        _assert_login(c2, IMM_NAME, IMM_PASS)
        _goto_checked(c2, 10308)
        look_out = c2.cmd("look")
        assert "corpse of Corpsea" in look_out, f"Corpse missing on reboot: {look_out!r}"

        _clean_quit(c2)
        c2 = None
        _shutdown_and_assert_clean(ctx, proc2)
    finally:
        _clean_quit(vic)
        _clean_quit(imm)
        _clean_quit(c2)
        _safe_stop_proc(proc)
        _safe_stop_proc(proc2)


@test_case(
    "T1-F03-05",
    1,
    "F03",
    "GameContext clean shutdown mud now lifecycle and zero-leak exit",
    requires_server=True,
    fresh_server=True,
)
def test_t1_f03_05(ctx: E2EContext) -> None:
    proc = ctx.start_fresh_server("f03_shutdown")
    c: TelnetClient | None = None
    try:
        c = ctx.make_client(port=proc.port)
        _assert_login(c, IMM_NAME, IMM_PASS)
        assert len(c.cmd("look").strip()) > 0
        assert "Areas" in c.cmd("memory")
        assert IMM_NAME in c.cmd("who")
        assert "Ok." in c.cmd("save")
        _clean_quit(c)
        c = None

        metrics = proc.shutdown_clean(IMM_NAME, IMM_PASS)
        ctx.assert_clean_sanitizers(metrics)
        assert metrics.clean_exit is True
        assert metrics.exit_code == 0
        assert metrics.normal_termination is True
        assert metrics.used_fallback_kill is False
    finally:
        _clean_quit(c)
        _safe_stop_proc(proc)


# ===========================================================================
# Feature F04: TCP Socket Loop, nanny Auth, DES->SHA-256 & id.c
# ===========================================================================


@test_case(
    "T1-F04-01",
    1,
    "F04",
    "Clean TCP connect, greeting, Immortal login, and quit",
    requires_server=True,
    fresh_server=False,
)
def test_t1_f04_01(ctx: E2EContext) -> None:
    c: TelnetClient | None = None
    try:
        c = ctx.make_client()
        transcript = _assert_login(c, IMM_NAME, IMM_PASS)
        clean_tr = strip_ansi(transcript)
        assert ("Name:" in clean_tr or "enter your name:" in clean_tr.lower()) and DEFAULT_PROMPT_RE.search(
            clean_tr
        ), f"Unexpected login transcript: {clean_tr!r}"
        out = c.cmd("look")
        assert len(out.strip()) > 0 and "Huh?" not in out
        c.send_line("quit")
        quit_out = c.drain(0.6)
        assert c.eof is True or "good things must come to an end" in strip_ansi(quit_out).lower()
    finally:
        _clean_quit(c)


@test_case(
    "T1-F04-02",
    1,
    "F04",
    "Legacy DES crypt(3) login upgrades to SHA-256 and cold-reconnects",
    requires_server=True,
    fresh_server=True,
)
def test_t1_f04_02(ctx: E2EContext) -> None:
    proc = ctx.start_fresh_server("f04-des", seed_immortal=True, seed_legacy_des=True)
    c1: TelnetClient | None = None
    c2: TelnetClient | None = None
    try:
        pfile = proc.work_dir / "player" / "l" / "Legacydes"
        assert pfile.is_file()
        pre_text = pfile.read_text(encoding="utf-8", errors="replace")
        expected_des = des_crypt("LegacyPass1", "Da")
        assert f"Password     {expected_des}~" in pre_text

        c1 = ctx.make_client(port=proc.port)
        tr1 = _assert_login(c1, "Legacydes", "LegacyPass1")
        assert "Welcome to Rise in Power" in strip_ansi(tr1) or DEFAULT_PROMPT_RE.search(strip_ansi(tr1))
        save_out = c1.cmd("save")
        assert "Ok." in save_out, f"save failed: {save_out!r}"
        _clean_quit(c1)
        c1 = None

        post_text = pfile.read_text(encoding="utf-8", errors="replace")
        m_pw = re.search(r"Password\s+([^\s~]+)~", post_text)
        assert m_pw is not None, f"Password line missing in {pfile}"
        expected_sha = sha256_hash("LegacyPass1")
        assert m_pw.group(1) == expected_sha, (
            f"Expected DES upgrade to SHA-256 ({expected_sha}), got {m_pw.group(1)!r}"
        )

        c2 = ctx.make_client(port=proc.port)
        _assert_login(c2, "Legacydes", "LegacyPass1")
        look2 = c2.cmd("look")
        assert len(look2.strip()) > 0
        _clean_quit(c2)
        c2 = None

        _shutdown_and_assert_clean(ctx, proc)
    finally:
        _clean_quit(c1)
        _clean_quit(c2)
        _safe_stop_proc(proc)


@test_case(
    "T1-F04-04",
    1,
    "F04",
    "New character creation nanny FSM and id.c non-leakage on player socket",
    requires_server=True,
    fresh_server=False,
)
def test_t1_f04_04(ctx: E2EContext) -> None:
    c: TelnetClient | None = None
    try:
        c = ctx.make_client()
        tr = _assert_create_character(
            c, "Nannyhappy", "ValidPass1", sex="M", race="Human", ability="combat", ansi="A"
        )
        c.drain(0.3)
        look_out = c.cmd("look")
        assert DEFAULT_PROMPT_RE.search(strip_ansi(tr))
        assert "Academy" in look_out or len(look_out.strip()) > 20, f"Unexpected look: {look_out!r}"
        assert re.search(r"^\s*\d+\s*,\s*\d+\s*$", strip_ansi(c.all_text), re.MULTILINE) is None, (
            f"id.c send_auth leaked ident query onto player socket: {c.all_text!r}"
        )
    finally:
        _clean_quit(c)


@test_case(
    "T1-F04-05",
    1,
    "F04",
    "Reconnect kick replaces active session cleanly",
    requires_server=True,
    fresh_server=False,
)
def test_t1_f04_05(ctx: E2EContext) -> None:
    c1: TelnetClient | None = None
    c2: TelnetClient | None = None
    try:
        c1 = ctx.make_client()
        _assert_login(c1, IMM_NAME, IMM_PASS)
        c1.cmd("look")

        c2 = ctx.make_client()
        _read_until_assert(c2, r"(?:Please enter your name:|Name:)\s*$", desc="Name prompt")
        c2.send_line(IMM_NAME)
        _read_until_assert(c2, r"Password:\s*", desc="Password prompt")
        c2.send_line(IMM_PASS)
        tr2 = _read_until_assert(
            c2,
            re.compile(r"(?:Already playing|Reconnecting|Health:\s*\d+/\d+\s+Movement:\s*\d+/\d+\s*>)"),
            timeout=5.0,
            desc="reconnect prompt",
        )
        if "Health:" not in strip_ansi(tr2):
            tr2 += _read_until_assert(c2, DEFAULT_PROMPT_RE, timeout=5.0, desc="reconnect game prompt")

        c1.drain(0.25)
        look2 = c2.cmd("look")

        assert "Kicking off old connection" in strip_ansi(c1.all_text)
        assert c1.eof is True, "Expected old connection c1 to be closed"
        tr2_clean = strip_ansi(tr2)
        assert "Already playing" in tr2_clean or "Reconnecting" in tr2_clean, f"Unexpected tr2: {tr2_clean!r}"
        assert len(look2.strip()) > 0
    finally:
        _clean_quit(c1)
        _clean_quit(c2)


# ===========================================================================
# Feature F05: Telnet FSM, MCCP2, UTF-8/Color, Pager & MSSP
# ===========================================================================


@test_case(
    "T1-F05-01",
    1,
    "F05",
    "Telnet handshake emits 0 NUL bytes, single WILL EOR, and negotiates TTYPE/NAWS/SGA/EOR",
    requires_server=True,
    fresh_server=False,
)
def test_t1_f05_01(ctx: E2EContext) -> None:
    c: TelnetClient | None = None
    try:
        c = ctx.make_client(
            auto_negotiate_ttype=True,
            auto_negotiate_naws=True,
            auto_negotiate_sga=True,
            auto_negotiate_eor=True,
            ttype_list=["SWRIP-E2E-UTF8", "xterm-256color", "ANSI"],
            naws_size=(100, 35),
        )
        _read_until_assert(c, r"(?:Please enter your name:|Name:)\s*$", desc="Name prompt")
        c.send_line(IMM_NAME)
        _read_until_assert(c, r"Password:\s*", desc="Password prompt")
        c.send_line(IMM_PASS)
        stage = _read_until_assert(
            c, r"(?:Press enter\.\.\.|Welcome to Rise in Power !!|Press \[ENTER\])", desc="title screen"
        )
        c.send_line("")
        stage2 = _read_until_assert(
            c, r"(?:Press \[ENTER\]|Health:\s*\d+/\d+\s+Movement:\s*\d+/\d+\s*>)", desc="MOTD/prompt"
        )
        if "Press [ENTER]" in strip_ansi(stage2):
            c.send_line("")
            _read_until_assert(c, DEFAULT_PROMPT_RE, desc="prompt")

        c.cmd("look")

        assert c.raw_null_byte_count == 0 and c.null_byte_count == 0, (
            f"Unexpected NUL bytes in handshake: raw={c.raw_null_byte_count}, plain={c.null_byte_count}"
        )
        assert c.will_eor_count == 1, f"Expected single IAC WILL EOR, got {c.will_eor_count}"
        assert c.server_options.get(TELOPT_SGA) is True
        assert c.server_options.get(TELOPT_EOR) is True
        assert c.client_options.get(TELOPT_NAWS) is True
        assert c.client_options.get(TELOPT_TTYPE) is True
        assert ("SB", TELOPT_TTYPE, bytes((TTYPE_SEND,))) in c.iac_events
        assert c.eor_enabled is True and c.eor_count >= 1
    finally:
        _clean_quit(c)


@test_case(
    "T1-F05-02",
    1,
    "F05",
    "MSSP reports server metadata with PORT matching actual listening TCP port",
    requires_server=True,
    fresh_server=False,
)
def test_t1_f05_02(ctx: E2EContext) -> None:
    c: TelnetClient | None = None
    try:
        c = ctx.make_client()
        _read_until_assert(c, r"(?:Please enter your name:|Name:)\s*$", desc="Name prompt")
        mssp = c.request_mssp(timeout=5.0)
        assert mssp.get("NAME") == "SWRIP2", f"Unexpected MSSP NAME: {mssp}"
        assert mssp.get("PORT") == str(ctx.shared_server.port), (
            f"MSSP PORT mismatch: expected {ctx.shared_server.port}, got {mssp.get('PORT')!r}"
        )
        assert mssp.get("GMCP") == "1" and mssp.get("MCCP") == "1" and mssp.get("ANSI") == "1", (
            f"Unexpected MSSP flags: {mssp}"
        )
        assert int(mssp.get("AREAS", "0")) >= 5 and int(mssp.get("ROOMS", "0")) > 0, f"Bad counts: {mssp}"
        assert "SWRIP2" in c.mssp_multi.get("CODEBASE", []), f"Unexpected CODEBASE: {c.mssp_multi}"
    finally:
        if c is not None:
            c.close()


@test_case(
    "T1-F05-03",
    1,
    "F05",
    "MCCP2 activates zlib stream and DONT COMPRESS2 cleanly emits Z_STREAM_END",
    requires_server=True,
    fresh_server=False,
)
def test_t1_f05_03(ctx: E2EContext) -> None:
    c: TelnetClient | None = None
    try:
        c = ctx.make_client()
        _assert_login(c, IMM_NAME, IMM_PASS)

        try:
            assert c.enable_mccp2(wait=True, timeout=3.0), "MCCP2 failed to activate"
            look1 = c.cmd("look")
            stat1 = c.cmd("mccpstat")

            assert c.disable_mccp2(wait=True, timeout=3.0), "MCCP2 failed to deactivate cleanly"
            look2 = c.cmd("look")
            stat2 = c.cmd("mccpstat")
        except RuntimeError as exc:
            raise AssertionError(f"MCCP2 zlib stream failure: {exc}") from exc

        assert c.mccp2_activations == 1 and c.mccp2_deactivations == 1
        assert c.mccp2_active is False
        assert "MCCP stats:" in stat1 and len(look1.strip()) > 0
        assert "MCCP is not active." in stat2 and len(look2.strip()) > 0
    finally:
        _clean_quit(c)


@test_case(
    "T1-F05-04",
    1,
    "F05",
    "UTF-8 multi-byte text and ANSI &/^ color codes vs config -ansi",
    requires_server=True,
    fresh_server=False,
)
def test_t1_f05_04(ctx: E2EContext) -> None:
    c: TelnetClient | None = None
    try:
        c = ctx.make_client()
        _assert_login(c, IMM_NAME, IMM_PASS)

        c.cmd("config +ansi")
        u_out = c.cmd("say é 日本語 🚀")
        assert "é 日本語 🚀" in u_out and "\ufffd" not in u_out, f"UTF-8 mangled: {u_out!r}"

        cur = len(c.all_text)
        c_out = c.cmd("say &rRed ^bBlue && ^^ &xEnd")
        raw_c = c.all_text[cur:]
        assert "\x1b[" in raw_c and "31" in raw_c and "44" in raw_c, f"Missing ANSI SGR in {raw_c!r}"
        assert "Red Blue & ^ End" in c_out, f"Color/escape rendering mismatch: {c_out!r}"

        c.cmd("config -ansi")
        cur2 = len(c.all_text)
        nc_out = c.cmd("say &rPlain ^bText && ^^")
        raw_nc = c.all_text[cur2:]
        assert "\x1b[" not in raw_nc, f"ANSI escape leaked with config -ansi: {raw_nc!r}"
        assert "Plain Text & ^" in nc_out, f"Expected plain text without color codes: {nc_out!r}"
    finally:
        _restore_immtest(c, restore_ansi=True)
        _clean_quit(c)


@test_case(
    "T1-F05-05",
    1,
    "F05",
    "Help pager pauses long help output and supports continue and quit",
    requires_server=True,
    fresh_server=False,
)
def test_t1_f05_05(ctx: E2EContext) -> None:
    c: TelnetClient | None = None
    try:
        c = ctx.make_client()
        _assert_login(c, IMM_NAME, IMM_PASS)
        c.cmd("config +pager")

        c.send_line("help prompt")
        p1 = _read_until_assert(
            c, r"\(C\)ontinue,\s*\(R\)efresh,\s*\(B\)ack,\s*\(Q\)uit:\s*\[C\]", timeout=5.0, desc="pager prompt 1"
        )
        assert "PROMPT" in strip_ansi(p1) and "(C)ontinue" in strip_ansi(p1)

        c.send_line("")
        p2 = _read_until_assert(c, DEFAULT_PROMPT_RE, timeout=5.0, desc="pager continue end")
        assert len(strip_ansi(p2).strip()) > 20

        c.send_line("help prompt")
        _read_until_assert(
            c, r"\(C\)ontinue,\s*\(R\)efresh,\s*\(B\)ack,\s*\(Q\)uit:\s*\[C\]", timeout=5.0, desc="pager prompt 2"
        )
        c.send_line("q")
        p4 = _read_until_assert(c, DEFAULT_PROMPT_RE, timeout=5.0, desc="pager quit prompt")
        assert len(strip_ansi(p4)) < len(strip_ansi(p2))
    finally:
        _restore_immtest(c, disable_pager=True)
        _clean_quit(c)


# ===========================================================================
# Feature F06: GMCP JSON Telemetry
# ===========================================================================


@test_case(
    "T1-F06-01",
    1,
    "F06",
    "GMCP option negotiation, Core.Hello, and Core.Ping echo",
    requires_server=True,
    fresh_server=False,
)
def test_t1_f06_01(ctx: E2EContext) -> None:
    c: TelnetClient | None = None
    try:
        c = ctx.make_client()
        _read_until_assert(c, r"(?:Please enter your name:|Name:)\s*$", desc="Name prompt")
        assert c.server_options.get(TELOPT_GMCP) is True, "Server did not send IAC WILL GMCP"

        c.enable_gmcp()
        c.drain(0.2)
        c.send_gmcp("Core.Hello", {"client": "SwripE2E", "version": "1.0"})
        c.send_gmcp("Core.Ping")
        c.drain(0.4)

        pings = [ev for ev in c.gmcp_events if ev.module == "Core.Ping"]
        assert pings, f"No Core.Ping GMCP response received: {c.gmcp_events}"
        assert pings[-1].json_valid is True and pings[-1].data == {}, f"Bad Core.Ping payload: {pings[-1]}"
    finally:
        if c is not None:
            c.close()


@test_case(
    "T1-F06-02",
    1,
    "F06",
    "GMCP login resync emits RFC 8259 JSON for Room.Info, Char.Vitals, and Char.Status",
    requires_server=True,
    fresh_server=False,
)
def test_t1_f06_02(ctx: E2EContext) -> None:
    c: TelnetClient | None = None
    try:
        c = ctx.make_client(timeout=5.0)
        _login_with_pre_options(c, IMM_NAME, IMM_PASS, gmcp=True)
        c.drain(0.25)

        assert len(c.gmcp_events) >= 3, f"Expected >=3 GMCP resync events, got {c.gmcp_events}"
        _assert_all_gmcp_valid(c)

        ri = _latest_gmcp(c, "Room.Info")
        cv = _latest_gmcp(c, "Char.Vitals")
        cs = _latest_gmcp(c, "Char.Status")
        assert ri is not None and cv is not None and cs is not None

        assert ri.data["num"] == 100 and isinstance(ri.data["name"], str) and ri.data["name"]
        assert ri.data["rentship"] in (0, 1) and ri.data["serinstop"] in (0, 1)

        for k in ("hp", "maxhp", "force", "maxforce", "move", "maxmove", "credits", "bank", "pkills", "mkills"):
            assert isinstance(cv.data.get(k), int), f"Missing/non-int Char.Vitals[{k!r}]: {cv.data}"

        assert cs.data.get("name") == IMM_NAME
        assert cs.data.get("trust") == 110
        for k in ("fullname", "class", "race", "wimpy", "wanted", "clan"):
            assert k in cs.data, f"Missing Char.Status[{k!r}]: {cs.data}"
        assert isinstance(cs.data["wimpy"], int)
    finally:
        _clean_quit(c)


@test_case(
    "T1-F06-03",
    1,
    "F06",
    "GMCP Char.Stats emitted via Core.Supports and updated on inventory change",
    requires_server=True,
    fresh_server=False,
)
def test_t1_f06_03(ctx: E2EContext) -> None:
    c: TelnetClient | None = None
    try:
        c = ctx.make_client(timeout=5.0)
        _login_with_pre_options(
            c, IMM_NAME, IMM_PASS, gmcp=True, gmcp_supports=["Char.Stats 1"]
        )
        c.drain(0.25)

        _assert_all_gmcp_valid(c)
        cs = _latest_gmcp(c, "Char.Stats")
        assert cs is not None, f"Char.Stats missing after login: {[e.module for e in c.gmcp_events]}"
        for k in (
            "str", "dex", "con", "int", "wis", "cha",
            "hitroll", "damroll", "armor", "align",
            "weight", "maxweight", "numitems", "maxitems",
        ):
            assert k in cs.data, f"Missing Char.Stats[{k!r}] (got keys {list(cs.data.keys())})"

        prev_items = cs.data["numitems"]
        cnt_before = _count_gmcp(c, "Char.Stats")

        assert "Ok." in c.cmd("oinvoke 10311")
        c.drain(0.2)
        _assert_all_gmcp_valid(c)
        assert _count_gmcp(c, "Char.Stats") > cnt_before, "Char.Stats not pushed on obj_to_char"
        cs2 = _latest_gmcp(c, "Char.Stats")
        assert cs2 is not None and cs2.data["numitems"] == prev_items + 1
    finally:
        _restore_immtest(c, purge_items=("glowrod",))
        _clean_quit(c)


@test_case(
    "T1-F06-04",
    1,
    "F06",
    "GMCP Room.Info and Char.Vitals push live deltas on movement and restore",
    requires_server=True,
    fresh_server=False,
)
def test_t1_f06_04(ctx: E2EContext) -> None:
    c: TelnetClient | None = None
    try:
        c = ctx.make_client(timeout=5.0)
        _login_with_pre_options(c, IMM_NAME, IMM_PASS, gmcp=True)
        c.drain(0.25)

        _goto_checked(c, 10300)
        c.drain(0.2)
        c.cmd("n")
        c.drain(0.2)
        c.cmd("restore Immtest")
        c.drain(0.2)

        _assert_all_gmcp_valid(c)
        room_nums = [e.data["num"] for e in c.gmcp_events if e.module == "Room.Info" and isinstance(e.data, dict)]
        assert 10300 in room_nums and 10301 in room_nums, f"Expected 10300 and 10301 in Room.Info: {room_nums}"
        assert room_nums.index(10300) < len(room_nums) - 1

        vitals = [e.data for e in c.gmcp_events if e.module == "Char.Vitals" and isinstance(e.data, dict)]
        assert len(vitals) >= 2
        assert vitals[-1]["move"] == vitals[-1]["maxmove"], f"Expected restore to reset move==maxmove: {vitals[-1]}"
    finally:
        if c is not None and c.sock is not None and not c.eof:
            try:
                c.cmd("restore Immtest", timeout=5.0)
            except Exception:
                pass
        _restore_immtest(c)
        _clean_quit(c)


@test_case(
    "T1-F06-05",
    1,
    "F06",
    "Core.Supports.Remove and Core.Supports.Set control GMCP subscription stream",
    requires_server=True,
    fresh_server=False,
)
def test_t1_f06_05(ctx: E2EContext) -> None:
    c: TelnetClient | None = None
    try:
        c = ctx.make_client(timeout=5.0)
        _login_with_pre_options(c, IMM_NAME, IMM_PASS, gmcp=True)
        c.drain(0.25)

        c.send_gmcp("Core.Supports.Set", ["Room 1", "Char 1"])
        c.drain(0.2)
        room_count = _count_gmcp(c, "Room.Info")
        _goto_checked(c, 10300)
        c.drain(0.2)
        assert _count_gmcp(c, "Room.Info") > room_count, "Room package did not emit Room.Info"

        c.send_gmcp("Core.Supports.Remove", ["Room"])
        c.drain(0.2)
        room_cnt_1 = _count_gmcp(c, "Room.Info")
        _goto_checked(c, 100)
        c.drain(0.2)
        assert _count_gmcp(c, "Room.Info") == room_cnt_1, "Room.Info still emitted after removing Room"

        c.send_gmcp("Core.Supports.Set", ["Room 1", "Char 1"])
        c.drain(0.2)
        _goto_checked(c, 10300)
        c.drain(0.2)
        assert _count_gmcp(c, "Room.Info") > room_cnt_1, (
            "Room.Info failed to resume after Core.Supports.Set ['Room 1', 'Char 1'] (gmcp_caps/subs split-brain)"
        )
    finally:
        _restore_immtest(c)
        _clean_quit(c)


# ===========================================================================
# Feature F07: Core DB, Boot, const.c/tables.c & 40-Area area/
# ===========================================================================


@test_case(
    "T1-F07-01",
    1,
    "F07",
    "Clean boot loads 40 areas with zero boot/GSN/language errors",
    requires_server=True,
    fresh_server=True,
)
def test_t1_f07_01(ctx: E2EContext) -> None:
    proc = ctx.start_fresh_server("f07-boot")
    c: TelnetClient | None = None
    c_login: TelnetClient | None = None
    try:
        bm = proc.boot_metrics
        assert bm is not None and bm.ready is True
        if bm.area_count is not None:
            assert bm.area_count == 40, f"Expected 40 loaded areas, got {bm.area_count}"
        assert bm.assign_gsn_failures == [], f"ASSIGN_GSN failures: {bm.assign_gsn_failures}"
        assert bm.init_language_failures == [], f"init_language_sn failures: {bm.init_language_failures}"
        assert bm.bug_lines == [], f"Boot BUG lines: {bm.bug_lines}"

        c = ctx.make_client(port=proc.port)
        _read_until_assert(c, r"(?:Please enter your name:|Name:)\s*$", desc="Name prompt")
        mssp = c.request_mssp(timeout=5.0)
        assert int(mssp.get("AREAS", "0")) == 40, f"Expected MSSP AREAS=40, got {mssp.get('AREAS')!r}"
        c.close()
        c = None

        c_login = ctx.make_client(port=proc.port)
        _assert_login(c_login, IMM_NAME, IMM_PASS)
        mem = c_login.cmd("memory")
        assert re.search(r"\bAreas\s+40\b", mem), f"Expected Areas 40 in memory output: {mem!r}"

        _clean_quit(c_login)
        c_login = None
        _shutdown_and_assert_clean(ctx, proc)
    finally:
        _clean_quit(c)
        _clean_quit(c_login)
        _safe_stop_proc(proc)


@test_case(
    "T1-F07-02",
    1,
    "F07",
    "areas, zones, and memory commands report loaded world tables",
    requires_server=True,
    fresh_server=False,
)
def test_t1_f07_02(ctx: E2EContext) -> None:
    c: TelnetClient | None = None
    try:
        c = ctx.make_client()
        _assert_login(c, IMM_NAME, IMM_PASS)

        areas_out = c.cmd("areas")
        zones_out = c.cmd("zones")
        mem_out = c.cmd("memory")

        assert "Coruscant Academy" in areas_out and "Limbo" in areas_out, f"Missing areas: {areas_out!r}"
        area_row_re = re.compile(r"^[^|]+\|[^|]+\|\s*-?\d+\s*-\s*-?\d+\s*\|\s*-?\d+\s*-\s*-?\d+\s*$")
        area_lines = [ln for ln in strip_ansi(areas_out).splitlines() if area_row_re.match(ln)]
        assert len(area_lines) >= 40, f"Expected >= 40 area rows, got {len(area_lines)}: {areas_out!r}"

        for fname in ("newacad.are", "limbo.are", "templates.are"):
            assert fname in zones_out, f"Missing {fname} in zones output"

        for lbl in ("Affects", "Areas", "Rooms", "IdxMobs", "IdxObjs", "Topsn"):
            assert re.search(rf"{lbl}\s+\d+", mem_out), f"Missing {lbl} count in memory: {mem_out!r}"
    finally:
        _clean_quit(c)


@test_case(
    "T1-F07-03",
    1,
    "F07",
    "commands and slist enumerate wired commands and skills cleanly",
    requires_server=True,
    fresh_server=False,
)
def test_t1_f07_03(ctx: E2EContext) -> None:
    c: TelnetClient | None = None
    try:
        c = ctx.make_client()
        _assert_login(c, IMM_NAME, IMM_PASS)

        cmds_out = c.cmd("commands", require_prompt=True)
        slist_out = c.cmd("slist", require_prompt=True)

        for cmd_name in ("look", "kill", "flee", "consider", "ammo", "buyvendor", "save", "craft"):
            assert re.search(rf"\b{re.escape(cmd_name)}\b", cmds_out), f"Command {cmd_name!r} missing from commands"
        help_cmds = c.cmd("commands help", require_prompt=True)
        assert "No command found under" not in help_cmds and re.search(r"\bhelp\b", help_cmds), (
            f"Command 'help' missing from 'commands help': {help_cmds!r}"
        )

        assert re.search(r"SPELL\s+(?:&\s+)?SKILL\s+LIST", slist_out), f"Missing header: {slist_out!r}"
        assert "** General Skills **" in slist_out and "** combat **" in slist_out
        assert "blasters" in slist_out and "track" in slist_out
    finally:
        _clean_quit(c)


@test_case(
    "T1-F07-04",
    1,
    "F07",
    "wizlist regenerates at boot containing Immtest",
    requires_server=True,
    fresh_server=False,
)
def test_t1_f07_04(ctx: E2EContext) -> None:
    c: TelnetClient | None = None
    try:
        c = ctx.make_client()
        _assert_login(c, IMM_NAME, IMM_PASS)

        wiz = c.cmd("wizlist")
        assert "Masters of Star Wars: Rise in Power!" in wiz, f"Missing wizlist header: {wiz!r}"
        assert "Immtest" in wiz, f"Immtest missing from wizlist (make_wizlist Pcflags 0 break bug): {wiz!r}"
    finally:
        _clean_quit(c)


@test_case(
    "T1-F07-05",
    1,
    "F07",
    "help displays valid topic entries from help.are",
    requires_server=True,
    fresh_server=False,
)
def test_t1_f07_05(ctx: E2EContext) -> None:
    c: TelnetClient | None = None
    try:
        c = ctx.make_client()
        _assert_login(c, IMM_NAME, IMM_PASS)

        h_help = c.cmd("help help")
        h_prac = c.cmd("help practice")
        assert "No help on" not in h_help and len(h_help.strip()) > 40
        assert "PRACTICE" in h_prac.upper() and "No help on" not in h_prac
    finally:
        _clean_quit(c)


# ===========================================================================
# Feature F08: Player Save/Load (a..z), Corpses, Storerooms & Vendors
# ===========================================================================


@test_case(
    "T1-F08-01",
    1,
    "F08",
    "New character creation, authorization, save, and cold-reconnect across a/m/z initials",
    requires_server=True,
    fresh_server=True,
)
def test_t1_f08_01(ctx: E2EContext) -> None:
    proc = ctx.start_fresh_server("f08-az")
    proc2: SwripProcess | None = None
    imm: TelnetClient | None = None
    clients: list[TelnetClient] = []
    try:
        imm = ctx.make_client(port=proc.port)
        _assert_login(imm, IMM_NAME, IMM_PASS)

        chars = [("Aalpha", "PassAlpha1"), ("Mmid", "PassMid1"), ("Zzeta", "PassZeta1")]
        for name, pwd in chars:
            cli = ctx.make_client(port=proc.port)
            clients.append(cli)
            _assert_create_character(cli, name, pwd)
            _authorize_player(imm, name)
            cli.drain(0.2)
            assert "Ok." in cli.cmd("save")
            _clean_quit(cli)

            pfile = proc.work_dir / "player" / name[0].lower() / name
            assert pfile.is_file() and pfile.stat().st_size > 0, (
                f"Player file not created on disk: {pfile}"
            )

        clients.clear()
        _clean_quit(imm)
        imm = None
        _shutdown_and_assert_clean(ctx, proc)

        proc2 = SwripProcess(proc.work_dir)
        proc2.start()
        ctx._processes.append(proc2)
        bm2 = proc2.wait_ready(15.0)
        assert bm2.ready is True, f"Cold reboot failed: {bm2.log_tail}"

        for name, pwd in chars:
            cli2 = ctx.make_client(port=proc2.port)
            clients.append(cli2)
            _assert_login(cli2, name, pwd)
            sc = cli2.cmd("score")
            inv = cli2.cmd("inventory")
            assert name in sc, f"Score missing {name}: {sc!r}"
            assert "vibro-blade" in inv.lower() or "comlink" in inv.lower(), f"Starter gear lost: {inv!r}"
            _clean_quit(cli2)

        clients.clear()
        _shutdown_and_assert_clean(ctx, proc2)
    finally:
        for cli in clients:
            _clean_quit(cli)
        _clean_quit(imm)
        _safe_stop_proc(proc)
        _safe_stop_proc(proc2)


@test_case(
    "T1-F08-03",
    1,
    "F08",
    "WEAR_DISGUISE equipment persists across save and cold reconnect",
    requires_server=True,
    fresh_server=True,
)
def test_t1_f08_03(ctx: E2EContext) -> None:
    proc = ctx.start_fresh_server("f08-disguise")
    imm: TelnetClient | None = None
    try:
        imm = ctx.make_client(port=proc.port)
        _assert_login(imm, IMM_NAME, IMM_PASS)

        _goto_checked(imm, 10300)
        assert "Ok." in imm.cmd("oinvoke 10308")
        imm.cmd("oset vest type disguise")
        imm.cmd("oset vest wear disguise")
        imm.cmd("oedit vest")
        imm.cmd("wearloc 25")
        imm.cmd("stop save")

        ostat_before = imm.cmd("ostat vest", require_prompt=True)
        assert re.search(r"Wear_loc:\s*25\b", ostat_before), (
            f"oedit wearloc 25 failed before save: {ostat_before!r}"
        )
        assert "Ok." in imm.cmd("save", require_prompt=True)
        _clean_quit(imm)
        imm = None

        imm = ctx.make_client(port=proc.port)
        _assert_login(imm, IMM_NAME, IMM_PASS)
        eq_after = imm.cmd("eq", require_prompt=True)
        ostat_after = imm.cmd("ostat vest", require_prompt=True)
        assert "<disguise>" in eq_after, f"WEAR_DISGUISE missing from eq across save/reload: {eq_after!r}"
        assert re.search(r"Wear_loc:\s*25\b", ostat_after), (
            f"WEAR_DISGUISE (25) lost across save/reload: {ostat_after!r}"
        )
        _clean_quit(imm)
        imm = None
        _shutdown_and_assert_clean(ctx, proc)
    finally:
        _clean_quit(imm)
        _safe_stop_proc(proc)


@test_case(
    "T1-F08-04",
    1,
    "F08",
    "Player vendor full lifecycle (buyvendor, placevendor, stock, pricevendor, buy, getcredits, reboot)",
    requires_server=True,
    fresh_server=True,
)
def test_t1_f08_04(ctx: E2EContext) -> None:
    proc = ctx.start_fresh_server("f08-vendor")
    proc2: SwripProcess | None = None
    imm: TelnetClient | None = None
    owner: TelnetClient | None = None
    buyer: TelnetClient | None = None
    try:
        imm = ctx.make_client(port=proc.port)
        _assert_login(imm, IMM_NAME, IMM_PASS)

        owner = ctx.make_client(port=proc.port)
        _assert_create_character(owner, "Vendowner", "VendOwn1")
        _authorize_player(imm, "Vendowner")
        buyer = ctx.make_client(port=proc.port)
        _assert_create_character(buyer, "Vendbuyer", "VendBuy1")
        _authorize_player(imm, "Vendbuyer")
        owner.drain(0.2)
        buyer.drain(0.2)

        _goto_checked(imm, 10300)
        imm.cmd("advance Vendowner combat 55")
        imm.cmd("mset Vendowner credits 2000000")
        imm.cmd("mset Vendbuyer credits 5000")
        assert "Ok." in imm.cmd("oinvoke 10311")
        imm.cmd("give glowrod Vendowner")

        _goto_checked(imm, 10321)
        imm.cmd("transfer Vendowner 10321")
        owner.drain(0.25)
        bv_out = owner.cmd("buyvendor", require_prompt=True)
        assert "contract for a vendor" in bv_out.lower(), f"buyvendor failed: {bv_out!r}"

        _goto_checked(imm, 10630)
        imm.cmd("transfer Vendowner 10630")
        imm.cmd("transfer Vendbuyer 10630")
        owner.drain(0.25)
        buyer.drain(0.25)

        pv_out = owner.cmd("placevendor", require_prompt=True)
        assert "swirl of smoke" in pv_out.lower() or "vendor" in owner.cmd("look", require_prompt=True).lower(), (
            f"placevendor failed: {pv_out!r}"
        )
        owner.cmd("give glowrod vendor", require_prompt=True)
        price_out = owner.cmd("pricevendor glowrod 500", require_prompt=True)
        assert "The price has been changed" in price_out, f"pricevendor failed: {price_out!r}"
        assert "Ok." in owner.cmd("save", require_prompt=True)

        list_out = buyer.cmd("list", require_prompt=True)
        assert "glowrod" in list_out.lower() and "500" in list_out, f"vendor list unexpected: {list_out!r}"
        buy_out = buyer.cmd("buy glowrod", require_prompt=True)
        assert "glowrod" in buy_out.lower(), f"buyer buy failed: {buy_out!r}"
        assert "glowrod" in buyer.cmd("inventory", require_prompt=True).lower()

        score_prompt = re.compile(r"\bcredits:\s*[\d,]+.*?Health:\s*\d+/\d+\s+Movement:\s*\d+/\d+\s*>",
                                  re.IGNORECASE | re.DOTALL)
        pre_sc = owner.cmd("score", prompt_pattern=score_prompt)
        m_pre = re.search(r"Credits:\s*([\d,]+)", pre_sc, re.IGNORECASE)
        col_out = owner.cmd("getcredits", require_prompt=True)
        assert "hands over his earnings" in col_out.lower(), f"getcredits failed: {col_out!r}"
        post_sc = owner.cmd("score", prompt_pattern=score_prompt)
        m_post = re.search(r"Credits:\s*([\d,]+)", post_sc, re.IGNORECASE)
        assert m_pre is not None and m_post is not None, (
            f"Credits missing in owner score: pre={pre_sc!r}, post={post_sc!r}"
        )
        g_pre = int(m_pre.group(1).replace(",", ""))
        g_post = int(m_post.group(1).replace(",", ""))
        assert g_post - g_pre == 475, f"Expected 475 credits collected (5% fee), got {g_post - g_pre}"

        # Settlement broadcasts to both clients. Wait for each save's result
        # before accepting a prompt that might belong to the earlier broadcast.
        save_prompt = re.compile(
            r"(?:Ok\.|Unable to save your (?:character|home)\.).*?Health:\s*\d+/\d+"
            r"\s+Movement:\s*\d+/\d+\s*>",
            re.DOTALL,
        )
        for client in (owner, buyer):
            saved = client.cmd("save", prompt_pattern=save_prompt)
            assert "Ok." in saved and "Unable to save" not in saved, saved

        _clean_quit(owner)
        owner = None
        _clean_quit(buyer)
        buyer = None
        _clean_quit(imm)
        imm = None
        _shutdown_and_assert_clean(ctx, proc)

        proc2 = SwripProcess(proc.work_dir)
        proc2.start()
        ctx._processes.append(proc2)
        bm2 = proc2.wait_ready(15.0)
        assert bm2.ready is True, f"Reboot failed: {bm2.log_tail}"

        buyer = ctx.make_client(port=proc2.port)
        _assert_login(buyer, "Vendbuyer", "VendBuy1")
        inventory = buyer.cmd("inventory", require_prompt=True)
        assert "glowrod" in inventory.lower(), f"Purchased glowrod missing after restart: {inventory!r}"
        look_v = buyer.cmd("look")
        assert "vendor" in look_v.lower() or "vendowner" in look_v.lower(), (
            f"Vendor did not persist across reboot via load_vendors: {look_v!r}"
        )
        _clean_quit(buyer)
        buyer = None
        _shutdown_and_assert_clean(ctx, proc2)
    finally:
        _clean_quit(owner)
        _clean_quit(buyer)
        _clean_quit(imm)
        _safe_stop_proc(proc)
        _safe_stop_proc(proc2)


@test_case(
    "T1-F08-05",
    1,
    "F08",
    "Clan storeroom (10556) and PC corpse persistence across cold server restart",
    requires_server=True,
    fresh_server=True,
)
def test_t1_f08_05(ctx: E2EContext) -> None:
    proc = ctx.start_fresh_server("f08-store-corpse")
    proc2: SwripProcess | None = None
    imm: TelnetClient | None = None
    vic: TelnetClient | None = None
    try:
        ws = proc.work_dir
        imm = ctx.make_client(port=proc.port)
        _assert_login(imm, IMM_NAME, IMM_PASS)

        _goto_checked(imm, 10556)
        assert "Ok." in imm.cmd("oinvoke 10311")
        imm.cmd("drop glowrod")
        assert "Ok." in imm.cmd("save")
        assert (ws / "storeroom" / "10556").is_file(), "storeroom/10556 not created"

        vic = ctx.make_client(port=proc.port)
        _assert_create_character(vic, "Corpsepc", "CorpsePc1")
        _authorize_player(imm, "Corpsepc")
        vic.drain(0.2)

        _goto_checked(imm, 10308)
        imm.cmd("transfer Corpsepc 10308")
        slay_out = _cmd_no_crash(proc, imm, "slay Corpsepc")
        assert "You slay Corpsepc in cold blood!" in slay_out, f"slay failed: {slay_out!r}"
        vic.close()
        vic = None
        assert (ws / "corpses" / "Corpsepc").is_file(), "corpses/Corpsepc not created"

        _clean_quit(imm)
        imm = None
        _shutdown_and_assert_clean(ctx, proc)

        proc2 = SwripProcess(ws)
        proc2.start()
        ctx._processes.append(proc2)
        bm2 = proc2.wait_ready(15.0)
        assert bm2.ready is True, f"Reboot failed: {bm2.log_tail}"

        imm = ctx.make_client(port=proc2.port)
        _assert_login(imm, IMM_NAME, IMM_PASS)
        _goto_checked(imm, 10556)
        assert "glowrod" in imm.cmd("look").lower(), "Storeroom 10556 item lost on reboot"
        _goto_checked(imm, 10308)
        assert "corpse of Corpsepc" in imm.cmd("look"), "PC corpse lost on reboot"
        _clean_quit(imm)
        imm = None

        _shutdown_and_assert_clean(ctx, proc2)
    finally:
        _clean_quit(vic)
        _clean_quit(imm)
        _safe_stop_proc(proc)
        _safe_stop_proc(proc2)


# ===========================================================================
# Feature F09: Combat, Blasters, Corpses, Tracking & special.c
# ===========================================================================


@test_case(
    "T1-F09-01",
    1,
    "F09",
    "consider, kill chadoyn (10308), corpse creation, and get all corpse",
    requires_server=True,
    fresh_server=False,
)
def test_t1_f09_01(ctx: E2EContext) -> None:
    imm: TelnetClient | None = None
    plr: TelnetClient | None = None
    try:
        imm = ctx.make_client(timeout=5.0)
        _assert_login(imm, IMM_NAME, IMM_PASS)

        plr = ctx.make_client(timeout=5.0)
        _assert_create_character(plr, "Ffighter", "FightPass1")
        _authorize_player(imm, "Ffighter")
        plr.drain(0.2)

        imm.cmd("transfer Ffighter 10308", timeout=5.0)
        imm.cmd("advance Ffighter combat 25", timeout=5.0)
        imm.cmd("mset Ffighter hitroll 15", timeout=5.0)
        _ensure_chadoyn_present(imm)
        plr.drain(0.25)

        con_out = plr.cmd("consider chadoyn", timeout=5.0, require_prompt=True)
        assert any(
            kw in con_out.lower()
            for kw in ("wimp", "weaker", "as strong", "feather", "womp", "suicide", "easy", "match", "kill")
        ), f"Unexpected consider output: {con_out!r}"

        plr.send_line("kill chadoyn")
        dead_out = _read_until_assert(plr, r"is DEAD!", timeout=8.0, desc="chadoyn death")
        assert "is DEAD!" in strip_ansi(dead_out)
        plr.drain(0.2)

        look_out = plr.cmd("look", timeout=5.0)
        assert "corpse" in look_out.lower(), f"Corpse missing after kill: {look_out!r}"
        loot_out = plr.cmd("get all corpse", timeout=5.0)
        assert "I see no " not in loot_out and (
            "You get" in loot_out or "I see nothing in the" in loot_out
        ), f"get all corpse failed: {loot_out!r}"
    finally:
        _clean_quit(plr)
        _restore_immtest(imm, respawn_chadoyn=True)
        _clean_quit(imm)


@test_case(
    "T1-F09-02",
    1,
    "F09",
    "flee escapes active combat into adjacent room",
    requires_server=True,
    fresh_server=False,
)
def test_t1_f09_02(ctx: E2EContext) -> None:
    imm: TelnetClient | None = None
    plr: TelnetClient | None = None
    try:
        imm = ctx.make_client(timeout=5.0)
        _assert_login(imm, IMM_NAME, IMM_PASS)

        plr = ctx.make_client(timeout=5.0)
        _assert_create_character(plr, "Ffleer", "FleePass1")
        _authorize_player(imm, "Ffleer")
        plr.drain(0.2)

        imm.cmd("transfer Ffleer 10308", timeout=5.0)
        _ensure_chadoyn_present(imm)
        imm.cmd("mset chadoyn hp 5000", timeout=5.0)
        restored = imm.cmd("restore chadoyn", timeout=5.0, require_prompt=True)
        assert "Ok." in restored, f"Couldn't prepare the fleeing opponent: {restored!r}"
        plr.drain(0.25)

        plr.send_line("kill chadoyn")
        _read_until_assert(
            plr,
            re.compile(
                r"(?:You (?:miss|do \d+ points? of damage)|chadoyn .*?(?:misses|dodges|parries|damage))",
                re.IGNORECASE,
            ),
            timeout=5.0,
            desc="combat start against chadoyn",
        )
        plr.drain(0.15)
        flee_out = ""
        for _ in range(6):
            plr.send_line("flee")
            flee_out = _read_until_assert(
                plr,
                r"(?:You run for cover!|You fled|PANIC!|You attempt to run for cover!|You aren't fighting anyone\.)",
                timeout=5.0,
                desc="flee result",
            )
            clean_flee = strip_ansi(flee_out)
            if "PANIC!" not in clean_flee and "You attempt to run for cover!" not in clean_flee:
                break
        plr.drain(0.2)
        assert "You run for cover!" in flee_out or "You fled" in flee_out, f"flee failed: {flee_out!r}"
        look_out = plr.cmd("look", timeout=5.0, require_prompt=True)
        assert "Dank Pit" in look_out or "10316" in look_out or "chadoyn" not in look_out.lower()
        assert "You aren't fighting anyone." in plr.cmd("flee", timeout=5.0, require_prompt=True)
    finally:
        _clean_quit(plr)
        _restore_immtest(imm, respawn_chadoyn=True)
        _clean_quit(imm)


@test_case(
    "T1-F09-03",
    1,
    "F09",
    "Blaster E-11 (50) reload with ammo (10488), setblaster, and combat/snipe firing",
    requires_server=True,
    fresh_server=False,
)
def test_t1_f09_03(ctx: E2EContext) -> None:
    imm: TelnetClient | None = None
    plr: TelnetClient | None = None
    try:
        imm = ctx.make_client(timeout=5.0)
        _assert_login(imm, IMM_NAME, IMM_PASS)

        plr = ctx.make_client(timeout=5.0)
        _assert_create_character(plr, "Fblaster", "BlastPass1")
        _authorize_player(imm, "Fblaster")
        plr.drain(0.2)

        _goto_checked(imm, 10316)
        imm.cmd("transfer Fblaster 10316", timeout=5.0)
        imm.cmd("advance Fblaster combat 10", timeout=5.0)
        imm.cmd("mset Fblaster str 18", timeout=5.0)
        imm.cmd("sset Fblaster blasters 100", timeout=5.0)
        imm.cmd("sset Fblaster snipe 100", timeout=5.0)
        assert "Ok." in imm.cmd("oinvoke 50 1", timeout=5.0)
        imm.cmd("oset blaster v4 0", timeout=5.0)
        assert "Ok." in imm.cmd("oinvoke 10488 1", timeout=5.0)
        imm.cmd("give blaster Fblaster", timeout=5.0)
        imm.cmd("give ammo Fblaster", timeout=5.0)
        plr.drain(0.25)

        plr.cmd("remove vibro", timeout=5.0, require_prompt=True)
        wield_out = plr.cmd("wield blaster", timeout=5.0, require_prompt=True)
        assert "You wield" in wield_out or "blaster" in wield_out.lower(), f"wield blaster failed: {wield_out!r}"
        sb_out = plr.cmd("setblaster normal", timeout=5.0, require_prompt=True)
        assert "Wielded blaster set to NORMAL Power" in sb_out, f"setblaster failed: {sb_out!r}"
        ammo_out = plr.cmd("ammo", timeout=5.0, require_prompt=True)
        assert "You replace your ammunition cartridge." in ammo_out, f"ammo reload failed: {ammo_out!r}"
        assert "charged with 100 shots at high power to 500 shots on low" in ammo_out

        eq_pre = plr.cmd("eq", timeout=5.0, require_prompt=True)
        assert "500" in eq_pre, f"Expected charge 500 in eq: {eq_pre!r}"

        imm.cmd("transfer Fblaster 10308", timeout=5.0)
        _ensure_chadoyn_present(imm)
        plr.drain(0.2)
        plr.send_line("kill chadoyn")
        _read_until_assert(plr, r"is DEAD!", timeout=8.0, desc="blaster kill chadoyn")
        plr.drain(0.2)

        eq_post = plr.cmd("eq", require_prompt=True)
        m_chg = re.search(r"NORMAL.*?(\d+)", eq_post) or re.search(r"(\d+)\s*/\s*500|Charge:\s*(\d+)", eq_post)
        if m_chg:
            val = int(next(g for g in m_chg.groups() if g is not None))
            assert val < 500, f"Expected blaster charge < 500 after combat, got {val}: {eq_post!r}"
        else:
            assert "500" not in eq_post, f"Blaster charge remained 500 after firing: {eq_post!r}"
    finally:
        _clean_quit(plr)
        _restore_immtest(imm, purge_items=("blaster", "ammo"), respawn_chadoyn=True)
        _clean_quit(imm)


@test_case(
    "T1-F09-04",
    1,
    "F09",
    "track locates target mob across rooms in same area",
    requires_server=True,
    fresh_server=False,
)
def test_t1_f09_04(ctx: E2EContext) -> None:
    c: TelnetClient | None = None
    try:
        c = ctx.make_client()
        _assert_login(c, IMM_NAME, IMM_PASS)
        _ensure_chadoyn_present(c)

        c.cmd("sset Immtest track 100")
        _goto_checked(c, 10315)
        t1 = c.cmd("track chadoyn")
        assert "You sense a trail down from here" in t1, f"Unexpected track from 10315: {t1!r}"

        _goto_checked(c, 10308)
        t2 = c.cmd("track chadoyn")
        assert "You're already in the same room!" in t2, f"Unexpected track in 10308: {t2!r}"
    finally:
        _restore_immtest(c)
        _clean_quit(c)


@test_case(
    "T1-F09-05",
    1,
    "F09",
    "Academy graduation through spec_auth (10324) and spec_newbie_pilot (10326) to valid non-Limbo room",
    requires_server=True,
    fresh_server=False,
)
def test_t1_f09_05(ctx: E2EContext) -> None:
    imm: TelnetClient | None = None
    plr: TelnetClient | None = None
    try:
        imm = ctx.make_client()
        _assert_login(imm, IMM_NAME, IMM_PASS)

        plr = ctx.make_client()
        _assert_create_character(plr, "Fgrad", "GradPass1", race="Human")

        for step in ("n", "n", "n", "n", "n", "n", "open north", "n", "d"):
            plr.cmd(step, timeout=5.0)
        plr.cmd("say hello", timeout=5.0)
        plr.drain(0.2)
        plr.cmd("say yes", timeout=5.0)
        plr.drain(0.2)
        for step in ("u", "n"):
            plr.cmd(step, timeout=5.0)
        plr.cmd("give luke child", timeout=5.0)
        plr.drain(0.2)
        plr.cmd("say key", timeout=5.0)
        plr.drain(0.2)
        diploma_msg = plr.cmd("n", timeout=5.0)

        # The mobile pulse can deliver the diploma in the movement response.
        if "gives you a diploma" not in strip_ansi(diploma_msg):
            diploma_msg = _read_until_assert(
                plr, r"gives you a diploma", timeout=6.0, desc="spec_auth diploma"
            )
        assert "gives you a diploma" in strip_ansi(diploma_msg)

        plr.cmd("say iamauthed", timeout=5.0)
        plr.drain(0.25)
        arrival_msg = plr.cmd("enter", timeout=5.0)

        arrival_pattern = r"After a brief journey|steps out|Pluogus|Menari Spaceport"
        if not re.search(arrival_pattern, strip_ansi(arrival_msg)):
            _read_until_assert(plr, arrival_pattern, timeout=6.0, desc="spec_newbie_pilot warp")
        plr.drain(0.25)
        look_grad = plr.cmd("look")
        assert "Limbo" not in look_grad and "You are floating in a void" not in look_grad, (
            f"Human graduate stranded in Limbo (#2): {look_grad!r}"
        )

        # Verify via immortal rstat at Fgrad's room
        at_stat = imm.cmd("at Fgrad rstat")
        assert not re.search(r"\bVnum:\s*2\b", at_stat) and "Name: Limbo" not in at_stat, (
            f"Fgrad landed in Limbo room #2: {at_stat!r}"
        )
        assert "Ok." in plr.cmd("save")
    finally:
        _clean_quit(plr)
        _restore_immtest(imm)
        _clean_quit(imm)
