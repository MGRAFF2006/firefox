# Source build and Speedometer baseline

This experiment compares Debian stable Chromium, Mozilla's official stable Firefox,
and a full-source release-oriented Firefox build at `FIREFOX_159_0_1_RELEASE`
(`6181e6b5f28a8fbae1d5c3ef6f6626b5a2180213`). It does not establish a general
browser speedup or a twofold gain. No browser source optimization is included.

## Build

Use a Linux x86_64 host with development storage, an isolated X display, matching
Chromium/ChromeDriver packages, and Mozilla's bootstrapped toolchains. Keep at
least 10 GiB free. Do not use an existing personal profile or disable sandboxing.

```sh
export FIREFOX_BASELINE_ROOT=/srv/development/build/firefox-perf
export MOZBUILD_STATE_PATH="$FIREFOX_BASELINE_ROOT/state"
export TMPDIR="$FIREFOX_BASELINE_ROOT/tmp"
source_checkout=$PWD
mkdir -p "$TMPDIR"
./mach --no-interactive bootstrap --application-choice=browser --no-system-changes
(cd "$MOZBUILD_STATE_PATH" && "$source_checkout/mach" artifact toolchain \
  --from-build linux64-rust --cache-dir "$MOZBUILD_STATE_PATH/toolchains")
tmux new-session -d -s firefox-build \
  "FIREFOX_BASELINE_ROOT='$FIREFOX_BASELINE_ROOT' bash tools/performance/baseline/build.sh > '$FIREFOX_BASELINE_ROOT/build-release.log' 2>&1"
```

The mozconfig retains upstream feature code/defaults and uses release optimization,
Rust SIMD, X11/Wayland, and cross-language ThinLTO with four compilation jobs.
It does not yet use PGO, and is not equivalent to Mozilla's official PGO/full-LTO
configuration. These flags are baseline configuration, not a new optimization.
The release tag already supplies the release milestone; `--as-milestone=release`
is rejected by configure on this source tag.

After a successful build, `bash tools/performance/baseline/preserve.sh` packages
and extracts the stock runtime under `ROOT/binaries/source-stock-SHORTREV`,
retains its unstripped libxul for symbolication, and records runtime/archive/config
hashes plus compiler, source, and checkout provenance. It permits the baseline
tooling commit while requiring everything outside this directory to match the
pinned release source. Use that preserved Firefox executable
for measurements and later same-config controls; rebuilding the objdir must not
destroy the unmodified control binary. The script refuses to overwrite a runtime.

Use the pinned Rust toolchain in `ROOT/state/rustc`, not whichever Rust happens
to be on PATH. Here bootstrap selected Clang 22.1.8 but accepted system Rust 1.99
(LLVM 23.1.1). A small cross-language link reproduced `Invalid summary version
14. Version should be in the range [1-12]`. Mozilla's Rust 1.95.0 (LLVM 22.1.2)
passes the same probe. Compiler paths are explicit in the mozconfig.

For a bounded smoke test after Browsertime setup:

```sh
xvfb-run -a -s '-screen 0 1920x1080x24' "$MOZBUILD_STATE_PATH/node/bin/node" \
  tools/performance/baseline/smoke.cjs /path/to/firefox
```

This uses a fresh WebDriver profile and tests DOM interaction, localStorage
across reload, Canvas pixels, WebAssembly execution, and WebRTC construction.
It audits default and effective security/PDF/WebRTC preferences, not API-backed
service operation. Privileged access is enabled only for this isolated test.
`remote.prefs.recommended=false` prevents WebDriver's recommended test preferences
from disabling Safe Browsing while auditing defaults. No stock feature pref is
overridden by this smoke test. It is not a standards-compliance test.

## Compare and profile

After the build session has ended successfully, run in a named tmux session:

