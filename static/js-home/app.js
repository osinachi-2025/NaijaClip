function renderHome() {
	document.getElementById("app").innerHTML = landingHTML();
	loadHomepageFeaturedClips();
}
function goDashboard(page = "dashboard-overview") {
	const destination = page === "dashboard-overview"
		? "/dashboard"
		: `/dashboard?page=${encodeURIComponent(page)}`;
	window.location.href = destination;
}
