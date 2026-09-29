/* ---------- Dashboard shell ---------- */
function sidebarNavHTML() {
  return NAV_ITEMS.map(
    (item) => `
    <button class="nc-navitem nc-focus ${state.page === item.key ? "active" : ""}" data-nav="${item.key}" data-close-drawer="1">
      ${item.icon}<span>${item.label}</span>
    </button>`
  ).join("");
}

function userDisplayName() {
  const first = (state.user?.first_name || '').trim();
  const last = (state.user?.last_name || '').trim();
  const fallback = (state.user?.email || 'User').split('@')[0] || 'User';
  if (first || last) return `${first} ${last}`.trim();
  return fallback;
}

function userInitials() {
  const first = (state.user?.first_name || '').trim();
  const last = (state.user?.last_name || '').trim();
  if (first || last) return `${first.charAt(0) || ''}${last.charAt(0) || ''}`.toUpperCase() || 'U';
  const email = (state.user?.email || 'User').trim();
  return email.slice(0, 2).toUpperCase() || 'U';
}

function userPlanLabel() {
  return (state.user?.plan || 'free').toLowerCase() === 'pro' ? 'Pro plan' : 'Free plan';
}

function sidebarInnerHTML() {
  return `
    <div class="nc-sidebar-head">${logoHTML()}</div>
    <nav class="nc-sidebar-nav">${sidebarNavHTML()}</nav>
    <div class="nc-sidebar-foot">
      <div class="nc-user-row">
        <div class="nc-avatar">${userInitials()}</div>
        <div style="min-width:0">
          <div class="nc-user-name">${userDisplayName()}</div>
          <div class="nc-user-plan">${userPlanLabel()}</div>
        </div>
      </div>
      <button class="nc-logout nc-focus" data-logout="1">${ICONS.logout}Log out</button>
    </div>`;
}

function clipModalHTML() {
  return `
    <div id="nc-clip-modal" class="nc-clip-modal" aria-hidden="true">
      <div class="nc-clip-modal-backdrop" data-clip-modal-close="1"></div>
      <div class="nc-clip-modal-panel">
        <button class="nc-clip-modal-close nc-focus" type="button" data-clip-modal-close="1" aria-label="Close video">${ICONS.x}</button>
        <div class="nc-clip-modal-stage">
          <video id="nc-clip-modal-video" controls playsinline preload="metadata"></video>
        </div>
      </div>
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
  </div>
  ${clipModalHTML()}`;
}

