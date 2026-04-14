import os
import time
import shutil
import json
import requests
from pathlib import Path
from urllib.parse import quote

ORG = "OmniOneID"
TARGET_DIR = Path("docs/collected")
DOCS_DIR = Path("docs")
MKDOCS_CONFIG = Path("mkdocs.yml")
CATEGORIES_DIR = DOCS_DIR / "categories"
ASSETS_DATA_DIR = DOCS_DIR / "assets" / "data"
GITHUB_API = "https://api.github.com"

TOKEN = os.environ.get("DOCS_READ_TOKEN", "")

HEADERS = {
    "Accept": "application/vnd.github+json",
}
if TOKEN:
    HEADERS["Authorization"] = f"Bearer {TOKEN}"

TEXT_EXTENSIONS = {".md"}
ASSET_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp",
    ".json", ".yaml", ".yml"
}
ALL_EXTENSIONS = TEXT_EXTENSIONS | ASSET_EXTENSIONS
MAX_RETRIES = 3


class GitHubFetchError(RuntimeError):
    pass


def _rate_limit_wait_seconds(headers) -> int | None:
    remaining = headers.get("X-RateLimit-Remaining")
    reset = headers.get("X-RateLimit-Reset")
    if remaining == "0" and reset:
        return max(int(reset) - int(time.time()) + 3, 3)
    return None


def gh_get(url: str):
    for attempt in range(1, MAX_RETRIES + 1):
        resp = requests.get(url, headers=HEADERS, timeout=30)

        if resp.status_code == 200:
            return resp.json()

        if resp.status_code == 404:
            resp.raise_for_status()

        if resp.status_code == 403:
            wait_seconds = _rate_limit_wait_seconds(resp.headers)
            if wait_seconds is not None:
                print(f"[RATE LIMIT] waiting {wait_seconds}s...")
                time.sleep(wait_seconds)
                continue

            raise GitHubFetchError(
                f"GitHub API returned 403 for {url}. "
                "Check DOCS_READ_TOKEN permissions or org/repo visibility."
            )

        if resp.status_code in {429, 500, 502, 503, 504} and attempt < MAX_RETRIES:
            wait_seconds = attempt * 5
            print(f"[RETRY] {resp.status_code} from {url}, waiting {wait_seconds}s...")
            time.sleep(wait_seconds)
            continue

        resp.raise_for_status()

    raise GitHubFetchError(f"Exceeded retry limit while requesting {url}")


def raw_download(url: str) -> bytes:
    for attempt in range(1, MAX_RETRIES + 1):
        r = requests.get(url, headers=HEADERS, timeout=30)
        if r.status_code == 200:
            return r.content

        if r.status_code == 403:
            wait_seconds = _rate_limit_wait_seconds(r.headers)
            if wait_seconds is not None:
                print(f"[RATE LIMIT-DOWNLOAD] waiting {wait_seconds}s...")
                time.sleep(wait_seconds)
                continue

            raise GitHubFetchError(
                f"GitHub download returned 403 for {url}. "
                "Check DOCS_READ_TOKEN permissions or raw content access."
            )

        if r.status_code in {429, 500, 502, 503, 504} and attempt < MAX_RETRIES:
            wait_seconds = attempt * 5
            print(f"[RETRY-DOWNLOAD] {r.status_code} from {url}, waiting {wait_seconds}s...")
            time.sleep(wait_seconds)
            continue

        r.raise_for_status()

    raise GitHubFetchError(f"Exceeded retry limit while downloading {url}")


def save_binary(download_url: str, target_path: Path):
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_bytes(raw_download(download_url))


def list_org_repos(org: str):
    repos = []
    page = 1
    while True:
        url = f"{GITHUB_API}/orgs/{org}/repos?per_page=100&page={page}"
        data = gh_get(url)
        if not data:
            break
        repos.extend(data)
        page += 1
    return repos


def list_dir(owner: str, repo: str, path: str):
    encoded_path = quote(path, safe="/")
    url = f"{GITHUB_API}/repos/{owner}/{repo}/contents/{encoded_path}"
    try:
        return gh_get(url)
    except requests.HTTPError as e:
        if e.response.status_code == 404:
            return None
        raise


