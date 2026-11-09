/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

const assert = require('node:assert/strict');
const {validateSecurityConfiguration, finishResults} = require('./runtime_checks.cjs');

async function main() {
  const prefs = {'security.sandbox.content.level': 6, 'xpinstall.signatures.required': true,
    'browser.safebrowsing.phishing.enabled': true, 'browser.safebrowsing.malware.enabled': true,
    'media.peerconnection.enabled': true, 'pdfjs.disabled': false};
  const valid = {constants: {MOZ_UPDATER: true, MOZ_CRASHREPORTER: true, MOZ_REQUIRE_SIGNING: true}, defaults: {...prefs}, effective: {...prefs}};
  validateSecurityConfiguration(valid);
  for (const branch of ['defaults', 'effective']) {
    for (const [name, value] of Object.entries(prefs)) {
      const broken = structuredClone(valid);
      broken[branch][name] = typeof value === 'boolean' ? !value : 0;
      assert.throws(() => validateSecurityConfiguration(broken), undefined, `${branch}: ${name}`);
      delete broken[branch][name];
      assert.throws(() => validateSecurityConfiguration(broken), undefined, `missing ${branch}: ${name}`);
    }
  }
  const results = {checks: [{name: 'completed-before-crash', status: 'pass', evidence: 'preserved'}]};
  let emitted;
  let serverClosed = false;
  const failed = await finishResults(results, {quit: async () => {throw new Error('fake transport failure');}},
    {close: callback => {serverClosed = true; callback(new Error('fake close failure'));}},
    value => {emitted = JSON.parse(JSON.stringify(value));});
  assert.equal(failed, true);
  assert.equal(serverClosed, true);
  assert.deepEqual(emitted.checks.map(check => [check.name, check.status]),
    [['completed-before-crash', 'pass'], ['browser-teardown', 'fail'], ['server-teardown', 'fail']]);
  console.log('Reporting regressions passed: disabled/missing required preferences rejected; teardown failures retain structured evidence and failure status.');
}
main().catch(error => {console.error(error); process.exitCode = 1;});
