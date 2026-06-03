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
  function fmtDuration(seconds) {
    const s = Math.round(seconds);
    if (s < 60) return `${s} s`;
    const m = Math.round(s / 60);
    if (m < 60) return `${m} min`;
    const h = Math.floor(m / 60);
    const rem = m % 60;
    return rem ? `${h} h ${rem} min` : `${h} h`;
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
      const [stateRes, histRes, ctrlRes] = await Promise.all([
        fetch(`/api/state/${discipline}`, { credentials: "same-origin" }),
        fetch(`/api/history/${discipline}`, { credentials: "same-origin" }),
        fetch(`/api/pipeline/${discipline}/control`, { credentials: "same-origin" }),
      ]);
      if (stateRes.ok) {
        const data = await stateRes.json();
        renderStatus(panel, data.pipeline);
        renderFiles(panel, discipline, data.files);
        renderUploadStatus(panel, data.upload);
        renderUploadThroughput(panel, data.upload_throughput);
        renderActiveTournament(panel, data.active_tournament);
        renderEstimate(panel, data.processing_estimate);
        renderTiering(panel, data.tiering);
      }
      if (histRes.ok) {
        renderHistory(panel, await histRes.json());
      }
      if (ctrlRes.ok) {
        renderPipelineControl(panel, await ctrlRes.json());
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

  function renderUploadThroughput(panel, t) {
    const el = panel.querySelector('[data-field="upload_speed"]');
    if (!el) return;
    if (!t || t.mbit_s == null) { el.textContent = "--"; return; }
    let txt = `${t.mbit_s.toFixed(2)} Mbit/s`;
    if (t.state === "uploading" && t.eta_seconds != null && t.eta_seconds > 0) {
      txt += ` · Rest ~ ${fmtDuration(t.eta_seconds)}`;
    } else if (t.state === "done") {
      txt = `Ø ${t.mbit_s.toFixed(2)} Mbit/s (abgeschlossen)`;
    }
    el.textContent = txt;
  }

  function renderTiering(panel, t) {
    const stateEl = panel.querySelector('[data-field="tiering_state"]');
    const detailEl = panel.querySelector('[data-field="tiering_detail"]');
    if (!stateEl) return;
    const state = (t && t.state) || "idle";
    const labels = { idle: "bereit", running: "laeuft...",
                     done: "abgeschlossen", error: "Fehler" };
    stateEl.textContent = labels[state] || state;
    if (!detailEl) return;
    if (state === "done" && t.bytes_freed != null) {
      detailEl.textContent = ` (${fmtBytes(t.bytes_freed)} verschoben)`;
    } else if (state === "error" && t.error) {
      detailEl.textContent = ` (${t.error})`;
    } else {
      detailEl.textContent = "";
    }
  }

  function renderEstimate(panel, est) {
    const valEl = panel.querySelector('[data-field="estimate_value"]');
    const hintEl = panel.querySelector('[data-field="estimate_hint"]');
    if (!valEl) return;
    if (hintEl) hintEl.textContent = "";
    if (!est) { valEl.textContent = "--"; return; }

    const bytes = est.input_bytes || 0;
    if (bytes === 0) {
      valEl.textContent = "kein Material im Eingang";
      return;
    }
    if (est.calibrating || est.total_seconds == null) {
      valEl.textContent = `kalibriert noch (${fmtBytes(bytes)} im Eingang)`;
      return;
    }
    valEl.textContent = `~ ${fmtDuration(est.total_seconds)} fuer ${fmtBytes(bytes)}`;
    if (hintEl && (est.sample_runs || 0) < 3) {
      hintEl.textContent = ` (grobe Schaetzung, erst ${est.sample_runs} Run(s) als Basis)`;
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
        '<tr><td colspan="12" class="empty">Noch keine Runs.</td></tr>';
      return;
    }
    const phaseDur = (run, name) => {
      const p = (run.phases || []).find(x => x.phase === name);
      return p ? p.duration_s.toFixed(2) + 's' : '--';
    };
    const fmtTime = (iso) => {
      if (!iso) return '--';
      const d = new Date(iso);
      if (isNaN(d.getTime())) return escapeHtml(iso);
      const p = (n) => String(n).padStart(2, '0');
      return `${p(d.getDate())}.${p(d.getMonth() + 1)}.${d.getFullYear()} `
           + `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
    };
    tbody.innerHTML = runs.map(r => `
      <tr class="run-${escapeHtml(r.state)}" data-run-id="${r.id}">
        <td><input type="checkbox" class="bulk-select" data-run-id="${r.id}"></td>
        <td class="run-time">${fmtTime(r.started_at)}</td>
        <td>${escapeHtml(r.folder_name)}</td>
        <td>${escapeHtml(r.state)}</td>
        <td><input type="number" class="prio-input" data-run-id="${r.id}"
                   value="${r.priority ?? 50}" min="0" max="999"
                   title="Niedriger = hoeher priorisiert"></td>
        <td>${fmtBytes(r.input_bytes)}</td>
        <td>${fmtBytes(r.output_bytes)}</td>
        <td>${phaseDur(r, 'merge')}</td>
        <td>${phaseDur(r, 'move')}</td>
        <td>${phaseDur(r, 'organize')}</td>
        <td>${phaseDur(r, 'rename')}</td>
        <td>
          <select class="restart-phase" data-run-id="${r.id}">
            <option value="merge">Merge</option>
            <option value="rename">Rename + Merge</option>
            <option value="output">Output</option>
          </select>
          <button class="row-restart" data-run-id="${r.id}">restart</button>
        </td>
      </tr>
    `).join('');

    // Wire up the per-row controls.
    const discipline = panel.dataset.discipline;
    tbody.querySelectorAll('.row-restart').forEach(btn => {
      btn.addEventListener('click', () => {
        const rid = btn.dataset.runId;
        const phase = tbody.querySelector(
          `.restart-phase[data-run-id="${rid}"]`).value;
        restartJob(discipline, rid, phase);
      });
    });
    tbody.querySelectorAll('.prio-input').forEach(inp => {
      let timer = null;
      inp.addEventListener('input', () => {
        clearTimeout(timer);
        timer = setTimeout(() => {
          updateJob(discipline, inp.dataset.runId,
                    { priority: parseInt(inp.value, 10) });
        }, 500);   // debounce so we do not POST on every keystroke
      });
    });
    tbody.querySelectorAll('.bulk-select').forEach(cb => {
      cb.addEventListener('change', () => updateBulkToolbar(panel));
    });

    updateBulkToolbar(panel);
  }

  async function restartJob(discipline, runId, fromPhase) {
    try {
      const res = await fetch(`/api/jobs/${discipline}/${runId}/restart`, {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ from_phase: fromPhase }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        alert(`Restart fehlgeschlagen: ${err.error || res.status}`);
      }
    } catch (e) { console.warn('restart failed', e); }
  }

  async function updateJob(discipline, runId, payload) {
    await fetch(`/api/jobs/${discipline}/${runId}`, {
      method: 'PATCH', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
  }

  function updateBulkToolbar(panel) {
    const toolbar = panel.querySelector('[data-bulk-toolbar]');
    if (!toolbar) return;
    const checked = panel.querySelectorAll('.bulk-select:checked');
    const counter = panel.querySelector('[data-bulk-count]');
    if (counter) counter.textContent = checked.length;
    toolbar.hidden = checked.length === 0;
  }

  function renderPipelineControl(panel, pipelineState) {
    const label = panel.querySelector('[data-field="pipeline_paused_label"]');
    const pauseBtn = panel.querySelector('[data-action="pipeline-pause"]');
    const resumeBtn = panel.querySelector('[data-action="pipeline-resume"]');
    const runBtn = panel.querySelector('[data-action="run"]');
    const paused = pipelineState && pipelineState.paused;
    if (label) {
      label.textContent = paused ? 'pausiert' : 'aktiv';
      label.classList.toggle('paused', !!paused);
    }
    if (pauseBtn)  pauseBtn.hidden  = !!paused;
    if (resumeBtn) resumeBtn.hidden = !paused;
    if (runBtn)    runBtn.disabled  = !!paused;
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

    // ----- Tiering: move-to-HDD + retention sweep -----
    document.querySelectorAll('[data-action="tiering-stage"]').forEach((btn) => {
      btn.addEventListener("click", async () => {
        const discipline = btn.closest(".tabpanel").dataset.discipline;
        if (!confirm(
          "Verarbeitete Daten dieser Disziplin verifiziert auf die HDD " +
          "verschieben? Die SSD-Quelle wird erst nach erfolgreicher Pruefung " +
          "geleert.")) return;
        btn.disabled = true;
        try {
          const res = await fetch(`/api/tiering/${discipline}`, {
            method: "POST", credentials: "same-origin",
          });
          if (!res.ok) alert("Verschieben abgelehnt: " + (await res.text()));
        } finally {
          setTimeout(() => { btn.disabled = false; }, 1500);
        }
      });
    });
    document.querySelectorAll('[data-action="tiering-sweep"]').forEach((btn) => {
      btn.addEventListener("click", async () => {
        const discipline = btn.closest(".tabpanel").dataset.discipline;
        if (!confirm(
          "Abgelaufene Staging-Ordner auf der HDD endgueltig loeschen?")) return;
        btn.disabled = true;
        try {
          const res = await fetch(`/api/tiering/${discipline}/sweep`, {
            method: "POST", credentials: "same-origin",
          });
          const body = await res.json().catch(() => ({}));
          if (res.ok) {
            alert(`Aufgeraeumt: ${(body.deleted || []).length} Ordner, ` +
                  `${fmtBytes(body.bytes_freed || 0)} frei.`);
          } else {
            alert("Sweep abgelehnt: " + (body.error || res.status));
          }
        } finally {
          setTimeout(() => { btn.disabled = false; }, 1000);
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
    bindPipelineControl();
    bindBulkRestart();

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
      renderSpeedtest(data.speedtest);
    } catch (e) { console.warn("storage poll failed", e); }
  }

  function renderSpeedtest(st) {
    const el = document.querySelector('[data-field="speedtest_line"]');
    if (!el) return;
    if (!st) { el.textContent = "Noch keine Messung."; return; }
    if (!st.ok) {
      el.textContent = `Letzter Speedtest fehlgeschlagen: ${st.error || ""}`;
      return;
    }
    const when = (st.measured_at || "").replace("T", " ").slice(0, 16);
    let txt = `↓ ${st.download_mbit_s} · ↑ ${st.upload_mbit_s} Mbit/s` +
              ` · ${st.ping_ms} ms · ${when}`;
    // Stale marker if older than 48 h.
    const t = Date.parse(st.measured_at);
    if (!isNaN(t) && (Date.now() - t) > 48 * 3600 * 1000) {
      txt += " (veraltet – System war durchgehend beschaeftigt?)";
    }
    el.textContent = txt;
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

  // ----- M2: Pause/Resume + Bulk Restart --------------------------------
  function bindPipelineControl() {
    document.querySelectorAll('[data-action="pipeline-pause"]').forEach(btn => {
      btn.addEventListener('click', async () => {
        const panel = btn.closest('.tabpanel');
        const discipline = panel.dataset.discipline;
        await fetch(`/api/pipeline/${discipline}/pause`,
          { method: 'POST', credentials: 'same-origin' });
        pollState(panel);
      });
    });
    document.querySelectorAll('[data-action="pipeline-resume"]').forEach(btn => {
      btn.addEventListener('click', async () => {
        const panel = btn.closest('.tabpanel');
        const discipline = panel.dataset.discipline;
        await fetch(`/api/pipeline/${discipline}/resume`,
          { method: 'POST', credentials: 'same-origin' });
        pollState(panel);
      });
    });
  }

  function bindBulkRestart() {
    document.querySelectorAll('.tabpanel').forEach(panel => {
      const selectAll = panel.querySelector('[data-bulk-select-all]');
      if (selectAll) {
        selectAll.addEventListener('change', () => {
          panel.querySelectorAll('.bulk-select').forEach(cb => {
            cb.checked = selectAll.checked;
          });
          updateBulkToolbar(panel);
        });
      }
      const clearBtn = panel.querySelector('[data-action="bulk-clear"]');
      if (clearBtn) {
        clearBtn.addEventListener('click', () => {
          panel.querySelectorAll('.bulk-select').forEach(cb => cb.checked = false);
          if (selectAll) selectAll.checked = false;
          updateBulkToolbar(panel);
        });
      }
      const restartBtn = panel.querySelector('[data-action="bulk-restart"]');
      if (restartBtn) {
        restartBtn.addEventListener('click', async () => {
          const discipline = panel.dataset.discipline;
          const phaseSel = panel.querySelector('[data-bulk-phase]');
          const phase = phaseSel ? phaseSel.value : 'merge';
          const ids = Array.from(
            panel.querySelectorAll('.bulk-select:checked')
          ).map(cb => parseInt(cb.dataset.runId, 10));
          if (ids.length === 0) return;
          if (!confirm(
            `Restart ${ids.length} Jobs ab Phase "${phase}"?`
          )) return;
          await fetch(`/api/jobs/${discipline}/bulk-restart`, {
            method: 'POST', credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ run_ids: ids, from_phase: phase }),
          });
          if (selectAll) selectAll.checked = false;
          pollState(panel);
        });
      }
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
