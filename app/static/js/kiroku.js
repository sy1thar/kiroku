(function () {
  const input = document.getElementById("global-search-input");
  const dropdown = document.getElementById("global-search-dropdown");
  const toastEl = document.getElementById("kiroku-toast");

  function showToast(msg) {
    if (!toastEl) return;
    toastEl.textContent = msg;
    toastEl.classList.add("show");
    setTimeout(() => toastEl.classList.remove("show"), 3200);
  }

  window.kirokuToast = showToast;

  function applySitePrefs() {
    const accent = localStorage.getItem("kiroku_accent");
    if (accent) {
      document.documentElement.style.setProperty("--accent", accent);
      document.documentElement.style.setProperty("--accent-glow", accent + "40");
    } else {
      document.documentElement.style.removeProperty("--accent");
      document.documentElement.style.removeProperty("--accent-glow");
    }
    const radius = localStorage.getItem("kiroku_radius");
    if (radius) {
      document.documentElement.style.setProperty("--radius", radius);
    } else {
      document.documentElement.style.removeProperty("--radius");
    }
    const density = localStorage.getItem("kiroku_density") || (localStorage.getItem("kiroku_compact_cards") === "1" ? "compact" : "normal");
    document.body.classList.toggle("compact-cards", density === "compact");
    document.body.classList.toggle("large-cards", density === "large");
    document.body.classList.toggle("no-glow", localStorage.getItem("kiroku_glow") === "0");
    document.body.classList.toggle("no-animations", localStorage.getItem("kiroku_animations") === "0");
    document.body.classList.toggle("hide-ratings", localStorage.getItem("kiroku_show_ratings") === "0");
    document.body.classList.toggle("hide-badges", localStorage.getItem("kiroku_show_badges") === "0");
    document.body.classList.toggle("hide-cosmetics", localStorage.getItem("kiroku_show_cosmetics") === "0");
  }

  window.applyKirokuSitePrefs = applySitePrefs;
  applySitePrefs();

  // Keyboard shortcuts: / or Ctrl+K
  document.addEventListener("keydown", (e) => {
    if (!input) return;
    if ((e.ctrlKey && e.key.toLowerCase() === "k") || (e.key === "/" && document.activeElement.tagName !== "INPUT" && document.activeElement.tagName !== "TEXTAREA")) {
      e.preventDefault();
      input.focus();
    }
    if (e.key === "Escape" && dropdown) {
      dropdown.classList.remove("open");
    }
  });

  if (input && dropdown) {
    let timer = null;
    const locale = input.dataset.locale || "ru";

    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && input.value.trim().length > 0) {
        e.preventDefault();
        const targetPage = localStorage.getItem("kiroku_search_target") === "characters" ? "characters" : "catalog";
        window.location.href = `/${locale}/${targetPage}?q=${encodeURIComponent(input.value.trim())}`;
      }
    });

    input.addEventListener("input", () => {
      clearTimeout(timer);
      const q = input.value.trim();
      if (q.length < 2) {
        dropdown.classList.remove("open");
        dropdown.innerHTML = "";
        return;
      }
      timer = setTimeout(async () => {
        try {
          const res = await fetch(`/api/search?q=${encodeURIComponent(q)}&locale=${encodeURIComponent(locale)}&limit=6`);
          const data = await res.json();
          renderDropdown(data, locale);
        } catch (err) {
          console.error(err);
        }
      }, 180);
    });

    document.addEventListener("click", (e) => {
      if (!dropdown.contains(e.target) && e.target !== input) {
        dropdown.classList.remove("open");
      }
    });

    function renderDropdown(data, loc) {
      let html = "";
      if (data.characters && data.characters.length) {
        html += `<div class="search-group-title">${loc === "ru" ? "Персонажи" : "Characters"}</div>`;
        for (const c of data.characters) {
          html += `<a class="search-item" href="/${loc}/characters/${c.slug}">
            <span>${escapeHtml(c.name)}</span>
            <span class="badge">${escapeHtml(c.mbti || c.hair_color)}</span>
          </a>`;
        }
      }
      if (data.media && data.media.length) {
        html += `<div class="search-group-title">${loc === "ru" ? "Тайтлы" : "Media"}</div>`;
        for (const m of data.media) {
          html += `<a class="search-item" href="/${loc}/media/${m.slug}">
            <span>${escapeHtml(m.title)}</span>
            <span class="badge badge-${escapeHtml(m.media_type)}">${escapeHtml(m.media_type)}</span>
          </a>`;
        }
      }
      if (!html) {
        html = `<div class="search-item" style="color: var(--muted)">${loc === "ru" ? "Ничего не найдено" : "No matches"}</div>`;
      }
      dropdown.innerHTML = html;
      dropdown.classList.add("open");
    }
  }

  function escapeHtml(s) {
    return String(s || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  // Trait Upvote Toggle Buttons (1 vote per user)
  document.querySelectorAll("[data-vote-trait]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const characterId = Number(btn.dataset.characterId);
      const traitSlug = btn.dataset.voteTrait;
      const res = await fetch("/api/traits/vote", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ character_id: characterId, trait_slug: traitSlug }),
      });
      if (res.ok) {
        const data = await res.json();
        const countEl = btn.querySelector(".trait-count");
        if (countEl) countEl.textContent = data.votes_count;
        btn.classList.toggle("voted", Boolean(data.voted));
        const loc = document.documentElement.lang || "ru";
        showToast(data.voted ? (loc === "ru" ? "Голос учтён" : "Vote added") : (loc === "ru" ? "Голос снят" : "Vote removed"));
      }
    });
  });

  // Anti-Spoiler Toggle + Auto-Reveal preference
  const spoilerBtn = document.getElementById("spoiler-toggle-btn");
  const spoilerBody = document.getElementById("spoiler-body");
  if (spoilerBtn && spoilerBody) {
    if (localStorage.getItem("kiroku_auto_reveal_spoilers") === "1") {
      spoilerBody.classList.remove("blurred");
      spoilerBtn.textContent = spoilerBtn.dataset.hideText;
    }
    spoilerBtn.addEventListener("click", () => {
      const hidden = spoilerBody.classList.toggle("blurred");
      spoilerBtn.textContent = hidden ? spoilerBtn.dataset.showText : spoilerBtn.dataset.hideText;
    });
  }

  // Wiki Revision Form
  const revForm = document.getElementById("wiki-revision-form");
  if (revForm) {
    revForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const fd = new FormData(revForm);
      const payload = {
        entity_type: fd.get("entity_type"),
        entity_id: Number(fd.get("entity_id")),
        locale: fd.get("locale"),
        author_name: fd.get("author_name") || "Community Contributor",
        field_name: fd.get("field_name"),
        proposed_value: fd.get("proposed_value"),
        reason: fd.get("reason") || "",
      };
      const res = await fetch("/api/revisions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (res.ok) {
        revForm.reset();
        showToast(payload.locale === "ru" ? "Правка отправлена в очередь модерации!" : "Revision submitted for moderation!");
      }
    });
  }

  // Personal Kiroku Tracker Form
  const trackerForm = document.getElementById("kiroku-tracker-form");
  if (trackerForm) {
    trackerForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const fd = new FormData(trackerForm);
      const scoreRaw = fd.get("score");
      const payload = {
        media_id: Number(fd.get("media_id")),
        status: fd.get("status"),
        progress_unit: Number(fd.get("progress_unit") || 0),
        score: scoreRaw ? Number(scoreRaw) : null,
        is_favorite: fd.get("is_favorite") === "on",
        notes: fd.get("notes") || "",
      };
      const res = await fetch("/api/lists", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (res.ok) {
        showToast("Saved to Kiroku!");
      }
    });
  }

  // Global Concurrent Draggable Loop Widgets & Poller (Admin only)
  const dockEl = document.getElementById("kiroku-loop-dock");
  if (dockEl) {
    const miniLocale = dockEl.dataset.locale || "ru";
    const listeners = [];
    const widgetEls = new Map();
    let pollTimer = null;

    const kindNames = {
      titles: miniLocale === "ru" ? "Импорт тайтлов" : "Titles Loop",
      characters_all: miniLocale === "ru" ? "Импорт персонажей" : "Characters Loop",
      media_characters: miniLocale === "ru" ? "Персонажи тайтла" : "Title Characters",
      translate: miniLocale === "ru" ? "Локализация (RU)" : "Localization",
      repair: miniLocale === "ru" ? "Починка переводов" : "Repair Translations",
    };

    function applySavedOrStackedPos(el, jobId, idx) {
      const saved = localStorage.getItem(`kiroku_widget_pos_${jobId}`);
      if (saved) {
        try {
          const { left, top } = JSON.parse(saved);
          const maxL = Math.max(8, window.innerWidth - 320);
          const maxT = Math.max(8, window.innerHeight - 130);
          el.style.left = `${Math.min(Math.max(8, left), maxL)}px`;
          el.style.top = `${Math.min(Math.max(8, top), maxT)}px`;
          el.style.right = "auto";
          el.style.bottom = "auto";
          return;
        } catch {}
      }
      el.style.right = "18px";
      el.style.bottom = `${18 + idx * 124}px`;
      el.style.left = "auto";
      el.style.top = "auto";
    }

    function makeDraggable(el, handle, jobId) {
      let dragging = false;
      let startX = 0, startY = 0, origLeft = 0, origTop = 0;

      handle.addEventListener("pointerdown", (e) => {
        if (e.target.closest("button") || e.target.closest("a")) return;
        e.preventDefault();
        const rect = el.getBoundingClientRect();
        dragging = true;
        startX = e.clientX;
        startY = e.clientY;
        origLeft = rect.left;
        origTop = rect.top;
        el.classList.add("dragging");
        el.style.left = `${origLeft}px`;
        el.style.top = `${origTop}px`;
        el.style.right = "auto";
        el.style.bottom = "auto";
        handle.setPointerCapture(e.pointerId);
      });

      handle.addEventListener("pointermove", (e) => {
        if (!dragging) return;
        const maxL = Math.max(8, window.innerWidth - el.offsetWidth - 8);
        const maxT = Math.max(8, window.innerHeight - el.offsetHeight - 8);
        const nextL = Math.min(Math.max(8, origLeft + (e.clientX - startX)), maxL);
        const nextT = Math.min(Math.max(8, origTop + (e.clientY - startY)), maxT);
        el.style.left = `${nextL}px`;
        el.style.top = `${nextT}px`;
      });

      const endDrag = () => {
        if (!dragging) return;
        dragging = false;
        el.classList.remove("dragging");
        localStorage.setItem(
          `kiroku_widget_pos_${jobId}`,
          JSON.stringify({ left: parseFloat(el.style.left), top: parseFloat(el.style.top) })
        );
      };
      handle.addEventListener("pointerup", endDrag);
      handle.addEventListener("pointercancel", endDrag);
    }

    function createWidgetEl(jobId, idx) {
      const el = document.createElement("div");
      el.className = "loop-mini-widget";
      el.dataset.jobId = jobId;
      el.innerHTML = `
        <div class="loop-mini-head" title="${miniLocale === "ru" ? "Перетащите мышкой" : "Drag to move"}">
          <div style="display:flex; align-items:center; gap:7px; min-width:0;">
            <span style="color:var(--muted); font-size:0.78rem; letter-spacing:-1px;">⋮⋮</span>
            <span class="loop-mini-dot"></span>
            <span class="w-title" style="white-space:nowrap; overflow:hidden; text-overflow:ellipsis;"></span>
          </div>
          <div style="display:flex; align-items:center; gap:6px;">
            <button type="button" class="btn btn-secondary btn-sm w-stop" style="display:none; color:#fda4af; padding:3px 9px; font-size:0.76rem;">${miniLocale === "ru" ? "⏹ Стоп" : "⏹ Stop"}</button>
            <button type="button" class="btn btn-secondary btn-sm w-close" style="display:none; padding:2px 8px; font-size:0.78rem;">×</button>
          </div>
        </div>
        <div class="w-info" style="color:var(--muted); white-space:nowrap; overflow:hidden; text-overflow:ellipsis;"></div>
        <div class="loop-mini-bar-wrap"><div class="loop-mini-bar w-bar"></div></div>
        <div style="display:flex; align-items:center; justify-content:space-between; font-size:0.78rem; color:var(--muted);">
          <span class="w-stats"></span>
          <a class="w-link" href="/${miniLocale}/import" style="color:var(--accent);">${miniLocale === "ru" ? "Открыть →" : "Open →"}</a>
        </div>
      `;
      el.querySelector(".w-stop").addEventListener("click", () => window.KirokuLoop.stop(jobId));
      el.querySelector(".w-close").addEventListener("click", () => window.KirokuLoop.dismiss(jobId));
      makeDraggable(el, el.querySelector(".loop-mini-head"), jobId);
      applySavedOrStackedPos(el, jobId, idx);
      dockEl.appendChild(el);
      widgetEls.set(jobId, el);
      return el;
    }

    function renderAllWidgets(data) {
      if (!data) return;
      const jobs = Array.isArray(data.jobs) ? data.jobs : (data.status && data.status !== "idle" ? [data] : []);
      const visibleIds = new Set();

      jobs.forEach((job, idx) => {
        if (!job.job_id || job.status === "idle") return;
        visibleIds.add(job.job_id);
        let el = widgetEls.get(job.job_id);
        if (!el) {
          el = createWidgetEl(job.job_id, idx);
        }
        const baseName = kindNames[job.kind] || (miniLocale === "ru" ? "Цикл" : "Loop");
        el.querySelector(".w-title").textContent = job.title_hint ? `${baseName}: ${job.title_hint}` : baseName;
        el.querySelector(".loop-mini-dot").style.display = job.active ? "inline-block" : "none";
        const stopBtn = el.querySelector(".w-stop");
        stopBtn.style.display = job.active ? "inline-flex" : "none";
        stopBtn.disabled = Boolean(job.stop_requested);
        el.querySelector(".w-close").style.display = job.active ? "none" : "inline-flex";

        let statusTag = "";
        if (job.status === "completed") statusTag = miniLocale === "ru" ? "✅ Готово · " : "✅ Done · ";
        else if (job.status === "stopped") statusTag = miniLocale === "ru" ? "⏹ Остановлено · " : "⏹ Stopped · ";
        else if (job.status === "error") statusTag = "⚠ ";
        el.querySelector(".w-info").textContent = statusTag + (job.current_label || "");
        el.querySelector(".w-bar").style.width = `${job.progress_pct || 0}%`;
        el.querySelector(".w-stats").textContent =
          (miniLocale === "ru" ? "Волна #" : "Wave #") + (job.wave || 0) + " · +" + (job.added_total || 0);

        const link = el.querySelector(".w-link");
        if (job.kind === "media_characters" && job.media_slug) {
          link.href = `/${miniLocale}/media/${job.media_slug}`;
        } else if (job.kind === "translate" || job.kind === "repair") {
          link.href = `/${miniLocale}/admin`;
        } else {
          link.href = `/${miniLocale}/import`;
        }
      });

      for (const [jid, el] of widgetEls.entries()) {
        if (!visibleIds.has(jid)) {
          el.remove();
          widgetEls.delete(jid);
        }
      }

      listeners.forEach((fn) => {
        try { fn(data); } catch (e) { console.error(e); }
      });
    }

    async function pollLoopStatus() {
      clearTimeout(pollTimer);
      try {
        const res = await fetch("/api/admin/loop/status", { cache: "no-store" });
        if (res.ok) {
          const data = await res.json();
          renderAllWidgets(data);
          if (data.any_active || data.active) {
            pollTimer = setTimeout(pollLoopStatus, 1400);
          }
        }
      } catch (e) {
        console.error(e);
      }
    }

    window.KirokuLoop = {
      async start(payload) {
        const res = await fetch("/api/admin/loop/start", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          showToast(data.detail || "Failed to start loop");
          return { ok: false, data };
        }
        await pollLoopStatus();
        return { ok: true, data };
      },
      async stop(jobId) {
        const url = jobId ? `/api/admin/loop/stop?job_id=${encodeURIComponent(jobId)}` : "/api/admin/loop/stop";
        const res = await fetch(url, { method: "POST" });
        const data = await res.json().catch(() => ({}));
        renderAllWidgets(data);
        clearTimeout(pollTimer);
        pollTimer = setTimeout(pollLoopStatus, 800);
        return data;
      },
      async dismiss(jobId) {
        const url = jobId ? `/api/admin/loop/dismiss?job_id=${encodeURIComponent(jobId)}` : "/api/admin/loop/dismiss";
        const res = await fetch(url, { method: "POST" });
        const data = await res.json().catch(() => ({}));
        renderAllWidgets(data);
        return data;
      },
      refresh: pollLoopStatus,
      onStatus(fn) {
        listeners.push(fn);
      },
    };

    pollLoopStatus();
  }
})();