```sh
python3 tools/performance/baseline/run.py \
  --source /path/to/existing/harness-checkout \
  --root "$FIREFOX_BASELINE_ROOT" \
  --official "$FIREFOX_BASELINE_ROOT/official/firefox/firefox" \
  --source-build "$FIREFOX_BASELINE_ROOT/binaries/source-stock-6181e6b5/firefox/firefox"
```

The runner rotates Chromium/official/source order across five independent rounds,
each with a fresh browser process/profile and one complete Speedometer suite
(five independent suites per browser). The cold-test manifest forces one page
cycle per browser cycle, so requesting five page cycles would not run five suites.
It uses the pinned source's Raptor/Browsertime harness and default startup delay.
Raptor's benchmark preferences and Browsertime's prompt/background-feature flags
are test conditions, distinct from shipped defaults. The runner explicitly keeps
Browsertime's Docker mode off; that mode would bypass Chromium's sandbox. Xvfb is
provided externally, with no sandbox-disable argument or browser feature removal
added to improve a score.
Child processes receive a minimal task environment, isolated runtime/cache
directories, and the actual unchanged HOME path. Harness environment dumps are
omitted from captured logs so unrelated agent-session state is not published.
Firefox 159 requires `MOZ_REMOTE_ALLOW_SYSTEM_ACCESS=1` for Browsertime's trusted
privileged test scripts; it is supplied only to these isolated test processes.
`--only-browser` supports diagnostic subsets; the summary command still rejects
incomplete comparisons and fewer than five paired rounds.
Pinned mozharness overrides `MOZ_UPLOAD_DIR` with its shared build directory.
Under the experiment lock, the runner moves that invocation's actual upload
directory and result JSON into its per-run directory before starting another
browser. Uncollected shared output blocks a new run instead of being overwritten.
The harness lock prevents concurrent invocations; the runner also rejects the
named build session. Any external workloads must be recorded and considered.
The runner validates the identities and SHA-256 hashes of every recorded runtime
artifact before and after each invocation, including the engine libraries and
configuration files. A detached custodian owns the same resource lock and
supervises setup probes and each Raptor invocation. It uses the reviewed
`tools/standards/lifecycle.py` dependency pinned to accepted merge
`c0bef74e02eee17d5df7a952269bbfb1557cb2ad`; a missing or changed dependency
fails closed. The dependency and executed baseline helper files must be deployed together under
`TOOL_ROOT`, derived from this script's location. `--source` selects a separate
existing Mach/Raptor harness checkout; it defaults to `TOOL_ROOT` for a full-tree
backward-compatible deployment. A tools-only bundle must supply `--source`;
it is not a replacement Mach tree. The selected source cannot supply or replace
the pinned custody dependency. No source files are installed or copied by selection.

Cancellation and loss of the CLI cause the custodian to drain and reap owned
descendants across worker threads, process groups and sessions before releasing
the lock. `ROOT/scoring-custody/ID/state.json` records custody and drainage.
Uncertain ownership retains that custodian and lock in `ownership_hold`; an outer
monitor must not kill it merely to meet a timeout. Killing the final custodian
or losing the host is outside this userspace guarantee. Manual recovery must
establish actual actor absence/reaping before ending a retained custodian.
Raptor output is held in an anonymous temporary file until drainage, then copied
with the same environment-dump redaction into `raptor.log`; live log progress is
therefore delayed. Round order, workload, score validation and runtime checks are
unchanged.

The harmless caller contracts use only a private temporary lock and synthetic
workload/source fixtures, including a worker-thread fork, nested session,
reparented descendant and unrelated sentinel. They are not browser scores:

```sh
python3 tools/performance/baseline/test_runner.py
python3 tools/performance/baseline/lifecycle-tests/contracts.py \
  --output /new/private/fixture-results
```

Run these under the normal bounded auxiliary admission. `contracts.py` invokes
actual argument parsing, lock ownership, the workload loop and result handling;
its source/runtime/version/Raptor dependencies are explicitly synthetic. The
`--runner OLD_RUN_PY --expect-red` option reproduces the original premature-unlock
bug. Do not point these fixtures at the production resource lock.

