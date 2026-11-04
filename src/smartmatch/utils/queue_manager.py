import asyncio
import logging
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import IntEnum
from typing import Any, Callable, Dict, Optional

from telegram.error import Forbidden, RetryAfter, TelegramError, TimedOut

from supabase import AsyncClient, create_async_client

from src.smartmatch.config import settings
from src.smartmatch.services.billing_service import BillingService, sync_currency_rates

logger = logging.getLogger("smartmatch.queue_manager")

# Scheduled Worker Constants
DEFAULT_SYNC_INTERVAL_SECONDS = 21600  # Sync every 6 hours
MAX_SCRAPE_RETRIES = 3
RETRY_BACKOFF_SECONDS = 15


class TaskPriority(IntEnum):
    CRITICAL = 0  # Billing, Payment Confirmation
    HIGH = 1  # Rides, Freight Matching
    MEDIUM = 2  # Equipment Rental, Status Updates
    LOW = 3  # Analytics, House Listings, Notifications


@dataclass(order=True)
class TaskItem:
    priority: int
    task_id: str = field(compare=False)
    func: Callable = field(compare=False)
    args: tuple = field(default_factory=tuple, compare=False)
    kwargs: Dict[str, Any] = field(default_factory=dict, compare=False)
    timestamp: float = field(default_factory=time.time, compare=False)


