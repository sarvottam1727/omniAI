// Shared OmniAI Campaign Studio shell helpers.
// Pages should call OmniAI.init({ route: "/providers" }) on DOMContentLoaded.

const NAV = [
  { route: "/",            label: "Dashboard",       icon: "grid" },
  { route: "/quick-launch", label: "Quick Launch",   icon: "zap" },
  { route: "/providers",   label: "Email Providers", icon: "mail" },
  { route: "/contacts",    label: "Contacts",        icon: "users" },
  { route: "/templates",   label: "Templates",       icon: "edit" },
  { route: "/campaigns",   label: "Campaigns",       icon: "send" },
  { route: "/live",        label: "Live Sending",    icon: "activity" },
  { route: "/reports",     label: "Reports",         icon: "bar-chart" },
  { route: "/suppression", label: "Suppression",     icon: "shield" },
  { route: "/chat",        label: "AI Chat",         icon: "message" },
  { route: "/settings",    label: "Settings",        icon: "settings" },
];

const ICONS = {
  "grid":      '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/></svg>',
  "zap":       '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>',
  "mail":      '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"/><polyline points="22,6 12,13 2,6"/></svg>',
  "users":     '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>',
  "edit":      '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>',
  "send":      '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>',
  "activity":  '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>',
  "bar-chart": '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="20" x2="12" y2="10"/><line x1="18" y1="20" x2="18" y2="4"/><line x1="6" y1="20" x2="6" y2="16"/></svg>',
  "shield":    '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>',
  "message":   '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>',
  "settings":  '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06A1.65 1.65 0 0 0 4.6 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>',
};

