"""Exercise clone publication through the normal player command in a disposable world."""

import argparse
from pathlib import Path
import re
import socket
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.e2e.telnet_client import TelnetClient
from tests.e2e.wsl_env import (
    SwripProcess,
    seed_des_legacy_fixture,
    seed_immortal_fixture,
    sha256_hash,
    stage_workspace,
)


def field(path: Path, name: str) -> int:
    match = re.search(rf"(?m)^{name}\s+(-?\d+)$", path.read_text(encoding="utf-8"))
    if match is None:
        raise AssertionError(f"{path.name} is missing {name}")
    return int(match[1])


def run(repo: Path, binary: Path) -> None:
    if {name for _, name in socket.if_nameindex()} != {"lo"}:
        raise RuntimeError("Use a private network namespace with loopback enabled.")

    world = stage_workspace("clone-save", repo)
    print(f"Clone persistence evidence: {world}", flush=True)
    seed_immortal_fixture(world)
    name, password = "Clonestudy", "CloneFixture1"
    player = seed_des_legacy_fixture(world, name=name, password=password)
    contents = player.read_text(encoding="utf-8")
    contents = re.sub(r"(?m)^Room\s+10300$", "Room         10001", contents)
    contents = re.sub(r"(?m)^Password\s+.+$", f"Password     {sha256_hash(password)}~", contents)
    player.write_text(contents, encoding="utf-8")

    clone = player.with_suffix(".clone")
    clone.mkdir()
    process = SwripProcess(world, binary_path=binary)
    process.start()
    try:
        boot = process.wait_ready(timeout=40)
        assert boot.ready and boot.area_count == 40 and not boot.bug_lines, boot
        with TelnetClient("127.0.0.1", process.port) as client:
            client.login(name, password)
            assert "Pay Counter" in client.cmd("look")
            rejected = client.cmd("clone")
            assert "unable to save your clone" in rejected.lower(), rejected
            assert "you pay" not in rejected.lower(), rejected
            assert "Pay Counter" in client.cmd("look")
            client.cmd("save")
            assert field(player, "Gold") == 5000
            assert field(player, "Room") == 10001
            assert not list(clone.parent.glob("Clonestudy.clone.tmp.*"))

            clone.rename(world / "blocked-clone-destination")
            accepted = client.cmd("clone")
            assert "You pay 2000 credits" in accepted, accepted
            assert "Small Sterile Room" in client.cmd("look")
            assert field(clone, "Room") == 10002
            assert field(clone, "Gold") == 1000
            assert field(clone, "Clones") == 1
            assert clone.read_text(encoding="utf-8").rstrip().endswith("#END")
            client.cmd("save")
            assert field(player, "Gold") == 3000
            assert field(player, "Room") == 10002
    finally:
        shutdown = process.shutdown_clean()
        assert shutdown.clean_exit and shutdown.sanitizer_report.clean, shutdown


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--binary", type=Path, required=True)
    args = parser.parse_args()
    run(args.repo.resolve(), args.binary.resolve())
    print("PASS clone save failure and successful publication")
