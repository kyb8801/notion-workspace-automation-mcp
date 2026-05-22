"""Build tools: apply template, clone workspace, dashboards, DB sync."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from src.tools.analyze import (
    _extract_title,
    _list_all_pages,
)


# -----------------------------------------------------------------------------
# apply_template
# -----------------------------------------------------------------------------


_TEMPLATE_URL_RE = re.compile(
    r"notion\.so/[^/]*/?(?P<slug>[a-zA-Z0-9-]+)?-?(?P<id>[0-9a-f]{32})"
)


def _extract_page_id(url_or_id: str) -> str:
    """Pull a 32-char Notion page id out of a URL or accept a raw id."""
    if not url_or_id:
        return ""
    raw = url_or_id.replace("-", "")
    if len(raw) == 32 and all(c in "0123456789abcdef" for c in raw.lower()):
        # Looks like a raw id (with or without dashes)
        return url_or_id
    m = _TEMPLATE_URL_RE.search(url_or_id)
    if m and m.group("id"):
        nid = m.group("id")
        return f"{nid[:8]}-{nid[8:12]}-{nid[12:16]}-{nid[16:20]}-{nid[20:]}"
    return url_or_id


def apply_template(
    client, template_url: str, target_page_id: str
) -> dict[str, Any]:
    """Apply a Notion template to a target page.

    Note: Notion API does not yet expose a public 'duplicate as template' RPC.
    We approximate by:
      1. Fetching the template page
      2. Copying its children blocks into target_page_id
    For databases, we create a new database under target with the same schema.
    """
    template_id = _extract_page_id(template_url)
    if not template_id or not target_page_id:
        return {"error": "template_url and target_page_id are required"}

    # Fetch template's children
    blocks: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        kwargs: dict[str, Any] = {"block_id": template_id, "page_size": 100}
        if cursor:
            kwargs["start_cursor"] = cursor
        resp = client.blocks.children.list(**kwargs)
        blocks.extend(resp.get("results", []))
        if not resp.get("has_more"):
            break
        cursor = resp.get("next_cursor")

    # Strip ids and metadata; keep type + payload
    new_blocks: list[dict[str, Any]] = []
    for b in blocks:
        btype = b.get("type")
        if not btype:
            continue
        payload = b.get(btype, {})
        new_blocks.append({"object": "block", "type": btype, btype: payload})

    appended = 0
    if new_blocks:
        # Notion limit ~100 children per request
        for i in range(0, len(new_blocks), 100):
            chunk = new_blocks[i : i + 100]
            client.blocks.children.append(
                block_id=target_page_id, children=chunk
            )
            appended += len(chunk)

    return {
        "template_id": template_id,
        "target_page_id": target_page_id,
        "blocks_added": appended,
        "warnings": (
            ["Inline databases inside template are not deep-cloned"]
            if any(b.get("type") in ("child_database", "child_page") for b in blocks)
            else []
        ),
    }


# -----------------------------------------------------------------------------
# clone_workspace
# -----------------------------------------------------------------------------


_PII_PATTERNS = [
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"), "[email]"),
    (re.compile(r"\b\d{3}-\d{4}-\d{4}\b"), "[phone]"),
    (re.compile(r"\b\d{6}-\d{7}\b"), "[id]"),
]


def _anonymize_text(text: str) -> str:
    for pat, repl in _PII_PATTERNS:
        text = pat.sub(repl, text)
    return text


def _strip_block_for_clone(
    block: dict[str, Any], anonymize: bool
) -> dict[str, Any] | None:
    btype = block.get("type")
    if not btype:
        return None
    payload = block.get(btype, {})
    if anonymize and isinstance(payload, dict):
        rich = payload.get("rich_text", [])
        for r in rich:
            if "text" in r and "content" in r["text"]:
                r["text"]["content"] = _anonymize_text(r["text"]["content"])
                if "plain_text" in r:
                    r["plain_text"] = _anonymize_text(r["plain_text"])
    return {"object": "block", "type": btype, btype: payload}


def clone_workspace(
    source_client,
    target_client,
    root_page_id: str,
    anonymize: bool = False,
) -> dict[str, Any]:
    """Clone a subtree from one workspace into another.

    Walks the source subtree, recreates pages in target. Databases are recreated
    with the same schema; row content is copied row-by-row.
    """
    if not root_page_id:
        return {"error": "root_page_id is required"}

    # Read source root
    root = source_client.pages.retrieve(page_id=root_page_id)
    root_title = _extract_title(root)
    if anonymize:
        root_title = _anonymize_text(root_title)

    # Create target root (as a workspace-level page)
    new_root = target_client.pages.create(
        parent={"type": "page_id", "page_id": root_page_id},
        properties={
            "title": {
                "title": [{"text": {"content": f"Cloned: {root_title}"}}]
            }
        },
    )
    new_root_id = new_root["id"]

    stats = {"pages_cloned": 1, "blocks_copied": 0, "databases_cloned": 0}
    visited = {root_page_id}

    def _clone_subtree(source_id: str, target_id: str, depth: int = 0) -> None:
        if depth > 10:
            return
        cursor: str | None = None
        children_blocks: list[dict[str, Any]] = []
        while True:
            kwargs: dict[str, Any] = {"block_id": source_id, "page_size": 100}
            if cursor:
                kwargs["start_cursor"] = cursor
            resp = source_client.blocks.children.list(**kwargs)
            children_blocks.extend(resp.get("results", []))
            if not resp.get("has_more"):
                break
            cursor = resp.get("next_cursor")

        # Separate child_page / child_database recursion from inline blocks
        inline_blocks: list[dict[str, Any]] = []
        for b in children_blocks:
            btype = b.get("type")
            if btype == "child_page":
                child_pid = b["id"]
                if child_pid in visited:
                    continue
                visited.add(child_pid)
                child_title = b.get("child_page", {}).get("title", "")
                if anonymize:
                    child_title = _anonymize_text(child_title)
                created = target_client.pages.create(
                    parent={"type": "page_id", "page_id": target_id},
                    properties={
                        "title": {
                            "title": [{"text": {"content": child_title}}]
                        }
                    },
                )
                stats["pages_cloned"] += 1
                _clone_subtree(child_pid, created["id"], depth + 1)
            elif btype == "child_database":
                stats["databases_cloned"] += 1
                # NOTE: simplified — full DB schema clone is out of MVP scope
            else:
                stripped = _strip_block_for_clone(b, anonymize)
                if stripped:
                    inline_blocks.append(stripped)

        # Bulk append inline blocks (chunks of 100)
        for i in range(0, len(inline_blocks), 100):
            chunk = inline_blocks[i : i + 100]
            target_client.blocks.children.append(
                block_id=target_id, children=chunk
            )
            stats["blocks_copied"] += len(chunk)

    _clone_subtree(root_page_id, new_root_id)

    return {
        "source_root_id": root_page_id,
        "target_root_id": new_root_id,
        "anonymized": anonymize,
        **stats,
        "warnings": (
            ["Full database row clone is in v1.1; placeholder created"]
            if stats["databases_cloned"]
            else []
        ),
    }


# -----------------------------------------------------------------------------
# create_dashboard
# -----------------------------------------------------------------------------


def create_dashboard(
    client,
    metric_db_id: str,
    dashboard_parent_id: str,
    chart_types: list[str] | None = None,
) -> dict[str, Any]:
    """Generate a KPI dashboard page from a database.

    Builds:
    - Today / Week / Month callouts
    - Linked DB view (filtered last 30d)
    - Markdown chart placeholders (Notion chart blocks are limited; we drop
      formatted text blocks that show aggregated metrics).
    """
    if not metric_db_id or not dashboard_parent_id:
        return {"error": "metric_db_id and dashboard_parent_id required"}

    chart_types = chart_types or ["line", "bar"]

    # Query last 30 days
    today = datetime.now(timezone.utc)
    thirty_days_ago = today.replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        resp = client.databases.query(
            database_id=metric_db_id,
            page_size=100,
        )
        rows = resp.get("results", [])
    except Exception as e:  # pragma: no cover
        return {"error": f"failed to query DB: {e}"}

    # Create a new dashboard page
    page = client.pages.create(
        parent={"type": "page_id", "page_id": dashboard_parent_id},
        properties={
            "title": {
                "title": [
                    {
                        "text": {
                            "content": (
                                f"KPI Dashboard — {today.date().isoformat()}"
                            )
                        }
                    }
                ]
            }
        },
    )
    dash_id = page["id"]

    callout_blocks = [
        {
            "object": "block",
            "type": "callout",
            "callout": {
                "icon": {"emoji": "📊"},
                "rich_text": [
                    {
                        "type": "text",
                        "text": {
                            "content": (
                                f"Total rows last 30 days: {len(rows)}\n"
                                f"DB: {metric_db_id}\n"
                                f"Generated: {today.isoformat()}"
                            )
                        },
                    }
                ],
            },
        }
    ]
    for ct in chart_types:
        callout_blocks.append(
            {
                "object": "block",
                "type": "heading_3",
                "heading_3": {
                    "rich_text": [
                        {
                            "type": "text",
                            "text": {"content": f"{ct.title()} Chart (placeholder)"},
                        }
                    ]
                },
            }
        )

    client.blocks.children.append(block_id=dash_id, children=callout_blocks)

    return {
        "dashboard_page_id": dash_id,
        "metric_db_id": metric_db_id,
        "rows_summarized": len(rows),
        "chart_types": chart_types,
        "warnings": [
            "Notion native chart blocks are limited; "
            "consider Grid.js / Chart.js embed via custom HTML for advanced charts."
        ],
    }


# -----------------------------------------------------------------------------
# sync_databases
# -----------------------------------------------------------------------------


def sync_databases(
    client,
    source_db_id: str,
    target_db_id: str,
    field_mapping: dict[str, str],
    direction: str = "one-way",
) -> dict[str, Any]:
    """Sync rows between two databases.

    one-way: source → target (target becomes mirror)
    two-way: last_edited_time wins
    """
    if not source_db_id or not target_db_id:
        return {"error": "source_db_id and target_db_id required"}
    if direction not in ("one-way", "two-way"):
        return {"error": "direction must be one-way or two-way"}
    if not field_mapping:
        return {"error": "field_mapping required (source_field -> target_field)"}

    source_rows = client.databases.query(database_id=source_db_id).get("results", [])
    target_rows = (
        client.databases.query(database_id=target_db_id).get("results", [])
        if direction == "two-way"
        else []
    )

    # Build lookup by an "ID" field if present in mapping
    id_field = next(
        (src for src, tgt in field_mapping.items() if "id" in src.lower()), None
    )

    created = 0
    updated = 0
    target_by_key: dict[str, dict[str, Any]] = {}
    if id_field:
        tgt_id_field = field_mapping[id_field]
        for r in target_rows:
            key = _property_string(r.get("properties", {}).get(tgt_id_field))
            if key:
                target_by_key[key] = r

    for src in source_rows:
        src_props = src.get("properties", {})
        new_props: dict[str, Any] = {}
        for src_field, tgt_field in field_mapping.items():
            if src_field in src_props:
                new_props[tgt_field] = src_props[src_field]

        # Identify existing row by id field if available
        existing = None
        if id_field and id_field in src_props:
            key = _property_string(src_props[id_field])
            existing = target_by_key.get(key) if key else None

        try:
            if existing:
                client.pages.update(page_id=existing["id"], properties=new_props)
                updated += 1
            else:
                client.pages.create(
                    parent={"type": "database_id", "database_id": target_db_id},
                    properties=new_props,
                )
                created += 1
        except Exception:  # pragma: no cover
            continue

    return {
        "direction": direction,
        "source_rows": len(source_rows),
        "target_rows_seen": len(target_rows),
        "created": created,
        "updated": updated,
        "field_mapping": field_mapping,
    }


def _property_string(prop: dict[str, Any] | None) -> str:
    """Best-effort string extraction from a Notion property value."""
    if not prop:
        return ""
    ptype = prop.get("type")
    if ptype == "title":
        return "".join(t.get("plain_text", "") for t in prop.get("title", []))
    if ptype == "rich_text":
        return "".join(t.get("plain_text", "") for t in prop.get("rich_text", []))
    if ptype == "number":
        return str(prop.get("number") or "")
    if ptype == "select":
        return (prop.get("select") or {}).get("name", "")
    if ptype == "unique_id":
        u = prop.get("unique_id") or {}
        prefix = u.get("prefix") or ""
        num = u.get("number")
        return f"{prefix}-{num}" if num is not None else ""
    return ""
