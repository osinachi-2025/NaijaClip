/* ---------- Dashboard shell ---------- */
function sidebarNavHTML() {
  return NAV_ITEMS.map(
    (item) => `
    <button class="nc-navitem nc-focus ${state.page === item.key ? "active" : ""}" data-nav="${item.key}" data-close-drawer="1">
      ${item.icon}<span>${item.label}</span>
    </button>`
  ).join("");
}

function sidebarInnerHTML() {
  return `
    <div class="nc-sidebar-head">${logoHTML()}</div>
    <nav class="nc-sidebar-nav">${sidebarNavHTML()}</nav>
    <div class="nc-sidebar-foot">
      <div class="nc-user-row">
        <div class="nc-avatar">AO</div>
        <div style="min-width:0">
          <div class="nc-user-name">Adaeze Okonkwo</div>
          <div class="nc-user-plan">Pro plan</div>
        </div>
      </div>
      <button class="nc-logout nc-focus" data-nav="landing">${ICONS.logout}Log out</button>
    </div>`;
}

function dashboardShellHTML(innerPageHTML) {
  const active = NAV_ITEMS.find((n) => n.key === state.page);
  return `
  <div class="nc-dash">
    <aside class="nc-sidebar">${sidebarInnerHTML()}</aside>

    <div class="nc-sidebar-mobile ${state.mobileOpen ? "open" : ""}">
      <div class="nc-sidebar-overlay" data-close-mobile="1"></div>
      <div class="nc-sidebar-drawer">
        <div class="nc-drawer-close"><button class="nc-focus" data-close-mobile="1">${ICONS.x}</button></div>
        ${sidebarInnerHTML()}
      </div>
    </div>

    <div class="nc-main">
      <div class="nc-topbar">
        <button class="nc-focus" data-open-mobile="1">${ICONS.menu}</button>
        <span class="nc-topbar-title nc-display">${active ? active.label : "NaijaClip"}</span>
      </div>
      ${innerPageHTML}
    </div>
  </div>`;
}

function dashboardOverviewHTML() {
  return `
  <div class="nc-page">
    <div class="nc-page-head">
      <div>
        <h1 class="nc-display nc-page-title">Welcome back, Adaeze</h1>
        <p class="nc-page-sub">Here's what's happening with your videos.</p>
      </div>
      <button class="nc-btn nc-btn-primary nc-focus" data-nav="dashboard-upload">${ICONS.upload}Upload video</button>
    </div>

    <div class="nc-stats-row">
      ${statCardHTML("Videos processed", "7 / 50", "This month")}
      ${statCardHTML("Clips generated", "34", "+12 this week")}
      ${statCardHTML("Processing minutes", "312 / 1,000", "Resets in 18 days")}
      ${statCardHTML("Current plan", "Pro", "₦15,000 / mo")}
    </div>

    <div class="nc-overview-grid">
      <div class="nc-overview-col-wide">
        <div class="nc-col-head">
          <h2>Recent videos</h2>
          <button class="nc-link nc-link-sm nc-focus" data-nav="dashboard-videos">View all ${ICONS.chevronRight}</button>
        </div>
        ${recentVideosTableHTML()}
      </div>

      <div class="nc-overview-col-narrow">
        <div class="nc-col-head">
          <h2>Recent clips</h2>
          <button class="nc-link nc-link-sm nc-focus" data-nav="dashboard-clips">View all ${ICONS.chevronRight}</button>
        </div>
        <div class="nc-clip-list">
          ${MOCK_CLIPS.slice(0, 3)
            .map(
              (c) => `
            <div class="nc-clip-list-item">
              <div class="nc-clip-list-thumb">${ICONS.play(11)}</div>
              <div style="min-width:0">
                <div class="nc-clip-list-title">${c.title}</div>
                <div class="nc-clip-list-dur">${c.duration}</div>
              </div>
            </div>`
            )
            .join("")}
        </div>
      </div>
    </div>
  </div>`;
}

function statCardHTML(label, value, sub) {
  return `
    <div class="nc-stat-card">
      <div class="nc-stat-label">${label}</div>
      <div class="nc-stat-value nc-display">${value}</div>
      <div class="nc-stat-sub">${sub}</div>
    </div>`;
}

/* Recent videos rendered as a real <table>, wrapped so it scrolls
   inside its own card instead of pushing the page wider (this was
   the "recent videos table runs outside the page" bug — table-layout:
   fixed + explicit column widths + ellipsis truncation on the title
   cell keeps it inside the grid column at every viewport size). */
function recentVideosTableHTML() {
  return `
  <div class="nc-table-card">
    <div class="nc-table-wrap nc-scrollbar">
      <table class="nc-video-table">
        <colgroup>
          <col class="col-thumb" />
          <col class="col-title" />
          <col class="col-status" />
        </colgroup>
        <tbody>
          ${MOCK_VIDEOS.map(
            (v) => `
            <tr>
              <td><div class="nc-video-thumb">${ICONS.fileVideo}</div></td>
              <td>
                <span class="nc-video-title">${v.title}</span>
                <div class="nc-video-meta">${v.duration} · ${v.date} · ${v.clips} clips</div>
              </td>
              <td style="text-align:right">
                <span class="nc-status-badge ${v.status === "Ready" ? "nc-status-ready" : "nc-status-processing"}">${v.status}</span>
              </td>
            </tr>`
          ).join("")}
        </tbody>
      </table>
    </div>
  </div>`;
}
