import os
import sys
import re
import logging
import asyncio
import inspect
import json
import signal
import warnings
import socket
import ssl
import uvicorn
from typing import Dict, Any, Optional, Tuple, Protocol, Set
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

# Core Web & ASGI Infrastructure Frameworks
from fastapi import FastAPI, APIRouter, Request, HTTPException, Header, Query, BackgroundTasks, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import httpx

# Telegram SDK & Errors
from telegram.error import (
    BadRequest,
    Conflict,
    NetworkError,
    RetryAfter,
    TimedOut,
    Forbidden
)
from telegram.constants import ParseMode
from telegram.request import HTTPXRequest
from telegram import CallbackQuery, Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    CallbackQueryHandler,
    CallbackContext,
    ExtBot,
    PicklePersistence,
    DictPersistence,
    Defaults
)
from telegram.warnings import PTBUserWarning

# Async Infrastructure Drivers
from supabase import AsyncClient, acreate_client, AsyncClientOptions
import redis.asyncio as aioredis
from redis.exceptions import ConnectionError as RedisConnectionError

# --- CORE PLATFORM INTEGRATION SETTINGS ---
from src.smartmatch.config import settings
from src.smartmatch.location_service import run_location_worker

# --- SYSTEMIC ENGINE SERVICES ---
from src.smartmatch.services.db_manager import DatabaseManager
import src.smartmatch.services.db_manager as db_module
from src.smartmatch.utils.image_service import ImageService
from src.smartmatch.services.billing_service import BillingService
from src.smartmatch.services.webhook_handler import WebhookHandler
from src.smartmatch.services.ai_engine import SmartMatchAIEngine
from src.smartmatch.services.reserve__quantity_location import ReservationHelper
from src.smartmatch.logic.matching import MatchingEngine
from src.smartmatch.utils.queue_manager import QueueManager
from src.smartmatch.utils.redis_persistence import RedisPersistence

# --- VERTICAL BUSINESS DOMAIN HANDLERS ---
from src.smartmatch.handlers.main_menu import MainMenuHandler
from src.smartmatch.handlers.housing import HousingHandler
from src.smartmatch.handlers.vehicle import VehicleHandler
from src.smartmatch.handlers.freight_ride import FreightRideHandler
from src.smartmatch.handlers.equipment_rental import EquipmentRentalHandler
from src.smartmatch.handlers.driver_verification import DriverVerificationHandler
from src.smartmatch.handlers.deals_handler import DealsHandler


# ==========================================
# MODULE-LEVEL NETWORK & INFRASTRUCTURE EXCEPTION REGISTRY
# ==========================================
# Pre-built at module load time using top-level imports & dynamic protocol adapters
_NETWORK_EXCEPTIONS_LIST = [
    NetworkError,
    TimedOut,
    httpx.HTTPError,
    httpx.TimeoutException,
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.PoolTimeout,
    httpx.WriteTimeout,
    socket.error,
    socket.gaierror,
    ssl.SSLError,
    OSError,
    asyncio.TimeoutError,
    RedisConnectionError,
]

# Dynamically bind optional underlying protocol adapters (Fixed for PyCharm analyzers)
try:
    import httpcore
    HTTPCORE_EXCEPTIONS: tuple[type[BaseException], ...] = (
        httpcore.ConnectError,
        httpcore.TimeoutException,
    )
    _NETWORK_EXCEPTIONS_LIST.extend([
        httpcore.ConnectTimeout,
        httpcore.ReadTimeout,
        httpcore.PoolTimeout,
        httpcore.ConnectError,
        httpcore.NetworkError,
    ])
except ImportError:
    httpcore = None  # Explicitly defined for static analyzers
    HTTPCORE_EXCEPTIONS = ()

try:
    import aiohttp
    _NETWORK_EXCEPTIONS_LIST.extend([
        aiohttp.ClientError,
        aiohttp.ServerTimeoutError,
    ])
except ImportError:
    aiohttp = None  # Explicitly defined for static analyzers

NETWORK_INFRASTRUCTURE_EXCEPTIONS = tuple(_NETWORK_EXCEPTIONS_LIST)


# Suppress PTBUserWarning about per_message=False to clean up production logs
warnings.filterwarnings('ignore', category=PTBUserWarning)


# ==========================================
# 1. CUSTOM TELEGRAM CONTEXT FOR MULTI-LANG
# ==========================================
class CustomContext(CallbackContext[ExtBot, dict, dict, dict]):
    @property
    def user_data(self) -> dict:
        data = super().user_data

        if data is not None:
            user_id = self._user_id

            if user_id is not None:
                backup_key = f"user_{user_id}_lang_backup"
                current_lang = data.get("lang") or data.get("language")

                if current_lang:
                    if self.bot_data.get(backup_key) != current_lang:
                        self.bot_data[backup_key] = current_lang
                    data["lang"] = current_lang
                    data["language"] = current_lang
                elif backup_key in self.bot_data:
                    restored_lang = self.bot_data[backup_key]
                    data["lang"] = restored_lang
                    data["language"] = restored_lang
                else:
                    data["is_new_user"] = True
                    update_obj = getattr(self, "_update", None)
                    if update_obj and hasattr(update_obj, "effective_user") and update_obj.effective_user:
                        tg_lang = update_obj.effective_user.language_code
                        new_lang = tg_lang if tg_lang else "en"
                    else:
                        new_lang = "en"
                    data["lang"] = new_lang
                    data["language"] = new_lang
                    self.bot_data[backup_key] = new_lang
            else:
                if "lang" not in data and "language" not in data:
                    data["lang"] = "en"
                    data["language"] = "en"
        return data


context_types = ContextTypes(context=CustomContext)


