/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

const assert = require('node:assert/strict');
const http = require('node:http');
const path = require('node:path');
const fs = require('node:fs');

function parseArguments(args) {
  let source = path.resolve(__dirname, '../../..');
  let binary;
  for (let i = 0; i < args.length; i++) {
    if (args[i] === '--source') {
      assert.ok(args[i + 1] && !args[i + 1].startsWith('-'), '--source requires a harness checkout');
      source = path.resolve(args[++i]);
    } else {
      assert.ok(!args[i].startsWith('-') && !binary, `Unexpected argument: ${args[i]}`);
      binary = path.resolve(args[i]);
    }
  }
  assert.ok(binary, 'Pass the Firefox executable [--source HARNESS]');
  return { binary, source };
}

function harnessPaths(source) {
  const modules = path.join(source, 'tools/browsertime/node_modules');
  return {
    selenium: path.join(modules, 'selenium-webdriver'),
    firefox: path.join(modules, 'selenium-webdriver/firefox'),
    geckodriver: path.join(modules, '@sitespeed.io/geckodriver/vendor/geckodriver'),
  };
}

function childEnvironment(env) {
  for (const [name, value] of Object.entries(env)) {
    assert.ok(!value || !(/^(MOZ_DISABLE_.*SANDBOX|MOZ_SANDBOX.*|MOZ_FORCE_DISABLE_E10S|MOZ_FORCE_DISABLE_FISSION)$/.test(name)),
      `Refusing sandbox/process override: ${name}`);
  }
  const names = ['PATH', 'HOME', 'LANG', 'LC_ALL', 'DISPLAY', 'XAUTHORITY',
    'WAYLAND_DISPLAY', 'XDG_RUNTIME_DIR', 'DBUS_SESSION_BUS_ADDRESS', 'TMPDIR'];
  return { ...Object.fromEntries(names.filter(name => env[name] !== undefined).map(name => [name, env[name]])),
    MOZ_REMOTE_ALLOW_SYSTEM_ACCESS: '1' };
}

function parseProc(stat, status) {
  const close = stat.lastIndexOf(')');
  assert.ok(close > 0, 'Malformed /proc stat');
  const fields = stat.slice(close + 1).trim().split(/\s+/);
  const values = Object.fromEntries(status.trim().split('\n').map(line => {
    const colon = line.indexOf(':');
    return [line.slice(0, colon), line.slice(colon + 1).trim()];
  }));
  const integer = value => /^\d+$/.test(value || '') ? Number(value) : null;
  const result = { pid: integer(stat.slice(0, stat.indexOf(' '))),
    parentPid: integer(fields[1]), startTicks: fields[19], state: fields[0],
    seccomp: integer(values.Seccomp), filters: integer(values.Seccomp_filters),
    noNewPrivs: integer(values.NoNewPrivs) };
  assert.ok(result.pid > 0 && result.parentPid > 0 && /^\d+$/.test(result.startTicks || ''), 'Invalid /proc identity');
  return result;
}

function readProcess(pid) {
  assert.ok(Number.isSafeInteger(pid) && pid > 0, 'Invalid process PID');
  const root = `/proc/${pid}`;
  const before = fs.readFileSync(`${root}/stat`, 'utf8');
  const result = parseProc(before, fs.readFileSync(`${root}/status`, 'utf8'));
  result.exe = fs.realpathSync(`${root}/exe`);
  result.userNamespace = fs.readlinkSync(`${root}/ns/user`);
  result.pidNamespace = fs.readlinkSync(`${root}/ns/pid`);
  const after = parseProc(fs.readFileSync(`${root}/stat`, 'utf8'), '');
  assert.equal(result.startTicks, after.startTicks, 'PID reused during /proc capture');
  assert.ok(/^[RSDTtIWP]$/.test(after.state), 'Process exited during /proc capture');
  return result;
}

