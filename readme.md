
ShopEase AI Support Desk — MCP Server + AutoGen Agents
The real-world problem
ShopEase is an online electronics store that gets thousands of support chats a day. About 70% of them are the same few questions: "Where is my order?", "Is this back in stock?", "I want a refund." Human agents answer them by switching between four internal systems: orders DB, carrier tracking, warehouse inventory and the ticketing tool.

Goal: a team of AI agents resolves the routine cases end to end using live company data, follows refund policy without exception, and hands only the hard cases to humans, with a clean ticket attached.

Why MCP?
Without MCP, every agent framework (AutoGen, LangGraph, CrewAI, Claude Desktop, …) needs its own hand-written wrappers around the same internal APIs. With MCP:

Without MCP	With MCP
Tool code copied into every agent project	One MCP server owns the integrations
Policy logic lives in prompts (LLM can ignore it)	Policy is enforced in the server (issue_refund rejects out-of-policy requests)
Changing an API means redeploying agents	Change the server; agents re-discover tools automatically
Hard to share across teams	Same server works over stdio (local) or SSE/HTTP (shared service)
Architecture
 Customer message
        │
        ▼
┌──────────────────── AutoGen SelectorGroupChat ────────────────────┐
│                                                                    │
│  triage_agent ──► order_agent ──► refund_agent ──► escalation_agent│
│  (find order)    (track/stock)   (refunds)        (human ticket)  │
│        │               │               │                 │         │
└────────┼───────────────┼───────────────┼─────────────────┼─────────┘
         │   MCP (stdio or SSE) — tools discovered at runtime         
         ▼               ▼               ▼                 ▼
┌──────────────────── shopease-support MCP server ───────────────────┐
│ list_customer_orders · get_order · track_shipment · check_inventory │
│ issue_refund (policy enforced) · create_ticket · policy://refunds  │
└───────────────────────────────┬────────────────────────────────────┘
                                ▼
          SQLite (stands in for order DB / carrier API / WMS / ticketing)
Least privilege: each agent gets only the tools it needs. The triage agent cannot issue refunds. Only refund_agent holds issue_refund.

Files
File	What it does
mcp_server/support_server.py	FastMCP server: 6 tools + 1 resource, seeds a SQLite DB on first run
agents/support_team.py	AutoGen team: discovers MCP tools, gives each agent its own subset, routes with SelectorGroupChat
requirements.txt	Dependencies (note the mcp<2 pin)
Run it
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
set OPENAI_API_KEY=sk-...
Run the three scripted scenarios:

python agents/support_team.py --demo
Or chat interactively:

python agents/support_team.py
Production style: run the server as a shared network service, then connect agents to it.

python mcp_server/support_server.py --transport sse --port 8765
python agents/support_team.py --sse
Inspect or test the server alone with the MCP Inspector (no LLM needed):

mcp dev mcp_server/support_server.py
Demo scenarios and expected flow
#	Customer says	Agent path	Outcome
1	Priya: "Where is my smart watch?"	triage → order_agent (track_shipment)	Live tracking events + ETA, RESOLVED
2	Arjun: refund for ORD-1004	triage → refund_agent (issue_refund) → escalation	Server rejects (delivered 40 days ago, window is 30). Agent explains and opens a ticket if needed
3	Meera: cracked UltraBook, refund 1299.00	triage → refund_agent → escalation_agent	Refund goes to pending_human_review (over the auto limit), high-priority ticket created
Scenario 2 shows why the policy lives in the MCP server. Even if the LLM is persuaded to try, the server refuses.

Sample data
Order	Customer	Item	Status
ORD-1001	priya@example.com	Wireless Headphones	delivered 8 days ago
ORD-1002	priya@example.com	Smart Watch Pro	in transit (live events unlock over time)
ORD-1003	arjun@example.com	Mechanical Keyboard ×2	processing
ORD-1004	arjun@example.com	Wireless Headphones	delivered 40 days ago
ORD-1005	meera@example.com	UltraBook 14	delivered 2 days ago, 1299.00
Delete mcp_server/shopease.db to reset the data.

Taking it further
Swap SQLite for real APIs (Shopify, a carrier API such as Shiprocket, Zendesk/Freshdesk) inside the same tool functions. The agents don't change.
Add a UserProxyAgent to require human approval before issue_refund runs.
Put the SSE server behind auth and let several agent teams (support, sales, ops) share it.
Swap OpenAIChatCompletionClient for any AutoGen model client (Azure OpenAI, Anthropic, Ollama).
