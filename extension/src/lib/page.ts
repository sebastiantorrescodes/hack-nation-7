// Read the app the user is working in (the active tab) from the side panel.

export async function getAppTab(): Promise<chrome.tabs.Tab> {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  // Chrome doesn't let extensions read its own pages (chrome://, the Web Store, other extensions).
  if (!tab?.id || !tab.url || !/^https?:\/\//.test(tab.url)) {
    throw new Error("Open the app you're working in (a normal web page) in the active tab first.");
  }
  return tab;
}

/** Attach packaged listeners to already-open pages; loading an extension doesn't reload them. */
export async function ensureCaptureLogger(tabId: number): Promise<{frames: number; warnings: string[]}> {
  let frameIds = [0];
  try {
    const frames = await chrome.scripting.executeScript({target: {tabId, allFrames: true}, func: () => true});
    frameIds = Array.from(new Set([0, ...frames.map(frame => frame.frameId)]));
  } catch { /* The main page may still be accessible when an embedded frame is restricted. */ }
  async function connect(frameId: number) {
    const ready = async () => {
      try { return (await chrome.tabs.sendMessage(tabId, {type: "capture-ready"}, {frameId}))?.version === "durable-events-v2"; }
      catch { return false; }
    };
    if (await ready()) return;
    try {
      await chrome.scripting.executeScript({target: {tabId, frameIds: [frameId]}, files: ["content.js"]});
    } catch {
      throw new Error("Chrome blocked access to this website. Click the AI Apprentice toolbar icon and allow access to this site, then retry. Protected Chrome pages cannot be used for an interview.");
    }
    if (!(await ready())) throw new Error("The website connection could not be verified after automatic setup. Check that AI Apprentice is loaded from hack-nation-7/extension/dist; reload that extension and reopen its panel.");
  }
  const results = await Promise.allSettled(frameIds.map(connect));
  if (results[0].status === "rejected") throw results[0].reason;
  const connected = results.filter(result => result.status === "fulfilled").length;
  return {frames: connected, warnings: connected < frameIds.length
    ? ["The interview page is connected, but some embedded frames could not record actions."] : []};
}

/** Drain debounced edits in every accessible frame before stopping the recording. */
export async function flushCaptureEdits(tabId: number): Promise<void> {
  const frames = await chrome.scripting.executeScript({target: {tabId, allFrames: true}, func: () => true});
  await Promise.all(frames.map(frame => chrome.tabs.sendMessage(tabId, {type: "capture-flush"}, {frameId: frame.frameId})));
}

// Injected into every frame; must be self-contained (no closures over module scope).
export function snapshotFrame() {
  const clean = (s: string | null | undefined) => (s ?? "").replace(/\s+/g, " ").trim();
  const fields: string[] = [];
  let truncated = false;
  const roots: (Document | ShadowRoot)[] = [document];
  // Custom controls often live in open shadow roots. Closed roots/canvas remain unreadable.
  for (let i = 0; i < roots.length && i < 50; i++) {
    const nodes = roots[i].querySelectorAll("*");
    for (let j = 0; j < nodes.length && j < 12000; j++) {
      if (nodes[j].shadowRoot) roots.push(nodes[j].shadowRoot!);
    }
    if (nodes.length > 12000) truncated = true;
  }
  if (roots.length > 50) truncated = true;
  for (const root of roots.slice(0, 50)) root.querySelectorAll('input, select, textarea, [contenteditable]:not([contenteditable="false"]), [role="textbox"], [role="combobox"], [role="checkbox"], [role="radio"], [role="switch"], [role="slider"]').forEach((node) => {
    const el = node as HTMLInputElement;
    const style = getComputedStyle(el);
    if (el.type === "hidden" || el.type === "password" || el.getAttribute("aria-hidden") === "true"
        || !el.getClientRects().length || style.visibility === "hidden" || style.display === "none") return;
    const labelledBy = clean((el.getAttribute("aria-labelledby") || "").split(/\s+/).filter(Boolean)
      .map(id => root.querySelector(`#${CSS.escape(id)}`)?.textContent || "").join(" "));
    const label = clean(
      el.labels?.[0]?.innerText ||
        el.getAttribute("aria-label") || labelledBy ||
        el.getAttribute("placeholder") ||
        el.closest("td")?.previousElementSibling?.textContent ||
        el.name || el.id || el.getAttribute("role") || "Editable field",
    ).slice(0, 80);
    let value: string;
    if (node instanceof HTMLSelectElement) value = node.selectedOptions[0]?.text ?? node.value;
    else if (el.type === "checkbox" || el.type === "radio") value = el.checked ? "checked" : "unchecked";
    else value = el.value ?? el.getAttribute("aria-valuetext") ?? el.getAttribute("aria-valuenow")
      ?? el.getAttribute("aria-checked") ?? el.innerText ?? "";
    value = clean(value);
    if (value.length > 1000) truncated = true;
    if (label || value) fields.push(`- ${label}: ${value.slice(0, 1000)}`);
  });
  const shadowText = roots.slice(1, 50).map(root => clean(Array.from((root as ShadowRoot).children)
    .filter(el => el.getClientRects().length).map(el => (el as HTMLElement).innerText || "").join(" ")));
  const text = [clean(document.body?.innerText), ...shadowText].filter(Boolean).join(" ");
  return {
    url: location.pathname + location.search,
    title: document.title,
    fields: fields.slice(0, 300),
    text: text.slice(0, 12000),
    truncated: truncated || fields.length > 300 || text.length > 12000,
    unreadableEmbeds: document.querySelectorAll("iframe, frame").length,
  };
}

