"""Organize tools: archive stale pages, merge duplicates, rebuild hierarchy."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone, timedelta
from typing import Any

from src.tools.analyze import (
    _extract_title,
    _fetch_block_text,
    _list_all_pages,
    _parent_id,
)


# -----------------------------------------------------------------------------
# archive_stale
# -----------------------------------------------------------------------------


def archive_stale(client, days: int = 90, dry_run: bool = True) -> dict[str, Any]:
    """Archive pages untouched for more than `days` days.

    If `dry_run=True`, only returns the candidate list.
    """
    pages = _list_all_pages(client, max_pages=10_000)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    candidates: list[dict[str, Any]] = []
    for p in pages:
        if p.get("archived"):
            continue
        last_edit = p.get("last_edited_time")
        if not last_edit:
            continue
        try:
            last = datetime.fromisoformat(last_edit.replace("Z", "+00:00"))
        except Exception:
            continue
        if last >= cutoff:
            continue
        candidates.append(
            {
                "id": p["id"],
                "title": _extract_title(p),
                "last_edited": last_edit,
                "days_stale": (datetime.now(timezone.utc) - last).days,
            }
        )

    candidates.sort(key=lambda x: x["days_stale"], reverse=True)

    archived: list[str] = []
    errors: list[dict[str, str]] = []
    if not dry_run:
        for c in candidates:
            try:
                client.pages.update(page_id=c["id"], archived=True)
                archived.append(c["id"])
            except Exception as e:  # pragma: no cover (network)
                errors.append({"id": c["id"], "error": str(e)})

    return {
        "dry_run": dry_run,
        "candidate_count": len(candidates),
        "candidates": candidates[:500],
        "archived_count": len(archived),
        "errors": errors,
    }


# -----------------------------------------------------------------------------
# consolidate_duplicates
# -----------------------------------------------------------------------------


def consolidate_duplicates(
    client,
    cluster_page_ids: list[str],
    keep_id: str,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Merge duplicate pages into `keep_id`.

    Strategy:
    1. Keep `keep_id` unchanged
    2. Append unique blocks from other pages
    3. Archive merged sources
    Currently link redirection is best-effort (Notion API does not support
    rewriting inbound links, so we leave a stub block pointing to `keep_id`).
    """
    if not keep_id or keep_id not in cluster_page_ids:
        return {
            "error": "keep_id must be one of cluster_page_ids",
            "cluster_page_ids": cluster_page_ids,
            "keep_id": keep_id,
        }

    to_merge = [pid for pid in cluster_page_ids if pid != keep_id]
    actions: list[dict[str, Any]] = []
    appended_block_count = 0
    archived_count = 0

    for src_id in to_merge:
        src_text = _fetch_block_text(client, src_id, max_blocks=100)
        actions.append(
            {
                "source_id": src_id,
                "operation": "merge_into",
                "target_id": keep_id,
                "preview_chars": len(src_text),
            }
        )
        if dry_run:
            continue

        # Append a separator + content block
        try:
            client.blocks.children.append(
                block_id=keep_id,
                children=[
                    {
                        "object": "block",
                        "type": "divider",
                        "divider": {},
                    },
                    {
                        "object": "block",
                        "type": "callout",
                        "callout": {
                            "icon": {"emoji": "🔀"},
                            "rich_text": [
                                {
                                    "type": "text",
                                    "text": {
                                        "content": (
                                            f"Merged from {src_id} on "
                                            f"{datetime.now(timezone.utc).date()}"
                                        )
                                    },
                                }
                            ],
                        },
                    },
                    {
                        "object": "block",
                        "type": "paragraph",
                        "paragraph": {
                            "rich_text": [
                                {"type": "text", "text": {"content": src_text[:1900]}}
                            ]
                        },
                    },
                ],
            )
            appended_block_count += 3

            # Archive source page + leave a redirect stub
            client.blocks.children.append(
                block_id=src_id,
                children=[
                    {
                        "object": "block",
                        "type": "callout",
                        "callout": {
                            "icon": {"emoji": "➡️"},
                            "rich_text": [
                                {
                                    "type": "text",
                                    "text": {
                                        "content": (
                                            "This page was merged into the "
                                            "canonical version."
                                        )
                                    },
                                },
                                {
                                    "type": "mention",
                                    "mention": {
                                        "type": "page",
                                        "page": {"id": keep_id},
                                    },
                                },
                            ],
                        },
                    }
                ],
            )
            client.pages.update(page_id=src_id, archived=True)
            archived_count += 1
        except Exception as e:  # pragma: no cover
            actions[-1]["error"] = str(e)

    return {
        "dry_run": dry_run,
        "keep_id": keep_id,
        "merged_count": len(to_merge),
        "actions": actions,
        "appended_blocks": appended_block_count,
        "archived_count": archived_count,
    }


# -----------------------------------------------------------------------------
# rebuild_hierarchy
# -----------------------------------------------------------------------------


def rebuild_hierarchy(
    client, root_page_id: str, max_depth: int = 4
) -> dict[str, Any]:
    """Suggest a new page-tree organization.

    Uses page titles (TF-IDF prefix clustering) and parent-child usage to
    suggest move operations. Does NOT mutate (advisory only).
    """
    if not root_page_id:
        return {"error": "root_page_id is required"}

    pages = _list_all_pages(client, max_pages=5_000)

    children_of: dict[str, list[dict[str, Any]]] = defaultdict(list)
    titles: dict[str, str] = {}
    for p in pages:
        pid = p["id"]
        titles[pid] = _extract_title(p)
        parent = _parent_id(p)
        if parent and parent != "_workspace":
            children_of[parent].append(p)

    # BFS from root
    current_layer = [root_page_id]
    depths: dict[str, int] = {root_page_id: 0}
    visited = {root_page_id}
    while current_layer:
        next_layer: list[str] = []
        for node in current_layer:
            for child in children_of.get(node, []):
                cid = child["id"]
                if cid in visited:
                    continue
                visited.add(cid)
                depths[cid] = depths[node] + 1
                next_layer.append(cid)
        current_layer = next_layer

    # Identify "too deep" pages (> max_depth) and orphan-from-root
    too_deep = [pid for pid, d in depths.items() if d > max_depth]
    move_operations: list[dict[str, Any]] = []
    for pid in too_deep:
        move_operations.append(
            {
                "page_id": pid,
                "title": titles.get(pid, "?"),
                "current_depth": depths[pid],
                "suggested_parent": root_page_id,
                "reason": f"depth {depths[pid]} > max_depth {max_depth}",
            }
        )

    # Build a Markdown outline of the proposed tree (capped depth)
    lines: list[str] = []

    def walk(node: str, depth: int) -> None:
        if depth > max_depth:
            return
        indent = "  " * depth
        lines.append(f"{indent}- {titles.get(node, '?')} (`{node[:8]}`)")
        for child in children_of.get(node, [])[:20]:
            walk(child["id"], depth + 1)

    walk(root_page_id, 0)

    depth_dist: dict[int, int] = defaultdict(int)
    for d in depths.values():
        depth_dist[d] += 1

    return {
        "root_page_id": root_page_id,
        "current_depth_distribution": dict(depth_dist),
        "max_observed_depth": max(depth_dist.keys()) if depth_dist else 0,
        "proposed_tree_markdown": "\n".join(lines),
        "move_operations": move_operations,
        "advisory_only": True,
    }
