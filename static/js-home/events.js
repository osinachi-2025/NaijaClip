/* ---------- Global event delegation (home page) ---------- */
document.addEventListener("click", (e) => {
  const navEl = e.target.closest("[data-nav]");
  if (navEl) {
    e.preventDefault();
    const page = navEl.getAttribute("data-nav");
    if (page === "landing") { renderHome(); window.scrollTo(0, 0); }
    else goDashboard(page);
    return;
  }
  const scrollEl = e.target.closest("[data-scroll]");
  if (scrollEl) {
    const t = document.getElementById(scrollEl.getAttribute("data-scroll"));
    if (t) t.scrollIntoView({ behavior: "smooth" });
    return;
  }
  const faqEl = e.target.closest("[data-faq]");
  if (faqEl) {
    const item = faqEl.parentElement;
    faqEl.setAttribute("aria-expanded", item.classList.toggle("open"));
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
    const y = window.scrollY;
    renderHome();
    window.scrollTo(0, y);
    return;
  }
});