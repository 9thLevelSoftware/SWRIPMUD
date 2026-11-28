# Home and bounty persistence recovery

Build the candidate once using the repository's ASan/UBSan build. Run these commands from a checkout with the tracked world data. The scripts stage disposable fixtures under `/var/tmp` and print the paths for inspection. Both require a non-root Linux process inside a private namespace with its loopback enabled. The bounty check also needs a C compiler, glibc, and `ldd`.

From inside that namespace, invoke each test with the absolute Linux path of the candidate executable.

```sh
python3 -B tests/persistence/home_recovery_test.py --binary "$candidate"
python3 -B tests/persistence/bounty_recovery_test.py --binary "$candidate"
```

See the [E2E runner setup](../e2e/README.md) for the WSL namespace command.

The home check covers purchase, an in-session resident removal, a legacy grant across restart, sale and repurchase while the former resident is offline, recovery of the current resident's grant, a blocked snapshot publication, and stored items after a cold restart.

The bounty check uses a launcher to inject failures only in the candidate process running a synthetic fixture. It tests interruptions after publishing the intent, player save, ledger, and intent cleanup for both posting and claiming. It also injects an I/O error before each operation's player-save publication. The unmodified candidate then boots twice for every case and verifies the expected balance, a single or absent ledger entry, and a cleared intent.
