/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { parseArguments, harnessPaths, childEnvironment, parseProc, securityDecision, collectSecurity } = require('./smoke.cjs');

function fixture() {
  const browser = { pid: 100, parentPid: 1, startTicks: '1000', state: 'S', exe: '/test/firefox',
    seccomp: 2, filters: 1, noNewPrivs: 0, userNamespace: 'user:[1]', pidNamespace: 'pid:[1]' };
  const content = { ...browser, pid: 101, parentPid: 100, startTicks: '1010', filters: 2, noNewPrivs: 1 };
  const snapshot = { pid: 100, type: 'browser', tabPid: 101, tabURL: 'http://127.0.0.1:12345/',
    children: [{ pid: 101, type: 'webIsolated', childID: 7 }] };
  return { binary: '/test/firefox', browserPid: 100, expectedURL: 'http://127.0.0.1:12345/', first: structuredClone(snapshot), second: structuredClone(snapshot),
    before: { 100: structuredClone(browser), 101: structuredClone(content) },
    after: { 100: structuredClone(browser), 101: structuredClone(content) } };
}

function rejected(evidence, reason) {
  const decision = securityDecision(evidence);
  assert.equal(decision.decision, 'reject-or-unresolved');
  assert.match(decision.issues.join('; '), reason);
}

test('tools-only code selects dependencies from explicit source, retaining full-checkout default', () => {
  assert.equal(parseArguments(['/tmp/firefox']).source, path.resolve(__dirname, '../../..'));
  const args = parseArguments(['--source', '/test/harness', '/tmp/firefox']);
  assert.equal(args.source, '/test/harness');
  assert.deepEqual(harnessPaths(args.source), {
    selenium: '/test/harness/tools/browsertime/node_modules/selenium-webdriver',
    firefox: '/test/harness/tools/browsertime/node_modules/selenium-webdriver/firefox',
    geckodriver: '/test/harness/tools/browsertime/node_modules/@sitespeed.io/geckodriver/vendor/geckodriver',
  });
  for (const args of [[], ['/tmp/firefox', '--profile', '/tmp/profile'], ['/tmp/firefox', '--no-sandbox'], ['--source']]) {
    assert.throws(() => parseArguments(args));
  }
});

test('selected dependency tree is loadable without importing it when smoke is required', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'smoke-source-'));
  try {
    const paths = harnessPaths(root);
    fs.mkdirSync(paths.selenium, { recursive: true });
    fs.writeFileSync(path.join(paths.selenium, 'index.js'), 'module.exports = { fixture: true };');
    fs.writeFileSync(`${paths.firefox}.js`, 'module.exports = { source: true };');
    assert.deepEqual(require(paths.selenium), { fixture: true });
    assert.deepEqual(require(paths.firefox), { source: true });
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});

test('sandbox overrides fail before launch, privileged automation is child-scoped and unrelated environment is omitted', () => {
  for (const name of ['MOZ_DISABLE_CONTENT_SANDBOX', 'MOZ_DISABLE_RDD_SANDBOX', 'MOZ_DISABLE_SOCKET_PROCESS_SANDBOX', 'MOZ_SANDBOX_LOGGING', 'MOZ_FORCE_DISABLE_E10S']) {
    assert.throws(() => childEnvironment({ [name]: '0' }), /Refusing/);
  }
  const env = { HOME: '/home/test', DISPLAY: ':12', PATH: '/bin', PRIVATE_TOKEN: 'secret' };
  assert.deepEqual(childEnvironment(env), { HOME: '/home/test', DISPLAY: ':12', PATH: '/bin', MOZ_REMOTE_ALLOW_SYSTEM_ACCESS: '1' });
  assert.equal(env.MOZ_REMOTE_ALLOW_SYSTEM_ACCESS, undefined);
});

test('proc parser handles spaces/parentheses in comm and preserves exact birth ticks', () => {
  const fields = ['S', '100', ...Array(17).fill('0'), '9007199254740993'];
  const proc = parseProc(`101 (Web Content (test)) ${fields.join(' ')}`, 'Seccomp:\t2\nSeccomp_filters:\t3\nNoNewPrivs:\t1\n');
  assert.equal(proc.pid, 101);
  assert.equal(proc.parentPid, 100);
  assert.equal(proc.startTicks, '9007199254740993');
  assert.equal(proc.seccomp, 2);
  assert.equal(proc.filters, 3);
  assert.equal(proc.noNewPrivs, 1);
  assert.equal(parseProc(`101 (web) ${fields.join(' ')}`, '').filters, null);
  assert.throws(() => parseProc('broken', ''), /Malformed/);
});

test('synthetic active-filter evidence yields only a bounded observation, not a global security pass', () => {
  const result = securityDecision(fixture());
  assert.equal(result.decision, 'observed-content-sandbox');
  assert.match(result.scope, /Firefox Linux smoke-tab/);
  assert.match(result.limits.join(' '), /does not validate filter policy/);
});

test('disabled sandbox, inherited-only filters and missing kernel evidence cannot pass', () => {
  for (const patch of [{ seccomp: 0 }, { seccomp: 1 }, { noNewPrivs: 0 }, { filters: 1 }, { filters: null }, { userNamespace: null }]) {
    const data = fixture();
    Object.assign(data.after[101], patch);
    rejected(data, /seccomp|NoNewPrivs|namespace/);
  }
});

test('reused/exited PIDs and missing content cannot pass', () => {
  const cases = [
    data => { data.after[101].startTicks = '1020'; },
    data => { data.after[100].startTicks = '999'; },
    data => { data.after[101].state = 'Z'; },
    data => { data.after[101].state = '?'; },
    data => { data.before[101].startTicks = 'invalid'; },
    data => { data.second.tabURL = 'about:blank'; },
    data => { delete data.after[101]; },
    data => { data.second.children = []; },
    data => { data.second.children[0].type = 'gpu'; },
    data => { data.second.children[0].childID = 8; },
    data => { data.second.tabPid = 102; },
    data => { delete data.first.tabPid; },
    data => { data.before[101].exe = '/unrelated/program'; },
    data => { data.first.pid = 200; },
    data => { data.before[101].startTicks = '10'; },
  ];
  for (const change of cases) {
    const data = fixture();
    change(data);
    assert.equal(securityDecision(data).decision, 'reject-or-unresolved');
  }
});

test('snapshot errors and unavailable live processes are recorded as unresolved', async () => {
  const driver = { executeAsyncScript: async () => ({ error: 'proc info unavailable' }) };
  const result = await collectSecurity(driver, 2147483647, process.execPath);
  assert.equal(result.decision, 'reject-or-unresolved');
  assert.equal(result.first.error, 'proc info unavailable');
  assert.ok(result.issues.length > 0);
});
