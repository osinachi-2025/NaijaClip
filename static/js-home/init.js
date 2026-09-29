async function initializeHome() {
	renderHome();
	try {
		const response = await authFetch("/users/me");
		state.isAuthenticated = response.ok;
	} catch (_error) {
		state.isAuthenticated = false;
	} finally {
		state.authChecked = true;
		const accountNav = document.getElementById("home-account-nav");
		if (accountNav) {
			accountNav.textContent = state.isAuthenticated ? "Dashboard" : "Log in";
			accountNav.style.visibility = "visible";
		}
	}
}

initializeHome();
