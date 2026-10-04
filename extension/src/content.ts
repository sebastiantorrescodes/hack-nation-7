// Runs in every frame of every page (many apps nest their screens in iframes).
// 1. Reports what the user does (field changes, button clicks) to the side panel. The side panel ignores
//    these unless an interview is being recorded.
// 2. In tutor mode, holds Save/Submit clicks until the tutor's guardrail check passes.
// Keep this file free of imports: content scripts can't load shared chunks.

(() => {
  const scope = globalThis as typeof globalThis & {__apprenticeCaptureInstalled?: boolean};
  if (scope.__apprenticeCaptureInstalled) return;
  const SAVE_RE = /\b(save|submit)\b/i;
  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message.type === "capture-ready") sendResponse({version: "durable-events-v2"});
    if (message.type === "capture-flush") {
      void flushEdits().then(() => sendResponse({ok: true})).catch(() => sendResponse({ok: false}));
      return true;
    }
  });
  let guardSave = false;
  let bypass = false;
  let checking = false;
  let ownTab: number | undefined;
  let guard: {tabId?: number; sessionId?: string} | false = false;
  const syncGuard = () => { guardSave = !!guard && guard.tabId === ownTab && !!guard.sessionId; };
  chrome.runtime.sendMessage({type: "tab-identity"}).then(v => { ownTab = v?.tabId; syncGuard(); }).catch(() => undefined);
  chrome.storage.local.get("guardSave").then(v => { guard = typeof v.guardSave === "object" ? v.guardSave : false; syncGuard(); });
  chrome.storage.onChanged.addListener(changes => {
    if (changes.guardSave) { guard = typeof changes.guardSave.newValue === "object" ? changes.guardSave.newValue : false; syncGuard(); }
  });

  function labelFor(el: Element): string {
    const input = el as HTMLInputElement;
    const fromLabel = input.labels?.[0]?.innerText;
    const prevCell = el.closest("td")?.previousElementSibling?.textContent;
    const raw =
      fromLabel ||
      el.getAttribute("aria-label") ||
      el.getAttribute("title") ||
      el.getAttribute("placeholder") ||
      prevCell ||
      el.getAttribute("name") ||
      el.id ||
      el.tagName.toLowerCase();
    return raw.replace(/\s+/g, " ").trim().slice(0, 80);
  }

  function valueOf(el: Element): string {
    if (el instanceof HTMLSelectElement) return el.selectedOptions[0]?.text ?? el.value;
    if (el instanceof HTMLInputElement && (el.type === "checkbox" || el.type === "radio")) return el.checked ? "checked" : "unchecked";
    return (el as HTMLInputElement).value ?? el.getAttribute("aria-valuetext") ?? el.getAttribute("aria-valuenow")
      ?? el.getAttribute("aria-checked") ?? (el as HTMLElement).innerText ?? "";
  }

  function buttonText(el: Element): string {
    const v = el instanceof HTMLInputElement ? el.value : (el as HTMLElement).innerText;
    return (v || el.getAttribute("aria-label") || el.getAttribute("title") || el.getAttribute("name") || "").replace(/\s+/g, " ").trim().slice(0, 60);
  }

  function eventIdentity(): string {
    if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
    // randomUUID may be unavailable on an HTTP website; keep using cryptographic IDs.
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128;
    const hex = Array.from(bytes, b => b.toString(16).padStart(2, "0")).join("");
    return `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`;
  }

  function report(text: string, event: Record<string, unknown>) {
    return chrome.runtime.sendMessage({ type: "ui-action", text, frame: location.pathname, t: Date.now(),
      event: {event_id: eventIdentity(), timestamp_ms: Date.now(), page: location.pathname,
        description: text, ...event} }).catch(() => {
      // Side panel closed: nobody is listening, which is fine.
    });
  }

  const EDITABLE = 'input, select, textarea, [contenteditable]:not([contenteditable="false"]), [role="textbox"], [role="combobox"], [role="checkbox"], [role="radio"], [role="switch"], [role="slider"]';
  const pendingEdits = new Map<Element, ReturnType<typeof setTimeout>>();
  const lastReported = new WeakMap<Element, string>();
  const beforeEdit = new WeakMap<Element, string>();
  function commitEdit(el: Element) {
    clearTimeout(pendingEdits.get(el)); pendingEdits.delete(el);
    const value = valueOf(el).slice(0, 1000);
    if (lastReported.get(el) === value) return;
    lastReported.set(el, value);
    const target = labelFor(el);
    const old_value = beforeEdit.get(el);
    beforeEdit.set(el, value);
    return report(`changed "${target}" to "${value}"`, {event_type: "field_change", target, new_value: value,
      ...(old_value === undefined ? {} : {old_value})});
  }
  function flushEdits() { return Promise.all(Array.from(pendingEdits.keys(), el => commitEdit(el))); }
  function editTarget(e: Event) {
    const target = (e.composedPath?.()[0] ?? e.target) as Element;
    const el = target?.closest(EDITABLE);
    if (!el || (el as HTMLInputElement).type === "password" || (el as HTMLInputElement).type === "hidden") return null;
    return el;
  }
  document.addEventListener("input", e => {
    const el = editTarget(e); if (!el) return;
    clearTimeout(pendingEdits.get(el));
    // One final value at a pause, rather than one API call per keystroke.
    pendingEdits.set(el, setTimeout(() => commitEdit(el), 700));
  }, true);
  document.addEventListener("focusin", e => {const el = editTarget(e); if (el) beforeEdit.set(el, valueOf(el).slice(0, 1000));}, true);
  document.addEventListener("change", e => { const el = editTarget(e); if (el) commitEdit(el); }, true);
  document.addEventListener("focusout", e => { const el = editTarget(e); if (el && pendingEdits.has(el)) commitEdit(el); }, true);

  function overlay(html: string, tone: "info" | "error") {
    document.getElementById("ba-overlay")?.remove();
    const box = document.createElement("div");
    box.id = "ba-overlay";
    box.style.cssText = `position:fixed;top:12px;right:12px;z-index:2147483647;max-width:360px;padding:12px 14px;
      border-radius:10px;font:14px/1.4 system-ui,sans-serif;box-shadow:0 6px 24px rgba(0,0,0,.2);
      background:${tone === "error" ? "#fff1f0" : "#f0f6ff"};color:#1a1a1a;
      border:1px solid ${tone === "error" ? "#e5484d" : "#3b82f6"}`;
    box.innerHTML = html;
    if (tone === "error") {
      const close = document.createElement("button");
      close.textContent = "Got it";
      close.style.cssText = "margin-top:8px;min-height:44px;min-width:44px;padding:8px 12px;border-radius:6px;border:1px solid #ccc;background:#fff;cursor:pointer";
      close.onclick = () => box.remove();
      box.appendChild(close);
    }
    document.body.appendChild(box);
    return box;
  }

  const esc = (s: string) => s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]!);

  async function verifySave(replay: () => void) {
    if (checking) return;
    checking = true;
    const pending = overlay("Checking this against the Work Map…", "info");
    let timeout: ReturnType<typeof setTimeout> | undefined;
    try {
      const res = await Promise.race([
        chrome.runtime.sendMessage({type: "save-attempt"}),
        new Promise<undefined>(resolve => { timeout = setTimeout(() => resolve(undefined), 60000); }),
      ]) as {ok?: boolean; message?: string} | undefined;
      if (res?.ok === true) {
        bypass = true;
        try { replay(); } finally { bypass = false; }
      } else {
        overlay(`<b>Save needs verification</b><div>${esc(res?.message || "Reopen the tutor and retry when it is connected.")}</div>`, "error");
      }
    } catch {
      overlay("<b>Save needs verification</b><div>Reopen the tutor before saving.</div>", "error");
    } finally { clearTimeout(timeout); pending.remove(); checking = false; }
  }

  document.addEventListener("click", e => {
    const target = (e.composedPath?.()[0] ?? e.target) as Element;
    const el = target.closest('button, a, input[type=submit], input[type=button], [role="button"], [role="tab"], [role="option"]');
    if (!el) return;
    flushEdits();
    const text = buttonText(el);
    if (text && !bypass) report(`clicked "${text}"`, {event_type: "click", target: text});
    if (!guardSave || bypass || !SAVE_RE.test(text)) return;
    e.preventDefault(); e.stopImmediatePropagation();
    void verifySave(() => (el as HTMLElement).click());
  }, true);

  // Enter-key and normal requestSubmit() submissions use the same verification gate.
  document.addEventListener("submit", e => {
    if (!guardSave || bypass) return;
    e.preventDefault(); e.stopImmediatePropagation();
    const form = e.target as HTMLFormElement;
    const submitter = (e as SubmitEvent).submitter as HTMLButtonElement | HTMLInputElement | null;
    report("attempted form submission", {event_type: "submit_attempt", target: submitter ? buttonText(submitter) : "form"});
    void verifySave(() => form.requestSubmit(submitter ?? undefined));
  }, true);
  scope.__apprenticeCaptureInstalled = true;
})();