function securityDecision({ browserPid, binary, expectedURL, first, second, before, after }) {
  const issues = [];
  const check = (ok, message) => { if (!ok) issues.push(message); };
  const contentTypes = ['web', 'webIsolated', 'withCoopCoep'];
  check(first?.pid === browserPid && second?.pid === browserPid && first?.type === 'browser' && second?.type === 'browser', 'Browser process identity unavailable or mismatched');
  const targetPid = first?.tabPid;
  check(Number.isSafeInteger(targetPid) && targetPid > 0 && targetPid === second?.tabPid, 'Smoke tab content process missing or changed');
  check(expectedURL && first?.tabURL === expectedURL && second?.tabURL === expectedURL, 'Selected tab is not the expected smoke page');
  const content = first?.children?.find(child => child.pid === targetPid && contentTypes.includes(child.type));
  check(content && second?.children?.some(child => child.pid === targetPid && child.type === content.type && child.childID === content.childID), 'Live content PID/type not confirmed by Firefox');
  const parent = before?.[browserPid];
  check(parent?.exe === binary, 'Browser executable does not match requested binary');
  for (const pid of [browserPid, targetPid]) {
    const a = before?.[pid];
    const b = after?.[pid];
    check(a && b && a.pid === pid && b.pid === pid && a.startTicks === b.startTicks && /^\d+$/.test(a.startTicks || '') && /^[RSDTtIWP]$/.test(a.state) && /^[RSDTtIWP]$/.test(b.state), `PID ${pid}: missing, exited or reused process`);
    check(a?.exe && a.exe === b?.exe && a.exe === parent?.exe, `PID ${pid}: executable identity changed or mismatched`);
    if (pid === targetPid && a && b && parent) {
      check(/^\d+$/.test(a.startTicks || '') && /^\d+$/.test(parent.startTicks || '') && BigInt(a.startTicks) >= BigInt(parent.startTicks), 'Content process birth invalid or predates browser');
      for (const sample of [a, b]) {
        check(sample.seccomp === 2 && sample.noNewPrivs === 1, 'Content seccomp filter mode/NoNewPrivs not active');
        check(Number.isInteger(sample.filters) && Number.isInteger(parent.filters) && sample.filters > parent.filters, 'Content has no demonstrated additional seccomp filter over browser/container');
        check(sample.userNamespace && sample.pidNamespace, 'Content namespace evidence missing');
      }
    }
  }
  return { scope: 'Firefox Linux smoke-tab content process',
    decision: issues.length ? 'reject-or-unresolved' : 'observed-content-sandbox', issues,
    limits: ['Filter presence does not validate filter policy or every process sandbox.',
      'User/PID namespaces are recorded, not assumed mandatory; container inheritance is possible.',
      'No Chromium sandbox, service-backed Safe Browsing, PDF rendering or general security/standards compatibility proof.'] };
}

async function collectSecurity(driver, browserPid, binary, expectedURL) {
  const snapshot = () => driver.executeAsyncScript(function (done) {
    ChromeUtils.requestProcInfo().then(info => {
      const browser = Services.wm.getMostRecentWindow('navigator:browser')?.gBrowser.selectedBrowser;
      done({ pid: info.pid, type: info.type, children: info.children.map(({ pid, type, childID }) => ({ pid, type, childID })),
        tabPid: browser?.browsingContext.currentWindowGlobal?.osPid, tabURL: browser?.currentURI.spec });
    }).catch(error => done({ error: String(error) }));
  });
  const first = await snapshot();
  const before = {};
  const after = {};
  const errors = [];
  const capture = target => {
    for (const pid of new Set([browserPid, first.tabPid])) {
      try { target[pid] = readProcess(pid); }
      catch (error) { errors.push(String(error)); }
    }
  };
  capture(before);
  const second = await snapshot();
  capture(after);
  const evidence = { browserPid, binary: fs.realpathSync(binary), expectedURL, first, second, before, after };
  const decision = securityDecision(evidence);
  if (errors.length) {
    decision.issues.push(...errors);
    decision.decision = 'reject-or-unresolved';
  }
  return { ...evidence, ...decision };
}

