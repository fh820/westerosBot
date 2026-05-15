# WesterosBot

A feature-rich, modular Discord bot simulating large-scale strategy and roleplay mechanics for a Game of Thrones–style world. Built as a production-capable Python application with clear separation of concerns, background task processing, and a pluggable service layer — ideal to showcase system design, asynchronous programming, and distributed task orchestration on a resume.

Project Summary
- Purpose: Simulates territorial control, battles, economy, diplomacy, and scouting in a persistent game world via Discord interactions.
- Scope: Multi-cog Discord bot with background workers, data persistence, scenario-driven starting states, and UI views for interactive gameplay.
- Role: Designed and implemented core bot architecture, wrote high-throughput battle resolution logic, integrated Celery for background tasks, and built robust domain services for gameplay rules and world updates.

Key Features
- Real-time gameplay: Discord command handlers and interactive views for player actions.
- Automated tasks: Asynchronous background jobs using Celery for battle resolution and periodic world updates.
- Battle system: Deterministic, testable battle math implemented in the service layer and Celery tasks.
- Modular cogs: Feature separation across app/cogs (e.g., warfare, economy, scouting).
- Data layer: Repository pattern with models under app/db/.
- Scenario support: Multiple starting scenarios under app/scenarios/ for reproducible game states and testing.
- UI components: Rich interactive views under app/ui/ (paginators, modals, battle viewer).

Architecture & Design
- Service-oriented: Core game logic lives in app/services/ to keep command handlers thin and testable.
- Worker model: Celery separates short-lived UI requests from long-running simulations and batch updates.
- Separation of concerns: bot wiring in app/bot.py, services independent of transport, repositories abstract persistence.
- Testability: Business logic isolated from Discord API calls for unit testing of rules and battle math.

Tech Stack
- Language: Python 3.12
- Frameworks/Libraries: discord.py, Celery, Redis/RabbitMQ (broker), SQL/JSON-backed persistence
- Tooling: virtualenv, pytest, Docker (Dockerfile + docker-compose.yml)

Getting Started (developer)
- Clone repo:
  git clone <repo-url>
  cd WesterosBot
- Create virtual environment (Windows PowerShell):
  python -m venv .venv
  .\.venv\Scripts\Activate.ps1
- Install dependencies:
  pip install -r requirements.txt
- Run bot locally:
  - Configure environment variables (Discord token, Celery broker URL, DB path) in your shell or a .env file.
  - Start Celery worker:
    celery -A app.celery_app worker --loglevel=info
  - Start the bot:
    python -m app.bot

Development Notes
- Entry points: The Discord client is initialized in app/bot.py. Background task scheduling is configured in app/celery_app.py.
- Notable modules:
  - app/services/warfare_service.py — battle orchestration and resolution.
  - app/services/gameplay_service.py — rules engine for movement, sieges, and events.
  - app/tasks/battle_tasks.py — Celery tasks for heavy battle computation.
- Data: World and scenario data live in master_world_data.json and app/scenarios/.

Deployment
- Docker: A Dockerfile and docker-compose.yml are included to containerize the bot and worker services. Typical flow:
  docker-compose up --build
- Scale: Celery workers can be scaled horizontally to handle spikes in battle computation.

Highlights & Achievements 
- Designed and implemented a modular, testable game engine for asynchronous multiplayer gameplay.
- Built a battle resolution system capable of simulating large engagements with deterministic, well-documented math.
- Integrated Celery for decoupled, scalable background processing of compute-heavy simulations.
- Authored scenario-driven test harnesses to validate game balance and reproducibility across releases.
- Reduced UI latency by moving heavy computation off the request path, improving player experience.

Contact
- Add your preferred email or GitHub handle here for inquiries.
