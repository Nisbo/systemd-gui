(function () {
  const key = "systemd-gui-theme";
  const root = document.documentElement;
  if (localStorage.getItem(key) === "dark") root.dataset.theme = "dark";
  const updateTheme = () => {
    const active = root.dataset.theme === "dark" ? "dark" : "light";
    document.querySelectorAll(".theme-option").forEach((button) => button.classList.toggle("active", button.dataset.themeChoice === active));
  };
  document.querySelectorAll(".theme-option").forEach((button) => {
    button.addEventListener("click", () => {
      if (button.dataset.themeChoice === "dark") { root.dataset.theme = "dark"; localStorage.setItem(key, "dark"); }
      else { delete root.dataset.theme; localStorage.setItem(key, "light"); }
      updateTheme();
    });
  });
  updateTheme();

  const modal = document.querySelector("[data-confirm-modal]");
  const message = document.querySelector("[data-confirm-message]");
  const cancel = document.querySelector("[data-confirm-cancel]");
  const submit = document.querySelector("[data-confirm-submit]");
  let pending = null;
  const close = () => { if (!modal) return; modal.hidden = true; pending = null; };
  document.querySelectorAll("form[data-confirm]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (form.dataset.confirmed === "true") { delete form.dataset.confirmed; return; }
      event.preventDefault(); pending = form; if (message) message.textContent = form.dataset.confirm || "Continue?"; if (modal) modal.hidden = false;
    });
  });
  cancel?.addEventListener("click", close);
  modal?.addEventListener("click", (event) => { if (event.target === modal) close(); });
  submit?.addEventListener("click", () => { if (!pending) return; pending.dataset.confirmed = "true"; pending.requestSubmit(); });
  document.addEventListener("keydown", (event) => { if (event.key === "Escape" && modal && !modal.hidden) close(); });
})();
