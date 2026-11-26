// Logout: the service's POST /auth/logout (same site, so the browser sends the Origin it checks), then the service home.
document.addEventListener("click", async (e) => {
  if (!e.target.closest("[data-logout]")) return;
  try {
    await fetch("/auth/logout", { method: "POST", credentials: "same-origin" });
  } finally {
    location.href = "/";
  }
});
