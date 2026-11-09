/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

const assert = require('node:assert/strict');
const http = require('node:http');
const path = require('node:path');

function validateSecurityConfiguration(evidence) {
  for (const name of ['MOZ_UPDATER', 'MOZ_CRASHREPORTER', 'MOZ_REQUIRE_SIGNING']) {
    assert.equal(evidence.constants[name], true, name);
  }
  for (const branch of ['defaults', 'effective']) {
    const prefs = evidence[branch];
    assert.ok(Number.isInteger(prefs['security.sandbox.content.level']) && prefs['security.sandbox.content.level'] >= 6, `${branch}: sandbox level`);
    for (const name of ['xpinstall.signatures.required', 'browser.safebrowsing.phishing.enabled', 'browser.safebrowsing.malware.enabled', 'media.peerconnection.enabled']) {
      assert.equal(prefs[name], true, `${branch}: ${name}`);
    }
    assert.equal(prefs['pdfjs.disabled'], false, `${branch}: PDF enabled`);
  }
}

async function finishResults(results, driver, server, emit) {
  for (const [name, operation] of [
    ['browser-teardown', async () => { if (driver) await driver.quit(); }],
    ['server-teardown', () => new Promise((resolve, reject) => server.close(error => error ? reject(error) : resolve()))],
  ]) {
    try { await operation(); }
    catch (error) { results.checks.push({name, status: 'fail', error: error.message}); }
  }
  emit(results);
  return results.checks.some(check => check.status !== 'pass');
}

