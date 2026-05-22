# Notion Workspace Automation MCP

> **The first MCP server that analyzes, cleans, and rebuilds Notion workspaces — for Claude / Cursor / Windsurf.**

[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11+-green.svg)](https://www.python.org/)
[![FastMCP](https://img.shields.io/badge/built%20with-FastMCP-orange.svg)](https://github.com/jlowin/fastmcp)

## ✨ What it does

While Notion's official MCP only **reads and writes** pages, this server **analyzes structure, finds duplicates, archives stale content, and rebuilds workspaces** — in 30 minutes instead of 8 hours.

Built by the creator of [measurement-uncertainty.mcpize.run](https://measurement-uncertainty.mcpize.run) — the world's first MCP server for GUM-compliant measurement uncertainty.

## 🛠️ 10 Tools

### Analyze (3)
- `analyze_workspace` — Full page tree, DB graph, relation map
- `find_duplicates` — TF-IDF clustering of similar pages (suggest merges)
- `audit_orphans` — Pages no one links to (suggest archive)

### Organize (3)
- `archive_stale` — Pages untouched > N days
- `consolidate_duplicates` — Auto-merge a duplicate cluster
- `rebuild_hierarchy` — Suggest a new page tree based on usage

### Build (4)
- `apply_template` — Drop a template into any page (one tool call)
- `clone_workspace` — Mirror one workspace into another (for job changes)
- `create_dashboard` — Generate KPI dashboards from any metric DB
- `sync_databases` — Two-way sync between DBs with field mapping

## 🚀 Install (60 seconds)

```bash
npx -y mcpize connect @kyb8801/notion-workspace-automation
```

Or manually in `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "notion-workspace": {
      "command": "npx",
      "args": ["-y", "@kyb8801/notion-workspace-automation"],
      "env": {
        "NOTION_TOKEN": "your-integration-token"
      }
    }
  }
}
```

## 💰 Pricing

| Tier | Price | Limits |
|---|---|---|
| Free | $0 | 50 calls/month, single workspace |
| **Pro** | **$19/month** | Unlimited calls, priority |
| Team | $79/month | 5 workspaces, priority support |

85% of revenue goes to the developer via MCPize Stripe Connect.

## 📊 Use cases

### Use case 1: "I just got a new job and need to migrate my Notion"

```
You: Hey Claude, clone my old workspace into the new one.
Claude (calling clone_workspace): Done. 87 pages, 12 DBs, 3 levels deep. 30 seconds.
```

### Use case 2: "My Notion has 200+ pages and I can't find anything"

```
You: Analyze my workspace and tell me what's worth keeping.
Claude (calling analyze_workspace + audit_orphans): 
  - 89 pages haven't been touched in 90+ days
  - 23 pages have zero inbound links
  - 14 duplicate clusters detected (potential merges)
Want me to archive the stale ones?
```

### Use case 3: "I want to sell my side hustle Notion as a template"

```
You: Clone my "Side Hustle OS" workspace, anonymize, and turn into a template.
Claude (calling clone_workspace + apply_template): Done.
  Template URL: notion.so/templates/xyz123 ready for Gumroad listing.
```

## 🔐 Privacy

- Your Notion content **never leaves your computer**. The MCP server runs locally; only Notion API tokens are stored in your env.
- No telemetry. No analytics. No external API calls except Notion.
- MIT licensed. Audit the code.

## 🧪 Tests

```bash
pytest tests/ -v
# 30+ test cases, ~95% coverage on tool logic
```

## 🛣️ Roadmap

- v1.0 (2026.05): 10 tools, MCPize live
- v1.1 (2026.06): Slack/Linear connectors
- v2.0 (2026.07): Visual workspace map (Mermaid output)
- v2.1: GitHub-style activity heatmap

## 📜 License

MIT © Yongbeom Kim (kyb8801)

## 🔗 Related

- [measurement-uncertainty MCP](https://measurement-uncertainty.mcpize.run) — GUM uncertainty analysis
- [B1 MCP Server Boilerplate Notion Guide](https://kyb8801.gumroad.com/l/mcp-boilerplate) — How to build your own MCP
- [B3 AI Side Hustle 100-Instance Dashboard](https://kyb8801.gumroad.com/l/100-instance-dashboard) — The workspace this MCP was built to manage

---

**Built in 8 days. Inspired by managing a 50+ page Notion side-hustle workspace where manual cleanup took 8 hours.**
