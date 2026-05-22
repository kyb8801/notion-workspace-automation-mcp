"""Tests for build tools."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.tools import build


# -----------------------------------------------------------------------------
# apply_template
# -----------------------------------------------------------------------------


class TestApplyTemplate:
    def test_requires_both_inputs(self):
        client = MagicMock()
        out = build.apply_template(client, template_url="", target_page_id="")
        assert "error" in out

    def test_extracts_id_from_url(self):
        client = MagicMock()
        client.blocks.children.list.return_value = {
            "results": [
                {"type": "paragraph", "paragraph": {"rich_text": []}, "id": "x"},
            ],
            "has_more": False,
        }
        url = "https://www.notion.so/template-3657f7ff1ca58127b096ec1777bea63f"
        out = build.apply_template(
            client, template_url=url, target_page_id="target123"
        )
        assert out["target_page_id"] == "target123"
        assert out["blocks_added"] == 1

    def test_chunks_large_block_lists(self):
        client = MagicMock()
        # 250 blocks → 3 chunks of 100/100/50
        client.blocks.children.list.return_value = {
            "results": [
                {"type": "paragraph", "paragraph": {"rich_text": []}, "id": str(i)}
                for i in range(250)
            ],
            "has_more": False,
        }
        out = build.apply_template(
            client,
            template_url="3657f7ff1ca58127b096ec1777bea63f",
            target_page_id="t1",
        )
        assert out["blocks_added"] == 250
        assert client.blocks.children.append.call_count == 3


# -----------------------------------------------------------------------------
# clone_workspace
# -----------------------------------------------------------------------------


class TestCloneWorkspace:
    def test_requires_root(self):
        out = build.clone_workspace(MagicMock(), MagicMock(), root_page_id="")
        assert "error" in out

    def test_creates_root_in_target(self):
        src = MagicMock()
        tgt = MagicMock()
        src.pages.retrieve.return_value = {
            "id": "src_root",
            "properties": {
                "Name": {"type": "title", "title": [{"plain_text": "Source Root"}]}
            },
        }
        tgt.pages.create.return_value = {"id": "tgt_root_new"}
        src.blocks.children.list.return_value = {
            "results": [],
            "has_more": False,
        }
        out = build.clone_workspace(src, tgt, root_page_id="src_root")
        assert out["target_root_id"] == "tgt_root_new"
        assert out["pages_cloned"] >= 1
        assert tgt.pages.create.called

    def test_anonymize_strips_email(self):
        # Direct test of helper
        text = "Contact me at user@example.com or 010-1234-5678"
        anon = build._anonymize_text(text)
        assert "user@example.com" not in anon
        assert "010-1234-5678" not in anon
        assert "[email]" in anon
        assert "[phone]" in anon


# -----------------------------------------------------------------------------
# create_dashboard
# -----------------------------------------------------------------------------


class TestCreateDashboard:
    def test_requires_both_ids(self):
        out = build.create_dashboard(
            MagicMock(), metric_db_id="", dashboard_parent_id=""
        )
        assert "error" in out

    def test_creates_dashboard_page(self):
        client = MagicMock()
        client.databases.query.return_value = {
            "results": [{"id": "r1"}, {"id": "r2"}, {"id": "r3"}]
        }
        client.pages.create.return_value = {"id": "dash_page"}
        out = build.create_dashboard(
            client,
            metric_db_id="db1",
            dashboard_parent_id="parent1",
            chart_types=["line"],
        )
        assert out["dashboard_page_id"] == "dash_page"
        assert out["rows_summarized"] == 3
        assert client.blocks.children.append.called


# -----------------------------------------------------------------------------
# sync_databases
# -----------------------------------------------------------------------------


class TestSyncDatabases:
    def test_requires_inputs(self):
        out = build.sync_databases(
            MagicMock(), source_db_id="", target_db_id="", field_mapping={}
        )
        assert "error" in out

    def test_invalid_direction(self):
        out = build.sync_databases(
            MagicMock(),
            source_db_id="a",
            target_db_id="b",
            field_mapping={"x": "y"},
            direction="bogus",
        )
        assert "error" in out

    def test_creates_target_rows_one_way(self):
        client = MagicMock()
        client.databases.query.return_value = {
            "results": [
                {
                    "id": "src_row_1",
                    "properties": {
                        "Title": {
                            "type": "title",
                            "title": [{"plain_text": "Hello"}],
                        }
                    },
                }
            ]
        }
        out = build.sync_databases(
            client,
            source_db_id="src_db",
            target_db_id="tgt_db",
            field_mapping={"Title": "Name"},
            direction="one-way",
        )
        assert out["created"] >= 1
        assert client.pages.create.called


# -----------------------------------------------------------------------------
# _extract_page_id helper
# -----------------------------------------------------------------------------


class TestExtractPageId:
    def test_accepts_raw_id(self):
        raw = "3657f7ff1ca58127b096ec1777bea63f"
        assert build._extract_page_id(raw) == raw

    def test_extracts_from_url(self):
        url = "https://notion.so/MyPage-3657f7ff1ca58127b096ec1777bea63f"
        out = build._extract_page_id(url)
        assert "3657f7ff" in out

    def test_returns_input_on_unknown_format(self):
        out = build._extract_page_id("not-a-notion-url")
        assert out == "not-a-notion-url"
