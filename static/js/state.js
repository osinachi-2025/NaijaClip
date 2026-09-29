/* ---------- App state ---------- */
const state = {
  page: "landing",
  mobileOpen: false,
  billing: "monthly",
  uploadFile: null, // { name, size, duration }
};

let toastTimer = null;
function toast(msg) {
  const el = document.getElementById("toast");
  el.innerHTML = `${ICONS.check(16, "var(--accent)")}<span>${msg}</span>`;
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), 2600);
}

function go(page) {
  state.page = page;
  state.mobileOpen = false;
  render();
  window.scrollTo(0, 0);
}
