# KTMB (KITS) Ticket Sniper Bot 🚄

> A high-performance, resilient, and privacy-first automated train ticket booking & monitoring daemon for Malaysia's KTMB (KITS) ETS railway system.

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![Playwright](https://img.shields.io/badge/Playwright-Chromium-green.svg)](https://playwright.dev/)
[![Telegram Bot](https://img.shields.io/badge/Telegram-Bot%20Remote%20Control-blue.svg)](https://core.telegram.org/bots)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED.svg)](https://www.docker.com/)
[![Tests](https://img.shields.io/badge/Tests-87%20Passed%20(83%25%20cov)-brightgreen.svg)]()
[![Security](https://img.shields.io/badge/Security-AES%20Encrypted%20%26%20PII%20Masked-success.svg)]()

---

## ✨ Features

- **⚡ Adaptive Polling with Gaussian Jitter**: Intelligently polls KTMB KITS endpoints with dynamic backoff, randomized jitter, and circuit breaker protection against rate-limiting or anti-bot blocks.
- **🔄 Full Round-Trip (往返双程) Lifecycle Automation**: Outbound and return journey auto-orchestration. When the outbound seat is reserved, it seamlessly transitions into tracking and booking the return leg.
- **🚉 Intelligent Station Resolver (`prompt_station`)**:
  - Direct alias and code recognition (`BM` -> `BUKIT MERTAJAM`, `KLS` -> `KL SENTRAL`, `PG` -> `BUTTERWORTH`).
  - Interactive top 10 hub menu (`?` or `list`).
  - Fuzzy-search collision picker.
- **👥 Multi-Passenger Support**:
  - Interactive CLI wizard for adding multiple travelers.
  - Automatic seat count synchronization (`required_seats = len(passengers)`).
  - Bulk reservation payload construction for KITS checkout.
- **🔒 Security-First & Zero PII Leakage**:
  - **Malaysian MyKad & Phone Masking**: All IDs (`900101-14-****`) and contact numbers (`0123****789`) are masked in console output, logs, Telegram messages, and SQLite audit records.
  - **AES-GCM (Fernet) Encryption at Rest**: Passenger credentials stored in SQLite are cryptographically encrypted; only decrypted in-memory during final reservation.
  - **Zero Secrets Committed**: Strict `.gitignore` protecting `.env`, `.ktm_key`, and database files.
- **📱 24/7 Mobile Remote Control via Telegram Bot**:
  - Control the daemon running on AWS EC2 directly from your phone.
  - Zero inbound web ports required (uses outbound long-polling).
  - Whitelist-only access restriction (`chat_id` verification).
- **🐳 Cloud-Ready Production Deployment**:
  - Official Playwright Debian image with all Linux Chromium dependencies pre-configured.
  - 1-click Docker Compose and Linux Systemd service setup.

---

## 📱 Telegram Bot Remote Commands

Manage your cloud daemon from Telegram anytime:

| Command | Example | Description |
| :--- | :--- | :--- |
| `/status` | `/status` | View current monitoring status, route, dates, seats, and cycles |
| `/add_passenger <Name> <IC> [Phone] [Gender]` | `/add_passenger "Tan Ah Kow" 900101-14-5566 0123456789 Male` | Add a new passenger. Seats target auto-syncs. PII encrypted and masked |
| `/passengers` | `/passengers` | Display all registered passengers with masked details |
| `/clear_passengers` | `/clear_passengers` | Clear passenger list and reset seats to 1 |
| `/set origin <Station>` | `/set origin BM` | Change origin station |
| `/set dest <Station>` | `/set dest Ipoh` | Change destination station |
| `/set date <YYYY-MM-DD>` | `/set date 2027-02-05` | Update departure date |
| `/set time <HH:MM-HH:MM>` | `/set time 08:00-14:00` | Update departure time window |
| `/set return_date <Date>` | `/set return_date 2027-02-08` | Set return date & activate round trip |
| `/set seats <Count>` | `/set seats 2` | Manually adjust target seat count |
| `/pause` | `/pause` | Pause polling loop (keeps daemon running) |
| `/resume` | `/resume` | Resume active monitoring |
| `/help` | `/help` | View help menu |

---

## 🚀 Quick Start (Local)

### 1. Clone & Setup
```bash
git clone https://github.com/elvinhui/KTMTicBot.git
cd KTMTicBot

python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
```

### 2. Environment Variables
Copy the template and fill in your Telegram Bot Token and Chat ID:
```bash
cp .env.example .env
```
Edit `.env`:
```ini
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_CHAT_ID=your_chat_id_here
KTM_DB_PATH=data/ktm_sniper.db
KTM_HEADLESS=true
```

### 3. Run Interactive Wizard
```bash
python main.py
```

---

## ☁️ AWS EC2 / Lightsail Deployment

### Security Group (Best Practice)
- **Inbound**: Port `22` (SSH) only. **NO web ports (80/443) needed!**
- **Outbound**: All Traffic allowed.

### 1-Click Docker Compose
```bash
# 1. Install Docker on Ubuntu
sudo apt update && sudo apt install -y docker.io docker-compose-v2
sudo usermod -aG docker $USER && newgrp docker

# 2. Clone repository & configure .env
git clone https://github.com/elvinhui/KTMTicBot.git ~/ktm-sniper
cd ~/ktm-sniper
cp .env.example .env
nano .env  # Enter your bot token and chat ID

# 3. Start daemon in background
docker compose up -d --build

# 4. View real-time logs
docker compose logs -f
```

---

## 🧪 Testing

Run full test suite with coverage report:
```bash
python -m pytest --cov=ktm_sniper
```
All **87 automated tests** pass with **83%+ coverage**.

---

## 📄 License

MIT License. For educational and personal travel booking convenience only.
