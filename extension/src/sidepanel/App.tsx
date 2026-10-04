import { useState } from "react";
import AccessGate from "./AccessGate";
import Expert from "./Expert";
import Trainee from "./Trainee";

const TABS = [
  { id: "expert", label: "Expert" },
  { id: "trainee", label: "Trainee" },
] as const;
type TabId = (typeof TABS)[number]["id"];

export default function App() {
  return <AccessGate>{role => <Workspace role={role} />}</AccessGate>;
}

function Workspace({role}: {role: "expert" | "trainee" | "admin"}) {
  const [tab, setTab] = useState<TabId>(role === "trainee" ? "trainee" : "expert");
  return (
    <div className="app">
      <header>
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">A</span>
          <div>
            <h1>AI Apprentice</h1>
            <p className="brand-caption">Experience becomes expertise.</p>
          </div>
          <span className="app-version" aria-label="Version 0.1.4">0.1.4</span>
        </div>
        <nav aria-label="Choose your role">
          {TABS.filter(t => role !== "trainee" || t.id === "trainee").map((t) => (
            <button key={t.id} className={tab === t.id ? "tab active" : "tab"} aria-pressed={tab === t.id} onClick={() => setTab(t.id)}>
              {t.label}
            </button>
          ))}
        </nav>
      </header>
      {/* Keep both tabs mounted so a running interview or tutor session survives tab switches. */}
      <main>
        <div hidden={tab !== "expert"}>
          <Expert active={tab === "expert"} />
        </div>
        <div hidden={tab !== "trainee"}>
          <Trainee active={tab === "trainee"} />
        </div>
      </main>
    </div>
  );
}
