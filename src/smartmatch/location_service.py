import asyncio
import gc
import logging
from typing import Optional
from urllib.parse import urlparse

from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.errors import PersistentTimestampOutdatedError, FloodWaitError

# Import your unified database manager structure
from src.smartmatch.services.db_manager import DatabaseManager
from src.smartmatch.config import settings

# --- LOGGING CONFIGURATION ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger("smartmatch.location_worker")

# Suppress noisy internal Telethon update and network logs
logging.getLogger('telethon').setLevel(logging.WARNING)
logging.getLogger('telethon.client.updates').setLevel(logging.WARNING)
logging.getLogger('telethon.client.users').setLevel(logging.WARNING)

# --- CONFIGURATION & CREDENTIALS ---
API_ID = settings.API_ID
API_HASH = settings.API_HASH
SESSION_STRING = getattr(settings, 'TELETHON_SESSION_STRING', '')

# Initialize Telethon Client with resource-optimized parameters and connection pooling
client = TelegramClient(
    StringSession(SESSION_STRING),
    API_ID,
    API_HASH,
    connection_retries=10,
    retry_delay=2,
    request_retries=5
)

# Type hinting the global manager explicitly
db_manager: Optional[DatabaseManager] = None


async def init_worker_infrastructure() -> DatabaseManager:
    """
    Validates environment configurations with non-blocking retry logic,
    returning an initialized, connected DatabaseManager wrapper instance using Supabase SDK.
    """
    logger.info("🚀 Validating configuration and initializing worker architecture...")

    raw_supabase_url = settings.SUPABASE_URL or ""
    sanitized_url = raw_supabase_url.strip()

    if not sanitized_url:
        logger.critical("❌ FATAL: SUPABASE_URL is completely missing from your configuration settings.")
        raise SystemExit("Supabase URL Missing")

    try:
        parsed_url = urlparse(sanitized_url)
        target_host = parsed_url.hostname
        logger.info(f"Targeting Supabase API Gateway host: '{target_host}'")
    except Exception as parse_err:
        logger.error(f"Failed to parse Supabase URL for local diagnostics: {parse_err}")

    if not (settings.SUPABASE_SERVICE_KEY or settings.SUPABASE_ANON_KEY):
        logger.critical("❌ FATAL: Missing credentials. Ensure SUPABASE_SERVICE_KEY or SUPABASE_ANON_KEY are specified.")
        raise SystemExit("Supabase Keys Missing")

    max_retries = 5
    delay = 3.0

    for attempt in range(1, max_retries + 1):
        try:
            manager = DatabaseManager()
            await manager.connect()

            if not getattr(manager, 'supabase', None):
                raise ValueError("DatabaseManager failed to securely bind the Supabase API AsyncClient engine.")

            logger.info("✅ Worker Supabase API client initialized flawlessly.")
            return manager

        except Exception as e:
            logger.warning(f"⚠️ Worker infrastructure connection attempt {attempt}/{max_retries} failed: {e}")
            if attempt == max_retries:
                logger.critical(f"❌ FATAL: Worker service initialization collapsed after {max_retries} attempts.", exc_info=True)
                raise SystemExit("Infrastructure Initialization Collapse")
            await asyncio.sleep(delay)
            delay *= 1.5


