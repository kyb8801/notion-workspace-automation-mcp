"""
Notion Workspace Automation MCP Server.

10 tools across 3 categories:
- Analyze: workspace structure, duplicates, orphans
- Organize: archive stale, consolidate, rebuild hierarchy
- Build: apply template, clone workspace, dashboards, DB sync

Author: Yongbeom Kim (kyb8801)
License: MIT
"""

from __future__ import annotations

import os
from typing import Annotated, Any

from fastmcp import FastMCP
from pydantic import Field

from src.tools import analyze, organize, build

mcp = FastMCP(
    "notion-workspace-automation",
    instructions=(
        "Use this server when the user wants to analyze, clean, or rebuild a "
        "Notion workspace. Always start with `analyze_workspace` to map the "
        "structure before making any changes."
    ),
)


def _client(token: str | None = None):
    """Lazily construct a Notion client (so tests can mock cleanly)."""
    from notion_client import Client

    return Client(auth=token or os.environ["NOTION_TOKEN"])


# ----------------------------------------------------------------------- ANALYZE


@mcp.tool()
def analyze_workspace(
    notion_token: Annotated[
        str | None,
        Field(description="Notion integration token. Falls back to NOTION_TOKEN env."),
    ] = None,
    max_pages: Annotated[
        int, Field(description="Maximum pages to scan", ge=1, le=10_000)
    ] = 1000,
) -> dict[str, Any]:
    """Return a structural map of a Notion workspace.

    Output includes:
    - page_count, db_count, total_blocks
    - tree (parent -> children adjacency list)
    - top-N pages by inbound links
    - depth histogram
    """
    return analyze.analyze_workspace(_client(notion_token), max_pages=max_pages)


@mcp.tool()
def find_duplicates(
    notion_token: Annotated[str | None, Field(description="Notion token")] = None,
    similarity_threshold: Annotated[
        float, Field(description="Cosine similarity threshold 0-1", ge=0.5, le=0.99)
    ] = 0.85,
    max_pages: Annotated[int, Field(ge=1, le=10_000)] = 1000,
) -> dict[str, Any]:
    """Cluster pages by TF-IDF + cosine similarity to find likely duplicates.

    Output:
    - clusters: list of {page_ids, sample_titles, similarity}
    - merge_suggestions: ordered by content overlap
    """
    return analyze.find_duplicates(
        _client(notion_token),
        threshold=similarity_threshold,
        max_pages=max_pages,
    )


@mcp.tool()
def audit_orphans(
    notion_token: Annotated[str | None, Field(description="Notion token")] = None,
    max_pages: Annotated[int, Field(ge=1, le=10_000)] = 1000,
) -> dict[str, Any]:
    """Find pages no other page links to.

    Output:
    - orphan_count
    - orphans: [{id, title, last_edited, depth_from_root}]
    - suggested_actions: archive / merge / delete recommendation per page
    """
    return analyze.audit_orphans(_client(notion_token), max_pages=max_pages)


# ---------------------------------------------------------------------- ORGANIZE


@mcp.tool()
def archive_stale(
    notion_token: Annotated[str | None, Field(description="Notion token")] = None,
    days: Annotated[int, Field(description="Days untouched threshold", ge=7, le=3650)] = 90,
    dry_run: Annotated[bool, Field(description="If true, only suggest")] = True,
) -> dict[str, Any]:
    """Archive (or list) pages untouched for more than `days` days.

    Default `dry_run=True` returns the candidate list without modifying anything.
    Set `dry_run=False` to actually archive.
    """
    return organize.archive_stale(_client(notion_token), days=days, dry_run=dry_run)


@mcp.tool()
def consolidate_duplicates(
    notion_token: Annotated[str | None, Field(description="Notion token")] = None,
    cluster_page_ids: Annotated[
        list[str], Field(description="Page IDs from find_duplicates cluster")
    ] = [],
    keep_id: Annotated[str, Field(description="Page to keep; others merged into it")] = "",
    dry_run: Annotated[bool, Field()] = True,
) -> dict[str, Any]:
    """Merge duplicate pages into a single canonical page.

    Strategy:
    1. Keep `keep_id` as canonical
    2. Append unique blocks from other pages
    3. Redirect inbound links to `keep_id`
    4. Archive the merged sources
    """
    return organize.consolidate_duplicates(
        _client(notion_token),
        cluster_page_ids=cluster_page_ids,
        keep_id=keep_id,
        dry_run=dry_run,
    )


