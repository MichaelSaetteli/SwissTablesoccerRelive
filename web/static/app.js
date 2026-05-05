/* Vanilla JS for the Pipeline Web-Interface.
 * - Tab switching (Doppel / Einzel)
 * - Status polling every 3s (briefing s.5)
 * - Manual pipeline trigger
 * - YouTube config form auto-save
 * - Output file list with download links
 */
(function () {
  "use strict";

  const POLL_MS = 3000;

  // ----- Tab switching ----------------------------------------------------
  function activateTab(name) {
    document.querySelectorAll(".tab").forEach((btn) => {
      const isActive = btn.dataset.tab === name;
      btn.setAttribute("aria-selected", isActive ? "true" : "false");
    });
    document.querySelectorAll(".tabpanel").forEach((panel) => {
      panel.classList.toggle("active", panel.dataset.discipline === name);
    });
  }

  function bindTabs() {
    const tabs = Array.from(document.querySelectorAll(".tab")).filter(
      (t) => !t.disabled
    );
    if (tabs.length === 0) return;
    tabs.forEach((btn) => {
      btn.addEventListener("click", () => activateTab(btn.dataset.tab));
    });
    activateTab(tabs[0].dataset.tab);
  }

  // ----- Status polling ---------------------------------------------------
  function fmtList(arr) {
    if (!arr || arr.length === 0) return "(keine)";
    return arr.join(", ");
  }
  function fmtBytes(n) {
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
    if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
    return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
  }

  async function pollStatus(panel) {
    const discipline = panel.dataset.discipline;
    try {
      const [statusRes, filesRes] = await Promise.all([
        fetch(`/api/status/${discipline}`, { credentials: "same-origin" }),
        fetch(`/api/files/${discipline}`, { credentials: "same-origin" }),
      ]);
      if (!statusRes.ok || !filesRes.ok) return;
      const status = await statusRes.json();
      const fileList = await filesRes.json();
      renderStatus(panel, status);
      renderFiles(panel, discipline, fileList.files);
    } catch (e) {
      console.warn("poll failed", e);
    }
  }

  function renderStatus(panel, status) {
    const set = (field, val) => {
      const el = panel.querySelector(`[data-field="${field}"]`);
      if (el) el.textContent = val == null ? "--" : val;
    };
    set("state", status.state);
    set("folders_detected", fmtList(status.folders_detected));
    set("folders_processed", fmtList(status.folders_processed));
    set("started_at", status.started_at || "--");
    set("updated_at", status.updated_at || "--");

    const errEl = panel.querySelector('[data-field="error"]');
    if (errEl) {
      if (status.error) {
        errEl.textContent = `Fehler: ${status.error}`;
        errEl.hidden = false;
      } else {
        errEl.textContent = "";
        errEl.hidden = true;
      }
    }

    const log = panel.querySelector('[data-field="log_tail"]');
    if (log) {
      log.textContent = (status.log_tail || []).join("\n");
      log.scrollTop = log.scrollHeight;
    }
  }

  function renderFiles(panel, discipline, files) {
    const list = panel.querySelector('[data-field="files"]');
    if (!list) return;
    if (!files || files.length === 0) {
      list.innerHTML = '<li class="empty">Noch keine Videos vorhanden.</li>';
      return;
    }
    list.innerHTML = files
      .map((f) => {
        const url = `/download/${discipline}/${encodeURIComponent(f.name)}`;
        return `<li><a href="${url}">${escapeHtml(f.name)}</a>` +
               `<span class="size">${fmtBytes(f.size_bytes)}</span></li>`;
      })
      .join("");
  }

  function escapeHtml(s) {
    return s.replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  // ----- Run button -------------------------------------------------------
  function bindRunButtons() {
    document.querySelectorAll('[data-action="run"]').forEach((btn) => {
      btn.addEventListener("click", async () => {
        const panel = btn.closest(".tabpanel");
        const discipline = panel.dataset.discipline;
        btn.disabled = true;
        try {
          const res = await fetch(`/api/run/${discipline}`, {
            method: "POST",
            credentials: "same-origin",
          });
          if (!res.ok) {
            console.warn("run failed", await res.text());
          }
        } finally {
          setTimeout(() => { btn.disabled = false; }, 1500);
        }
      });
    });
  }

  // ----- Filename constants form (live preview + save) -------------------
  function buildPreview(values) {
    // Mirror pipeline.config_loader.build_output_filename:
    // {jahr} {sts_nummer} {tischnummer} {turniername} {disziplin} [{part}].mp4
    const example = "T01"; // mock tischnummer for the preview
    const parts = [
      values.jahr, values.sts_nummer, example,
      values.turniername, values.disziplin, values.part,
    ];
    const filtered = parts.map((p) => (p || "").trim()).filter(Boolean);
    return filtered.join(" ") + ".mp4";
  }

  function readFilenameForm(form) {
    return {
      jahr: form.elements.jahr.value,
      sts_nummer: form.elements.sts_nummer.value,
      turniername: form.elements.turniername.value,
      disziplin: form.elements.disziplin.value,
      part: form.elements.part.value,
    };
  }

  function updateFilenamePreview(form) {
    const preview = form.querySelector('[data-field="filename_preview"]');
    if (preview) preview.textContent = buildPreview(readFilenameForm(form));
  }

  async function loadFilenameConfig(panel) {
    const form = panel.querySelector('[data-form="filename"]');
    if (!form) return;
    const discipline = panel.dataset.discipline;
    try {
      const res = await fetch(`/api/filename-config/${discipline}`, {
        credentials: "same-origin",
      });
      if (!res.ok) return;
      const data = await res.json();
      ["jahr", "sts_nummer", "turniername", "disziplin", "part"].forEach((k) => {
        if (form.elements[k]) form.elements[k].value = data[k] || "";
      });
      updateFilenamePreview(form);
    } catch (e) {
      console.warn("load filename config failed", e);
    }
  }

  function bindFilenameForms() {
    document.querySelectorAll('[data-form="filename"]').forEach((form) => {
      form.addEventListener("input", () => updateFilenamePreview(form));

      form.addEventListener("submit", async (e) => {
        e.preventDefault();
        const panel = form.closest(".tabpanel");
        const discipline = panel.dataset.discipline;
        const status = form.querySelector("[data-form-status]");
        try {
          const res = await fetch(`/api/filename-config/${discipline}`, {
            method: "POST",
            credentials: "same-origin",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(readFilenameForm(form)),
          });
          if (status) {
            status.textContent = res.ok ? "Gespeichert." : "Fehler beim Speichern.";
            status.hidden = false;
            setTimeout(() => { status.hidden = true; }, 3000);
          }
        } catch (e) {
          if (status) {
            status.textContent = `Fehler: ${e}`;
            status.hidden = false;
          }
        }
      });
    });
  }

  // ----- YouTube config form ---------------------------------------------
  async function loadYoutubeConfig(panel) {
    const form = panel.querySelector('[data-form="youtube"]');
    if (!form) return;
    const discipline = panel.dataset.discipline;
    try {
      const res = await fetch(`/api/youtube-config/${discipline}`, {
        credentials: "same-origin",
      });
      if (!res.ok) return;
      const data = await res.json();
      Object.entries(data).forEach(([key, value]) => {
        const el = form.elements[key];
        if (el && el.type !== "radio") el.value = value || "";
      });
      const mode = data.playlist_create_new ? "new" : "existing";
      const radio = form.querySelector(`input[name="playlist_mode"][value="${mode}"]`);
      if (radio) radio.checked = true;
    } catch (e) {
      console.warn("load youtube config failed", e);
    }
  }

  function bindYoutubeForms() {
    document.querySelectorAll('[data-form="youtube"]').forEach((form) => {
      form.addEventListener("submit", async (e) => {
        e.preventDefault();
        const panel = form.closest(".tabpanel");
        const discipline = panel.dataset.discipline;
        const status = form.querySelector("[data-form-status]");
        const data = Object.fromEntries(new FormData(form).entries());
        data.playlist_create_new = data.playlist_mode === "new";
        delete data.playlist_mode;
        try {
          const res = await fetch(`/api/youtube-config/${discipline}`, {
            method: "POST",
            credentials: "same-origin",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(data),
          });
          if (status) {
            status.textContent = res.ok ? "Gespeichert." : "Fehler beim Speichern.";
            status.hidden = false;
            setTimeout(() => { status.hidden = true; }, 3000);
          }
        } catch (e) {
          if (status) {
            status.textContent = `Fehler: ${e}`;
            status.hidden = false;
          }
        }
      });
    });
  }

  // ----- Boot -------------------------------------------------------------
  document.addEventListener("DOMContentLoaded", () => {
    bindTabs();
    bindRunButtons();
    bindFilenameForms();
    bindYoutubeForms();

    const panels = Array.from(
      document.querySelectorAll(".tabpanel[data-discipline]")
    ).filter((p) => p.getAttribute("aria-disabled") !== "true");

    panels.forEach((panel) => {
      loadFilenameConfig(panel);
      loadYoutubeConfig(panel);
      pollStatus(panel);
      setInterval(() => pollStatus(panel), POLL_MS);
    });
  });
})();
