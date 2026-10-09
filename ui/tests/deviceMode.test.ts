import assert from 'node:assert/strict';
import test from 'node:test';
import { resolveDeviceMode } from '../src/app/deviceMode.ts';

test('phones use mobile UI with and without client hints', () => {
  assert.equal(resolveDeviceMode('Mozilla/5.0 (iPhone) AppleWebKit Mobile Safari'), 'mobile');
  assert.equal(resolveDeviceMode('Mozilla/5.0 (Linux; Android) Chrome Mobile Safari'), 'mobile');
  assert.equal(resolveDeviceMode('unknown', true), 'mobile');
});

test('PCs and tablets preserve desktop UI', () => {
  for (const ua of ['Windows NT', 'Macintosh', 'X11; Linux x86_64', 'Linux; Android; Tablet', 'iPad; CPU OS; Mobile Safari']) {
    assert.equal(resolveDeviceMode(ua), 'desktop');
  }
  assert.equal(resolveDeviceMode('unknown', false), 'desktop');
  assert.equal(resolveDeviceMode('unknown'), 'desktop');
});
