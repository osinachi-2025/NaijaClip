/* ---------- Landing page ---------- */
function landingHTML() {
  const platforms = ["TikTok", "Instagram Reels", "YouTube Shorts", "Facebook"];
  const steps = [
    { n: "Upload", d: "Drop in a podcast, sermon, interview, or property tour — any long-form video." },
    { n: "AI finds the moments", d: "NaijaClip scans the transcript for hooks, strong opinions, and standalone stories." },
    { n: "Clips, captioned", d: "Each moment becomes a vertical clip with captions, ready to post." },
  ];
  const audiences = ["Podcasters", "YouTubers", "Real estate agents", "Coaches", "Educators", "Churches", "Businesses"];

  return `
  <header class="nc-navbar">
    <div class="nc-navbar-inner">
      ${logoHTML()}
      <nav class="nc-navbar-links nc-hide-mobile">
        <button class="nc-link nc-focus" data-toast="Pricing page — next up in this build">Pricing</button>
        <button class="nc-link nc-focus" data-nav="dashboard-overview">Log in</button>
      </nav>
      <button class="nc-btn nc-btn-primary nc-btn-sm nc-focus" data-nav="dashboard-overview">Start clipping</button>
    </div>
  </header>

  <section class="nc-hero-section">
    <div class="nc-hero-grid">
      <div class="nc-hero-copy">
        <h1 class="nc-display nc-hero-title nc-rise">Turn your long videos into clips worth sharing.</h1>
        <p class="nc-hero-sub nc-rise" style="animation-delay:.08s">NaijaClip uses AI to find the moments that matter in your videos, turn them into short-form clips, add captions, and get them ready for social media.</p>
        <div class="nc-hero-ctas nc-rise" style="animation-delay:.14s">
          <button class="nc-btn nc-btn-primary nc-btn-reverse nc-focus" data-nav="dashboard-overview">${ICONS.arrowRight}Start clipping</button>
          <button class="nc-btn nc-btn-outline nc-focus" data-nav="dashboard-upload">See how it works</button>
        </div>
        <div class="nc-rise" style="animation-delay:.2s;margin-top:32px">
          <div class="nc-platform-label">Clips built for</div>
          <div class="nc-platform-pills">${platforms.map((p) => `<span class="nc-pill">${p}</span>`).join("")}</div>
        </div>
      </div>
      <div class="nc-hero-visual">${clipStackHTML()}</div>
    </div>
  </section>

  <section class="nc-section-surface">
    <div class="nc-section-inner">
      <h2 class="nc-display nc-section-title">How it works</h2>
      <div class="nc-steps">
        ${steps
          .map(
            (s, i) => `
          <div class="nc-step ${i === 0 ? "is-first" : ""}">
            <div class="nc-step-title">${s.n}</div>
            <div class="nc-step-desc">${s.d}</div>
          </div>`
          )
          .join("")}
      </div>
    </div>
  </section>

  <section class="nc-section">
    <div class="nc-audience-grid">
      <div class="nc-audience-copy">
        <h2 class="nc-display">Built for creators doing the talking</h2>
        <p>If your content is long-form and spoken — a podcast, a property walkthrough, a sermon, a class — NaijaClip finds the parts worth cutting out on its own.</p>
      </div>
      <div class="nc-audience-tags">${audiences.map((a) => `<span class="nc-tag">${a}</span>`).join("")}</div>
    </div>
  </section>

  <section class="nc-section-dark">
    <div class="nc-section-dark-inner">
      <h2 class="nc-display">Every video hides a handful of clips</h2>
      <div class="nc-sub">Real moments NaijaClip has pulled out of longer recordings.</div>
      <div class="nc-clip-scroll nc-scrollbar">
        ${MOCK_CLIPS.map(
          (c) => `
          <div class="nc-dark-clip-card">
            <div class="nc-dark-clip-thumb">${ICONS.play(20, "rgba(255,255,255,0.6)")}</div>
            <div class="nc-dark-clip-title">${c.title}</div>
            <div class="nc-dark-clip-dur">${c.duration}</div>
          </div>`
        ).join("")}
      </div>
    </div>
  </section>

  <section class="nc-section">
    <div class="nc-pricing-head">
      <h2 class="nc-display" style="font-size:26px;font-weight:700">Simple pricing</h2>
      <div class="nc-toggle">
        <button class="${state.billing === "monthly" ? "active" : ""}" data-billing="monthly">Monthly</button>
        <button class="${state.billing === "yearly" ? "active" : ""}" data-billing="yearly">Yearly <span class="nc-save">−20%</span></button>
      </div>
    </div>
    <div class="nc-plans">
      ${planCardHTML({ name: "Free", price: 0, features: ["3 videos / month", "720p exports", "AI clip detection", "Captions", "NaijaClip watermark"] })}
      ${planCardHTML({
        name: "Pro",
        price: state.billing === "monthly" ? 15000 : 12000,
        highlight: true,
        features: ["50 videos / month", "1080p exports", "No watermark", "Priority processing", "Brand customization"],
      })}
    </div>
  </section>

  <section class="nc-section" style="padding-top:0">
    <div class="nc-final-cta">
      <h2 class="nc-display">Your next clip is already in your camera roll.</h2>
      <p>Upload once. Get short-form content ready to share.</p>
      <button class="nc-btn nc-btn-primary nc-focus" data-nav="dashboard-overview">Start clipping — it's free</button>
    </div>
  </section>

  <footer class="nc-footer">
    <div class="nc-footer-inner">
      ${logoHTML()}
      <span class="nc-footer-copy">© 2026 NaijaClip. Built for creators.</span>
    </div>
  </footer>`;
}

function planCardHTML(p) {
  return `
    <div class="nc-plan-card ${p.highlight ? "highlight" : ""}">
      ${p.highlight ? `<span class="nc-plan-badge">Most popular</span>` : ""}
      <div class="nc-plan-name">${p.name}</div>
      <div class="nc-plan-price nc-display">${p.price === 0 ? "₦0" : "₦" + p.price.toLocaleString()}<span> /mo</span></div>
      <div class="nc-plan-features">
        ${p.features.map((f) => `<div class="nc-plan-feature">${ICONS.check(15, p.highlight ? "var(--accent)" : "var(--muted)")}${f}</div>`).join("")}
      </div>
      <button class="nc-btn ${p.highlight ? "nc-btn-primary" : "nc-btn-outline"} nc-btn-full nc-focus" data-nav="dashboard-overview">
        ${p.price === 0 ? "Start free" : "Start free trial"}
      </button>
    </div>`;
}
