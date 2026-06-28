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

  const infoModal = document.querySelector("[data-info-modal]");
  const infoTitle = document.querySelector("[data-info-modal-title]");
  const infoSummary = document.querySelector("[data-info-modal-summary]");
  const infoLinks = document.querySelector("[data-info-modal-links]");
  const infoClose = document.querySelector("[data-info-close]");
  const closeInfo = () => { if (infoModal) infoModal.hidden = true; };
  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-service-info]");
    if (!button || !infoModal) return;
    if (infoTitle) infoTitle.textContent = button.dataset.infoTitle || "Service info";
    if (infoSummary) infoSummary.textContent = button.dataset.infoSummary || "No additional information is available yet.";
    if (infoLinks) {
      infoLinks.innerHTML = "";
      try {
        JSON.parse(button.dataset.infoLinks || "[]").forEach((link) => {
          if (!link.label || !link.url) return;
          const anchor = document.createElement("a");
          anchor.className = "ghost-button";
          anchor.href = link.url;
          anchor.target = "_blank";
          anchor.rel = "noopener noreferrer";
          anchor.textContent = link.label;
          infoLinks.appendChild(anchor);
        });
      } catch (_error) {}
    }
    infoModal.hidden = false;
  });
  infoClose?.addEventListener("click", closeInfo);
  infoModal?.addEventListener("click", (event) => { if (event.target === infoModal) closeInfo(); });
  document.addEventListener("keydown", (event) => { if (event.key === "Escape" && infoModal && !infoModal.hidden) closeInfo(); });

  const markCopied = (button) => {
    button.classList.add("copied");
    window.setTimeout(() => button.classList.remove("copied"), 850);
  };
  const fallbackCopy = (value) => {
    const textarea = document.createElement("textarea");
    textarea.value = value;
    textarea.setAttribute("readonly", "");
    textarea.style.position = "fixed";
    textarea.style.left = "-9999px";
    textarea.style.top = "0";
    document.body.appendChild(textarea);
    textarea.select();
    textarea.setSelectionRange(0, textarea.value.length);
    const ok = document.execCommand("copy");
    textarea.remove();
    return ok;
  };
  const writeClipboard = async (value) => {
    if (navigator.clipboard && window.isSecureContext) {
      try {
        await navigator.clipboard.writeText(value);
        return true;
      } catch (_error) {}
    }
    return fallbackCopy(value);
  };
  const copyValue = async (button) => {
    const target = button.dataset.copyTarget ? document.querySelector(button.dataset.copyTarget) : null;
    const value = target ? target.textContent : button.dataset.copyText;
    if (!value) return false;
    return writeClipboard(value);
  };
  document.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-copy-text]");
    const targetButton = event.target.closest("[data-copy-target]");
    const copyButton = button || targetButton;
    if (!copyButton) return;
    if (await copyValue(copyButton)) markCopied(copyButton);
  });

  const downloadModal = document.querySelector("[data-download-modal]");
  const downloadCheckbox = document.querySelector("[data-download-unit-name]");
  const downloadLabel = document.querySelector("[data-download-label]");
  const downloadCancel = document.querySelector("[data-download-cancel]");
  const downloadSubmit = document.querySelector("[data-download-submit]");
  let pendingDownload = null;
  const closeDownload = () => { if (downloadModal) downloadModal.hidden = true; pendingDownload = null; };
  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-download-choice]");
    if (!button || !downloadModal) return;
    pendingDownload = button;
    if (downloadCheckbox) downloadCheckbox.checked = false;
    if (downloadLabel) {
      const unitName = button.dataset.downloadUnitNameText || "name.service";
      const backupName = button.dataset.downloadBackupNameText || "backupname";
      downloadLabel.textContent = `Download as ${unitName} instead of ${backupName}`;
    }
    downloadModal.hidden = false;
  });
  downloadCancel?.addEventListener("click", closeDownload);
  downloadModal?.addEventListener("click", (event) => { if (event.target === downloadModal) closeDownload(); });
  downloadSubmit?.addEventListener("click", () => {
    if (!pendingDownload) return;
    const url = downloadCheckbox?.checked ? pendingDownload.dataset.downloadUnitUrl : pendingDownload.dataset.downloadBackupUrl;
    closeDownload();
    if (url) window.location.href = url;
  });
  document.addEventListener("keydown", (event) => { if (event.key === "Escape" && downloadModal && !downloadModal.hidden) closeDownload(); });

  document.querySelectorAll("form[data-live-search]").forEach((form) => {
    const input = form.querySelector("input[name='q']");
    if (!input) return;
    let timer = null;
    const runSearch = async () => {
      const params = new URLSearchParams(new FormData(form));
      const target = `${form.dataset.fragmentUrl || form.action}?${params.toString()}`;
      const response = await fetch(target, { headers: { "X-Requested-With": "fetch" } });
      if (!response.ok) return;
      const doc = new DOMParser().parseFromString(await response.text(), "text/html");
      const nextStats = doc.querySelector("[data-services-stats]");
      const nextTable = doc.querySelector("[data-services-table]");
      if (nextStats) document.querySelector("[data-services-stats]")?.replaceWith(nextStats);
      if (nextTable) document.querySelector("[data-services-table]")?.replaceWith(nextTable);
      const pageUrl = new URL(window.location.href);
      for (const [key, value] of params.entries()) {
        const clean = value.trim();
        if (clean) pageUrl.searchParams.set(key, clean);
        else pageUrl.searchParams.delete(key);
      }
      window.history.replaceState({}, "", pageUrl);
    };
    input.addEventListener("input", () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(runSearch, 350);
    });
    form.querySelectorAll("select").forEach((select) => {
      select.addEventListener("change", runSearch);
    });
  });

  document.querySelectorAll("form[data-auto-submit]").forEach((form) => {
    form.querySelectorAll("select,input[type='checkbox']").forEach((control) => {
      control.addEventListener("change", () => form.requestSubmit());
    });
  });

  const logPanel = document.querySelector("[data-log-panel]");
  if (logPanel?.dataset.refreshEnabled === "true") {
    const seconds = Number.parseInt(logPanel.dataset.refreshInterval || "5", 10);
    const interval = Number.isFinite(seconds) && seconds > 0 ? seconds * 1000 : 5000;
    const refreshLogs = async () => {
      const params = new URLSearchParams(window.location.search);
      const target = `${logPanel.dataset.logUrl}?lines=${encodeURIComponent(params.get("lines") || "200")}`;
      const response = await fetch(target, { headers: { "X-Requested-With": "fetch" } });
      if (!response.ok) return;
      const doc = new DOMParser().parseFromString(await response.text(), "text/html");
      const nextLog = doc.querySelector("[data-log-output]");
      if (nextLog) document.querySelector("[data-log-output]")?.replaceWith(nextLog);
    };
    window.setInterval(refreshLogs, interval);
  }
})();