@mcp.tool()
def rebuild_hierarchy(
    notion_token: Annotated[str | None, Field(description="Notion token")] = None,
    root_page_id: Annotated[str, Field(description="Workspace root page ID")] = "",
    max_depth: Annotated[int, Field(ge=2, le=10)] = 4,
) -> dict[str, Any]:
    """Suggest a new page-tree organization based on usage and link graph.

    Output:
    - current_depth_distribution
    - proposed_tree (Markdown outline)
    - move_operations: ordered list of recommended parent changes
    """
    return organize.rebuild_hierarchy(
        _client(notion_token),
        root_page_id=root_page_id,
        max_depth=max_depth,
    )


# ------------------------------------------------------------------------- BUILD


@mcp.tool()
def apply_template(
    notion_token: Annotated[str | None, Field(description="Target Notion token")] = None,
    template_url: Annotated[str, Field(description="Notion duplicate-as-template URL")] = "",
    target_page_id: Annotated[str, Field(description="Where to drop the template")] = "",
) -> dict[str, Any]:
    """Apply a public Notion template (duplicate-as-template URL) into a target page.

    Output:
    - inserted_page_id
    - blocks_added
    - dbs_created
    """
    return build.apply_template(
        _client(notion_token),
        template_url=template_url,
        target_page_id=target_page_id,
    )


@mcp.tool()
def clone_workspace(
    source_token: Annotated[str, Field(description="Source workspace token")] = "",
    target_token: Annotated[str, Field(description="Target workspace token")] = "",
    root_page_id: Annotated[str, Field(description="Source root page to clone")] = "",
    anonymize: Annotated[
        bool, Field(description="Strip personal info before clone")
    ] = False,
) -> dict[str, Any]:
    """Clone an entire workspace subtree from one Notion account to another.

    Use cases:
    - Job change (migrate work workspace to personal)
    - Productize a workspace (anonymize=True → strip emails, names)
    """
    return build.clone_workspace(
        source_client=_client(source_token),
        target_client=_client(target_token),
        root_page_id=root_page_id,
        anonymize=anonymize,
    )


@mcp.tool()
def create_dashboard(
    notion_token: Annotated[str | None, Field(description="Notion token")] = None,
    metric_db_id: Annotated[str, Field(description="DB to read metrics from")] = "",
    dashboard_parent_id: Annotated[str, Field(description="Page to put dashboard in")] = "",
    chart_types: Annotated[
        list[str], Field(description="Chart types to include")
    ] = ["line", "bar"],
) -> dict[str, Any]:
    """Generate a KPI dashboard page from a metric database.

    Builds:
    - Today / Week / Month rollup callouts
    - Line chart of primary metric
    - Bar chart of categorical dimension
    - Linked database view filtered to last 30 days
    """
    return build.create_dashboard(
        _client(notion_token),
        metric_db_id=metric_db_id,
        dashboard_parent_id=dashboard_parent_id,
        chart_types=chart_types,
    )


@mcp.tool()
def sync_databases(
    notion_token: Annotated[str | None, Field(description="Notion token")] = None,
    source_db_id: Annotated[str, Field()] = "",
    target_db_id: Annotated[str, Field()] = "",
    field_mapping: Annotated[
        dict[str, str], Field(description="source_field -> target_field")
    ] = {},
    direction: Annotated[
        str, Field(description="one-way | two-way")
    ] = "one-way",
) -> dict[str, Any]:
    """Sync rows between two databases.

    One-way: source → target (target is mirror).
    Two-way: bidirectional, last-write-wins by `last_edited_time`.
    """
    return build.sync_databases(
        _client(notion_token),
        source_db_id=source_db_id,
        target_db_id=target_db_id,
        field_mapping=field_mapping,
        direction=direction,
    )


def main() -> None:
    """Entry point for `notion-workspace-automation` console script."""
    mcp.run()


if __name__ == "__main__":
    main()