export type PageSnapshot = {page: string; title: string; capturedAt: number; frames: number; fields: number; warnings: string[]};

/** Facts and capture quality are separate: an empty page must not look like a successful read. */
export async function readPageSnapshot(tabId?: number): Promise<PageSnapshot> {
  const tab = tabId === undefined ? await getAppTab() : await chrome.tabs.get(tabId);
  let results: chrome.scripting.InjectionResult<ReturnType<typeof snapshotFrame>>[];
  const warnings: string[] = [];
  try {
    results = await chrome.scripting.executeScript({ target: { tabId: tab.id!, allFrames: true }, func: snapshotFrame });
  } catch {
    results = await chrome.scripting.executeScript({ target: { tabId: tab.id!, frameIds: [0] }, func: snapshotFrame });
    warnings.push("Only the main page could be read. Some embedded content is restricted.");
  }
  const frames = results.filter(r => r.result).sort((a, b) => (a.frameId === 0 ? -1 : b.frameId === 0 ? 1 : a.frameId - b.frameId));
  const readable = frames.filter(r => r.result!.fields.length || r.result!.text.length);
  const raw = readable.map(({result: f}) => `## Frame ${f!.url} — ${f!.title}\nFields:\n${f!.fields.join("\n") || "(none)"}\nVisible text:\n${f!.text}`).join("\n\n");
  if (!readable.length) warnings.push("No readable text or fields. Try an optional screenshot or a different page.");
  if (frames.some(r => r.result!.truncated) || raw.length > 100000) warnings.push("Some page content was shortened. Keep the relevant record visible.");
  if (results.some(r => !r.result) || frames.reduce((n, r) => n + r.result!.unreadableEmbeds, 0) > frames.length - 1)
    warnings.push("Some embedded content could not be read. Check site access for the embedded app.");
  return {page: raw.slice(0, 100000), title: tab.title || frames[0]?.result?.title || "Interview page",
    capturedAt: Date.now(), frames: readable.length, fields: readable.reduce((n, r) => n + r.result!.fields.length, 0), warnings};
}

/** Compatibility for tutor/practice callers that need only the page facts. */
export async function snapshotPage(tabId?: number): Promise<string> {
  return (await readPageSnapshot(tabId)).page;
}

/** Optional images never prevent text context from being captured. */
export async function readCaptureSnapshot(tabId: number, includeImage: boolean) {
  const snapshot = await readPageSnapshot(tabId);
  let image_base64: string | undefined;
  if (includeImage) {
    try { image_base64 = await screenshot(tabId); }
    catch { snapshot.warnings.push("Screenshot unavailable. Text capture continues; return to the interview tab to include an image."); }
  }
  return {...snapshot, image_base64};
}

/** Explicit screenshot preview works independently of action listeners and the microphone. */
export async function readScreenPreview(expectedTabId?: number) {
  const tab = await getAppTab();
  if (expectedTabId !== undefined && tab.id !== expectedTabId)
    throw new Error("Switch back to the interview tab before checking its screen.");
  const image_base64 = await screenshot(tab.id);
  let page = "";
  try { page = await snapshotPage(tab.id); } catch { /* Vision can still read the supplied picture. */ }
  return {image_base64, page};
}

/** JPEG of the visible tab, base64 without the data: prefix. */
export async function screenshot(tabId?: number): Promise<string> {
  const tab = await getAppTab();
  if (tabId !== undefined && tab.id !== tabId) throw new Error("Switch back to the interview tab before taking a screenshot.");
  const dataUrl = await chrome.tabs.captureVisibleTab(tab.windowId, { format: "jpeg", quality: 60 });
  if ((await getAppTab()).id !== tab.id) throw new Error("The active tab changed while taking the screenshot. Text capture continues.");
  return dataUrl.split(",")[1];
}
