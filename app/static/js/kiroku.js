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
        window.location.href = `/${locale}/catalog?q=${encodeURIComponent(input.value.trim())}`;
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

  // Apply saved site display preferences
  if (localStorage.getItem("kiroku_compact_cards") === "1") {
    document.body.classList.add("compact-cards");
  }

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
})();
