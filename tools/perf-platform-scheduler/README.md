# Parser deadline attribution diagnostic

Diagnostic only: no Firefox behavior, build flags, preferences, or runtime changes.
Python 3.11+ standard library only. Accepts raw Gecko row-table profiles, including
gzip; processed Firefox Profiler tables are deliberately rejected.

```sh
python3 -m unittest discover -s tools/perf-platform-scheduler -v
python3 tools/perf-platform-scheduler/analyze.py /path/to/geckoProfile-1.json.gz \
  --source-revision 6181e6b5f28a8fbae1d5c3ef6f6626b5a2180213 \
  --expect-sha256 d586a53daabb2dc0a5e593fce083ce3dedfc056334ba6e49fbc77f1ba207aa8d
```

Selects the unique GeckoMain thread carrying `-sync`/`-async` UserTiming measures;
use `--pid` if multiple threads qualify. All intervals and samples stay in the
selected process's clock domain. It unions measured windows, clips parser
intervals to those windows, counts deadline-yield points, and reports sample
quantiles of raw eventDelay separately. Interval endpoints are half-open. Split
parser markers are paired as nested scopes; unmatched endpoints are reported.
Nonzero unmatched endpoints make parser coverage incomplete. This tool does not
reconstruct profiler input-delay curves or infer queue residence from eventDelay.

## Named mechanism resolved

Hypothesis: deadline-driven HTML tree-operation continuation contributes to
measured Speedometer overhead. `historical-result.json` is the actual SHA-verified
run on the preserved historical diagnostic profile, source `6181e6b5…`.
It reproduces the independently archived 1,160 measures and 43,915 measured samples.
All 37 `HTMLParserTreeOpsYieldedOnDeadline` points and all 2,546.006463 ms of recorded
`HTMLParserTreeOps` interval coverage lie outside measured windows. No parser
interval endpoints are missing. The hypothesis has no observed support in these
historical measured windows; changing parser yielding on this evidence is unjustified.

This is specifically the streaming tree-op deadline mechanism. It does not exclude
synchronous fragment parsing. Existing symbolicated exports show that distinction:
`export-self-evidence.json` preserves selected rows, scope filters and input hashes.
CodeMirror covers only Long/Highlight sync+async (627 samples); Todo covers only
TodoMVC-jQuery.CompletingAllItems sync+async (3,071 samples). In both, TaskController
RunTask and DoExecuteNextTaskOnlyMainThreadInternal have zero self samples despite
568 and 2,839 inclusive samples respectively. These inclusive ancestors are not
dispatch overhead, task counts or queue timing. Zero samples is not zero true cost.
Todo's tokenizer `stateLoop<nsHtml5FastestPolicySIMD>` has 42 self and 1,114 inclusive
samples; `appendStrBuf(char16_t)` has 10 self samples. Fragment ParseFragment has
1 self and 1,132 inclusive samples. Do not attribute its descendants to parser self.

Raw eventDelay is queue-delay plus running-event elapsed time in
`tools/profiler/core/platform.cpp`. `TaskController.cpp` zeros the queue-delay part
for tasks below input priority or without an insertion timestamp. The measured
raw p95 of 309.693004 ms therefore does not establish scheduler overhead.
Missing/negative values are reported separately rather than replaced with zero.

CPU diagnostic root metadata appBuildID is `20261108230948`; that label refers to
root profile metadata, not independently verified selected-child identity. The
scored matrix root `/srv/development/build/firefox-perf/runs/2026-11-09T02-03-12+0100`
has build `20261109005556` and a different invocation. Its s3_internal contract is
10 iterations, 20 suites and 58 steps. This diagnostic must not be presented as a
profile of the current pinned runtime or as a score change. No new profiling ran.

## Next focused measurement

Use the existing historical symbolicated profile, restore the exact Todo filter
from `todo-complete-status.json`, and request
`profiler-cli function annotate f-19758 --mode all` to attribute the tokenizer's
42 **self** samples to source/assembly. Inspect `appendStrBuf`'s 10 self samples
separately. This is offline annotation, not a browser run or rebuild. Do not
mutate another owner's active profiler session: coordinate a session with BUILD.
Compare the sampled branch/loop body against the pinned source before proposing
a bounded tokenizer change. Preserve buffer capacity checks, malformed-input
handling, UTF-16 behavior and generated-parser source consistency. If annotation
does not isolate redundant work, no source change is warranted from these counts.
Do not pursue TaskController dispatch tuning from inclusive ancestry alone.

## Integration and source provenance

Source was read-only; scratch is not a Git checkout. ROOT owns serial integration,
commit, push and PR. Apply only the supplied new-file patch; no raw profile or
authentication state is included. No build is needed for this diagnostic. Rerun
the unit command and SHA-bound profile command above after integration.

The following inspected source blobs are identical at task base
`42b6683257d3f2950e9676b986ac3b42e4563f14` and pinned runtime base
`99cd4fee1a1ce503950d2f8b99167ef5b8ca5911`:

| Path | Git blob at both bases |
| --- | --- |
| xpcom/threads/TaskController.cpp | 61fcb7013b0c77b07278f45b5dbb4eaf33aae346 |
| parser/html/nsHtml5TreeOpExecutor.cpp | 37d55ec1f13733547aa1af82100dd82c4372fd58 |
| tools/profiler/core/platform.cpp | 385f008bc8a7574e9c4175303b87dc636ab1c9e4 |

`tools/perf-platform-scheduler/` is absent at the task base, so all patch entries
have an absent base blob. Searchfox CLI queries verified upstream identifiers
and parser marker semantics; pinned local bodies were inspected for differences.
Installed Searchfox 0.19.0 reports 0.20.6 available; no tool/config upgrade made.

Independent diagnostic reviewer `firefox-platform-scheduler-review-20261111`
found no blocker and ran all seven tests. Reviewed analyzer SHA-256
`9bfd1d4b3d1c962b4f7aef538bd775c90b9dc10a6765ec2743708b943a512051`
and tests `7c0c9ad48af82447d2033d16b17768d30ddcfce2129c9a067036c10fb3c3fdb9`.
Subsequent owner fixes close the digest input file and clarify the root-metadata
label; reviewer did not verify the real-profile run or current-runtime behavior.
ROOT's independent final exact-commit review remains required. No merge approval.

Owner: `firefox-platform-scheduler-parser-20261111`.