async def terms_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends inline text for Terms, Policies, and Commission Framework aligned with the frontend WebApp."""
    lang = str(
        context.user_data.get('language') or context.user_data.get('lang') or 'en'
    ).lower()

    if 'am' in lang:
        terms_text = (
            "📜 *ስማርት ማች አፍሪካ - የፕላትፎርም ደንቦች፣ መመሪያዎች እና የኮሚሽን ማዕቀፍ*\n\n"
            "🔹 *1. ለአቅራቢዎች 100% ነጻ የዝርዝር (Listing) አገልግሎት:*\n"
            "የንብረት አቅራቢዎች (የቤት ሽያጭ/ክራይ፣ የመኪና ሽያጭ/ክራይ፣ የከባድ ማሽኖች ክራይ፣ ራይድ (ታክሲ) እና የጭነት አጓጓዦች) ንብረቶቻቸውን *በሙሉ በነጻ*"
            " መዘርዘር ወይም ማከራየት ይችላሉ። ገዢዎች ወይም ተከራዮች የፕላትፎርሙን አገልግሎት ኮሚሽን ይከፍላሉ።\n\n"
            "🔹 *2. ስማርት ድርድር እና የኮሚሽን ክፍያ ብቻ:*\n"
            "ይህ መድረክ ለንብረት ወይም ለሎጂስቲክስ አገልግሎቶች የሚደረጉ ክፍያዎችንም ሆነ ግብይቶችን አያስተናግድም። መድረኩ በንብረት አቅራቢዎች እና ፈላጊዎች"
            " መካከል ትስስር ብቻ የሚፈጥር ሲሆን፥ ወገኖች ስምምነት ላይ ከደረሱ በኋላ ዝርዝር መረጃዎችን እና የእውቂያ አድራሻዎችን (የስልክ ቁጥርን ጨምሮ)"
            " ለመግለጥ የመድረክ አገልግሎት ኮሚሽን ብቻ ይሰበስባል።\n"
            "• *መመሪያው:* `ዝርዝር መረጃዎችን እና የመገናኛ ስልክ ቁጥር ለማግኘት፥ እባክዎ ከታች የክፍያ አማራጭ በመምረጥ የኮሚሽን ክፍያ ይፈጽሙ።`\n"
            "የ Smart ስምምነቶች ሞጁል ተደራዳሪ ወገኖች የኮሚሽን ክፍያ ከመፈጸማቸው በፊት በዋጋ ላይ እንዲደራደሩና የጋራ ስምምነት ላይ እንዲደርሱ ያስችላል።\n\n"
            "🔹 *3. መረጃ መለዋወጥ እና በአካል መፈጸም:*\n"
            "ኮሚሽኑ ከተከፈለ በኋላ ለሁለቱም ወገኖች የስልክ ቁጥር እና ዝርዝር መረጃ በተሳካ ሁኔታ ይላካል፦\n"
            "• *ለፈላጊው/ገዢው:* `አገልግሎቱን ለማስጀመር ከአቅራቢው ጋር በቀጥታ ይገናኙ። ስማርት ማች አፍሪካን ስለተጠቀሙ እናመሰግናለን!`\n"
            "• *ለአቅራቢው/ሻጩ:* `ጥሪውን በቅርቡ ይጠብቁ፣ ወይም ለመጀመር በቀጥታ ያግኟቸው። Smart Match Africa ስለተጠቀሙ እናመሰግናለን!`\n"
            "ቀሪው የክፍያ እና የማድረስ ስራ በአካል ይከናወናል።\n\n"
            "🌐 *ሙሉውን መመሪያ እዚህ ያንብቡ:* https://smart-match-africa-gateway-ijg3.onrender.com"
        )
    elif 'om' in lang or 'or' in lang:
        terms_text = (
            "📜 *Smart Match Africa - Imaammata Fayyadamaa fi Sirna Komishinii*\n\n"
            "🔹 *1. Abbootii Qabeenyaaf Galmeessuun 100% Bilisa:*\n"
            "Abbootiin qabeenyaa (Gurgurtaa/Kiree mana, Konkolaataa, Meeshalee ijaarsaa, Tajaajila geejjibaa fi "
            "Fe'umsaa) qabeenya isaanii *bilisaan* galmeessuu danda'u. Bittonni ykn kireeffattonni kaffaltii tajaajila "
            "bilaatfoormichaa (komishinii) ni kaffalu.\n\n"
            "🔹 *2. Daldala Smartii fi Kaffaltii Komishinii Qofa:*\n"
            "Jiddu-galli kun kaffaltii ykn daldala meeshaalee fi tajaajila loojistiksii kamiyyuu hin keessummeessu. "
            "Sirnichi qondaaltota (dhiyeessitoota) fi barbaaddota qabeenyaa walitti hidhuu qofa kan raawwatu yoo ta'u, "
            "erga waliigaltee irra gahanii booda odeeffannoo bal’aa fi teessoo quunnamtii "
            "(Lakkoofsa bilbilaa dabalatee)ifa gochuuf kaffaltii tajaajila madalichaa (komishinii) qofa sassaaba.\n"
            "• *Qajeelfama:* `Odeeffannoo bal'aa fi bilbila quunnamtii argachuuf, maaloo kaffaltii komishinii "
            "raawwachuuf filannoo kaffaltii gadii filadhaa.`\n"
            "Moduulii waliigaltee Smart kun kaffaltii komishinii dura qaamonni dhimmi ilaallatu gatii irratti"
            " mari’atanii waliigaltee waloo bira akka gahan dandeessisa.\n\n"
            "🔹 *3. Qunnamtii Erguu fi Waliigaltee Kallattii:*\n"
            "Kaffaltii komishinii booda odeeffannoon qunnamtii ni ergama:\n"
            "• *Gaafataa/Bittaaf:* `Dhiyeessaa wajjin kallattiin qunnamaa. Smart Match Africa fayyadamuu keessaniif "
            "galatoomaa!`\n"
            "• *Dhiyeessaaf:* `Bilbila isaanii dhiyootti eegaa, ykn jalqabuuf kallattiidhaan isaan quunnamaa. "
            "Smart Match Africa fayyadamuu keessaniif galatoomaa!`\n"
            "Kaffaltii fi dhiyeessiin dhumaa qaamaan raawwatama.\n\n"
            "🌐 *Imaammata guutuu dubbisuuf:* https://smart-match-africa-gateway-ijg3.onrender.com"
        )
    else:
        terms_text = (
            "📜 *Smart Match Africa - Platform User Policy & Commission Framework*\n\n"
            "🔹 *1. 100% Free Listing Model for Providers:*\n"
            "Asset providers (Housing sales/rentals, Vehicle sales/rentals, Equipment rentals, Ride-hailing drivers, "
            "and Freight carriers) can list, sell, or rent out their assets at *zero cost*. Buyers or renters pay the "
            "platform service commission..\n\n"
            "🔹 *2. Smart Negotiations & Commission-Only Payments:*\n"
            "This platform does not process transactions or payments for assets or logistics services."
            " The system solely connects asset providers and seekers, collecting a platform service commission to"
            " disclose detailed information and contact credentials (including phone number) once a mutual agreement"
            " is reached.\n"
            "• *Guidance Prompt:* `\"To view details and contact information, please select a payment method below to "
            "pay the commission.\"`\n"
            "The smart deals module enables parties to negotiate prices and reach a mutual agreement prior to "
            "commission payment.\n\n"
            "🔹 *3. Contact Dispatch & In-Person Finalization:*\n"
            "Upon successful commission payment, contact information is securely dispatched to both negotiated parties:\n"
            "• *For Seeker:* `\"Directly contact the provider to arrange delivery. Thank you for using Smart Match Africa!\"`\n"
            "• *For Provider:* `\"Expect their call shortly, or contact them directly to get started. Thank you for "
            "using Smart Match Africa!\"`\n"
            "All final asset or service payments are completed securely in person between the parties.\n\n"
            "🌐 *Read the full policy here:* https://smart-match-africa-gateway-ijg3.onrender.com"
        )

    if update.message:
        await update.message.reply_text(
            text=terms_text,
            parse_mode="Markdown",
            disable_web_page_preview=True
        )


# ==========================================
# 2. ENVIRONMENT & LOGGING SETUP (WITH WEBHOOK REDACTION)
# ==========================================
class ComprehensiveCredentialRedactor(logging.Filter):
    """Scans log messages and replaces sensitive credentials, tokens, and connection strings."""

    BOT_TOKEN_PATTERN = re.compile(r'bot\d+:[A-Za-z0-9_-]+', re.IGNORECASE)

    # Matches secret tokens in strings, key-value dict representations, or URLs
    SECRET_TOKEN_PATTERN = re.compile(r'(secret_token[\'"]?\s*[:=]\s*[\'"])([\w-]+)([\'"])', re.IGNORECASE)

    # Matches database & cache URIs containing credentials (postgres://user:pass@host, rediss://user:pass@host)
    DB_URI_PATTERN = re.compile(r'(postgres(?:ql)?|rediss?|amqp|valkey)://[^:\s]+:[^@\s]+@\S+', re.IGNORECASE)

    # Matches Supabase API tokens and JWTs (anon/service_role keys)
    JWT_PATTERN = re.compile(r'eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+', re.IGNORECASE)

    # Matches general configuration key-value leaks for Supabase, Valkey, and API keys
    KV_SECRET_PATTERN = re.compile(
        r'(password|passwd|pwd|secret|api_key|supabase_key|valkey_password|service_role_key|anon_key|chapa_secret_key|telegram_token|telegram_webhook_secret)[\'"]?\s*[:=]\s*[\'"]([^\'"]+)[\'"]',
        re.IGNORECASE
    )

    def _redact(self, text: str) -> str:
        if not isinstance(text, str):
            return text
        text = self.BOT_TOKEN_PATTERN.sub('bot[REDACTED_TOKEN]', text)
        text = self.SECRET_TOKEN_PATTERN.sub(r'\1[REDACTED_SECRET]\3', text)
        text = self.DB_URI_PATTERN.sub(r'\1://[REDACTED_CREDENTIALS]@host', text)
        text = self.JWT_PATTERN.sub('[REDACTED_JWT_TOKEN]', text)
        text = self.KV_SECRET_PATTERN.sub(r'\1=[REDACTED_SECRET]', text)
        return text

    def _redact_structure(self, obj: Any) -> Any:
        """Recursively traverses dictionaries, lists, and tuples to redact sensitive keys or values."""
        if isinstance(obj, str):
            return self._redact(obj)
        elif isinstance(obj, dict):
            new_dict = {}
            for k, v in obj.items():
                if isinstance(k, str) and k.lower() in ['secret_token', 'token', 'password', 'secret', 'api_key',
                                                        'service_role_key', 'chapa_secret_key']:
                    new_dict[k] = '[REDACTED_SECRET]'
                else:
                    new_dict[k] = self._redact_structure(v)
            return new_dict
        elif isinstance(obj, list):
            return [self._redact_structure(item) for item in obj]
        elif isinstance(obj, tuple):
            return tuple(self._redact_structure(item) for item in obj)
        return obj

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = self._redact(record.msg)

        if record.args:
            record.args = self._redact_structure(record.args)

        return True


class HealthFilter(logging.Filter):
    """Filter out frequent /health endpoint hits to reduce log noise."""

    def filter(self, record: logging.LogRecord) -> bool:
        return "/health" not in record.getMessage()


def setup_logging():
    redactor = ComprehensiveCredentialRedactor()

    # Determine log level based on DEBUG_MODE setting
    is_debug = getattr(settings, "DEBUG_MODE", False)
    root_level = logging.DEBUG if is_debug else logging.INFO

    logging.basicConfig(
        level=root_level,
        format="%(asctime)s [%(levelname)s] %(name)s (Line: %(lineno)d): %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)]
    )

    root_logger = logging.getLogger()
    root_logger.addFilter(redactor)

    # Attach redactor explicitly to all root handlers to intercept third-party debug leaks
    for handler in root_logger.handlers:
        handler.addFilter(redactor)

    # Suppress noisy HTTP client libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").addFilter(HealthFilter())

    # Third-party telemetry loggers should follow DEBUG_MODE in production
    third_party_level = logging.DEBUG if is_debug else logging.INFO
    for logger_name in ["telegram.ext", "telegram.bot", "supabase", "redis", "valkey"]:
        third_party_logger = logging.getLogger(logger_name)
        third_party_logger.setLevel(third_party_level)
        third_party_logger.addFilter(redactor)


setup_logging()
logger = logging.getLogger("smartmatch.gateway")

# ==========================================
# Critical Configuration & Fail-Fast Validation
# ==========================================
SUPABASE_URL = getattr(settings, "SUPABASE_URL", None)
SERVICE_ROLE_KEY = getattr(settings, "SERVICE_ROLE_KEY", None)
REDIS_URL = getattr(settings, "REDIS_URL", None)

if not SUPABASE_URL or not SERVICE_ROLE_KEY:
    logger.critical(
        "FATAL: Missing critical Supabase configuration (SUPABASE_URL or SERVICE_ROLE_KEY). Check Render environment variables.")
    sys.exit(1)

PORT = int(os.environ.get("PORT", 8080))

AIVEN_API_TOKEN = os.environ.get("AIVEN_API_TOKEN")
AIVEN_PROJECT_NAME = os.environ.get("AIVEN_PROJECT_NAME")
AIVEN_SERVICE_NAME = os.environ.get("AIVEN_SERVICE_NAME", "valkey-19c9b0d0-sma-logistics-assets")


# ==========================================
# 3. GLOBAL APPLICATION STATE REGISTRY
# ==========================================
db_manager: Optional[DatabaseManager] = None
supabase_client: Optional[AsyncClient] = None
redis_client: Optional[aioredis.Redis] = None

billing_service: Optional[BillingService] = None
image_service: Optional[ImageService] = None
ai_engine: Optional[SmartMatchAIEngine] = None
matching_engine: Optional[MatchingEngine] = None
deals_handler: Optional[DealsHandler] = None
queue_manager: Optional[QueueManager] = None
webhook_handler: Optional[WebhookHandler] = None
main_menu: Optional[MainMenuHandler] = None
telegram_app: Optional[Any] = None

shutdown_event = asyncio.Event()


# ==========================================
# 4. CONTEXT EVALUATOR & GLOBAL TEXT ROUTER
# ==========================================
def is_user_in_active_flow(user_data: dict) -> bool:
    """
    Evaluates whether the user is currently in an active state machine phase
    (registration, driver verification, vertical onboarding, offer entry, or active deal negotiation).
    """
    if not user_data:
        return False

    # 1. Explicit conversation / vertical workflow / custom offer flags
    if (
        user_data.get("in_conversation")
        or user_data.get("flow")
        or user_data.get("active_vertical_flow")
        or user_data.get("awaiting_counter_offer")
        or user_data.get("active_freight_deal_id")
    ):
        return True

    # 2. Registration & Verification active states
    if (
        user_data.get("is_registering")
        or user_data.get("registration_step")
        or user_data.get("driver_verification_state")
        or user_data.get("in_driver_verification")
    ):
        return True

    # 3. Standard PTB ConversationHandler state tracking
    if user_data.get("state") is not None or user_data.get("_conversation_state") is not None:
        return True

    # 4. Generic prompt & step input collection flags
    if user_data.get("awaiting_input") or user_data.get("current_step") or user_data.get("step"):
        return True

    # 5. Dynamic state key pattern detection
    for key, val in user_data.items():
        if isinstance(key, str) and (
            key.startswith("conv_")
            or key.startswith("flow_")
            or key.endswith("_step")
            or key.endswith("_state")
        ):
            if val is not None and val is not False:
                return True

    return False


async def global_text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Smart & Robust global text router: Intercepts all free-text and menu interactions,
    guaranteeing zero silent drops with comprehensive fallback tracing.
    """
    if not update.message or not update.message.text:
        return

    user_data = context.user_data or {}
    user_text = update.message.text.strip()
    user_id = update.effective_user.id if update.effective_user else "Unknown"

    logger.info(f"📥 Global text router ingested message from User {user_id}: '{user_text}'")

    # 1. COMMAND & PERSISTENT MENU BUTTON INTERCEPT (Highest Priority)
    # Check if text matches any persistent menu button across EN, AM, OM or is a start command
    text_lower = user_text.lower()
    if text_lower in ("/start", "/menu", "start", "menu") or user_text.startswith("/"):
        context.user_data.clear()
        if main_menu:
            logger.info(f"🚀 Triggering start/menu workflow via global router for User {user_id}")
            await main_menu.start_command(update, context)
            return

    # Check multilingual menu options dynamically
    menu_button_texts = set()
    for lang_code in ("en", "am", "om"):
        try:
            keyboard = MainMenuHandler.get_persistent_keyboard(lang_code)
            for row in keyboard.keyboard:
                for btn in row:
                    menu_button_texts.add(btn.text.strip().lower())
        except Exception:
            pass

    if text_lower in menu_button_texts:
        logger.info(f"🎛️ Persistent menu button match detected ('{user_text}'). Routing to MainMenuHandler.")
        if main_menu:
            await main_menu.route_menu_click(update, context)
            return

    # 2. CONVERSATION & ACTIVE FLOW BYPASS
    if is_user_in_active_flow(user_data):
        logger.debug(f"⚡ Bypassing global router: User {user_id} is in an active conversational flow.")
        return

    # 3. ACTIVE NEGOTIATION INTERCEPT
    if user_data.get("active_negotiation_id") and user_data.get("awaiting_counter_offer"):
        if deals_handler:
            logger.info(f"🤝 Routing message to deals handler counter-offer flow for User {user_id}")
            await deals_handler.process_counter_message(update, context)
        return

    # 4. INTELLIGENT STRAY NUMERIC & REPLY-TO FALLBACK
    clean_num = user_text.upper().replace(",", "").replace("ETB", "").replace("BIRR", "").strip()
    if clean_num.isdigit() or (clean_num.replace('.', '', 1).isdigit() and clean_num.count('.') < 2):
        target_deal_id = None

        if update.message.reply_to_message:
            replied_msg_id = update.message.reply_to_message.message_id
            msg_deal_map = user_data.get("msg_deal_map", {})
            if replied_msg_id in msg_deal_map:
                target_deal_id = msg_deal_map[replied_msg_id]
            elif deals_handler and hasattr(deals_handler, 'get_deal_by_message_id'):
                target_deal_id = await deals_handler.get_deal_by_message_id(replied_msg_id)

        if not target_deal_id:
            target_deal_id = user_data.get("active_negotiation_id")

        if not target_deal_id and db_manager:
            active_deal = await db_manager.get_user_latest_active_negotiation(user_id)
            if active_deal:
                target_deal_id = active_deal.get("id")

        if target_deal_id:
            context.user_data["active_negotiation_id"] = target_deal_id
            context.user_data["awaiting_counter_offer"] = True
            if deals_handler:
                await deals_handler.process_counter_message(update, context)
            return
        else:
            msg = (
                "⚠️ **Action Required**\n\n"
                "It looks like you are trying to submit a price, but no active negotiation was found.\n"
                "Please select an active request from the menu first."
            )
            await update.message.reply_text(msg, parse_mode="Markdown")
            return

    # 5. SAFE FALLBACK ROUTING TO MAIN MENU
    if main_menu:
        logger.info(f"🔀 Fallback routing text input for User {user_id} to MainMenuHandler: '{user_text}'")
        try:
            await main_menu.route_menu_click(update, context)
        except Exception as route_err:
            logger.error(f"❌ Error in route_menu_click for text '{user_text}': {route_err}", exc_info=True)
            await update.message.reply_text("⚠️ An error occurred processing your request. Please tap /start to reset.")
    else:
        logger.warning(f"⚠️ MainMenuHandler instance is None. Dropped text: '{user_text}'")
        await update.message.reply_text("⚠️ System menu is reloading. Please try /start.")


