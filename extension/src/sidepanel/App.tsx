import { useState } from "react";
import Expert from "./Expert";
import Trainee from "./Trainee";

const TABS = [
  { id: "expert", label: "Expert" },
  { id: "trainee", label: "Trainee" },
] as const;
type TabId = (typeof TABS)[number]["id"];

export default function App() {
  const [tab, setTab] = useState<TabId>("expert");
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