async function main() {
  const { binary, source } = parseArguments(process.argv.slice(2));
  const environment = childEnvironment(process.env);
  const paths = harnessPaths(source);
  const { Builder, By } = require(paths.selenium);
  const firefox = require(paths.firefox);
  const server = http.createServer((request, response) => {
    response.setHeader('Content-Type', 'text/html; charset=utf-8');
    response.end('<!doctype html><title>Firefox baseline smoke</title><button id="button">0</button><script>button.onclick=()=>button.textContent=Number(button.textContent)+1</script>');
  });
  let driver;
  try {
    await new Promise((resolve, reject) => {
      server.once('error', reject);
      server.listen(0, '127.0.0.1', resolve);
    });
    const options = new firefox.Options().setBinary(binary).setPreference('remote.prefs.recommended', false);
    const service = new firefox.ServiceBuilder(paths.geckodriver).setEnvironment(environment);
    driver = await new Builder().forBrowser('firefox').setFirefoxOptions(options).setFirefoxService(service).build();
    const smokeURL = `http://127.0.0.1:${server.address().port}/`;
    await driver.get(smokeURL);
    await driver.findElement(By.id('button')).click();
    assert.equal(await driver.findElement(By.id('button')).getText(), '1');
    const features = await driver.executeScript(() => {
      localStorage.setItem('baseline-smoke', 'persisted');
      const canvas = document.createElement('canvas');
      const context = canvas.getContext('2d');
      context.fillStyle = '#ff0000';
      context.fillRect(0, 0, 1, 1);
      const peer = new RTCPeerConnection();
      peer.createDataChannel('smoke');
      peer.close();
      const bytes = new Uint8Array([0,97,115,109,1,0,0,0,1,5,1,96,0,1,127,3,2,1,0,7,10,1,6,97,110,115,119,101,114,0,0,10,6,1,4,0,65,42,11]);
      return {
        wasm: new WebAssembly.Instance(new WebAssembly.Module(bytes)).exports.answer(),
        canvas: Array.from(context.getImageData(0, 0, 1, 1).data),
        webRTC: true,
        userAgent: navigator.userAgent,
      };
    });
    assert.equal(features.wasm, 42);
    assert.deepEqual(features.canvas, [255, 0, 0, 255]);
    await driver.navigate().refresh();
    assert.equal(await driver.executeScript("return localStorage.getItem('baseline-smoke')"), 'persisted');
    await driver.setContext(firefox.Context.CHROME);
    const preferences = await driver.executeScript(() => {
      const preferences = ['security.sandbox.content.level', 'xpinstall.signatures.required', 'media.peerconnection.enabled', 'pdfjs.disabled', 'browser.safebrowsing.malware.enabled', 'browser.safebrowsing.phishing.enabled'];
      const read = branch => Object.fromEntries(preferences.map(name => [name, branch.getPrefType(name) === Services.prefs.PREF_BOOL ? branch.getBoolPref(name) : branch.getIntPref(name)]));
      return { defaults: read(Services.prefs.getDefaultBranch('')), effective: read(Services.prefs) };
    });
    const security = await collectSecurity(driver, (await driver.getCapabilities()).get('moz:processID'), binary, smokeURL);
    console.log(JSON.stringify({ binary, source, automation: {
      remoteSystemAccess: 'isolated WebDriver child only', recommendedPreferences: false,
      profile: 'fresh geckodriver-owned',
    }, capabilities: (await driver.getCapabilities()).get('browserVersion'), features, preferences, security }, null, 2));
    assert.equal(security.decision, 'observed-content-sandbox', security.issues.join('; '));
    for (const values of [preferences.defaults, preferences.effective]) {
      assert.ok(values['security.sandbox.content.level'] >= 6);
      for (const preference of ['xpinstall.signatures.required', 'media.peerconnection.enabled', 'browser.safebrowsing.malware.enabled', 'browser.safebrowsing.phishing.enabled']) {
        assert.equal(values[preference], true, preference);
      }
      assert.equal(values['pdfjs.disabled'], false);
    }
  } finally {
    try {
      if (driver) await driver.quit();
    } finally {
      await new Promise(resolve => server.close(resolve));
    }
  }
}

module.exports = { parseArguments, harnessPaths, childEnvironment, parseProc, securityDecision, collectSecurity };
if (require.main === module) {
  main().catch(error => { console.error(error); process.exitCode = 1; });
}
