# Pinned standards checks

This bounded WPT harness preserves raw statuses. Expected FAIL remains FAIL;
SKIP/NOTRUN and absent IDs are separate from passes. It is not certification of
universal standards compliance. Smoke selects 15 files generating 33 IDs in the
previous runs. Candidate anchor tests are investigations, not confirmed fork
regressions; see [TRIAGE.md](TRIAGE.md).

## Prerequisites

Use Python 3.11+ and an already provisioned WPT virtualenv with the vendored
requirements. The source WPT sync marker must be
`0364154f9175aa87045a6647869641901b2efceb`; the complete vendored WPT tree is
recorded separately. WPT/profile files must be clean. The source harness checkout
revision is recorded as `source_head`; this does **not** identify the tested
binary's source revision. Binary, engine components, build-config hash and tools
are recorded separately. Supply `--component` for engine binaries behind launch
wrappers (especially Chromium).

Linux execution requires existing noninteractive permission for `sudo -n unshare`
and installed `mount`, `ip`, matching WebDriver, and NSS `certutil` for Firefox.
No permission configuration is changed. Tests run headless as the invoking
non-root UID in private mount and network namespaces with loopback only. The
launcher binds generated WPT mappings to its private `/etc/hosts`, verifies both
namespaces differ from the parent, drops supplementary groups and privileges,
and records isolation evidence. The host `/etc/hosts` and unrelated services
are untouched. No browser sandbox/TLS bypasses are added.

Only explicitly declared `--build-mode noninstrumented-release` is supported.
Confirm this against the actual runtime/build evidence. The seven required
build-mode metadata booleans include ccov/asan/tsan/ubsan=false; instrumented or
nightly binaries require extending and reviewing this interface first.

Optional component installations are declined without prompts. Chromium's
unused automatic MojoJS download is suppressed in the runner setup shim. No
MojoJS or downloaded OpenH264 coverage is claimed. Unexpected prompts fail
closed. Browser processes have no external network. Existing local components
can still be used by WPT; inspect raw logs. No browser feature gate is flipped
by this wrapper. Mozilla metadata and standard WPT automation prefs still apply;
empty metadata is **not** release-default API exposure.

## Prepare, plan and run

Set SOURCE to the pinned checkout, DATA to a runtime directory outside source,
TOOLS to this directory, and PYTHON to the provisioned virtualenv launcher.
Coordinate one compute slot with the build lead before manifest generation or
execution. Both operations take the shared failfast resource lock.

```sh
python3 "$TOOLS/prepare_manifest.py" --source "$SOURCE" --python "$PYTHON" \
  --output "$DATA/manifests-new" --selection "$TOOLS/candidates.txt" \
  --resource-lock "$DATA/resource.lock"
```

Manifest generation uses the vendored `manifest --no-download --no-parallel`
command with its explicit cache outside source and only the selected source files.
A full-tree generation attempt exceeded the coordinated 120-second bound before
producing a manifest; that attempt was retained and no browser started. A sidecar records the generated
manifest SHA-256, exact WPT tree, command, generator/Python hashes and installed
package versions. Reuse requires the same WPT tree, matching hash, selected-file coverage and a
successful read-only load through the pinned WPT loader with URL base `/`. The
offline runner also rejects every attempted update/rebuild rather than falling
back to source-local metadata caches. The test
runner uses `--no-manifest-update --no-manifest-download`, avoiding implicit
source metadata caches. Previous manifests without this sidecar are not silently
accepted; generate a fresh verified one during an approved slot.

```sh
python3 "$TOOLS/run.py" --source "$SOURCE" --python "$PYTHON" \
  --product firefox --label source-stock --binary "$CONTROL_FIREFOX" \
  --certutil "$CERTUTIL" --webdriver "$GECKODRIVER" --metadata mozilla \
  --manifest "$DATA/manifests-new/MANIFEST.json" \
  --build-mode noninstrumented-release --tmp-root /tmp \
  --resource-lock "$DATA/resource.lock" --output "$DATA/plan-new"
```

