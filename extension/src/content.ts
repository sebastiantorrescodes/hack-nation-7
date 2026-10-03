// Runs in every frame of every page (many apps nest their screens in iframes).
// 1. Reports what the user does (field changes, button clicks) to the side panel. The side panel ignores
//    these unless an interview is being recorded.
// 2. In tutor mode, holds Save/Submit clicks until the tutor's guardrail check passes.
// Keep this file free of imports: content scripts can't load shared chunks.

const SAVE_RE = /\b(save|submit)\b/i;
let guardSave = false;
let bypass = false;

chrome.storage.local.get("guardSave").then((v) => (guardSave = !!v.guardSave));
chrome.storage.onChanged.addListener((changes) => {
  if (changes.guardSave) guardSave = !!changes.guardSave.newValue;
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
  return (el as HTMLInputElement).value ?? "";
}

function buttonText(el: Element): string {
  const v = el instanceof HTMLInputElement ? el.value : (el as HTMLElement).innerText;
  return (v || el.getAttribute("title") || "").replace(/\s+/g, " ").trim().slice(0, 60);
}

function report(text: string) {
  chrome.runtime.sendMessage({ type: "ui-action", text, frame: location.pathname, t: Date.now() }).catch(() => {
    // Side panel closed: nobody is listening, which is fine.
  });
}

document.addEventListener(
  "change",
  (e) => {
    const el = e.target as Element;
    if (!el.matches("input, select, textarea")) return;
    if ((el as HTMLInputElement).type === "password") return; // runs on every site: never report passwords
    report(`changed "${labelFor(el)}" to "${valueOf(el).slice(0, 120)}"`);
  },
  true,
);

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
    close.style.cssText = "margin-top:8px;padding:4px 10px;border-radius:6px;border:1px solid #ccc;background:#fff;cursor:pointer";
    close.onclick = () => box.remove();
    box.appendChild(close);
  }
  document.body.appendChild(box);
  return box;
}

const esc = (s: string) => s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]!);

document.addEventListener(
  "click",
  async (e) => {
    const el = (e.target as Element).closest("button, a, input[type=submit], input[type=button]");
    if (!el) return;
    const text = buttonText(el);
    if (text) report(`clicked "${text}"`);

    if (!guardSave || bypass || !SAVE_RE.test(text)) return;

    // Hold the save until the tutor has checked the record against the Work Map guardrails.
    e.preventDefault();
    e.stopImmediatePropagation();
    const pending = overlay("Checking this against the Work Map…", "info");
    let res: { ok: boolean; message?: string } | undefined;
    try {
      res = await chrome.runtime.sendMessage({ type: "save-attempt" });
    } catch {
      res = undefined; // side panel closed: fail open
    }
    pending.remove();
    if (!res || res.ok) {
      bypass = true;
      (el as HTMLElement).click();
      bypass = false;
    } else {
      overlay(`<b>Hold on before saving</b><div style="margin-top:6px">${esc(res.message ?? "")}</div>`, "error");
    }
  },
  true,
);
