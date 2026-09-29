/* ---------- Global event delegation ---------- */
document.addEventListener("click", (e) => {
  const navEl = e.target.closest("[data-nav]");
  if (navEl) {
    e.preventDefault();
    go(navEl.getAttribute("data-nav"));
    return;
  }
  const toastEl = e.target.closest("[data-toast]");
  if (toastEl) {
    toast(toastEl.getAttribute("data-toast"));
    return;
  }
  const billingEl = e.target.closest("[data-billing]");
  if (billingEl) {
    state.billing = billingEl.getAttribute("data-billing");
    render();
    return;
  }
  if (e.target.closest("[data-open-mobile]")) {
    state.mobileOpen = true;
    render();
    return;
  }
  if (e.target.closest("[data-close-mobile]")) {
    state.mobileOpen = false;
    render();
    return;
  }
});
