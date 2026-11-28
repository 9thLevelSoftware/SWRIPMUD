"""Shared process and fixture setup for the home and bounty persistence checks."""

import argparse
from contextlib import contextmanager
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from tests.e2e.framework import E2EContext
from tests.e2e.tiers import tier1_f10_18 as live
from tests.e2e.wsl_env import (
    SwripProcess,
    require_private_network,
    seed_immortal_fixture,
    stage_workspace,
)


def arguments(description):
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--repo", type=Path, default=REPO)
    parser.add_argument("--binary", type=Path, required=True)
    args = parser.parse_args()
    require_private_network()
    args.repo = args.repo.resolve()
    args.binary = args.binary.resolve(strict=True)
    return args


def stage(repo, label):
    world = stage_workspace(label, repo)
    print(f"Retained fixture and logs: {world}", flush=True)
    seed_immortal_fixture(world)
    return world


def pfile(world, name):
    return world / "player" / name[0].lower() / name


def command(client, line, expected=None):
    live._safe_drain(client, 0.10)
    client.send_line(line)
    output = live._safe_drain(client, 0.57)
    if expected is not None:
        assert expected.lower() in output.lower(), (line, expected, output[-1500:])
    return output


def login(ctx, process, name):
    client = ctx.make_client(port=process.port)
    live._safe_login(client, name, "E2ePass1")
    return client


def mortal(ctx, process, admin, prefix, gold=None):
    return live._create_authed_mortal(
        ctx, admin, port=process.port, prefix=prefix, room=10300, gold=gold
    )


def quit_player(client):
    command(client, "quit", "surroundings begin to fade")
    assert client.eof, "The test player must be offline before the next operation"
    client.close()


@contextmanager
def server(repo, world, binary, expected_exit=0):
    ctx = E2EContext(repo, keep_workspaces=True)
    process = SwripProcess(world, binary_path=binary)
    admin = None
    try:
        process.start()
        boot = process.wait_ready(timeout=30)
        assert boot.ready and boot.area_count == 40 and not boot.bug_lines, boot
        admin = live._login_imm(ctx, port=process.port)
        yield ctx, process, admin
        if expected_exit == 0:
            admin.send_line("shutdown mud now")
            process.proc.wait(timeout=20)
            shutdown = process.shutdown_clean()
            assert shutdown.clean_exit, shutdown
        else:
            shutdown = process.shutdown_clean()
            assert shutdown.exit_code == expected_exit and shutdown.sanitizer_report.clean, shutdown
    finally:
        process.terminate()
        ctx.cleanup()
