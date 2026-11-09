# Warm Firefox iteration

Use one persistent builder and, when worthwhile, one disposable Boat test worker.
Keep the same source path, `obj-release`, release mozconfig and pinned toolchain.
Do not start another cold build while the baseline is running. This workflow has
no measured incremental rebuild result yet; it does not claim a speedup.

## Local lane

Python 3.11+ on Linux is required. Integrate `run.py`, `verify.py` and this README
under `tools/iteration`. Runtime records stay outside source/Git.

```sh
SOURCE=/srv/development/projects/firefox-perf
ROOT=/srv/development/build/firefox-perf
python3 "$SOURCE/tools/iteration/run.py" --source "$SOURCE" --root "$ROOT" binaries
# Only after the baseline owner has released the hold:
python3 "$SOURCE/tools/iteration/run.py" --source "$SOURCE" --root "$ROOT" --execute binaries
```

Omitting `--execute` prints intent/readiness without acquiring the lock or writing
files. An exit-zero plan is not a build or proof of readiness. Execution takes
`ROOT/resource.lock` with nonblocking exclusive flock; busy means fail and retry
later, not queue or restart the existing build. It calls Mach directly under that
lock. Never wrap self-locking `baseline/build.sh`, `baseline/preserve.sh` or
`baseline/run.py` in `check`: their locks are not reentrant.

Choose the target deliberately:

| Change | Operation | Actual command |
| --- | --- | --- |
| C/C++/Rust implementation only | `binaries` | `./mach --no-interactive build binaries` |
| Frontend implementation only | `faster` | `./mach --no-interactive build faster` |
| Build dependencies/mixed change requiring a full build | `full` | `./mach --no-interactive build` |
| Test-only edit | no build | Run the focused test command through `check` |

For a focused test without its own lock, append
`--execute check -- COMMAND ARG...` to the same source/root options. Use the
commands and prerequisites in `tools/standards/README.md` for standards tests.
`check` runs its argument vector directly (no implicit shell), with the same
readiness and resource guards. A command must remain in the foreground until
its children finish; do not use it to launch detached workers. Measurements with
the baseline runner already take the shared lock and must run directly.

The launcher constructs a clean environment with `HOME` from the current Unix
account, `PATH=/usr/local/bin:/usr/bin:/bin`, `LANG=C.UTF-8`, `LC_ALL=C.UTF-8`
and `TZ=Europe/Berlin`. These match the stable build inputs established after the
PATH-triggered global dependency rebuild. It also sets `FIREFOX_BASELINE_ROOT`, `MOZBUILD_STATE_PATH=ROOT/state`,
`TMPDIR=ROOT/tmp`, `MOZCONFIG=SOURCE/tools/performance/baseline/mozconfig` and
`CARGO_HOME=ROOT/cargo`. That mozconfig selects `ROOT/obj-release`, pinned Rust/
Cargo in `ROOT/state/rustc` and the existing j4/release/cross-ThinLTO options.
Mozilla's existing bootstrapped Clang stays in `ROOT/state/clang`. Common compiler,
flag and cache-wrapper overrides are rejected instead of silently changing the
build. No cache enabling, configure, clobber, bootstrap or job-count command is
issued by this launcher. Ambient variables outside this allowlist are omitted;
compiler overrides still fail explicitly. Toolchain version probes and Git
identity/diff probes use the same clean environment as the command. This keeps a
shell or tmux PATH change from silently changing Mach's build dependencies.
Changing these defaults requires a deliberate build-environment transition;
the original baseline launcher PATH was not recorded and is not reconstructed.
Mach still owns its normal dependency processing; if a
patch needs changed build configuration, stop and coordinate that separately.
The existence of a warm `obj-release/config.status` and a 10 GiB free reserve
are required. The existing `mozinfo.json` must match the exact source, objdir
and mozconfig paths, preventing accidental reuse from another checkout. This is a task launcher; it does not isolate filesystem contents or system libraries.