function dashboardOverviewHTML() {
  const videos = state.dashboardData.videos;
  const clips = state.dashboardData.clips;
  const processed = videos.filter((video) => video.status === "completed").length;
  const currentPlan = (state.user?.plan || 'free').toLowerCase() === 'pro' ? 'Pro' : 'Free';
  return `
  <div class="nc-page">
    <div class="nc-page-head">
      <div>
        <h1 class="nc-display nc-page-title">Welcome back, ${userDisplayName()}</h1>
        <p class="nc-page-sub">Here's what's happening with your videos.</p>
      </div>
      <button class="nc-btn nc-btn-primary nc-focus" data-nav="dashboard-upload">${ICONS.upload}Upload video</button>
    </div>

    <div class="nc-stats-row">
      ${statCardHTML("Videos processed", String(processed), "Completed videos")}
      ${statCardHTML("Clips generated", String(clips.length), "Available clips")}
      ${statCardHTML("Videos processing", String(videos.filter((video) => !["completed", "failed", "cancelled", "canceled"].includes(video.status)).length), "Currently active")}
      ${statCardHTML("Current plan", currentPlan, state.user?.plan ? `${state.user.plan.toLowerCase()} plan` : "User workspace")}
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
          ${clips.slice(0, 3)
            .map(
              (c) => `
            <div class="nc-clip-list-item nc-clip-list-playable" data-clip-id="${c.id || ""}">
              <div class="nc-clip-preview">
                ${c.output_url ? `<video controls preload="metadata" playsinline muted data-clip-id="${c.id || ""}" data-clip-url="${c.output_url}" data-clip-title="${(c.title || "Clip").replace(/"/g, "&quot;")}" data-clip-poster="${(c.thumbnail_url || "").replace(/"/g, "&quot;")}" src="${c.output_url}" poster="${c.thumbnail_url || ""}"></video>` : `<div class="nc-clip-list-thumb">${ICONS.play(11)}</div>`}
              </div>
              <div class="nc-clip-meta">
                <div class="nc-clip-list-title">${c.title}</div>
                <div class="nc-clip-list-dur">${formatClipDuration(c.duration_seconds)} · ${c.status}</div>
                ${c.id ? `<button class="nc-link nc-link-sm nc-focus" type="button" data-edit-clip="${c.id}">Edit clip</button>` : ""}
              </div>
              ${c.output_url ? `<button class="nc-clip-fullscreen-btn nc-focus" type="button" data-clip-modal-open="${c.id || ""}" data-clip-url="${c.output_url}" data-clip-title="${(c.title || "Clip").replace(/"/g, "&quot;")}" data-clip-poster="${(c.thumbnail_url || "").replace(/"/g, "&quot;")}">${ICONS.maximize}Full</button>` : ""}
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
  const videos = state.dashboardData.videos;
  if (!videos.length) return `<div class="nc-table-card"><div class="nc-placeholder-box">No videos uploaded yet.</div></div>`;
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
          ${videos.slice(0, 5).map(
            (v) => `
            <tr>
              <td><div class="nc-video-thumb">${ICONS.fileVideo}</div></td>
              <td>
                <span class="nc-video-title">${v.title}</span>
                <div class="nc-video-meta">${formatVideoDuration(v.duration_seconds)} · ${formatDate(v.created_at)} · ${v.clip_count} clips</div>
              </td>
              <td style="text-align:right">
                <span class="nc-status-badge ${v.status === "completed" ? "nc-status-ready" : "nc-status-processing"}">${videoStatusLabel(v)}</span>
                ${videoActionsHTML(v)}
              </td>
            </tr>`
          ).join("")}
        </tbody>
      </table>
    </div>
  </div>`;
}

function formatVideoDuration(seconds) {
  if (!seconds) return "Duration pending";
  const minutes = Math.floor(seconds / 60);
  return `${minutes}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;
}

function formatClipDuration(seconds) {
  return formatVideoDuration(seconds);
}

function formatDate(value) {
  return value ? new Date(value).toLocaleDateString() : "Recently";
}

function videoStatusLabel(video) {
  if (video.status === "completed") return "Ready";
  if (video.status === "failed") return "Failed";
  if (["cancelled", "canceled"].includes(video.status)) return "Cancelled";
  return `${video.job && video.job.progress ? video.job.progress + "%" : "Queued"}`;
}

function videoActionsHTML(video) {
  const terminal = ["completed", "failed", "cancelled", "canceled"].includes(video.status);
  return `<div class="nc-video-actions">
    ${terminal ? "" : `<button class="nc-btn nc-btn-outline nc-btn-sm nc-focus" type="button" data-video-cancel="${video.id}">Cancel</button>`}
    <button class="nc-btn nc-btn-outline nc-btn-sm nc-focus" type="button" data-video-delete="${video.id}" aria-label="Delete ${video.title}">${ICONS.trash}Delete</button>
  </div>`;
}

function retentionInfoHTML(label) {
  return `
    <div class="nc-retention-note" style="margin:0 0 18px; padding:10px 12px; border:1px solid rgba(148,163,184,.22); background:rgba(148,163,184,.06); border-radius:10px; color:var(--muted); font-size:12px; line-height:1.5;">
      <strong style="color:var(--text);">Note:</strong> ${label}
    </div>`;
}

