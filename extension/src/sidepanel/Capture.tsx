import { useConversation } from "@elevenlabs/react";
import { useEffect, useRef, useState } from "react";
import { api, type CaptureSession, type ScreenEvent, type WorkMap } from "../lib/api";
import { screenshot, snapshotPage } from "../lib/page";

type Line = { t: number; who: "expert" | "agent" | "screen"; text: string; decision?: boolean };

// Wait this long after the expert's last UI action before asking Claude what happened.
const SETTLE_MS = 2500;

async function ensureMicrophone(): Promise<boolean> {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    stream.getTracks().forEach((t) => t.stop());
    return true;
  } catch {
    // Side panels can't show the permission prompt; ask from a normal extension tab instead.
    chrome.tabs.create({ url: chrome.runtime.getURL("permission.html") });
    return false;
  }
}

type Props = {
  workflowId: string;
  expertName: string;
  /** Lets the parent stop navigation away while recording (unmounting would end the interview). */
  onRecordingChange: (recording: boolean) => void;
  onBuilt: (wm: WorkMap) => void;
};

export default function Capture({ workflowId, expertName, onRecordingChange, onBuilt }: Props) {
  const [session, setSession] = useState<CaptureSession | null>(null);
  const [lines, setLines] = useState<Line[]>([]);
  const [withScreenshots, setWithScreenshots] = useState(false);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [workmap, setWorkmap] = useState<WorkMap | null>(null);

  const sessionRef = useRef<CaptureSession | null>(null);
  const pendingActions = useRef<string[]>([]);
  const settleTimer = useRef<number>();
  const analyzing = useRef(false);
  const screenshotsRef = useRef(withScreenshots);
  screenshotsRef.current = withScreenshots;

  const elapsed = () => (sessionRef.current ? Date.now() / 1000 - sessionRef.current.started_at : 0);
  const addLine = (l: Line) => setLines((prev) => [...prev, l]);

  const conversation = useConversation({
    onMessage: ({ message, source }: { message: string; source: string }) => {
      const s = sessionRef.current;
      if (!s) return;
      const role = source === "user" ? "expert" : "agent";
      const t = elapsed();
      addLine({ t, who: role, text: message });
      api(`/api/capture/sessions/${s.id}/transcript`, { body: { t, role, text: message } }).catch((e) => setError(String(e)));
    },
    onError: (e: unknown) => setError(`Voice agent: ${String(e)}`),
  });
  // analyze() runs from a listener registered once, so read the live conversation through a ref.
  const conversationRef = useRef(conversation);
  conversationRef.current = conversation;

  async function analyze() {
    const s = sessionRef.current;
    if (!s || analyzing.current || pendingActions.current.length === 0) return;
    analyzing.current = true;
    const actions = pendingActions.current.splice(0);
    try {
      const page = await snapshotPage();
      const image_base64 = screenshotsRef.current ? await screenshot() : undefined;
      const event = await api<ScreenEvent | null>(`/api/capture/sessions/${s.id}/frames`, {
        body: { t: elapsed(), actions, page, image_base64 },
      });
      if (event) {
        addLine({ t: event.t, who: "screen", text: event.description, decision: event.is_decision_point });
        const conv = conversationRef.current;
        if (event.is_decision_point && event.ask_why && conv.status === "connected") {
          // Steer the voice agent: it asks the question at the next natural pause.
          conv.sendContextualUpdate(
            `On screen, the expert just did this: ${event.description}. ` +
              `This is a judgment call. At the next natural pause, ask: "${event.ask_why}"`,
          );
        }
      }
    } catch (e) {
      setError(String(e));
    } finally {
      analyzing.current = false;
      if (pendingActions.current.length) settleTimer.current = window.setTimeout(analyze, SETTLE_MS);
    }
  }

  // UI actions reported by the content script running in the app.
  useEffect(() => {
    const onMsg = (msg: { type?: string; text?: string }) => {
      if (msg.type !== "ui-action" || !sessionRef.current || !msg.text) return;
      pendingActions.current.push(msg.text);
      window.clearTimeout(settleTimer.current);
      settleTimer.current = window.setTimeout(analyze, SETTLE_MS);
    };
    chrome.runtime.onMessage.addListener(onMsg);
    return () => chrome.runtime.onMessage.removeListener(onMsg);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function start() {
    setError("");
    setWorkmap(null);
    setLines([]);
    if (!(await ensureMicrophone())) {
      setError("Allow the microphone in the tab that just opened, then press Start again.");
      return;
    }
    setBusy("Starting…");
    try {
      const s = await api<CaptureSession>("/api/capture/sessions", { body: { expert_name: expertName, workflow_id: workflowId } });
      sessionRef.current = s;
      setSession(s);
      const { signed_url } = await api<{ signed_url: string }>("/api/voice/signed-url");
      await conversation.startSession({ signedUrl: signed_url, dynamicVariables: { expert_name: s.expert_name } });
      // Baseline snapshot so Claude knows the starting screen.
      pendingActions.current.push("started the session");
      analyze();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy("");
    }
  }

  async function stop() {
    await conversation.endSession();
    window.clearTimeout(settleTimer.current);
    await analyze();
  }

  async function buildWorkMap() {
    if (!session) return;
    setBusy("Claude is building the Work Map…");
    setError("");
    try {
      const wm = await api<WorkMap>(`/api/capture/sessions/${session.id}/workmap`, { body: {} });
      setWorkmap(wm);
      onBuilt(wm);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy("");
    }
  }

  const live = conversation.status === "connected";
  useEffect(() => onRecordingChange(live), [live, onRecordingChange]);

  return (
    <section>
      <p className="muted">
        Work in the app as usual. The voice agent asks why at each decision point.
      </p>

      {!live && (
        <button className="primary" onClick={start} disabled={!!busy || !expertName}>
          Start interview
        </button>
      )}
      {live && (
        <div className="row">
          <span className="pill live">● Recording {conversation.isSpeaking ? "· agent speaking" : ""}</span>
          <button onClick={stop}>Stop</button>
        </div>
      )}
      <label className="check">
        <input type="checkbox" checked={withScreenshots} onChange={(e) => setWithScreenshots(e.target.checked)} />
        Also send screenshots (slower, helps on screens with little text)
      </label>

      {busy && <p className="muted">{busy}</p>}
      {error && <p className="error">{error}</p>}

      <ol className="log">
        {lines.map((l, i) => (
          <li key={i} className={`log-${l.who}${l.decision ? " decision" : ""}`}>
            <span className="t">{l.t.toFixed(0)}s</span>
            <span className="who">{l.who === "screen" ? (l.decision ? "decision" : "screen") : l.who}</span>
            <span>{l.text}</span>
          </li>
        ))}
      </ol>

      {session && !live && lines.length > 0 && !workmap && (
        <button className="primary" onClick={buildWorkMap} disabled={!!busy}>
          Build Work Map
        </button>
      )}
      {workmap && (
        <p className="ok">
          Work Map ready with {workmap.skills.length} skills. Review them below and approve the ones trainees should learn.
        </p>
      )}
    </section>
  );
}
