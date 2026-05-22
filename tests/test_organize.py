"""Tests for organize tools."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from src.tools import organize


def _make_page(
    page_id: str,
    title: str,
    parent_page: str | None = None,
    last_edited_days_ago: int = 1,
    archived: bool = False,
) -> dict:
    parent = (
        {"page_id": parent_page} if parent_page else {"workspace": True}
    )
    last = (datetime.now(timezone.utc) - timedelta(days=last_edited_days_ago)).isoformat()
    return {
        "id": page_id,
        "object": "page",
        "last_edited_time": last,
        "parent": parent,
        "archived": archived,
        "properties": {"Name": {"type": "title", "title": [{"plain_text": title}]}},
    }


# -----------------------------------------------------------------------------
# archive_stale
# -----------------------------------------------------------------------------


class TestArchiveStale:
    def test_identifies_old_pages(self):
        client = MagicMock()
        client.search.return_value = {
            "results": [
                _make_page("s1", "old", last_edited_days_ago=200),
                _make_page("s2", "recent", last_edited_days_ago=10),
                _make_page("s3", "ancient", last_edited_days_ago=400),
            ],
            "has_more": False,
        }
        out = organize.archive_stale(client, days=90, dry_run=True)
        ids = {c["id"] for c in out["candidates"]}
        assert "s1" in ids
        assert "s3" in ids
        assert "s2" not in ids

    def test_dry_run_does_not_archive(self):
        client = MagicMock()
        client.search.return_value = {
            "results": [_make_page("s1", "x", last_edited_days_ago=200)],
            "has_more": False,
        }
        out = organize.archive_stale(client, days=90, dry_run=True)
        assert out["dry_run"] is True
        assert out["archived_count"] == 0
        client.pages.update.assert_not_called()

    def test_non_dry_run_archives(self):
        client = MagicMock()
        client.search.return_value = {
            "results": [_make_page("s1", "x", last_edited_days_ago=200)],
            "has_more": False,
        }
        out = organize.archive_stale(client, days=90, dry_run=False)
        assert out["archived_count"] == 1
        client.pages.update.assert_called_once_with(page_id="s1", archived=True)

    def test_excludes_already_archived(self):
        client = MagicMock()
        client.search.return_value = {
            "results": [
                _make_page("s1", "x", last_edited_days_ago=200, archived=True),
            ],
            "has_more": False,
        }
        out = organize.archive_stale(client, days=90, dry_run=True)
        assert out["candidate_count"] == 0

    def test_sorted_by_staleness_desc(self):
        client = MagicMock()
        client.search.return_value = {
            "results": [
                _make_page("a", "x", last_edited_days_ago=100),
                _make_page("b", "y", last_edited_days_ago=500),
                _make_page("c", "z", last_edited_days_ago=200),
            ],
            "has_more": False,
        }
        out = organize.archive_stale(client, days=90, dry_run=True)
        days = [c["days_stale"] for c in out["candidates"]]
        assert days == sorted(days, reverse=True)


# -----------------------------------------------------------------------------
# consolidate_duplicates
# -----------------------------------------------------------------------------


class TestConsolidateDuplicates:
    def test_requires_keep_id_in_cluster(self):
        client = MagicMock()
        out = organize.consolidate_duplicates(
            client, cluster_page_ids=["a", "b"], keep_id="c"
        )
        assert "error" in out

    def test_dry_run_returns_plan(self):
        client = MagicMock()
        client.blocks.children.list.return_value = {
            "results": [],
            "has_more": False,
        }
        out = organize.consolidate_duplicates(
            client,
            cluster_page_ids=["k1", "k2", "k3"],
            keep_id="k1",
            dry_run=True,
        )
        assert out["dry_run"] is True
        assert out["keep_id"] == "k1"
        assert out["merged_count"] == 2
        assert out["archived_count"] == 0

    def test_non_dry_run_merges(self):
        client = MagicMock()
        client.blocks.children.list.return_value = {
            "results": [],
            "has_more": False,
        }
        out = organize.consolidate_duplicates(
            client,
            cluster_page_ids=["k1", "k2"],
            keep_id="k1",
            dry_run=False,
        )
        assert out["archived_count"] == 1
        assert client.blocks.children.append.called
        assert client.pages.update.called


# -----------------------------------------------------------------------------
# rebuild_hierarchy
# -----------------------------------------------------------------------------


class TestRebuildHierarchy:
    def test_requires_root(self):
        client = MagicMock()
        out = organize.rebuild_hierarchy(client, root_page_id="")
        assert "error" in out

    def test_builds_outline(self):
        client = MagicMock()
        client.search.return_value = {
            "results": [
                _make_page("root", "Root"),
                _make_page("c1", "Child 1", parent_page="root"),
                _make_page("c2", "Child 2", parent_page="root"),
                _make_page("g1", "Grandchild", parent_page="c1"),
            ],
            "has_more": False,
        }
        out = organize.rebuild_hierarchy(
            client, root_page_id="root", max_depth=4
        )
        assert "Root" in out["proposed_tree_markdown"]
        assert "Child 1" in out["proposed_tree_markdown"]
        assert out["advisory_only"] is True

    def test_flags_too_deep_pages(self):
        client = MagicMock()
        # Build a chain root → a → b → c → d → e (depth 5)
        client.search.return_value = {
            "results": [
                _make_page("root", "Root"),
                _make_page("a", "A", parent_page="root"),
                _make_page("b", "B", parent_page="a"),
                _make_page("c", "C", parent_page="b"),
                _make_page("d", "D", parent_page="c"),
                _make_page("e", "E", parent_page="d"),
            ],
            "has_more": False,
        }
        out = organize.rebuild_hierarchy(
            client, root_page_id="root", max_depth=3
        )
        moved_ids = {m["page_id"] for m in out["move_operations"]}
        assert "d" in moved_ids or "e" in moved_ids