function videosPageHTML() {
  return `
    <div class="nc-page">
      <div class="nc-page-head">
        <div>
          <h1 class="nc-display nc-page-title">Videos</h1>
          <p class="nc-page-sub">Your uploaded videos and their processing status.</p>
        </div>
        <button class="nc-btn nc-btn-primary nc-focus" data-nav="dashboard-upload">${ICONS.upload}Upload video</button>
      </div>
      ${retentionInfoHTML("Uploaded videos expire after 7 days. Please download anything you want to keep.")}
      ${recentVideosTableHTML()}
    </div>`;
}

function billingPageHTML() {
  const billing = state.billingData || {};
  const plan = (state.user?.plan || billing.current_plan || "free").toLowerCase();
  const planLabel = plan === "pro" ? "Pro" : "Free";
  const used = Number(billing.used_this_month ?? state.user?.videos_used ?? 0);
  const limit = Number(billing.usage_limit ?? 0);
  const latestPayment = billing.payments?.[0];
  const paymentStatus = latestPayment?.payment_mode === "test" && latestPayment?.status === "success"
    ? "Pending approval"
    : latestPayment?.status || billing.subscription_status || "No payment";
  return `
    <div class="nc-page">
      <div class="nc-page-head">
        <div>
          <h1 class="nc-display nc-page-title">Billing</h1>
          <p class="nc-page-sub">Manage your plan and payment status.</p>
        </div>
      </div>

      <div class="nc-stats-row">
        ${statCardHTML("Current plan", planLabel, `${planLabel} account`)}
        ${statCardHTML("Usage", limit ? `${used} / ${limit} videos` : `${used} videos`, "This month")}
        ${statCardHTML("Payment status", paymentStatus, latestPayment?.payment_mode || "Account status")}
        ${statCardHTML("Mode", latestPayment?.payment_mode || "Ready", latestPayment ? "Latest payment" : "No payment yet")}
      </div>

      <div class="nc-table-card" style="padding:24px; margin-top: 18px;">
        <h2 class="nc-display" style="margin:0 0 12px; font-size:26px;">Upgrade to Pro</h2>
        <p style="margin:0 0 18px; color:var(--muted);">This app is running in Paystack test mode, so the subscription stays pending until the admin approves it. No immediate activation happens.</p>
        <button id="init-pro-payment" class="nc-btn nc-btn-primary" type="button">Start Pro upgrade</button>
        <div id="billing-status" style="margin-top:12px; color:var(--muted); min-height:24px;"></div>
      </div>
    </div>`;
}

