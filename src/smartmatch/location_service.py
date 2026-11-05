import asyncio
import logging
from typing import Optional
from urllib.parse import urlparse

from telethon import TelegramClient, events
from telethon.sessions import StringSession

# Import your unified database manager structure
from src.smartmatch.services.db_manager import DatabaseManager
from src.smartmatch.config import settings

# --- LOGGING CONFIGURATION ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger("smartmatch.location_worker")

# --- CONFIGURATION & CREDENTIALS ---
API_ID = settings.API_ID
API_HASH = settings.API_HASH
SESSION_STRING = getattr(settings, 'TELETHON_SESSION_STRING', '')

# Initialize Telethon Client
client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)

# Type hinting the global manager explicitly so PyCharm understands it
db_manager: Optional[DatabaseManager] = None


async def init_worker_infrastructure() -> DatabaseManager:
    """
    Validates environment configurations, triggers diagnostics for local vs. cloud
    deployments, and returns an initialized, connected DatabaseManager wrapper instance
    using the Supabase SDK.
    """
    logger.info("🚀 Validating configuration and initializing worker architecture...")

    # 1. Supabase Environment Validation & Diagnostics
    raw_supabase_url = settings.SUPABASE_URL or ""
    sanitized_url = raw_supabase_url.strip()  # Wipe out hidden trailing spaces/newlines

    if not sanitized_url:
        logger.critical("❌ FATAL: SUPABASE_URL is completely missing from your configuration settings.")
        raise SystemExit("Supabase URL Missing")

    try:
        parsed_url = urlparse(sanitized_url)
        target_host = parsed_url.hostname

        logger.info(f"Targeting Supabase API Gateway host: '{target_host}'")

        # Network configuration safety guardrail
        if target_host and (target_host.endswith(".internal") or target_host == "localhost"):
            logger.warning(
                f"⚠️ DEPLOYMENT WARNING: The host '{target_host}' looks like an internal private cluster link or local instance. "
                f"If you are executing this application worker locally from your machine, ensure your environment files "
                f"reference the public, external Supabase API endpoint."
            )
    except Exception as parse_err:
        logger.error(f"Failed to parse Supabase URL for local diagnostics: {parse_err}")

    if not (settings.SUPABASE_SERVICE_KEY or settings.SUPABASE_ANON_KEY):
        logger.critical("❌ FATAL: Missing credentials. Ensure SUPABASE_SERVICE_KEY or SUPABASE_ANON_KEY are specified.")
        raise SystemExit("Supabase Keys Missing")

    # 2. Redis Environment Validation
    raw_redis_url = settings.REDIS_URL or ""
    sanitized_redis_url = raw_redis_url.strip()

    if not sanitized_redis_url:
        logger.critical("❌ FATAL: REDIS_URL is missing from environment variables.")
        raise SystemExit("Redis URL Missing")

    # 3. Instantiate and Connect the Central Database Manager
    try:
        manager = DatabaseManager()

        # Connect to Supabase and Redis through your manager's internal method
        await manager.connect()

        # Structural assertions to ensure API compliance before beginning worker cycles
        if not getattr(manager, 'supabase', None):
            raise ValueError("DatabaseManager failed to securely bind the Supabase API AsyncClient engine.")
        if not getattr(manager, 'redis', None):
            raise ValueError("DatabaseManager failed to securely bind the underlying Redis cache cluster link.")

        logger.info("✅ Worker Supabase API client initialized flawlessly.")
        logger.info("✅ Worker Redis cache connection ping test successful.")
        return manager

    except Exception as e:
        logger.critical(f"❌ FATAL: Worker service initialization collapsed: {str(e)}", exc_info=True)

        if "getaddrinfo failed" in str(e) or "Name or service not known" in str(e):
            logger.critical(
                "💡 TROUBLESHOOTING HINT: This indicates a local DNS routing failure. Verify your computer's internet "
                "connectivity, verify your string entries contain no trailing syntax or formatting anomalies, "
                "and ensure you are not running cloud-only host clusters inside an isolated local environment."
            )
        raise SystemExit("Infrastructure Initialization Collapse")


