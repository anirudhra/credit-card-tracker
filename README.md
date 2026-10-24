# Credit Card Tracker

A self-hosted, zero-dependency Python and SQLite web application for tracking credit card benefits, rotating 5% quarterly categories, selectable 3% choice categories, and live merchant recommendations across a multi-card portfolio.

## Features

- **7 Pre-Loaded Credit Cards**:
  - **Chase Sapphire Reserve** (`csr_795` refreshed $795 annual fee and `csr_550` legacy $550 annual fee)
  - **Chase Freedom** (`chase_freedom` - $0 annual fee, 5% / 5x Ultimate Rewards rotating quarterly categories up to $1,500/quarter, transferable to Chase Sapphire Reserve)
  - **Discover it Cash Back** (`discover_it` - $0 annual fee, 5% rotating quarterly categories up to $1,500/quarter)
  - **Amex EveryDay Credit Card** (`amex_everyday` - $0 annual fee, 2x U.S. Supermarkets up to $6,000/year, 20% Membership Rewards bonus with 20+ monthly purchases)
  - **Amex Blue Cash Everyday** (`amex_bce` - $0 annual fee, $7/month Disney Bundle credit, $15/month Home Chef credit, 3% U.S. Online Retail / Supermarkets / Gas up to $6,000/year each)
  - **Bank of America Customized Cash Rewards** (`boa_ccr` - $0 annual fee, selectable 3% Choice Category + 2% Grocery Stores & Wholesale Clubs up to $2,500 combined/quarter, Museums on Us)
  - **Bank of America Travel Rewards** (`boa_travel` - $0 annual fee, 1.5x-2.62x catch-all points on all purchases, 3x Bank of America Travel Center, no foreign transaction fees, Museums on Us)
- **Two-Section Landing Page (`Which Card to Use Today`)**:
  - **Section 1: Currently Valid**: Displays only currently active reward rates, active quarterly 5% categories, selectable Bank of America 3% choice category, and active statement credits.
  - **Section 2: Upcoming Rotations & Resets**: Previews next month's credit resets, the next Bank of America Museums on Us weekend, cardmember anniversary resets, next quarter's 5% rotating categories, and semi-annual/annual cap renewals.
- **First-Open-of-the-Day + Manual Vendor Sync (`Sync With Vendors`)**:
  - Automatically syncs with public Chase, American Express, Discover, and Bank of America benefit and calendar pages on the first visit of each calendar day (using a `last_sync_date` flag in SQLite with no background daemon or system service required), while also supporting on-demand manual sync.
- **Interactive Period Bubbles (`Perks & Bubbles Tracker`)**:
  - Left-click any monthly, quarterly, semi-annual, or annual period bubble to toggle 100% completion, or right-click (or click the edit button) to log partial dollar amounts, swipe counts, dates, and notes.
- **Calendar & Webhook Integrations**:
  - Export expiring perk reminders as an iCalendar (`.ics`) feed or send formatted digests to a Discord, Slack, or `ntfy.sh` webhook URL.

## Deployment Option 1: Docker Compose (Recommended)

The repository includes a `Dockerfile` and `docker-compose.yml` that run the application on port `8795` and persist the SQLite database in `./data/credit_card_tracker.db` via a bind mount.

### Start the Container

```bash
git clone https://github.com/anirudhra/credit-card-tracker credit-card-tracker
cd credit-card-tracker
docker compose up -d --build
```

Open `http://localhost:8795` (or `http://<server-ip>:8795`) in your browser.

### View Logs, Restart, or Stop

```bash
# View container logs
docker compose logs -f

# Restart the service
docker compose restart

# Stop the service (data in ./data/credit_card_tracker.db is preserved)
docker compose down
```

## Deployment Option 2: Direct Python Execution (Zero External Dependencies)

The application uses only the Python 3.10+ standard library (`http.server`, `sqlite3`, `urllib`, `json`, `datetime`). No `pip install` or virtual environment is required.

```bash
git clone https://github.com/anirudhra/credit-card-tracker credit-card-tracker
cd credit-card-tracker
python3 app.py --host 0.0.0.0 --port 8795
```

Open `http://localhost:8795` in your browser.

## Data Persistence and Backups

- **SQLite Database**: All cards, custom perks, logged usage, Bank of America 3% category selection, and vendor sync state are stored automatically in `data/credit_card_tracker.db`.
- **JSON Backup and Restore**: In the web UI under `Perks & Bubbles Tracker`, click `Backup JSON` to download a full JSON snapshot of your database, or click `Restore` to import a backup file onto another machine.
- **Custom Database Path**: Set the `CSR_TRACKER_DB` environment variable to override the default SQLite file path.

## Project Structure

- `app.py`: HTTP server, SQLite schema and migrations, card presets, ROI/period calculator, iCalendar generator, and REST API endpoints.
- `vendor_engine.py`: Live issuer website scraper and merchant lookup engine.
- `static/index.html`: Single-page application layout and modals.
- `static/app.js`: Frontend state management and UI rendering.
- `static/styles.css`: Responsive layout and dark theme stylesheet.
- `Dockerfile` & `docker-compose.yml`: Container build and deployment configuration.
- `data/credit_card_tracker.db`: Local SQLite database (auto-created on startup; excluded from Git via `.gitignore`).
