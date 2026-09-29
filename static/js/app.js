/* ---------- Render ---------- */
function render() {
  const app = document.getElementById("app");

  if (state.page === "landing") {
    app.innerHTML = landingHTML();
  } else {
    let inner;
    if (state.page === "dashboard-overview") inner = dashboardOverviewHTML();
    else if (state.page === "dashboard-upload") inner = uploadPageHTML();
    else {
      const item = NAV_ITEMS.find((n) => n.key === state.page);
      inner = placeholderHTML(item ? item.label : "");
    }
    app.innerHTML = dashboardShellHTML(inner);
    if (state.page === "dashboard-upload") attachUploadHandlers();
  }
}