# ==========================================
# 4.1. UNMATCHED & ORPHANED CALLBACK HANDLERS
# ==========================================

async def handle_unmatched_callback(query: CallbackQuery, silent: bool = True) -> None:
    """
    Clears UI loading spinners without displaying intrusive modal alerts
    to the user during active negotiation transitions.
    """
    try:
        await query.answer(
            text="" if silent else "This option is no longer available.",
            show_alert=not silent
        )
    except Exception as err:
        logger.debug(f"Callback already answered or expired: {err}")

    logger.warning(
        f"⚠️ UNMATCHED CALLBACK QUERY DETECTED | User ID: {query.from_user.id} | Data: '{query.data}'"
    )


async def global_unhandled_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Safety net handler that safely ignores active menu, language selection, equipment, vehicle,
    housing, negotiation, freight, ride, and conversation prefixes, and gracefully catches orphaned
    or expired inline button clicks.
    """
    if not update.callback_query:
        return

    query = update.callback_query
    data = query.data or ""

    # 1. Ignore active conversation, menu, language, equipment, housing, vehicle, freight, ride, and negotiation prefixes
    active_prefixes = (
        # Language Selection
        "set_lang:", "lang_",

        # General & Menu Navigation
        "btn_", "cat_", "eq_cat_", "eq_", "equip_deal_", "list_equipment", "find_equipment",

        # Transaction & Type Prefixes (CRITICAL: Included find_trans_ and list_trans_)
        "find_trans_", "list_trans_", "trans_", "vtype_", "fuel_", "list_vehicle", "find_vehicle",
        "action_", "use_", "housing_",

        # Role Selection & Flow Entry Prefixes
        "role_", "freight_", "ride_", "cargo_", "shipment_", "fr_", "rd_", "hs_", "vh_",

        # Match & Selection Prefixes
        "select_house_match:", "select_equip_match:", "select_equipment_match:", "select_seeker_match:",
        "eq_select_seeker_match:", "fr_select_seeker_match:", "rd_select_seeker_match:",
        "hs_select_seeker_match:", "vh_select_seeker_match:", "select_vehicle_match:",
        "select_freight_match:", "select_match:", "fr_select_", "rd_select_", "hs_select_", "vh_select_",
        "back_to_top_matches",

        # Vehicle Flow & Entry Points
        "vehicle_offer:", "vehicle_prompt_offer:", "vehicle_back_to_top", "lock_vehicle_deal:",
        "cancel_vehicle_offer", "cancel_vehicle_deal", "cancel_vertical_offer",

        # Equipment & Housing Flow
        "equip_prompt_offer:", "equip_back_to_top", "seeker_back_to_top",
        "lock_housing_deal:", "lock_equip_deal:", "lock_", "prompt_offer:",

        # Cancellation & General Flow Control (Broadened cancel_ coverage)
        "cancel_", "cancel_to_main", "cancel_match", "cancel_counter_offer", "cancel_offer",

        # Payment Integration
        "pay_local_", "pay_intl_",

        # Negotiation & Deal Actions
        "deal_accept", "deal_counter", "deal_decline", "seeker_accept", "provider_accept",
        "accept_offer_", "reject_offer_", "lock_freight_deal"
    )

    if data.startswith(active_prefixes):
        try:
            await query.answer()  # Quietly clear Telegram button spinner without wiping markup
        except Exception:
            pass
        return

    # 2. Target explicitly deprecated legacy buttons
    if data.startswith(('action_old_', 'legacy_')):
        try:
            await query.answer("⚠️ This button is outdated. Please use /start to refresh.", show_alert=True)
            if query.message and hasattr(query.message, "edit_reply_markup"):
                await query.message.edit_reply_markup(reply_markup=None)
        except Exception as e:
            logger.error(f"Failed to clean up stale callback: {e}")
        return

    # 3. Graceful fallback for genuinely expired/orphaned queries
    try:
        await query.answer(
            text="⏳ This action has expired. Returning to main menu...",
            show_alert=True
        )

        if query.message and hasattr(query.message, "edit_reply_markup"):
            try:
                await query.message.edit_reply_markup(reply_markup=None)
            except Exception:
                pass

        menu_instance = (
                context.bot_data.get("main_menu")
                or (context.application.bot_data.get("main_menu")
                    if context.application and context.application.bot_data else None)
                or globals().get("main_menu")
        )

        if menu_instance is not None:
            if context.user_data is not None:
                context.user_data.clear()
            await menu_instance.show_main_menu(update, context)
        else:
            logger.warning("⚠ Main menu handler reference is None in bot_data; fallback redirect skipped.")

    except Exception as err:
        logger.debug(f"Handled expired callback redirection gracefully: {err}")


# ==========================================
# 4.2. ORPHANED TEXT INPUT HANDLER (FIXED)
# ==========================================

async def handle_orphaned_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Catches text messages when a user's conversation state was wiped out,
    ignoring persistent main menu button clicks to prevent false-positive warnings.
    """
    if not update.message or not update.message.text:
        return

    text = update.message.text.strip()
    text_lower = text.lower()

    # Dynamic main menu buttons across EN, AM, OM
    menu_button_texts = set()
    for lang_code in ("en", "am", "om"):
        try:
            keyboard = MainMenuHandler.get_persistent_keyboard(lang_code)
            for row in keyboard.keyboard:
                for btn in row:
                    menu_button_texts.add(btn.text.strip().lower())
        except Exception:
            pass

    # Static multilingual fallbacks
    fallback_triggers = {
        "/start", "/menu", "start", "menu",
        # Afaan Oromoo
        "🔎 konkolaataa barbaaduu", "🏠 manoota barbaaduu", "🚜 meeshaalee barbaaduu",
        "🚗 konkolaataa galmeessuu", "🏡 mana galmeessuu", "🏗️ meeshaa galmeessuu",
        "📦 fe'umsa", "🚖 imala",
        # Amharic
        "🔎 ተሽከርካሪ ለመፈለግ", "🏠 ቤት ለመፈለግ", "🚜 መሣሪያ ለመፈለግ",
        "🚗 ተሽከርካሪ ለመመዝገብ", "🏡 ቤት ለመመዝገብ", "🏗️ መሣሪያ ለመመዝገብ",
        "📦 ጭነት", "🚖 ጉዞ",
        # English
        "🔎 find vehicle", "🏠 find house", "🚜 find equipment",
        "🚗 list vehicle", "🏡 list house", "🏗️ list equipment",
        "📦 freight", "🚖 ride"
    }

    # If menu button or command was pressed, clear leftover data silently
    if text_lower in menu_button_texts or text_lower in fallback_triggers or text.startswith("/"):
        context.user_data.clear()
        return

    # FIXED: Only trigger restart warning if an explicit orphaned flag is present
    # Do NOT check domain keys like 'target_seeker_id' or standard session state
    is_orphaned_session = context.user_data.get("is_session_orphaned", False)

    if is_orphaned_session:
        raw_lang = str(
            context.user_data.get('language') or context.user_data.get('lang') or 'en'
        ).lower()

        if 'am' in raw_lang:
            lang = 'am'
        elif 'om' in raw_lang or 'or' in raw_lang:
            lang = 'om'
        else:
            lang = 'en'

        messages = {
            'en': "⚠️ <b>Session refreshed.</b>\nThe server was recently restarted, resetting our current step. Please use the menu below or tap /start to pick up where you left off.",
            'am': "⚠️ <b>ክፍለ-ጊዜው ታድሷል።</b>\nሰቨሩ እንደገና ስለተጀመረ የነበረበት ሂደት ተቋርጧል። እባክዎ /start በመጫን እንደገና ይጀምሩ።",
            'om': "⚠️ <b>Sajoo (Session) haaromfameera.</b>\nServerichi waan haaromfameef, deeggarsi keessan adda kuteera. Mee /start tuquun irra deebi'aa jalqabaa."
        }

        await update.message.reply_text(messages[lang], parse_mode="HTML")
        context.user_data.clear()


# ==========================================
# 4.3 FORCE IPV4 RESOLUTION (Fixes 15s Delays)
# ==========================================
def force_ipv4_dns():
    """
    Patches socket.getaddrinfo to filter for IPv4 (AF_INET) addresses first.
    Prevents ~15s OS-level IPv6 TCP handshake timeouts on network routes.
    """
    try:
        orig_getaddrinfo = socket.getaddrinfo

        def getaddrinfo_ipv4(*args, **kwargs):
            res = orig_getaddrinfo(*args, **kwargs)
            ipv4_res = [r for r in res if r[0] == socket.AF_INET]
            return ipv4_res if ipv4_res else res

        socket.getaddrinfo = getaddrinfo_ipv4
        logger.info("⚡ IPv4 DNS resolution patch applied successfully.")
    except Exception as e:
        logger.warning(f"⚠️ Could not patch socket for IPv4: {e}")


# Execute IPv4 patching on module load
force_ipv4_dns()


# ==========================================
# MODULE-LEVEL CONSTANTS (Zero allocation overhead on error trigger)
# ==========================================
BENIGN_ERROR_PATTERNS: tuple[str, ...] = (
    "query is too old",
    "message is not modified",
    "message to delete not found",
    "message can't be deleted",
    "message to edit not found",
    "chat not found",
    "there is no text in the message to edit",
)

