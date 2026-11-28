"""Exercise the Harlef contract against an existing server binary in a private test namespace."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from .telnet_client import DEFAULT_PROMPT_RE, TelnetClient
from .wsl_env import SwripProcess, seed_des_legacy_fixture, seed_immortal_fixture, stage_workspace


PLAYER = "Couriertest"
CAPPED_PLAYER = "Capcourier"
PASSWORD = "CourierPass1"
PACKAGE_NAME = "package to captain harlef"
ACCEPT = "say accept harlef delivery"
HAND_IN = "give package to harlef"
REPRESENTATIVE_ROOM = 10248
HARLEF_ROOM = 699
# A human combat specialist with 12 in each permanent stat has a leadership cap of 86.
LEADERSHIP_CAP_XP = 86 * 86 * 500 - 1
CREDIT_LIMIT = 2_147_483_647


def check_pfile(path: Path, *, credits: int, xp: int, has_package: bool) -> None:
    saved = path.read_text(encoding="utf-8")
    gold = re.search(r"^Gold\s+(\d+)$", saved, re.MULTILINE)
    leadership = re.search(r"^Ability\s+6\s+\d+\s+(\d+)$", saved, re.MULTILINE)
    package = re.findall(r"^Vnum\s+10210$", saved, re.MULTILINE)
    assert gold and int(gold[1]) == credits, gold
    assert int(leadership[1] if leadership else 0) == xp, leadership
    assert len(package) == int(has_package), package


def check_inventory(player: TelnetClient, *, has_package: bool) -> None:
    inventory = player.cmd("inventory", require_prompt=True).lower()
    assert inventory.count(PACKAGE_NAME) == int(has_package), inventory
    assert not re.search(r"package to captain harlef\s+\(\d+\)", inventory), inventory


def transfer(guide: TelnetClient, player: TelnetClient, name: str, room: int) -> None:
    guide.cmd(f"transfer {name} {room}", require_prompt=True)
    arrival = player.read_until(DEFAULT_PROMPT_RE, timeout=5)
    assert "has transferred you." in arrival, arrival


def verify_issuance(server: SwripProcess, pfile: Path) -> None:
    with TelnetClient("127.0.0.1", server.port) as player, TelnetClient("127.0.0.1", server.port) as guide:
        player.login(PLAYER, PASSWORD)
        guide.login("Immtest", "E2eImmPass1")
        transfer(guide, player, PLAYER, REPRESENTATIVE_ROOM)
        check_inventory(player, has_package=False)

        player.cmd("say where do I accept harlef delivery", require_prompt=True)
        check_inventory(player, has_package=False)
        player.cmd("say yes", require_prompt=True)
        listing = player.cmd("look list", require_prompt=True)
        assert "Deliver the package to Captain Harlef" in listing, listing
        issued = player.cmd(ACCEPT, require_prompt=True)
        assert "hands you a package" in issued, issued
        already = player.cmd(ACCEPT, require_prompt=True)
        assert "already have a delivery" in already, already
        drop = player.cmd("drop package", require_prompt=True)
        assert "can't let go of it" in drop, drop
        check_inventory(player, has_package=True)
        player.cmd("save", require_prompt=True)
        check_pfile(pfile, credits=5000, xp=0, has_package=True)


def verify_delivery(server: SwripProcess, pfile: Path) -> None:
    with TelnetClient("127.0.0.1", server.port) as player, TelnetClient("127.0.0.1", server.port) as guide:
        player.login(PLAYER, PASSWORD)
        guide.login("Immtest", "E2eImmPass1")
        check_inventory(player, has_package=True)
        transfer(guide, player, PLAYER, HARLEF_ROOM)
        wrong = player.cmd("give list to harlef", require_prompt=True)
        assert "only accept the package" in wrong, wrong
        inventory = player.cmd("inventory", require_prompt=True).lower()
        assert "job's list" in inventory, inventory
        player.cmd("save", require_prompt=True)
        check_pfile(pfile, credits=5000, xp=0, has_package=True)

        paid = player.cmd(HAND_IN, require_prompt=True)
        assert "pays you 5000 credits" in paid, paid
        assert "100000 leadership experience" in paid, paid
        check_inventory(player, has_package=False)
        repeat = player.cmd(HAND_IN, require_prompt=True)
        assert "You do not have that item." in repeat, repeat
        player.cmd("save", require_prompt=True)
        check_pfile(pfile, credits=10000, xp=100000, has_package=False)

        transfer(guide, player, PLAYER, REPRESENTATIVE_ROOM)
        reissued = player.cmd(ACCEPT, require_prompt=True)
        assert "hands you a package" in reissued, reissued
        transfer(guide, player, PLAYER, HARLEF_ROOM)
        paid_again = player.cmd(HAND_IN, require_prompt=True)
        assert "pays you 5000 credits" in paid_again, paid_again
        check_inventory(player, has_package=False)
        player.cmd("save", require_prompt=True)
        check_pfile(pfile, credits=15000, xp=200000, has_package=False)


def verify_reward_limits(server: SwripProcess, pfile: Path) -> None:
    with TelnetClient("127.0.0.1", server.port) as player, TelnetClient("127.0.0.1", server.port) as guide:
        player.login(CAPPED_PLAYER, PASSWORD)
        guide.login("Immtest", "E2eImmPass1")
        transfer(guide, player, CAPPED_PLAYER, REPRESENTATIVE_ROOM)
        player.cmd(ACCEPT, require_prompt=True)
        transfer(guide, player, CAPPED_PLAYER, HARLEF_ROOM)
        paid = player.cmd(HAND_IN, require_prompt=True)
        assert "pays you 5000 credits" in paid, paid
        assert "50000 leadership experience" in paid, paid
        assert "capped at your current maximum" in paid, paid
        player.cmd("save", require_prompt=True)
        check_pfile(pfile, credits=CREDIT_LIMIT - 2000, xp=LEADERSHIP_CAP_XP, has_package=False)

        transfer(guide, player, CAPPED_PLAYER, REPRESENTATIVE_ROOM)
        player.cmd(ACCEPT, require_prompt=True)
        transfer(guide, player, CAPPED_PLAYER, HARLEF_ROOM)
        rejected = player.cmd(HAND_IN, require_prompt=True)
        assert "Keep the package for now" in rejected, rejected
        check_inventory(player, has_package=True)
        player.cmd("save", require_prompt=True)
        check_pfile(pfile, credits=CREDIT_LIMIT - 2000, xp=LEADERSHIP_CAP_XP, has_package=True)


def verify_death_reissue(server: SwripProcess, pfile: Path) -> None:
    pfile.with_suffix(".clone").write_bytes(pfile.read_bytes())
    with TelnetClient("127.0.0.1", server.port) as guide:
        guide.login("Immtest", "E2eImmPass1")
        guide.cmd(f"goto {REPRESENTATIVE_ROOM}", require_prompt=True)
        with TelnetClient("127.0.0.1", server.port) as player:
            player.login(PLAYER, PASSWORD)
            transfer(guide, player, PLAYER, REPRESENTATIVE_ROOM)
            player.cmd("say yes", require_prompt=True)
            player.cmd(ACCEPT, require_prompt=True)
            check_inventory(player, has_package=True)
            guide.drain(0.2)
            slain = guide.cmd(f"slay {PLAYER}", require_prompt=True)
            assert f"You slay {PLAYER}" in slain, slain
        corpse = guide.cmd("look in corpse", require_prompt=True).lower()
        assert "job's list" in corpse, corpse
        assert PACKAGE_NAME not in corpse, corpse

        with TelnetClient("127.0.0.1", server.port) as player:
            player.login(PLAYER, PASSWORD)
            check_inventory(player, has_package=False)
            transfer(guide, player, PLAYER, REPRESENTATIVE_ROOM)
            reissued = player.cmd(ACCEPT, require_prompt=True)
            assert "hands you a package" in reissued, reissued
            check_inventory(player, has_package=True)
            player.cmd("save", require_prompt=True)
            check_pfile(pfile, credits=5000, xp=0, has_package=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", type=Path, help="Built Linux swrip executable")
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--death-only", action="store_true", help="Check corpse handling and reissuance")
    args = parser.parse_args()
    work_dir = stage_workspace("harlef-courier", args.source.resolve())
    print(f"Courier fixture staged at {work_dir}", flush=True)
    seed_immortal_fixture(work_dir)
    pfile = seed_des_legacy_fixture(work_dir, PLAYER, PASSWORD)
    if args.death_only:
        checks = (verify_death_reissue,)
    else:
        capped_pfile = seed_des_legacy_fixture(work_dir, CAPPED_PLAYER, PASSWORD)
        capped_fixture = capped_pfile.read_text(encoding="utf-8")
        capped_fixture = capped_fixture.replace("Gold         5000", f"Gold         {CREDIT_LIMIT - 7000}")
        capped_fixture = capped_fixture.replace("Played       100", f"Ability      6 86 {LEADERSHIP_CAP_XP - 50000}\nPlayed       100")
        capped_pfile.write_text(capped_fixture, encoding="utf-8")
        checks = (verify_issuance, verify_delivery)
    for verify in checks:
        server = SwripProcess(work_dir, binary_path=args.binary.resolve())
        server.start()
        try:
            server.wait_ready()
            verify(server, pfile)
            if verify is verify_delivery:
                verify_reward_limits(server, capped_pfile)
            shutdown = server.shutdown_clean()
            assert shutdown.clean_exit, shutdown
        finally:
            if server.is_running():
                server.terminate()
    print("Harlef courier checks passed.")


if __name__ == "__main__":
    main()