Raw browser results, commands, versions, SHA-256 hashes, manifest hash, host details,
load, and failures are retained under `ROOT/runs/BERLIN-TIMESTAMP` (with offset). Do not discard
failed runs or mix profiled scores into comparisons. For separate diagnostic
profiling append `--profile --rounds 1`; output goes under `ROOT/profiles`.
`checkout_revision` identifies the harness checkout; runtime INI files record
the actual version/build ID and any embedded source information. This unbranded
build does not embed a SourceStamp, so its verified pinned source revision is
retained externally in the preserved runtime's `source-revision.txt`, bound to
the archive/config/toolchain by `SHA256SUMS`. Executed
harness files are snapshotted with their hashes alongside the results.

Run `python3 tools/performance/baseline/summarize.py ROOT/runs/BERLIN-TIMESTAMP`
to validate complete paired rounds and write `summary.json`. It reports every
score, medians, paired ratios, geometric mean ratios, and a deterministic paired
bootstrap percentile interval. With five rounds the interval is exploratory,
not a guarantee of performance under other host loads or workloads.

Report Firefox score / Chromium score for the paired rounds, together with
uncertainty and raw samples. Shared VM results are directional; this host has
no GPU and cannot support GPU performance claims. A claim of at least 2x would
need the uncertainty interval to support that threshold, and a defined workload.

The collected baseline is documented in [evidence](evidence/README.md), with the
full raw cohort, paired summary, runtime hashes, smoke results, and profile-query
exports. Run `python3 tools/performance/baseline/test_summary.py` for the summary
layout regression and rejection of profiled, incomplete, failed, swapped, or
changed-binary comparisons.
Run `python3 tools/performance/baseline/test_runner.py` for engine-only mutation,
missing-artifact, cancellation and inherited-lock regressions. These use isolated
files and harmless processes, not browsers. The archived scored cohort retains
its original executed harness snapshot; these later guard fixes do not rewrite
that evidence or establish a new performance result.

## Feature/service limits

The build uses unofficial branding. It does not assert `--built-by-mozilla` or
copy Mozilla's private keyfiles. Sandbox, extension signature enforcement,
updater/MAR verification, crash reporter, WebRTC, PDF, and DRM support retain
upstream defaults. On Linux, EME's initial opt-in default is upstream behavior.

Compiling the features does not establish full official service parity:
`build/moz.configure/keyfiles.configure` substitutes missing-key sentinel values;
network geolocation and Google Safe Browsing requests use configured API keys.
The unbranded build also enables upstream's `glean_disable_upload` Cargo feature
(`fog_control/disable_upload`), so telemetry upload differs from Mozilla's
official distribution; no official-distribution identity is asserted to enable it.
Authorized service keys/providers, a signed fork update service, crash-report
symbolication, real DRM playback, and hardware integration remain unverified.
Do not disable protection/signature requirements to conceal those gaps.

Relevant source: `browser/config/mozconfigs/linux64/common-opt`,
`build/moz.configure/keyfiles.configure`, `toolkit/moz.configure`,
`build/moz.configure/update-programs.configure`,
`browser/app/profile/firefox.js`, and `modules/libpref/init/StaticPrefList.yaml`.


## Explicit harness source and supplied runtime roles

The opening build description records the original baseline experiment. The
collector also accepts newly supplied control/candidate runtimes through the
legacy `--official` and `--source-build` slots. Those names do not establish
Mozilla distribution, PGO, LTO or compiled-source identity. Before a new cohort,
attach the actual returned runtime-role manifests, BuildIDs, binary/library
hashes, source/CPP, config, toolchain and loader receipts separately. Do not
relabel the harness checkout/config as either browser's build provenance or
reuse historical scores for a new pair.

