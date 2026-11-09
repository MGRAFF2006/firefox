/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

const assert = require('node:assert/strict');
const http = require('node:http');
const path = require('node:path');
const { Builder, By } = require('../../browsertime/node_modules/selenium-webdriver');
const firefox = require('../../browsertime/node_modules/selenium-webdriver/firefox');

async function main() {
  const binary = process.argv[2];
  assert.ok(binary, 'Pass the Firefox executable');
  const server = http.createServer((request, response) => {
    response.setHeader('Content-Type', 'text/html; charset=utf-8');
    response.end('<!doctype html><title>Firefox baseline smoke</title><button id="button">0</button><script>button.onclick=()=>button.textContent=Number(button.textContent)+1</script>');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  process.env.MOZ_REMOTE_ALLOW_SYSTEM_ACCESS = '1';
  const options = new firefox.Options().setBinary(binary).setPreference('remote.prefs.recommended', false);
  const service = new firefox.ServiceBuilder(path.resolve(__dirname, '../../browsertime/node_modules/@sitespeed.io/geckodriver/vendor/geckodriver'));
  let driver;
  try {
    driver = await new Builder().forBrowser('firefox').setFirefoxOptions(options).setFirefoxService(service).build();
    await driver.get(`http://127.0.0.1:${server.address().port}/`);
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
    for (const values of [preferences.defaults, preferences.effective]) {
      assert.ok(values['security.sandbox.content.level'] >= 6);
      for (const preference of ['xpinstall.signatures.required', 'media.peerconnection.enabled', 'browser.safebrowsing.malware.enabled', 'browser.safebrowsing.phishing.enabled']) {
        assert.equal(values[preference], true, preference);
      }
      assert.equal(values['pdfjs.disabled'], false);
    }
    console.log(JSON.stringify({ binary, capabilities: (await driver.getCapabilities()).get('browserVersion'), features, preferences }, null, 2));
  } finally {
    try {
      if (driver) await driver.quit();
    } finally {
      await new Promise(resolve => server.close(resolve));
    }
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
