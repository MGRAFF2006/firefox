# Isolated Firefox feature and service inventory

This harness checks real local WebRTC data transfer, an IndexedDB transaction
across reload, permanent unsigned-extension rejection, and required default and
effective security/feature configuration. It separately inventories distribution
constants and service provisioning as `missing`, `sentinel` or
`configured-unverified`; it never exports API-key values.

These checks do not prove online service operation, PDF rendering, licensed DRM
playback, crash submission/symbolication, trusted automatic updates, hardware,
native accessibility, other platforms, standards conformance or performance.
Sandbox preference validation does not independently prove effective OS sandbox
enforcement. Updater/crashreporter constants do not prove service operation.
Linux EME opt-in and unofficial telemetry behavior remain explicit differences.

## Dependencies and execution

Use Linux with Python3, Node, `unshare`, `ip`, user/network namespace support and
the existing `tools/browsertime/node_modules` Selenium/geckodriver installation.
No packages or optional downloads are installed by the runner. It needs existing
preserved official/custom Firefox executables, a writable build root for the
shared `resource.lock`, and an owned output location. Do not point it at a
personal browser profile or modify preserved runtimes.

Acquire a resource window from the build owner before running browsers. For
example, from the source checkout, substitute owned absolute paths:

```sh
python3 tools/parity/run.py \
  --root /path/to/build-root \
  --source /path/to/source-checkout \
  --official /path/to/preserved-official/firefox \
  --custom /path/to/preserved-custom/firefox \
  --output /path/to/new-owned-output \
  --node /path/to/node
```

The output directory must not exist. `--node` otherwise uses the Node executable
on PATH; `--fixture` defaults to the adjacent unsigned XPI. Optional
`--official-source-record` and `--custom-source-record` supply existing provenance
records explicitly; they are not inferred from the harness commit. All paths
must refer to the intended host. The runner holds the resource lock nonblocking and runs the
official binary before custom, one fresh headless profile at a time. A failure
stops the comparison; it is retained rather than discarded or retried silently.

Networking stays inside a disposable user/network namespace: loopback serves
WebDriver, dummy `192.0.2.1/32` serves test content, and only the
`224.0.0.251/32` route supports local mDNS. There is no default route, veth or
external interface. No ICE, signature or other security preference is relaxed.
The only profile preference explicitly set is
`remote.prefs.recommended=false`, to retain normal security defaults while
using WebDriver. Privileged remote access is limited to the disposable test
session for configuration inspection. Do not invoke `runtime_checks.cjs`
directly as a network-isolation substitute.

The original loopback-only attempt failed WebRTC even in official Firefox.
Stock ICE excludes loopback candidates; without media permission it also uses
default-address selection based on the page route. The isolated dummy/content
address and local mDNS route supply the missing test environment while
preserving those policies. Candidate counts and ICE states are captured without
raw candidates or addresses. Local transport success does not prove external
STUN/TURN, audio/video, codecs or physical device operation.

## Verification and evidence

Run `node tools/parity/test_reporting.cjs` and
`PYTHONDONTWRITEBYTECODE=1 python3 tools/parity/test_runner.py` for reporting/supervisor regressions without
starting Firefox. No browser execution was authorized for the later reusable
runner during its productionization; the historical successful browser run
used the explicitly identified scratch predecessor.

`evidence/paired-runtime.json` retains the successful official159.0.1/custom
comparison; `evidence/initial-failure.json` retains the earlier official-only
failure and incomplete-comparison limit. The custom runtime was built from
`99cd4fee1a1ce503950d2f8b99167ef5b8ca5911`. The integration merge and this
harness's later publication commit are separate from that runtime source.
Existing artifact manifests and source metadata are provenance records, not
new runtime hashes computed by this harness or reproducible-build proof.

In that run both browsers delivered the WebRTC payload, retained IndexedDB
data across reload, rejected the unsigned permanent fixture, and passed the
required configuration gate. The custom runtime's three API-key states were
`sentinel`; the official runtime's were `configured-unverified`. SafeBrowsing
preferences can remain enabled despite missing Google-provider provisioning.
No online requests were possible in the isolated namespace. Do not turn these
results into an official service-parity claim.

The fixture is a small unsigned XPI containing only a manifest with a synthetic
extension ID; it is intentionally rejected. Do not sign it or use a temporary
installation path to make that check pass.

Track operational provisioning, legal service entitlement, signed fork updates,
DRM and broader platform gates in [parity issue9](https://github.com/MGRAFF2006/firefox/issues/9),
linked to [program hub5](https://github.com/MGRAFF2006/firefox/issues/5).
