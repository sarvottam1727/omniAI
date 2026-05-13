import React, { useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  AlertTriangle,
  BarChart3,
  CheckCircle2,
  Clock,
  Contact,
  FileText,
  Inbox,
  LayoutDashboard,
  ListChecks,
  Mail,
  PauseCircle,
  PlayCircle,
  Settings,
  ShieldCheck,
  Upload,
  Users,
} from "lucide-react";
import "./styles.css";

const contacts = [
  { email: "aisha@example.com", name: "Aisha Mehta", company: "BrightPath", source: "CSV", consent: "opted_in" },
  { email: "rahul@example.com", name: "Rahul Iyer", company: "Northwind", source: "Manual", consent: "soft_opt_in" },
  { email: "unknown@example.com", name: "Unknown Lead", company: "Atlas", source: "API", consent: "unknown" },
  { email: "bounce@example.com", name: "Old Contact", company: "Contoso", source: "CRM", consent: "bounced" },
];

const checks = [
  { label: "Sender profile complete", status: "ok", detail: "Truthful From and Reply-To configured" },
  { label: "Physical address in footer", status: "ok", detail: "Required for marketing messages" },
  { label: "Unsubscribe link included", status: "ok", detail: "One-click tokenized unsubscribe" },
  { label: "Unknown consent blocked", status: "warn", detail: "1 recipient excluded from marketing send" },
  { label: "Provider limits respected", status: "ok", detail: "Campaign pauses at configured limits" },
];

const nav = [
  ["Dashboard", LayoutDashboard],
  ["Contacts", Contact],
  ["Import", Upload],
  ["Lists", Users],
  ["Campaign Wizard", Mail],
  ["Preview", FileText],
  ["Compliance", ShieldCheck],
  ["Queue", Clock],
  ["Logs", ListChecks],
  ["Suppression", PauseCircle],
  ["Settings", Settings],
];

