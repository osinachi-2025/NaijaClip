/* ---------- Small shared partials ---------- */
function logoHTML() {
  return `
    <a class="nc-logo nc-focus" href="/" aria-label="NaijaClip homepage">
      <span class="nc-logo-mark">${ICONS.scissors}</span>
      <span class="nc-logo-text nc-display">Naija<span>Clip</span></span>
    </a>`;
}

function clipStackHTML() {
  return `
  <div class="nc-clipstack nc-rise">
    <div class="nc-timeline-card">
      <div class="nc-timeline-head">
        <span>Inside a ₦250M Lagos Property.mp4</span><span>18:47</span>
      </div>
      <div class="nc-timeline-track">
        <div class="nc-timeline-mark" style="left:12%;width:9%"></div>
        <div class="nc-timeline-mark" style="left:44%;width:7%"></div>
        <div class="nc-timeline-mark" style="left:76%;width:11%"></div>
      </div>
      <div class="nc-timeline-note">3 moments found</div>
    </div>
    <div class="nc-clip-row">
      ${[
        { label: "The biggest mistake buyers make", rotate: -3, ty: 0, dark: "#1F2327" },
        { label: "₦250M and worth every naira", rotate: 0, ty: 10, dark: "var(--accent-dark)" },
        { label: "What the agent won't tell you", rotate: 3, ty: -6, dark: "#1F2327" },
      ]
        .map(
          (c) => `
        <div class="nc-clip-card" style="transform: translateY(${c.ty}px) rotate(${c.rotate}deg)">
          <div class="nc-clip-grad" style="background: linear-gradient(160deg, ${c.dark}, var(--ink))"></div>
          <div class="nc-clip-bar"></div>
          <div class="nc-clip-label">${c.label}</div>
          <div class="nc-clip-play">${ICONS.play(11)}</div>
        </div>`
        )
        .join("")}
    </div>
  </div>`;
}