def _display_name(path: Path) -> str:
    parts = path.stem.replace("_", " ").replace("-", " ").split()
    if not parts:
        return path.stem
    return " ".join(part if any(c.isupper() for c in part) else part.capitalize() for part in parts)


def _nav_lines_for_dir(directory: Path, depth: int):
    lines = []

    for child in sorted(directory.iterdir(), key=lambda item: (item.is_file(), item.name.lower())):
        if child.name.startswith("."):
            continue

        label = _display_name(child)
        indent = "  " * depth

        if child.is_dir():
            child_lines = _nav_lines_for_dir(child, depth + 1)
            if child_lines:
                lines.append(f"{indent}- {label}:")
                lines.extend(child_lines)
        elif child.suffix.lower() == ".md":
            rel = child.relative_to(DOCS_DIR).as_posix()
            lines.append(f"{indent}- {label}: {rel}")

    return lines


def _category_name_for_repo_doc(md_file: Path) -> str | None:
    repo_root = md_file.parents[1]
    rel_parts = md_file.relative_to(repo_root).parts
    if len(rel_parts) < 2:
        return None
    return rel_parts[0]


def collect_categories(collected_repos):
    categories = {}

    for repo_name in sorted(collected_repos):
        repo_dir = TARGET_DIR / repo_name
        for md_file in sorted(repo_dir.rglob("*.md")):
            category = _category_name_for_repo_doc(md_file)
            if not category:
                continue
            categories.setdefault(category, []).append((repo_name, md_file))

    return categories


def build_category_indexes(categories):
    if CATEGORIES_DIR.exists():
        shutil.rmtree(CATEGORIES_DIR)
    CATEGORIES_DIR.mkdir(parents=True, exist_ok=True)

    lines = [
        "# Categories",
        "",
        f"총 {len(categories)}개 문서 카테고리를 제공합니다.",
        "",
    ]

    for category in sorted(categories):
        label = _display_name(Path(category))
        category_rel = Path("categories") / f"{category}.md"
        lines.append(f"- [{label}]({category_rel.as_posix()})")

        category_lines = [
            f"# {label}",
            "",
            f"`{category}` 카테고리에 속한 문서를 저장소별로 정리했습니다.",
            "",
        ]

        current_repo = None
        for repo_name, md_file in categories[category]:
            if repo_name != current_repo:
                category_lines.append(f"## {repo_name}")
                current_repo = repo_name

            rel = md_file.relative_to(DOCS_DIR)
            repo_root = md_file.parents[1]
            title_parts = md_file.relative_to(repo_root).parts[1:]
            title = _display_name(Path("/".join(title_parts)))
            category_lines.append(f"- [{title}]({rel.as_posix()})")

        (CATEGORIES_DIR / f"{category}.md").write_text("\n".join(category_lines) + "\n", encoding="utf-8")

    (DOCS_DIR / "categories.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[CATEGORY INDEX CREATED] {DOCS_DIR / 'categories.md'}")


def build_category_data(categories):
    ASSETS_DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"categories": []}

    for category in sorted(categories):
        label = _display_name(Path(category))
        items = []
        for repo_name, md_file in categories[category]:
            rel = md_file.relative_to(DOCS_DIR).as_posix()
            repo_root = md_file.parents[1]
            title_parts = md_file.relative_to(repo_root).parts[1:]
            title = _display_name(Path("/".join(title_parts)))
            items.append({
                "repo": repo_name,
                "title": title,
                "path": rel,
            })

        payload["categories"].append({
            "key": category,
            "label": label,
            "items": items,
        })

    output = ASSETS_DATA_DIR / "categories.json"
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[CATEGORY DATA CREATED] {output}")


