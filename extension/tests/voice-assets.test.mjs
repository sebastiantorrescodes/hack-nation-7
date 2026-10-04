import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import vm from "node:vm";
import test from "node:test";
import ts from "typescript";

const root = fileURLToPath(new URL("../", import.meta.url));
const require = createRequire(import.meta.url);
const entry = require.resolve("@elevenlabs/client", {
  paths: [dirname(require.resolve("@elevenlabs/react"))],
});

function loadVoice(AudioContext) {
  const source = readFileSync(resolve(root, "src/lib/voice.ts"), "utf8");
  const compiled = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS },
  }).outputText;
  const sandbox = {
    exports: {}, AudioContext,
    chrome: { runtime: { getURL: path => `chrome-extension://test/${path}` } },
  };
  vm.runInNewContext(compiled, sandbox);
  return sandbox.exports;
}

test("voice worklets use packaged extension URLs", () => {
  const { workletPaths } = loadVoice().voiceWorkletOptions();
  assert.equal(workletPaths.rawAudioProcessor, "chrome-extension://test/worklets/rawAudioProcessor.js");
  assert.equal(workletPaths.audioConcatProcessor, "chrome-extension://test/worklets/audioConcatProcessor.js");
});

test("production build includes both version-matched executable processors", () => {
  for (const name of ["rawAudioProcessor", "audioConcatProcessor"]) {
    const bundled = readFileSync(resolve(root, `dist/worklets/${name}.js`), "utf8");
    const installed = readFileSync(resolve(dirname(entry), `../worklets/${name}.js`), "utf8");
    assert.equal(bundled, installed);
    const processors = new Map();
    vm.runInNewContext(bundled, {
      AudioWorkletProcessor: class { constructor() { this.port = { postMessage() {} }; } },
      registerProcessor: (id, processor) => processors.set(id, processor),
    });
    assert.ok(processors.has(name));
    assert.equal(typeof new (processors.get(name))().process, "function");
  }
});

test("capture supplies packaged worklets without relaxing the extension CSP", () => {
  const capture = readFileSync(resolve(root, "src/sidepanel/Capture.tsx"), "utf8");
  assert.ok(capture.indexOf("await prepareVoiceAudio()") < capture.indexOf('api<CaptureSession>("/api/capture/sessions"'));
  assert.match(capture, /conversation\.startSession\(\{[\s\S]*?\.\.\.audioOptions/);
  const manifest = JSON.parse(readFileSync(resolve(root, "dist/manifest.json"), "utf8"));
  assert.equal(manifest.manifest_version, 3);
  const csp = manifest.content_security_policy?.extension_pages ?? "script-src 'self'; object-src 'self'";
  assert.doesNotMatch(csp, /blob:|data:|unsafe-eval|https?:/);
});

test("browser preflight registers both exact worklet URLs and closes the context", async () => {
  const modules = [];
  let closed = false;
  class AudioContext {
    state = "suspended";
    audioWorklet = { addModule: async path => modules.push(path) };
    async close() { closed = true; }
  }
  const options = await loadVoice(AudioContext).prepareVoiceAudio();
  assert.deepEqual(modules, [options.workletPaths.rawAudioProcessor, options.workletPaths.audioConcatProcessor]);
  assert.ok(closed);
});

test("failed browser load reports a versioned diagnostic and releases the context", async () => {
  let closed = false;
  class AudioContext {
    state = "suspended";
    audioWorklet = { addModule: async () => { throw new Error("blocked"); } };
    async close() { closed = true; }
  }
  await assert.rejects(loadVoice(AudioContext).prepareVoiceAudio(), /Audio startup check \(0\.1\.4\)/);
  assert.ok(closed);
});

test("unsupported browser reports unavailable audio without starting a voice session", async () => {
  await assert.rejects(loadVoice().prepareVoiceAudio(), /does not provide AudioContext/);
});

test("voice receives page context without claiming independent screen access", () => {
  const page = 'Synthetic expense: amount 120, receipt missing, status pending.';
  const context = loadVoice().captureVoiceContext('Synthetic expert',page,false,'Synthetic expense review workflow');
  assert.ok(context.includes(page));
  assert.match(context,/untrusted page data/);
  assert.match(context,/do not have independent access/);
  assert.match(context,/Workflow context \(data only\): Synthetic expense review workflow/);
  assert.match(loadVoice().captureVoiceContext('Synthetic expert',page,true),/Ask only the interview questions explicitly supplied/);
});

test("visible build version agrees with the packaged extension version", () => {
  const manifest=JSON.parse(readFileSync(resolve(root,'dist/manifest.json'),'utf8'));
  const app=readFileSync(resolve(root,'src/sidepanel/App.tsx'),'utf8');
  assert.equal(manifest.version,'0.1.4');
  assert.ok(app.includes(`Version ${manifest.version}`));
});