async function main() {
  const [source, binary, unsignedXpi] = process.argv.slice(2);
  assert.ok(source && binary && unsignedXpi, 'usage: node runtime_checks.cjs SOURCE BINARY UNSIGNED_XPI');
  const modules = path.join(source, 'tools/browsertime/node_modules');
  const {Builder} = require(path.join(modules, 'selenium-webdriver'));
  const firefox = require(path.join(modules, 'selenium-webdriver/firefox'));
  const results = {schema: 1, binary, time: new Date().toISOString(), checks: [], limits: [
    'Headless Linux software rendering; no hardware, native AT or physical media device claim',
    'No authenticated service operation, update application, crash submission or DRM playback',
    'Fresh automation profile; remote.prefs.recommended=false; privileged context only for inspection',
  ]};
  const server = http.createServer((req, res) => {
    res.setHeader('Content-Type', 'text/html');
    res.end('<!doctype html><title>Isolated parity checks</title>');
  });
  const testHost = process.env.PARITY_TEST_HOST || '127.0.0.1';
  await new Promise(resolve => server.listen(0, testHost, resolve));
  let driver;
  async function check(name, run) {
    try { results.checks.push({name, status: 'pass', evidence: await run()}); }
    catch (error) { results.checks.push({name, status: 'fail', error: error.message}); }
  }
  try {
    process.env.MOZ_REMOTE_ALLOW_SYSTEM_ACCESS = '1';
    const options = new firefox.Options().setBinary(binary).addArguments('-headless')
      .setPreference('remote.prefs.recommended', false);
    const service = new firefox.ServiceBuilder(path.join(modules, '@sitespeed.io/geckodriver/vendor/geckodriver'));
    driver = await new Builder().forBrowser('firefox').setFirefoxOptions(options).setFirefoxService(service).build();
    results.version = (await driver.getCapabilities()).get('browserVersion');
    await driver.manage().setTimeouts({script: 20000, pageLoad: 30000});
    await driver.get(`http://${testHost}:${server.address().port}/`);
    await check('webrtc-local-data-transport', async () => {
      const evidence = await driver.executeAsyncScript(function () {
        const done = arguments[arguments.length - 1];
        const a = new RTCPeerConnection({iceServers: []});
        const b = new RTCPeerConnection({iceServers: []});
        const candidates = {a: 0, b: 0};
        const timer = setTimeout(() => finish({error: 'local WebRTC timeout', candidates,
          a: {gathering: a.iceGatheringState, connection: a.iceConnectionState},
          b: {gathering: b.iceGatheringState, connection: b.iceConnectionState}}), 15000);
        let finished = false;
        function finish(value) {
          if (finished) return;
          finished = true; clearTimeout(timer);
          const diagnostics = {candidates, a: {gathering: a.iceGatheringState, connection: a.iceConnectionState}, b: {gathering: b.iceGatheringState, connection: b.iceConnectionState}};
          a.close(); b.close(); done({...value, diagnostics});
        }
        a.onicecandidate = e => { if (e.candidate) { candidates.a++; b.addIceCandidate(e.candidate).catch(e => finish({error: e.message})); } };
        b.onicecandidate = e => { if (e.candidate) { candidates.b++; a.addIceCandidate(e.candidate).catch(e => finish({error: e.message})); } };
        b.ondatachannel = e => { e.channel.onmessage = event => finish({received: event.data}); };
        const channel = a.createDataChannel('parity');
        channel.onopen = () => channel.send('fork-parity-transport');
        (async () => {
          const offer = await a.createOffer(); await a.setLocalDescription(offer); await b.setRemoteDescription(offer);
          const answer = await b.createAnswer(); await b.setLocalDescription(answer); await a.setRemoteDescription(answer);
        })().catch(e => finish({error: e.message}));
      });
      assert.equal(evidence.received, 'fork-parity-transport', JSON.stringify(evidence));
      return evidence;
    });
    await check('indexeddb-transaction-after-reload', async () => {
      const written = await driver.executeAsyncScript(function () {
        const done = arguments[arguments.length - 1];
        const request = indexedDB.open('parity', 1);
        request.onupgradeneeded = () => request.result.createObjectStore('values');
        request.onerror = () => done({error: request.error.name});
        request.onsuccess = () => {
          const db = request.result; const tx = db.transaction('values', 'readwrite');
          tx.objectStore('values').put('persistent-value', 'key');
          tx.oncomplete = () => { db.close(); done({written: true}); };
          tx.onabort = () => { db.close(); done({error: 'transaction aborted'}); };
        };
      });
      assert.equal(written.written, true);
      await driver.navigate().refresh();
      const value = await driver.executeAsyncScript(function () {
        const done = arguments[arguments.length - 1];
        const request = indexedDB.open('parity', 1);
        request.onerror = () => done(null);
        request.onsuccess = () => {
          const db = request.result; const tx = db.transaction('values');
          const read = tx.objectStore('values').get('key');
          tx.oncomplete = () => { const value = read.result; db.close(); done(value); };
          tx.onabort = () => { db.close(); done(null); };
        };
      });
      assert.equal(value, 'persistent-value'); return {value};
    });
    await check('unsigned-permanent-addon-rejected', async () => {
      try {
        const id = await driver.installAddon(unsignedXpi, false);
        await driver.uninstallAddon(id);
        throw new Error('Unsigned permanent extension was accepted');
      } catch (error) {
        if (!/ERROR_SIGNEDSTATE_REQUIRED/.test(error.message)) throw error;
        return {rejection: 'ERROR_SIGNEDSTATE_REQUIRED'};
      }
    });
    let configuration;
    await check('runtime-configuration-inventory-collected', async () => {
      await driver.setContext(firefox.Context.CHROME);
      try {
        configuration = await driver.executeScript(function () {
          const {AppConstants} = ChromeUtils.importESModule('resource://gre/modules/AppConstants.sys.mjs');
          const names = ['MOZ_UPDATER', 'MOZ_CRASHREPORTER', 'MOZ_UPDATE_CHANNEL', 'MOZ_OFFICIAL_BRANDING', 'MOZ_TELEMETRY_REPORTING', 'MOZ_REQUIRE_SIGNING'];
          const constants = Object.fromEntries(names.map(name => [name, AppConstants[name] ?? null]));
          const prefs = ['security.sandbox.content.level', 'xpinstall.signatures.required', 'browser.safebrowsing.phishing.enabled', 'browser.safebrowsing.malware.enabled', 'media.peerconnection.enabled', 'media.eme.enabled', 'media.gmp-widevinecdm.enabled', 'pdfjs.disabled'];
          const read = branch => Object.fromEntries(prefs.map(name => {
            const type = branch.getPrefType(name);
            return [name, type === Services.prefs.PREF_BOOL ? branch.getBoolPref(name) : type === Services.prefs.PREF_INT ? branch.getIntPref(name) : null];
          }));
          const keyState = name => {
            const value = AppConstants[name];
            if (!value) return 'missing';
            if (value.startsWith('no-') && value.endsWith('-key')) return 'sentinel';
            return 'configured-unverified';
          };
          const provisioning = Object.fromEntries(['MOZ_GOOGLE_LOCATION_SERVICE_API_KEY', 'MOZ_GOOGLE_SAFEBROWSING_API_KEY', 'MOZ_MOZILLA_API_KEY'].map(name => [name, keyState(name)]));
          return {constants, provisioning, defaults: read(Services.prefs.getDefaultBranch('')), effective: read(Services.prefs)};
        });
        return configuration;
      } finally { await driver.setContext(firefox.Context.CONTENT); }
    });
    await check('required-security-feature-configuration', async () => {
      assert.ok(configuration, 'configuration inventory unavailable');
      validateSecurityConfiguration(configuration);
      return {validated: ['sandbox', 'extension-signatures', 'SafeBrowsing-preferences', 'PDF-preference', 'WebRTC-preference', 'updater/crashreporter/signature-constants'],
        limit: 'Preference validation does not prove service operation; EME opt-in and unofficial distribution differences remain inventory only'};
    });
  } catch (error) {
    results.checks.push({name: 'execution', status: 'fail', error: error.message});
  } finally {
    if (await finishResults(results, driver, server, value => console.log(JSON.stringify(value, null, 2)))) process.exitCode = 1;
  }
}
module.exports = {validateSecurityConfiguration, finishResults};
if (require.main === module) main().catch(error => { console.error(error); process.exitCode = 1; });
