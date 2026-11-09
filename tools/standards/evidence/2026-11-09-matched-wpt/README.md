# Matched WPT evidence: 2026-11-09

This evidence-only package publishes the unchanged raw reports from two completed,
bounded Firefox comparisons. It does not add tests, modify the engine, fix the
Typed OM failures, or establish universal standards compliance, release-default
API exposure, native accessibility, service parity or a speed improvement.

The package's eventual Git commit is a **publication revision**, distinct from
all execution identities below. Packaging did not rerun any workload.

## Reports and recorded outcomes

- Phase2 smoke selected 15 source files, generating 33 runnable IDs. Both preserved
  runtimes completed all IDs: harness 2 PASS/31 OK; subtests 3076 PASS/145 FAIL;
  zero unexpected outcomes. Every named harness/subtest status, expected field,
  message and known-intermittent field matched across the pair (3254 records
  each). Expected FAIL remains FAIL.
- Stage2 Web API selected six additional source files, generating seven runnable
  IDs. Both runtimes completed all IDs: harness 7 OK; subtests 602 PASS/8 FAIL;
  zero unexpected outcomes. All 617 named records matched across the pair.
  The eight expected failures are Typed OM IDL assertions, not evidence here of
  an engine root cause or an eight-test repair.

The report files preserve the original bytes, including timing and run_info;
only named outcome records, not durations/timestamps, were compared for equality.
No duplicate, missing, extra or unresolved selected IDs were found. Generated-ID
coverage is derived from each actual selected manifest, not assumed from filenames.
See [summary.json](summary.json) for exact IDs and counts.

Raw reports, with contemporaneously recorded hashes checked during packaging:

- [Smoke: stacked runtime](phase2-smoke/stacked/wptreport.json)
- [Smoke: sealed stock runtime](phase2-smoke/source-stock/wptreport.json)
- [Web API: stacked runtime](stage2-webapi/stacked/wptreport.json)
- [Web API: sealed stock runtime](stage2-webapi/source-stock/wptreport.json)

These links identify package files. A publishing agent must separately verify
public download byte identity at the actual evidence publication commit; this
scratch preparation does not claim that a public download has already occurred.

## Execution identities and contemporaneous captures

The accepted tooling merge was
`72b742f61cfe1fff4426608dcd2ff8de6d28872e`. Its unchanged
[export manifest](accepted-tooling-manifest.json) has SHA-256
`db3690de2ed3d90c62a45ab8602e77b55ad70b30d202f2f9b1b5f6b812fb96de`
and binds 14 exported tool files. The export manifest's preparation-time cutover
text is preserved; it is not rewritten to describe a later scheduler adoption.

The source checkout used for the WPT runner/tests/metadata/profiles was
`99cd4fee1a1ce503950d2f8b99167ef5b8ca5911`. This is not a substitute for
browser binary source provenance. The tested runtime source revisions were
recorded separately from preserved build records:

- stacked runtime: `99cd4fee1a1ce503950d2f8b99167ef5b8ca5911`;
- sealed source-stock runtime: `6181e6b5f28a8fbae1d5c3ef6f6626b5a2180213`.

Each original invocation records the declared source-revision scope and the
contemporaneously captured executable, libxul/components, driver/certutil/Python
and four executed harness-file hashes. Packaging verified report hashes and
captured-record consistency; it did not rehash browser binaries, independently
rebuild them or turn source declarations into embedded source attestations.

Each suite directory includes these unchanged original captures:

- `selection.txt`: exact executed selection bytes, hash-matched to both invocations;
- `MANIFEST.json` and `MANIFEST.provenance.json`: actual selected manifest and its
  original tree/upstream/hash/generator/package provenance;
- `accepted-tooling.json`: **one aggregate pair-level sidecar**, not a separate
  per-command/per-run accepted-merge/manifest record;
- `prepare-manifest-command.json`, `stacked-command.json` and
  `source-stock-command.json`: the original command/minimal-environment records;
- each runtime's `invocation.json`: its original WPT command, run configuration,
  runner exit, source identities and own component/tool/harness hash set.

Both per-run harness hash sets agree. No historical per-command merge/manifest
sidecar was invented or backfilled. Later supervisor capture improvements are
not execution evidence for these historical runs.

The captured commands contain machine-local paths and a minimal allowlisted
environment. They document the actual jobs; **do not execute them blindly** on
another machine. Reproduction needs an accepted tooling snapshot, the matching
vendored source/test trees, provisioned tools, preserved runtimes or independently
built controls, fresh output/profile paths and an explicit resource admission.
Existing report paths refer to historical artifacts and must not be overwritten.

## Conditions and evidence limits

Execution was serial, headless, offline in private mount/network namespaces,
with fresh profiles and matched Mozilla WPT metadata. Metadata and standard WPT
preferences can enable experimental features. In particular, Typed OM WPT
coverage is separate from a clean-release API exposure check. Empty or absent
metadata would not itself establish release-default settings either.

The original audit snapshots support agreement of source HEAD, WPT/profile Git
status, cache absence and hosts identity at their recorded boundaries. They do
**not** prove full index/config/objdir invariance or continuous resource peaks.
Memory/PSI values are recorded observations and configured guards, not complete
continuous peak measurements. Paging statements mean **no recorded swap
activity**; ordinary `pgpgin` disk paging increased. Saved cleanup/no-survivor
statements are the author's cleanup evidence, not independent continuous process
monitoring. Resource/isolation snapshots and runtime directories are deliberately
not copied into this small public package; they remain in the original runtime
artifact records and the independent audit.

Original runtime artifact cohorts:

- `ROOT/teams/standards/phase2-smoke-20261109T090038`;
- `ROOT/teams/standards/stage2-webapi-20261109T090654`.

Here ROOT denotes the original Firefox program runtime root. Preserved public
outcome records: [phase2](https://github.com/MGRAFF2006/firefox/issues/8#issuecomment-6735192891)
and [stage2](https://github.com/MGRAFF2006/firefox/issues/8#issuecomment-6735271516),
including their later audit qualifications.

Clean-release exposure, native AT/platform/hardware checks and diagnostic fixture
execution are not added by this package. Full WPT/Test262/security/protocol
conformance remains outside this evidence. The earlier failed full-tree manifest
preparation is not converted into a success; its separate historical record is
outside these two completed cohorts.

## Byte verification and publication scope

From this directory, `sha256sum -c SHA256SUMS` verifies all package payload files.
The four raw report hashes in [publication-checks.json](publication-checks.json)
match the original contemporaneous validated-evidence records. Original report,
selection, manifest and provenance files are copied byte-for-byte with no
redaction or normalization. Summary/checks/this README are new packaging output,
not backdated runtime captures. Their contents are independently checksummed too.

The explicit publication allowlist excludes profiles/runtime directories,
authentication state, private MAR/artifacts, browser/console/runner logs and agent
transcripts. Recorded minimal environments were checked against an explicit key
allowlist. Credential-pattern screening and field/content review found no
credential material requiring redaction. Standard WPT fixture strings can contain
synthetic URL userinfo; those are test data, not real account credentials.
Machine-local paths, the public account name and host/platform metadata in
commands/invocations are intentionally retained for provenance. This is a scoped
privacy review of known controlled captures, not a proof that an arbitrary file
cannot contain any secret.

Original execution/evidence author: `firefox-standards-lead`.

> **Agent:** `firefox-standards-evidence-packager` · **Role:** evidence packaging worker
> **Runtime:** T3 Code / Codex / OpenAI · **Model:** `gpt-6.1-sol` · **Reasoning:** high
> **Context:** `/root/public_evidence_package`
