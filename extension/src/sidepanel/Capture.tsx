import { useConversation } from "@elevenlabs/react";
import { useEffect, useRef, useState } from "react";
import { api, type CaptureSession, type ScreenEvent, type WorkMap, type Workflow } from "../lib/api";
import { ensureCaptureLogger, flushCaptureEdits, getAppTab, readCaptureSnapshot, readScreenPreview, snapshotPage, type PageSnapshot } from "../lib/page";
import { CaptureQueue, captureTabMatches } from "../lib/captureQueue";
import { captureVoiceContext, prepareVoiceAudio } from "../lib/voice";

type Line = { t: number; who: "expert" | "agent" | "screen"; text: string; decision?: boolean };

// Wait this long after the expert's last UI action before asking the model what happened.
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
  workflow?: Workflow;
  expertName: string;
  /** Lets the parent stop navigation away while recording (unmounting would end the interview). */
  onRecordingChange: (recording: boolean) => void;
  onBuilt: (wm: WorkMap) => void;
};

export default function Capture({ workflowId, workflow, expertName, onRecordingChange, onBuilt }: Props) {
  const [session, setSession] = useState<CaptureSession | null>(null);
  const [lines, setLines] = useState<Line[]>([]);
  const [withScreenshots, setWithScreenshots] = useState(false);
  const [useQwen, setUseQwen] = useState(false);
  const [extraAnalysis, setExtraAnalysis] = useState(false);
  const extraAnalysisRef = useRef(extraAnalysis);
  extraAnalysisRef.current = extraAnalysis;
  const workflowRef = useRef(workflow);
  workflowRef.current = workflow;
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [workmap, setWorkmap] = useState<WorkMap | null>(null);
  const [savedEvents, setSavedEvents] = useState(0);
  const [website, setWebsite] = useState("");
  const [pageShared, setPageShared] = useState(false);
  const [screen, setScreen] = useState<PageSnapshot | null>(null);
  const [analysisStatus, setAnalysisStatus] = useState("");
  const [screenError, setScreenError] = useState("");
  const [analysisError, setAnalysisError] = useState("");
  const [connectionWarnings, setConnectionWarnings] = useState<string[]>([]);
  const [preview, setPreview] = useState<{summary: string; fields: Record<string, unknown>; uncertainties: string[]} | null>(null);
  const [previewImage, setPreviewImage] = useState<string | null>(null);
  const lastSharedPage = useRef("");
  const latestFrame = useRef<string | null>(null);

  const sessionRef = useRef<CaptureSession | null>(null);
  const pendingActions = useRef<string[]>([]);
  const pendingEvents = useRef<Record<string, unknown>[]>([]);
  const qwenRef = useRef(useQwen);
  qwenRef.current = useQwen;
  const queuedQuestion = useRef<{id: string; text: string} | null>(null);
  const [pendingQuestion, setPendingQuestion] = useState<{id: string; text: string} | null>(null);
  const promptedQuestion = useRef<string | null>(null);
  const deliveredQuestion = useRef<string | null>(null);
  const transcriptWrites = useRef<Promise<void>>(Promise.resolve());
  const expertSpeaking = useRef(false);
  const settleTimer = useRef<number>();
  const snapshotTimer = useRef<number>();
  const followupTimer = useRef<number>();
  const snapshotting = useRef<Promise<void> | null>(null);
  const snapshotAgain = useRef(false);
  const lastAnalysisAt = useRef(0);
  const analyzing = useRef<Promise<void> | null>(null);
  const accepting = useRef(false);
  const boundTab = useRef<number | null>(null);
  const queue = useRef<CaptureQueue | null>(null);
  const failedFrames = useRef<Record<string, unknown>[]>([]);
  const buildIdentity = useRef<string | null>(null);
  const [recoverable, setRecoverable] = useState<{session: CaptureSession; tabId: number; bounded?: boolean} | null>(null);
  const makeQueue = (id: string) => new CaptureQueue(`capture-writes:${id}`, chrome.storage.local, async (path, body) => {
    const result = await api<{detail?: string}>(path, {body});
    if (result.detail) setError(result.detail);
    else if (body && typeof body === "object" && "role" in body && body.role === "expert" && "question_id" in body && queuedQuestion.current?.id === body.question_id) {
      queuedQuestion.current = null; setPendingQuestion(null);
    }
    return result;
  });
  useEffect(() => {
    chrome.storage.local.get(`capture-session:${workflowId}`).then(v => setRecoverable(v[`capture-session:${workflowId}`] ?? null));
  }, [workflowId]);
  async function persist(path: string, body: Record<string, unknown>) {
    if (!queue.current) throw new Error("Capture queue is not ready.");
    await queue.current.enqueue({id: String(body.client_id), path, body});
    await queue.current.flush();
  }
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
      const normalize = (text: string) => text.trim().replace(/\s+/g, " ").toLowerCase();
      let question_id: string | undefined;
      const queued = queuedQuestion.current;
      if (qwenRef.current && role === "agent" && queued && normalize(message) === normalize(queued.text)) {
        question_id = queued.id;
        deliveredQuestion.current = queued.id;
      } else if (qwenRef.current && role === "expert" && deliveredQuestion.current) {
        question_id = deliveredQuestion.current;
        deliveredQuestion.current = null;
      }
      const body = { client_id: crypto.randomUUID(), t, role, text: message, ...(question_id ? {question_id} : {}) };
      // Queue writes before network work. A failed write stays available after the panel reopens.
      transcriptWrites.current = transcriptWrites.current.catch(() => undefined).then(async () => {
        await persist(`/api/capture/sessions/${s.id}/transcript`, body);
        if (qwenRef.current && role === "expert") {
          window.clearTimeout(followupTimer.current);
          followupTimer.current = window.setTimeout(() => void advance(), SETTLE_MS);
        }
      });
      void transcriptWrites.current.catch(e => setError(String(e)));

    },
    onVadScore: ({ vadScore }: { vadScore: number }) => { expertSpeaking.current = vadScore > 0.5; },
    onDisconnect: () => {
      accepting.current = false; promptedQuestion.current = null; lastSharedPage.current = ""; setPageShared(false);
      window.clearTimeout(settleTimer.current); window.clearTimeout(snapshotTimer.current); window.clearTimeout(followupTimer.current);
    },
    onError: (e: unknown) => setError(`Voice agent: ${String(e)}`),
  });
  // analyze() runs from a listener registered once, so read the live conversation through a ref.
  const conversationRef = useRef(conversation);
  conversationRef.current = conversation;

  function showEvent(event: ScreenEvent | null, frameId?: string) {
    if (!event) return;
    addLine({t: event.t, who: "screen", text: event.description, decision: event.is_decision_point});
    const conv = conversationRef.current;
    // Store the result, but don't interrupt with a question about a screen the expert has left.
    if (event.ask_why && conv.status === "connected" && accepting.current
        && (!frameId || frameId === latestFrame.current) && elapsed() - event.t < 20
        && !expertSpeaking.current && !conv.isSpeaking) {
      if (event.question_id && promptedQuestion.current === event.question_id) return;
      if (event.question_id) {
        queuedQuestion.current = {id: event.question_id, text: event.ask_why};
        setPendingQuestion(queuedQuestion.current); promptedQuestion.current = event.question_id;
      }
      conv.sendContextualUpdate(`The expert just did this: ${event.description}. At the next natural pause, ask: "${event.ask_why}"`);
    }
  }
  async function advance() {
    const s = sessionRef.current;
    if (!s || !accepting.current || analyzing.current || expertSpeaking.current || conversationRef.current.isSpeaking) return;
    try {
      await queue.current?.flush();
      showEvent(await api<ScreenEvent | null>(`/api/capture/sessions/${s.id}/apprentice/advance`, {
        body: {context: {expert_paused: true}, page: await snapshotPage(boundTab.current ?? undefined)}}));
    } catch (e) { setError(String(e)); }
  }
  function captureFrame(): Promise<void> {
    if (snapshotting.current) { snapshotAgain.current = true; return snapshotting.current; }
    const s = sessionRef.current;
    if (!s || boundTab.current === null) return Promise.resolve();
    const work = async () => {
      do {
        snapshotAgain.current = false;
        const snapshot = await readCaptureSnapshot(boundTab.current!, screenshotsRef.current && extraAnalysisRef.current && !qwenRef.current);
        setScreen(snapshot); setScreenError("");
        const body = {client_id: crypto.randomUUID(), t: elapsed(), actions: pendingActions.current.splice(0, 30),
            events: pendingEvents.current.splice(0, 30), page: snapshot.page, image_base64: snapshot.image_base64,
            reasoning_provider: qwenRef.current ? "qwen" : "standard",
            context: {expert_paused: !expertSpeaking.current && !conversationRef.current.isSpeaking,
              expert_speaking: expertSpeaking.current, expert_busy: false}};
        // Keep one pending visual frame; every raw action and snapshot is persisted separately.
        const inFlight = analyzing.current ? failedFrames.current[0] : undefined;
        failedFrames.current = inFlight ? [inFlight, body] : [body]; latestFrame.current = body.client_id;
        await chrome.storage.local.set({[`capture-analysis:${s.id}`]: failedFrames.current});
        // Commit facts and full page context before starting model reasoning.
        await persist(`/api/capture/sessions/${s.id}/observations`, body);
        // Give the voice interviewer page context even when no decision question is needed.
        // This remains a snapshot, not an invented observed action or continuous screen access.
        if (accepting.current && conversationRef.current.status === "connected" && typeof body.page === "string"
            && body.page && (body.page !== lastSharedPage.current || body.events.length > 0)) {
          conversationRef.current.sendContextualUpdate(captureVoiceContext(s.expert_name,
            `Observed browser actions (facts only):\n${body.actions.join("\n") || "No new action in this snapshot."}\n\n${body.page}`,
            qwenRef.current, JSON.stringify(workflowRef.current ?? {})));
          lastSharedPage.current = body.page; setPageShared(true);
        }
      } while (snapshotAgain.current || pendingActions.current.length);
    };
    snapshotting.current = work().catch(e => { setScreenError(`Screen capture needs attention: ${String(e)}`); throw e; })
      .finally(() => { snapshotting.current = null; });
    return snapshotting.current;
  }
  function analyze(): Promise<void> {
    if (analyzing.current) return analyzing.current;
    const s = sessionRef.current;
    if (!s) return Promise.resolve();
    let failed = false;
    const work = async () => {
      if (snapshotting.current) await snapshotting.current;
      if (pendingActions.current.length || accepting.current) await captureFrame();
      await queue.current?.flush();
      setAnalysisError("");
      if (failedFrames.current.length) {
        // All prior observations are durable. Reason about the newest screen, rather than
        // spending free quota on a backlog of stale screens; Work Map uses every raw event.
        if (failedFrames.current.length > 1) {
          failedFrames.current = failedFrames.current.slice(-1);
          await chrome.storage.local.set({[`capture-analysis:${s.id}`]: failedFrames.current});
        }
        const body = failedFrames.current[0];
        setAnalysisStatus("Analyzing captured screen…"); lastAnalysisAt.current = Date.now();
        const startedAt = Date.now();
        const event = await api<ScreenEvent | null>(`/api/capture/sessions/${s.id}/frames`, {body});
        if (sessionRef.current?.workmap_revision) return;
        showEvent(event, String(body.client_id));
        failedFrames.current.shift();
        if (!sessionRef.current?.workmap_revision) await chrome.storage.local.set({[`capture-analysis:${s.id}`]: failedFrames.current});
        const seconds = ((Date.now() - startedAt) / 1000).toFixed(1);
        setAnalysisStatus(`${event?.is_decision_point ? "Decision identified" : "Screen analyzed · no question needed"} · ${seconds}s`);
      }
    };
    analyzing.current = work().catch(e => {
      failed = true;
      if (sessionRef.current?.workmap_revision) return;
      setAnalysisStatus("Analysis paused · captured facts retained"); setAnalysisError(String(e)); throw e;
    }).finally(() => {
      analyzing.current = null;
      if (accepting.current && failedFrames.current.length && (qwenRef.current || extraAnalysisRef.current)) {
        window.clearTimeout(settleTimer.current);
        // A failed request waits for an explicit retry or new activity.
        if (!failed) settleTimer.current = window.setTimeout(() => void analyze().catch(() => undefined),
          Math.max(SETTLE_MS, 8000 - (Date.now() - lastAnalysisAt.current)));
      }
    });
    return analyzing.current;
  }
  const scheduleAnalysis = () => {
    window.clearTimeout(snapshotTimer.current);
    snapshotTimer.current = window.setTimeout(() => void captureFrame().catch(() => undefined), 350);
    window.clearTimeout(settleTimer.current);
    // Limit automatic reasoning frequency; snapshots and saved actions still update promptly.
    if (qwenRef.current || extraAnalysisRef.current) {
      settleTimer.current = window.setTimeout(() => void analyze().catch(() => undefined),
        Math.max(SETTLE_MS, 8000 - (Date.now() - lastAnalysisAt.current)));
    }
  };

  // UI actions reported by the content script running in the app.
  useEffect(() => {
    const onMsg = (msg: { type?: string; text?: string; event?: Record<string, unknown> }, sender: chrome.runtime.MessageSender,
                   sendResponse: (result: unknown) => void) => {
      if (msg.type !== "ui-action" || !sessionRef.current || !msg.text || !captureTabMatches(boundTab.current, sender.tab?.id, accepting.current)) return;
      if (!msg.event?.event_id) {setError("The website event logger is outdated. Refresh the interview tab before continuing."); return;}
      pendingActions.current.push(msg.text);
      addLine({t: elapsed(), who: "screen", text: msg.text});
      if (msg.event) {
        pendingEvents.current.push(msg.event);
        const body = {client_id: `event:${msg.event.event_id}`, t: elapsed(), events: [msg.event]};
        void persist(`/api/capture/sessions/${sessionRef.current.id}/observations`, body)
          .then(() => { setSavedEvents(n => n + 1); sendResponse({ok: true}); })
          .catch(e => { setError(String(e)); sendResponse({ok: false}); });
      }
      scheduleAnalysis();
      return true;
    };
    chrome.runtime.onMessage.addListener(onMsg);
    const onTabUpdated = (tabId: number, change: chrome.tabs.TabChangeInfo) => {
      if (!captureTabMatches(boundTab.current, tabId, accepting.current) || (!change.url && change.status !== "complete")) return;
      if (change.url) {
        const page = new URL(change.url).pathname;
        onMsg({type: "ui-action", text: "navigated to a new page", event: {
          event_id: crypto.randomUUID(), timestamp_ms: Date.now(), event_type: "navigation", page,
          description: "navigated to a new page"}}, {tab: {id: tabId}} as chrome.runtime.MessageSender, () => undefined);
      } else {
        void ensureCaptureLogger(tabId).then(connection => {setConnectionWarnings(connection.warnings); scheduleAnalysis();})
          .catch(e => setScreenError(String(e)));
      }
    };
    chrome.tabs.onUpdated.addListener(onTabUpdated);
    return () => {
      chrome.runtime.onMessage.removeListener(onMsg); chrome.tabs.onUpdated.removeListener(onTabUpdated);
      window.clearTimeout(settleTimer.current); window.clearTimeout(snapshotTimer.current); window.clearTimeout(followupTimer.current);
    };
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
      const tab = await getAppTab();
      boundTab.current = tab.id!;
      const connection = await ensureCaptureLogger(tab.id!);
      setConnectionWarnings(connection.warnings);
      setWebsite(new URL(tab.url!).hostname); setSavedEvents(0);
      const audioOptions = await prepareVoiceAudio();
      const s = await api<CaptureSession>("/api/capture/sessions", { body: { session_id: crypto.randomUUID(), expert_name: expertName, workflow_id: workflowId } });
      sessionRef.current = s;
      queue.current = makeQueue(s.id);
      accepting.current = true;
      failedFrames.current = [];
      buildIdentity.current = null;
      await chrome.storage.local.set({[`capture-session:${workflowId}`]: {session: s, tabId: boundTab.current, bounded: qwenRef.current}});
      queuedQuestion.current = null;
      setPendingQuestion(null); promptedQuestion.current = null;
      deliveredQuestion.current = null;
      pendingActions.current = [];
      pendingEvents.current = [];
      setScreen(null); setScreenError(""); setAnalysisError(""); setAnalysisStatus("");
      latestFrame.current = null; lastAnalysisAt.current = 0;
      lastSharedPage.current = "";
      setSession(s);
      const { signed_url } = await api<{ signed_url: string }>("/api/voice/signed-url");
      await conversation.startSession({
        signedUrl: signed_url,
        dynamicVariables: { expert_name: s.expert_name },
        ...audioOptions,
      });
      // Baseline snapshot so the model knows the starting screen.
      pendingActions.current.push("started the session");
      await captureFrame();
      if (qwenRef.current || extraAnalysisRef.current) void analyze().catch(() => undefined);
      else setAnalysisStatus("Page and actions shared with the voice interviewer");
    } catch (e) {
      if (conversationRef.current.status !== "connected") accepting.current = false;
      setError(String(e));
    } finally { setBusy(""); }
  }

  async function stop() {
    setBusy("Saving the final actions…");
    try {
      // Flush typing that has not reached the 700ms pause or blur yet.
      if (boundTab.current !== null) {
        try { await flushCaptureEdits(boundTab.current); }
        catch { setScreenError("Could not flush edits in every frame. Pending captured actions remain saved for retry."); }
      }
      accepting.current = false;
      window.clearTimeout(settleTimer.current); window.clearTimeout(snapshotTimer.current); window.clearTimeout(followupTimer.current);
      try { await captureFrame(); } catch { /* End voice even if the page was closed. */ }
      await conversation.endSession();
      await transcriptWrites.current;
      await queue.current?.flush();
      // Model work may finish in the background. Stop only waits for durable facts.
      if (sessionRef.current) {
        await api(`/api/capture/sessions/${sessionRef.current.id}/debrief`, {body: {}});
        sessionRef.current = {...sessionRef.current, phase: "debrief"};
        setSession(sessionRef.current);
      }
    } catch (e) { setError(`The interview is saved for retry: ${String(e)}`); }
    finally { setBusy(""); }
  }
  async function resume() {
    const s = sessionRef.current;
    if (!s || s.phase === "finished" || s.workmap_revision !== 0 || !(await ensureMicrophone())) return;
    setBusy("Resuming interview…"); setError("");
    try {
      await snapshotPage(boundTab.current ?? undefined);
      if (boundTab.current === null) throw new Error("The interview tab is unavailable. Recover the saved interview first.");
      const connection = await ensureCaptureLogger(boundTab.current);
      setConnectionWarnings(connection.warnings);
      await queue.current?.flush();
      const audioOptions = await prepareVoiceAudio();
      const {signed_url} = await api<{signed_url: string}>("/api/voice/signed-url");
      const resumed = await api<CaptureSession>(`/api/capture/sessions/${s.id}/resume`, {body: {}});
      sessionRef.current = resumed; setSession(resumed);
      await conversation.startSession({signedUrl: signed_url, dynamicVariables: {expert_name: s.expert_name}, ...audioOptions});
      accepting.current = true;
      pendingActions.current.push("resumed the interview");
      await captureFrame();
      if (qwenRef.current || extraAnalysisRef.current) void analyze().catch(() => undefined);
    } catch(e) {accepting.current = false; setError(String(e));}
    finally {setBusy("");}
  }
  async function confirmDelivery() {
    const s = sessionRef.current, q = queuedQuestion.current;
    if (!s || !q) return;
    try {
      await queue.current?.flush();
      await api(`/api/capture/sessions/${s.id}/apprentice/questions/${q.id}/delivery`, {body: {confirmed: true}});
      deliveredQuestion.current = q.id;
      setError("");
    } catch (e) { setError(String(e)); }
  }
  async function linkLastAnswer() {
    const s = sessionRef.current, q = queuedQuestion.current;
    if (!s || !q) return;
    try {
      await queue.current?.flush();
      const latest = await api<CaptureSession>(`/api/capture/sessions/${s.id}`);
      const turn = latest.transcript?.filter(t => t.role === "expert").at(-1);
      if (!turn?.segment_id) throw new Error("No recorded expert answer yet.");
      await api(`/api/capture/sessions/${s.id}/apprentice/questions/${q.id}/link-answer`, {body: {segment_id: turn.segment_id}});
      queuedQuestion.current = null; setPendingQuestion(null); deliveredQuestion.current = null; setError("");
      await advance();
    } catch (e) {setError(String(e));}
  }
  async function recover() {
    if (!recoverable) return;
    setBusy("Recovering saved capture…");
    try {
      sessionRef.current = recoverable.session;
      queue.current = makeQueue(recoverable.session.id);
      boundTab.current = recoverable.tabId;
      qwenRef.current = !!recoverable.bounded; setUseQwen(!!recoverable.bounded);
      await queue.current.flush();
      const stored = await chrome.storage.local.get(`capture-analysis:${recoverable.session.id}`);
      failedFrames.current = stored[`capture-analysis:${recoverable.session.id}`] ?? [];
      const s = await api<CaptureSession>(`/api/capture/sessions/${recoverable.session.id}`);
      sessionRef.current = s; setSession(s);
      setSavedEvents(s.events?.length ?? 0);
      const tab = await chrome.tabs.get(recoverable.tabId);
      setWebsite(tab.url ? new URL(tab.url).hostname : "original tab");
      if (recoverable.bounded) {
        const state = await api<{questions: {id: string; text: string}[]; answers: {question_id: string}[]}>(`/api/capture/sessions/${s.id}/apprentice`);
        queuedQuestion.current = state.questions.find(q => !state.answers.some(a => a.question_id === q.id)) ?? null;
        setPendingQuestion(queuedQuestion.current);
      }
      setLines((s.transcript ?? []).map(t => ({t: t.t, who: t.role, text: t.text})));
      setRecoverable(null);
    } catch (e) { setError(String(e)); }
    finally { setBusy(""); }
  }

  async function buildWorkMap() {
    if (!session) return;
    setBusy("Building the Work Map…");
    setError("");
    try {
      await transcriptWrites.current.catch(() => undefined);
      await queue.current?.flush();
      // Screen reasoning is optional enrichment; saved actions + expert evidence can build
      // even when the screen-analysis provider is temporarily unavailable.
      if (snapshotting.current) await snapshotting.current;
      if (pendingActions.current.length) await captureFrame();
      await queue.current?.flush();
      await api(`/api/capture/sessions/${session.id}/debrief`, {body: {}});
      buildIdentity.current ??= crypto.randomUUID();
      const latest = await api<CaptureSession>(`/api/capture/sessions/${session.id}`);
      const wm = await api<WorkMap>(`/api/capture/sessions/${session.id}/workmap`, { body: {client_id: buildIdentity.current, expected_revision: latest.workmap_revision} });
      sessionRef.current = {...latest, workmap_revision: wm.revision, workmap_id: wm.id};
      failedFrames.current = []; setAnalysisError(""); setAnalysisStatus("");
      setWorkmap(wm);
      onBuilt(wm);
      await chrome.storage.local.remove([`capture-session:${workflowId}`, `capture-writes:${session.id}`, `capture-analysis:${session.id}`]);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy("");
    }
  }

  async function checkScreen() {
    setBusy("Reading the visible screen…"); setError(""); setPreview(null); setPreviewImage(null);
    try {
      // One explicit free-model request. No microphone, logger, session or database write.
      const body = await readScreenPreview(accepting.current ? boundTab.current ?? undefined : undefined);
      setPreviewImage(body.image_base64);
      const result = await api<{summary: string; fields: Record<string, unknown>; uncertainties: string[]}>("/api/capture/screen-preview", {body});
      setPreview(result);
      if (accepting.current && conversationRef.current.status === "connected") {
        conversationRef.current.sendContextualUpdate(captureVoiceContext(expertName,
          "Screenshot interpretation (model-derived, may be incomplete):\n" + JSON.stringify(result), qwenRef.current,
          JSON.stringify(workflowRef.current ?? {})));
      }
    } catch (e) { setError(`Screen check: ${String(e)}`); }
    finally { setBusy(""); }
  }

  const live = conversation.status === "connected";
  useEffect(() => onRecordingChange(live || !!busy), [live, busy, onRecordingChange]);

  return (
    <section>
      <p className="muted">
        Work in the app as usual. The voice agent asks why at each decision point.
      </p>
      <button onClick={checkScreen} disabled={!!busy}>Check screen</button>
      <p className="muted">Read the visible page with one screenshot and one model request. No interview needed; free API limits apply.</p>
      {previewImage && <details open><summary>Captured screenshot</summary>
        <img className="capture-image" src={`data:image/jpeg;base64,${previewImage}`} alt="Captured visible website" />
        <p className="muted">The picture was captured. AI interpretation depends on available model capacity.</p>
      </details>}
      {preview && <div className="card"><p>{preview.summary}</p>
        <p className="muted">Screenshot interpretation · no action evidence or skills created</p>
        <details><summary>Values read from the screen</summary><pre className="screen-preview">{JSON.stringify(preview.fields, null, 2)}</pre></details>
        {preview.uncertainties.map(text => <p className="muted" key={text}>{text}</p>)}
      </div>}

      {recoverable && !session && <button onClick={recover} disabled={!!busy}>Recover saved interview</button>}
      {!live && !session && (
        <button className="primary" onClick={start} disabled={!!busy || !expertName}>
          Start interview
        </button>
      )}
      {!live && session && session.phase !== "finished" && session.workmap_revision === 0 && !workmap && <button onClick={resume} disabled={!!busy}>Resume interview</button>}
      {session && <div className="capture-status" role="status" aria-live="polite">
        <p>Interview website: {website || "original tab"} · Actions saved: {savedEvents}</p>
        {screen && <p className="muted">{screen.fields} readable fields · {screen.frames} frames · Captured at {new Date(screen.capturedAt).toLocaleTimeString()}{pageShared ? " · Shared with voice agent" : ""}</p>}
        {analysisStatus && <p className="muted">{analysisStatus}</p>}
      </div>}
      {screen?.warnings.map(warning => <p className="muted" key={warning}>{warning}</p>)}
      {connectionWarnings.map(warning => <p className="muted" key={warning}>{warning}</p>)}
      {screenError && <p className="error">{screenError}</p>}
      {analysisError && <p className="error">Screen analysis: {analysisError}</p>}
      {session && !workmap && <details><summary>What the agent can read</summary>
        <p className="muted">This is the latest saved text snapshot. Screenshots help screen analysis but are not sent directly to the voice agent.</p>
        <pre className="screen-preview">{screen?.page || "No screen snapshot yet."}</pre>
      </details>}
      {live && <button onClick={() => void captureFrame().catch(() => undefined)} disabled={!!busy}>Refresh screen context</button>}
      {session && !workmap && <button onClick={async () => {
        try {
          if (boundTab.current === null) throw new Error("The interview tab is unavailable.");
          await chrome.tabs.update(boundTab.current, {active: true});
        } catch(e) {setError(String(e));}
      }}>Return to interview tab</button>}
      {live && (
        <div className="row">
          <span className="pill live">● Recording {conversation.isSpeaking ? "· agent speaking" : ""}</span>
          <button onClick={stop} disabled={!!busy}>Stop</button>
        </div>
      )}
      <details><summary>Interview options</summary>
      <label className="check">
        <input type="checkbox" checked={useQwen} onChange={(e) => setUseQwen(e.target.checked)} disabled={live || !!session} />
        Use bounded apprentice for interview questions (optional)
      </label>
      <label className="check">
        <input type="checkbox" checked={extraAnalysis} onChange={e => setExtraAnalysis(e.target.checked)} disabled={live || useQwen} />
        Add automatic screen analysis (uses extra free API requests)
      </label>
      <label className="check">
        <input type="checkbox" checked={withScreenshots} onChange={(e) => setWithScreenshots(e.target.checked)} disabled={useQwen || !extraAnalysis} />
        Include screenshots in automatic analysis (slower)
      </label>
      </details>

      {busy && <p className="muted">{busy}</p>}
      {error && <p className="error">{error}</p>}
      {session && !workmap && (analysisError || (!live && (useQwen || extraAnalysis) && failedFrames.current.length > 0)) && <button onClick={() => void analyze().catch(() => undefined)} disabled={!!busy}>Retry pending screen analysis</button>}
      {useQwen && pendingQuestion && <div><p>{pendingQuestion.text}</p><button onClick={() => void confirmDelivery()}>Confirm the agent asked this question</button><button onClick={() => void linkLastAnswer()}>Link my last recorded answer to this question</button></div>}

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
