# Network/cache marker overlap diagnostic

`analyze.py` reads a raw Gecko profile (JSON or JSON.gz) and compares recorded
Network/FileIO intervals with the union of UserTiming **measure** intervals
whose names end in `-sync` or `-async`. It aligns process-local timestamps using
`meta.startTime`. Marks and preparation intervals do not define measured time.
If multiple timing threads qualify, select one explicitly with `--timing-tid`.
Incomplete I/O intervals are counted separately, without invented durations.

Run with Python 3.11+ and no additional dependencies:

```sh
python3 -m unittest discover -s tools/perf-platform-network -v
python3 tools/perf-platform-network/analyze.py /path/to/geckoProfile-1.json.gz > network-overlap.json
```

Use an already captured profile; this tool starts no browser or profiler.
The report includes its compressed-input SHA-256, marker groups by recorded
status/cache disposition, individual overlapping events with request IDs for
cross-process correlation, and captured thread names. It omits URIs, filenames
and stacks. Keep the raw profile outside Git.

This is a diagnostic, not a production optimization. Request intervals may
include waiting, run concurrently or duplicate the same request in parent and
child processes. Their durations cannot be summed into CPU cost, blocking time
or a score delta. A category or dispatch ancestor is not function self-cost.
Missing FileIO markers and uncaptured socket/cache/storage threads cannot prove
that those mechanisms were idle. This tool does not validate full suite/step
coverage; use the scored harness's workload validation separately.

## Historical reproduction and interpretation

The preserved diagnostic profile's SHA-256 is
`d586a53daabb2dc0a5e593fce083ce3dedfc056334ba6e49fbc77f1ba207aa8d`.
Its location is recorded in
`tools/performance/baseline/evidence/profile-evidence.json`.
It comes from source revision `6181e6b5f28a8fbae1d5c3ef6f6626b5a2180213`,
appBuildID `20261108230948`. The full scored matrix used another invocation and
build (`20261109005556`). Neither establishes current runtime behavior.

The diagnostic selects TID 3234334 and finds 1,160 measure intervals with
116 distinct names, covering 66,784.923528 ms after union. All 2,302 recorded
Network starts, 974 parent cache-hit completions and 158 parent cache-miss
completions have zero overlap with that union. Seven overlapping event records
represent four request IDs: all four have parent STATUS_CANCEL/Hit records,
and three also have child STATUS_STOP records without a cache disposition.
Do not count those seven records as seven independent requests or infer that
cancellation itself consumed the reported interval as CPU.

There are no FileIO markers and no socket/cache/storage thread names among the
86 captured threads. This resolves the narrow historical question of whether
recorded ordinary cache-hit/miss completion intervals overlap measured work;
it does not justify changing cache policy or imply zero networking cost.

Next item: run the same command against an existing profile of the accepted
current workload when Integration supplies it, bind the result to its runtime
and harness hashes, and inspect any overlapping request IDs before considering
a source optimization. If the current profile lacks I/O thread/features, report
that coverage gap to Integration rather than infer absence or launch profiling.

The upstream marker labels were checked using Searchfox
`NetworkMarker::GetNetworkState` and `NetworkMarker::GetCacheState` in
`netwerk/protocol/http/NetworkMarker.cpp`. Its Git blob is
`88c8dd82673cf1f16f6fb2bd1a94702917c257dd` at both task base
`42b6683257d3f2950e9676b986ac3b42e4563f14` and pinned runtime base
`99cd4fee1a1ce503950d2f8b99167ef5b8ca5911`. No source behavior changes here.