def update_mkdocs_nav(collected_repos, categories):
    lines = [
        "site_name: OmniOne Unified Docs Portal",
        "site_description: Consolidated Markdown docs from OmniOneID repositories",
        "",
        "theme:",
        "  name: material",
        "  palette:",
        "    primary: orange",
        "    accent: deep orange",
        "  features:",
        "    - search.highlight",
        "    - search.suggest",
        "",
        "nav:",
        "  - Home: index.md",
        "  - Collected Docs: collected-index.md",
        "  - Categories: categories.md",
    ]

    if collected_repos:
        lines.append("  - By Repository:")
        for repo_name in sorted(collected_repos):
            repo_dir = TARGET_DIR / repo_name
            repo_nav = _nav_lines_for_dir(repo_dir, 4)
            if repo_nav:
                lines.append(f"      - {repo_name}:")
                lines.extend(repo_nav)

    lines.extend([
        "",
        "plugins:",
        "  - search",
        "",
        "extra_javascript:",
        "  - assets/javascripts/category-filter.js",
        "",
        "extra_css:",
        "  - assets/stylesheets/category-filter.css",
        "",
        "markdown_extensions:",
        "  - toc:",
        "      permalink: true",
        "  - tables",
        "  - fenced_code",
        "",
    ])

    MKDOCS_CONFIG.write_text("\n".join(lines), encoding="utf-8")
    print(f"[MKDOCS NAV UPDATED] {MKDOCS_CONFIG}")


def collect_docs_recursive(owner: str, repo: str, path: str, repo_target_root: Path):
    items = list_dir(owner, repo, path)
    if not items:
        return False

    found = False

    for item in items:
        item_type = item.get("type")
        item_path = item.get("path")
        item_name = item.get("name")
        suffix = Path(item_name).suffix.lower()

        if item_type == "dir":
            if collect_docs_recursive(owner, repo, item_path, repo_target_root):
                found = True

        elif item_type == "file" and suffix in ALL_EXTENSIONS:
            rel_under_docs = Path(item_path).relative_to("docs")
            target = repo_target_root / rel_under_docs
            target.parent.mkdir(parents=True, exist_ok=True)

            if suffix in TEXT_EXTENSIONS:
                raw = raw_download(item["download_url"]).decode("utf-8", errors="replace")
                target.write_text(raw, encoding="utf-8")
                print(f"[MD] {repo}/{item_path} -> {target}")
            else:
                save_binary(item["download_url"], target)
                print(f"[ASSET] {repo}/{item_path} -> {target}")

            found = True

    return found


def build_collected_index(collected_repos):
    lines = [
        "# Collected Documents",
        "",
        f"총 {len(collected_repos)}개 저장소에서 `docs/` 하위 문서를 수집했습니다.",
        "",
    ]

    for repo_name in sorted(collected_repos):
        lines.append(f"## {repo_name}")
        repo_dir = TARGET_DIR / repo_name

        for md_file in sorted(repo_dir.rglob("*.md")):
            rel = md_file.relative_to(DOCS_DIR)
            title = md_file.stem
            lines.append(f"- [{title}]({rel.as_posix()})")
        lines.append("")

    output_file = DOCS_DIR / "collected-index.md"
    output_file.write_text("\n".join(lines), encoding="utf-8")
    print(f"[INDEX CREATED] {output_file}")


def main():
    if TARGET_DIR.exists():
        shutil.rmtree(TARGET_DIR)
    TARGET_DIR.mkdir(parents=True, exist_ok=True)
    DOCS_DIR.mkdir(parents=True, exist_ok=True)

    repos = list_org_repos(ORG)
    collected_repos = []

    for repo in repos:
        repo_name = repo["name"]

        if repo.get("archived") or repo.get("disabled"):
            print(f"[SKIP] {repo_name} archived or disabled")
            continue

        print(f"[CHECK] {repo_name}/docs")
        repo_target = TARGET_DIR / repo_name

        try:
            found = collect_docs_recursive(ORG, repo_name, "docs", repo_target)
            if found:
                collected_repos.append(repo_name)
        except Exception as e:
            print(f"[ERROR] {repo_name}: {e}")
            continue

    build_collected_index(collected_repos)
    categories = collect_categories(collected_repos)
    build_category_indexes(categories)
    build_category_data(categories)
    update_mkdocs_nav(collected_repos, categories)

    print("")
    print("[DONE]")
    print(f"Collected repos: {collected_repos}")


if __name__ == "__main__":
    main()