After each executed operation, `ROOT/iterations/BERLIN-TIMESTAMP-PID` contains
`command.log` and `invocation.json`: command, current revision, tracked diff hash,
readiness marker and hash, selected environment paths, config/compiler hashes
and versions, host/load, exit status, elapsed and child CPU times. Elapsed/CPU accounting
covers the tool/Git probes and command; `executed` marks whether command launch
was attempted. A cancellation during preparation may lack complete source/tool
fingerprints, with `active_command` identifying the interrupted probe. `build_inputs_sha256` hashes
the clean environment, mozconfig identity and toolchain paths/hashes/versions;
compare it before treating repeated operations as equivalent. It excludes
source inputs, which have separate revision/tracked-diff fields. Records use
atomic replacement so readers see complete JSON. `preparing` or `running` without `finished`
indicates incomplete evidence, including an uncatchable launcher termination.
SIGINT/SIGTERM handling starts before lock acquisition and remains active through
final evidence and descendant cleanup. Toolchain/Git probes and the command all run in
a Linux owned-descendant supervisor (Python pidfd APIs and readable procfs
required). It walks only the dedicated launcher’s children and their children,
including thread-created children, validating ancestry while holding pidfds.
It never selects processes by name, UID, session or a global process scan.
Cancellation forwards the signal on the next supervision tick and escalates
to SIGKILL after five seconds. On every leader exit, including ordinary nonzero
failure or supervisor exceptions, it kills and reaps owned descendants across
PGIDs and sessions. Subreaper adoption catches even descendants that fork and
change groups between observations. Repeated discovery, killing and reaping
finish before evidence finalization and lock release; discovery errors retain
the lock and retry. This assumes a dedicated launcher that creates no unrelated
children; it is not a security boundary against hostile credential/procfs changes. The original command exit status is preserved. Cancellation exit codes are 128 plus the received signal.
Processes blocked in uninterruptible kernel I/O can delay reaping; the lock stays
held rather than admitting another heavy operation. The launcher cannot recover
from SIGKILL or host loss. Child RSS is
a high-water mark including compiler-version probes, **not** aggregate parallel
build RAM. Untracked source files are not covered by the tracked diff hash;
commit task inputs before recording a comparable rebuild. Logs may contain test
output; keep runtime evidence outside Git and review any publication.

## Baseline hold and artifact handoff

The parent/build owner alone creates `ROOT/baseline-complete.json`, after the
unmodified source runtime is preserved and baseline **and** profile evidence
have been captured. The launcher never creates the marker and has no bypass.
Required schema (values below describe fields, not an authorization template):

| Field | Required value |
| --- | --- |
| `schema` | integer `1` |
| `source_revision` | `6181e6b5f28a8fbae1d5c3ef6f6626b5a2180213` |
| `mozconfig_sha256` | SHA-256 of the actual baseline mozconfig |
| `preserved_runtime` | absolute path to executable preserved stock Firefox |
| `baseline_evidence` | absolute path to nonempty completed baseline evidence file |
| `profile_evidence` | absolute path to nonempty captured profile/evidence file |

Missing/malformed marker, config hash drift or absent artifacts fail closed.
The marker revision identifies the preserved control, not a later patch HEAD.
Artifact existence is checked; the owner remains responsible for evidence quality.
Remove/withhold readiness whenever the configuration or control must be replaced.

Use the existing `baseline/preserve.sh` after its successful build, under its own
resource lock, to retain `ROOT/binaries/source-stock-6181e6b5` **outside objdir**:
full archive and extracted runtime, unstripped libxul symbols, mozconfig, compiler
versions, source revision/diff stat and SHA256SUMS. Keep packaged `platform.ini`
with the runtime; include its SHA-256 in the handoff manifest. Do not overwrite
this control when rebuilding. Preserve each patched package separately. Verify
archive/runtime hashes before transfer and after extraction on a worker.

A future real worker handoff must contain only immutable allowlisted inputs:
packaged runtime; the identical integrated vendored WPT tests/tools and Mozilla
metadata for both stock/patched runs; selected standards wrapper files; selection
manifests; dependency lock/freeze; source and WPT tree identities; mozconfig and
compiler provenance; runtime/platform.ini hashes. Pin the exact test-tree input
hash, not just the upstream WPT sync marker. The worker must not clone arbitrary
moving source, use personal profiles, copy source-root runtime state or receive
account credentials. Save actual invocation, raw WPT report/logs, launcher status,
host details and hashes locally before stopping it. Missing prerequisites or
browser crashes remain failures. See `tools/standards/README.md` for setup.

## Optional Boat lane

Keep at most one task sandbox running; no distributed compiler or build farm.
Check `boat status`, `boat limits` and `boat list --all` first. Use explicit IDs,
`--no-env` and automatic stop. At quota failure, defer without retrying/upgrading.
No cold Firefox clone/build/toolchain download is needed to validate this lane.

The actual check on 2026-11-08 staged **only** these seven files in a fresh local
directory: `README.md`, `TRIAGE.md`, `candidates.txt`, `run.py`, `smoke.txt`,
`summarize.py`, `test_summary.py` from `tools/standards`. It then ran:

```sh
boat new --type small --ttl 600 --no-env --json
# Use the returned explicit ready-event ID; set cleanup immediately.
boat push ID /path/to/seven-file-stage --to /home/user/standards
boat exec ID -- bash -lc 'cd /home/user/standards && sha256sum README.md TRIAGE.md candidates.txt run.py smoke.txt summarize.py test_summary.py && python3 --version && python3 -B -m unittest -v test_summary.py'
boat stop ID
```

