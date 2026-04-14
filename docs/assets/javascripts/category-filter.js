(() => {
  function normalizePath(pathname) {
    if (!pathname) return "/";
    return pathname.endsWith("/") ? pathname : `${pathname}/`;
  }

  function pagePathVariants(itemPath, basePath) {
    const clean = itemPath.replace(/^\//, "").replace(/\.md$/i, "");
    return new Set([
      normalizePath(`${basePath}${clean}/`),
      normalizePath(`${basePath}${clean}/index.html`),
    ]);
  }

  function currentBasePath() {
    const marker = "/collected/";
    const pathname = window.location.pathname;
    const idx = pathname.indexOf(marker);
    if (idx >= 0) {
      return pathname.slice(0, idx + 1);
    }
    return pathname.endsWith("/") ? pathname : pathname.replace(/[^/]+$/, "");
  }

  function dataUrl() {
    return `${currentBasePath()}assets/data/doc-index.json`;
  }

  function normalizeToken(value) {
    return value.trim().toLowerCase();
  }

  function tokenizeQuery(value) {
    return value
      .split(/\s+/)
      .map(normalizeToken)
      .filter(Boolean);
  }

  function findCurrentPath(items, basePath) {
    const current = normalizePath(window.location.pathname);
    for (const item of items) {
      if (pagePathVariants(item.path, basePath).has(current)) {
        return item.path;
      }
    }
    return "";
  }

  function createLink(basePath, itemPath) {
    const clean = itemPath.replace(/^\//, "").replace(/\.md$/i, "");
    return `${basePath}${clean}/`;
  }

  function matchesAllTokens(item, tokens) {
    if (!tokens.length) return true;
    return tokens.every((token) => item.searchText.includes(token));
  }

  function renderList(listEl, items, basePath, currentPath) {
    listEl.replaceChildren();

    for (const item of items) {
      const entry = document.createElement("li");
      entry.className = "category-filter__item";

      const link = document.createElement("a");
      link.className = "category-filter__link";
      if (item.path === currentPath) {
        link.classList.add("category-filter__link--active");
      }
      link.href = createLink(basePath, item.path);
      link.textContent = `${item.repo} / ${item.title}`;

      entry.appendChild(link);
      listEl.appendChild(entry);
    }
  }

  function renderEmptyState(listEl, query) {
    listEl.replaceChildren();

    const entry = document.createElement("li");
    entry.className = "category-filter__empty";
    entry.textContent = query
      ? `No files matched "${query}".`
      : "No files available.";

    listEl.appendChild(entry);
  }

  function setExpandedState(container, toggle, expanded) {
    container.classList.toggle("category-filter--collapsed", !expanded);
    toggle.setAttribute("aria-expanded", String(expanded));
    toggle.textContent = expanded ? "Hide list" : "Show list";
  }

  function mountCategoryFilter(items) {
    const sidebarNav = document.querySelector(".md-sidebar--primary .md-sidebar__scrollwrap");
    if (!sidebarNav || !items.length) return;

    const existing = sidebarNav.querySelector(".category-filter");
    if (existing) {
      existing.remove();
    }

    const container = document.createElement("section");
    container.className = "category-filter";

    const title = document.createElement("div");
    title.className = "category-filter__title";
    title.textContent = "Filter Files";

    const description = document.createElement("p");
    description.className = "category-filter__description";
    description.textContent = "Type one or more terms. All terms must match.";

    const input = document.createElement("input");
    input.className = "category-filter__input";
    input.type = "search";
    input.placeholder = "e.g. API ko";
    input.autocomplete = "off";

    const count = document.createElement("div");
    count.className = "category-filter__count";

    const toggle = document.createElement("button");
    toggle.className = "category-filter__toggle";
    toggle.type = "button";

    const list = document.createElement("ul");
    list.className = "category-filter__list";

    container.appendChild(title);
    container.appendChild(description);
    container.appendChild(input);
    container.appendChild(count);
    container.appendChild(toggle);
    container.appendChild(list);
    sidebarNav.prepend(container);

    const basePath = currentBasePath();
    const currentPath = findCurrentPath(items, basePath);

    const refresh = () => {
      const query = input.value.trim();
      const tokens = tokenizeQuery(query);
      const matched = items.filter((item) => matchesAllTokens(item, tokens));

      count.textContent = `${matched.length} / ${items.length} files`;

      if (matched.length) {
        renderList(list, matched, basePath, currentPath);
        return;
      }

      renderEmptyState(list, query);
    };

    let expanded = true;
    setExpandedState(container, toggle, expanded);

    toggle.addEventListener("click", () => {
      expanded = !expanded;
      setExpandedState(container, toggle, expanded);
    });

    input.addEventListener("input", refresh);
    refresh();
  }

  async function bootstrap() {
    try {
      const response = await fetch(dataUrl());
      if (!response.ok) return;
      const payload = await response.json();
      const items = (payload.items || []).map((item) => ({
        ...item,
        searchText: `${item.repo} ${item.title} ${item.path}`.toLowerCase(),
      }));
      mountCategoryFilter(items);
    } catch (_error) {
      // Ignore sidebar enhancement failures and keep the default nav usable.
    }
  }

  if (typeof document$ !== "undefined" && typeof document$.subscribe === "function") {
    document$.subscribe(() => {
      bootstrap();
    });
  } else if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bootstrap, { once: true });
  } else {
    bootstrap();
  }
})();
