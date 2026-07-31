(function () {
  const key = "systemd-gui-theme";
  const root = document.documentElement;
  if (localStorage.getItem(key) === "dark") root.dataset.theme = "dark";
  const updateTheme = () => {
    const isDark = root.dataset.theme === "dark";
    const toggle = document.querySelector("[data-theme-toggle]");
    const label = isDark ? "Switch to light theme" : "Switch to dark theme";
    if (toggle) {
      toggle.setAttribute("aria-label", label);
      toggle.title = label;
    }
    document.querySelectorAll("[data-theme-icon]").forEach((icon) => {
      icon.hidden = icon.dataset.themeIcon !== (isDark ? "light" : "dark");
    });
  };
  document.querySelector("[data-theme-toggle]")?.addEventListener("click", () => {
    if (root.dataset.theme === "dark") { delete root.dataset.theme; localStorage.setItem(key, "light"); }
    else { root.dataset.theme = "dark"; localStorage.setItem(key, "dark"); }
    updateTheme();
  });
  updateTheme();

  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-scope-select-all]");
    if (!button) return;
    const group = button.closest("[data-scope-group]");
    if (!group) return;
    group.querySelectorAll('input[type="checkbox"][name="scopes"]:not(:disabled)').forEach((checkbox) => {
      checkbox.checked = true;
    });
  });

  const closeNodeSwitchers = () => {
    document.querySelectorAll(".node-switcher[open]").forEach((details) => { details.open = false; });
  };
  document.addEventListener("click", (event) => {
    if (event.target.closest(".node-switcher")) return;
    closeNodeSwitchers();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeNodeSwitchers();
  });

  const replaceRemoteDockerRow = (row, html) => {
    const template = document.createElement("template");
    template.innerHTML = html.trim();
    const replacement = template.content.firstElementChild;
    if (replacement) {
      row.replaceWith(replacement);
      initDockerView(replacement);
      applyDockerDefaults(replacement);
    }
  };
  const loadRemoteDockerRows = (rootNode = document) => {
    rootNode.querySelectorAll("[data-remote-docker-node-url]").forEach((row) => {
      const url = row.dataset.remoteDockerNodeUrl;
      if (!url) return;
      fetch(url, { headers: { "X-Requested-With": "fetch" } })
        .then((response) => {
          if (!response.ok) throw new Error(`HTTP ${response.status}`);
          return response.text();
        })
        .then((html) => replaceRemoteDockerRow(row, html))
        .catch((error) => {
          const message = row.querySelector(".remote-docker-title .muted:last-child");
          if (message) message.textContent = `Remote Docker overview could not be loaded: ${String(error.message || error)}`;
          const tag = row.querySelector(".tag");
          if (tag) {
            tag.classList.remove("neutral");
            tag.classList.add("danger");
            tag.textContent = "error";
          }
        });
    });
  };

  document.querySelectorAll("[data-remote-docker-url]").forEach((target) => {
    const url = target.dataset.remoteDockerUrl;
    if (!url) return;
    fetch(url, { headers: { "X-Requested-With": "fetch" } })
      .then((response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.text();
      })
      .then((html) => {
        target.innerHTML = html;
        initDockerView(target);
        applyDockerDefaults(target);
        loadRemoteDockerRows(target);
      })
      .catch((error) => {
        target.replaceChildren();
        const message = document.createElement("p");
        message.className = "empty-note remote-docker-loading";
        message.textContent = `Remote Docker overview could not be loaded: ${String(error.message || error)}`;
        target.append(message);
      });
  });

  const dockerSettingsKey = "systemd-gui-docker-view";
  const dockerDefaults = { collapseRemoteNodes: false, collapseLocalCompose: false, collapseRemoteCompose: false };
  const readDockerSettings = () => {
    try {
      const stored = JSON.parse(window.localStorage.getItem(dockerSettingsKey) || "{}");
      if (Object.prototype.hasOwnProperty.call(stored, "collapseCompose")) {
        stored.collapseLocalCompose = Boolean(stored.collapseCompose);
        stored.collapseRemoteCompose = Boolean(stored.collapseCompose);
        delete stored.collapseCompose;
      }
      return { ...dockerDefaults, ...stored };
    } catch {
      return { ...dockerDefaults };
    }
  };
  const writeDockerSettings = (settings) => {
    window.localStorage.setItem(dockerSettingsKey, JSON.stringify({ ...dockerDefaults, ...settings }));
  };
  const setComposeGroupCollapsed = (header, collapsed) => {
    const composeId = header.dataset.composeId;
    if (!composeId) return;
    header.dataset.collapsed = collapsed ? "true" : "false";
    const button = header.querySelector("[data-docker-compose-toggle]");
    if (button) {
      button.textContent = collapsed ? "+" : "-";
      button.title = collapsed ? "Expand compose group" : "Collapse compose group";
      button.setAttribute("aria-label", button.title);
    }
    const table = header.closest("table") || document;
    table.querySelectorAll(`[data-compose-child="${CSS.escape(composeId)}"]`).forEach((row) => {
      row.hidden = collapsed;
    });
  };
  const setComposeGroupsCollapsed = (rootNode, collapsed) => {
    rootNode.querySelectorAll("[data-docker-compose-header]").forEach((header) => setComposeGroupCollapsed(header, collapsed));
  };
  const dockerComposeScopeRoot = (scope) => {
    if (scope === "local") return document.querySelector(".docker-table") || document;
    if (scope === "remote") return document.querySelector(".remote-docker-table") || document;
    return document;
  };
  const initDockerSettingsControls = () => {
    const settings = readDockerSettings();
    document.querySelectorAll("[data-docker-setting]").forEach((input) => {
      const name = input.dataset.dockerSetting;
      if (!name) return;
      input.checked = Boolean(settings[name]);
      if (input.dataset.dockerSettingBound === "true") return;
      input.dataset.dockerSettingBound = "true";
      input.addEventListener("change", () => {
        const next = { ...readDockerSettings(), [name]: input.checked };
        writeDockerSettings(next);
      });
    });
  };
  const applyDockerDefaults = (rootNode = document) => {
    const settings = readDockerSettings();
    rootNode.querySelectorAll(".remote-docker-details").forEach((details) => { details.open = !settings.collapseRemoteNodes; });
    rootNode.querySelectorAll("[data-docker-compose-header]").forEach((header) => {
      const isRemote = Boolean(header.closest(".remote-docker-table"));
      setComposeGroupCollapsed(header, isRemote ? settings.collapseRemoteCompose : settings.collapseLocalCompose);
    });
  };
  const initDockerView = (rootNode = document) => {
    initDockerSettingsControls();
    rootNode.querySelectorAll("[data-docker-node-action]").forEach((button) => {
      if (button.dataset.dockerActionBound === "true") return;
      button.dataset.dockerActionBound = "true";
      button.addEventListener("click", () => {
        const open = button.dataset.dockerNodeAction === "expand-all";
        document.querySelectorAll(".remote-docker-details").forEach((details) => { details.open = open; });
      });
    });
    rootNode.querySelectorAll("[data-docker-compose-action]").forEach((button) => {
      if (button.dataset.dockerActionBound === "true") return;
      button.dataset.dockerActionBound = "true";
      button.addEventListener("click", () => {
        setComposeGroupsCollapsed(dockerComposeScopeRoot(button.dataset.dockerComposeScope), button.dataset.dockerComposeAction === "collapse-all");
      });
    });
    rootNode.querySelectorAll("[data-docker-node-compose-action]").forEach((button) => {
      if (button.dataset.dockerActionBound === "true") return;
      button.dataset.dockerActionBound = "true";
      button.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        const details = button.closest(".remote-docker-details");
        if (!details) return;
        setComposeGroupsCollapsed(details, button.dataset.dockerNodeComposeAction === "collapse");
      });
    });
    rootNode.querySelectorAll("[data-docker-compose-toggle]").forEach((button) => {
      if (button.dataset.dockerActionBound === "true") return;
      button.dataset.dockerActionBound = "true";
      button.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        const header = button.closest("[data-docker-compose-header]");
        if (!header) return;
        setComposeGroupCollapsed(header, header.dataset.collapsed !== "true");
      });
    });
  };
  initDockerView(document);
  applyDockerDefaults(document);

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

  const quickShellAddModal = document.querySelector("[data-quick-shell-add-modal]");
  const quickShellAddTabs = document.querySelectorAll("[data-quick-shell-add-tab]");
  const quickShellAddPanels = document.querySelectorAll("[data-quick-shell-add-panel]");
  const closeQuickShellModals = () => {
    document.querySelectorAll("[data-quick-shell-add-modal],[data-quick-shell-edit-modal]").forEach((modalNode) => { modalNode.hidden = true; });
  };
  const setQuickShellAddType = (type) => {
    const cleanType = ["command", "category", "sequence"].includes(type) ? type : "command";
    quickShellAddTabs.forEach((tab) => tab.classList.toggle("active", tab.dataset.quickShellAddTab === cleanType));
    quickShellAddPanels.forEach((panel) => {
      const active = panel.dataset.quickShellAddPanel === cleanType;
      panel.hidden = !active;
      panel.querySelectorAll("input,select,textarea,button").forEach((control) => { control.disabled = !active; });
    });
  };
  setQuickShellAddType("command");
  quickShellAddTabs.forEach((tab) => {
    tab.addEventListener("click", () => setQuickShellAddType(tab.dataset.quickShellAddTab));
  });
  document.addEventListener("click", (event) => {
    const addButton = event.target.closest("[data-quick-shell-add-open]");
    if (addButton && quickShellAddModal) {
      const parentPath = addButton.dataset.parentPath || "";
      document.querySelectorAll("[data-quick-shell-parent-select]").forEach((select) => { select.value = parentPath; });
      setQuickShellAddType(addButton.dataset.entryType || "command");
      quickShellAddModal.hidden = false;
      quickShellAddModal.querySelector("input[name='name']")?.focus();
      return;
    }
    const editButton = event.target.closest("[data-quick-shell-edit-open]");
    if (editButton) {
      const target = editButton.dataset.target ? document.querySelector(editButton.dataset.target) : null;
      if (target) {
        target.hidden = false;
        target.querySelector("input[name='name']")?.focus();
      }
      return;
    }
    if (event.target.closest("[data-quick-shell-modal-close]")) {
      closeQuickShellModals();
      return;
    }
    const shellModal = event.target.closest("[data-quick-shell-add-modal],[data-quick-shell-edit-modal]");
    if (shellModal && event.target === shellModal) closeQuickShellModals();
  });
  document.addEventListener("keydown", (event) => { if (event.key === "Escape") closeQuickShellModals(); });

  const closeNodeModals = () => {
    document.querySelectorAll("[data-node-edit-modal]").forEach((modalNode) => { modalNode.hidden = true; });
  };
  document.addEventListener("click", (event) => {
    const editButton = event.target.closest("[data-node-edit-open]");
    if (editButton) {
      const target = editButton.dataset.target ? document.querySelector(editButton.dataset.target) : null;
      if (target) {
        target.hidden = false;
        target.querySelector("input[name='name']")?.focus();
      }
      return;
    }
    if (event.target.closest("[data-node-modal-close]")) {
      closeNodeModals();
      return;
    }
    const nodeModal = event.target.closest("[data-node-edit-modal]");
    if (nodeModal && event.target === nodeModal) closeNodeModals();
  });
  document.addEventListener("keydown", (event) => { if (event.key === "Escape") closeNodeModals(); });

  const closeApiTokenModals = () => {
    document.querySelectorAll("[data-api-token-modal]").forEach((modalNode) => { modalNode.hidden = true; });
  };
  document.addEventListener("click", (event) => {
    const editButton = event.target.closest("[data-api-token-edit-open]");
    if (editButton) {
      const target = editButton.dataset.target ? document.querySelector(editButton.dataset.target) : null;
      if (target) {
        target.hidden = false;
        target.querySelector("input[name='name']")?.focus();
      }
      return;
    }
    if (event.target.closest("[data-api-token-modal-close]")) {
      closeApiTokenModals();
      return;
    }
    const tokenModal = event.target.closest("[data-api-token-modal]");
    if (tokenModal && event.target === tokenModal) closeApiTokenModals();
  });
  document.addEventListener("keydown", (event) => { if (event.key === "Escape") closeApiTokenModals(); });

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
    form.querySelectorAll("input[name='position']").forEach((control) => {
      control.addEventListener("change", () => form.requestSubmit());
      control.addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
          event.preventDefault();
          form.requestSubmit();
        }
      });
    });
  });

  document.querySelectorAll("select[data-quick-shell-type]").forEach((select) => {
    const form = select.closest("form");
    const syncQuickShellFields = () => {
      const isCommand = select.value === "command";
      form?.querySelectorAll(".quick-shell-command-field,.quick-shell-confirm-field").forEach((field) => {
        field.hidden = !isCommand;
      });
      form?.querySelectorAll(".quick-shell-command-field input").forEach((input) => {
        input.required = isCommand;
      });
    };
    select.addEventListener("change", syncQuickShellFields);
    syncQuickShellFields();
  });

  document.querySelectorAll("[data-quick-shell-import-form]").forEach((form) => {
    const modeSelect = form.querySelector("[data-import-mode-select]");
    const duplicateSelect = form.querySelector("[data-duplicate-mode-select]");
    const duplicateControl = form.querySelector("[data-duplicate-control]");
    const duplicateDisabledHelp = form.querySelector("[data-duplicate-mode-disabled]");
    const targetSelect = form.querySelector("[data-import-target-select]");
    const fileInput = form.querySelector("input[name='import_file']");
    const sourceRadios = Array.from(form.querySelectorAll("[data-import-source]"));
    const fileField = form.querySelector("[data-import-file-field]");
    const remoteField = form.querySelector("[data-import-remote-field]");
    const remoteSelect = form.querySelector("[data-import-remote-node]");
    const preview = form.querySelector("[data-import-preview]");
    const previewTitle = form.querySelector("[data-import-preview-title]");
    const previewSummary = form.querySelector("[data-import-preview-summary]");
    const previewList = form.querySelector("[data-import-preview-list]");
    const importSubmit = form.querySelector("[data-import-submit]");
    const currentDataNode = form.querySelector("[data-current-quick-shell]");
    const currentQuickShell = (() => {
      try {
        return JSON.parse(currentDataNode?.textContent || "{\"items\":[]}");
      } catch (_error) {
        return { items: [] };
      }
    })();
    let importPreviewPayload = null;
    let importPreviewReady = false;
    let remotePreviewController = null;
    const HANDLED_PREVIEW_IMPORT = { handled: true };

    const entryName = (entry) => String(entry?.name || entry?.command || "Unnamed entry");
    const entryType = (entry) => ["category", "sequence", "command"].includes(entry?.type) ? entry.type : "command";
    const cloneJson = (value) => JSON.parse(JSON.stringify(value));
    const pathParts = (path) => String(path || "").split(".").filter((part) => part !== "").map((part) => Number.parseInt(part, 10)).filter((part) => Number.isInteger(part));
    const pathFor = (parentPath, index) => parentPath === "" ? String(index) : `${parentPath}.${index}`;
    const pathsEqual = (left, right) => String(left || "") === String(right || "");
    const pathIsAncestor = (ancestor, path) => ancestor === "" || path === ancestor || path.startsWith(`${ancestor}.`);
    const pathIsDescendant = (path, ancestor) => ancestor !== "" && path.startsWith(`${ancestor}.`);
    const decorateExisting = (items, parentPath = "") => items.map((entry, index) => {
      const nextEntry = cloneJson(entry);
      const nextPath = pathFor(parentPath, index);
      nextEntry.__previewPath = nextPath;
      nextEntry.__previewState = "existing";
      if (entryType(nextEntry) === "category") nextEntry.items = decorateExisting(Array.isArray(nextEntry.items) ? nextEntry.items : [], nextPath);
      return nextEntry;
    });
    const decorateImported = (items) => items.map((entry) => {
      const nextEntry = cloneJson(entry);
      nextEntry.__previewState = "imported";
      if (entryType(nextEntry) === "category") nextEntry.items = decorateImported(Array.isArray(nextEntry.items) ? nextEntry.items : []);
      return nextEntry;
    });
    const childrenForPreviewPath = (rootItems, path) => {
      let items = rootItems;
      for (const part of pathParts(path)) {
        const entry = items[part];
        if (!entry || entryType(entry) !== "category") return null;
        items = Array.isArray(entry.items) ? entry.items : [];
      }
      return items;
    };
    const parentChildrenForPreviewPath = (rootItems, path) => {
      const parts = pathParts(path);
      const index = parts.pop();
      const parentPath = parts.join(".");
      const parentItems = childrenForPreviewPath(rootItems, parentPath);
      return { parentItems, index, parentPath };
    };
    const normalizePreviewItem = (entry) => {
      const type = entryType(entry);
      const normalized = {
        type,
        name: String(entry?.name || "").trim(),
        enabled: Boolean(entry?.enabled ?? true),
      };
      if (type === "category") {
        normalized.items = (Array.isArray(entry?.items) ? entry.items : []).map(normalizePreviewItem);
      } else if (type === "sequence") {
        normalized.commands = String(entry?.commands || "").trim();
        normalized.confirm = Boolean(entry?.confirm ?? true);
        normalized.confirm_each = Boolean(entry?.confirm_each ?? false);
        normalized.print_comments = Boolean(entry?.print_comments ?? true);
        normalized.stop_on_error = Boolean(entry?.stop_on_error ?? true);
        normalized.show_menu_after = Boolean(entry?.show_menu_after ?? false);
      } else {
        normalized.command = String(entry?.command || "").trim();
        normalized.confirm = Boolean(entry?.confirm ?? true);
        normalized.show_menu_after = Boolean(entry?.show_menu_after ?? false);
      }
      return normalized;
    };
    const itemKey = (entry) => JSON.stringify(normalizePreviewItem(entry));
    const itemLabelKey = (entry) => String(entry?.name || entryName(entry)).trim();
    const uniqueImportName = (name, targetItems) => {
      const base = name || "Imported entry";
      const names = new Set(targetItems.map((entry) => itemLabelKey(entry)));
      let candidate = `${base} (imported)`;
      let counter = 2;
      while (names.has(candidate)) {
        candidate = `${base} (imported ${counter})`;
        counter += 1;
      }
      return candidate;
    };
    const prepareImportItem = (item, targetItems, duplicateMode) => {
      const nextItem = cloneJson(item);
      const duplicateName = itemLabelKey(nextItem);
      const duplicateEntries = targetItems.filter((existing) => itemLabelKey(existing) === duplicateName);
      const markDuplicateEntries = () => {
        duplicateEntries.forEach((existing) => { existing.__previewDuplicate = true; });
        nextItem.__previewDuplicate = true;
      };
      if (duplicateMode === "keep_all") {
        if (duplicateEntries.length) markDuplicateEntries();
        return nextItem;
      }
      if (duplicateMode === "copy_conflicts") {
        if (duplicateEntries.length) {
          nextItem.name = uniqueImportName(itemLabelKey(nextItem), targetItems);
          nextItem.__previewRenamed = true;
        }
        return nextItem;
      }
      const identicalEntry = targetItems.find((existing) => itemKey(existing) === itemKey(nextItem));
      if (identicalEntry) {
        identicalEntry.__previewSkippedIdentical = true;
        return HANDLED_PREVIEW_IMPORT;
      }
      if (duplicateMode === "replace_conflicts" && duplicateEntries.length) {
        const conflictIndex = targetItems.indexOf(duplicateEntries[0]);
        const replacedItem = cloneJson(targetItems[conflictIndex]);
        replacedItem.__previewState = "removed";
        replacedItem.__previewWillBeReplaced = true;
        nextItem.__previewReplaced = true;
        nextItem.__previewReplacement = true;
        targetItems.splice(conflictIndex, 1, replacedItem, nextItem);
        return HANDLED_PREVIEW_IMPORT;
      }
      if (duplicateMode === "rename_conflicts" && targetItems.some((existing) => itemLabelKey(existing) === itemLabelKey(nextItem))) {
        nextItem.name = uniqueImportName(itemLabelKey(nextItem), targetItems);
        nextItem.__previewRenamed = true;
      }
      return nextItem;
    };
    const importPreviewItemIntoTarget = (item, targetItems, duplicateMode, mergeCategories) => {
      if (mergeCategories && entryType(item) === "category") {
        const existingCategory = targetItems.find((existing) => entryType(existing) === "category" && itemLabelKey(existing) === itemLabelKey(item));
        if (existingCategory) {
          if (duplicateMode !== "keep_all" && itemKey(existingCategory) === itemKey(item)) {
            existingCategory.__previewSkippedIdentical = true;
            return;
          }
          existingCategory.__previewMerged = true;
          const existingChildren = Array.isArray(existingCategory.items) ? existingCategory.items : [];
          existingCategory.items = existingChildren;
          (Array.isArray(item.items) ? item.items : []).forEach((child) => {
            importPreviewItemIntoTarget(child, existingChildren, duplicateMode, true);
          });
          return;
        }
      }
      const preparedItem = prepareImportItem(item, targetItems, duplicateMode);
      if (preparedItem === HANDLED_PREVIEW_IMPORT) return;
      targetItems.push(preparedItem);
    };
    const collectImportStats = (items) => {
      const stats = { total: 0, categories: 0, commands: 0, sequences: 0 };
      const walk = (entryList) => {
        entryList.forEach((entry) => {
          const type = entryType(entry);
          stats.total += 1;
          if (type === "category") {
            stats.categories += 1;
            walk(Array.isArray(entry.items) ? entry.items : []);
          } else if (type === "sequence") {
            stats.sequences += 1;
          } else {
            stats.commands += 1;
          }
        });
      };
      walk(items);
      return stats;
    };
    const parseImportItems = (payload) => {
      if (!payload || typeof payload !== "object" || Array.isArray(payload)) throw new Error("JSON must contain an object.");
      if (!Array.isArray(payload.items)) throw new Error("Import file does not contain an items list.");
      return payload.items.filter((item) => item && typeof item === "object");
    };
    const targetLabel = () => (targetSelect?.selectedOptions?.[0]?.textContent || "Root category").replace(/^[-\s]+/, "").trim() || "Root category";
    const plural = (count, word) => `${count} ${count === 1 ? word : (word.endsWith("y") ? `${word.slice(0, -1)}ies` : `${word}s`)}`;
    const applyPreviewImport = (items, mode, targetPath, duplicateMode) => {
      const tree = decorateExisting(Array.isArray(currentQuickShell.items) ? currentQuickShell.items : []);
      const importedItems = decorateImported(items);
      const markRemoved = (entry) => {
        const nextEntry = cloneJson(entry);
        nextEntry.__previewState = "removed";
        if (entryType(nextEntry) === "category") nextEntry.items = (Array.isArray(nextEntry.items) ? nextEntry.items : []).map(markRemoved);
        return nextEntry;
      };
      const removedItems = (entries) => entries.map(markRemoved);
      if (mode === "replace_all") {
        const removedRoot = {
          type: "category",
          name: "Current Quick Shell categories",
          items: [],
          __previewState: "removed",
          __previewNote: `${plural(tree.length, "top-level entry")} will be replaced`,
        };
        return [removedRoot, ...importedItems];
      }
      if (mode === "replace_selected_category") {
        const { parentItems, index } = parentChildrenForPreviewPath(tree, targetPath);
        if (!parentItems || index === undefined || !parentItems[index]) return tree;
        const oldTarget = markRemoved(parentItems[index]);
        oldTarget.__previewNote = "selected category will be replaced";
        const nextCategory = importedItems[0] ? cloneJson(importedItems[0]) : null;
        if (nextCategory) nextCategory.__previewNote = "new category from import file";
        parentItems.splice(index, 1, oldTarget, ...(nextCategory ? [nextCategory] : []));
        return tree;
      }
      const targetItems = childrenForPreviewPath(tree, targetPath);
      if (!targetItems) return tree;
      if (mode === "replace_target") {
        const removed = removedItems(targetItems);
        targetItems.splice(0, targetItems.length, ...removed, ...importedItems);
        return tree;
      }
      importedItems.forEach((item) => {
        importPreviewItemIntoTarget(item, targetItems, duplicateMode, mode === "add_to_target");
      });
      return tree;
    };
    const buildPreviewTree = (items, mode, targetPath, duplicateMode) => {
      if (!previewList) return;
      previewList.replaceChildren();
      const previewItems = applyPreviewImport(items, mode, targetPath, duplicateMode);
      let rendered = 0;
      const maxItems = 80;
      const addNode = (entry, depth) => {
        if (rendered >= maxItems) return;
        rendered += 1;
        const row = document.createElement("div");
        const type = entryType(entry);
        const state = entry.__previewState || "existing";
        const path = entry.__previewPath || "";
        const isTarget = state === "existing" && path !== "" && pathsEqual(path, targetPath);
        row.className = `import-preview-item ${type} ${state}${isTarget ? " target" : ""}${entry.__previewReplacement ? " replacement" : ""}`;
        row.style.setProperty("--depth", String(Math.min(depth, 4)));
        const tag = document.createElement("span");
        tag.className = "tag";
        tag.textContent = type;
        const label = document.createElement("strong");
        label.textContent = entryName(entry);
        row.append(tag, label);
        const addStatusChip = (text, extraClass = "") => {
          const status = document.createElement("span");
          status.className = `import-preview-state ${extraClass}`.trim();
          status.textContent = text;
          row.appendChild(status);
        };
        if (isTarget && state === "existing") addStatusChip("target");
        if (entry.__previewMerged) addStatusChip("merged", "merged");
        else if (state === "imported") addStatusChip(entry.__previewRenamed ? "imported + renamed" : (entry.__previewReplaced ? "replaces same name" : "imported"), entry.__previewRenamed || entry.__previewReplaced ? "warning" : "");
        else if (state === "removed") addStatusChip(entry.__previewWillBeReplaced ? "will be replaced" : "will be removed");
        else if (state === "skipped") addStatusChip("will be skipped");
        if (entry.__previewDuplicate) addStatusChip("duplicate", "warning");
        if (entry.__previewSkippedIdentical) addStatusChip("identical skipped");
        if (type === "command" && entry.command) {
          const code = document.createElement("code");
          code.textContent = entry.command;
          row.appendChild(code);
        } else if (type === "sequence") {
          const lineCount = String(entry.commands || "").split(/\r?\n/).filter((line) => line.trim() && !line.trim().startsWith("#")).length;
          const note = document.createElement("span");
          note.className = "empty-note";
          note.textContent = plural(lineCount, "line");
          row.appendChild(note);
        }
        if (entry.__previewNote) {
          const note = document.createElement("span");
          note.className = "empty-note";
          note.textContent = entry.__previewNote;
          row.appendChild(note);
        }
        previewList.appendChild(row);
        if (type === "category") {
          const childItems = Array.isArray(entry.items) ? entry.items : [];
          const shouldExpand = state !== "existing" || isTarget || entry.__previewMerged || pathIsAncestor(path, targetPath) || pathIsDescendant(path, targetPath);
          if (shouldExpand) {
            childItems.forEach((child) => addNode(child, depth + 1));
          } else if (childItems.length) {
            const collapsed = document.createElement("div");
            collapsed.className = "import-preview-collapsed";
            collapsed.style.setProperty("--depth", String(Math.min(depth + 1, 4)));
            collapsed.textContent = `${plural(childItems.length, "entry")} unchanged`;
            previewList.appendChild(collapsed);
          }
        }
      };
      previewItems.forEach((entry) => addNode(entry, 0));
      if (rendered >= maxItems) {
        const more = document.createElement("div");
        more.className = "import-preview-more";
        more.textContent = "Preview shortened. The full import is still handled by the server.";
        previewList.appendChild(more);
      }
    };
    const setPreviewState = (state, summary, items = [], mode = "add_to_target", targetPath = "", duplicateMode = "replace_conflicts") => {
      if (!preview || !previewTitle || !previewSummary) return;
      preview.hidden = false;
      preview.classList.remove("ok", "warning", "danger");
      preview.classList.add(state);
      previewTitle.textContent = state === "danger" ? "Import preview needs attention" : "Import preview";
      previewSummary.textContent = summary;
      buildPreviewTree(items, mode, targetPath, duplicateMode);
    };
    const importSource = () => sourceRadios.find((radio) => radio.checked)?.value || "file";
    const updateImportSubmit = (state) => {
      if (!importSubmit) return;
      const source = importSource();
      if (state === "ready") {
        importSubmit.disabled = false;
        importSubmit.textContent = source === "remote" ? "Import from node" : "Import file";
      } else if (state === "loading") {
        importSubmit.disabled = true;
        importSubmit.textContent = source === "remote" ? "Loading remote preview..." : "Loading import file...";
      } else if (state === "error") {
        importSubmit.disabled = true;
        importSubmit.textContent = source === "remote" ? "Choose available remote node" : "Choose valid import file";
      } else {
        importSubmit.disabled = true;
        importSubmit.textContent = source === "remote" ? "Load remote preview" : "Load file for import";
      }
    };
    const setImportPayload = (payload) => {
      parseImportItems(payload);
      importPreviewPayload = payload;
      importPreviewReady = true;
      updateImportSubmit("ready");
      syncImportPreview();
    };
    const loadRemotePreview = () => {
      const nodeId = remoteSelect?.value || "";
      importPreviewPayload = null;
      importPreviewReady = false;
      if (!nodeId || !form.dataset.remotePreviewUrl) {
        updateImportSubmit("empty");
        syncImportPreview();
        return;
      }
      if (remotePreviewController) remotePreviewController.abort();
      remotePreviewController = new AbortController();
      updateImportSubmit("loading");
      const url = new URL(form.dataset.remotePreviewUrl, window.location.origin);
      url.searchParams.set("node_id", nodeId);
      fetch(url, { signal: remotePreviewController.signal })
        .then((response) => response.json().then((payload) => ({ response, payload })))
        .then(({ response, payload }) => {
          if (!response.ok || !payload?.ok) throw new Error(payload?.error || `Remote node returned HTTP ${response.status}.`);
          setImportPayload(payload.payload);
        })
        .catch((error) => {
          if (error.name === "AbortError") return;
          importPreviewPayload = {};
          importPreviewReady = false;
          updateImportSubmit("error");
          setPreviewState("danger", error.message || "Could not load the remote Quick Shell export.");
        });
    };
    const syncImportPreview = () => {
      if (!preview || !previewSummary) return;
      if (!importPreviewPayload) {
        preview.hidden = true;
        return;
      }
      let items = [];
      try {
        items = parseImportItems(importPreviewPayload);
      } catch (error) {
        setPreviewState("danger", error.message || "This file cannot be previewed.");
        return;
      }
      const mode = modeSelect?.value || "add_to_target";
      const targetPath = targetSelect?.value || "";
      const duplicateMode = duplicateSelect?.value || "replace_conflicts";
      const target = targetLabel();
      const stats = collectImportStats(items);
      const countSummary = `${plural(items.length, "top-level entry")}; ${plural(stats.categories, "category")}, ${plural(stats.commands, "command")}, ${plural(stats.sequences, "sequence")} total.`;
      if (mode === "add_to_target") {
        setPreviewState("ok", `Will merge the imported entries into ${target}. Categories with the same name are combined; conflict handling is applied inside them. ${countSummary}`, items, mode, targetPath, duplicateMode);
      } else if (mode === "add_as_new") {
        setPreviewState("ok", `Will add imported top-level entries as new entries inside ${target}. Existing entries are not changed; duplicate names may be created. ${countSummary}`, items, mode, targetPath, "keep_all");
      } else if (mode === "add_as_copy") {
        setPreviewState("ok", `Will add imported top-level entries as separate copies inside ${target}. Same-name entries are renamed, for example APT (imported). ${countSummary}`, items, mode, targetPath, "copy_conflicts");
      } else if (mode === "replace_target") {
        setPreviewState("warning", `Will delete entries inside ${target}, then import this file there. Conflict handling is not used. ${countSummary}`, items, mode, targetPath, "keep_all");
      } else if (mode === "replace_selected_category") {
        if ((targetSelect?.value || "") === "") {
          setPreviewState("danger", "Choose a real category first. The Root category cannot be replaced with this mode.", items, mode, targetPath, duplicateMode);
        } else if (items.length !== 1 || entryType(items[0]) !== "category") {
          setPreviewState("danger", `This mode expects exactly one top-level category in the file. This file has ${plural(items.length, "top-level entry")}.`, items, mode, targetPath, duplicateMode);
        } else {
          setPreviewState("warning", `Will replace ${target} with the imported category ${entryName(items[0])}. Child entries inside the old category are deleted.`, items, mode, targetPath, duplicateMode);
        }
      } else if (mode === "replace_all") {
        setPreviewState("danger", `Will replace all Quick Shell categories with this file. Current entries outside the import are deleted. ${countSummary}`, items, mode, targetPath, duplicateMode);
      }
    };
    const syncImportHelp = () => {
      const duplicateApplies = (modeSelect?.value || "add_to_target") === "add_to_target";
      form.querySelectorAll("[data-import-mode-help]").forEach((node) => {
        node.hidden = node.dataset.importModeHelp !== modeSelect?.value;
      });
      form.querySelectorAll("[data-duplicate-mode-help]").forEach((node) => {
        node.hidden = !duplicateApplies || node.dataset.duplicateModeHelp !== duplicateSelect?.value;
      });
      if (duplicateSelect) duplicateSelect.disabled = !duplicateApplies;
      if (duplicateControl) duplicateControl.classList.toggle("disabled", !duplicateApplies);
      if (duplicateDisabledHelp) duplicateDisabledHelp.hidden = duplicateApplies;
      syncImportPreview();
    };
    const syncImportSource = () => {
      const source = importSource();
      const isRemote = source === "remote";
      if (form.dataset.fileAction && form.dataset.remoteAction) {
        form.action = isRemote ? form.dataset.remoteAction : form.dataset.fileAction;
      }
      if (fileField) fileField.hidden = isRemote;
      if (remoteField) remoteField.hidden = !isRemote;
      if (fileInput) fileInput.required = !isRemote;
      if (remoteSelect) remoteSelect.required = isRemote;
      form.querySelectorAll("[data-import-source-help]").forEach((node) => {
        node.hidden = node.dataset.importSourceHelp !== source;
      });
      importPreviewPayload = null;
      importPreviewReady = false;
      if (isRemote) {
        loadRemotePreview();
      } else {
        updateImportSubmit(fileInput?.files?.length ? "loading" : "empty");
        if (fileInput?.files?.length) fileInput.dispatchEvent(new Event("change"));
        else syncImportPreview();
      }
    };
    fileInput?.addEventListener("change", () => {
      if (importSource() !== "file") return;
      const file = fileInput.files?.[0];
      importPreviewPayload = null;
      importPreviewReady = false;
      if (!file) {
        updateImportSubmit("empty");
        syncImportPreview();
        return;
      }
      updateImportSubmit("loading");
      file.text().then((text) => {
        const payload = JSON.parse(text);
        setImportPayload(payload);
      }).catch((error) => {
        importPreviewPayload = {};
        importPreviewReady = false;
        updateImportSubmit("error");
        setPreviewState("danger", error instanceof SyntaxError ? "This file is not valid JSON." : (error.message || "Could not read this file."));
      });
    });
    modeSelect?.addEventListener("change", syncImportHelp);
    duplicateSelect?.addEventListener("change", syncImportHelp);
    targetSelect?.addEventListener("change", syncImportPreview);
    remoteSelect?.addEventListener("change", () => {
      if (importSource() === "remote") loadRemotePreview();
    });
    sourceRadios.forEach((radio) => radio.addEventListener("change", syncImportSource));
    form.addEventListener("submit", (event) => {
      if (!importPreviewReady) {
        event.preventDefault();
        if (importSource() === "remote") loadRemotePreview();
        else updateImportSubmit(fileInput?.files?.length ? "loading" : "empty");
      }
    });
    updateImportSubmit("empty");
    syncImportHelp();
    syncImportSource();
  });

  const logPanel = document.querySelector("[data-log-panel]");
  if (logPanel) {
    const logControls = document.querySelector("[data-log-controls]");
    const linesSelect = logControls?.querySelector("[data-log-lines]");
    const perNodeSelect = logControls?.querySelector("[data-log-per-node]");
    const timeSelect = logControls?.querySelector("[data-log-time]");
    const sinceInput = logControls?.querySelector("[data-log-since]");
    const untilInput = logControls?.querySelector("[data-log-until]");
    const prioritySelect = logControls?.querySelector("[data-log-priority]");
    const wrapCheckbox = logControls?.querySelector("[data-log-wrap]");
    const smallLinesCheckbox = logControls?.querySelector("[data-log-small]");
    const excludeSearchCheckbox = logControls?.querySelector("[data-log-exclude]");
    const intervalSelect = logControls?.querySelector("[data-log-interval]");
    const searchInput = logControls?.querySelector("[data-log-search]");
    const refreshNow = logControls?.querySelector("[data-log-refresh-now]");
    const logWindowLink = logControls?.querySelector("[data-log-window]");
    const saveSettingsButton = logPanel.querySelector("[data-log-save-settings]");
    const panelMaximizeButtons = logPanel.querySelectorAll("[data-log-maximize-panel]");
    const outputMaximizeButtons = logPanel.querySelectorAll("[data-log-maximize-output]");
    const logScrollButtons = logPanel.querySelectorAll("[data-log-scroll]");
    const searchStatus = document.querySelector("[data-log-search-status]");
    const refreshPaused = document.querySelector("[data-log-refresh-paused]");
    const lineCountLabel = document.querySelector("[data-log-line-count]");
    let timer = null;
    let loading = false;
    let searchTimer = null;

    const selectedLines = () => linesSelect?.value || "200";
    const selectedPerNode = () => perNodeSelect?.value || selectedLines();
    const selectedTime = () => timeSelect?.value || "all";
    const selectedSince = () => sinceInput?.value || "";
    const selectedUntil = () => untilInput?.value || "";
    const selectedPriority = () => prioritySelect?.value || "all";
    const selectedSmallLines = () => Boolean(smallLinesCheckbox?.checked);
    const selectedExcludeSearch = () => Boolean(excludeSearchCheckbox?.checked);
    const logSettingsKey = "systemd-gui-log-settings";
    const logUrlHasExplicitSettings = () => {
      const params = new URLSearchParams(window.location.search);
      return ["lines", "per_node", "time", "since", "until", "priority", "refresh", "interval", "log_q", "wrap", "small", "exclude", "node"].some((key) => params.has(key));
    };
    const readSavedLogSettings = () => {
      try {
        return JSON.parse(window.localStorage.getItem(logSettingsKey) || "null");
      } catch {
        return null;
      }
    };
    const syncMaximizeButtons = () => {
      const panelMaximized = logPanel.classList.contains("log-panel-maximized");
      const outputMaximized = logPanel.classList.contains("log-output-maximized");
      document.body.classList.toggle("log-maximized-page", panelMaximized || outputMaximized);
      panelMaximizeButtons.forEach((button) => {
        button.setAttribute("aria-pressed", String(panelMaximized));
        button.title = panelMaximized ? "Restore log panel" : "Maximize log panel";
        button.setAttribute("aria-label", button.title);
        button.classList.toggle("is-active", panelMaximized);
      });
      outputMaximizeButtons.forEach((button) => {
        button.setAttribute("aria-pressed", String(outputMaximized));
        button.title = outputMaximized ? "Restore log messages" : "Maximize log messages";
        button.setAttribute("aria-label", button.title);
        button.classList.toggle("is-active", outputMaximized);
      });
    };
    const togglePanelMaximized = () => {
      logPanel.classList.remove("log-output-maximized");
      logPanel.classList.toggle("log-panel-maximized");
      syncMaximizeButtons();
    };
    const toggleOutputMaximized = () => {
      logPanel.classList.toggle("log-output-maximized");
      syncMaximizeButtons();
    };
    const selectedNodes = () => {
      const values = [];
      logControls?.querySelectorAll("input[name='node']:checked").forEach((input) => values.push(input.value));
      return values.length ? values : ["local"];
    };
    const selectedSearch = () => searchInput?.value.trim() || "";
    const wrapEnabled = () => !wrapCheckbox || Boolean(wrapCheckbox.checked);
    const nodeColorClasses = new Map();
    const nodeLoadedCounts = new Map();
    const nodeLogStatuses = new Map();
    const nodeLogMessages = new Map();
    const captureNodeColors = (root) => {
      root?.querySelectorAll("[data-log-node][data-node-color]").forEach((line) => {
        const nodeName = line.dataset.logNode || "";
        const colorClass = line.dataset.nodeColor || "";
        if (nodeName && colorClass) nodeColorClasses.set(nodeName, colorClass);
      });
      root?.querySelectorAll("[data-log-node-meta]").forEach((meta) => {
        const nodeName = meta.dataset.logNodeLabel || "";
        const colorClass = meta.dataset.nodeColor || "";
        const loaded = Number.parseInt(meta.dataset.loadedCount || "0", 10);
        const status = meta.dataset.logStatus || "ok";
        const message = meta.dataset.logStatusMessage || "";
        if (nodeName && colorClass) nodeColorClasses.set(nodeName, colorClass);
        if (nodeName && Number.isFinite(loaded)) nodeLoadedCounts.set(nodeName, loaded);
        if (nodeName) nodeLogStatuses.set(nodeName, status);
        if (nodeName) nodeLogMessages.set(nodeName, message);
      });
    };
    const selectedInterval = () => {
      const seconds = Number.parseInt(intervalSelect?.value || logPanel.dataset.refreshInterval || "5", 10);
      return Number.isFinite(seconds) && seconds > 0 ? seconds : 5;
    };
    const refreshEnabled = () => intervalSelect ? intervalSelect.value !== "off" : logPanel.dataset.refreshEnabled === "true";
    const syncTimeFields = () => {
      const mode = selectedTime();
      logControls?.querySelectorAll("[data-log-time-custom]").forEach((field) => {
        const fieldName = field.dataset.logTimeCustom || "";
        field.hidden = mode !== "between" && !(mode === "since" && fieldName === "since");
      });
    };
    const applyTimeParams = (params) => {
      if (selectedTime() && selectedTime() !== "all") params.set("time", selectedTime());
      else params.delete("time");
      if (selectedTime() === "since" || selectedTime() === "between") {
        if (selectedSince()) params.set("since", selectedSince());
        else params.delete("since");
      } else {
        params.delete("since");
      }
      if (selectedTime() === "between") {
        if (selectedUntil()) params.set("until", selectedUntil());
        else params.delete("until");
      } else {
        params.delete("until");
      }
    };
    const syncLogUrl = () => {
      const url = new URL(window.location.href);
      if (url.pathname.indexOf("/logs") === -1) url.searchParams.set("tab", "logs");
      url.searchParams.set("lines", selectedLines());
      url.searchParams.set("per_node", selectedPerNode());
      applyTimeParams(url.searchParams);
      url.searchParams.delete("node");
      selectedNodes().forEach((node) => url.searchParams.append("node", node));
      if (selectedPriority() && selectedPriority() !== "all") url.searchParams.set("priority", selectedPriority());
      else url.searchParams.delete("priority");
      if (refreshEnabled()) {
        url.searchParams.set("refresh", "1");
        url.searchParams.set("interval", String(selectedInterval()));
      } else {
        url.searchParams.delete("refresh");
        url.searchParams.delete("interval");
      }
      if (selectedSearch()) url.searchParams.set("log_q", selectedSearch());
      else url.searchParams.delete("log_q");
      if (selectedExcludeSearch()) url.searchParams.set("exclude", "1");
      else url.searchParams.delete("exclude");
      if (wrapEnabled()) url.searchParams.delete("wrap");
      else url.searchParams.set("wrap", "0");
      if (selectedSmallLines()) url.searchParams.set("small", "1");
      else url.searchParams.delete("small");
      window.history.replaceState({}, "", url);
      if (logWindowLink) {
        const windowUrl = new URL(logWindowLink.href, window.location.href);
        windowUrl.searchParams.set("lines", selectedLines());
        windowUrl.searchParams.set("per_node", selectedPerNode());
        applyTimeParams(windowUrl.searchParams);
        windowUrl.searchParams.delete("node");
        selectedNodes().forEach((node) => windowUrl.searchParams.append("node", node));
        if (selectedPriority() && selectedPriority() !== "all") windowUrl.searchParams.set("priority", selectedPriority());
        else windowUrl.searchParams.delete("priority");
        if (refreshEnabled()) {
          windowUrl.searchParams.set("refresh", "1");
          windowUrl.searchParams.set("interval", String(selectedInterval()));
        } else {
          windowUrl.searchParams.delete("refresh");
          windowUrl.searchParams.delete("interval");
        }
        if (selectedSearch()) windowUrl.searchParams.set("log_q", selectedSearch());
        else windowUrl.searchParams.delete("log_q");
        if (selectedExcludeSearch()) windowUrl.searchParams.set("exclude", "1");
        else windowUrl.searchParams.delete("exclude");
        if (wrapEnabled()) windowUrl.searchParams.delete("wrap");
        else windowUrl.searchParams.set("wrap", "0");
        if (selectedSmallLines()) windowUrl.searchParams.set("small", "1");
        else windowUrl.searchParams.delete("small");
        logWindowLink.href = windowUrl.toString();
      }
    };
    const updateLineCountLabel = () => {
      if (lineCountLabel) lineCountLabel.textContent = selectedLines() === "all" ? "all" : selectedLines();
    };
    const updateNodeChoiceCounts = (lines) => {
      const visibleCounts = new Map();
      lines.forEach((line) => {
        const match = line.match(/^\[([^\]]+)\]\s+/);
        if (!match) return;
        visibleCounts.set(match[1], (visibleCounts.get(match[1]) || 0) + 1);
      });
      logControls?.querySelectorAll("[data-log-node-choice]").forEach((choice) => {
        const nodeName = choice.dataset.logNodeLabel || "";
        const countTarget = choice.querySelector("[data-log-node-count]");
        const colorClass = nodeColorClasses.get(nodeName);
        if (colorClass && !choice.classList.contains(colorClass)) choice.classList.add(colorClass);
        const visible = visibleCounts.get(nodeName) || 0;
        const loaded = nodeLoadedCounts.has(nodeName)
          ? nodeLoadedCounts.get(nodeName)
          : Number.parseInt(choice.dataset.loadedCount || "0", 10) || 0;
        const status = nodeLogStatuses.get(nodeName) || choice.dataset.logStatus || "ok";
        const message = nodeLogMessages.get(nodeName) || choice.dataset.logStatusMessage || "";
        choice.dataset.loadedCount = String(loaded);
        choice.dataset.logStatus = status;
        choice.dataset.logStatusMessage = message;
        choice.classList.toggle("log-node-error", status === "error");
        if (countTarget) countTarget.textContent = status === "error" ? "Error" : (loaded || visible ? `${visible}/${loaded}` : "");
        if (status === "error") choice.title = message ? `${nodeName}: ${message}` : `${nodeName}: log access failed.`;
        else if (loaded || visible) choice.title = `${nodeName}: ${visible} visible, ${loaded} loaded from this node.`;
      });
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
    const logLevelClass = (line) => {
      const match = line.match(/\[(EMERGENCY|ALERT|CRITICAL|ERROR|WARNING|NOTICE|INFO|DEBUG|UNKNOWN)\]/);
      return match ? `log-level-${match[1].toLowerCase()}` : "";
    };
    const renderLogLine = (lineNode, line, query) => {
      const nodeMatch = line.match(/^\[([^\]]+)\]\s+(.*)$/);
      const content = nodeMatch ? nodeMatch[2] : line;
      if (nodeMatch) {
        const chip = document.createElement("span");
        chip.className = ["log-node-chip", nodeColorClasses.get(nodeMatch[1])].filter(Boolean).join(" ");
        chip.title = "Log source node";
        chip.textContent = nodeMatch[1];
        lineNode.append(chip, document.createTextNode(" "));
      }
      const levelMatch = content.match(/\[(EMERGENCY|ALERT|CRITICAL|ERROR|WARNING|NOTICE|INFO|DEBUG|UNKNOWN)\]/);
      if (!levelMatch) {
        appendHighlightedText(lineNode, content, query);
        return;
      }
      const before = content.slice(0, levelMatch.index);
      const after = content.slice(levelMatch.index + levelMatch[0].length);
      appendHighlightedText(lineNode, before, query);
      const level = document.createElement("span");
      level.className = `log-level log-level-${levelMatch[1].toLowerCase()}`;
      level.textContent = levelMatch[1];
      lineNode.append(level);
      appendHighlightedText(lineNode, after, query);
    };
    const applyLogWrap = () => {
      document.querySelector("[data-log-output]")?.classList.toggle("no-wrap", !wrapEnabled());
    };
    const applyLogDensity = () => {
      document.querySelector("[data-log-output]")?.classList.toggle("small-lines", selectedSmallLines());
    };
    const renderLogText = (rawText) => {
      const output = document.querySelector("[data-log-output]");
      const code = output?.querySelector("code");
      if (!output || !code) return;
      applyLogWrap();
      applyLogDensity();
      const query = selectedSearch();
      output.dataset.rawLog = rawText;
      code.textContent = "";
      const fragment = document.createDocumentFragment();
      const lines = rawText.split("\n");
      const normalizedQuery = query.toLowerCase();
      const matchingLines = query
        ? lines.filter((line) => selectedExcludeSearch() !== line.toLowerCase().includes(normalizedQuery))
        : lines;
      if (query && matchingLines.length === 0) {
        fragment.appendChild(document.createTextNode(selectedExcludeSearch() ? "No loaded log lines remain after excluding this search." : "No loaded log lines match this search."));
      } else {
        matchingLines.forEach((line, index) => {
          if (index > 0) fragment.appendChild(document.createTextNode("\n"));
          const lineNode = document.createElement("span");
          lineNode.className = ["log-line", logLevelClass(line)].filter(Boolean).join(" ");
          renderLogLine(lineNode, line, query);
          fragment.appendChild(lineNode);
        });
      }
      updateNodeChoiceCounts(matchingLines);
      code.appendChild(fragment);
      if (searchStatus) {
        searchStatus.hidden = !query;
        searchStatus.textContent = query
          ? selectedExcludeSearch()
            ? `${matchingLines.length} line${matchingLines.length === 1 ? "" : "s"} remain after excluding this search.`
            : `${matchingLines.length} matching line${matchingLines.length === 1 ? "" : "s"} in the loaded logs.`
          : "";
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
        const params = new URLSearchParams();
        params.set("lines", selectedLines());
        params.set("per_node", selectedPerNode());
        applyTimeParams(params);
        selectedNodes().forEach((node) => params.append("node", node));
        if (selectedPriority() && selectedPriority() !== "all") params.set("priority", selectedPriority());
        if (!wrapEnabled()) params.set("wrap", "0");
        const target = `${logPanel.dataset.logUrl}?${params.toString()}`;
        const response = await fetch(target, { headers: { "X-Requested-With": "fetch" } });
        if (!response.ok) return;
        const doc = new DOMParser().parseFromString(await response.text(), "text/html");
        const nextLog = doc.querySelector("[data-log-output]");
        if (!nextLog || !currentLog) return;
        captureNodeColors(doc);
        const nextCode = nextLog.querySelector("code");
        renderLogText(nextLog.dataset.rawLog || nextCode?.textContent || "");
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
    const applySavedLogSettings = () => {
      if (logUrlHasExplicitSettings()) {
        const params = new URLSearchParams(window.location.search);
        if (smallLinesCheckbox) smallLinesCheckbox.checked = params.get("small") === "1";
        if (excludeSearchCheckbox) excludeSearchCheckbox.checked = params.get("exclude") === "1";
        return false;
      }
      const settings = readSavedLogSettings();
      if (!settings || typeof settings !== "object") return false;
      if (linesSelect && settings.lines) linesSelect.value = settings.lines;
      if (perNodeSelect && settings.perNode) perNodeSelect.value = settings.perNode;
      if (timeSelect && settings.time) timeSelect.value = settings.time;
      if (sinceInput && typeof settings.since === "string") sinceInput.value = settings.since;
      if (untilInput && typeof settings.until === "string") untilInput.value = settings.until;
      if (prioritySelect && settings.priority) prioritySelect.value = settings.priority;
      if (intervalSelect) intervalSelect.value = settings.refresh ? String(settings.interval || "5") : "off";
      if (searchInput && typeof settings.search === "string") searchInput.value = settings.search;
      if (wrapCheckbox) wrapCheckbox.checked = settings.wrap !== false;
      if (smallLinesCheckbox) smallLinesCheckbox.checked = Boolean(settings.small);
      if (excludeSearchCheckbox) excludeSearchCheckbox.checked = Boolean(settings.exclude);
      if (Array.isArray(settings.nodes)) {
        logControls?.querySelectorAll("input[name='node']").forEach((checkbox) => {
          if (!checkbox.disabled) checkbox.checked = settings.nodes.includes(checkbox.value);
        });
      }
      return true;
    };
    const saveLogSettings = () => {
      const settings = {
        lines: selectedLines(),
        perNode: selectedPerNode(),
        time: selectedTime(),
        since: selectedSince(),
        until: selectedUntil(),
        priority: selectedPriority(),
        refresh: refreshEnabled(),
        interval: selectedInterval(),
        search: selectedSearch(),
        exclude: selectedExcludeSearch(),
        wrap: wrapEnabled(),
        small: selectedSmallLines(),
        nodes: selectedNodes(),
      };
      try {
        window.localStorage.setItem(logSettingsKey, JSON.stringify(settings));
      } catch {
        return;
      }
      saveSettingsButton?.classList.add("saved");
      window.setTimeout(() => saveSettingsButton?.classList.remove("saved"), 900);
    };

    const restoredLogSettings = applySavedLogSettings();
    captureNodeColors(document);
    renderLogText(document.querySelector("[data-log-output]")?.dataset.rawLog || document.querySelector("[data-log-output] code")?.textContent || "");
    linesSelect?.addEventListener("change", () => applyLogControls({ refresh: true }));
    perNodeSelect?.addEventListener("change", () => applyLogControls({ refresh: true }));
    timeSelect?.addEventListener("change", () => {
      syncTimeFields();
      applyLogControls({ refresh: true });
    });
    sinceInput?.addEventListener("change", () => applyLogControls({ refresh: selectedTime() === "since" || selectedTime() === "between" }));
    untilInput?.addEventListener("change", () => applyLogControls({ refresh: selectedTime() === "between" }));
    prioritySelect?.addEventListener("change", () => applyLogControls({ refresh: true }));
    logControls?.querySelectorAll("input[name='node']").forEach((checkbox) => {
      checkbox.addEventListener("change", () => applyLogControls({ refresh: true }));
    });
    wrapCheckbox?.addEventListener("change", () => {
      applyLogWrap();
      syncLogUrl();
    });
    smallLinesCheckbox?.addEventListener("change", () => {
      applyLogDensity();
      syncLogUrl();
    });
    excludeSearchCheckbox?.addEventListener("change", applyLogSearch);
    intervalSelect?.addEventListener("change", () => applyLogControls({ refresh: refreshEnabled() }));
    searchInput?.addEventListener("input", () => {
      window.clearTimeout(searchTimer);
      searchTimer = window.setTimeout(applyLogSearch, 120);
    });
    document.addEventListener("selectionchange", updateRefreshPaused);
    refreshNow?.addEventListener("click", () => {
      syncLogUrl();
      refreshLogs({ followBottom: false });
    });
    saveSettingsButton?.addEventListener("click", saveLogSettings);
    panelMaximizeButtons.forEach((button) => button.addEventListener("click", togglePanelMaximized));
    outputMaximizeButtons.forEach((button) => button.addEventListener("click", toggleOutputMaximized));
    logScrollButtons.forEach((button) => {
      button.addEventListener("click", () => {
        const output = document.querySelector("[data-log-output]");
        if (!output) return;
        output.scrollTo({ top: button.dataset.logScroll === "top" ? 0 : output.scrollHeight, behavior: "smooth" });
      });
    });
    document.addEventListener("keydown", (event) => {
      if (event.key !== "Escape") return;
      if (!logPanel.classList.contains("log-panel-maximized") && !logPanel.classList.contains("log-output-maximized")) return;
      logPanel.classList.remove("log-panel-maximized", "log-output-maximized");
      syncMaximizeButtons();
    });
    applyLogSearch();
    syncTimeFields();
    updateLineCountLabel();
    updateRefreshPaused();
    syncMaximizeButtons();
    startTimer();
    if (restoredLogSettings) refreshLogs({ followBottom: false });
  }
})();
