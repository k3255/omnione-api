(() => {
  const DATA_URL = new URL("../data/categories.json", import.meta.url);

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

  function findCurrentCategory(categories, basePath) {
    const current = normalizePath(window.location.pathname);
    for (const category of categories) {
      for (const item of category.items) {
        if (pagePathVariants(item.path, basePath).has(current)) {
          return category.key;
        }
      }
    }
    return categories[0]?.key ?? "";
  }

  function createLink(basePath, itemPath) {
    const clean = itemPath.replace(/^\//, "").replace(/\.md$/i, "");
    return `${basePath}${clean}/`;
  }

  function renderList(listEl, category, basePath) {
    listEl.replaceChildren();

    for (const item of category.items) {
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

  function mountCategoryFilter(categories) {
    const sidebarNav = document.querySelector(".md-sidebar--primary .md-sidebar__scrollwrap");
    if (!sidebarNav || !categories.length) return;

    const container = document.createElement("section");
    container.className = "category-filter";

    const title = document.createElement("div");
    title.className = "category-filter__title";
    title.textContent = "Browse By Category";

    const select = document.createElement("select");
    select.className = "category-filter__select";

    for (const category of categories) {
      const option = document.createElement("option");
      option.value = category.key;
      option.textContent = `${category.label} (${category.items.length})`;
      select.appendChild(option);
    }

    const list = document.createElement("ul");
    list.className = "category-filter__list";

    container.appendChild(title);
    container.appendChild(select);
    container.appendChild(list);
    sidebarNav.prepend(container);

    const basePath = currentBasePath();
    const initial = findCurrentCategory(categories, basePath);
    if (initial) {
      select.value = initial;
    }

    const refresh = () => {
      const category = categories.find((entry) => entry.key === select.value);
      if (category) {
        renderList(list, category, basePath);
      }
    };

    select.addEventListener("change", refresh);
    refresh();
  }

  document.addEventListener("DOMContentLoaded", async () => {
    try {
      const response = await fetch(DATA_URL);
      if (!response.ok) return;
      const payload = await response.json();
      mountCategoryFilter(payload.categories || []);
    } catch (_error) {
      // Ignore sidebar enhancement failures and keep the default nav usable.
    }
  });
})();
