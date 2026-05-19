/* Vanilla JS for the Pipeline Web-Interface.
 * - Tab switching (Doppel / Einzel)
 * - Single combined poll of /api/state every 3s (briefing s.5)
 * - Manual pipeline trigger + YouTube upload trigger
 * - Filename + YouTube config forms with autosave
 * - Output file list with per-file download links
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

  /**
   * Single combined poll - replaces the previous two parallel fetches
   * (/api/status + /api/files + /api/upload-status) with one request.
   * Also pulls the active Tournament + run history so the discipline
   * panel stays self-contained.
   */
  async function pollState(panel) {
    const discipline = panel.dataset.discipline;
    try {
      const [stateRes, histRes] = await Promise.all([
        fetch(`/api/state/${discipline}`, { credentials: "same-origin" }),
        fetch(`/api/history/${discipline}`, { credentials: "same-origin" }),
      ]);
      if (stateRes.ok) {
        const data = await stateRes.json();
        renderStatus(panel, data.pipeline);
        renderFiles(panel, discipline, data.files);
        renderUploadStatus(panel, data.upload);
        renderActiveTournament(panel, data.active_tournament);
      }
      if (histRes.ok) {
        renderHistory(panel, await histRes.json());
      }
    } catch (e) {
      console.warn("poll failed", e);
    }
  }

  function renderActiveTournament(panel, t) {
    const name = panel.querySelector('[data-field="active_tournament_name"]');
    const badge = panel.querySelector('[data-field="active_tournament_auto_badge"]');
    if (!name) return;
    if (!t) {
      name.textContent = "(keines - wird beim naechsten Run angelegt)";
      if (badge) badge.textContent = "";
    } else {
      name.textContent = t.name;
      if (badge) badge.textContent = t.is_auto_created ? " (auto)" : "";
    }
  }

  function renderHistory(panel, payload) {
    const stats = payload.stats || {};
    const totalEl = panel.querySelector('[data-field="history_total"]');
    const thrEl = panel.querySelector('[data-field="history_throughput"]');
    const failEl = panel.querySelector('[data-field="history_failed"]');
    if (totalEl) totalEl.textContent = stats.total_runs ?? 0;
    if (thrEl)   thrEl.textContent  = (stats.avg_throughput_gbph ?? 0).toFixed(1);
    if (failEl)  failEl.textContent = stats.failed_runs ?? 0;

    const tbody = panel.querySelector('[data-field="history_rows"] tbody');
    if (!tbody) return;
    const runs = payload.runs || [];
    if (runs.length === 0) {
      tbody.innerHTML =
        '<tr><td colspan="9" class="empty">Noch keine Runs.</td></tr>';
      return;
    }
    const phaseDur = (run, name) => {
      const p = (run.phases || []).find(x => x.phase === name);
      return p ? p.duration_s.toFixed(2) + 's' : '--';
    };
    tbody.innerHTML = runs.map(r => `
      <tr class="run-${escapeHtml(r.state)}">
        <td>${escapeHtml(r.folder_name)}</td>
        <td>${escapeHtml(r.state)}</td>
        <td>${fmtBytes(r.input_bytes)}</td>
        <td>${fmtBytes(r.output_bytes)}</td>
        <td>${phaseDur(r, 'merge')}</td>
        <td>${phaseDur(r, 'move')}</td>
        <td>${phaseDur(r, 'organize')}</td>
        <td>${phaseDur(r, 'rename')}</td>
        <td>${phaseDur(r, 'merge')}</td>
      </tr>
    `).join('');
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

  // ----- Upload preview + upload trigger + status polling ---------------
  async function loadUploadPreview(panel) {
    const discipline = panel.dataset.discipline;
    const list = panel.querySelector('[data-field="upload_preview"]');
    const quotaEl = panel.querySelector('[data-field="quota_hint"]');
    if (!list) return;
    try {
      const res = await fetch(`/api/upload-preview/${discipline}`, {
        credentials: "same-origin",
      });
      if (!res.ok) return;
      const data = await res.json();
      if (!data.files || data.files.length === 0) {
        list.innerHTML = '<li class="empty">Noch keine Videos vorhanden.</li>';
      } else {
        list.innerHTML = data.files
          .map((f) => {
            const title = escapeHtml(f.title || "(kein Titel)");
            const file = escapeHtml(f.file);
            return `<li><strong>${title}</strong>` +
                   `<br><span class="size">${file}</span></li>`;
          })
          .join("");
      }
      if (quotaEl) {
        if (data.quota_hint) {
          quotaEl.textContent = data.quota_hint;
          quotaEl.hidden = false;
        } else {
          quotaEl.hidden = true;
        }
      }
    } catch (e) {
      console.warn("preview failed", e);
    }
  }

  function renderUploadStatus(panel, s) {
    const set = (field, val) => {
      const el = panel.querySelector(`[data-field="${field}"]`);
      if (el) el.textContent = val == null ? "--" : val;
    };
    set("upload_state", s.state);
    if (s.total_files > 0) {
      set("upload_progress",
        `${s.completed_files} / ${s.total_files}` +
        (s.current_progress_percent
          ? ` (${s.current_progress_percent.toFixed(1)}%)` : ""));
    } else {
      set("upload_progress", "--");
    }
    set("upload_current", s.current_file || "--");

    const bar = panel.querySelector('[data-field="upload_bar"]');
    if (bar) {
      if (s.total_files > 0) {
        const overall = (s.completed_files * 100 +
                         (s.current_progress_percent || 0)) / s.total_files;
        bar.value = Math.min(100, overall);
      } else {
        bar.value = 0;
      }
    }

    const errEl = panel.querySelector('[data-field="upload_error"]');
    if (errEl) {
      if (s.error) {
        errEl.textContent = `Fehler: ${s.error}`;
        errEl.hidden = false;
      } else {
        errEl.hidden = true;
      }
    }

    const log = panel.querySelector('[data-field="upload_log"]');
    if (log) {
      log.textContent = (s.log_tail || []).join("\n");
      log.scrollTop = log.scrollHeight;
    }
  }

  function bindUploadButtons() {
    document.querySelectorAll('[data-action="upload-refresh"]').forEach((btn) => {
      btn.addEventListener("click", () => {
        const panel = btn.closest(".tabpanel");
        loadUploadPreview(panel);
      });
    });
    document.querySelectorAll('[data-action="upload"]').forEach((btn) => {
      btn.addEventListener("click", async () => {
        const panel = btn.closest(".tabpanel");
        const discipline = panel.dataset.discipline;
        btn.disabled = true;
        try {
          const res = await fetch(`/api/upload/${discipline}`, {
            method: "POST",
            credentials: "same-origin",
          });
          if (!res.ok) console.warn("upload failed", await res.text());
        } finally {
          setTimeout(() => { btn.disabled = false; }, 2000);
        }
      });
    });
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
    bindUploadButtons();
    bindFilenameForms();
    bindYoutubeForms();

    const panels = Array.from(
      document.querySelectorAll(".tabpanel[data-discipline]")
    ).filter((p) => p.getAttribute("aria-disabled") !== "true");

    panels.forEach((panel) => {
      loadFilenameConfig(panel);
      loadYoutubeConfig(panel);
      loadUploadPreview(panel);
      pollState(panel);
      setInterval(() => pollState(panel), POLL_MS);
    });

    // Cross-cutting Dashboard pieces.
    bindGotoTournaments();
    bindTournamentForms();
    bindArchiveForms();
    loadTournaments();
    pollStorage();
    setInterval(pollStorage, 10000);
    loadArchives();
    setInterval(loadArchives, POLL_MS);
  });

  // ----- Storage banner + System tab -------------------------------------
  async function pollStorage() {
    try {
      const res = await fetch("/api/storage", { credentials: "same-origin" });
      if (!res.ok) return;
      const data = await res.json();
      renderStorageBanner(data);
      renderStorageList(data);
    } catch (e) { console.warn("storage poll failed", e); }
  }

  function renderStorageBanner(data) {
    const banner = document.getElementById("storage-banner");
    if (!banner) return;
    const overall = data.overall_status || "ok";
    banner.setAttribute("data-status", overall);
    if (overall === "ok") {
      banner.hidden = true;
      return;
    }
    const worst = (data.volumes || [])
      .filter(v => v.status === overall)
      .map(v => `${v.path}: ${(v.free_bytes / 1024**3).toFixed(1)} GB frei`)
      .join(" · ");
    const msg = banner.querySelector('[data-field="storage_message"]');
    if (msg) msg.textContent =
      (overall === "critical" ? "Speicherplatz kritisch: " : "Speicherplatz knapp: ")
      + worst;
    banner.hidden = false;
  }

  function renderStorageList(data) {
    const list = document.querySelector('[data-field="volumes_list"]');
    if (!list) return;
    const vols = data.volumes || [];
    if (vols.length === 0) {
      list.innerHTML = '<li class="empty">Keine Volumes erkannt.</li>';
      return;
    }
    list.innerHTML = vols.map(v => {
      const totalGb = (v.total_bytes / 1024**3).toFixed(1);
      const freeGb  = (v.free_bytes  / 1024**3).toFixed(1);
      return `<li class="volume volume-${escapeHtml(v.status)}">
        <strong>${escapeHtml(v.path)}</strong>
        ${v.pct_used.toFixed(1)}% belegt
        · ${freeGb} GB frei von ${totalGb} GB
        <span class="vstatus">${escapeHtml(v.status)}</span>
      </li>`;
    }).join('');
  }

  // ----- Tournaments tab --------------------------------------------------
  function bindGotoTournaments() {
    document.querySelectorAll('[data-action="goto-tournaments"]').forEach(a => {
      a.addEventListener("click", (e) => {
        e.preventDefault();
        activateTab("Turniere");
      });
    });
  }

  function bindTournamentForms() {
    const form = document.querySelector('[data-form="tournament-create"]');
    if (!form) return;
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const data = Object.fromEntries(new FormData(form).entries());
      if (data.max_workers) data.max_workers = parseInt(data.max_workers, 10);
      // Submit against the first discipline we know of.
      const discipline = firstAvailableDiscipline();
      if (!discipline) return;
      const res = await fetch(`/api/tournaments/${discipline}`, {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data),
      });
      const status = form.querySelector("[data-form-status]");
      if (status) {
        status.textContent = res.ok ? "Erstellt." : "Fehler beim Anlegen.";
        status.hidden = false;
        setTimeout(() => { status.hidden = true; }, 3000);
      }
      if (res.ok) {
        form.reset();
        form.elements["max_workers"].value = 4;
        loadTournaments();
      }
    });
  }

  function firstAvailableDiscipline() {
    const tab = document.querySelector('.tab[data-tab="Doppel"]:not([disabled])')
             || document.querySelector('.tab[data-tab="Einzel"]:not([disabled])');
    return tab ? tab.dataset.tab : null;
  }

  async function loadTournaments() {
    const tbody = document.querySelector('[data-field="tournaments_rows"] tbody');
    if (!tbody) return;
    const discipline = firstAvailableDiscipline();
    if (!discipline) return;
    try {
      const res = await fetch(`/api/tournaments/${discipline}`, {
        credentials: "same-origin",
      });
      if (!res.ok) return;
      const data = await res.json();
      const tournaments = data.tournaments || [];
      const activeId = (data.active_tournament || {}).id;
      if (tournaments.length === 0) {
        tbody.innerHTML =
          '<tr><td colspan="5" class="empty">Noch keine Turniere.</td></tr>';
        return;
      }
      tbody.innerHTML = tournaments.map(t => `
        <tr>
          <td>${escapeHtml(t.name)}${t.is_auto_created ? ' <em>(auto)</em>' : ''}</td>
          <td>${escapeHtml(t.date || '--')}</td>
          <td>${escapeHtml(t.location || '--')}</td>
          <td>${t.id === activeId ? `<strong>${escapeHtml(discipline)}</strong>` : '--'}</td>
          <td>
            ${t.id !== activeId
              ? `<button data-action="activate-tournament" data-tid="${t.id}">aktivieren</button>`
              : '<em>aktiv</em>'}
          </td>
        </tr>
      `).join('');
      tbody.querySelectorAll('[data-action="activate-tournament"]').forEach(b => {
        b.addEventListener("click", async () => {
          const tid = b.dataset.tid;
          await fetch(`/api/tournaments/${discipline}/${tid}/activate`, {
            method: "POST", credentials: "same-origin",
          });
          loadTournaments();
        });
      });
    } catch (e) { console.warn("tournaments load failed", e); }
  }

  // ----- Archive flow -----------------------------------------------------
  function bindArchiveForms() {
    const planForm = document.querySelector('[data-form="archive-plan"]');
    if (!planForm) return;
    let lastPlan = null;
    planForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const data = Object.fromEntries(new FormData(planForm).entries());
      const res = await fetch(`/api/archive/${data.discipline}/plan`, {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          tournament_id: parseInt(data.tournament_id, 10),
          archive_root: data.archive_root,
        }),
      });
      const preview = document.querySelector('[data-field="archive_plan_preview"]');
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        alert("Plan-Fehler: " + (err.error || res.status));
        return;
      }
      const plan = await res.json();
      lastPlan = { ...data, plan };
      preview.querySelector('[data-field="archive_plan_files"]').textContent = plan.total_files;
      preview.querySelector('[data-field="archive_plan_gb"]').textContent = plan.total_gb;
      preview.querySelector('[data-field="archive_plan_target"]').textContent = plan.archive_root;
      preview.hidden = false;
    });

    const execBtn = document.querySelector('[data-action="archive-execute"]');
    execBtn.addEventListener("click", async () => {
      if (!lastPlan) { alert("Bitte zuerst Plan vorbereiten."); return; }
      const ok = confirm(
        `Archivierung starten?\n\n` +
        `${lastPlan.plan.total_files} Dateien (${lastPlan.plan.total_gb} GB)\n` +
        `Ziel: ${lastPlan.plan.archive_root}\n\n` +
        `SHA-256-Verifikation auf jeder Datei. SSD-Originale werden ` +
        `nach erfolgreicher Verifikation geloescht.`
      );
      if (!ok) return;
      const deleteSource = document.querySelector(
        '[data-field="archive_delete_source"]'
      ).checked;
      const res = await fetch(`/api/archive/${lastPlan.discipline}/execute`, {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          tournament_id: parseInt(lastPlan.tournament_id, 10),
          archive_root: lastPlan.archive_root,
          delete_source: deleteSource,
          confirm: "yes",
        }),
      });
      if (!res.ok) {
        alert("Archive-Start fehlgeschlagen: " + res.status);
        return;
      }
      alert("Archivierung gestartet - Status in der Verlaufstabelle.");
      loadArchives();
    });
  }

  async function loadArchives() {
    const tbody = document.querySelector('[data-field="archives_rows"] tbody');
    if (!tbody) return;
    const discipline = firstAvailableDiscipline();
    if (!discipline) return;
    try {
      const res = await fetch(`/api/archives/${discipline}`, {
        credentials: "same-origin",
      });
      if (!res.ok) return;
      const data = await res.json();
      const archives = data.archives || [];
      if (archives.length === 0) {
        tbody.innerHTML =
          '<tr><td colspan="5" class="empty">Noch keine Archivierungen.</td></tr>';
        return;
      }
      tbody.innerHTML = archives.map(a => `
        <tr>
          <td>${escapeHtml(a.tournament_name || '--')}</td>
          <td><code>${escapeHtml(a.archive_path)}</code></td>
          <td class="run-${escapeHtml(a.state)}">${escapeHtml(a.state)}</td>
          <td>${a.files_verified}/${a.files_total}</td>
          <td>${fmtBytes(a.bytes_copied || 0)}</td>
        </tr>
      `).join('');
    } catch (e) { console.warn("archives load failed", e); }
  }
})();
