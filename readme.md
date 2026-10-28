# ShopEase AI Support Desk

An AI customer-support desk for an online electronics store. A team of [AutoGen](https://microsoft.github.io/autogen/) agents resolves routine chats (order tracking, stock checks, refunds) using live company data exposed through an [MCP](https://modelcontextprotocol.io) server, and hands the hard cases to humans with a ticket attached. A Streamlit app wraps it with sign-in, role-based pages and a staff back office.

## The problem

About 70% of support chats are the same few questions: "Where is my order?", "Is this back in stock?", "I want a refund." Human agents answer them by switching between four internal systems (orders, carrier tracking, inventory, ticketing).

**Goal:** agents resolve the routine cases end to end, follow refund policy without exception, and escalate only what needs a person.

## Why MCP?

| Without MCP | With MCP |
|---|---|
| Tool code copied into every agent project | One MCP server owns the integrations |
| Policy lives in prompts (the LLM can ignore it) | Policy is enforced in the server (`issue_refund` rejects out-of-policy requests) |
| Changing an API means redeploying agents | Change the server; agents re-discover tools automatically |
| Hard to share across teams | Same server works over stdio (local) or SSE/HTTP (shared) |

## Architecture

```
Customer message
       │
       ▼
┌─────────────── AutoGen SelectorGroupChat ───────────────┐
│ triage_agent → order_agent → refund_agent → escalation  │
│ (find order)   (track/stock)  (refunds)     (human ticket)
└───────┬────────────┬─────────────┬──────────────┬───────┘
        │   MCP (stdio or SSE), tools discovered at runtime
        ▼            ▼             ▼              ▼
┌──────────────── shopease-support MCP server ────────────┐
│ list_customer_orders · get_order · track_shipment       │
│ check_inventory · issue_refund · cancel_order           │
│ create_ticket · resource: policy://refunds              │
└────────────────────────────┬────────────────────────────┘
                             ▼
              SQLite (stands in for order DB, carrier API, WMS, ticketing)
```

**Least privilege:** each agent only receives the tools it needs. Triage cannot issue refunds; only `refund_agent` holds `issue_refund`.

**Refund policy (enforced in the server):** 30-day window, one non-rejected refund per order, and refunds over 200.00 go to `pending_human_review` instead of being auto-approved.

## The web app

`streamlit run streamlit_app.py`

| Role | Pages |
|---|---|
| Customer | Support chat, my tickets and refunds |
| Staff | Ticket inbox, refund approvals, contacts (CRM), dashboard, support chat |

Passcodes are salted PBKDF2 hashes, session tokens are stored only as SHA-256, all SQL is parameterised, and free text is sanitised and length-capped. Permissions are enforced in the data layer (`desk.py`), not just the UI.

## Project layout

| File | What it does |
|---|---|
| `streamlit_app.py` | Sign-in and role-based navigation |
| `app_pages/` | Chat, my tickets, inbox, refund approvals, contacts, dashboard |
| `app.py` | AutoGen agent team; CLI with `--demo` and `--sse` modes |
| `mcp_server.py` | FastMCP server: 7 tools + 1 resource, seeds SQLite on first run |
| `desk.py` | Support-desk data layer (tickets, refund approvals, ratings, metrics) |
| `auth.py` | Accounts and sessions (`auth.db`) |
| `ui.py` | Shared styling |
| `provision_customers.py` | Creates logins for customers who have orders but no account |
| `generate_seed_data.py` | Generates 100 records per table into `seed_data/` |
| `evals/run_evals.py` | Security and policy checks |

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

Create a `.env` file:

```
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini     # optional
```

Demo logins are read from `.streamlit/secrets.toml` (`[[users]]` entries with `email`, `role`, `salt`, `hash`). That file is git-ignored; create your own.

## Run

```bash
# Web app
streamlit run streamlit_app.py

# Agent team in the terminal
python app.py --demo        # three scripted scenarios
python app.py               # interactive chat

# MCP server as a shared network service, then connect agents to it
python mcp_server.py --transport sse --port 8765
python app.py --sse

# Inspect the server alone (no LLM needed)
mcp dev mcp_server.py
```

## Demo scenarios

| # | Customer says | Agent path | Outcome |
|---|---|---|---|
| 1 | Priya: "Where is my smart watch?" | triage → order_agent (`track_shipment`) | Live tracking events and ETA |
| 2 | Arjun: refund for ORD-1004 | triage → refund_agent → escalation | Server rejects: delivered 40 days ago, window is 30 days |
| 3 | Meera: cracked UltraBook, refund 1299.00 | triage → refund_agent → escalation | Refund goes to `pending_human_review`, high-priority ticket created |

Scenario 2 shows why policy lives in the server: even if the LLM is persuaded to try, the server refuses.

## Evals

```bash
python evals/run_evals.py           # tool-level checks, no LLM calls, about 1 second
python evals/run_evals.py --agent   # also runs the real agent team (uses OpenAI tokens)
```

Runs against a throwaway database, never `shopease.db`. Exits with code 1 on any failure, so it can gate CI.

## Sample data

Delete `shopease.db` to reset to the five demo orders (ORD-1001 to ORD-1005 for priya, arjun and meera `@example.com`). `python generate_seed_data.py --load` appends 100 generated records per table.

## Taking it further

- Swap SQLite for real APIs (Shopify, a carrier API, Zendesk) inside the same tool functions; the agents don't change.
- Require human approval before `issue_refund` runs with a `UserProxyAgent`.
- Put the SSE server behind auth and share it across support, sales and ops agent teams.
- Swap `OpenAIChatCompletionClient` for any AutoGen model client (Azure OpenAI, Anthropic, Ollama).
