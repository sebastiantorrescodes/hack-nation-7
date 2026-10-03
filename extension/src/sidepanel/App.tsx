import { useState } from "react";
import Capture from "./Capture";
import Tutor from "./Tutor";
import WorkMapView from "./WorkMapView";

const TABS = [
  { id: "capture", label: "Capture" },
  { id: "workmap", label: "Work Map" },
  { id: "tutor", label: "Tutor" },
] as const;
type TabId = (typeof TABS)[number]["id"];

export default function App() {
  const [tab, setTab] = useState<TabId>("capture");
  return (
    <div className="app">
      <header>
        <strong>Billing Apprentice</strong>
        <nav>
          {TABS.map((t) => (
            <button key={t.id} className={tab === t.id ? "tab active" : "tab"} onClick={() => setTab(t.id)}>
              {t.label}
            </button>
          ))}
        </nav>
      </header>
      {/* Keep every tab mounted so a running interview or tutor session survives tab switches. */}
      <main>
        <div hidden={tab !== "capture"}>
          <Capture />
        </div>
        <div hidden={tab !== "workmap"}>
          <WorkMapView active={tab === "workmap"} />
        </div>
        <div hidden={tab !== "tutor"}>
          <Tutor active={tab === "tutor"} />
        </div>
      </main>
    </div>
  );
}
