# Campus Tech Store: agentic commerce with local models

An e-commerce shop built on [Django Oscar](https://github.com/django-oscar/django-oscar) 4.2 with two AI agents that run on a local model through [Ollama](https://ollama.com). No API keys and no subscriptions: everything runs on one laptop.

- The Shopping Agent works for a buyer. It turns a project goal into a verified cart, buys delegated items later while the buyer is offline, and negotiates bulk prices.
- The Store Agent works for the shop. It reorders stock from the supplier when sales or buyer demand require it, writes a briefing for staff, negotiates with the buyers' agents and answers staff questions in a chat.

Both agents act only inside mandates that a person signed. A mandate is a set of terms (product, quantity, price cap, expiry, autonomy level) with an HMAC-SHA256 signature. The Mandate Guard checks every action against the signed terms in plain Python before it has any effect, so the model never decides a money limit.

## Requirements

- Windows 10/11, Python 3.12 or newer (built with 3.14; tested with 3.13)
- Ollama with `qwen3:4b` pulled: `ollama pull qwen3:4b`
- About 4 GB of free RAM for the model. A CPU is enough; each model call takes 5 to 30 seconds.

## Setup

Double-click `setup.bat`. It creates `venv`, installs the packages, creates the database, loads the demo data and runs the safeguard tests. The same steps by hand:

```
py -3 -m venv venv
venv\Scripts\activate.bat
python -m pip install -r requirements.txt
python manage.py migrate
python manage.py seed_demo_store
python manage.py evaluate_agents
```

## Run

Start Ollama, then double-click `run.bat`. It opens a second window called "Agent worker" and starts the web server. Keep both windows open.

| Page | URL | Login |
|---|---|---|
| Shopping Agent (buyer) | http://127.0.0.1:8000/agent/ | student@example.com / student12345 |
| Agent Control Center (staff) | http://127.0.0.1:8000/dashboard/assistant/control/ | admin@example.com / admin12345 |
| Policy and autonomy | http://127.0.0.1:8000/dashboard/assistant/policy/ | admin |
| Store Agent chat | http://127.0.0.1:8000/dashboard/assistant/ | admin |

Use two browsers (or one normal and one private window) so the student and the admin are logged in at the same time. `reset_demo.bat` puts the shop back to its starting state.

## Demonstration script (about 10 minutes)

The student starts with $500 in the Campus Wallet and has bought a breadboard kit and a DHT22 sensor before. The MQ-135 air-quality sensor is sold out.

1. Student, Plan a project: click "Air-quality monitor" and "Plan it". The steps appear live: the agent reads the catalogue and the student's order history, drafts a cart, and the verifier checks budget, stock and part compatibility. If a check fails (for example an analog sensor on a board without analog input), the agent revises the cart. It leaves out the breadboard because the student already owns one. The step log also shows that instruction-like text in the "37-in-1 Sensor Kit" description was removed before the model saw it.
2. The MQ-135 line is out of stock. Click "Sign and hold funds": this signs an intent mandate, holds $4.90 in the wallet and tells the Store Agent about the demand. Then click "Approve and pay" for the in-stock items. The order is placed through Oscar with status "Paid".
3. Staff, Agent Control Center: the Store Agent has already placed a purchase order for MQ-135 on its own, because the amount is under the $150 auto-approve limit in the signed policy. The ESP32 sale may also have triggered a second purchase order that is above the limit and waits in "Needs your decision".
4. Click "Receive delivery" on the MQ-135 purchase order. The stock change wakes the Shopping Agent, which buys the sensor for the student with the held funds. The activity log shows "Bought under M-x (buyer not present)", and the student's page shows the delegation as fulfilled.
5. Student, Bulk quote: 10 x Arduino Uno R4 WiFi with a private maximum of $26.00. The two agents negotiate for up to 3 rounds. Neither side sees the other's limit. If a model offers past its owner's limit or writes the limit in a message, the guard corrects it and the transcript shows the correction. A deal becomes a single-use Oscar voucher, and the student pays the deal price.
6. Staff, Policy: switch negotiation to "L4 Approver (ask)" and sign. Repeat a bulk quote: the deal now waits for staff approval in the inbox.
7. Student, Delegate a purchase: type "Get me one Raspberry Pi 5 if the price stays under $85 this month", click "Draft mandate", then sign it. The kit costs $119, so the agent waits. Staff, Control Center: click "Demo: tamper with terms" on that delegation. The terms change without a new signature, the Shopping Agent refuses the mandate and the held funds return to the wallet.
8. Optional: "Write briefing", or ask the Store Agent chat "How many days will the ESP32 stock last?".

## How it works

```
 Buyer (browser)                         Staff (Oscar dashboard)
   |  plan / delegate / bulk quote          |  policy, approvals, deliveries
   v                                        v
 Django views ---- create AgentRun ----> run queue (database)
                                            |
 Oscar signals: stock saved, order placed --+--> agent_worker (separate process)
                                                   |  1. model call (Ollama, JSON schema output)
                                                   |  2. Mandate Guard (Python, signed terms)
                                                   |  3. Oscar action: order, voucher, purchase order
                                                   v
                                     AgentAction audit log, live steps in the UI
```

The web server never waits for the model. Every agent task is an `AgentRun` row; the worker processes the queue in priority order (buyer-facing tasks first) and writes each step to the row, which the pages poll every 1.5 to 4 seconds. Stock changes (`post_save` on Oscar's `StockRecord`, which Oscar also sends when an order allocates stock) queue work for both agents without anyone asking.

### Autonomy levels

The seller policy sets how much the Store Agent may do alone. The levels follow Feng, McDonald and Zhang (2025), where the user's role moves from operator to observer.

| Setting | Reordering | Bulk negotiation |
|---|---|---|
| off | | L1 Operator: list price only |
| suggest | L2 Collaborator: drafts only | |
| ask | L4 Approver: staff approve each purchase order | L4 Approver: staff approve each deal |
| auto | L5 Observer within the limit: places orders up to the auto-approve amount | L5 Observer within the floor: closes deals above the policy floor |

### Safeguards

| Risk | Safeguard | Where |
|---|---|---|
| Agent pays above the buyer's limit | Price caps and budgets are checked in code before payment; negotiation offers are capped | `agentic/guard.py` |
| Agent reveals a private limit | Limits are removed from negotiation messages before they are stored or shown | `redact_secret` |
| Seller text manipulates the buyer agent | Instruction-like sentences are removed from product descriptions; the catalogue order is shuffled with a fixed seed to reduce position bias | `sanitize_untrusted`, `catalogue_lines` |
| Terms changed after the owner agreed | HMAC-SHA256 signature over the terms; a mismatch cancels the mandate and releases funds | `sign`, `verify` |
| Store Agent over-orders | Policy limit plus a hard block above 3x the computed need | `check_reorder` |
| Discount on a scarce item | No discount when stock after the sale would last less than the policy minimum | `seller_floor` |
| Cart that does not work | Verifier checks controller board, analog input, WiFi, the measured quantity and the budget; failures go back to the model | `verify_cart` |

`python manage.py evaluate_agents` runs 24 of these scenarios without the model in about one second. `python manage.py evaluate_agents --llm` also runs a cart plan and a negotiation on the local model and prints the timings.

### Module concepts used

- Small language models on a CPU: `qwen3:4b` with thinking turned off, temperature 0, `num_ctx` 8192 and `keep_alive` 30 minutes, so the model loads once (`agentic/llm.py`).
- Structured output: every agent decision is a JSON object constrained by a schema passed to Ollama's `format` parameter, which keeps a 4B model's output parseable.
- Ollama Python library with system/user/assistant/tool message roles (`assistant/agent.py` for the tool-calling chat).
- Local memory: the Shopping Agent reads the buyer's Oscar order history and skips parts they already own.

## Configuration

Set these in the terminal before `run.bat`, for example `set MODEL=llama3.2:3b`.

| Variable | Default | Meaning |
|---|---|---|
| `MODEL` | `qwen3:4b` | Ollama model for both agents and the chat |
| `OLLAMA_HOST` | `http://127.0.0.1:11434` | Ollama address |
| `AGENT_NUM_CTX` | `8192` | Context window for agent calls |
| `AGENT_SCAN_MINUTES` | `30` | Periodic stock scan (0 turns it off; the first scan runs one interval after the worker starts) |

## Project structure

| Path | What it is |
|---|---|
| `agentic/models.py` | Wallet, mandates, purchase orders, agent runs, audit log, negotiations |
| `agentic/guard.py` | Mandate Guard: signatures, spending checks, negotiation guard, sanitizer |
| `agentic/shopping_agent.py` | Buyer agent: plan and verify a cart, delegations, negotiation moves |
| `agentic/store_agent.py` | Seller agent: reorder decisions, briefing, negotiation moves |
| `agentic/negotiation.py` | Agent-to-agent negotiation loop and deal vouchers |
| `agentic/commerce.py` | Campus Wallet, stock metrics, paid checkout through Oscar's `OrderCreator` |
| `agentic/signals.py` | Store events that queue agent work |
| `agentic/management/commands/agent_worker.py` | Background worker |
| `agentic/management/commands/evaluate_agents.py` | Safeguard and model evaluation |
| `shopper/` | Buyer pages (Shopping Agent) |
| `assistant/` | Staff pages: Control Center, policy, Store Agent chat |
| `assistant/management/commands/seed_demo_store.py` | Demo catalogue, orders, accounts, wallet and policy |

## Troubleshooting

- "Agent worker: Offline" or requests stay "queued": the worker window is closed. Run `venv\Scripts\python.exe manage.py agent_worker` in this folder.
- A run fails with "Failed to connect to Ollama": start the Ollama app, then check `ollama list` shows `qwen3:4b`.
- The first request after starting is slow: the worker loads the model at start-up (about 10 to 30 seconds on a CPU). Wait for "Model loaded" in the worker window before the demonstration.
- "database is locked": the database runs in WAL mode with a 30-second timeout, so this should not happen; if it does, restart both windows.
- Payment is simulated: the Campus Wallet is stored value inside this demo and no payment provider is involved.

## References

- Anthropic (2024), [Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)
- Feng, McDonald and Zhang (2025), [Levels of Autonomy for AI Agents](https://arxiv.org/abs/2506.12469)
- Google Cloud (2025), [Announcing the Agent Payments Protocol (AP2)](https://cloud.google.com/blog/products/ai-machine-learning/announcing-agents-to-payments-ap2-protocol)
- Microsoft Research (2025), [Magentic Marketplace: An Open-Source Environment for Studying Agentic Markets](https://arxiv.org/abs/2510.25779)
- Zhu et al. (2025), [The Automated but Risky Game: Modeling and Benchmarking Agent-to-Agent Negotiations and Transactions in Consumer Markets](https://arxiv.org/abs/2506.00073)
