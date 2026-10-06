// Exercise multiple deployments of exactly the same compiled Docker image.
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { execFileSync, spawnSync } from 'node:child_process';
import { normalizeUiBasePath } from '../src/deployment.ts';

const image = process.env.BZCARD_TEST_UI_IMAGE || 'bzcard-ui:distribution-test';
const docker = (...args) => execFileSync('docker', args, {encoding:'utf8', stdio:['ignore','pipe','pipe']}).trim();
const logs = container => {
  const result = spawnSync('docker', ['logs','--tail=30',container], {encoding:'utf8'});
  return result.stdout + result.stderr;
};
const imageId = docker('image', 'inspect', '--format', '{{.Id}}', image);
const source = docker('run', '--rm', '--entrypoint', 'cat', image, '/opt/bzcard/dist/index.html');
assert.match(source, /<base href="\.\/"\s*\/?>/);
assert.ok(source.includes('./assets/'));
const deployments = [
  ['/bzcard/', '/bzcard-api'],
  ['/', '/'],
  ['/tools/liff/', '/tools/cards-api'],
  ['/liff/', '/deep/api'],
  ['/v1.0+cards/', '/cards-api'],
  ['/名刺 管理/', '/cards-api'],
];
const assetDigests = new Map();
async function ready(url, container) {
  for (let attempt = 0; attempt < 100; attempt++) {
    let response;
    try {
      response = await fetch(url, {signal:AbortSignal.timeout(1000)});
    } catch {}
    if (response?.ok) return;
    if (response) throw new Error('UI returned HTTP ' + response.status + ': ' + logs(container));
    if (docker('inspect', '--format', '{{.State.Running}}', container) !== 'true') {
      throw new Error(logs(container));
    }
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  throw new Error('UI did not start: ' + logs(container));
}
for (const [ui, api] of deployments) {
  const name = 'bzcard-ui-regression-' + process.pid;
  const container = docker('run', '-d', '--name', name, '-p', '127.0.0.1::5173',
    '-e', 'UI_BASE_PATH=' + ui, '-e', 'API_BASE_PATH=' + api, image);
  try {
    const port = docker('port', container, '5173/tcp').split(':').at(-1);
    const origin = 'http://127.0.0.1:' + port;
    const path = normalizeUiBasePath(ui);
    const home = origin + path;
    await ready(home, container);
    assert.equal(docker('inspect', '--format', '{{.Image}}', container), imageId);
    docker('exec', container, 'nginx', '-t');
    const response = await fetch(home);
    const document = await response.text();
    assert.match(response.headers.get('cache-control'), /no-store/);
    assert.ok(document.includes('<base href="' + path + '">'));
    assert.ok(document.indexOf('runtime-config.js') < document.indexOf('type="module"'));
    const configuration = await fetch(new URL('runtime-config.js', home));
    assert.match(configuration.headers.get('cache-control'), /no-store/);
    const config = JSON.parse((await configuration.text()).replace(/^window\.__BZCARD_CONFIG__ = /, '').trim().replace(/;$/, ''));
    assert.deepEqual(config, {uiBasePath:path,apiBasePath:api==='/'?'':api});
    const assets = [...document.matchAll(/(?:src|href)="(\.\/assets\/[^"]+)"/g)].map(match => match[1]);
    assert.ok(assets.length >= 2, 'entry document should load JS and CSS');
    for (const asset of assets) {
      const result = await fetch(new URL(asset, home));
      assert.equal(result.status, 200);
      assert.match(result.headers.get('cache-control'), /immutable/);
      const digest = createHash('sha256').update(Buffer.from(await result.arrayBuffer())).digest('hex');
      if (assetDigests.has(asset)) assert.equal(digest, assetDigests.get(asset));
      assetDigests.set(asset, digest);
      // Existing reverse proxies may strip the UI prefix.
      assert.equal((await fetch(new URL(asset, origin + '/'))).status, 200);
    }
    const icon = await fetch(new URL('favicon.png', home));
    assert.equal(icon.status, 200);
    assert.match(icon.headers.get('content-type'), /image\/png/);
    assert.equal((await fetch(new URL('assets/missing.js', home))).status, 404);
    const liff = await fetch(new URL('liff?connection=test', home));
    assert.equal(liff.status, 200);
    assert.match(liff.headers.get('cache-control'), /no-store/);
    assert.equal(await liff.text(), document);
    if (path !== '/') {
      const redirect = await fetch(origin + path.slice(0, -1) + '?mode=liff', {redirect:'manual'});
      assert.equal(redirect.status, 308);
      assert.equal(redirect.headers.get('location'), path + '?mode=liff');
    }
    const browser = spawnSync(process.execPath, ['--experimental-strip-types', 'tests/browserRegression.mjs'], {
      stdio:'inherit',
      env:{...process.env,BZCARD_TEST_UI_URL:home,BZCARD_TEST_API_BASE_PATH:api},
    });
    if (browser.error) throw browser.error;
    assert.equal(browser.status, 0, 'browser regression failed for ' + ui);
    if (ui === '/tools/liff/') {
      docker('restart', container);
      // Docker may assign a different host port when restarting a dynamic binding.
      const restartedPort = docker('port', container, '5173/tcp').split(':').at(-1);
      const restartedHome = 'http://127.0.0.1:' + restartedPort + path;
      await ready(restartedHome, container);
      assert.equal(await (await fetch(restartedHome)).text(), document);
    }
    console.log('PASS: same image serves ' + ui + ' with API ' + api);
  } finally {
    docker('rm', '-f', container);
  }
}
const invalid = spawnSync('docker', ['run','--rm','-e','UI_BASE_PATH=//invalid',image], {encoding:'utf8'});
assert.notEqual(invalid.status, 0);
assert.match(invalid.stdout + invalid.stderr, /bzCard UI configuration error/);
console.log('PASS: invalid deployment configuration stops the container');
