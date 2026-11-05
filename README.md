# Smart Match Africa API & Telegram Gateway

Smart Match Africa is an asynchronous multi-vertical marketplace platform and logistics gateway. It features localized Telegram bot interfaces, a high-performance FastAPI backend, and an integrated Telethon background worker handling live GPS tracking and asset synchronization.

---

## 🚀 Deployment on Render

This repository is configured for unified deployment on **Render**, running both the FastAPI web server and the Telethon background worker concurrently within a single RAM-optimized container.

### Service Configuration
* **Runtime:** Python 3.11+
* **Port:** `10000` (Assigned and dynamically bound via Render's `$PORT`)
* **Build Command:** `pip install -r requirements.txt`
* **Start Command:** `uvicorn src.smartmatch.main:app --host 0.0.0.0 --port $PORT`

---

## ⚙️ Environment Variables

Ensure the following production environment variables are properly set in your Render service dashboard:

### Core & Telegram
* `TELEGRAM_TOKEN`: Your Telegram Bot API token.
* `TELEGRAM_WEBHOOK_SECRET`: Secret token for securing Telegram webhook updates.
* `SERVER_PORT`: `10000`

### Database & Caching
* `SUPABASE_URL`: Your Supabase project REST endpoint.
* `SUPABASE_SERVICE_KEY`: Supabase service role key for administrative data tasks.
* `REDIS_URL`: Connection string for Redis / Aiven Valkey cache.

### Telethon Location Worker
* `API_ID`: Telegram API ID from my.telegram.org.
* `API_HASH`: Telegram API Hash.
* `TELETHON_SESSION_STRING`: Serialized string session for the userbot tracking daemon.
* `TRACKER_PHONE_NUMBER`: Phone number associated with the tracking userbot session.

---

## 🔌 Core Architecture & Features
* **Unified Lifecycle Management:** FastAPI lifespan initializes Supabase, Redis, background queue workers, and the Telethon tracking daemon asynchronously.
* **Lightweight Matching Engine:** Optimized fallback token and string intersection search matching algorithms designed to stay comfortably within Render's free-tier 512 MB RAM threshold.
* **Financial Webhooks:** Secure Chapa payment gateway integration with dual-layer atomic Redis and in-memory locking to prevent duplicate processing.