This writes a plan and does not launch WPT or a browser. After reviewing it and
receiving a slot, repeat with a fresh output directory and `--execute`. Repeat
for official and fork runtimes with matching selections/metadata/environment.
Use `--selection "$TOOLS/candidates.txt"` for the focused anchor pair.
For Chromium use `--product chromium --metadata empty`, matching chromedriver,
and the actual Chromium engine with `--component`. It remains a comparator,
not the normative specification.

Execute writes invocation evidence before launch, contemporary binary/component
and tool hashes, package versions, harness hashes, runner command, explicit
allowlisted environment, host mappings, namespaces, reports, raw logs and exit
status. Source identities describe the harness/test checkout. Namespace or
runner startup failure is not a test result. Short unique runtime directories
are created under `--tmp-root` (at most 20 characters) to avoid Unix socket path
limits. They remain recorded and retained for explicit owner cleanup; remove
only that invocation's directory after collecting needed diagnostics.

```sh
python3 "$TOOLS/summarize.py" "$DATA/fork-run" --compare "$DATA/control-run"
WPT_TEST_ROOT="$SOURCE/testing/web-platform/tests" WPT_TEST_PYTHON="$PYTHON" \
  python3 -B -m unittest discover -s "$TOOLS" -p 'test_*.py' -v
```

A nonzero runner exit is retained, even when a complete report can be summarized.
Inspect raw logs and report run_info. Empty/incomplete reports, dry plans and
duplicate attempts are rejected. Comparison rejects different WPT trees or
selections and lists missing IDs. Other configuration/build/font differences
still require review. Full WPT, Test262, AT/screen-reader, GPU, mobile, device,
network-dependent service and security audit coverage remain unrun.

## Existing evidence and this candidate

Six real earlier machine-local runs are described in the area evidence delivery.
They validate that earlier scratch execution setup, **not this portable candidate**.
The first successful Firefox smoke wrapper hash was reconstructed later and is
explicitly not contemporaneous; do not replace this disclosure with the new
harness hashes. Existing reports and raw evidence must be retained unchanged.
This candidate requires a new real execution and independent final review before
publication/merge acceptance. The focused unit checks cover preparation and
report-integrity contracts, not browsers or privileged namespace setup.

Binary source provenance must remain separate from `source_head` (the harness checkout).
Supply `--binary-source-revision` from the preserved runtime's build record. If
omitted it is recorded as null/unknown; the wrapper never substitutes checkout
HEAD. Binary/component hashes bind the execution to the preserved runtime but do
not independently prove its source revision.

## Earlier actual bounded execution (2026-11-09)

The earlier scratch execution completed six serial headless WPT runs, before
this candidate existed. Source-stock and official Firefox each emitted all
33 smoke IDs: harness 2 PASS/31 OK, subtests 3076 PASS/145 FAIL; exit 0. Chromium
emitted all33: harness 2 PASS/31 OK, subtests 3027 PASS/194 FAIL; exit1. The
two anchor IDs each produced harness OK; both Firefox controls had 0 PASS/2 FAIL
subtests and Chromium2 PASS/0 FAIL, all runner exits0. No missing/extra expected
IDs or emitted ERROR/TIMEOUT/CRASH/SKIP/NOTRUN/PRECONDITION_FAILED were recorded.
Expected FAIL remains FAIL. Chromium used empty metadata, Firefox Mozilla
metadata including experimental prefs. This is neither full WPT nor stock API
exposure, and none of these runs validates this production candidate.

The saved harness checkout was167641452ead1de8f552ce21c05f1bea717927b7; this
is NOT the binary source revision. Sealed source-stock build provenance points
to6181e6b5f28a8fbae1d5c3ef6f6626b5a2180213. Its recorded executable SHA256
is c3b28015d5ac8d894ea95cb897c90fb2c944b9c159ee7cf66e5260124ca0b414 and
libxul SHA25654063bdc4f55e5d671731166c60be5d96de8883d286058283fbdf00b626f887d.
The initial Firefox executed-wrapper hash was reconstructed afterward; it was
not contemporaneously captured. Original raw reports, invocations and failed
setup attempts remain preserved in the program's runtime artifact archive
(`wpt-runs`); this paragraph is a bounded summary, not a substitute for raw
reports. See [standards acceptance issue8](https://github.com/MGRAFF2006/firefox/issues/8)
and [program hub5](https://github.com/MGRAFF2006/firefox/issues/5).