ERROR_MESSAGES: dict[str, dict[str, str]] = {
    "en": {
        "network": (
            "⚠️ <b>Connection hiccup detected.</b>\n"
            "Your last action experienced a brief network delay. "
            "Please tap or re-send your response to continue."
        ),
        "unexpected": (
            "⚠️ <b>Processing error occurred.</b>\n"
            "Please try your last step again or tap /start to refresh the menu."
        ),
        "cb_toast": "⚠️ Network/server delay. Please try again.",
    },
    "am": {
        "network": (
            "⚠️ <b>የግንኙነት መቆራረጥ አጋጥሟል።</b>\n"
            "ያደረጉት የመጨረሻ እርምጃ አጭር የኔትዎርክ መዘግየት አጋጥሞታል። "
            "እባክዎ መልስዎን እንደገና ይላኩ።"
        ),
        "unexpected": (
            "⚠️ <b>ያልተጠበቀ ስህተት አጋጥሟል።</b>\n"
            "እባክዎ የመጨረሻውን እርምጃ እንደገና ይሞክሩ ወይም /start በመጫን እንደገና ይጀምሩ።"
        ),
        "cb_toast": "⚠️ ጊዜያዊ የኔትዎርክ ዘግየት አጋጥሟል። እባክዎ እንደገና ይሞክሩ።",
    },
    "om": {
        "network": (
            "⚠️ <b>Rakkoo walqunnamtii mudateera.</b>\n"
            "Adeemsi keessan yeroo xiqqoo turateera. Itti fufuuf deebii keessan irra deebi'aa ergaa."
        ),
        "unexpected": (
            "⚠️ <b>Dogoggorri hin eegamne mudateera.</b>\n"
            "Mee tarkaanfii keessan irra deebi'aa yaalaa ykn /start tuquun haaromsaa."
        ),
        "cb_toast": "⚠️ Dogoggorri walqunnamtii uumameera. Mee irra deebi'aa yaalaa.",
    },
}


def _resolve_user_language(context: ContextTypes.DEFAULT_TYPE) -> str:
    """Fast non-allocating language extraction helper."""
    if not context or not context.user_data:
        return "en"

    raw_lang = str(
        context.user_data.get("language")
        or context.user_data.get("lang")
        or "en"
    ).lower()

    if "am" in raw_lang:
        return "am"
    if "om" in raw_lang or "or" in raw_lang:
        return "om"
    return "en"


