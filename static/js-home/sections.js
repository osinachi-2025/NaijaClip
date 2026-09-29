/* ---------- Extra landing sections ---------- */
function statsHTML() {
  const s = [["12,400+", "clips exported"], ["3,100", "creators clipping"], ["4 min", "average processing time per hour of video"], ["9 in 10", "clips posted without edits"]];
  return `<section class="nc-lx-stats"><div class="nc-lx-stats-inner">${s
    .map((x) => `<div><div class="nc-display nc-lx-stat-n">${x[0]}</div><div class="nc-lx-stat-l">${x[1]}</div></div>`)
    .join("")}</div></section>`;
}

function featuresHTML() {
  const f = [
    [ICONS.scissors, "Finds the strong moments", "Hooks, hot takes and complete stories are picked from the transcript, so clips start and end where a viewer expects."],
    [ICONS.film, "Captions that fit Nigerian speech", "Accurate captions for English, Pidgin and mixed-language talk, with the styles creators actually use."],
    [ICONS.maximize, "Vertical by default", "Auto-reframes to 9:16 and keeps the speaker in shot, ready for Reels, Shorts and TikTok."],
    [ICONS.settings, "Your brand on every clip", "Add your logo, colours and font once. Pro removes the NaijaClip watermark."],
    [ICONS.upload, "Big files welcome", "Upload up to 5 GB, or a full two-hour sermon, and leave the tab. We'll notify you when clips are ready."],
    [ICONS.card, "Pay in naira", "Simple monthly or yearly plans in ₦. No dollar cards, no surprise conversion fees."],
  ];
  return `<section class="nc-section" id="features">
    <div class="nc-lx-head"><h2 class="nc-display">Everything between upload and post</h2><p>You bring the recording. NaijaClip handles finding, cutting, framing and captioning.</p></div>
    <div class="nc-lx-features">${f
      .map((x) => `<div class="nc-lx-feature"><div class="nc-lx-ficon">${x[0]}</div><h3>${x[1]}</h3><p>${x[2]}</p></div>`)
      .join("")}</div></section>`;
}

function testimonialsHTML() {
  const t = [
    ["I used to spend Sundays cutting the week's podcast by hand. Now I review five clips over breakfast and schedule them.", "Tobenna A.", "Podcast host, Enugu"],
    ["Our property tours used to sit on YouTube with 300 views. The clips bring in real enquiries on WhatsApp.", "Halima B.", "Real estate agent, Abuja"],
    ["The church media team posts sermon highlights the same day now. Captions handle Yoruba and English switching well.", "Pastor Kunle O.", "Media lead, Lagos"],
  ];
  return `<section class="nc-section nc-lx-quotes"><div class="nc-lx-head"><h2 class="nc-display">Creators who stopped cutting by hand</h2></div>
    <div class="nc-lx-quote-grid">${t
      .map((x) => `<figure class="nc-lx-quote"><blockquote>${x[0]}</blockquote><figcaption><b>${x[1]}</b>${x[2]}</figcaption></figure>`)
      .join("")}</div></section>`;
}

function faqHTML() {
  const q = [
    ["What kind of videos work best?", "Anything long and spoken: podcasts, interviews, sermons, classes and property walkthroughs. Music videos and silent footage give weaker results."],
    ["Which languages are supported?", "English and Nigerian Pidgin today, with Yoruba, Igbo and Hausa in progress. Mixed-language videos are handled, and you can edit any caption before export."],
    ["How long does processing take?", "Roughly 4 minutes per hour of video. You can close the page; we'll notify you when clips are ready."],
    ["Can I edit the clips before posting?", "Yes. Trim start and end points, change caption style, and swap the reframing before you export."],
    ["What does the free plan include?", "3 videos a month, 720p exports, AI clip detection and captions, with a NaijaClip watermark. No card needed."],
    ["How do I pay, and can I cancel?", "Pay by card or bank transfer in naira. Cancel any time from Billing; you keep Pro until the period ends."],
  ];
  return `<section class="nc-section" id="faq"><div class="nc-lx-head"><h2 class="nc-display">Questions, answered</h2></div>
    <div class="nc-lx-faq">${q
      .map((x) => `<div class="nc-faq-item"><button class="nc-faq-q nc-focus" data-faq="1" aria-expanded="false">${x[0]}<span class="nc-faq-plus"></span></button><div class="nc-faq-a"><p>${x[1]}</p></div></div>`)
      .join("")}</div></section>`;
}

function footerHTML() {
  const col = (t, items) => `<div class="nc-lx-fcol"><div class="nc-lx-ftitle">${t}</div>${items.map((i) => `<button class="nc-lx-flink nc-focus" ${i[1]}>${i[0]}</button>`).join("")}</div>`;
  return `<footer class="nc-footer"><div class="nc-lx-footer">
    <div class="nc-lx-fbrand">${logoHTML()}<p>Turn long videos into short clips, captioned and ready to post.</p></div>
    ${col("Product", [["Features", 'data-scroll="features"'], ["How it works", 'data-scroll="how"'], ["Pricing", 'data-scroll="pricing"'], ["Upload a video", 'data-nav="dashboard-upload"']])}
    ${col("Support", [["FAQ", 'data-scroll="faq"'], ["Contact us", 'data-toast="hello@naijaclip.com"'], ["Help centre", 'data-toast="Help centre coming soon"']])}
    ${col("Legal", [["Privacy policy", 'data-toast="Privacy policy coming soon"'], ["Terms of service", 'data-toast="Terms coming soon"']])}
  </div><div class="nc-lx-fbottom"><span class="nc-footer-copy">© 2026 NaijaClip. Built for creators.</span><span class="nc-footer-copy">Made in Nigeria</span></div></footer>`;
}