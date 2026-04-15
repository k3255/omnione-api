(() => {
  const FALLBACK_BRANCHES = ["develop", "main"];
  const BRANCH_STORAGE_KEY = "omnione-docs-branch";

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

  function normalizeBranches(payload) {
    const branches = Array.isArray(payload.branches) && payload.branches.length
      ? payload.branches
      : FALLBACK_BRANCHES;

    return branches.map(String).filter(Boolean);
  }

  function readStoredBranch(branches) {
    try {
      const stored = window.localStorage.getItem(BRANCH_STORAGE_KEY);
      return branches.includes(stored) ? stored : null;
    } catch (_error) {
      return null;
    }
  }

  function storeBranch(branch) {
    try {
      window.localStorage.setItem(BRANCH_STORAGE_KEY, branch);
    } catch (_error) {
      // Ignore storage failures; navigation still works.
    }
  }

  function branchFromPath(branches) {
    const marker = "/collected/";
    const idx = window.location.pathname.indexOf(marker);
    if (idx < 0) return null;

    const rest = window.location.pathname.slice(idx + marker.length);
    const branch = decodeURIComponent(rest.split("/")[0] || "");
    return branches.includes(branch) ? branch : null;
  }

  function selectedBranch(branches, defaultBranch) {
    return branchFromPath(branches)
      || readStoredBranch(branches)
      || (branches.includes(defaultBranch) ? defaultBranch : null)
      || branches[0];
  }

  function currentDocPath(basePath) {
    let path = decodeURIComponent(window.location.pathname);
    if (path.startsWith(basePath)) {
      path = path.slice(basePath.length);
    }

    path = path.replace(/^\/+|\/+$/g, "");
    if (!path) return "index.md";
    if (/^collected\/[^/]+$/i.test(path)) return `${path}/index.md`;
    return path.endsWith(".md") ? path : `${path}.md`;
  }

  function branchOverviewPath(branch) {
    return `collected/${branch}/index.md`;
  }

  function targetBranchPath(basePath, items, currentBranch, targetBranch) {
    const currentPath = currentDocPath(basePath);
    const currentPrefix = `collected/${currentBranch}/`;
    const itemPaths = new Set(items.map((item) => item.path));

    if (currentPath.startsWith(currentPrefix)) {
      const nextPath = `collected/${targetBranch}/${currentPath.slice(currentPrefix.length)}`;
      if (itemPaths.has(nextPath) || nextPath === branchOverviewPath(targetBranch)) {
        return nextPath;
      }
    }

    return branchOverviewPath(targetBranch);
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

  function createLink(basePath, itemPath) {
    const clean = itemPath
      .replace(/^\//, "")
      .replace(/\.md$/i, "")
      .replace(/\/index$/i, "");
    return `${basePath}${clean}/`;
  }

  function matchesAllTokens(item, tokens) {
    if (!tokens.length) return true;
    return tokens.every((token) => item.searchText.includes(token));
  }

  function renderList(listEl, items, basePath) {
    listEl.replaceChildren();

    for (const item of items) {
      const entry = document.createElement("li");
      entry.className = "category-filter__item";

      const link = document.createElement("a");
      link.className = "category-filter__link";
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

  function mountCategoryFilter(items, branch) {
    const sidebarNav = document.querySelector(".md-sidebar--primary .md-sidebar__scrollwrap");
    if (!sidebarNav) return;

    const existing = sidebarNav.querySelector(".category-filter");
    if (existing) {
      existing.remove();
    }

    const branchItems = items.filter((item) => item.branch === branch);
    if (!branchItems.length) return;

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

    const refresh = () => {
      const query = input.value.trim();
      const tokens = tokenizeQuery(query);
      const matched = branchItems.filter((item) => matchesAllTokens(item, tokens));

      count.textContent = `${matched.length} / ${branchItems.length} files`;

      if (matched.length) {
        renderList(list, matched, basePath);
        return;
      }

      renderEmptyState(list, query);
    };

    let expanded = false;
    setExpandedState(container, toggle, expanded);

    toggle.addEventListener("click", () => {
      expanded = !expanded;
      setExpandedState(container, toggle, expanded);
    });

    input.addEventListener("input", refresh);
    refresh();
  }

  function mountBranchSelector(branches, branch, items) {
    const header = document.querySelector(".md-header__inner");
    if (!header || !branches.length) return;

    const existing = header.querySelector(".branch-selector");
    if (existing) {
      existing.remove();
    }

    const wrapper = document.createElement("label");
    wrapper.className = "branch-selector";

    const label = document.createElement("span");
    label.className = "branch-selector__label";
    label.textContent = "Branch";

    const select = document.createElement("select");
    select.className = "branch-selector__select";
    select.setAttribute("aria-label", "Repository branch");

    for (const optionBranch of branches) {
      const option = document.createElement("option");
      option.value = optionBranch;
      option.textContent = optionBranch;
      select.appendChild(option);
    }

    select.value = branch;
    select.addEventListener("change", () => {
      const nextBranch = select.value;
      storeBranch(nextBranch);
      const basePath = currentBasePath();
      const destination = targetBranchPath(basePath, items, branch, nextBranch);
      window.location.assign(createLink(basePath, destination));
    });

    wrapper.appendChild(label);
    wrapper.appendChild(select);
    header.appendChild(wrapper);
  }

  async function bootstrap() {
    try {
      const response = await fetch(dataUrl());
      if (!response.ok) {
        const branch = selectedBranch(FALLBACK_BRANCHES, FALLBACK_BRANCHES[0]);
        mountBranchSelector(FALLBACK_BRANCHES, branch, []);
        return;
      }
      const payload = await response.json();
      const branches = normalizeBranches(payload);
      const branch = selectedBranch(branches, payload.defaultBranch);
      const items = (payload.items || []).map((item) => ({
        ...item,
        searchText: `${item.repo} ${item.title} ${item.path}`.toLowerCase(),
      }));
      mountBranchSelector(branches, branch, items);
      mountCategoryFilter(items, branch);
    } catch (_error) {
      const branch = selectedBranch(FALLBACK_BRANCHES, FALLBACK_BRANCHES[0]);
      mountBranchSelector(FALLBACK_BRANCHES, branch, []);
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