@client.on(events.MessageEdited())
async def handle_location_update(event: events.MessageEdited.Event) -> None:
    """
    Listens for live location modifications inside the userbot's active chats
    and streams incoming packets down to the asset tracking storage layer.
    """
    global db_manager
    message = event.message

    if not (message.media and hasattr(message.media, 'geo') and message.media.geo):
        return

    lat = message.media.geo.lat
    lon = message.media.geo.long
    telegram_id = message.sender_id

    try:
        if db_manager is None:
            logger.error("Global database manager instance is offline. Dropping location update stream packet.")
            return

        # Directly update tables to maintain consistency with `provider_listings` and `seeker_requests`.
        # We explicitly filter for `ride` and `freight` vertical_types because live mobility updates
        # apply strictly to moving assets, ignoring static assets like housing or equipment.
        if hasattr(db_manager, 'supabase') and db_manager.supabase:
            def sync_updates():
                # Update Active Provider Listings (Drivers/Carriers)
                db_manager.supabase.table('provider_listings').update({
                    'latitude': lat,
                    'longitude': lon
                }).eq('provider_id', telegram_id)\
                  .in_('vertical_type', ['ride', 'freight'])\
                  .eq('status', 'active')\
                  .execute()

                # Update Active Seeker Requests (Passengers/Cargo Owners)
                db_manager.supabase.table('seeker_requests').update({
                    'latitude': lat,
                    'longitude': lon
                }).eq('seeker_id', telegram_id)\
                  .in_('vertical_type', ['ride', 'freight'])\
                  .eq('status', 'active')\
                  .execute()

            # Offloading to a separate thread ensures the synchronous Supabase SDK
            # does not block the fast async Telethon event loop.
            await asyncio.to_thread(sync_updates)
            logger.debug(f"📍 Mobility GPS ({lat}, {lon}) synchronized for Entity ID: {telegram_id}")

        else:
            # Fallback to abstract method if direct table access isn't available
            await db_manager.update_asset_location(
                telegram_id=telegram_id,
                current_lat=lat,
                current_lon=lon
            )
            logger.debug(f"📍 GPS coordinates ({lat}, {lon}) synchronized via generic method for Entity ID: {telegram_id}")

    except Exception as e:
        logger.error(f"Failed to route live GPS updates into engine storage for entity {telegram_id}: {str(e)}",
                     exc_info=True)


# Change from: async def main() -> None:
async def run_location_worker() -> None:
    """
    Master entry point and execution supervisor for the location tracking daemon.
    """
    global db_manager
    logger.info("🚀 Booting Location Tracking Worker Daemon...")

    # Initialize infrastructure manager and assign it cleanly to global scope
    db_manager = await init_worker_infrastructure()

    logger.info("Connecting Telethon Userbot to Telegram Edge Nodes...")
    target_phone = getattr(settings, 'TRACKER_PHONE_NUMBER', None)

    try:
        await client.start(phone=lambda: target_phone)  # type: ignore
        logger.info("✅ Tracking interface fully active. Listening for continuous live GPS streams...")

        await client.run_until_disconnected()

    except Exception as run_err:
        logger.critical(f"Unhandled runtime exception inside Telethon master event loop: {str(run_err)}", exc_info=True)

    finally:
        logger.info("Initiating elegant and graceful shutdown protocols for background daemon...")

        if client.is_connected():
            await client.disconnect()
            logger.info("Telethon network interface cleanly unlinked.")

        if db_manager and hasattr(db_manager, 'redis') and db_manager.redis:
            try:
                await db_manager.redis.close()
                logger.info("Redis shared cache client connection closed.")
            except Exception as close_err:
                logger.error(f"Error encountered during Redis release step: {close_err}")

        # Note: No need to explicitly close a Supabase client connection pool
        # like we had to do with raw asyncpg. The SDK manages HTTP sessions internally.

        logger.info("Worker safe shutdown sequence executed completely.")


if __name__ == '__main__':
    try:
        asyncio.run(run_location_worker())
    except KeyboardInterrupt:
        logger.info("\n🛑 Tracking background process gracefully interrupted by operational command.")
    except SystemExit as sys_exit:
        logger.error(f"System exit routine processed: {str(sys_exit)}")
