# Main Street Motors — Inventory Replenishment Agent

An AI agent that monitors a car dealership's Dealer Management System (DMS)
and automatically orders replacement vehicles when inventory is depleted by sales.

## What it does

Every 3 simulation days the agent:

1. Queries `dms.db` for cars sold since the last order
2. If more than 1 car has accumulated, calls **OpenAI gpt-4o-mini** to select
   the best replacement vehicles from the market pool (`cars_available.db`)
3. Adds the selected cars to DMS inventory
4. Records a debit transaction in the ERP general ledger
5. Marks purchased cars as `used` in the market catalogue to prevent re-ordering

Sales accumulate across cycles — if only 1 car sells in the first 3-day window
and 1 more sells in the second, the total of 2 triggers an order on the second cycle.

The agent also auto-detects when a new simulation run starts and resets its state.

## Setup

```bash
# Clone and enter the repo
git clone https://github.com/paulwilcox99/newcar_agent.git
cd newcar_agent

# Create virtual environment and install dependencies
bash setup.sh

# Add your OpenAI API key
echo "OPENAI_API_KEY=sk-your-key-here" > .env
```

## Usage

Run the simulation and agent in separate terminals.

**Terminal 1 — simulation** (disable the built-in new_cars worker):
```bash
cd /path/to/auto-dealership
.venv/bin/python main.py --reset --days 14 --seed 42 --disable-workers new_cars --step-mode
```

**Terminal 2 — agent:**
```bash
.venv/bin/python agent.py
```

### Options

| Flag | Default | Description |
|---|---|---|
| `--poll-interval N` | `3.0` | Seconds between state file checks |
| `--db-dir PATH` | `../auto-dealership/data` | Path to dealership database directory |
| `--dry-run` | off | Log decisions without writing to any database |

### Dry-run / replay testing

Use `replay_test.py` to replay a completed simulation at human pace (no step mode needed):

```bash
# Terminal 1
.venv/bin/python agent.py --dry-run

# Terminal 2
.venv/bin/python replay_test.py
```

## Databases

| Database | Access | Purpose |
|---|---|---|
| `cars_available.db` | Read + update status | Market pool — source of replacement vehicles |
| `dms.db` | Read + insert | Live lot inventory |
| `erp.db` | Append | General ledger — records purchase debits |
| `crm.db` | Read | Confirms sale records |

## File structure

```
agent.py          # Entry point: polling loop + agent orchestration
inventory.py      # All database reads and writes
llm.py            # OpenAI API call + structured response parsing
state.py          # Agent state persistence (agent_state.json)
replay_test.py    # Test helper: replays completed simulation dates
requirements.txt  # Python dependencies
setup.sh          # One-time venv setup script
.env.example      # API key template
```

## Requirements

- Python 3.12+
- OpenAI API key with access to `gpt-4o-mini`
- [Main Street Motors simulation](https://github.com/paulwilcox99/auto-dealership) running alongside
