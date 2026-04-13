# QuantFX AI — Complete Setup Guide

A full-stack AI forex trading bot with FastAPI backend, MongoDB, MT5 integration,
Telegram control, and automated GitHub → VPS deployment.

---

## Project structure

```
quantfx/
├── backend/
│   ├── main.py              ← FastAPI server (all endpoints)
│   ├── auto_trade.py        ← Trading bot loop
│   ├── strategy.py          ← RSI, MACD, Bollinger, Trend + consensus
│   ├── ml_model.py          ← Random Forest signal confirmation
│   ├── telegram_alert.py    ← Send Telegram messages
│   ├── telegram_control.py  ← Read Telegram commands
│   ├── database.py          ← MongoDB connection
│   ├── logger.py            ← File + console logging
│   ├── requirements.txt
│   ├── Dockerfile
│   └── .env                 ← Your secrets (never commit this)
├── docker-compose.yml
├── .github/
│   └── workflows/
│       └── deploy.yml       ← Auto-deploy on git push
└── README.md
```

---

## Step 1 — Create your Telegram bot

1. Open Telegram and search **@BotFather**
2. Send `/newbot` and follow the prompts
3. Copy the **bot token** (looks like `123456:ABCDEFabcdef...`)
4. Open your new bot and send it any message
5. Visit in your browser:
   ```
   https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates
   ```
6. Find `"chat":{"id": 987654321}` — that number is your **chat ID**

---

## Step 2 — Create MongoDB Atlas (free)

1. Go to https://cloud.mongodb.com and create a free account
2. Create a free **M0** cluster
3. Under **Database Access** — create a user with read/write access
4. Under **Network Access** — allow `0.0.0.0/0` (all IPs)
5. Click **Connect → Drivers** and copy the connection string:
   ```
   mongodb+srv://username:password@cluster0.xxxxx.mongodb.net/quantfx
   ```

---

## Step 3 — Fill in your .env file

```bash
cp backend/.env.example backend/.env
```

Open `backend/.env` and fill in every value:

```env
MONGO_URL=mongodb+srv://user:pass@cluster0.xxxxx.mongodb.net/quantfx
SECRET_KEY=any-long-random-string-here
MT5_LOGIN=12345678
MT5_PASSWORD=your-mt5-password
MT5_SERVER=YourBroker-Demo
TELEGRAM_BOT_TOKEN=123456:ABCDEFabcdef...
TELEGRAM_CHAT_ID=987654321
RISK_PERCENT=1.0
STOP_LOSS_PIPS=20
TAKE_PROFIT_PIPS=40
MAX_TRADES_PER_DAY=5
MIN_CONFIDENCE=60
```

---

## Step 4 — Test Telegram connection locally

```bash
cd backend
pip install -r requirements.txt
python telegram_alert.py
```

You should receive a test message in your Telegram chat.

---

## Step 5 — Run locally with Docker

```bash
# From the project root
docker compose up --build
```

- Backend API: http://localhost:8000
- API docs:    http://localhost:8000/docs
- Bot loop runs automatically as a separate container

---

## Step 6 — Push to GitHub

```bash
git init
git remote add origin https://github.com/YOUR_USERNAME/quantfx-ai.git
git add .
git commit -m "initial commit"
git push -u origin main
```

> ⚠️ Make sure `.env` is in `.gitignore` — never commit your secrets.

---

## Step 7 — Set up your VPS (one time)

Use any Linux VPS (Ubuntu 22.04 recommended).
Providers: Contabo, Vultr, DigitalOcean, AWS Lightsail.

SSH into your server and run:

```bash
# Install Docker
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER

# Clone your repo
git clone https://github.com/YOUR_USERNAME/quantfx-ai.git ~/quantfx
cd ~/quantfx

# Copy and fill your .env
cp backend/.env.example backend/.env
nano backend/.env   # paste your real values

# Start everything
docker compose up -d --build
```

Check it's running:
```bash
docker compose ps
curl http://localhost:8000/health
```

---

## Step 8 — Auto-deploy with GitHub Actions

Every time you `git push` to `main`, GitHub will automatically SSH into your
VPS and redeploy. Add these secrets in your GitHub repo:

**Settings → Secrets → Actions → New repository secret**

| Secret name   | Value                              |
|---------------|------------------------------------|
| `VPS_HOST`    | Your VPS IP address                |
| `VPS_USER`    | Your VPS username (usually `root`) |
| `VPS_SSH_KEY` | Your private SSH key               |

To get your SSH key:
```bash
# On your local machine
cat ~/.ssh/id_rsa
# Copy the entire output including -----BEGIN----- and -----END-----
```

If you don't have one:
```bash
ssh-keygen -t rsa -b 4096
ssh-copy-id root@YOUR_VPS_IP
```

---

## Step 9 — Monitor from Telegram

Once the bot is running, control it entirely from Telegram:

| Command      | What it does                        |
|--------------|-------------------------------------|
| `/startbot`  | Resume automatic trading            |
| `/stopbot`   | Stop trading                        |
| `/pause1h`   | Pause for 1 hour                    |
| `/status`    | Bot status + latest signal          |
| `/balance`   | Live account balance                |
| `/trades`    | All open positions                  |
| `/signal`    | Latest AI signal                    |
| `/pairs`     | Scan all 6 currency pairs           |
| `/closeall`  | Emergency close everything          |
| `/report`    | Full performance report             |
| `/help`      | Show all commands                   |

---

## Safety checklist before going live

- [ ] Run on a **demo account** for at least 2 weeks
- [ ] Win rate consistently above 55%
- [ ] Max drawdown below 10% of balance
- [ ] `RISK_PERCENT` set to 1% or lower
- [ ] `MIN_CONFIDENCE` set to 60% or higher
- [ ] `MAX_TRADES_PER_DAY` set to 5 or lower
- [ ] Telegram alerts confirmed working
- [ ] MongoDB trade history recording correctly
- [ ] Only then: switch `MT5_SERVER` to your live account

---

## API endpoints reference

| Method | Endpoint                   | Auth | Description                  |
|--------|----------------------------|------|------------------------------|
| POST   | `/register`                | No   | Create account               |
| POST   | `/login`                   | No   | Get JWT token                |
| GET    | `/health`                  | No   | Server + MT5 status          |
| GET    | `/price/{symbol}`          | No   | Live bid/ask                 |
| GET    | `/signals`                 | No   | All pair consensus signals   |
| GET    | `/signals/{symbol}`        | No   | Single pair full breakdown   |
| GET    | `/backtest/{symbol}`       | No   | Walk-forward backtest        |
| POST   | `/risk/calculate`          | No   | Lot size + R:R calculator    |
| POST   | `/trade`                   | Yes  | Place a trade                |
| GET    | `/trades`                  | Yes  | Your trade history           |
| GET    | `/analytics`               | Yes  | Performance stats            |
| GET    | `/account`                 | Yes  | MT5 account info             |
| GET    | `/positions`               | Yes  | Open positions               |
| POST   | `/positions/close-all`     | Yes  | Emergency close all          |

Full interactive docs available at: `http://YOUR_VPS_IP:8000/docs`