@client.on(events.MessageEdited())
async def handle_location_update(event: events.MessageEdited.Event) -> None:
    """
    Listens for live location modifications inside the userbot's active chats
    and streams incoming packets down asynchronously to the asset tracking storage layer.
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
            return

        # Fully asynchronous non-blocking Supabase update calls
        if hasattr(db_manager, 'supabase') and db_manager.supabase:
            supabase_client = db_manager.supabase

            # Update Active Provider Listings (Drivers/Carriers) asynchronously
            await supabase_client.table('provider_listings').update({
                'latitude': lat,
                'longitude': lon
            }).eq('provider_id', telegram_id)\
              .in_('vertical_type', ['ride', 'freight'])\
              .eq('status', 'active')\
              .execute()

            # Update Active Seeker Requests (Passengers/Cargo Owners) asynchronously
            await supabase_client.table('seeker_requests').update({
                'latitude': lat,
                'longitude': lon
            }).eq('seeker_id', telegram_id)\
              .in_('vertical_type', ['ride', 'freight'])\
              .eq('status', 'active')\
              .execute()
        else:
            await db_manager.update_asset_location(
                telegram_id=telegram_id,
                current_lat=lat,
                current_lon=lon
            )

    except Exception as e:
        logger.error(f"Failed to route live GPS updates into engine storage for entity {telegram_id}: {str(e)}")


async def run_location_worker() -> None:
    """
    Master entry point and execution supervisor for the location tracking daemon
    with robust session desync recovery, graceful shutdown, and aggressive RAM management.
    """
    global db_manager
    logger.info("🚀 Booting Location Tracking Worker Daemon...")

    db_manager = await init_worker_infrastructure()
    target_phone = getattr(settings, 'TRACKER_PHONE_NUMBER', None)

    cleanup_task = None
    try:
        while True:
            try:
                if not client.is_connected():
                    logger.info("🔌 Connecting Telethon client session...")
                    await client.start(phone=lambda: target_phone)  # type: ignore
                    logger.info("✅ Tracking interface fully active. Listening for continuous live GPS streams...")

                # Background RAM monitoring & Telethon entity/dialog cache cleanup loop (runs every 3 mins)
                async def memory_cleanup_loop():
                    while client.is_connected():
                        await asyncio.sleep(180)
                        try:
                            if hasattr(client, 'entity_cache') and client.entity_cache:
                                client.entity_cache.clear()

                            # Safely access protected member using getattr to silence PyCharm inspector warnings
                            dialogs_cache = getattr(client, '_dialogs', None)
                            if dialogs_cache is not None and hasattr(dialogs_cache, 'clear'):
                                dialogs_cache.clear()

                            gc.collect()
                        except Exception as gc_err:
                            logger.debug(f"Memory cleanup routine notice: {gc_err}")

                cleanup_task = asyncio.create_task(memory_cleanup_loop())

                try:
                    await client.run_until_disconnected()
                except PersistentTimestampOutdatedError:
                    logger.warning(
                        "⚠️ Telethon PersistentTimestampOutdatedError detected. Reconnecting session cleanly...")
                    if client.is_connected():
                        await client.disconnect()
                    await asyncio.sleep(3.0)
                finally:
                    if cleanup_task and not cleanup_task.done():
                        cleanup_task.cancel()

            except FloodWaitError as fw:
                logger.warning(f"⚠️ Telethon flood wait triggered. Sleeping for {fw.seconds} seconds...")
                await asyncio.sleep(fw.seconds + 5)
            except Exception as run_err:
                logger.error(
                    f"⚠️ Unhandled runtime exception inside Telethon event loop: {str(run_err)}. Restarting worker in 10s...")
                await asyncio.sleep(10)

    except asyncio.CancelledError:
        logger.info("🛑 Location tracking worker task cancelled.")
    finally:
        logger.info("Initiating elegant and graceful shutdown protocols for background location daemon...")

        if cleanup_task and not cleanup_task.done():
            cleanup_task.cancel()

        try:
            if client.is_connected():
                await client.disconnect()
                logger.info("Telethon network interface cleanly unlinked.")
        except Exception as disc_err:
            logger.error(f"Error disconnecting Telethon client: {disc_err}")

        if db_manager and hasattr(db_manager, 'redis') and db_manager.redis:
            try:
                await db_manager.redis.close()
                logger.info("Redis shared cache client connection closed.")
            except Exception as close_err:
                logger.error(f"Error encountered during Redis release step: {close_err}")

        gc.collect()
        logger.info("Worker safe shutdown sequence executed completely.")
