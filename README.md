# bf_trader_py

Betfair algorithmic trading application. Monitors football markets via the Betfair API, tracks odds over time in a PostgreSQL database, and identifies trading opportunities based on configurable strategies.

## Prerequisites

- Python 3.12+
- Docker (for running PostgreSQL)
- Betfair account with API access and SSL certificates

## Setup

1. **Clone the repository**
   ```bash
   git clone https://github.com/agm-kuraudo/bf_trader_py.git
   cd bf_trader_py
   ```

2. **Create and activate a virtual environment**
   ```bash
   python -m venv .venv
   # Windows
   .venv\Scripts\activate
   # Linux/macOS
   source .venv/bin/activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r build/requirements.txt
   ```

4. **Configure environment variables**
   ```bash
   cp .env.example .env
   ```
   Edit `.env` and populate with your real credentials:
   - `BF_AppKey` — your Betfair application key
   - `BF_CRT_FILE` — path to your Betfair SSL certificate
   - `BF_KEY_FILE` — path to your Betfair SSL key
   - `BF_USERID` — your Betfair username
   - `BF_PWD` — your Betfair password
   - `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PWD` — PostgreSQL connection details

5. **Start PostgreSQL**
   ```bash
   docker run -d --name bf_postgres -p 5432:5432 -e POSTGRES_PASSWORD=yourpassword postgres:latest
   ```

## Running the Services

**Target Service** — identifies betting targets based on your strategy:
```bash
python target_service.py
```

**Monitor Service** — tracks odds for identified targets:
```bash
python monitor_service.py
```

**Analyse Service** — analyses gathered data:
```bash
python analyse_service.py
```

## Running Tests

```bash
# Unit tests
python -m pytest tests/unit_tests_betfair_objects.py -v

# Property-based tests
python -m pytest tests/test_property_dotenv.py -v

# All tests
python -m pytest tests/ -v
```

Note: `test_db_connection` and `test_db_object_ids` require a running PostgreSQL instance with valid credentials in `.env`.

## Project Structure

```
bf_trader_py/
├── api/                    # API layer (auth, HTTP calls, request bodies)
│   └── auth/              # Authentication (DotenvLoader, Auth class)
├── betfair/               # Betfair domain objects (events, markets, positions)
├── build/                 # Dockerfile, requirements.txt
├── certs/                 # SSL certificates (not committed)
├── config/                # Configuration files
├── decorators/            # Python decorators
├── logic/                 # Trading strategies
├── output/                # Logging and database output
├── scripts/               # Startup and utility scripts
├── tests/                 # Unit and property-based tests
├── web/                   # Flask web interface for monitoring
├── .env.example           # Template for environment configuration
├── BFDriver.py            # Main driver class orchestrating all operations
├── target_service.py      # Service: identify trading targets
├── monitor_service.py     # Service: monitor odds for targets
└── analyse_service.py     # Service: analyse gathered data
```

## Configuration

All secrets and configuration are managed via a `.env` file at the project root. See `.env.example` for the full list of required keys. The `.env` file is excluded from version control via `.gitignore`.
