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
        <button class="nc-link nc-focus" data-scroll="features">Features</button>
        <button class="nc-link nc-focus" data-scroll="how">How it works</button>
        <button class="nc-link nc-focus" data-scroll="pricing">Pricing</button>
        <button class="nc-link nc-focus" data-scroll="faq">FAQ</button>
        <button id="home-account-nav" class="nc-link nc-focus" data-nav="dashboard-overview" style="visibility:${state.authChecked ? "visible" : "hidden"}">${state.isAuthenticated ? "Dashboard" : "Log in"}</button>
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
          <button class="nc-btn nc-btn-outline nc-focus" data-scroll="how">See how it works</button>
        </div>
        <div class="nc-rise" style="animation-delay:.2s;margin-top:32px">
          <div class="nc-platform-label">Clips built for</div>
          <div class="nc-platform-pills">${platforms.map((p) => `<span class="nc-pill">${p}</span>`).join("")}</div>
        </div>
      </div>
      <div class="nc-hero-visual">${clipStackHTML()}</div>
    </div>
  </section>

  ${statsHTML()}

  <section class="nc-section-surface" id="how">
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

  ${featuresHTML()}

  <section class="nc-section">
    <div class="nc-audience-grid">
      <div class="nc-audience-copy">
        <h2 class="nc-display">Built for creators doing the talking</h2>
        <p>If your content is long-form and spoken — a podcast, a property walkthrough, a sermon, a class — NaijaClip finds the parts worth cutting out on its own.</p>
      </div>
      <div class="nc-audience-tags">${audiences.map((a) => `<span class="nc-tag">${a}</span>`).join("")}</div>
    </div>
  </section>

  <section class="nc-section-dark nc-featured-section" id="homepage-featured-section">
    <div class="nc-section-dark-inner">
      <h2 class="nc-display">See NaijaClip in Action</h2>
      <div class="nc-sub">Real clips selected by our creators.</div>
      <div class="nc-featured-clip-grid" id="homepage-featured-clips">${featuredClipPlaceholdersHTML()}</div>
    </div>
  </section>

  ${testimonialsHTML()}

  <section class="nc-section" id="pricing">
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

  ${faqHTML()}

  <section class="nc-section" style="padding-top:0">
    <div class="nc-final-cta">
      <h2 class="nc-display">Your next clip is already in your camera roll.</h2>
      <p>Upload once. Get short-form content ready to share.</p>
      <button class="nc-btn nc-btn-primary nc-focus" data-nav="dashboard-overview">Start clipping — it's free</button>
    </div>
  </section>

  ${footerHTML()}`;
}

function escapeFeaturedClipHTML(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[character]);
}

function featuredClipPlaceholdersHTML() {
  return Array.from({ length: 3 }, (_, index) => `
    <article class="nc-featured-clip-card nc-featured-clip-placeholder" aria-label="Featured clip placeholder ${index + 1}">
      <div class="nc-featured-video-frame">
        <div class="nc-featured-placeholder-content">
          <span class="nc-featured-placeholder-icon" aria-hidden="true">${ICONS.play(22, "currentColor")}</span>
          <span>Demo clip coming soon</span>
        </div>
      </div>
      <div class="nc-featured-clip-caption"><h3>NaijaClip example</h3><span>--</span></div>
    </article>`).join("");
}

async function loadHomepageFeaturedClips() {
  const section = document.getElementById("homepage-featured-section");
  const container = document.getElementById("homepage-featured-clips");
  if (!section || !container) return;

  try {
    const response = await fetch("/api/homepage/featured-clips", { headers: { Accept: "application/json" } });
    if (!response.ok) throw new Error("Featured clips are unavailable");
    const clips = await response.json();
    if (!Array.isArray(clips) || clips.length === 0) {
      container.innerHTML = featuredClipPlaceholdersHTML();
      return;
    }

    container.innerHTML = clips.map((clip) => {
      const title = escapeFeaturedClipHTML(clip.title || "NaijaClip example");
      const videoUrl = escapeFeaturedClipHTML(clip.video_url);
      const thumbnailUrl = escapeFeaturedClipHTML(clip.thumbnail_url || "");
      const duration = Math.round(Number(clip.duration) || 0);
      return `<article class="nc-featured-clip-card">
        <div class="nc-featured-video-frame">
          <video controls preload="none" playsinline poster="${thumbnailUrl}" aria-label="${title}">
            <source src="${videoUrl}" type="video/mp4" />
          </video>
        </div>
        <div class="nc-featured-clip-caption"><h3>${title}</h3><span>${duration}s</span></div>
      </article>`;
    }).join("");
  } catch (_error) {
    container.innerHTML = featuredClipPlaceholdersHTML();
  }
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