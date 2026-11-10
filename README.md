# Campus Tech Store: Django Oscar + local AI Store Ops Assistant

An e-commerce shop built on [Django Oscar](https://github.com/django-oscar/django-oscar)
(open source, BSD licence) with a new component: a staff-only AI assistant inside
Oscar's dashboard. The assistant runs a local language model through
[Ollama](https://ollama.com) and uses tools to read the shop's real catalogue,
stock and orders. It can propose restocks, which change stock only after a
staff member clicks Confirm.

Everything is free and runs on your own computer: no API keys, no subscriptions.

## Requirements

- Windows 10/11, Python 3.12 or newer (https://www.python.org/downloads/windows/)
- Ollama with a tool-capable model: `ollama pull qwen3:4b`

## Setup (once)

Double-click `setup.bat`, or in a terminal inside this folder:

```
python -m venv venv
venv\Scripts\activate.bat
python -m pip install -r requirements.txt
python manage.py migrate
python manage.py seed_demo_store
```

## Run

Double-click `run.bat`, or `python manage.py runserver 127.0.0.1:8000`.

- Storefront: http://127.0.0.1:8000
- Dashboard: http://127.0.0.1:8000/dashboard/ (login `admin@example.com` / `admin12345`)
- AI Assistant: dashboard menu "AI Assistant", or http://127.0.0.1:8000/dashboard/assistant/

Use another model: `set MODEL=llama3.2:3b` before `python manage.py runserver`.

Reset the demo data at any time: `python manage.py seed_demo_store`.

## Project structure

| Path | What it is |
|---|---|
| `shopsite/settings.py` | Oscar configuration, dashboard menu entry, Ollama settings |
| `shopsite/urls.py` | Oscar URLs plus `/dashboard/assistant/` |
| `assistant/apps.py` | Registers the assistant as an Oscar dashboard app (staff-only permissions) |
| `assistant/tools.py` | The 6 agent tools, querying Oscar's Product, StockRecord, Order and Line models |
| `assistant/agent.py` | Agent loop: model, tool calls, results, final answer (max 5 rounds) |
| `assistant/views.py` | Chat endpoint, Confirm/Cancel for restocks, live low-stock data |
| `assistant/templates/assistant/index.html` | Chat page rendered inside Oscar's dashboard layout |
| `assistant/management/commands/seed_demo_store.py` | Demo products, stock and 40 orders |

## Agent tools

| Tool | Type | Oscar data used |
|---|---|---|
| `search_products(query)` | read | Product, Category, StockRecord |
| `get_product_details(product_id)` | read | Product, StockRecord, order Lines (30-day sales) |
| `list_low_stock()` | read | StockRecord thresholds |
| `get_sales_summary(days)` | read | Order, Line |
| `get_recent_orders(limit)` | read | Order, Line |
| `propose_restock(product_id, quantity, reason)` | write, needs staff confirmation | StockRecord |
