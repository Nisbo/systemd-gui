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
    const preview = form.querySelector("[data-import-preview]");
    const previewTitle = form.querySelector("[data-import-preview-title]");
    const previewSummary = form.querySelector("[data-import-preview-summary]");
    const previewList = form.querySelector("[data-import-preview-list]");
    const currentDataNode = form.querySelector("[data-current-quick-shell]");
    const currentQuickShell = (() => {
      try {
        return JSON.parse(currentDataNode?.textContent || "{\"items\":[]}");
      } catch (_error) {
        return { items: [] };
      }
    })();
    let importPreviewPayload = null;

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
    const itemKey = (entry) => JSON.stringify(entry, (key, value) => key.startsWith("__preview") ? undefined : value);
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
      if (duplicateMode === "keep_all") {
        const duplicateName = itemLabelKey(nextItem);
        const duplicateEntries = targetItems.filter((existing) => itemLabelKey(existing) === duplicateName);
        if (duplicateEntries.length) {
          duplicateEntries.forEach((existing) => { existing.__previewDuplicate = true; });
          nextItem.__previewDuplicate = true;
        }
        return nextItem;
      }
      if (targetItems.some((existing) => itemKey(existing) === itemKey(nextItem))) return null;
      if (duplicateMode === "rename_conflicts" && targetItems.some((existing) => itemLabelKey(existing) === itemLabelKey(nextItem))) {
        nextItem.name = uniqueImportName(itemLabelKey(nextItem), targetItems);
        nextItem.__previewRenamed = true;
      }
      return nextItem;
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
    const plural = (count, word) => `${count} ${word}${count === 1 ? "" : "s"}`;
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
        const preparedItem = prepareImportItem(item, targetItems, duplicateMode);
        if (!preparedItem) {
          const skipped = cloneJson(item);
          skipped.__previewState = "skipped";
          skipped.__previewNote = "exact duplicate will be skipped";
          targetItems.push(skipped);
          return;
        }
        targetItems.push(preparedItem);
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
        const isTarget = pathsEqual(path, targetPath);
        row.className = `import-preview-item ${type} ${state}${isTarget ? " target" : ""}`;
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
        else if (state === "imported") addStatusChip(entry.__previewRenamed ? "imported + renamed" : "imported", entry.__previewRenamed ? "warning" : "");
        else if (state === "removed") addStatusChip("will be removed");
        else if (state === "skipped") addStatusChip("will be skipped");
        if (entry.__previewDuplicate) addStatusChip("duplicate", "warning");
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
          const shouldExpand = state !== "existing" || isTarget || pathIsAncestor(path, targetPath) || pathIsDescendant(path, targetPath);
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
    const setPreviewState = (state, summary, items = [], mode = "add_to_target", targetPath = "", duplicateMode = "rename_conflicts") => {
      if (!preview || !previewTitle || !previewSummary) return;
      preview.hidden = false;
      preview.classList.remove("ok", "warning", "danger");
      preview.classList.add(state);
      previewTitle.textContent = state === "danger" ? "Import preview needs attention" : "Import preview";
      previewSummary.textContent = summary;
      buildPreviewTree(items, mode, targetPath, duplicateMode);
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
      const duplicateMode = duplicateSelect?.value || "rename_conflicts";
      const target = targetLabel();
      const stats = collectImportStats(items);
      const countSummary = `${plural(items.length, "top-level entry")}; ${plural(stats.categories, "category")}, ${plural(stats.commands, "command")}, ${plural(stats.sequences, "sequence")} total.`;
      if (mode === "add_to_target") {
        setPreviewState("ok", `Will merge the imported entries into ${target}. Existing entries stay. ${countSummary}`, items, mode, targetPath, duplicateMode);
      } else if (mode === "replace_target") {
        setPreviewState("warning", `Will delete entries inside ${target}, then import this file there. ${countSummary}`, items, mode, targetPath, duplicateMode);
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
    fileInput?.addEventListener("change", () => {
      const file = fileInput.files?.[0];
      importPreviewPayload = null;
      if (!file) {
        syncImportPreview();
        return;
      }
      file.text().then((text) => {
        importPreviewPayload = JSON.parse(text);
        syncImportPreview();
      }).catch((error) => {
        importPreviewPayload = {};
        setPreviewState("danger", error instanceof SyntaxError ? "This file is not valid JSON." : "Could not read this file.");
      });
    });
    modeSelect?.addEventListener("change", syncImportHelp);
    duplicateSelect?.addEventListener("change", syncImportHelp);
    targetSelect?.addEventListener("change", syncImportPreview);
    syncImportHelp();
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