window.OmniAI = (function(){

  let state = {};
  let currentRoute = "/";

  function $(id){ return document.getElementById(id) }
  function escapeHtml(s){return String(s).replace(/[&<>"]/g, c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]))}

  function toast(message, kind="info", title=""){
    let wrap = $("toastWrap");
    if(!wrap){
      wrap = document.createElement("div"); wrap.id="toastWrap"; wrap.className="toast-wrap";
      document.body.appendChild(wrap);
    }
    const el = document.createElement("div");
    el.className = "toast " + kind;
    const icons = {ok:"✓", bad:"!", info:"i"};
    el.innerHTML = `<div class="ti">${icons[kind]||"i"}</div><div class="msg">${title?`<b>${escapeHtml(title)}</b>`:""}<span>${escapeHtml(message)}</span></div>`;
    wrap.appendChild(el);
    requestAnimationFrame(()=>el.classList.add("show"));
    setTimeout(()=>{el.classList.remove("show"); setTimeout(()=>el.remove(), 300)}, 3600);
  }

  async function api(path, options={}){
    const response = await fetch(path, options);
    let data; try{ data = await response.json() }catch{ data = {} }
    if(!response.ok || data.ok===false){ throw new Error(data.message || "Request failed") }
    return data;
  }

  function consentClass(v){return ["opted_in","soft_opt_in","transactional"].includes(v)?"ok":v==="unknown"?"warn":"bad"}

  function renderSidebar(){
    const items = NAV.map(n=>`
      <a href="${n.route}" data-route="${n.route}" class="sb-link ${currentRoute===n.route?'active':''}">
        ${ICONS[n.icon] || ""}<span>${n.label}</span>
      </a>
    `).join("");
    return `
      <aside class="sidebar">
        <div class="sb-brand">
          <div class="logo">O</div>
          <div>
            <div class="brand-name">OmniAI</div>
            <div class="brand-sub">Campaign Studio</div>
          </div>
        </div>
        <nav class="sb-nav">${items}</nav>
        <div class="sb-foot">
          <div class="sb-health" id="sbHealth">
            <span class="dot"></span>
            <div><div class="t">SMTP</div><div class="s" id="sbHealthLabel">checking…</div></div>
          </div>
          <div class="sb-mode" id="sbMode" style="display:none">
            <span class="pill info" id="sbModePill">Local</span>
          </div>
        </div>
      </aside>
    `;
  }

  function renderTopbar(opts={}){
    const title = opts.title || "Dashboard";
    const subtitle = opts.subtitle || "";
    return `
      <div class="topbar">
        <div class="tb-left">
          <button class="icon-btn-mini" id="sbToggle" aria-label="Toggle sidebar">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="18" x2="21" y2="18"/></svg>
          </button>
          <div>
            <div class="tb-title">${escapeHtml(title)}</div>
            ${subtitle ? `<div class="tb-sub">${escapeHtml(subtitle)}</div>` : ""}
          </div>
        </div>
        <div class="tb-right">
          <div class="tb-search">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
            <input type="search" id="globalSearch" placeholder="Search (alpha)..." />
          </div>
          <div class="badges">
            <span class="badge" id="senderBadge"><span class="ico"></span>No sender</span>
            <span class="badge" id="contactBadge"><span class="ico"></span>0</span>
            <span class="badge" id="campaignBadge"><span class="ico"></span>No campaign</span>
          </div>
        </div>
      </div>
    `;
  }

  async function renderBadges(){
    const sb = $("senderBadge");
    const senders = (state.senders||[]).filter(s=>s.id!=="local-mailpit" && s.id!=="local-dryrun");
    const real = senders.find(s=>s.password_configured) || senders[0];
    if(sb){
      if(real){ sb.className="badge ok"; sb.innerHTML=`<span class="ico"></span>${escapeHtml(real.label||real.sender_email)}`; }
      else { const dr=(state.senders||[]).find(s=>s.provider==="dryrun"); sb.className=dr?"badge info":"badge warn"; sb.innerHTML=`<span class="ico"></span>${dr?"Dry-run sender":"No sender"}`; }
    }
    const cb = $("contactBadge");
    if(cb){ const n=(state.contacts||[]).length; cb.className=n>0?"badge ok":"badge"; cb.innerHTML=`<span class="ico"></span>${n} recipient${n===1?"":"s"}`; }
    const pb = $("campaignBadge");
    if(pb){
      const last = (state.campaigns||[]).slice(-1)[0];
      pb.className = last ? "badge info" : "badge";
      pb.innerHTML = `<span class="ico"></span>${last ? "Last: "+escapeHtml(last.name) : "No campaign"}`;
    }
    // Sidebar SMTP health dot
    const hb = $("sbHealth"); const hl = $("sbHealthLabel");
    if(hb && hl){
      if(real && real.password_configured){ hb.className="sb-health ok"; hl.textContent="Verified · "+(real.sender_email||""); }
      else if((state.senders||[]).find(s=>s.provider==="dryrun")){ hb.className="sb-health info"; hl.textContent="Dry-run active"; }
      else { hb.className="sb-health warn"; hl.textContent="Not configured"; }
    }
    // LLM mode pill
    try{
      const r = await api("/api/chat-config");
      const m = $("sbMode"); const p = $("sbModePill");
      if(m && p){ m.style.display="block"; p.className = r.enabled?"pill ok":"pill muted"; p.textContent = r.enabled?"AI mode · "+(r.model||"Claude"):"Local mode"; }
    }catch{}
  }

  async function refresh(){
    try{ state = await api("/api/state"); }catch{ state = {}; }
    await renderBadges();
    return state;
  }

  function getState(){ return state }

  function lsGet(k, def){ try{ const v=localStorage.getItem("omniai:"+k); return v===null?def:JSON.parse(v) }catch{ return def } }
  function lsSet(k, v){ try{ localStorage.setItem("omniai:"+k, JSON.stringify(v)) }catch{} }

  function init(opts={}){
    currentRoute = opts.route || "/";
    const shell = document.getElementById("appShell");
    if(shell){
      shell.innerHTML = renderSidebar() + `<main class="content"><div id="topbarWrap">${renderTopbar(opts)}</div><div id="pageContent">${shell.innerHTML}</div></main>`;
      // Hook sidebar toggle (mobile)
      const tog = document.getElementById("sbToggle");
      if(tog) tog.addEventListener("click", ()=>{
        document.querySelector(".sidebar")?.classList.toggle("open");
      });
    } else {
      // Legacy single-page mode (chat) — render topbar into existing #topbar
      const tb = document.getElementById("topbar");
      if(tb) tb.innerHTML = renderTopbar(opts);
    }
    refresh().catch(err=>toast(err.message || "Connection error","bad","Error"));
  }

  return { init, refresh, api, toast, getState, consentClass, escapeHtml, lsGet, lsSet, $, NAV };
})();
