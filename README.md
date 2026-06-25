# SmartBus AI Agent 🚌

> AI-powered bus travel assistant for Rwanda — built with Qwen Cloud for the Global AI Hackathon (Track 4: Autopilot Agent)

SmartBus is a production-grade conversational agent that automates end-to-end bus travel booking in Rwanda. Users interact in natural language (English, Kinyarwanda, French, or Swahili) and the agent handles everything: searching routes, comparing prices, holding seats, confirming bookings, and generating PDF receipts — all backed by real database operations, never hallucinated.

---

## Demo

> 📹 [Watch the 3-minute demo on YouTube](#) *(link coming soon)*

---

## Features

- **Multi-language support** — English, Kinyarwanda, French, Swahili
- **Multi-agent routing** — intent classifier delegates to Search, Booking, or Pricing sub-agent
- **Persistent memory** — remembers preferred routes and bus types across sessions
- **Booking state machine** — enforces search → hold → confirm → book flow server-side
- **Graceful fallbacks** — retries on quota/timeout errors, surfaces friendly messages
- **PDF receipt generation** — auto-generated after every confirmed booking
- **Human-in-the-loop** — seat is held and user must explicitly confirm before booking is created
- **Tool-grounded** — all trip data, prices, and seat counts come from real DB queries, never invented

---

## Architecture

```
User (Web / WhatsApp)
        │
        ▼
Django REST API  (POST /api/chat/)
        │
        ▼
Intent Router  ←── Traveler Memory (DB)
        │
   ┌────┼────────────┐
   ▼    ▼            ▼
Search  Booking   Pricing
Agent   Agent     Agent
   └────┼────────────┘
        │
        ▼
Tools Layer (deterministic)
search_routes · get_trips · compare_prices
check_seats · hold_seat · create_booking
get_booking_history · generate_booking_receipt
        │
        ▼
PostgreSQL  ←── Alibaba Cloud ECS
```

---

## Tech Stack

| Layer | Technology |
|---|---|
| LLM | Qwen Cloud (`qwen-plus-latest`) |
| Backend | Django 5 + Django REST Framework |
| Database | PostgreSQL (Alibaba Cloud) / SQLite (local dev) |
| PDF Generation | ReportLab |
| Deployment | Alibaba Cloud ECS |

---

## Project Structure

```
smartbus-ai-agent/
├── backend/
│   ├── agent/
│   │   ├── orchestrator.py       # LLM loop, state machine, memory, multi-agent routing
│   │   ├── tools/
│   │   │   ├── transit_tools.py  # All tool implementations (pure Django ORM)
│   │   │   ├── dispatcher.py     # Tool registry and safe execution wrapper
│   │   │   └── schemas.py        # OpenAI-compatible tool schemas for Qwen
│   │   ├── views.py              # HTTP layer (POST /api/chat/)
│   │   └── tests/
│   │       └── test_agent.py     # 23 tests, zero API tokens consumed
│   ├── transit/
│   │   └── models.py             # Route, Trip, Seat, Booking, Traveler, TravelerPreference
│   └── smartbus/
│       └── settings.py
├── frontend/
│   ├── templates/chat.html
│   └── static/
│       ├── css/chat.css
│       └── js/chat.js
├── alibaba_cloud/
│   └── deployment.py             # Proof of Alibaba Cloud deployment
├── .env.example
└── README.md
```

---

## Quick Start

### Prerequisites
- Python 3.12+
- Qwen Cloud API key ([sign up here](https://home.qwencloud.com))

### Setup

```bash
# Clone the repo
git clone https://github.com/justinjunior23/smartbus-ai-agent.git
cd smartbus-ai-agent

# Create virtual environment
cd backend
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # Mac/Linux

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env and add your DASHSCOPE_API_KEY

# Run migrations
python manage.py migrate

# Load sample data (routes, towns, buses)
python manage.py loaddata fixtures/sample_data.json

# Start the server
python manage.py runserver
```

Then open [http://127.0.0.1:8000](http://127.0.0.1:8000)

---

## Running Tests

Zero API tokens consumed — all LLM calls are mocked:

```bash
cd backend
python manage.py test agent.tests.test_agent --verbosity=2
```

23 tests covering: state machine enforcement, hallucination prevention, full booking flow, fallbacks, HTTP layer, and multilingual input.

---

## Environment Variables

| Variable | Description |
|---|---|
| `DASHSCOPE_API_KEY` | Your Qwen Cloud API key |
| `QWEN_BASE_URL` | `https://dashscope-intl.aliyuncs.com/compatible-mode/v1` |
| `QWEN_MODEL` | `qwen-plus-latest` |
| `DATABASE_URL` | PostgreSQL connection string (production) |

---

## Hackathon Track

**Track 4: Autopilot Agent**

SmartBus automates a real-world business workflow end-to-end:
- Handles ambiguous natural language input across 4 languages
- Invokes 10 external tools against a live database
- Enforces human-in-the-loop at the critical booking confirmation step
- Recovers gracefully from API errors with retries and fallback messages
- Remembers traveler preferences across sessions for personalization

---

## License

MIT — see [LICENSE](LICENSE)