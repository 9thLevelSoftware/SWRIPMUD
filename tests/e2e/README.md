# SWRIPMUD end-to-end verification

The manifest adopts 200 cases across four tiers. `manifest.py` lists the expected cases and the 30 exclusions, including fixtures that need deterministic setup. Gate mode requires every selected case to pass and each ordinary server process to shut down cleanly with no sanitizer errors or leaks. Build and game writes occur in disposable `/var/tmp` workspaces.

## Linux or WSL

Use Python 3, GNU Make, and GCC with ASan/UBSan. The build needs zlib, OpenSSL, and libcrypt. Provision the development headers and the runner's sysroot before starting the private network session. From the repository root, prepare the sysroot, discover the approved manifest, and run the static checks.

```sh
python3 -B -c 'from tests.e2e.wsl_env import ensure_sysroot; ensure_sysroot()'
python3 -B tests/e2e/run_e2e.py --list
python3 -B tests/e2e/static_scanners.py --repo "$PWD" --mode post-remediation --check all
```

The runtime runner requires a non-root user and a network namespace with loopback as its only interface. With unprivileged user namespaces enabled, run the full gate as follows.

```sh
unshare --user --map-root-user --net sh -c '
  ip link set lo up &&
  unshare --user --map-user=1000 --map-group=1000 "$@"
' sh python3 -B tests/e2e/run_e2e.py --tier all --mode gate --keep-workspaces \
  --json-out /var/tmp/swrip-e2e-report.json
```

Use `--test <ID>` for a scoped check. Gate reports include the selected case IDs, process exit status, boot log paths and sanitizer totals. Run the harness unit tests with `python3 -B -m unittest tests.e2e.test_gate`.

## Focused checks on a built candidate

Inside the same kind of private namespace, pass the absolute Linux executable path to the following commands.

```sh
python3 -B -m tests.e2e.harlef_courier "$candidate"
python3 -B -m tests.e2e.harlef_courier "$candidate" --death-only
python3 -B -m tests.e2e.officers_lounge "$candidate"
python3 -B -m tests.e2e.ship_room_delete "$candidate"
python3 -B tests/persistence/clone_save_test.py --binary "$candidate"
python3 -B tests/persistence/clan_roster_test.py --binary "$candidate"
```

See the [home and bounty recovery checks](../persistence/home_bounty_recovery.md) for failure-boundary and restart verification.
