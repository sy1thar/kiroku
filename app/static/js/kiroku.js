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

  // Global Background Loop Poller & Mini-Window (Admin only)
  const miniBox = document.getElementById("kiroku-loop-mini");
  if (miniBox) {
    const miniLocale = miniBox.dataset.locale || "ru";
    const miniDot = document.getElementById("loop-mini-dot");
    const miniTitle = document.getElementById("loop-mini-title");
    const miniStop = document.getElementById("loop-mini-stop");
    const miniClose = document.getElementById("loop-mini-close");
    const miniInfo = document.getElementById("loop-mini-info");
    const miniBar = document.getElementById("loop-mini-bar");
    const miniStats = document.getElementById("loop-mini-stats");
    const miniLink = document.getElementById("loop-mini-link");
    const listeners = [];
    let pollTimer = null;
    let dismissed = false;
    let seenActive = false;

    const kindNames = {
      titles: miniLocale === "ru" ? "Импорт тайтлов" : "Titles Loop",
      characters_all: miniLocale === "ru" ? "Импорт персонажей" : "Characters Loop",
      media_characters: miniLocale === "ru" ? "Персонажи тайтла" : "Title Characters",
      translate: miniLocale === "ru" ? "Локализация (RU)" : "Localization",
    };

    function renderMini(data) {
      if (!data) return;
      if (data.active) {
        dismissed = false;
        seenActive = true;
      }
      const shouldShow = data.active || (seenActive && !dismissed && data.status && data.status !== "idle");
      miniBox.style.display = shouldShow ? "flex" : "none";
      if (shouldShow) {
        miniTitle.textContent = kindNames[data.kind] || (miniLocale === "ru" ? "Цикл" : "Loop");
        miniDot.style.display = data.active ? "inline-block" : "none";
        miniStop.style.display = data.active ? "inline-flex" : "none";
        miniStop.disabled = Boolean(data.stop_requested);
        miniClose.style.display = data.active ? "none" : "inline-flex";
        let statusTag = "";
        if (data.status === "completed") statusTag = miniLocale === "ru" ? "✅ Готово · " : "✅ Done · ";
        else if (data.status === "stopped") statusTag = miniLocale === "ru" ? "⏹ Остановлено · " : "⏹ Stopped · ";
        else if (data.status === "error") statusTag = "⚠ ";
        miniInfo.textContent = statusTag + (data.current_label || "");
        miniBar.style.width = `${data.progress_pct || 0}%`;
        miniStats.textContent =
          (miniLocale === "ru" ? "Волна #" : "Wave #") +
          (data.wave || 0) +
          " · +" +
          (data.added_total || 0);
        if (data.kind === "media_characters" && data.media_slug) {
          miniLink.href = `/${miniLocale}/media/${data.media_slug}`;
        } else if (data.kind === "translate") {
          miniLink.href = `/${miniLocale}/admin`;
        } else {
          miniLink.href = `/${miniLocale}/import`;
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
          renderMini(data);
          if (data.active) {
            pollTimer = setTimeout(pollLoopStatus, 1400);
          }
        }
      } catch (e) {
        console.error(e);
      }
    }

    window.KirokuLoop = {
      async start(payload) {
        dismissed = false;
        seenActive = true;
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
        renderMini(data);
        clearTimeout(pollTimer);
        pollTimer = setTimeout(pollLoopStatus, 1000);
        return { ok: true, data };
      },
      async stop() {
        miniStop.disabled = true;
        const res = await fetch("/api/admin/loop/stop", { method: "POST" });
        const data = await res.json().catch(() => ({}));
        renderMini(data);
        clearTimeout(pollTimer);
        pollTimer = setTimeout(pollLoopStatus, 800);
        return data;
      },
      dismiss() {
        dismissed = true;
        miniBox.style.display = "none";
      },
      refresh: pollLoopStatus,
      onStatus(fn) {
        listeners.push(fn);
      },
    };

    pollLoopStatus();
  }
})();
