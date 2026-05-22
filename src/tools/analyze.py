"""Analyze tools: workspace map, duplicate clustering, orphan audit."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

import networkx as nx
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


def _list_all_pages(client, max_pages: int) -> list[dict[str, Any]]:
    """Page through search() to get all pages the integration can see."""
    pages: list[dict[str, Any]] = []
    cursor = None
    while True:
        kwargs = {
            "filter": {"property": "object", "value": "page"},
            "page_size": 100,
        }
        if cursor:
            kwargs["start_cursor"] = cursor
        resp = client.search(**kwargs)
        pages.extend(resp["results"])
        if not resp.get("has_more") or len(pages) >= max_pages:
            break
        cursor = resp.get("next_cursor")
    return pages[:max_pages]


def _list_all_databases(client, max_dbs: int = 1000) -> list[dict[str, Any]]:
    """Page through search() for databases."""
    dbs: list[dict[str, Any]] = []
    cursor = None
    while True:
        kwargs = {
            "filter": {"property": "object", "value": "database"},
            "page_size": 100,
        }
        if cursor:
            kwargs["start_cursor"] = cursor
        resp = client.search(**kwargs)
        dbs.extend(resp["results"])
        if not resp.get("has_more") or len(dbs) >= max_dbs:
            break
        cursor = resp.get("next_cursor")
    return dbs[:max_dbs]


def _extract_title(page: dict[str, Any]) -> str:
    """Best-effort title extraction across page/db shapes."""
    props = page.get("properties", {})
    for prop in props.values():
        if prop.get("type") == "title":
            title_arr = prop.get("title") or []
            return "".join(t.get("plain_text", "") for t in title_arr) or "(untitled)"
    # Database
    title = page.get("title") or []
    if isinstance(title, list):
        return "".join(t.get("plain_text", "") for t in title) or "(untitled)"
    return "(untitled)"


def _parent_id(page: dict[str, Any]) -> str | None:
    p = page.get("parent", {})
    return p.get("page_id") or p.get("database_id") or p.get("workspace") and "_workspace"


def _fetch_block_text(client, page_id: str, max_blocks: int = 200) -> str:
    """Concatenate plain_text of the first N blocks for duplicate detection."""
    pieces: list[str] = []
    cursor = None
    fetched = 0
    while fetched < max_blocks:
        kwargs = {"block_id": page_id, "page_size": min(100, max_blocks - fetched)}
        if cursor:
            kwargs["start_cursor"] = cursor
        try:
            resp = client.blocks.children.list(**kwargs)
        except Exception:
            break
        for block in resp.get("results", []):
            btype = block.get("type")
            content = block.get(btype, {})
            for rich in content.get("rich_text", []) or []:
                pieces.append(rich.get("plain_text", ""))
        fetched += len(resp.get("results", []))
        if not resp.get("has_more"):
            break
        cursor = resp.get("next_cursor")
    return " ".join(pieces)


# -----------------------------------------------------------------------------
# Public tool functions
# -----------------------------------------------------------------------------


def analyze_workspace(client, max_pages: int = 1000) -> dict[str, Any]:
    """Build a structural map of the workspace."""
    pages = _list_all_pages(client, max_pages)
    dbs = _list_all_databases(client)

    # Build adjacency
    tree: dict[str, list[str]] = defaultdict(list)
    titles: dict[str, str] = {}
    depths: dict[str, int] = {}

    for p in pages + dbs:
        pid = p["id"]
        titles[pid] = _extract_title(p)
        parent = _parent_id(p)
        if parent:
            tree[parent].append(pid)

    # BFS for depths from workspace root
    queue = list(tree.get("_workspace", []))
    seen = set(queue)
    depth = 0
    while queue:
        next_queue: list[str] = []
        for node in queue:
            depths[node] = depth
            for child in tree.get(node, []):
                if child not in seen:
                    seen.add(child)
                    next_queue.append(child)
        queue = next_queue
        depth += 1

    depth_hist = Counter(depths.values())

    # Inbound link approximation: parent relationships
    inbound: Counter = Counter()
    for parent, children in tree.items():
        for c in children:
            inbound[parent] += 1
    top_inbound = inbound.most_common(20)

    return {
        "page_count": len(pages),
        "db_count": len(dbs),
        "depth_histogram": dict(depth_hist),
        "max_depth": max(depth_hist.keys()) if depth_hist else 0,
        "top_inbound_pages": [
            {"id": pid, "title": titles.get(pid, "?"), "children": count}
            for pid, count in top_inbound
        ],
        "tree_size": sum(len(v) for v in tree.values()),
    }


def find_duplicates(
    client, threshold: float = 0.85, max_pages: int = 1000
) -> dict[str, Any]:
    """Cluster pages by TF-IDF cosine similarity."""
    pages = _list_all_pages(client, max_pages)
    if len(pages) < 2:
        return {"clusters": [], "merge_suggestions": []}

    titles = [_extract_title(p) for p in pages]
    ids = [p["id"] for p in pages]

    # Pull a small text sample per page (limited for speed/cost)
    texts: list[str] = []
    for p in pages:
        title = _extract_title(p)
        body = _fetch_block_text(client, p["id"], max_blocks=30)
        texts.append(f"{title}. {body}")

    vectorizer = TfidfVectorizer(
        max_features=2_000, ngram_range=(1, 2), stop_words="english"
    )
    matrix = vectorizer.fit_transform(texts)
    sim = cosine_similarity(matrix)

    # Build graph of edges above threshold
    g = nx.Graph()
    g.add_nodes_from(range(len(pages)))
    n = len(pages)
    for i in range(n):
        for j in range(i + 1, n):
            if sim[i, j] >= threshold:
                g.add_edge(i, j, weight=float(sim[i, j]))

    clusters_out: list[dict[str, Any]] = []
    for component in nx.connected_components(g):
        if len(component) < 2:
            continue
        idxs = sorted(component)
        avg_sim = float(
            sum(sim[a, b] for a in idxs for b in idxs if a < b)
            / max(1, len(idxs) * (len(idxs) - 1) / 2)
        )
        clusters_out.append(
            {
                "page_ids": [ids[i] for i in idxs],
                "titles": [titles[i] for i in idxs],
                "avg_similarity": round(avg_sim, 3),
                "size": len(idxs),
            }
        )

    clusters_out.sort(key=lambda c: (-c["size"], -c["avg_similarity"]))

    merge_suggestions = [
        {
            "keep": c["page_ids"][0],
            "merge": c["page_ids"][1:],
            "reason": f"avg_sim={c['avg_similarity']}, n={c['size']}",
        }
        for c in clusters_out
    ]

    return {"clusters": clusters_out, "merge_suggestions": merge_suggestions}


def audit_orphans(client, max_pages: int = 1000) -> dict[str, Any]:
    """Pages with zero inbound links (no parent page references them)."""
    pages = _list_all_pages(client, max_pages)

    # Build reverse adjacency from parent relations
    parents = {p["id"]: _parent_id(p) for p in pages}
    referenced_ids = {pid for pid in parents.values() if pid and pid != "_workspace"}

    now = datetime.now(timezone.utc)
    orphans: list[dict[str, Any]] = []
    for p in pages:
        pid = p["id"]
        if pid in referenced_ids:
            continue
        if parents.get(pid) and parents[pid] != "_workspace":
            # Has a parent — not a true orphan
            continue
        last_edit = p.get("last_edited_time")
        try:
            days_since = (
                now - datetime.fromisoformat(last_edit.replace("Z", "+00:00"))
            ).days
        except Exception:
            days_since = None

        orphans.append(
            {
                "id": pid,
                "title": _extract_title(p),
                "last_edited": last_edit,
                "days_since_edit": days_since,
                "suggested_action": (
                    "archive"
                    if days_since is not None and days_since > 90
                    else "review"
                ),
            }
        )

    orphans.sort(key=lambda x: x["days_since_edit"] or 0, reverse=True)
    return {
        "orphan_count": len(orphans),
        "total_pages_scanned": len(pages),
        "orphans": orphans[:200],
    }
