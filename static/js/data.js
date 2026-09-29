/* ---------- Mock data ---------- */
const MOCK_VIDEOS = [
  { id: "v1", title: "How I Built My First Successful Business.mp4", duration: "34:12", date: "2 days ago", status: "Ready", clips: 6 },
  { id: "v2", title: "Inside a ₦250M Lagos Property.mp4", duration: "18:47", date: "5 days ago", status: "Ready", clips: 4 },
  { id: "v3", title: "What Nobody Tells You About Starting a Podcast.mp4", duration: "42:03", date: "1 week ago", status: "Processing", clips: 2 },
];

const MOCK_CLIPS = [
  { id: "c1", title: "Nobody tells you this about starting a business…", duration: "0:38" },
  { id: "c2", title: "How I turned ₦1M into my first business…", duration: "0:52" },
  { id: "c3", title: "This is the biggest mistake new property buyers make…", duration: "0:44" },
  { id: "c4", title: "Why most podcasts fail in their first year…", duration: "1:05" },
];

const NAV_ITEMS = [
  { key: "dashboard-overview", label: "Overview", icon: ICONS.grid },
  { key: "dashboard-videos", label: "Videos", icon: ICONS.film },
  { key: "dashboard-clips", label: "Clips", icon: ICONS.scissors },
  { key: "dashboard-upload", label: "Upload", icon: ICONS.upload },
  { key: "dashboard-usage", label: "Usage", icon: ICONS.chart },
  { key: "dashboard-billing", label: "Billing", icon: ICONS.card },
  { key: "dashboard-settings", label: "Settings", icon: ICONS.settings },
];