class QueueManager:
    """
    Production-grade Async Queue Manager.
    Features:
    - Graceful shutdown with task draining.
    - Priority-based execution for dynamic tasks.
    - Managed continuous background tasks (e.g., currency scraping loop).
    - Automatic exception catching for background workers.
    """

    def __init__(self, worker_count: int = 5):
        self.queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
        self.worker_count = worker_count
        self.workers: list[asyncio.Task] = []
        self._scheduled_tasks: list[asyncio.Task] = []
        self._running = False
        self._startup_lock = asyncio.Lock()

    async def start(self):
        """Properly initializes and boots the background worker pool."""
        async with self._startup_lock:
            if self._running:
                logger.warning("QueueManager is already running.")
                return

            self._running = True
            logger.info(f"🚀 Booting {self.worker_count} background queue workers...")

            self.workers = [
                asyncio.create_task(self._worker_loop(i))
                for i in range(self.worker_count)
            ]
            logger.info("✅ All background queue workers are online and listening.")

    async def _worker_loop(self, worker_id: int):
        """Internal loop processing tasks from the priority queue."""
        logger.debug(f"👷 Worker-{worker_id} standing by.")

        while self._running:
            try:
                item = await asyncio.wait_for(self.queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            try:
                logger.info(f"⚙️ Worker-{worker_id} executing task: {item.task_id} (Priority: {item.priority})")

                if asyncio.iscoroutinefunction(item.func):
                    await item.func(*item.args, **item.kwargs)
                else:
                    item.func(*item.args, **item.kwargs)

                logger.info(f"✅ Task {item.task_id} completed successfully.")

            except Exception as e:
                logger.error(f"❌ Worker-{worker_id} critical failure on task {item.task_id}: {str(e)}", exc_info=True)
            finally:
                self.queue.task_done()

        logger.info(f"🛑 Worker-{worker_id} offline.")

    async def enqueue(self, func: Callable, priority: Any = TaskPriority.MEDIUM, *args, **kwargs) -> str:
        """Adds a new task to the queue with dynamic priority handling."""
        priority_value = priority.value if hasattr(priority, 'value') else priority

        task_id = str(uuid.uuid4())
        item = TaskItem(
            priority=priority_value,
            task_id=task_id,
            func=func,
            args=args,
            kwargs=kwargs
        )

        await self.queue.put(item)
        logger.debug(f"📥 Task {task_id} enqueued successfully with priority {priority_value}.")
        return task_id

    async def put(self, func: Callable, priority: TaskPriority = TaskPriority.MEDIUM, *args, **kwargs) -> str:
        """Alias for enqueue()."""
        return await self.enqueue(func, priority, *args, **kwargs)

    async def start_currency_worker(self, db_client: AsyncClient, interval_seconds: int = DEFAULT_SYNC_INTERVAL_SECONDS):
        """Attaches and launches the continuous currency worker loop to QueueManager."""
        task = asyncio.create_task(start_currency_worker_loop(db_client=db_client, interval_seconds=interval_seconds))
        self._scheduled_tasks.append(task)
        logger.info(f"🌐 Currency scraper loop registered with QueueManager (Interval: {interval_seconds}s).")
        return task

    async def start_daily_menu_repost_worker(self, telegram_app: Any, db_client: AsyncClient, target_hour_eat: int = 3):
        """Registers the 3:00 AM EAT daily menu repost loop task with QueueManager."""
        task = asyncio.create_task(
            start_daily_menu_repost_loop(
                telegram_app=telegram_app,
                db_client=db_client,
                target_hour_eat=target_hour_eat
            )
        )
        self._scheduled_tasks.append(task)
        logger.info(f"📅 Registered daily menu repost worker with QueueManager (Target: {target_hour_eat}:00 AM EAT).")
        return task

    async def stop_workers(self):
        """Gracefully drains queue, stops priority workers, and cancels scheduled loops."""
        logger.info("⚠️ Signaling workers to shut down. Waiting for queue to drain...")

        self._running = False

        if not self.queue.empty():
            await self.queue.join()

        # Cancel standard worker pool
        for worker in self.workers:
            if not worker.done():
                worker.cancel()

        # Cancel managed background worker loops
        for task in self._scheduled_tasks:
            if not task.done():
                task.cancel()

        all_tasks = self.workers + self._scheduled_tasks
        if all_tasks:
            await asyncio.gather(*all_tasks, return_exceptions=True)

        logger.info("🔌 All queue workers and background tasks stopped cleanly.")

    def get_status(self) -> Dict[str, Any]:
        """Returns health stats for API monitoring endpoints."""
        return {
            "queue_size": self.queue.qsize(),
            "worker_count": len(self.workers),
            "scheduled_tasks_count": len(self._scheduled_tasks),
            "is_running": self._running
        }


# --- Standalone / Managed Currency Scraping Workers ---

async def run_currency_scrape_job(db_client: Optional[AsyncClient] = None) -> bool:
    """
    Executes a single cycle of scraping the NBE exchange rate
    and persisting updated rates to Supabase with resilient error handling.
    """
    logger.info("Starting scheduled NBE currency exchange rate scrape job...")

    should_close_client = False
    if db_client is None:
        if not settings.SUPABASE_URL or not settings.SUPABASE_SERVICE_ROLE_KEY:
            logger.critical("Supabase credentials missing from settings. Aborting currency worker.")
            return False
        db_client = await create_async_client(
            settings.SUPABASE_URL,
            settings.SUPABASE_SERVICE_ROLE_KEY
        )
        should_close_client = True

    billing_service = BillingService(db_client=db_client)
    live_rate: Optional[float] = None

    for attempt in range(1, MAX_SCRAPE_RETRIES + 1):
        try:
            logger.info(f"Attempt {attempt}/{MAX_SCRAPE_RETRIES}: Fetching USD/ETB rate from NBE...")
            if hasattr(billing_service, "get_nbe_usd_to_etb"):
                live_rate = await billing_service.get_nbe_usd_to_etb()
            elif hasattr(billing_service, "currency_converter"):
                converter = billing_service.currency_converter
                if hasattr(converter, "get_active_usd_rate"):
                    live_rate = await converter.get_active_usd_rate()
                elif hasattr(converter, "get_latest_exchange_rate"):
                    live_rate = await converter.get_latest_exchange_rate()

            if live_rate and live_rate > 0:
                logger.info(f"Successfully scraped live NBE rate: {live_rate} ETB/USD")
                break
        except Exception as e:
            logger.warning(f"Attempt {attempt} failed with error: {e}")

        if attempt < MAX_SCRAPE_RETRIES:
            wait_time = RETRY_BACKOFF_SECONDS * attempt
            logger.info(f"Retrying in {wait_time} seconds...")
            await asyncio.sleep(wait_time)

    if not live_rate:
        logger.warning("All primary NBE scraping attempts failed. Executing fallback rate sync pipeline.")

    try:
        # sync_currency_rates safely upserts USD (live_rate or 160.82) AND ETB (1.0)
        sync_result = await sync_currency_rates(supabase=db_client, live_rate=live_rate)

        status_ok = sync_result.get("status") == "success" if isinstance(sync_result, dict) else False
        logger.info(
            f"Currency scrape job completed. Result status: {sync_result.get('status') if isinstance(sync_result, dict) else 'completed'}"
        )
        return status_ok

    except Exception as sync_err:
        logger.critical(f"Failed during rate persistence step: {sync_err}", exc_info=True)
        return False

    finally:
        if should_close_client and db_client:
            try:
                if hasattr(db_client, "aclose"):
                    await db_client.aclose()
                elif hasattr(db_client, "close"):
                    close_res = db_client.close()
                    if asyncio.iscoroutine(close_res):
                        await close_res
            except Exception as close_err:
                logger.warning(f"Error closing temporary Supabase client: {close_err}")


async def start_currency_worker_loop(
        db_client: AsyncClient,
        interval_seconds: int = DEFAULT_SYNC_INTERVAL_SECONDS
):
    """
    Continuous async background loop for execution within ASGI application lifespan.
    """
    logger.info(f"Initializing continuous currency worker loop (Interval: {interval_seconds}s)")
    while True:
        try:
            await run_currency_scrape_job(db_client=db_client)
        except asyncio.CancelledError:
            logger.info("Currency worker loop received cancellation signal. Stopping cleanly.")
            break
        except Exception as unhandled_err:
            logger.error(f"Unhandled error in currency worker loop: {unhandled_err}", exc_info=True)

        await asyncio.sleep(interval_seconds)


# ==========================================
# DAILY MAIN MENU REPOST WORKER
# ==========================================

def get_moving_track_banner(step: int = 0) -> str:
    """
    Generates a straight moving track banner with category emojis traveling on a bold light green line.

    Categories: House (🏠), Vehicle (🚗), Equipment (🚜), Freight (🚚), Ride (🚕)
    """
    emojis = ["🏠", "🚗", "🚜", "🚚", "🚕"]
    track_length = 15
    track = ["━"] * track_length

    # Advance emoji positions along the track based on current step
    for idx, emoji in enumerate(emojis):
        pos = (step + idx * 3) % track_length
        track[pos] = emoji

    # Wrap track in bold light green border indicators
    return f"🟩━{''.join(track)}━🟩"


async def run_daily_menu_repost_job(telegram_app: Any, db_client: AsyncClient) -> None:
    """
    Queries active users from Supabase in batches of 1,000 to prevent bottlenecks
    and sends a refreshed persistent main menu at 3:00 AM EAT.
    """
    logger.info("🚀 Starting daily persistent main menu repost job (3:00 AM EAT)...")

    if not telegram_app or not hasattr(telegram_app, "bot"):
        logger.error("❌ Cannot execute daily menu repost: telegram_app is uninitialized.")
        return

    # Dynamic import to prevent circular dependency issues
    from src.smartmatch.handlers.main_menu import MainMenuHandler

    bot = telegram_app.bot
    success_count = 0
    blocked_count = 0

    # Frame step counter for moving track motion
    animation_step = 0

    # Database pagination settings
    CHUNK_SIZE = 1000
    offset = 0

    while True:
        # 1. Retrieve users in chunks of 1,000 to prevent RAM & DB bottlenecking
        try:
            res = (
                await db_client.table("users")
                .select("telegram_id, language")
                .range(offset, offset + CHUNK_SIZE - 1)
                .execute()
            )
            users_chunk = res.data if res and res.data else []
        except Exception as err:
            logger.error(f"❌ Database error fetching user chunk offset {offset}: {err}")
            break

        if not users_chunk:
            break

        logger.info(f"📦 Processing user dispatch chunk: {offset} to {offset + len(users_chunk) - 1}")

        for user in users_chunk:
            chat_id = user.get("telegram_id")
            lang = user.get("language") if user.get("language") in ["en", "am", "om"] else "en"

            if not chat_id:
                continue

            # Render moving emoji motion on bold light green line header
            banner = get_moving_track_banner(animation_step)
            animation_step = (animation_step + 1) % 15

            # Multilingual message templates with top UI motion banner
            repost_texts = {
                "en": (
                    f"<code>{banner}</code>\n"
                    f"⚡ <b>SMART MATCH AFRICA - DAILY MARKETPLACE UPDATE</b> ⚡\n\n"
                    f"Your permanent menu has been refreshed below.\n\n"
                    f"👇 <b>Select an option from the keyboard to browse listings or start a request:</b>"
                ),
                "am": (
                    f"<code>{banner}</code>\n"
                    f"⚡ <b>ስማርት ማች አፍሪካ - የዕለት ገበያ ማሻሻያ</b> ⚡\n\n"
                    f"ቋሚ ማኑዩ ከዚህ በታች ታድሷል።\n\n"
                    f"👇 <b>ማስታወቂያዎችን ለመመልከት ወይም ጥያቄ ለመጀመር ከቁልፍ ሰሌዳው ላይ አማራጭ ይምረጡ፡</b>"
                ),
                "om": (
                    f"<code>{banner}</code>\n"
                    f"⚡ <b>SMART MATCH AFRICA - HAAROMSA GABOOWAA GUUTUU</b> ⚡\n\n"
                    f"Menuun keessan kan dhaabbataa ta'e armaan gadiitti haaromfameera.\n\n"
                    f"👇 <b>Beeksisoota ilaaluuf ykn gaaffii jalqabuuf keyboard irraa filannoo filadhaa:</b>"
                )
            }

            repost_text = repost_texts[lang]

            try:
                keyboard = MainMenuHandler.get_persistent_keyboard(lang)
                await bot.send_message(
                    chat_id=chat_id,
                    text=repost_text,
                    reply_markup=keyboard,
                    parse_mode="HTML",
                    read_timeout=30.0,
                    write_timeout=30.0
                )
                success_count += 1
                # Rate limiting: Sleep 50ms between requests (~20 msgs/sec)
                await asyncio.sleep(0.05)

            except TimedOut:
                # Retry once with extended timeout
                await asyncio.sleep(1.0)
                try:
                    keyboard = MainMenuHandler.get_persistent_keyboard(lang)
                    await bot.send_message(
                        chat_id=chat_id,
                        text=repost_text,
                        reply_markup=keyboard,
                        parse_mode="HTML",
                        read_timeout=45.0,
                        write_timeout=45.0
                    )
                    success_count += 1
                    await asyncio.sleep(0.05)
                except Exception as retry_err:
                    logger.warning(f"⚠️ Failed to deliver persistent menu to User {chat_id} after retry: {retry_err}")
                    blocked_count += 1

            except RetryAfter as e:
                logger.warning(f"⚠️ Telegram Flood Control triggered. Sleeping for {e.retry_after} seconds.")
                await asyncio.sleep(e.retry_after)
            except Forbidden:
                blocked_count += 1
            except TelegramError as tg_err:
                logger.warning(f"⚠️ Failed to deliver persistent menu to User {chat_id}: {tg_err}")
                blocked_count += 1
            except Exception as e:
                logger.error(f"❌ Unexpected error delivering persistent menu to User {chat_id}: {e}")
                blocked_count += 1

        # Advance chunk offset for next 1,000 users
        offset += CHUNK_SIZE

        # Stop if current chunk was smaller than requested batch size
        if len(users_chunk) < CHUNK_SIZE:
            break

    logger.info(
        f"✅ Daily main menu repost completed. Delivered: {success_count} | Blocked/Failed: {blocked_count}"
    )


async def start_daily_menu_repost_loop(telegram_app: Any, db_client: AsyncClient, target_hour_eat: int = 3):
    """
    Continuous async loop that calculates delay until the next 3:00 AM EAT (UTC+3)
    and triggers the main menu repost job once every 24 hours.
    """
    eat_tz = timezone(timedelta(hours=3))  # EAT is UTC+3
    logger.info(f"⏰ Initializing Daily Menu Repost Loop target set for {target_hour_eat}:00 AM EAT.")

    while True:
        try:
            now_eat = datetime.now(eat_tz)
            target_time = now_eat.replace(hour=target_hour_eat, minute=0, second=0, microsecond=0)

            # If current time is past 3:00 AM today, schedule for 3:00 AM tomorrow
            if now_eat >= target_time:
                target_time += timedelta(days=1)

            seconds_until_next_run = max(1.0, (target_time - now_eat).total_seconds())
            logger.info(
                f"⏳ Next daily menu repost scheduled in {seconds_until_next_run / 3600:.2f} hours (at {target_time.strftime('%Y-%m-%d %H:%M:%S %Z')})"
            )

            await asyncio.sleep(seconds_until_next_run)

            # Execute job at 3:00 AM EAT
            await run_daily_menu_repost_job(telegram_app=telegram_app, db_client=db_client)

        except asyncio.CancelledError:
            logger.info("🛑 Daily menu repost loop received cancellation signal. Exiting.")
            break
        except Exception as unhandled_err:
            logger.error(f"❌ Error in daily menu repost loop: {unhandled_err}", exc_info=True)
            # Sleep 1 minute before retrying calculation in case of transient failure
            await asyncio.sleep(60)


if __name__ == "__main__":
    """
    Standalone CLI execution entry point for external cron jobs.
    Usage: python -m src.smartmatch.services.queue_manager
    """
    logger.info("Executing currency worker as a standalone process...")
    success = asyncio.run(run_currency_scrape_job())
    sys.exit(0 if success else 1)
