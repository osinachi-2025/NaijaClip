const state = {
  page: "dashboard-overview",
  mobileOpen: false,
  uploadFile: null,
  uploadJob: null,
  dashboardData: { videos: [], clips: [], loading: true },
  billingData: null,
  clipEditor: null,
  user: {
    id: "",
    email: "",
    first_name: "",
    last_name: "",
    role: "user",
    plan: "free",
    subscription_status: "inactive",
    videos_used: 0,
  },
};
let toastTimer = null;
function toast(msg, type = "success") {
  const el = document.getElementById("toast");
  const icon = document.createElement("span");
  icon.innerHTML = type === "error" ? ICONS.x : ICONS.check(16, "currentColor");
  const text = document.createElement("span");
  text.textContent = msg;
  el.replaceChildren(icon, text);
  el.className = `nc-toast ${type} show`;
  el.setAttribute("role", type === "error" ? "alert" : "status");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), 3200);
}

function setButtonLoading(button, isLoading, label) {
  if (!button) return;
  if (isLoading) {
    button.dataset.originalContent = button.innerHTML;
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
    button.innerHTML = `<span class="nc-button-spinner" aria-hidden="true"></span><span>${label}</span>`;
    return;
  }
  button.disabled = false;
  button.removeAttribute("aria-busy");
  if (button.dataset.originalContent) {
    button.innerHTML = button.dataset.originalContent;
    delete button.dataset.originalContent;
  }
}
