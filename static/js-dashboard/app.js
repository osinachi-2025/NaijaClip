function getDashboardPage() {
  const queryPage = new URLSearchParams(window.location.search).get("page");
  if (queryPage) {
    const pageMap = {
      overview: "dashboard-overview",
      videos: "dashboard-videos",
      clips: "dashboard-clips",
      upload: "dashboard-upload",
      usage: "dashboard-usage",
      billing: "dashboard-billing",
      settings: "dashboard-settings",
      editor: "dashboard-editor",
    };
    return pageMap[queryPage] || queryPage;
  }

  const pathMap = {
    "/dashboard/videos": "dashboard-videos",
    "/dashboard/clips": "dashboard-clips",
    "/dashboard/upload": "dashboard-upload",
    "/dashboard/usage": "dashboard-usage",
    "/dashboard/billing": "dashboard-billing",
    "/dashboard/settings": "dashboard-settings",
    "/dashboard": "dashboard-overview",
  };

  return pathMap[window.location.pathname] || "dashboard-overview";
}

function requireDashboardAuth() {
  return true;
}

function renderDashboard() {
  if (!requireDashboardAuth()) return;
  const app = document.getElementById("app");
  const page = getDashboardPage(); state.page = page;
  let inner;
  if (page === "dashboard-overview") inner = dashboardOverviewHTML();
  else if (page === "dashboard-upload") inner = uploadPageHTML();
  else if (page === "dashboard-videos") inner = videosPageHTML();
  else if (page === "dashboard-clips") inner = clipsPageHTML();
  else if (page === "dashboard-editor") inner = clipEditorHTML();
  else if (page === "dashboard-billing") inner = billingPageHTML();
  else { const item = NAV_ITEMS.find((n) => n.key === page); inner = placeholderHTML(item ? item.label : ""); }
  app.innerHTML = dashboardShellHTML(inner);
  if (page === "dashboard-upload") attachUploadHandlers();
  if (page === "dashboard-editor") {
    const clipId = new URLSearchParams(window.location.search).get("clip");
    if (clipId && state.clipEditor?.requestedId !== clipId) loadClipEditor(clipId);
  }
}

async function loadDashboardData() {
  if (!requireDashboardAuth()) return;
  state.dashboardData.loading = true;
  try {
    const profileResponse = await authFetch('/users/me');
    if (!profileResponse.ok) {
      window.location.replace('/login');
      return;
    }
    const profile = await profileResponse.json();
    state.user = {
      ...state.user,
      ...profile,
      first_name: profile.first_name || '',
      last_name: profile.last_name || '',
      plan: profile.plan || 'free',
    };

    const [videosResponse, clipsResponse, billingResponse] = await Promise.all([
      authFetch('/api/videos'),
      authFetch('/api/clips'),
      authFetch('/billing'),
    ]);
    if (!videosResponse.ok || !clipsResponse.ok) throw new Error("Unable to load dashboard data");
    const videos = await videosResponse.json();
    const clips = await clipsResponse.json();
    state.billingData = billingResponse.ok ? await billingResponse.json() : null;
    state.dashboardData = { videos: videos.videos || [], clips: clips.clips || [], loading: false };
  } catch (error) {
    state.dashboardData = { videos: [], clips: [], loading: false, error: error.message };
  }
  renderDashboard();
}
