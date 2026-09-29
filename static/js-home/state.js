const state = { billing: "monthly", isAuthenticated: false, authChecked: false };
let toastTimer = null;
function toast(msg) { const el = document.getElementById("toast"); el.innerHTML = `${ICONS.check(16, "var(--accent)")}<span>${msg}</span>`; el.classList.add("show"); clearTimeout(toastTimer); toastTimer = setTimeout(() => el.classList.remove("show"), 2600); }
