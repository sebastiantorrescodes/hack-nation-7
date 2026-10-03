// Read the OpenEMR tab from the side panel.

export async function getOpenEmrTab(): Promise<chrome.tabs.Tab> {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id || !tab.url || !/^http:\/\/(localhost|127\.0\.0\.1):8300\//.test(tab.url)) {
    throw new Error("Open OpenEMR (http://localhost:8300) in the active tab first.");
  }
  return tab;
}

// Injected into every frame; must be self-contained (no closures over module scope).
function snapshotFrame() {
  const clean = (s: string | null | undefined) => (s ?? "").replace(/\s+/g, " ").trim();
  const fields: string[] = [];
  document.querySelectorAll("input, select, textarea").forEach((node) => {
    const el = node as HTMLInputElement;
    if (el.type === "hidden" || el.type === "password" || el.offsetParent === null) return;
    const label = clean(
      el.labels?.[0]?.innerText ||
        el.getAttribute("aria-label") ||
        el.getAttribute("placeholder") ||
        el.closest("td")?.previousElementSibling?.textContent ||
        el.name ||
        el.id,
    ).slice(0, 80);
    let value: string;
    if (node instanceof HTMLSelectElement) value = node.selectedOptions[0]?.text ?? node.value;
    else if (el.type === "checkbox" || el.type === "radio") value = el.checked ? "checked" : "unchecked";
    else value = el.value;
    if (label || value) fields.push(`- ${label}: ${clean(value).slice(0, 200)}`);
  });
  return {
    url: location.pathname + location.search,
    title: document.title,
    fields: fields.slice(0, 300),
    text: clean(document.body?.innerText).slice(0, 6000),
  };
}

/** Text snapshot of every frame in the OpenEMR tab: field labels/values plus visible text. */
export async function snapshotPage(): Promise<string> {
  const tab = await getOpenEmrTab();
  const results = await chrome.scripting.executeScript({ target: { tabId: tab.id!, allFrames: true }, func: snapshotFrame });
  return results
    .map((r) => r.result)
    .filter((f): f is ReturnType<typeof snapshotFrame> => !!f && (f.fields.length > 0 || f.text.length > 0))
    .map((f) => `## Frame ${f.url} — ${f.title}\nFields:\n${f.fields.join("\n") || "(none)"}\nVisible text:\n${f.text}`)
    .join("\n\n");
}

/** JPEG of the visible tab, base64 without the data: prefix. */
export async function screenshot(): Promise<string> {
  const tab = await getOpenEmrTab();
  const dataUrl = await chrome.tabs.captureVisibleTab(tab.windowId, { format: "jpeg", quality: 60 });
  return dataUrl.split(",")[1];
}