function clipsPageHTML() {
  const clips = state.dashboardData.clips;
  const featuredCount = clips.filter((clip) => clip.is_featured).length;
  if (!clips.length) return `
    <div class="nc-page">
      <h1 class="nc-display nc-page-title">Clips</h1>
      ${retentionInfoHTML("Clips expire after 30 days.")}
      <div class="nc-placeholder-box">No clips are ready yet.</div>
    </div>`;
  return `
    <div class="nc-page">
      <h1 class="nc-display nc-page-title">Clips</h1>
      <p class="nc-page-sub" style="margin-bottom:12px">Clips generated from your processed videos.</p>
      ${retentionInfoHTML("Clips expire after 30 days. Uploaded videos expire after 7 days.")}
      <div class="nc-clip-grid">
        ${clips.map((clip) => `
          <article class="nc-clip-video-card" data-clip-id="${clip.id || ""}">
            <div class="nc-clip-video-frame">
              ${clip.output_url ? `<video controls preload="metadata" playsinline muted data-clip-id="${clip.id || ""}" data-clip-url="${clip.output_url}" data-clip-title="${(clip.title || "Clip").replace(/"/g, "&quot;")}" data-clip-poster="${(clip.thumbnail_url || "").replace(/"/g, "&quot;")}" src="${clip.output_url}" poster="${clip.thumbnail_url || ""}"></video>` : `<div class="nc-clip-list-thumb nc-clip-list-thumb-lg">${ICONS.play(13)}</div>`}
              ${clip.output_url ? `<button class="nc-clip-fullscreen-btn nc-focus" type="button" data-clip-modal-open="${clip.id || ""}" data-clip-url="${clip.output_url}" data-clip-title="${(clip.title || "Clip").replace(/"/g, "&quot;")}" data-clip-poster="${(clip.thumbnail_url || "").replace(/"/g, "&quot;")}">${ICONS.maximize}Full</button>` : ""}
            </div>
            <div class="nc-clip-info">
              <div class="nc-clip-list-title">${clip.title}</div>
              <div class="nc-clip-list-dur">${formatClipDuration(clip.duration_seconds)} · ${clip.status}</div>
              ${state.user.role === "admin" && clip.is_featured ? `<div class="nc-featured-status">Featured on Homepage</div>` : ""}
            </div>
            <div class="nc-clip-actions">
              ${clip.output_url ? `<a class="nc-btn nc-btn-primary nc-btn-sm" href="${clip.output_url}" target="_blank" rel="noreferrer">Open</a>` : ""}
              ${clip.output_url ? `<button class="nc-btn nc-btn-outline nc-btn-sm nc-focus" type="button" data-edit-clip="${clip.id}">Edit</button>` : ""}
              ${state.user.role === "admin" && (clip.can_feature || clip.is_featured) ? clip.is_featured ? `
                <button class="nc-btn nc-btn-outline nc-btn-sm nc-focus" type="button" data-feature-clip="${clip.id}" data-feature-action="remove">Remove from Homepage</button>
                ${clip.can_feature ? `
                <button class="nc-btn nc-btn-ghost nc-btn-sm nc-focus" type="button" data-feature-clip="${clip.id}" data-feature-action="up" aria-label="Move featured clip up" ${clip.featured_order <= 1 ? "disabled" : ""}>Move up</button>
                <button class="nc-btn nc-btn-ghost nc-btn-sm nc-focus" type="button" data-feature-clip="${clip.id}" data-feature-action="down" aria-label="Move featured clip down" ${clip.featured_order >= featuredCount ? "disabled" : ""}>Move down</button>
                ` : ""}
              ` : `<button class="nc-btn nc-btn-outline nc-btn-sm nc-focus" type="button" data-feature-clip="${clip.id}" data-feature-action="add">Feature on Homepage</button>` : ""}
            </div>
          </article>`).join("")}
      </div>
    </div>`;
}

function openClipModal(clip) {
  const modal = document.getElementById("nc-clip-modal");
  const video = document.getElementById("nc-clip-modal-video");
  if (!modal || !video) return;

  const videoUrl = clip && clip.output_url ? clip.output_url : (clip && clip.url ? clip.url : "");
  const posterUrl = clip && clip.thumbnail_url ? clip.thumbnail_url : (clip && clip.poster ? clip.poster : "");

  if (!videoUrl) return;

  video.src = videoUrl;
  video.poster = posterUrl;
  video.load();
  modal.classList.add("visible");
  modal.setAttribute("aria-hidden", "false");
  video.play().catch(() => {});
}

function closeClipModal() {
  const modal = document.getElementById("nc-clip-modal");
  const video = document.getElementById("nc-clip-modal-video");
  if (modal) {
    modal.classList.remove("visible");
    modal.setAttribute("aria-hidden", "true");
  }
  if (video) {
    video.pause();
    video.removeAttribute("src");
    video.load();
  }
}

function bindClipPreviewInteractions() {
  const videos = document.querySelectorAll("video[data-clip-url]");
  videos.forEach((video) => {
    video.muted = true;
    video.playsInline = true;

    video.addEventListener("mouseenter", () => {
      if (!video.paused) return;
      video.play().catch(() => {});
    });

    video.addEventListener("mouseleave", () => {
      video.pause();
      video.currentTime = 0;
    });

    video.addEventListener("click", (event) => {
      event.preventDefault();
      openClipModal({
        output_url: video.dataset.clipUrl,
        thumbnail_url: video.dataset.clipPoster || "",
        title: video.dataset.clipTitle || "Clip preview",
      });
    });
  });
}