# ==========================================
# 5. GLOBAL TELEGRAM ERROR HANDLER (OPTIMIZED)
# ==========================================
async def global_error_handler(
        update: object, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Optimized & Bulletproof global error handler:
    1. Short-circuits benign Telegram UI states without disturbing the user.
    2. Answers button spinners instantly on genuine application errors with alerts and tracebacks.
    3. Safely resolves user language with fallback protection.
    """
    error = context.error
    if error is None:
        return

    user_id: Optional[int] = None
    chat_id: Optional[int] = None
    callback_query = None
    effective_msg = None

    if isinstance(update, Update):
        if update.effective_user:
            user_id = update.effective_user.id
        if update.effective_chat:
            chat_id = update.effective_chat.id
        callback_query = update.callback_query
        effective_msg = update.effective_message

    # --------------------------------------------------------------------------
    # STEP 1: EARLY EXIT FOR BENIGN / NON-ACTIONABLE ERRORS
    # --------------------------------------------------------------------------
    if isinstance(error, BadRequest):
        err_str = str(error).lower()
        if any(pattern in err_str for pattern in BENIGN_ERROR_PATTERNS):
            logger.debug(
                f"ℹ️ Benign Telegram UI state warning for User {user_id or 'Unknown'}: {error}"
            )
            if callback_query:
                try:
                    await callback_query.answer()
                except Exception:
                    pass
            return

    if isinstance(error, Forbidden):
        logger.info(
            f"ℹ️ Bot was blocked by User {user_id or 'Unknown'} or kicked from Chat {chat_id or 'Unknown'}."
        )
        return

    if isinstance(error, RetryAfter):
        logger.warning(
            f"⚠️ Telegram API rate limit reached. Retry after {error.retry_after}s for User {user_id or 'Unknown'}."
        )
        return

    if isinstance(error, Conflict):
        logger.error(
            "🚨 Critical Conflict: Multiple bot instances are running simultaneously on the same Telegram Token!"
        )
        return

    # --------------------------------------------------------------------------
    # STEP 2: SAFE LANGUAGE RESOLUTION & DISMISS SPINNER WITH ALERT TOAST
    # --------------------------------------------------------------------------
    try:
        lang = _resolve_user_language(context)
    except Exception:
        lang = "en"

    norm_lang = str(lang).lower().strip().split('_')[0] if lang else 'en'
    if norm_lang not in ('en', 'am', 'om'):
        norm_lang = 'en'

    lang_msgs = ERROR_MESSAGES.get(norm_lang, ERROR_MESSAGES["en"])

    if callback_query:
        try:
            await callback_query.answer(
                text=lang_msgs.get("cb_toast", "⚠️ An error occurred during processing."),
                show_alert=True  # Show alert so user sees the notification instead of silent drop
            )
        except Exception:
            pass  # Query might already be answered or expired

    # --------------------------------------------------------------------------
    # STEP 3: CATEGORIZE REAL EXCEPTIONS & NOTIFY USER WITH FULL TRACEBACK
    # --------------------------------------------------------------------------
    if isinstance(error, NETWORK_INFRASTRUCTURE_EXCEPTIONS):
        logger.warning(
            f"ℹ️ Transient network/infrastructure event for User ID {user_id or 'Unknown'}: {error}. Handled gracefully."
        )
        if chat_id or effective_msg:
            await _safe_send_error_notice(
                context=context,
                chat_id=chat_id,
                effective_msg=effective_msg,
                text=lang_msgs.get("network", "⚠️ Network timeout. Please try again."),
            )
        return

    # Uncaught Application Exceptions (Logs full traceback to debug pipeline / state drops)
    logger.error(
        f"🚨 Unhandled exception while processing update for User ID {user_id or 'Unknown'} in Chat {chat_id or 'Unknown'}: {error}",
        exc_info=error,
    )
    if chat_id or effective_msg:
        await _safe_send_error_notice(
            context=context,
            chat_id=chat_id,
            effective_msg=effective_msg,
            text=lang_msgs.get("unexpected", "⚠️ An unexpected system error occurred. Please restart with /start."),
        )


async def _safe_send_error_notice(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int | None,
    effective_msg: object | None,
    text: str,
) -> None:
    """Helper method to safely deliver error notices without triggering secondary exception loops."""
    if not chat_id and not effective_msg:
        return

    try:
        # Attempt 1: Reply directly to effective message if available
        if effective_msg and hasattr(effective_msg, "reply_text"):
            await effective_msg.reply_text(text, parse_mode="HTML")
        elif chat_id:
            await context.bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
    except BadRequest:
        # Fallback Attempt 2: Send plain text if HTML entity parsing failed
        try:
            clean_text = (
                text.replace("<b>", "")
                .replace("</b>", "")
                .replace("<i>", "")
                .replace("</i>", "")
            )
            if effective_msg and hasattr(effective_msg, "reply_text"):
                await effective_msg.reply_text(clean_text)
            elif chat_id:
                await context.bot.send_message(chat_id=chat_id, text=clean_text)
        except Exception as fallback_err:
            logger.debug(f"Failed fallback error notice delivery: {fallback_err}")
    except Exception as send_err:
        logger.debug(f"Could not deliver error notice to Chat {chat_id}: {send_err}")


# ==========================================
# 6. TELEGRAM BOT INITIALIZATION & SETUP
# ==========================================

def initialize_telegram_application():
    global telegram_app, redis_client

    # Early guard clause: Ensure bot token exists before attempting initialization
    telegram_token = getattr(settings, "TELEGRAM_TOKEN", None) or os.getenv(
        "TELEGRAM_TOKEN"
    )
    if not telegram_token:
        logger.critical(
            "❌ TELEGRAM_TOKEN is missing from settings and environment variables."
        )
        raise ValueError(
            "Cannot initialize Telegram Application without a valid TELEGRAM_TOKEN."
        )

    # Dynamic check: Use local proxy if configured in .env, otherwise default to direct connection
    proxy_url = getattr(settings, "TELEGRAM_PROXY_URL", None) or os.getenv(
        "TELEGRAM_PROXY_URL"
    )

    # Tuned HTTPX configuration optimized for quick socket setup and fast delivery
    request_kwargs = {
        "connection_pool_size": 100,  # High concurrency pool limit
        "connect_timeout": 30.0,  # Fast connect timeout (5s max) to prevent long TCP hangs
        "read_timeout": 30.0,  # Read timeout for standard outbound Telegram API calls
        "write_timeout": 30.0,  # Write timeout for payload/photo uploads
        "pool_timeout": 30.0,  # Prevents pool starvation without blocking threads
        "get_updates_read_timeout": 60.0,  # Exceeds long-poll duration (30s) to prevent client timeouts
        "http_version": "1.1",  # Forces HTTP/1.1 for socket stability
    }

    if proxy_url:
        logger.info(f"🌐 Routing Telegram traffic through proxy: {proxy_url}")
        request_kwargs["proxy_url"] = proxy_url
    else:
        logger.info("🌐 Initializing Telegram HTTPXRequest with direct connection.")

    # Robustness Filter: Filter out unsupported arguments against HTTPXRequest signature
    httpx_sig = inspect.signature(HTTPXRequest.__init__).parameters
    sanitized_request_kwargs = {
        k: v for k, v in request_kwargs.items() if k in httpx_sig
    }

    request_config = HTTPXRequest(**sanitized_request_kwargs)
    defaults = Defaults(parse_mode=ParseMode.MARKDOWN)

    # Multi-tiered persistence layer strategy:
    # 1. Production: RedisPersistence (Instant DB persistence immune to local restarts/power outages)
    # 2. Local Dev: PicklePersistence with frequent disk flush (update_interval=60.0)
    # 3. Last Resort: DictPersistence (in-memory)
    bot_persistence = None

    if "redis_client" in globals() and redis_client:
        try:
            bot_persistence = RedisPersistence(redis_client=redis_client)
            logger.info(
                "Telegram application initialized with production RedisPersistence."
            )
        except Exception as e:
            logger.warning(
                f"⚠️ Redis persistence initialization failed ({e}). Falling back to PicklePersistence."
            )

    if not bot_persistence:
        try:
            # Set update_interval=1.0 to flush session/conversation changes to disk every second.
            # This protects state loss in the event of power failures or sudden machine restarts.
            bot_persistence = PicklePersistence(
                filepath="smartmatch_bot_persistence.pickle",
                update_interval=60.0
            )
            logger.info(
                "Telegram application initialized with local file PicklePersistence (1s auto-flush)."
            )
        except Exception as e:
            logger.warning(
                f"⚠️ PicklePersistence initialization failed ({e}). Falling back to DictPersistence."
            )
            bot_persistence = DictPersistence()

    # Build Application
    telegram_app = (
        ApplicationBuilder()
        .token(telegram_token)
        .persistence(bot_persistence)
        .context_types(context_types)
        .request(request_config)
        .concurrent_updates(True)  # Prevents one user's DB flush from blocking others
        .defaults(defaults)
        .build()
    )

    # Register global error handler for runtime connection drops
    telegram_app.add_error_handler(global_error_handler)

    return telegram_app


# ==========================================
# 7. INFRASTRUCTURE WARMUP CONTROLLERS
# ==========================================
async def wake_up_aiven_service() -> bool:
    """Trigger Aiven REST API to power on the Valkey service if it is powered off."""
    if not AIVEN_API_TOKEN or not AIVEN_PROJECT_NAME:
        logger.warning("⚠️ Missing AIVEN configs. Cannot send automated wake-up signal to Aiven.")
        return False

    url = f"https://api.aiven.io/v1/project/{AIVEN_PROJECT_NAME}/service/{AIVEN_SERVICE_NAME}"
    headers = {
        "Authorization": f"aivenv1 {AIVEN_API_TOKEN}",
        "Content-Type": "application/json"
    }

    logger.info(f"🔌 Sending power-on request to Aiven service '{AIVEN_SERVICE_NAME}'...")
    try:
        async with httpx.AsyncClient(timeout=10.0) as http_client:
            response = await http_client.put(url, json={"powered": True}, headers=headers)
            if response.status_code in (200, 201):
                logger.info("⚡ Aiven power-on signal accepted. Waiting for service node initialization...")
                return True
            logger.warning(f"⚠️ Aiven power-on request returned non-success status. HTTP {response.status_code}: {response.text}")
    except Exception as err:
        # Gracefully logged as a warning instead of error to avoid false alarms when direct connection fallback/recovery occurs
        logger.warning(f"⚠️ Aiven API initial check encountered an issue (will attempt direct connection): {str(err)}")
    return False


async def connect_redis_with_retry(client: Any, max_attempts: int = 30, delay: int = 10) -> None:
    """Ping Redis/Valkey with an extended window for cold-starts and DNS propagation."""
    if client is None:
        return

    attempt = 0
    while attempt < max_attempts:
        attempt += 1
        try:
            await client.ping()
            logger.info("⚡ Valkey connection established successfully.")
            return
        except (RedisConnectionError, socket.gaierror, OSError) as conn_err:
            if attempt >= max_attempts:
                logger.critical(f"❌ Valkey service failed to wake up or resolve after {max_attempts * delay} seconds.")
                raise conn_err
            logger.info(
                f"⏳ Waiting for Valkey DNS to provision... (Attempt {attempt}/{max_attempts}). Retrying in {delay}s...")
            await asyncio.sleep(delay)


async def init_infrastructure() -> Tuple[AsyncClient, Optional[aioredis.Redis]]:
    """INITIALIZER: Fast connection setup optimized for sub-10s cold starts on Render free tier."""
    global supabase_client, redis_client
    logger.info("Initializing connection parameters for stateful storage layers...")

    supabase_url = SUPABASE_URL.strip().strip("'\"")
    service_role_key = SERVICE_ROLE_KEY.strip().strip("'\"")
    clean_redis_url = REDIS_URL.strip().strip("'\"")

    if not supabase_url or not service_role_key:
        raise ValueError("SUPABASE_URL or SERVICE_ROLE_KEY missing from configuration.")

    # Optimized for fast cold-start compliance (< 10s total timeout)
    max_retries = 3
    retry_delay = 1.0  # seconds

    for attempt in range(1, max_retries + 1):
        try:
            logger.info(f"🔄 Initializing infrastructure and testing connections (Attempt {attempt}/{max_retries})...")

            # Client handle creation with tight timeout constraints
            supabase_client = await acreate_client(supabase_url=supabase_url, supabase_key=service_role_key)

            # Fast test connection health
            await supabase_client.table("users").select("telegram_id").limit(1).execute()
            logger.info("✅ Supabase REST client authenticated successfully.")

            # Instantiates async Redis client with hardened health check parameters
            if clean_redis_url and redis_client is None:
                redis_client = aioredis.from_url(
                    clean_redis_url,
                    decode_responses=False,
                    socket_connect_timeout=10,
                    socket_timeout=10,
                    health_check_interval=30  # Actively pings and keeps socket alive
                )

            break

        except Exception as err:
            logger.warning(f"⚠️ Infrastructure connection attempt {attempt} failed: {err}")
            if attempt == max_retries:
                logger.critical(
                    f"❌ Critical failure initializing infrastructure engines after {max_retries} attempts: {str(err)}",
                    exc_info=True)
                raise err

            await asyncio.sleep(retry_delay)
            retry_delay *= 1.5  # Gentle backoff

    return supabase_client, redis_client


async def _background_warmup_pipeline(rd_client: Any, ai_eng: Any, match_eng: Any) -> None:
    """Non-blocking background worker for heavy DNS checks and lightweight resource initialization."""
    logger.info("⚡ [Background] Executing non-blocking Valkey wake-up & lightweight resource warmup...")

    if rd_client is not None:
        try:
            logger.info("🔌 [Background] Proactively checking/waking Aiven Valkey...")
            woke_up = await wake_up_aiven_service()
            if woke_up:
                await asyncio.sleep(2)

            # Fast, resilient background redis check with bounded retries
            await connect_redis_with_retry(rd_client, max_attempts=3, delay=3)
            logger.info("✅ [Background] Redis cache successfully synchronized.")
        except Exception as valkey_err:
            logger.error(f"⚠️ [Background] Valkey startup issue (proceeding gracefully without blocking): {valkey_err}")

    try:
        def _load_ml_models():
            logger.info("🧠 [Background] Skipping heavy HuggingFace AI model pre-warming to conserve RAM...")
            if hasattr(ai_eng, "warmup") and callable(getattr(ai_eng, "warmup", None)):
                try:
                    ai_eng.warmup()
                except Exception:
                    pass

            if hasattr(match_eng, "warmup") and callable(getattr(match_eng, "warmup", None)):
                try:
                    match_eng.warmup()
                except Exception:
                    pass

        # Execute the wrapper safely in a thread to maintain exact structural hook compatibility
        await asyncio.to_thread(_load_ml_models)
        logger.info("✅ [Background] Application background initialization completed successfully.")

    except Exception as ai_err:
        logger.error(f"⚠️ [Background] Background initialization notice: {ai_err}")


# ==========================================
# 8. TELEGRAM BOT SYSTEM COMMAND HANDLERS
# ==========================================
_handlers_initialized = False


def register_telegram_handlers() -> None:
    global main_menu, telegram_app, _handlers_initialized, billing_service, webhook_handler

    if _handlers_initialized:
        logger.warning("Handlers already initialized. Skipping to prevent duplication.")
        return

    # Self-healing check: instantiate telegram_app if it hasn't been built yet
    if telegram_app is None:
        logger.info("⚡ Telegram application instance is None. Initializing now...")
        telegram_app = initialize_telegram_application()

    if telegram_app is None:
        raise RuntimeError("❌ Critical: Failed to build or retrieve the Telegram application instance.")

    logger.info("Initializing vertical business domain handlers...")

    if not all([db_manager, queue_manager, billing_service]):
        logger.critical("Cannot register Telegram handlers: Core dependency singletons are missing.")
        raise RuntimeError("Service injection failed due to missing globals.")

    # Late-binding wire-up: inject WebhookHandler into BillingService context to resolve circular dependency
    if billing_service and webhook_handler:
        billing_service.webhook_handler = webhook_handler
        logger.info("🔗 Late-binding active: Successfully bound WebhookHandler to BillingService singleton.")

    if telegram_app and telegram_app.bot_data:
        telegram_app.bot_data["db"] = db_manager
        telegram_app.bot_data["supabase"] = supabase_client
        telegram_app.bot_data["webhook_handler"] = webhook_handler
        telegram_app.bot_data["billing_service"] = billing_service

    housing = HousingHandler(
        db_manager=db_manager,
        ai_engine=ai_engine,
        matching_engine=matching_engine,
        billing_service=billing_service,
        image_service=image_service,
        deals_handler=deals_handler,
        queue_manager=queue_manager
    )

    vehicle = VehicleHandler(
        db_manager=db_manager,
        ai_engine=ai_engine,
        matching_engine=matching_engine,
        billing_service=billing_service,
        image_service=image_service,
        deals_handler=deals_handler,
        queue_manager=queue_manager
    )

    freight = FreightRideHandler(
        db_manager=db_manager,
        ai_engine=ai_engine,
        matching_engine=matching_engine,
        billing_service=billing_service,
        deals_handler=deals_handler,
        image_service=image_service,
        queue_manager=queue_manager
    )

    equip = EquipmentRentalHandler(
        db_manager=db_manager,
        ai_engine=ai_engine,
        matching_engine=matching_engine,
        billing_service=billing_service,
        image_service=image_service,
        deals_handler=deals_handler,
        queue_manager=queue_manager
    )

    verify = DriverVerificationHandler(
        db_manager=db_manager,
        ai_engine=ai_engine,
        queue_manager=queue_manager,
        telegram_app=telegram_app
    )

    main_menu = MainMenuHandler(
        vehicle_handler=vehicle,
        equipment_handler=equip,
        freight_ride_handler=freight,
        housing_handler=housing,
        db=db_manager,
        supabase=supabase_client
    )

    # Handlers injected by strict priority grouping
    telegram_app.add_handler(CommandHandler(["terms", "privacy"], terms_command), group=0)

    telegram_app.add_handler(deals_handler.get_text_interceptor(), group=-1)

    for handler in deals_handler.get_action_handlers():
        telegram_app.add_handler(handler, group=0)

    # Register active conversation handlers and menu workflows first in group=0
    telegram_app.add_handler(housing.get_conversation_handler(), group=0)
    telegram_app.add_handler(vehicle.get_conversation_handler(), group=0)
    telegram_app.add_handler(freight.get_conversation_handler(), group=0)
    telegram_app.add_handler(equip.get_conversation_handler(), group=0)
    telegram_app.add_handler(verify.get_verification_handler(), group=0)

    # Dynamically inject main_menu handlers but strip conflicting generic message handlers
    for handler in main_menu.get_main_menu_handlers():
        if isinstance(handler, MessageHandler):
            continue
        telegram_app.add_handler(handler, group=0)

    # Register global text router at the bottom of group 0
    telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, global_text_router), group=0)

    # 🛡️ Low-priority fallback in group=1: Catches orphaned text inputs when active conversation states are lost
    telegram_app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_orphaned_text_input),
        group=1
    )

    # Catch unhandled orphaned callbacks at group 999
    telegram_app.add_handler(CallbackQueryHandler(global_unhandled_callback_handler), group=999)

    _handlers_initialized = True
    logger.info("✅ Telegram handlers registered successfully with priority groups.")


# ==========================================
# 9. BACKGROUND PROCESSES & TEARDOWN
# ==========================================
async def render_keep_alive_pulse():
    """
    Sends an HTTP GET request to the external public health endpoint every 9 minutes
    to ensure Render's edge router registers inbound external traffic, preventing free-tier idle spin-downs.
    """
    await asyncio.sleep(30)
    port = getattr(settings, "SERVER_PORT", int(os.environ.get("PORT", 8080)))
    local_url = f"http://127.0.0.1:{port}/health"

    # Resolve the external public URL dynamically
    app_url = (
            os.getenv("PRODUCTION_API_URL") or
            os.getenv("RENDER_EXTERNAL_URL") or
            getattr(settings, "PRODUCTION_API_URL", None)
    )

    _external_url = None
    if app_url:
        clean_domain = app_url.strip().replace("http://", "").replace("https://", "").rstrip("/")
        external_url = f"https://{clean_domain}/health"
    else:
        # Fallback default pointing to your live Render service domain
        external_url = "https://smart-match-africa-gateway-ijg3.onrender.com/health"

    logger.info(
        f"🚀 Starting robust keep-alive pulse. Primary target (External): {external_url} | Fallback (Local): {local_url}")

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        while not shutdown_event.is_set():
            success = False

            # 1. Try External URL First (Crucial for resetting Render's edge router idle timer)
            if external_url:
                try:
                    ext_resp = await client.get(external_url)
                    if ext_resp.status_code == 200:
                        logger.info(f"✅ External keep-alive pulse successful | Status: 200 OK | Target: {external_url}")
                        success = True
                    else:
                        logger.warning(f"⚠️ External keep-alive returned status code: {ext_resp.status_code}")
                except Exception as ext_err:
                    logger.warning(
                        f"⚠️ External keep-alive connection failed ({repr(ext_err)}). Trying local fallback...")

            # 2. Fallback to Local URL if External failed (e.g., network/DNS glitch)
            if not success:
                try:
                    response = await client.get(local_url)
                    payload = {}
                    try:
                        payload = response.json()
                    except Exception:
                        pass

                    if response.status_code == 200:
                        logger.info(
                            f"✅ Local fallback keep-alive pulse successful | Status: 200 OK | "
                            f"Cache: {payload.get('cache', 'active')}"
                        )
                    else:
                        db_state = payload.get("database", "unknown")
                        cache_state = payload.get("cache", "unknown")
                        reason = payload.get("detail", "Service degraded")
                        logger.warning(
                            f"⚠️ Local keep-alive pulse status {response.status_code} ({reason}) | "
                            f"DB: {db_state} | Cache: {cache_state}"
                        )
                except Exception as local_err:
                    logger.error(f"❌ Both external and local keep-alive pulses failed: {repr(local_err)}")

            try:
                # Sleep for 9 minutes (540 seconds) - safely below Render's 15-minute (900s) idle threshold
                await asyncio.sleep(540)
            except asyncio.CancelledError:
                break


# ==========================================
# 9. BACKGROUND PROCESSES & TEARDOWN
# ==========================================
async def shutdown_resources():
    global db_manager, redis_client
    logger.info("Recycling open storage pools and cache socket threads...")

    try:
        if db_manager and hasattr(db_manager, 'close'):
            await asyncio.wait_for(db_manager.close(), timeout=3.0)
    except Exception as e:
        logger.error(f"Error closing Database Manager: {e}")

    try:
        if redis_client:
            if hasattr(redis_client, 'aclose'):
                await asyncio.wait_for(redis_client.aclose(), timeout=3.0)
            else:
                await redis_client.close()
    except Exception as e:
        logger.error(f"Error closing Redis Client connection: {e}")

    logger.info("🔌 All resources recycled safely. Core application process offline.")


# ==========================================
# TYPE HINT DEFINITIONS FOR PYCHARM COMPLIANCE
# ==========================================
class SmartMatchAppState(Protocol):
    """Structural type protocol to eliminate PyCharm app.state inspection errors."""
    db_client: AsyncClient
    redis_client: aioredis.Redis
    queue_manager: 'QueueManager'
    webhook_handler: 'WebhookHandler'


# ==========================================
# 10. MASTER UNIFIED LIFESPAN MANAGEMENT
# ==========================================
def _fetch_active_ngrok_url_sync() -> str | None:
    """Helper function to fetch ngrok tunnel synchronously.
    Skipped automatically in Render production to avoid startup timeout delays."""
    # Skip local ngrok inspection entirely if running on Render
    if os.environ.get("RENDER") or os.environ.get("RENDER_EXTERNAL_URL"):
        return None

    try:
        import urllib.request
        import json
        req = urllib.request.Request(
            "http://127.0.0.1:4040/api/tunnels",
            headers={"User-Agent": "SmartMatchAfricaBot"}
        )
        # Reduced timeout to 1 second so local debugging isn't slowed down if ngrok isn't running
        with urllib.request.urlopen(req, timeout=1) as response:
            if response.status == 200:
                data = json.loads(response.read().decode())
                for tunnel in data.get("tunnels", []):
                    if tunnel.get("proto") == "https":
                        return tunnel.get("public_url")
    except Exception:
        pass
    return None


@asynccontextmanager
async def master_lifespan(_app: FastAPI):
    global db_manager, billing_service, image_service, ai_engine, matching_engine, deals_handler
    global queue_manager, webhook_handler, supabase_client, redis_client

    logger.info("🚀 Initializing Smart Match Africa Integrated Application Lifecycle...")
    background_tasks: Set[asyncio.Task] = set()

    try:
        if (supabase_client is None or redis_client is None) and "init_infrastructure" in globals():
            supabase_client, redis_client = await init_infrastructure()

        state: SmartMatchAppState = _app.state  # type: ignore

        state.db_client = supabase_client
        state.redis_client = redis_client

        db_manager = DatabaseManager(supabase_client, redis_client)
        db_manager.supabase = supabase_client
        db_manager.redis = redis_client
        db_module.db_manager = db_manager

        # 1. Create BillingService WITHOUT the webhook_handler
        billing_service = BillingService(db_client=supabase_client)
        state.billing_service = billing_service

        endpoint = settings.B2_ENDPOINT_URL
        if endpoint and not endpoint.startswith(("http://", "https://")):
            endpoint = f"https://{endpoint}"

        image_service = ImageService(
            aws_access_key=settings.B2_KEY_ID,
            aws_secret_key=settings.B2_APPLICATION_KEY,
            region_name="eu-central-003",
            bucket_name="sma-assets-image-bucket",
            endpoint_url=endpoint  # <-- Fixed: use the local variable here instead of settings.B2_ENDPOINT_URL
        )

        ai_engine = SmartMatchAIEngine()
        matching_engine = MatchingEngine(db_manager=db_manager)
        deals_handler = DealsHandler(db_manager=db_manager, billing_service=billing_service, telegram_app=telegram_app)

        queue_manager = QueueManager(worker_count=getattr(settings, 'WORKER_COUNT', 5))
        state.queue_manager = queue_manager

        # 2. Create WebhookHandler and pass the billing_service in
        webhook_handler = WebhookHandler(
            billing_service=billing_service,
            db_manager=db_manager,
            queue_manager=queue_manager,
            webhook_secret=settings.CHAPA_WEBHOOK_SECRET,
            telegram_app=telegram_app
        )
        state.webhook_handler = webhook_handler

        # 3. Late-Binding: Inject webhook_handler into billing_service
        billing_service.webhook_handler = webhook_handler
        logger.info("🔗 Late-binding active: Successfully bound WebhookHandler to BillingService singleton.")

        await queue_manager.start()

        currency_interval = getattr(settings, 'CURRENCY_SYNC_INTERVAL_SECONDS', 21600)
        await queue_manager.start_currency_worker(db_client=supabase_client, interval_seconds=currency_interval)

        # ==========================================
        # 📍 START TELETHON LOCATION TRACKING WORKER (NON-BLOCKING)
        # ==========================================
        async def safe_start_location_worker():
            try:
                await run_location_worker()
            except Exception as loc_err:
                logger.error(f"⚠️ Location tracking worker encountered an unhandled exception: {loc_err}",
                             exc_info=True)

        location_worker_task = asyncio.create_task(safe_start_location_worker())
        background_tasks.add(location_worker_task)
        location_worker_task.add_done_callback(background_tasks.discard)
        logger.info("📍 Telethon location tracking background daemon initiated successfully (non-blocking).")

        # ==========================================
        # 🔄 STARTUP RECOVERY: RESUME PENDING DISPATCHES
        # ==========================================
        async def safe_recovery():
            try:
                await recover_undelivered_dispatches(webhook_handler)
            except Exception as rec_err:
                logger.error(f"⚠️ Startup recovery task failed: {rec_err}", exc_info=True)

        recovery_task = asyncio.create_task(safe_recovery())
        background_tasks.add(recovery_task)
        recovery_task.add_done_callback(background_tasks.discard)

        if "register_telegram_handlers" in globals():
            register_telegram_handlers()

        # Save telegram_app to state for route handlers
        if "telegram_app" in globals() and telegram_app:
            state.telegram_app = telegram_app

            # ==========================================
            # 🚀 LIGHTNING-FAST TELEGRAM INITIALIZATION
            # ==========================================
            max_retries = 3
            base_delay = 1.0

            for attempt in range(max_retries):
                try:
                    logger.info(f"Initializing Telegram Bot core instance (Attempt {attempt + 1}/{max_retries})...")
                    await telegram_app.initialize()
                    await telegram_app.start()
                    logger.info("✅ Telegram application initialized and started successfully.")
                    break
                except Exception as e:
                    if attempt == max_retries - 1:
                        logger.warning(
                            f"⚠️ Telegram startup encountered minor lag during cold restart; proceeding with background recovery: {e}")
                        break

                    sleep_time = base_delay * (attempt + 1)
                    logger.warning(f"⚠️ Telegram API connection retry in {sleep_time}s... (Error: {e})")
                    await asyncio.sleep(sleep_time)

            # ==========================================
            # 📅 START SCHEDULED DAILY MENU REPOST WORKER (NON-BLOCKING)
            # ==========================================
            if queue_manager and supabase_client:
                async def safe_start_repost_worker():
                    try:
                        await queue_manager.start_daily_menu_repost_worker(
                            telegram_app=telegram_app,
                            db_client=supabase_client,
                            target_hour_eat=3  # 3:00 AM EAT dispatch
                        )
                    except Exception as repost_err:
                        logger.error(f"⚠️ Daily menu repost worker failed to start: {repost_err}", exc_info=True)

                repost_task = asyncio.create_task(safe_start_repost_worker())
                background_tasks.add(repost_task)
                repost_task.add_done_callback(background_tasks.discard)

        # ==========================================
        # 🌐 ROBUST NON-BLOCKING URL & WEBHOOK RESOLUTION
        # ==========================================
        active_ngrok_url = await asyncio.to_thread(_fetch_active_ngrok_url_sync)

        # Enforce Render's live ijg3 production URL with fallback safety
        raw_app_url = (
                os.environ.get("RENDER_EXTERNAL_URL") or
                os.environ.get("BASE_URL") or
                os.getenv("WEBHOOK_URL") or
                os.getenv("PRODUCTION_API_URL") or
                active_ngrok_url or
                "https://smart-match-africa-gateway-ijg3.onrender.com"
        )

        # Automatically sanitize old service names to prevent 502 Bad Gateway binding errors
        if raw_app_url and "smart-match-africa-gateway.onrender.com" in raw_app_url and "ijg3" not in raw_app_url:
            raw_app_url = "https://smart-match-africa-gateway-ijg3.onrender.com"

        placeholders = ("your-active-ngrok-subdomain", "example.com", "placeholder")
        is_valid_url = raw_app_url and not any(p in raw_app_url for p in placeholders)

        if is_valid_url:
            raw_url = raw_app_url.strip()
            if not raw_url.startswith(("http://", "https://")):
                raw_url = f"https://{raw_url}"

            if raw_url.endswith("/webhooks/telegram"):
                webhook_endpoint = raw_url
            else:
                webhook_endpoint = f"{raw_url.rstrip('/')}/webhooks/telegram"

            # Force exact active ijg3 webhook endpoint compliance
            if "smart-match-africa-gateway" in webhook_endpoint and "ijg3" not in webhook_endpoint:
                webhook_endpoint = "https://smart-match-africa-gateway-ijg3.onrender.com/webhooks/telegram"

            logger.info(f"Setting Telegram webhook to: {webhook_endpoint}")
            bot_instance: ExtBot = getattr(telegram_app, "bot")

            # Robust webhook registration with retry handling
            webhook_success = False
            for wh_attempt in range(1, 4):
                try:
                    await bot_instance.set_webhook(
                        url=webhook_endpoint,
                        drop_pending_updates=True,
                        secret_token=getattr(settings, 'TELEGRAM_WEBHOOK_SECRET', None) or os.getenv(
                            "TELEGRAM_WEBHOOK_SECRET"),
                        allowed_updates=Update.ALL_TYPES
                    )
                    logger.info(f"✅ Telegram webhook configured successfully on attempt {wh_attempt}.")
                    webhook_success = True
                    break
                except Exception as wh_err:
                    logger.warning(f"⚠️ Webhook registration attempt {wh_attempt} failed: {wh_err}")
                    await asyncio.sleep(2.0 * wh_attempt)

            if not webhook_success:
                logger.error("❌ CRITICAL: Failed to register Telegram webhook after 3 attempts.")
        else:
            logger.warning(
                "⚠️ No valid production URL or active ngrok tunnel detected. Falling back to polling mode...")
            updater_val = getattr(telegram_app, "updater", None)
            if updater_val:
                await updater_val.start_polling(drop_pending_updates=True)

        warmup_task = asyncio.create_task(
            _background_warmup_pipeline(redis_client, ai_engine, matching_engine)
        )
        background_tasks.add(warmup_task)
        warmup_task.add_done_callback(background_tasks.discard)

        if "render_keep_alive_pulse" in globals():
            pulse_task = asyncio.create_task(render_keep_alive_pulse())
            background_tasks.add(pulse_task)
            pulse_task.add_done_callback(background_tasks.discard)

        logger.info("🚀 Core System Online. Web services active; non-blocking warmups & location worker running.")
        yield

    except Exception as startup_error:
        logger.critical(f"❌ Fatal crash during system runtime startup phase: {str(startup_error)}", exc_info=True)
        if "shutdown_event" in globals():
            shutdown_event.set()
        raise startup_error

    finally:
        logger.info("🔌 Teardown signal caught. Initiating graceful shutdown protocol...")
        if "shutdown_event" in globals():
            shutdown_event.set()

        try:
            for bg_task in list(background_tasks):
                if not bg_task.done():
                    bg_task.cancel()

            if background_tasks:
                await asyncio.gather(*background_tasks, return_exceptions=True)

            try:
                shutdown_updater = getattr(telegram_app, "updater", None)
                if shutdown_updater and getattr(shutdown_updater, "running", False):
                    await shutdown_updater.stop()

                if telegram_app and getattr(telegram_app, "running", False):
                    await telegram_app.stop()
                    await telegram_app.shutdown()
            except Exception as shutdown_tg_err:
                logger.error(f"Error shutting down Telegram cleanly: {shutdown_tg_err}")

            if queue_manager:
                try:
                    await queue_manager.stop_workers()
                except Exception as shutdown_queue_err:
                    logger.error(f"Error stopping queue workers: {shutdown_queue_err}")

            if "shutdown_resources" in globals():
                await shutdown_resources()

            logger.info("✅ Graceful shutdown protocol completed successfully. Exiting process.")

        except asyncio.CancelledError:
            logger.warning("⚠️ Shutdown was interrupted by a forced Uvicorn server timeout.")
        except Exception as teardown_err:
            logger.error(f"❌ Unexpected error during teardown execution: {teardown_err}")


# ==========================================
# 11. FASTAPI CORE & REST ROUTER SETUP
# ==========================================
app = FastAPI(
    title="Smart Match Africa Integrated Gateway",
    description="Unified API gateway orchestration, webhooks, and Mini App frontend delivery.",
    version="4.0.0",
    lifespan=master_lifespan,
    docs_url="/docs" if settings.DEBUG_MODE else None,
    redoc_url="/redoc" if settings.DEBUG_MODE else None
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.DEBUG_MODE else [
        "https://smartmatchafrica.com",
        "https://www.smartmatchafrica.com",
        "https://dashboard.smartmatchafrica.com",
        "https://smart-match-africa-gateway-ijg3.onrender.com",
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Chapa-Signature", "chapa-signature"],
)

api_router = APIRouter()


# ------------------------------------------------------------------------
# FRONTEND HTML ROUTING (MINI APP & TERMS/PRIVACY)
# ------------------------------------------------------------------------
@api_router.get("/", response_class=HTMLResponse, tags=["Frontend"])
@api_router.get("/privacy", response_class=HTMLResponse, tags=["Frontend"])
@api_router.get("/terms", response_class=HTMLResponse, tags=["Frontend"])
async def serve_frontend():
    """Serves the Single Page Application index.html for WebApp compliance."""
    current_dir = os.path.dirname(os.path.abspath(__file__))

    candidate_paths = [
        os.path.join(current_dir, '..', '..', 'frontend', 'index.html'),
        os.path.join(current_dir, '..', 'frontend', 'index.html'),
        os.path.join(current_dir, 'frontend', 'index.html'),
        os.path.join(os.getcwd(), 'frontend', 'index.html'),
    ]

    for file_path in candidate_paths:
        normalized_path = os.path.normpath(file_path)
        if os.path.exists(normalized_path):
            try:
                with open(normalized_path, 'r', encoding='utf-8') as f:
                    return HTMLResponse(content=f.read(), status_code=200)
            except Exception as read_err:
                logger.error(f"Error reading frontend file at {normalized_path}: {read_err}")

    logger.error("❌ Frontend deployment file index.html not found in candidate paths.")
    return HTMLResponse(
        content="<h1>Deploy Error 404</h1><p>index.html not found. Check deployment paths.</p>",
        status_code=404
    )


@api_router.get("/api/v1/search", tags=["Search"])
async def search_listings(
        request: Request,
        q: str = Query(..., min_length=2, description="The user's search text"),
        vertical_type: Optional[str] = Query(None, description="e.g., equipment, housing, ride"),
        limit: int = Query(10, le=50)
):
    """
    Hybrid search: Executes fast pg_trgm lexical RPC query with
    automatic fallback/enrichment via SemanticMatcher vector scoring.
    """
    clean_q = q.strip()
    if not clean_q:
        return {"status": "success", "matches": [], "search_type": "none"}

    supabase = getattr(request.app.state, "db_client", None)
    if not supabase:
        logger.error("Supabase client missing from app state.")
        raise HTTPException(status_code=503, detail="Database service currently unavailable")

    try:
        rpc_args = {
            "search_term": clean_q,
            "similarity_threshold": 0.15,
            "result_limit": limit
        }
        if vertical_type:
            rpc_args["p_vertical_type"] = vertical_type

        response = await supabase.rpc("match_provider_listings", rpc_args).execute()
        matches = response.data if response and hasattr(response, "data") and response.data else []

        search_type = "lexical_trgm"
        if not matches and hasattr(request.app.state, "semantic_matcher"):
            matcher = request.app.state.semantic_matcher
            candidates_res = await supabase.table("provider_listings").select("id, title, description")\
                .eq("is_active", True).limit(100).execute()

            if candidates_res.data:
                candidate_titles = [c.get("title", "") for c in candidates_res.data]
                semantic_results = matcher.batch_similarity(clean_q, candidate_titles, threshold=0.55)

                matched_ids = [
                    candidates_res.data[i]["id"]
                    for i, (_, score, is_match) in enumerate(semantic_results) if is_match
                ]

                if matched_ids:
                    matches = [c for c in candidates_res.data if c["id"] in matched_ids][:limit]
                    search_type = "semantic_vector_fallback"

        return {
            "status": "success",
            "count": len(matches),
            "search_type": search_type,
            "matches": matches
        }

    except Exception as e:
        logger.error(f"Search endpoint execution failure for query '{clean_q}': {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="An error occurred processing your search request.")


# ------------------------------------------------------------------------
# HEALTH, WEBHOOKS & CRON ENDPOINTS
# ------------------------------------------------------------------------

@api_router.post("/webhooks/telegram", tags=["Telegram Webhook"])
async def telegram_webhook(request: Request, background_tasks: BackgroundTasks) -> JSONResponse:
    if not telegram_app:
        return JSONResponse(status_code=503, content={"detail": "Service unavailable"})

    try:
        expected_secret = os.getenv("TELEGRAM_WEBHOOK_SECRET")
        received_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
        if expected_secret and received_secret != expected_secret:
            logger.warning(f"Unauthorized webhook attempt. Received: '{received_secret}'")
            return JSONResponse(status_code=401, content={"detail": "Unauthorized"})

        data = await request.json()
        bot_instance = getattr(telegram_app, "bot", None)
        if not bot_instance or not getattr(telegram_app, "running", False):
            return JSONResponse(status_code=503, content={"detail": "Bot engine initializing"})

        update = Update.de_json(data, bot_instance)

        # Decouple processing via background tasks so Telegram receives 200 OK immediately (<50ms)
        background_tasks.add_task(telegram_app.process_update, update)

        return JSONResponse(status_code=200, content={"status": "ok"})

    except Exception as e:
        logger.error(f"❌ Error processing Telegram webhook update: {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"detail": "Error processing update payloads."})


@api_router.get("/health", tags=["Diagnostics"])
async def get_system_health(request: Request):
    """Diagnostic system health check endpoint evaluating DB and Redis connectivity."""
    active_db = getattr(db_manager, "supabase", None) or getattr(request.app.state, "db_client", None)
    if not db_manager or not active_db:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "status": "unhealthy",
                "detail": "Database core engine not initialized",
                "timestamp": asyncio.get_event_loop().time(),
                "database": "disconnected",
                "cache": "uninitialized",
                "service": "Smart Match Africa Gateway",
                "version": "4.0.0"
            }
        )

    try:
        is_db_healthy = await db_manager.health_check()
    except Exception as ex:
        logger.error(f"Health check execution exception: {str(ex)}", exc_info=True)
        is_db_healthy = False

    active_redis = (
            getattr(db_manager, "redis", None)
            or getattr(request.app.state, "redis_client", None)
            or globals().get("redis_client")
    )

    if active_redis:
        try:
            await asyncio.wait_for(active_redis.ping(), timeout=1.0)
            cache_status = "active"
        except Exception:
            cache_status = "provisioning_or_unreachable"
    else:
        cache_status = "uninitialized"

    if is_db_healthy:
        overall_status = "healthy" if cache_status == "active" else "degraded"
        http_code = status.HTTP_200_OK
    else:
        overall_status = "unhealthy"
        http_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return JSONResponse(
        status_code=http_code,
        content={
            "status": overall_status,
            "timestamp": asyncio.get_event_loop().time(),
            "database": "connected (Supabase SDK)" if is_db_healthy else "disconnected",
            "cache": cache_status,
            "service": "Smart Match Africa Gateway",
            "version": "4.0.0"
        }
    )


@api_router.post("/cron/rollback-expired-reservations", tags=["System Cron"])
async def trigger_rollback(authorization: str = Header(None)):
    expected_secret = os.getenv("SUPABASE_SERVICE_KEY")
    supabase_url = os.getenv("SUPABASE_URL")

    if not authorization or authorization != f"Bearer {expected_secret}":
        raise HTTPException(status_code=401, detail="Unauthorized")

    if not supabase_url or not expected_secret:
        raise HTTPException(status_code=500, detail="Database credentials missing from environment.")

    client_options = AsyncClientOptions(postgrest_client_timeout=30, storage_client_timeout=30)
    admin_supabase_client = await acreate_client(
        supabase_url,
        expected_secret,
        options=client_options
    )

    rolled_back_count = await ReservationHelper.rollback_expired_reservations(admin_supabase_client)
    return {"status": "success", "rolled_back": rolled_back_count}


async def handle_dispatch_failure(handler: Any, deal_id: str, chat_id: int, payload: dict, error_msg: str) -> None:
    """
    Persists failed dispatches into a database retry table for background worker pickup.
    """
    logger.error(f"Persisting failed dispatch for deal {deal_id} to chat {chat_id} due to: {error_msg}")

    db_client = getattr(
        handler.db, "supabase", handler.db
    )

    retry_payload = {
        "deal_id": deal_id,
        "chat_id": chat_id,
        "payload": payload,
        "attempt_count": 1,
        "status": "pending_retry",
        "next_retry_at": datetime.utcnow() + timedelta(seconds=60)  # Exponential backoff
    }

    try:
        # Insert into database retry table securely
        await db_client.table("notification_retries").insert(retry_payload).execute()
    except Exception as db_err:
        logger.error(f"Failed to persist dispatch failure retry payload for deal {deal_id}: {db_err}")


async def recover_undelivered_dispatches(handler: Any) -> None:
    """
    Run on startup/shutdown to process deals paid but not dispatched before crash/restart.
    Updated: Selects and uses the existing 'payment_reference' column from the deals table.
    """
    db_client = getattr(
        handler.db, "supabase", handler.db
    )

    # Query the existing 'payment_reference' column along with 'id'
    res = (
        await db_client.table("deals")
        .select("id, payment_reference")
        .eq("payment_status", "paid")
        .or_("dispatch_sent.is.null,dispatch_sent.eq.false")
        .execute()
    )

    if res and res.data:
        logger.info(f"Startup Recovery: Found {len(res.data)} pending dispatches.")
        for deal_item in res.data:
            # Use 'payment_reference' if populated, falling back to 'id' if null/empty
            tx_ref = deal_item.get("payment_reference") or deal_item.get("id")
            await handler.process_payment_async(
                {"tx_ref": tx_ref, "status": "success"}
            )


# In-memory tracking set and async lock to guarantee zero-race protection
# even during Aiven Valkey cold-starts or DNS wake-up delays.
_local_processing_tx_refs: Set[str] = set()
_local_lock = asyncio.Lock()


async def release_local_lock_after_ttl(tx_ref: str, ttl: int = 30):
    """Background task to hold the local memory lock for the exact same duration as the Redis TTL."""
    await asyncio.sleep(ttl)
    async with _local_lock:
        _local_processing_tx_refs.discard(tx_ref)


@api_router.post("/webhooks/chapa", tags=["Financials"])
async def handle_chapa_callback(
        request: Request,
        background_tasks: BackgroundTasks,
        x_chapa_signature: Optional[str] = Header(None, alias="x-chapa-signature"),
        chapa_signature: Optional[str] = Header(None, alias="chapa-signature"),
) -> Dict[str, str]:
    """Receives asynchronous payment notifications directly from Chapa with dual-layer async locking."""
    signature = x_chapa_signature or chapa_signature

    if not webhook_handler:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Payment processing sub-engine offline",
        )

    # 1. Extract tx_ref from body for atomic locking
    tx_ref = None
    try:
        body_bytes = await request.body()
        if body_bytes:
            body_json = json.loads(body_bytes.decode("utf-8"))
            data_payload = body_json.get("data", {})
            tx_ref = data_payload.get("tx_ref") or body_json.get("tx_ref")
    except Exception:
        pass

    lock_acquired = True
    lock_key = None

    # 2. Layer 1: In-Memory Fast Local Lock (Active instantly, bypasses Redis cold-start gaps)
    if tx_ref:
        async with _local_lock:
            if tx_ref in _local_processing_tx_refs:
                lock_acquired = False
            else:
                _local_processing_tx_refs.add(tx_ref)

    # 3. Layer 2: Distributed Redis/Valkey Lock
    if lock_acquired and tx_ref and redis_client:
        lock_key = f"lock:chapa:webhook:{tx_ref}"
        try:
            # 30-second TTL prevents aggressive webhook retries from bypassing idempotency
            redis_acquired = await redis_client.set(lock_key, "locked", nx=True, ex=30)
            if not redis_acquired:
                lock_acquired = False
                # Revert local lock if distributed lock caught it first
                async with _local_lock:
                    _local_processing_tx_refs.discard(tx_ref)
        except Exception as e:
            logger.warning(f"⚠️ Redis lock unreachable (proceeding with local lock guard): {e}")

    if not lock_acquired:
        logger.warning(f"⚠️ Duplicate Chapa webhook blocked for TX Ref: {tx_ref}")
        return {"status": "success", "message": "Webhook already processed or in progress"}

    try:
        # Handles signature verification, DB deal state updates, confirmations, and Telegram dispatches safely
        response = await webhook_handler.handle_payment_event(
            request=request, signature=signature
        )

        # On SUCCESS: Schedule the local lock to expire in 30 seconds.
        # DO NOT delete the Redis lock; let it naturally expire so duplicate retries are blocked.
        if tx_ref:
            background_tasks.add_task(release_local_lock_after_ttl, tx_ref, 30)

        return response

    except Exception as e:
        # On FAILURE: Release both locks immediately so the transaction can be safely retried.
        if tx_ref:
            async with _local_lock:
                _local_processing_tx_refs.discard(tx_ref)
            if lock_key and redis_client:
                try:
                    await redis_client.delete(lock_key)
                except Exception:
                    pass

        logger.error(f"Error processing Chapa webhook event: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Webhook processing failed",
        )


@api_router.get("/payment-callback", tags=["Financials"])
async def payment_callback_landing(
        request: Request,
        trx_ref: Optional[str] = Query(None),
        payment_status: Optional[str] = Query(None, alias="status"),
        ref_id: Optional[str] = Query(None),
) -> HTMLResponse:
    """
    Landing page for browser redirection following Chapa payment.
    Triggers direct live API re-verification only if the webhook handler
    has not already finalized the deal/dispatched notifications.
    """
    reference = trx_ref or ref_id
    logger.info(
        f"📥 Payment callback landing hit | Ref: {reference} | Status: {payment_status}"
    )

    is_successful_param = payment_status and str(payment_status).lower() in [
        "success",
        "completed",
        "paid",
    ]

    if is_successful_param and reference:
        wk_handler = getattr(request.app.state, "webhook_handler", None)

        if wk_handler:
            try:
                is_already_finalized = False
                db = getattr(wk_handler, "db", None)

                # --- ATOMIC DB GUARD ALIGNED WITH DEALS TABLE ---
                # Check current status and attempt an atomic lock update using correct schema column 'payment_reference'
                if db and hasattr(db, "table"):
                    deal_res = await db.table("deals").select("id, status, dispatch_sent, payment_status").eq("payment_reference", reference).maybe_single().execute()

                    if deal_res and getattr(deal_res, "data", None):
                        deal_data = deal_res.data
                        if deal_data.get("dispatch_sent") is True or deal_data.get("status") == "finalized":
                            is_already_finalized = True
                        else:
                            # Atomically claim processing rights to block webhook/callback race conditions
                            update_res = await db.table("deals").update({"dispatch_sent": True, "status": "processing"}).eq("id", deal_data["id"]).eq("dispatch_sent", False).execute()
                            if not update_res or not getattr(update_res, "data", None):
                                # Another thread/webhook successfully claimed or finalized it first!
                                is_already_finalized = True

                if hasattr(wk_handler, "is_transaction_finalized") and not is_already_finalized:
                    is_already_finalized = await wk_handler.is_transaction_finalized(tx_ref=reference)

                if is_already_finalized:
                    logger.info(
                        f"⏩ TX Ref {reference} already processed/finalized or locked. Suppressing duplicate fallback dispatch."
                    )
                else:
                    # Execute fallback strictly if no other worker has claimed it
                    await wk_handler.verify_and_confirm_transaction(tx_ref=reference)
                    logger.info(f"✅ Live fallback re-verification succeeded for TX Ref: {reference}")

            except Exception as e:
                logger.warning(
                    f"⚠ Live re-verification check could not confirm TX Ref {reference}: {e}"
                )
                return HTMLResponse(
                    content="""
                    <html>
                        <head>
                            <title>Payment Pending</title>
                            <meta name="viewport" content="width=device-width, initial-scale=1">
                        </head>
                        <body style="font-family: Arial, sans-serif; text-align: center; padding: 50px 20px;">
                            <h2 style="color: #e65100;">⚠️ Payment Pending or Unconfirmed</h2>
                            <p>We could not confirm payment completion with Chapa. If debited, your deal status will update shortly.</p>
                        </body>
                    </html>
                    """,
                    status_code=200,
                )

        return HTMLResponse(
            content="""
            <html>
                <head>
                    <title>Payment Confirmed</title>
                    <meta name="viewport" content="width=device-width, initial-scale=1">
                </head>
                <body style="font-family: Arial, sans-serif; text-align: center; padding: 50px 20px;">
                    <h2 style="color: #2e7d32;">🎉 Payment Successful!</h2>
                    <p>Your payment has been verified and confirmed. You can now close this window and return to the Telegram bot.</p>
                </body>
            </html>
            """,
            status_code=200,
        )

    return HTMLResponse(
        content="""
        <html>
            <head>
                <title>Payment Pending</title>
                <meta name="viewport" content="width=device-width, initial-scale=1">
            </head>
            <body style="font-family: Arial, sans-serif; text-align: center; padding: 50px 20px;">
                <h2 style="color: #e65100;">⚠️ Payment Pending or Cancelled</h2>
                <p>If you completed your payment, your deal status will update shortly. You may return to Telegram.</p>
            </body>
        </html>
        """,
        status_code=200,
    )

# ------------------------------------------------------------------------
# ATTACH ROUTER TO MAIN APP
# ------------------------------------------------------------------------
app.include_router(api_router)


# ==========================================
# 12. MIDDLEWARE & EXCEPTION HANDLERS
# ==========================================
@app.middleware("http")
async def audit_logging_middleware(request: Request, call_next):
    start_time = asyncio.get_event_loop().time()
    path = request.url.path
    method = request.method
    is_health_check = path == "/health"

    if not is_health_check:
        logger.info(
            f"📥 Gateway Request Ingested: {method} {path} | "
            f"Client: {request.client.host if request.client else 'Unknown'}")

    try:
        response = await call_next(request)
        if not is_health_check:
            duration = asyncio.get_event_loop().time() - start_time
            logger.info(
                f"📤 Gateway Request Completed: {method} {path} | "
                f"Status: {response.status_code} | Duration: {duration:.4f}s")
        return response
    except Exception as exc:
        raise exc


@app.exception_handler(HTTPException)
async def custom_http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail, "path": request.url.path})


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error(f"🚨 Global API Error intercepted on path '{request.url.path}': {str(exc)}", exc_info=True)
    if shutdown_event.is_set():
        return JSONResponse(status_code=503, content={"detail": "Server restarting.", "retry_after": 10})
    return JSONResponse(status_code=500,
                        content={"detail": "Internal Server Error.", "reference_path": request.url.path})


# ==========================================
# 13. PROCESS RUNNER & SIGNALS
# ==========================================
def setup_process_signal_handlers(loop: asyncio.AbstractEventLoop) -> None:
    def target_signal_action(sig_enum: signal.Signals):
        if shutdown_event.is_set():
            return
        logger.critical(f"⚠️ Lifecycle Notification: Received {sig_enum.name}. Initiating shutdown...")
        shutdown_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, lambda s=sig: target_signal_action(s))
        except NotImplementedError:
            signal.signal(sig, lambda sig_num, frame: target_signal_action(signal.Signals(sig_num)))


async def run_production_server() -> None:
    logger.info(f"💾 Launching production ASGI listener on port {settings.SERVER_PORT}...")

    config = uvicorn.Config(
        app="src.smartmatch.main:app",
        host=settings.SERVER_HOST,
        port=settings.SERVER_PORT,
        workers=settings.SERVER_WORKERS,
        reload=settings.DEBUG_MODE,
        log_level="info",
        proxy_headers=True,
        forwarded_allow_ips="*",
        interface="asgi3",
        loop="asyncio",
    )
    config.install_signal_handlers = True
    server = uvicorn.Server(config)

    current_loop = asyncio.get_running_loop()
    setup_process_signal_handlers(current_loop)
    await server.serve()


if __name__ == "__main__":
    logger.info("Initializing Smart Match Africa Master Entrypoint...")
    try:
        asyncio.run(run_production_server())
    except KeyboardInterrupt:
        logger.info("\n🛑 System execution stopped manually by administrative keyboard interrupt.")
        sys.exit(0)
    except SystemExit:
        logger.info("System exit signal processed. Master process terminated.")
    except Exception as boot_exception:
        logger.critical(f"❌ Fatal crash during low-level execution startup: {str(boot_exception)}", exc_info=True)
        sys.exit(1)
