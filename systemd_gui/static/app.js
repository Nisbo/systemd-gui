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
  const extra = document.querySelector("[data-confirm-extra]");
  const cancel = document.querySelector("[data-confirm-cancel]");
  const submit = document.querySelector("[data-confirm-submit]");
  let pending = null;
  const close = () => { if (!modal) return; modal.hidden = true; pending = null; if (extra) extra.innerHTML = ""; };
  const renderConfirmExtra = (form) => {
    if (!extra) return;
    extra.innerHTML = "";
    if (!form.dataset.confirmCheckboxName) return;
    const label = document.createElement("label");
    label.className = "toggle-label confirm-extra-toggle";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.name = form.dataset.confirmCheckboxName;
    checkbox.value = form.dataset.confirmCheckboxValue || "1";
    checkbox.checked = form.dataset.confirmCheckboxChecked === "true";
    checkbox.dataset.confirmExtraCheckbox = "true";
    const text = document.createElement("span");
    text.textContent = form.dataset.confirmCheckboxLabel || "Confirm option";
    label.append(checkbox, text);
    extra.appendChild(label);
  };
  document.querySelectorAll("form[data-confirm]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (form.dataset.confirmed === "true") { delete form.dataset.confirmed; return; }
      event.preventDefault(); pending = form; if (message) message.textContent = form.dataset.confirm || "Continue?"; renderConfirmExtra(form); if (modal) modal.hidden = false;
    });
  });
  cancel?.addEventListener("click", close);
  modal?.addEventListener("click", (event) => { if (event.target === modal) close(); });
  submit?.addEventListener("click", () => {
    if (!pending) return;
    pending.querySelectorAll("[data-confirm-extra-field]").forEach((field) => field.remove());
    extra?.querySelectorAll("[data-confirm-extra-checkbox]").forEach((checkbox) => {
      if (!checkbox.checked) return;
      const hidden = document.createElement("input");
      hidden.type = "hidden";
      hidden.name = checkbox.name;
      hidden.value = checkbox.value;
      hidden.dataset.confirmExtraField = "true";
      pending.appendChild(hidden);
    });
    pending.dataset.confirmed = "true";
    pending.requestSubmit();
  });
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

  document.addEventListener("click", (event) => {
    const link = event.target.closest("[data-log-window]");
    if (!link) return;
    event.preventDefault();
    const width = Math.min(1180, Math.max(860, Math.round(window.screen.availWidth * 0.72)));
    const height = Math.min(900, Math.max(640, Math.round(window.screen.availHeight * 0.78)));
    const left = Math.max(0, Math.round((window.screen.availWidth - width) / 2));
    const top = Math.max(0, Math.round((window.screen.availHeight - height) / 2));
    const features = `popup=yes,width=${width},height=${height},left=${left},top=${top},resizable=yes,scrollbars=yes`;
    const popup = window.open(link.href, "systemdGuiLogWindow", features);
    if (popup) popup.focus();
    else window.open(link.href, "_blank", "noopener,noreferrer");
  });

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
  if (logPanel) {
    const logControls = document.querySelector("[data-log-controls]");
    const linesSelect = logControls?.querySelector("[data-log-lines]");
    const refreshCheckbox = logControls?.querySelector("[data-log-refresh]");
    const intervalSelect = logControls?.querySelector("[data-log-interval]");
    const searchInput = logControls?.querySelector("[data-log-search]");
    const refreshNow = logControls?.querySelector("[data-log-refresh-now]");
    const searchStatus = document.querySelector("[data-log-search-status]");
    const refreshPaused = document.querySelector("[data-log-refresh-paused]");
    const lineCountLabel = document.querySelector("[data-log-line-count]");
    let timer = null;
    let loading = false;
    let searchTimer = null;

    const selectedLines = () => linesSelect?.value || "200";
    const selectedSearch = () => searchInput?.value.trim() || "";
    const selectedInterval = () => {
      const seconds = Number.parseInt(intervalSelect?.value || logPanel.dataset.refreshInterval || "5", 10);
      return Number.isFinite(seconds) && seconds > 0 ? seconds : 5;
    };
    const refreshEnabled = () => Boolean(refreshCheckbox?.checked);
    const syncLogUrl = () => {
      const url = new URL(window.location.href);
      if (url.pathname.indexOf("/logs") === -1) url.searchParams.set("tab", "logs");
      url.searchParams.set("lines", selectedLines());
      if (refreshEnabled()) {
        url.searchParams.set("refresh", "1");
        url.searchParams.set("interval", String(selectedInterval()));
      } else {
        url.searchParams.delete("refresh");
        url.searchParams.delete("interval");
      }
      if (selectedSearch()) url.searchParams.set("log_q", selectedSearch());
      else url.searchParams.delete("log_q");
      window.history.replaceState({}, "", url);
    };
    const updateLineCountLabel = () => {
      if (lineCountLabel) lineCountLabel.textContent = selectedLines();
    };
    const escapeRegex = (value) => value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const appendHighlightedText = (fragment, text, query) => {
      if (!query) {
        fragment.appendChild(document.createTextNode(text));
        return;
      }
      const regex = new RegExp(escapeRegex(query), "gi");
      let cursor = 0;
      for (const match of text.matchAll(regex)) {
        if (match.index > cursor) fragment.appendChild(document.createTextNode(text.slice(cursor, match.index)));
        const mark = document.createElement("mark");
        mark.textContent = match[0];
        fragment.appendChild(mark);
        cursor = match.index + match[0].length;
      }
      if (cursor < text.length) fragment.appendChild(document.createTextNode(text.slice(cursor)));
    };
    const renderLogText = (rawText) => {
      const output = document.querySelector("[data-log-output]");
      const code = output?.querySelector("code");
      if (!output || !code) return;
      const query = selectedSearch();
      output.dataset.rawLog = rawText;
      code.textContent = "";
      const fragment = document.createDocumentFragment();
      const lines = rawText.split("\n");
      const matchingLines = query ? lines.filter((line) => line.toLowerCase().includes(query.toLowerCase())) : lines;
      if (query && matchingLines.length === 0) {
        fragment.appendChild(document.createTextNode("No loaded log lines match this search."));
      } else {
        matchingLines.forEach((line, index) => {
          if (index > 0) fragment.appendChild(document.createTextNode("\n"));
          appendHighlightedText(fragment, line, query);
        });
      }
      code.appendChild(fragment);
      if (searchStatus) {
        searchStatus.hidden = !query;
        searchStatus.textContent = query ? `${matchingLines.length} matching line${matchingLines.length === 1 ? "" : "s"} in the loaded logs.` : "";
      }
    };
    const applyLogSearch = () => {
      const output = document.querySelector("[data-log-output]");
      const code = output?.querySelector("code");
      renderLogText(output?.dataset.rawLog ?? code?.textContent ?? "");
      syncLogUrl();
    };
    const hasActiveLogSelection = () => {
      const output = document.querySelector("[data-log-output]");
      const selection = window.getSelection();
      if (!output || !selection || selection.isCollapsed || selection.rangeCount === 0) return false;
      const range = selection.getRangeAt(0);
      return output.contains(range.commonAncestorContainer);
    };
    const updateRefreshPaused = () => {
      const paused = refreshEnabled() && hasActiveLogSelection();
      if (logControls) logControls.classList.toggle("refresh-paused", paused);
      if (refreshPaused) refreshPaused.hidden = !paused;
    };
    const refreshLogs = async ({ followBottom = true, skipWhenSelecting = false } = {}) => {
      if (loading) return;
      const currentLog = document.querySelector("[data-log-output]");
      updateRefreshPaused();
      if (skipWhenSelecting && hasActiveLogSelection()) return;
      const distanceFromBottom = currentLog ? currentLog.scrollHeight - currentLog.scrollTop - currentLog.clientHeight : 0;
      const wasNearBottom = distanceFromBottom < 32;
      const previousTop = currentLog?.scrollTop || 0;
      loading = true;
      refreshNow?.setAttribute("aria-busy", "true");
      try {
        const target = `${logPanel.dataset.logUrl}?lines=${encodeURIComponent(selectedLines())}`;
        const response = await fetch(target, { headers: { "X-Requested-With": "fetch" } });
        if (!response.ok) return;
        const doc = new DOMParser().parseFromString(await response.text(), "text/html");
        const nextLog = doc.querySelector("[data-log-output]");
        if (!nextLog || !currentLog) return;
        const nextCode = nextLog.querySelector("code");
        renderLogText(nextCode?.textContent || "");
        if (followBottom && wasNearBottom) {
          currentLog.scrollTop = currentLog.scrollHeight;
        } else {
          currentLog.scrollTop = Math.min(previousTop, currentLog.scrollHeight);
        }
      } finally {
        loading = false;
        refreshNow?.removeAttribute("aria-busy");
      }
    };
    const stopTimer = () => {
      if (timer) window.clearInterval(timer);
      timer = null;
      updateRefreshPaused();
    };
    const startTimer = () => {
      stopTimer();
      if (!refreshEnabled()) return;
      timer = window.setInterval(() => refreshLogs({ followBottom: true, skipWhenSelecting: true }), selectedInterval() * 1000);
    };
    const applyLogControls = ({ refresh = false } = {}) => {
      updateLineCountLabel();
      syncLogUrl();
      startTimer();
      if (refresh) refreshLogs({ followBottom: false });
    };

    linesSelect?.addEventListener("change", () => applyLogControls({ refresh: true }));
    refreshCheckbox?.addEventListener("change", () => applyLogControls({ refresh: refreshEnabled() }));
    intervalSelect?.addEventListener("change", () => applyLogControls());
    searchInput?.addEventListener("input", () => {
      window.clearTimeout(searchTimer);
      searchTimer = window.setTimeout(applyLogSearch, 120);
    });
    document.addEventListener("selectionchange", updateRefreshPaused);
    refreshNow?.addEventListener("click", () => {
      syncLogUrl();
      refreshLogs({ followBottom: false });
    });
    applyLogSearch();
    updateLineCountLabel();
    updateRefreshPaused();
    startTimer();
  }
})();