function Stat({ label, value, tone }) {
  return (
    <section className={`stat ${tone || ""}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </section>
  );
}

function Sidebar({ active, setActive }) {
  return (
    <aside className="sidebar">
      <div className="brand">
        <span className="brandMark">O</span>
        <div>
          <strong>OmniAI</strong>
          <small>Email Shooter</small>
        </div>
      </div>
      <nav>
        {nav.map(([label, Icon]) => (
          <button key={label} className={active === label ? "active" : ""} onClick={() => setActive(label)} title={label}>
            <Icon size={18} />
            <span>{label}</span>
          </button>
        ))}
      </nav>
    </aside>
  );
}

function Dashboard() {
  return (
    <div className="stack">
      <div className="headerRow">
        <div>
          <p className="eyebrow">Production-grade bulk email</p>
          <h1>Consent-first campaign control center</h1>
        </div>
        <button className="primary"><PlayCircle size={18} /> New campaign</button>
      </div>
      <div className="stats">
        <Stat label="Campaigns sent" value="12" />
        <Stat label="Total recipients" value="8,420" />
        <Stat label="Delivery rate" value="97.8%" tone="good" />
        <Stat label="Suppressed" value="214" tone="caution" />
      </div>
      <section className="panel">
        <div className="panelTitle"><BarChart3 size={19} /> Campaign timeline</div>
        <div className="timeline">
          {["Draft created", "Contacts validated", "Test email sent", "Compliance confirmed", "Queued safely"].map((item, index) => (
            <div className="step" key={item}>
              <span>{index + 1}</span>
              <p>{item}</p>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}

function Contacts() {
  const eligible = useMemo(() => contacts.filter((c) => ["opted_in", "soft_opt_in"].includes(c.consent)), []);
  return (
    <div className="stack">
      <div className="headerRow">
        <div>
          <p className="eyebrow">Recipient source options</p>
          <h1>Contacts, imports, lists and consent</h1>
        </div>
        <button className="secondary"><Upload size={18} /> Import CSV/XLSX</button>
      </div>
      <div className="stats">
        <Stat label="All contacts" value={contacts.length} />
        <Stat label="Eligible now" value={eligible.length} tone="good" />
        <Stat label="Excluded" value={contacts.length - eligible.length} tone="caution" />
      </div>
      <section className="panel">
        <table>
          <thead><tr><th>Email</th><th>Name</th><th>Company</th><th>Source</th><th>Consent</th></tr></thead>
          <tbody>
            {contacts.map((contact) => (
              <tr key={contact.email}>
                <td>{contact.email}</td>
                <td>{contact.name}</td>
                <td>{contact.company}</td>
                <td>{contact.source}</td>
                <td><span className={`pill ${contact.consent}`}>{contact.consent}</span></td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}

function CampaignWizard() {
  return (
    <div className="wizard">
      <section className="panel formPanel">
        <p className="eyebrow">Step-by-step wizard</p>
        <h1>Create campaign</h1>
        <label>Campaign name<input defaultValue="May product newsletter" /></label>
        <label>Campaign type<select defaultValue="newsletter"><option>newsletter</option><option>marketing</option><option>transactional</option><option>sales_outreach</option><option>job_outreach</option></select></label>
        <label>Subject<input defaultValue="What changed in OmniAI this month" /></label>
        <label>Preview text<input defaultValue="A concise update for opted-in subscribers." /></label>
        <label>Campaign purpose<textarea defaultValue="Monthly product update to subscribers who opted in from the website." /></label>
      </section>
      <section className="panel editor">
        <div className="panelTitle"><Mail size={19} /> Template builder</div>
        <div className="toolbar">
          <button>B</button><button>I</button><button title="Insert link">↗</button><button title="Personalization">{"{{ }}"}</button>
        </div>
        <textarea defaultValue={`Hi {{first_name}},\n\nHere is your update from {{sender_name}}.\n\nYou can unsubscribe here: {{unsubscribe_url}}\n\n{{physical_address}}`} />
      </section>
    </div>
  );
}

function Compliance() {
  return (
    <div className="stack">
      <div>
        <p className="eyebrow">Guardrails before send</p>
        <h1>Compliance checklist</h1>
      </div>
      <section className="panel checks">
        {checks.map((check) => (
          <div className="check" key={check.label}>
            {check.status === "ok" ? <CheckCircle2 className="ok" /> : <AlertTriangle className="warn" />}
            <div><strong>{check.label}</strong><p>{check.detail}</p></div>
          </div>
        ))}
      </section>
      <section className="warning">
        This system does not include spam evasion, fake headers, rotating accounts, scraping, proxy rotation, CAPTCHA bypass, or provider limit bypass logic.
      </section>
    </div>
  );
}

function Queue() {
  return (
    <div className="stack">
      <div className="headerRow">
        <div>
          <p className="eyebrow">Safe sending pipeline</p>
          <h1>Campaign queue</h1>
        </div>
        <button className="secondary"><PauseCircle size={18} /> Pause campaign</button>
      </div>
      <section className="panel queue">
        {["queued", "sending", "sent", "delivered", "failed", "bounced", "unsubscribed", "skipped"].map((status, index) => (
          <div key={status}>
            <span>{status}</span>
            <strong>{[1200, 60, 940, 923, 8, 4, 6, 182][index]}</strong>
          </div>
        ))}
      </section>
    </div>
  );
}

function SettingsPage() {
  return (
    <div className="stack">
      <div>
        <p className="eyebrow">Local development mode</p>
        <h1>Provider and sender settings</h1>
      </div>
      <section className="panel formGrid">
        <label>Company<input defaultValue="Sarvottam Labs" /></label>
        <label>Sender name<input defaultValue="Sarvottam Team" /></label>
        <label>Sender email<input defaultValue="dev@omniai.local" /></label>
        <label>Reply-To<input defaultValue="support@omniai.local" /></label>
        <label>Provider<select defaultValue="mailpit"><option>mailpit</option><option>smtp</option><option>gmail_smtp</option><option>ses</option><option>sendgrid</option><option>mailgun</option><option>brevo</option></select></label>
        <label>Password<input type="password" value="masked-secret" readOnly /></label>
      </section>
    </div>
  );
}

function Placeholder({ title }) {
  return (
    <div className="stack">
      <p className="eyebrow">{title}</p>
      <h1>{title}</h1>
      <section className="panel empty"><Inbox size={28} /> Module screen ready for API-backed data.</section>
    </div>
  );
}

function Content({ active }) {
  if (active === "Dashboard") return <Dashboard />;
  if (["Contacts", "Import", "Lists", "Suppression", "Logs"].includes(active)) return <Contacts />;
  if (active === "Campaign Wizard" || active === "Preview") return <CampaignWizard />;
  if (active === "Compliance") return <Compliance />;
  if (active === "Queue") return <Queue />;
  if (active === "Settings") return <SettingsPage />;
  return <Placeholder title={active} />;
}

function App() {
  const [active, setActive] = useState("Dashboard");
  return (
    <main>
      <Sidebar active={active} setActive={setActive} />
      <section className="content">
        <Content active={active} />
      </section>
    </main>
  );
}

createRoot(document.getElementById("root")).render(<App />);