Capture every response locally. Inspect the exec JSON's `success`, `exitCode`,
`timedOut`, truncation and stderr fields as well as the CLI status. Compare each
remote file hash to the staged manifest. Install a shell EXIT trap or equivalent
`try/finally` for `boat stop ID` before transfers/tests; stop on failure too, then
confirm stopped state. For a future artifact export the supported command is
`boat scp ID:/absolute/result/path /local/result/path` (add `-r` for a directory).
Do not upload host config/auth files or create durable paid snapshots.

Sandbox `bx_uphy4xgv` (small 2 CPU/4 GB, TTL 600, no environment injection) ran
four real summary-integrity tests with Python 3.12.3. All passed, transfer hashes
matched, and final state was `stopped` with completed stop. Local evidence is
in the delivery snapshot's `evidence/boat-*` files. This proves remote execution
and lifecycle only. It ran no Firefox browser, WPT suite or build. A later focused
WPT worker may use one default sandbox if assets fit its disk/RAM. Boat scores
must never be mixed with local performance scores as a browser improvement.

## Verification and next use

```sh
python3 tools/iteration/verify.py --source "$SOURCE"
```

This uses an isolated temporary root and fixture toolchain executables. It checks
all target intents, write-free planning, missing/malformed marker, config drift,
missing profile evidence, lock contention/release, absent warm objdir, mismatched
checkout paths, real harmless
command invocation, nonzero exit propagation, logs/time/memory records and unchanged
source mozconfig. It additionally exercises a hostile ambient PATH/HOME/locale,
omission of unrelated secret-like environment, launch-error evidence, and a real
SIGTERM cancellation with a SIGTERM-resistant child and grandchild. The fixture
checks both lock retention during execution and grandchild removal/reaping. Two
additional CLI regressions signal a blocked fixture compiler probe and fail a
foreground command before it waits for its child. Both require descendant
cleanup before lock release; probe cancellation also requires finalized early
evidence. `--runner PATH --lifecycle-case probe` (or `failure`) runs the focused
regression against a selected launcher for before/after reproduction.
It never invokes Mach. The source build/config/active lock and
parent readiness marker are untouched. Verification passed on homelab-dev; no
incremental Firefox rebuild or actual browser run has been tested by this leaf.

After integration: independently review the exact committed HEAD and obtain the
shared compute/source slot from the build lead. Existing baseline readiness does
not grant that slot. Run the next accepted patch through `binaries`, retain its
raw record and compare actual elapsed/link work before changing architecture. If repeated
measured link time dominates, consider a separate non-LTO correctness objdir
later. Keep release builds as performance truth. sccache is not enabled here;
its existing installation alone does not establish cache hits. Any later bounded
cache experiment needs deliberate configuration plus before/after statistics.

Primary references: Mozilla's [Mach build targets](https://firefox-source-docs.mozilla.org/python/mozbuild.html#mozbuild.build_commands.build),
[objdir/configuration guidance](https://firefox-source-docs.mozilla.org/setup/configuring_build_options.html#building-with-an-objdir),
and [sccache Rust limitations](https://github.com/mozilla/sccache/blob/main/docs/Rust.md).


Nested process-group regression (2026-11-09): `profileserver.py` leaves
`FirefoxRunner` process arguments at mozprocess's `ignore_children=False`;
`Process.__init__` uses `os.setpgid(0, 0)`. Such foreground descendants are
legitimate, so original-PGID cleanup is insufficient. This is distinct from
`MozbuildObject._run_make`, which explicitly passes `ignore_children=True`.
No training or browser execution is authorized or performed by these checks.

`verify.py --source SOURCE` also exercises a new-PGID child spawning a
new-session, TERM-resistant grandchild, with probe cancellation, command failure,
SIGTERM and an injected one-shot supervisor exception. Each test uses its own
temporary root, independently probes the launcher lock, records PID/procfs
identity evidence and checks all fixture descendants are reaped. A sibling
sentinel belongs to the test parent and must survive launcher cleanup. Fixture
cleanup also handles older broken runners. The original same-group checks and
readiness/config/path/environment guards remain. Git revision/diff replies are
fixture substitutions to avoid scanning/hashing the large source checkout;
tool probes, command processes, cancellation, locks and evidence are real.
The injected exception replaces one sleep after the fixture becomes ready.
`--lifecycle-case nested-failure --runner OLD_RUNNER` reproduces the old leak.
These checks do not verify real Git fingerprint generation, kernel D-state,
SIGKILL/host-loss recovery, browsers, compilers or training workloads.

Exited-process pidfds are retired before each discovery pass, including cleanup
retries. A sequential build must not accumulate one descriptor per finished
subprocess. `--lifecycle-case fd-churn` runs 60 short children under a per-launcher
40-descriptor limit; `fd-recovery` additionally seeds exited-process handles and
injects discovery exhaustion during cleanup. Both require completion evidence
and lock release. These are fixture limits, not changes to host limits.
