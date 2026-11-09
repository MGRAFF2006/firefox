# Baseline evidence: 2026-11-09

This is an unmodified engine at `FIREFOX_159_0_1_RELEASE`, revision
`6181e6b5f28a8fbae1d5c3ef6f6626b5a2180213`. No source optimization or twofold
improvement is established. The full-source release-oriented build runs, packages,
and passes the isolated smoke test. Full feature/service parity, release signing,
PGO parity, and standards compliance are not established.

## Fixed comparison

The [predeclared protocol](scored-baseline-plan.json) uses five independent rounds
of one complete vendored Speedometer 3 suite per browser process/profile, with
rotating order. The manifest revision and hash, exact vendored WebKit revision,
browser hashes, toolchain, host, commands, and executed harness snapshot are
retained in the [raw cohort archive](2026-11-09-speedometer-raw.tar.gz).

| Browser | Version | Median score | Paired ratio to Chromium | Exploratory 95% interval |
| --- | --- | ---: | ---: | ---: |
| Debian Chromium | 156.0.8078.79 | 9.924 | 1 | reference |
| Mozilla official Firefox | 159.0.1 | 11.631 | 1.171406 | 1.159376–1.183229 |
| Source stock Firefox | 159.0.1 | 8.941 | 0.903662 | 0.895510–0.916805 |

All 15 scored suites passed. All recorded swap-I/O deltas are zero, and no
executable changed during the cohort. No scored run or outlier was discarded.
The ratio is the geometric mean of paired Firefox/Chromium scores, with 10,000
paired bootstrap resamples and seed 0. These five-round intervals are exploratory.
The [machine-readable summary](baseline-summary.json) contains every score.

This shared six-CPU Debian VM has no GPU. Xvfb and fresh isolated profiles were
used; no sandbox-disable flag was added. These results cover this workload and
host only. The source build uses cross-language ThinLTO without PGO; Mozilla's
official build uses PGO/full-LTO. The official/source difference is not attributed
causally to one flag. The vendored package metadata says `3.0.0-alpha`; the pinned
WebKit commit in `moz.yaml` identifies the actual benchmark, rather than a claim
to have run a separately downloaded latest Speedometer release.

Raptor/Browsertime use benchmark preferences and background/prompt controls.
Those are harness conditions, distinct from shipped defaults. Scores from the
separate diagnostic profile are excluded. The original executed summary snapshot
has a directory-layout bug fixed in the published summary command; raw results
and executed runner are unchanged. See [failure history](failure-history.json)
for all unscored setup/validation failures and their repairs.

## Build, preservation, and verification

[Build evidence](build-evidence.json) records the pinned configuration, actual
toolchain, wrapper failure, build timing limits, swap cleanup, and cache state.
Clang 22.1.8 and Mozilla Rust 1.95.0/LLVM 22.1.2 were used. System Rust 1.99/LLVM 23
was incompatible with cross-language LTO and was replaced only for this build.
The final four-job invocation took 6,395 seconds and reused earlier objects;
this is not a cold full-build time. The libxul shared-library task took
1,136.421 seconds, as exported by profiler-cli from the build resource profile.
Sccache 0.18.0 is installed but was not enabled.

Native mach reports success. The running wrapper had been edited and Bash failed
after mach returned; its exit 2 and original log are retained. The final wrapper
uses `exec`, with isolated exit-propagation checks. Packaging and both official
and source browser smoke tests subsequently passed. Compiler warnings remain
upstream warnings; this is not a warning-free or signed official distribution.

The unmodified runnable control is preserved outside the rebuildable objdir:

```
/srv/development/build/firefox-perf/binaries/source-stock-6181e6b5/firefox/firefox
```

The sibling archive, configuration, source record, toolchain record, runtime, and
unstripped libraries are bound by [SHA-256 records](stock-runtime-SHA256SUMS).
The unbranded runtime does not embed SourceStamp; the verified pinned revision
is recorded externally. Later patched binaries must be compared with this
same-config control, using the same post-integration test tree when tests change.

The task-owned 16 GiB build swap was deactivated successfully after available RAM
could absorb its used pages with headroom, then removed only after verification
that it was inactive. [Cleanup evidence](swap-cleanup.log) records before/after
memory and swapon state. No fstab changes or unrelated process stops were made.

The [source](smoke-source-stock.json) and [official](smoke-official.json) smoke
results cover DOM interaction, storage across reload, Canvas pixels, Wasm42,
WebRTC construction, and default/effective security/PDF preferences. They do not
prove real Safe Browsing/geolocation service operation, DRM playback, hardware
integration, all features, or web standards compliance. Service/key/signing and
telemetry differences are described in the main README.

Fork GitHub Actions are deliberately disabled because the inherited upstream
handler closes/locks PRs from non-Lando members. Local checks are not GitHub CI;
there are no GitHub CI check results. Re-enabling Actions needs a separate
fork-infrastructure change, outside this baseline PR.

## Separate diagnostic profile

[Profile evidence](profile-evidence.json) records the original profile path/hash,
symbol identity, exact analysis scope, source locations, proposed experiment,
and its required correctness checks. The [query archive](2026-11-09-profile-queries.tar.gz)
contains profiler-cli JSON exports and the scoped symbol-server command wrapper.
The original 85 MB compressed browser profile remains on the host; it is not
included in this repository. The unstripped library and packaged runtime have
the same ELF build ID. The owned profiler session was stopped after analysis.

The benchmark content process's main thread dominates recorded CPU. Restricting
it to 1,160 UserTiming sync/async measurement intervals leaves 43,915 active
samples: JavaScript 41.70%, layout 23.45%, DOM 22.87%, and GC/CC 2.98%.
`Element::GetBoundingClientRect` contains 4,927 of those samples (11.22%), almost
all in descendant layout/style work, with zero self samples. Inclusive shares
overlap: geometry, flush, restyle and reflow percentages must not be added.
Required synchronous geometry flushes cannot simply be skipped.

The whole-profile GC/CC share is 15.93%; using that number to prioritize scored
work would overstate cleanup cost. `SnowWhiteKiller::Visit` has 2,143 self samples
over the full recording, but only 372 inside measured intervals (0.85%). A
bounded secondary experiment is to inline its cheap budget/refcount checks and
outline the unchanged zero-reference body. The preserved binary's
[prologue](snowwhite-before.asm.txt) saves six registers and installs a stack
guard before the cheap exits. A source investigation and independent inspection
of the pinned body support this hypothesis, but no change or gain is implemented.
Keep deletion ordering, budgets, tracing and mutation-safe traversal intact;
verify generated code, focused collection behavior, and same-config measurements.
This small sampled share cannot plausibly establish 2x by itself. The larger
geometry-triggered reflow/restyle paths need further attribution before choosing
a behavior-preserving source change.

The full raw profile and build logs remain under the task artifact root on
homelab-dev. The raw benchmark archive is published here; browser binaries,
unstripped libraries, private session state, and the initial private environment
dump are excluded from Git.
