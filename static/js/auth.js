function clearLegacyAuthentication() {
  localStorage.removeItem("naijaclip_access_token");
  localStorage.removeItem("naijaclip_refresh_token");
  localStorage.removeItem("naijaclip_user_email");
}

async function authFetch(url, options = {}) {
  const legacyAccessToken = localStorage.getItem("naijaclip_access_token");
  const makeRequest = (includeLegacyToken) => {
    const headers = new Headers(options.headers || {});
    if (includeLegacyToken && legacyAccessToken && !headers.has("Authorization")) {
      headers.set("Authorization", `Bearer ${legacyAccessToken}`);
    }
    return fetch(url, { ...options, headers, credentials: "include" });
  };

  let response = await makeRequest(true);
  if (response.status === 401) {
    const legacyRefreshToken = localStorage.getItem("naijaclip_refresh_token");
    if (legacyRefreshToken) {
      try {
        const refreshResponse = await fetch("/auth/refresh", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "include",
          body: JSON.stringify({ refresh_token: legacyRefreshToken }),
        });
        if (refreshResponse.ok) {
          clearLegacyAuthentication();
          response = await makeRequest(false);
        } else {
          clearLegacyAuthentication();
        }
      } catch (_error) {
        clearLegacyAuthentication();
      }
    } else if (legacyAccessToken) {
      clearLegacyAuthentication();
    }
  }
  return response;
}

async function endAuthenticationSession() {
  try {
    await fetch("/auth/logout", { method: "POST", credentials: "include" });
  } finally {
    clearLegacyAuthentication();
  }
}