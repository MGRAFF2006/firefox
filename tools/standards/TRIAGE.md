# Standards compatibility triage, 2026-11-08

Owner: standards-research, leaf researcher; T3 Code/Codex, gpt-6.1-sol, high.
Scope: read-only source and primary-source investigation. No browser tests ran in
this assignment, no source/expectations changed, no compliance claim established.
Source inspected with `git show HEAD:<known path>` and bounded directory listings
at Firefox `6181e6b5f28a8fbae1d5c3ef6f6626b5a2180213` (159.0.1 release).
`searchfox-cli` was absent on PATH; notified area owner. Did not run competing
bootstrap during build. Exact engine implementation symbol tracing remains open.

## Acceptance and coverage

Use the pinned source's vendored WPT and its Mozilla expectations, record source
HEAD, test tree identity, browser binary hash/version, platform, run configuration,
all preference overlays and test IDs. Compare fork to an unmodified same-source
build when available; official Firefox159.0.1 is a useful secondary control but
packaging, build options and distribution differ. Chromium156.0.8078.79 is an
interoperability comparator, never an oracle. Report file and subtest outcomes
separately; expected failures are upstream gaps, not passes. Also report skipped,
disabled, crash, timeout, harness-error and unrun tests. An empty suite is no proof.

Mozilla documents `mach wpt <paths>` and `--product chrome`; Chrome ordinarily
uses no Firefox expectation metadata. Keep product-native expectations when
classifying expected status and compare raw outcomes. Firefox metadata can also
set preferences: preserve and disclose these rather than calling their results
stock release behavior. [Mozilla WPT docs](https://firefox-source-docs.mozilla.org/web-platform/index.html).

Coverage inventory to expand in stages:

- HTML/DOM: parsing, forms, dialog/popover, custom elements, navigation/history,
  events, selection, shadow DOM; WPT html/, dom/, custom-elements/, shadow-dom/.
- CSS/layout: parsing/CSSOM, selectors, sizing/overflow, fonts, flex/grid, writing
  modes, positioning, animation and rendering via testharness plus reftests;
  representative computed-value tests alone miss visual correctness.
- Web APIs: fetch/streams, URL/encoding, workers/service workers, Web Storage,
  IndexedDB, WebIDL, media, WebRTC, WebTransport and WebAssembly integration;
  device/permission/network-dependent tests need distinct environment coverage.
- JS/ECMAScript: WPT covers browser integration, not full language conformance.
  Use vendored Test262 via `mach jstests` (shell) or `mach jstestbrowser` (browser),
  plus targeted existing jit-tests for engine repairs; track proposal flags and
  ECMA-262/402 edition. [Mozilla JS test docs](https://firefox-source-docs.mozilla.org/js/test.html),
  [official Test262](https://github.com/tc39/test262).
- Accessibility: WPT accessible name/role and ARIA reflection tests are useful but
  do not establish native AT-SPI/IA2/AX mapping, assistive technology interaction,
  keyboard UX or WCAG conformance. Include existing accessible/ tests on relevant
  OSes and real assistive technology checks separately. [Core AAM](https://www.w3.org/TR/core-aam-1.2/)
  describes platform mappings; [Interop2026](https://github.com/web-platform-tests/interop/blob/main/2026/README.md)
  treats accessibility testing as an infrastructure investigation.
- Networking/security: CORS, CSP, mixed content, secure contexts, cookies/storage
  partitioning, referrer policy, permissions, navigation isolation, TLS and HTTP
  behavior. Do not disable protection to make tests pass. WPT is not a security
  audit or a complete protocol conformance suite.
- Graphics/device limits: this VM has no GPU. WebGPU requires separately vendored
  CTS under testing/web-platform/mozilla/tests/webgpu; the upstream WPT webgpu/
  directory at this pin contains documentation only. Desktop headless Linux also
  does not establish mobile, GPU-driver, screen-reader, camera or audio behavior.

Interop2026 selects twenty focused areas, not the entire platform; its official
README links the associated specifications and test labels. Living standards,
W3C Recommendations, Candidate Recommendations, Working Drafts, Editor's Drafts
and WICG proposals must retain their status in gap classification. An implemented
Chromium proposal is not thereby a mandatory web standard. Optional features,
allowed implementation limits and intentional privacy restrictions need separate
analysis from violated normative requirements.

## Prioritized candidate gaps

These are source/expectation-supported hypotheses to reproduce, not measured
fork defects. Pin-relative source links use
https://github.com/MGRAFF2006/firefox/tree/6181e6b5f28a8fbae1d5c3ef6f6626b5a2180213 .

### 1. Anchor positioning with animated transforms or chained anchors

Reproduce `/css/css-anchor-position/transform-010.html` and `transform-023.html`.
Their pinned metadata records failing subtests (animation update and chained
anchors respectively). StaticPrefList's
`layout.css.anchor-positioning.follows-transforms.enabled` defaults false.
The former uses ResizeObserver to observe sizes during anchor translation; the
latter checks hit-testing after scrolling a transformed container with chained
anchors. Compare stock and fork at identical viewport/timing settings.

Rationale: [CSS Anchor Positioning](https://drafts.csswg.org/css-anchor-position-1/)
describes transform effects, but also contains unresolved timing/transform issues;
it is a Working Draft. Confirm exact intended rule before repairing a marginal
timing result. Start tracing from the named preference into layout positioning
once searchfox is available; test and metadata paths above are verified. Do not
flip this gate globally without implementation/reftest evidence. This is the
best focused repair candidate after actual baseline reproduction.

### 2. Cross-document view transitions

Reproduce `/css/css-view-transitions/navigation/at-rule-opt-in-auto.html`.
Its pinned metadata expects TIMEOUT at file and subtest level. The test opens a
same-origin window, navigates and expects a pagereveal event with a transition.
`dom.viewTransitions.cross-document.enabled` defaults false in StaticPrefList.

Rationale: [CSS View Transitions Level2](https://drafts.csswg.org/css-view-transitions-2/)
is an Editor's Draft and an Interop2026 focus. Preserve same-document support;
its existence does not prove cross-document support. Trace navigation/pagereveal
and capture lifecycle, BFCache, cancellation, cross-origin restrictions before
any enablement. Likely a larger upstream feature completion rather than a safe
one-line source repair. A timeout is still a failure requiring explanation even
when Mozilla expects it.

### 3. CSS Typed OM stock-release availability differs from WPT configuration

`dom/webidl/CSSStyleValue.webidl` gates exposure on
`layout.css.typed-om.enabled`; StaticPrefList sets it to `@IS_NIGHTLY_BUILD@`.
However `testing/web-platform/meta/css/css-typed-om/__dir__.ini` forces Typed OM
and color-interface preferences on (along with other CSS preferences).
Run `/css/css-typed-om/idlharness.html` using normal metadata as implementation
coverage, then independently record clean-release exposure of CSSStyleValue,
attributeStyleMap and computedStyleMap. Do not describe the former as shipped API
availability. Keep metadata intact and record its overlays.

Rationale: [CSS Typed OM Level1](https://drafts.css-houdini.org/css-typed-om-1/)
is an Editor's Draft. The implementation has WebIDL and tests already; inspect
actual remaining failures and release criteria rather than writing a parallel API
or blindly enabling preferences. This is a concrete reporting risk immediately
relevant to the standards harness.

### 4. WebGPU Linux/device support is an environment and release-policy gap

StaticPrefList enables `dom.webgpu.enabled` by default on Windows/Apple Silicon
macOS; other platforms use the early-beta-or-earlier condition. Linux release
therefore differs. The no-GPU VM cannot substantiate hardware conformance.
`testing/web-platform/tests/webgpu/README.md` directs browsers to pin the external
CTS, present here in Mozilla's private WPT tree. Run that only on a suitable
isolated hardware target after recording adapter/backend details; do not replace
it with an empty upstream directory or force an unsafe backend.

[WebGPU](https://www.w3.org/TR/webgpu/) is a Candidate Recommendation Draft and
permits lack of a suitable adapter; unavailable hardware is not automatically a
normative implementation bug. Inspect actual API exposure separately from adapter
availability. No feature removal or security bypass is justified by Chromium's
availability on a different OS/device.

### 5. Network Information API is an intentional privacy restriction

`dom.netinfo.enabled` defaults false; its adjacent pinned source rationale cites
user-information exposure and bug1637922. `/netinfo/idlharness.any.js` and
`/netinfo/netinfo-basics.html` exist for investigation. The [Network Information
API](https://wicg.github.io/netinfo/) is a WICG proposal. Treat absent
navigator.connection as a documented compatibility difference and privacy choice,
not evidence that Firefox violates every standard. Enabling it would need a
separate privacy/design decision and implementation testing, not a pass-rate fix.

## External results context, exact applicability

Read official `https://wpt.fyi/api/runs?product=firefox&label=stable&max-count=1`
and the equivalent chrome query on 2026-11-08. Firefox run5140459022974976:
159.0.1, Linux22.04, 2026-11-08T03:28:20.477Z, WPT
fea36e49ddda8a206a5d3489ac377c8b9f406af9. Chrome run5070179567140864:
157.0.8103.38, Linux22.04, 2026-11-08T00:42:08.199Z, same WPT hash.
Thus Firefox's version matches but OS/configuration/test revision may differ;
Chrome is newer than locally installed Chromium156.0.8078.79. These run identities
are context only. No external pass percentages or local fork measurements were
inferred. Direct dashboard pages were not readable through web extraction;
API JSON supplied identities. Current pages/spec drafts are not frozen to this
Firefox release and must not silently override its vendored test semantics.

## Next actions

1. Area owner runs coordinated small baseline suites and the two anchor tests
   against actual binaries; retain raw failures and run_info.
2. Triage candidate1 on exact source and reproduce before requesting a bounded
   engine patch; do not patch shared active-build source.
3. Keep shipped-feature exposure and metadata-enabled WPT evidence distinct.
4. Expand representative coverage with explicit unrun-domain ledger; schedule
   full WPT/Test262/native accessibility and hardware runs as separate compute
   milestones. Universal web-standards compliance remains unproven.