With `--source`, Mach/cwd, Raptor/benchmark inputs, Browsertime/driver paths,
mozharness output and actual Mach mozconfig all use that checkout. Executed
collector/helper hashes and snapshots use `HERE`/`TOOL_ROOT`; the actual source
mozconfig has its own path/hash and `harness/source-mozconfig` snapshot.
`checkout_revision` and `source_diff` remain harness fields; `harness_source`
labels that scope explicitly. `toolchain` describes harness-side tools only.
`collector_semantics=explicit-harness-source-v1` distinguishes this collector
from unchanged historical snapshots/results. The summarizer no longer infers
an official-PGO versus custom-no-PGO build configuration from legacy slot names.
It retains the same rotating rounds, full-suite construction, rejection rules,
score calculation and exploratory bootstrap interval.

An existing source directory must contain Mach and the baseline mozconfig.
This is a path preflight, not dependency provision or runtime readiness. Actual
Raptor providers/obj-venv/Node/Browsertime/driver dependencies must already be
provisioned and separately admitted before scoring; missing dependencies can
make upstream harness setup attempt downloads. No automatic installation or
real scoring is authorized by these preparation changes. Harness-generated
outputs need their ordinary source-output window; selecting --source does not
authorize tracked source edits. New paired runtimes and a separately admitted
15-invocation cohort remain prerequisites for new measurements.

`--source` is now an explicit harness option. Spell the runtime option
`--source-build` in full: earlier argparse versions could treat `--source` only
as an abbreviation for that runtime slot, not as a harness selector.

## New matched-pair evaluation

Use the same BUILD-owned sandbox after BUILD hands off preserved control and
candidate executables with matching configuration and distinct source/build
receipts. The legacy `--official` slot names the supplied control; it does not
make that binary an official Mozilla build. Do not reuse historical scores.

Run the smoke for each supplied Firefox under the existing owned workload
supervisor, with a private Xvfb display and `--source /actual/harness`:

```sh
xvfb-run -a -s '-screen 0 1920x1080x24' /actual/node \
  /executed/tools/performance/baseline/smoke.cjs \
  /preserved/control/firefox --source /actual/harness
```

The smoke resolves Selenium/geckodriver from that harness and checks the live
smoke-tab content PID against Firefox and Linux process identity, additional
seccomp filters and NoNewPrivs. It records namespace observations and default
and effective preferences. Missing evidence fails; this is a bounded content
sandbox observation, not full security/feature compatibility or Chromium proof.
It never enables a profiling or sandbox-disable mode. Any automation privilege
is scoped to this isolated child. Keep the exit status alongside its JSON.

First run a complete diagnostic round (`--rounds 1`) with the new control,
candidate and Chromium, using explicit `--source`, `--root`, `--official`,
`--source-build`, `--chromium` and `--chromedriver`. After valid diagnostics,
start a distinct predeclared five-round/15-invocation cohort without profiling.
The one-round diagnostic is not eligible for the comparison summary.

`run.py`, `full_suites.py` and `speedometer3-workload.json` must deploy together.
Before publishing any `score.json`, every raw Browsertime result must contain
all ten configured internal iterations, twenty suite IDs and fifty-eight step
IDs with complete finite measurements. The result's requested and observed
page URLs must agree with the full local Speedometer URL without suite/tag or
iteration overrides. Raw bytes are hashed, coverage is retained per invocation,
and changed declared source inputs reject. Aggregate/statistics alone cannot
stand in for the matrix. The summary's score math, round rotation, counts and
bootstrap remain unchanged; medians are scores, not elapsed seconds.

These source pins and page URLs do not authenticate HTTP response bodies.
Bind actual served workload bytes/selection, role/config/BuildID and effective
security/driver state in the runtime evidence before accepting a performance
claim. Xvfb exercises a rendered document but does not prove physical display
frames, GPU behavior, accessibility or all standards/services. Retain those
limits and failures rather than interpreting clean isolation as security proof.
