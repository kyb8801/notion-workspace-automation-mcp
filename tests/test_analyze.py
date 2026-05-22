"""Tests for analyze tools."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from src.tools import analyze


# -----------------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------------


def _make_page(
    page_id: str,
    title: str,
    parent_page: str | None = None,
    parent_db: str | None = None,
    last_edited_days_ago: int = 1,
) -> dict:
    parent: dict = {}
    if parent_page:
        parent = {"page_id": parent_page}
    elif parent_db:
        parent = {"database_id": parent_db}
    else:
        parent = {"workspace": True}

    last = (
        datetime.now(timezone.utc) - timedelta(days=last_edited_days_ago)
    ).isoformat()

    return {
        "id": page_id,
        "object": "page",
        "last_edited_time": last,
        "parent": parent,
        "properties": {
            "Name": {
                "type": "title",
                "title": [{"plain_text": title}],
            }
        },
    }


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.search.return_value = {
        "results": [
            _make_page("p1", "Project A"),
            _make_page("p2", "Project A copy", parent_page="p1"),
            _make_page("p3", "Notes about A", parent_page="p1"),
            _make_page(
                "p4", "Orphan page", last_edited_days_ago=120
            ),
        ],
        "has_more": False,
    }
    # Default block list — empty
    client.blocks.children.list.return_value = {
        "results": [],
        "has_more": False,
    }
    return client


# -----------------------------------------------------------------------------
# analyze_workspace
# -----------------------------------------------------------------------------


class TestAnalyzeWorkspace:
    def test_returns_page_count(self, mock_client):
        out = analyze.analyze_workspace(mock_client)
        # Both pages and databases call search; first call is pages
        assert out["page_count"] >= 1

    def test_builds_depth_histogram(self, mock_client):
        out = analyze.analyze_workspace(mock_client)
        assert isinstance(out["depth_histogram"], dict)

    def test_top_inbound_includes_parent_page(self, mock_client):
        out = analyze.analyze_workspace(mock_client)
        top = {x["id"] for x in out["top_inbound_pages"]}
        # p1 should have at least 2 children (p2, p3)
        assert "p1" in top

    def test_max_pages_caps_scan(self, mock_client):
        out = analyze.analyze_workspace(mock_client, max_pages=2)
        assert out["page_count"] <= 2


# -----------------------------------------------------------------------------
# find_duplicates
# -----------------------------------------------------------------------------


class TestFindDuplicates:
    def test_clusters_similar_titles(self):
        client = MagicMock()
        client.search.return_value = {
            "results": [
                _make_page("d1", "Quarterly revenue report Q3 2026"),
                _make_page("d2", "Quarterly revenue report Q3 2026 v2"),
                _make_page("d3", "Totally unrelated cooking blog post"),
            ],
            "has_more": False,
        }
        client.blocks.children.list.return_value = {
            "results": [],
            "has_more": False,
        }
        out = analyze.find_duplicates(client, threshold=0.5)
        # Should find a cluster of d1, d2
        assert len(out["clusters"]) >= 1
        cluster = out["clusters"][0]
        assert "d1" in cluster["page_ids"]
        assert "d2" in cluster["page_ids"]

    def test_returns_empty_for_unique_pages(self):
        client = MagicMock()
        client.search.return_value = {
            "results": [
                _make_page("u1", "Apples and oranges"),
                _make_page("u2", "Quantum field theory primer"),
                _make_page("u3", "How to bake sourdough"),
            ],
            "has_more": False,
        }
        client.blocks.children.list.return_value = {
            "results": [],
            "has_more": False,
        }
        out = analyze.find_duplicates(client, threshold=0.95)
        assert out["clusters"] == []

    def test_threshold_bounds(self):
        client = MagicMock()
        client.search.return_value = {"results": [], "has_more": False}
        out = analyze.find_duplicates(client, threshold=0.9)
        assert out == {"clusters": [], "merge_suggestions": []}

    def test_merge_suggestions_choose_first_as_keeper(self):
        client = MagicMock()
        client.search.return_value = {
            "results": [
                _make_page("m1", "Side hustle dashboard draft"),
                _make_page("m2", "Side hustle dashboard draft copy"),
            ],
            "has_more": False,
        }
        client.blocks.children.list.return_value = {
            "results": [],
            "has_more": False,
        }
        out = analyze.find_duplicates(client, threshold=0.5)
        if out["clusters"]:
            sug = out["merge_suggestions"][0]
            assert sug["keep"] == out["clusters"][0]["page_ids"][0]


# -----------------------------------------------------------------------------
# audit_orphans
# -----------------------------------------------------------------------------


class TestAuditOrphans:
    def test_identifies_orphan_pages(self, mock_client):
        out = analyze.audit_orphans(mock_client)
        orphan_ids = {x["id"] for x in out["orphans"]}
        # p4 has no parent (workspace) and is not referenced
        assert "p4" in orphan_ids

    def test_does_not_flag_referenced_pages(self, mock_client):
        out = analyze.audit_orphans(mock_client)
        orphan_ids = {x["id"] for x in out["orphans"]}
        # p2 and p3 have parent p1
        assert "p2" not in orphan_ids
        assert "p3" not in orphan_ids

    def test_suggests_archive_for_old_orphans(self, mock_client):
        out = analyze.audit_orphans(mock_client)
        old_orphan = next(o for o in out["orphans"] if o["id"] == "p4")
        assert old_orphan["days_since_edit"] >= 90
        assert old_orphan["suggested_action"] == "archive"

    def test_returns_total_count(self, mock_client):
        out = analyze.audit_orphans(mock_client)
        assert out["total_pages_scanned"] == 4
        assert isinstance(out["orphan_count"], int)
