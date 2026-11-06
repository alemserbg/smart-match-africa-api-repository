import os
import hmac
import hashlib
import logging
import json
import html
import re
import asyncio
import uuid
import urllib.parse
import httpx
from fastapi import APIRouter, Request, HTTPException
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, Tuple, Union, List, TYPE_CHECKING, Set
import inspect

from telegram import (
    Bot, Update, InlineKeyboardButton,
    InlineKeyboardMarkup, Message
)
from telegram.ext import ContextTypes, ConversationHandler

from src.smartmatch.config import settings
from src.smartmatch.services.reserve__quantity_location import _extract_location_data
from src.smartmatch.services.reserve__quantity_location import ReservationHelper
from src.smartmatch.services.reserve__quantity_location import LocationService
from src.smartmatch.utils.translations import get_text
from src.smartmatch.handlers.main_menu import MainMenuHandler

if TYPE_CHECKING:
    from src.smartmatch.services.billing_service import BillingService
    from src.smartmatch.services.db_manager import DatabaseManager
    from src.smartmatch.utils.queue_manager import QueueManager, TaskPriority

# Setup production structured logging with explicit system isolation
logger = logging.getLogger("smartmatch.webhook")

# Sentinels used to filter out raw database/user placeholders
INVALID_SENTINELS: Set[str] = {
    "none", "null", "n/a", "na", "undefined", "not specified", "not_specified",
    "unspecified", "hin ibsamne", "አልተገለጸም", "አልተጠቀሰም", "noe", ""
}

INVALID_NUMERIC_SENTINELS: Set[str] = {
    "0", "0.0", "0.0kg", "0kg", "0.0 kg", "0 kg", "none", "null", "none kg", "null kg",
    "0 people", "0 person"
}


# Error messages that indicate further retries will also fail
FATAL_TELEGRAM_ERRORS = (
    "bot was blocked by the user",
    "chat not found",
    "user is deactivated",
    "have no rights to send a message",
)


# ==========================================
# HELPER FUNCTIONS
# ==========================================
def _is_valid_uuid(val: Any) -> bool:
    """
    Checks if a given value is a valid UUID string or UUID object.
    Prevents PostgreSQL syntax errors when passing raw Telegram IDs or invalid strings to UUID queries.
    """
    if not val:
        return False
    try:
        uuid_obj = uuid.UUID(str(val).strip())
        return str(uuid_obj) == str(val).strip().lower()
    except (ValueError, TypeError, AttributeError):
        return False


def _normalize_lang_code(lang: Optional[str]) -> str:
    """
    Normalizes raw language inputs to standard language codes ('en', 'am', 'om').
    Defaults to 'en' if invalid or missing.
    """
    if not lang:
        return "en"
    lng = str(lang).lower().strip()
    if any(code in lng for code in ["om", "oromo", "afaan", "or"]):
        return "om"
    if any(code in lng for code in ["am", "amh", "amharic"]):
        return "am"
    if any(code in lng for code in ["en", "eng", "english"]):
        return "en"
    return "en"


def _build_google_maps_link(
        lat: Any,
        lon: Any,
        location_text: Optional[str] = None,
        operating_region: Optional[str] = None,
        country: Optional[str] = None  # ✅ Removed hardcoded "Ethiopia" default
) -> Optional[str]:
    """
    Constructs a valid Google Maps search/directions URL given coordinates or location text.
    Guarantees 'https://www.google.com/maps?q=...' format to prevent 404 errors in Telegram webviews.
    """
    try:
        if lat is not None and lon is not None:
            f_lat, f_lon = float(lat), float(lon)
            if (f_lat != 0.0 or f_lon != 0.0) and -90.0 <= f_lat <= 90.0 and -180.0 <= f_lon <= 180.0:
                return f"https://www.google.com/maps?q={f_lat:.6f},{f_lon:.6f}"
    except (ValueError, TypeError):
        pass

    if location_text and str(location_text).strip():
        clean_text = str(location_text).strip()
        if clean_text.lower() not in INVALID_SENTINELS:
            clean_text = re.sub(r"^(?:Fe'umsaa|ጭነት|Freight)\s*-\s*", "", clean_text, flags=re.IGNORECASE).strip()

            query_parts = [clean_text]

            if operating_region and str(operating_region).strip():
                clean_region = str(operating_region).strip()
                if clean_region.lower() not in INVALID_SENTINELS and clean_region.lower() not in clean_text.lower():
                    query_parts.append(clean_region)

            # ✅ Only append country if explicitly provided and not already present in the location text
            if country and str(country).strip():
                clean_country = str(country).strip()
                if clean_country.lower() not in INVALID_SENTINELS and clean_country.lower() not in clean_text.lower():
                    query_parts.append(clean_country)

            search_query = ", ".join(query_parts)
            encoded = urllib.parse.quote_plus(search_query)
            return f"https://www.google.com/maps?q={encoded}"

    return None


def _clean_markdown_syntax(text: str) -> str:
    """
    Converts raw Markdown remnants (*bold*, `code`, _italic_) into valid Telegram HTML
    to prevent unparsed symbols leaking into client UI.
    """
    if not text:
        return ""
    text = re.sub(r'`([^`]+)`', r'<code>\1</code>', text)
    text = re.sub(r'\*([^*]+)\*', r'<b>\1</b>', text)
    text = re.sub(r'_([^_]+)_', r'<i>\1</i>', text)
    return text


def _strip_html_tags(text: str) -> str:
    """
    Strips HTML tags and unescapes entities for safe plain text Telegram fallback delivery.
    Guarantees Telegram will accept text messages even if HTML formatting is malformed.
    """
    if not text:
        return ""
    clean = re.sub(r'<[^>]+>', '', text)
    return html.unescape(clean)


def _safe_get_text(lang: str, key: str, default: Optional[str] = None, **kwargs) -> str:
    """
    Safely retrieves localized text without throwing KeyError or ValueError on missing placeholders.
    """
    try:
        res = get_text(lang, key, **kwargs)
        if res and str(res).strip() and not str(res).startswith("MISSING_TRANSLATION"):
            return res
    except Exception as e:
        logger.warning(f"get_text formatted call failed for key '{key}' in lang '{lang}': {e}")

    try:
        res = get_text(lang, key)
        if res and str(res).strip() and not str(res).startswith("MISSING_TRANSLATION"):
            for k, v in kwargs.items():
                res = res.replace(f"{{{k}}}", str(v if v is not None else ""))
            return res
    except Exception:
        pass

    return default or ""


def _extract_field(sources: list, keys: list, default: Optional[str] = None) -> Optional[str]:
    """Helper to extract non-empty, non-placeholder values across multiple source dicts and key aliases."""
    for src in sources:
        if not isinstance(src, dict):
            continue
        for k in keys:
            val = src.get(k)
            if val is not None:
                s_val = str(val).strip()
                if (
                        s_val
                        and s_val.lower() not in INVALID_SENTINELS
                        and not s_val.lower().startswith("not_specified")
                        and not s_val.lower().startswith("not specified")
                ):
                    return s_val
    return default


def _clean_asset_title_language_mixup(title: Optional[str]) -> str:
    """Removes hardcoded inline Oromoo/English/Amharic label artifacts from raw saved titles strictly."""
    if not title:
        return ""
    # Strip explicit category prefixes across languages
    cleaned = re.sub(r"^(?:Fe'umsaa|Fe'umsa|ጭነት|Freight|Transport|Ride)\s*[:\-–—]\s*", "", str(title), flags=re.IGNORECASE)
    cleaned = re.sub(r"(?:Fe'umsaa|Fe'umsa|ጭነት|Freight)\s*-\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\((?:Gabatee|ሰሌዳ|Plate):[^)]+\)", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"📍\s*(?:Karaa|መስመር|Route|Location|Bakka|ቦታ):[^\n]+", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"⚖️\s*(?:Ulfaatina|ክብደት|Weight):[^\n]+", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"👥\s*(?:Baay'ina Namaa|የሰው ብዛት|Party Size):[^\n]+", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


def _format_weight(val: Any) -> str:
    """Formats cargo weight with thousand separators (e.g., 2900 or 2900.0 -> 2,900 kg)."""
    if val is None:
        return ""
    s = str(val).strip()
    if not s or s.lower() in INVALID_SENTINELS or s.lower() in INVALID_NUMERIC_SENTINELS:
        return ""

    unit_match = re.search(r"(kg|kgs|ton|tons|quintal|t)\b", s, re.IGNORECASE)
    unit_str = unit_match.group(1) if unit_match else "kg"

    num_match = re.search(r"([\d,.]+)", s)
    if not num_match:
        return s if s.lower() not in INVALID_SENTINELS else ""

    clean_num_str = num_match.group(1).replace(",", "")
    try:
        num = float(clean_num_str)
        if num <= 0:
            return ""
        if num.is_integer():
            formatted_num = f"{int(num):,}"
        else:
            formatted_num = f"{num:,.1f}".rstrip('0').rstrip('.')
        return f"{formatted_num} {unit_str}"
    except (ValueError, TypeError):
        return s if s.lower() not in INVALID_SENTINELS else ""


def _format_party_size(val: Any, lang: str = "en") -> str:
    """Formats party size (e.g., 4 -> 4 People / 4 ሰው / 4 Nama)."""
    if val is None:
        return ""
    s = str(val).strip()
    if not s or s.lower() in INVALID_SENTINELS or s.lower() in INVALID_NUMERIC_SENTINELS:
        return ""

    num_match = re.search(r"(\d+)", s)
    if not num_match:
        return s if s.lower() not in INVALID_SENTINELS else ""

    try:
        num = int(num_match.group(1))
        if num <= 0:
            return ""
        person_unit = "ሰው" if lang == "am" else "Nama" if lang == "om" else ("Person" if num == 1 else "People")
        return f"{num:,} {person_unit}"
    except (ValueError, TypeError):
        return s if s.lower() not in INVALID_SENTINELS else ""


def _safe_parse_float(val: Any, default: float = 0.0) -> float:
    """Safely parses a price or numeric value, handling strings like 'Negotiable', 'Free', or None."""
    if val is None:
        return default
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).replace(",", "").strip()
    if not s or s.lower() in ["negotiable", "free", "none", "null", "n/a", "tbd"]:
        return default
    num_match = re.search(r"([\d.]+)", s)
    if num_match:
        try:
            return float(num_match.group(1))
        except (ValueError, TypeError):
            pass
    return default


# ==========================================
# DISPATCH SERVICE
# ==========================================

class DispatchService:
    """
    Service responsible for post-payment notification dispatch and mutual contact delivery.
    Supports both standard forward matches and reverse matches with full multilingual localization.
    Includes atomic double-dispatch prevention, multi-tier delivery fallbacks, safe user UUID
    resolution, vehicle fuel-type inclusion, and aligned Google Maps link formatting.
    """
    db: Any
    telegram_app: Any
    bot: Any
    storage: Any
    image_service: Any

    def _get_bot_instance(self) -> Optional[Any]:
        """
        Robustly resolves the Telegram Bot instance across `self.bot`, `self.telegram_app.bot`,
        direct `telegram_app` instances, or instantiates a direct Bot from settings/env.
        Prevents 'Bot Instance is invalid' errors in asynchronous worker threads.
        """
        bot = getattr(self, "bot", None)
        if not bot and hasattr(self, "telegram_app") and self.telegram_app:
            if hasattr(self.telegram_app, "bot"):
                bot = self.telegram_app.bot
            elif hasattr(self.telegram_app, "send_message"):
                bot = self.telegram_app

        if not bot:
            try:
                bot_token = (
                        getattr(settings, "TELEGRAM_BOT_TOKEN", None) or
                        getattr(settings, "TELEGRAM_TOKEN", None) or
                        os.getenv("TELEGRAM_BOT_TOKEN") or
                        os.getenv("TELEGRAM_TOKEN")
                )
                if bot_token:
                    bot = Bot(token=bot_token)
                    self.bot = bot
            except Exception as token_err:
                logger.warning(f"Failed to instantiate fallback Telegram Bot instance from token: {token_err}")

        if not bot:
            logger.warning("⚠️️ Telegram Bot instance could not be resolved from self.telegram_app, self.bot, or token.")
        return bot

    @staticmethod
    async def _resolve_chat_id(supabase_client: Any, raw_id: Union[int, str, None]) -> Optional[int]:
        """
        Safely resolves an integer Telegram Chat ID from raw IDs, string numbers, or Supabase User UUIDs.
        """
        if not raw_id or supabase_client is None:
            return None
        try:
            return int(raw_id)
        except (ValueError, TypeError):
            if _is_valid_uuid(raw_id):
                try:
                    user_res = (
                        await supabase_client.table("users")
                        .select("telegram_id")
                        .eq("id", str(raw_id))
                        .maybe_single()
                        .execute()
                    )

                    if user_res and hasattr(user_res, 'data') and user_res.data and user_res.data.get("telegram_id"):
                        return int(user_res.data.get("telegram_id"))
                except Exception as uuid_err:
                    logger.debug(f"Failed to resolve Telegram ID via UUID '{raw_id}': {uuid_err}")

            try:
                user_res = await supabase_client.table("users").select("telegram_id").eq("telegram_id",
                                                                                         raw_id).maybe_single().execute()
                if user_res and hasattr(user_res, 'data') and user_res.data and user_res.data.get("telegram_id"):
                    return int(user_res.data.get("telegram_id"))
            except Exception as id_err:
                logger.warning(f"Failed to resolve Telegram ID for user '{raw_id}': {id_err}")
        return None

    async def _safe_send_telegram_notification(
            self,
            target_id: Optional[int],
            message_text: str,
            presigned_url: Optional[str] = None,
            reply_markup: Optional[Any] = None,
            role: str = "User"
    ) -> bool:
        """
        Bulletproof notification delivery to Telegram.
        Tries:
        1. Photo with HTML caption + Reply keyboard
        2. Photo with Plain text caption + Reply keyboard
        3. Text message + HTML + Reply keyboard
        4. Text message + Plain text + Reply keyboard
        5. Text message + Plain text (no keyboard markup)
        """
        bot_instance = self._get_bot_instance()
        if not target_id or not bot_instance:
            logger.error(f"Skipping dispatch to {role}: Target Chat ID ('{target_id}') or Bot Instance is invalid.")
            return False

        clean_plain_text = _strip_html_tags(message_text)

        # Tier 1 & 2: Presigned Photo Delivery
        if presigned_url:
            try:
                await bot_instance.send_photo(
                    chat_id=target_id,
                    photo=presigned_url,
                    caption=message_text,
                    reply_markup=reply_markup,
                    parse_mode="HTML"
                )
                logger.info(f"Payment confirmation photo dispatched successfully to {role} (Chat ID: {target_id}).")
                return True
            except Exception as photo_html_err:
                logger.warning(
                    f"Failed photo HTML dispatch to {role} ({target_id}): {photo_html_err}. Retrying photo plain text...")

            try:
                await bot_instance.send_photo(
                    chat_id=target_id,
                    photo=presigned_url,
                    caption=clean_plain_text,
                    reply_markup=reply_markup,
                    parse_mode=None
                )
                logger.info(
                    f"Payment confirmation photo (plain text) dispatched successfully to {role} (Chat ID: {target_id}).")
                return True
            except Exception as photo_plain_err:
                logger.warning(
                    f"Failed photo plain text dispatch to {role} ({target_id}): {photo_plain_err}. Falling back to text message...")

        # Tier 3: Text + HTML + Keyboard
        try:
            await bot_instance.send_message(
                chat_id=target_id,
                text=message_text,
                reply_markup=reply_markup,
                parse_mode="HTML"
            )
            logger.info(f"Payment confirmation text dispatched successfully to {role} (Chat ID: {target_id}).")
            return True
        except Exception as text_html_err:
            logger.warning(
                f"Failed text HTML dispatch to {role} ({target_id}): {text_html_err}. Retrying plain text...")

        # Tier 4: Text + Plain Text + Keyboard
        try:
            await bot_instance.send_message(
                chat_id=target_id,
                text=clean_plain_text,
                reply_markup=reply_markup,
                parse_mode=None
            )
            logger.info(f"Payment confirmation text (plain) dispatched successfully to {role} (Chat ID: {target_id}).")
            return True
        except Exception as text_plain_err:
            logger.warning(
                f"Failed text plain dispatch to {role} ({target_id}): {text_plain_err}. Retrying without reply markup...")

        # Tier 5: Text + Plain Text (no Keyboard)
        try:
            await bot_instance.send_message(
                chat_id=target_id,
                text=clean_plain_text,
                parse_mode=None
            )
            logger.info(
                f"Payment confirmation text (plain, no markup) dispatched successfully to {role} (Chat ID: {target_id}).")
            return True
        except Exception as final_err:
            logger.error(f"CRITICAL: All dispatch attempts failed for {role} (Chat ID: {target_id}): {final_err}")

        return False

    async def handle_dispatch_failure(self, deal_id: str, chat_id: int, payload: dict, error_msg: str) -> None:
        """
        Persists failed dispatches into a database retry table for background worker pickup.
        """
        logger.error(f"Persisting failed dispatch for deal {deal_id} to chat {chat_id} due to: {error_msg}")

        retry_payload = {
            "deal_id": deal_id,
            "chat_id": chat_id,
            "payload": payload,
            "attempt_count": 1,
            "status": "pending_retry",
            "next_retry_at": datetime.utcnow() + timedelta(seconds=60)  # Exponential backoff
        }

        # Safely resolve db and supabase clients
        db_client = getattr(self, "db", None)
        supabase_client = getattr(db_client, "supabase", db_client) if db_client else None

        # Guard against boolean/None inference and ensure the table method exists
        if supabase_client and not isinstance(supabase_client, bool) and hasattr(supabase_client, "table"):
            await supabase_client.table("notification_retries").insert(retry_payload).execute()
        else:
            logger.error(
                f"Failed to insert retry payload for deal {deal_id}: Database client 'self.db' or 'supabase' is uninitialized or invalid.")

    async def send_dispatch_notification(self, *args, **kwargs) -> None:
        """Public alias for _send_dispatch_notification for backward compatibility."""
        await self._send_dispatch_notification(*args, **kwargs)

    async def dispatch_deal_notifications(self, *args, **kwargs) -> None:
        """Public alias for _send_dispatch_notification for backward compatibility."""
        await self._send_dispatch_notification(*args, **kwargs)

    async def _send_dispatch_notification(
            self,
            deal_id: str,
            seeker_id: Union[int, str],
            provider_id: Union[int, str],
            seeker_phone: str,
            provider_phone: str,
            asset_title: Optional[str] = None,
            payment_reference: Optional[str] = None
    ) -> None:
        """
        Executes cross-matched phone data delivery natively to both client apps.
        Fully de-duplicated final dispatch layout with multilingual support (EN, AM, OM)
        for both forward and reverse matching protocols. Correctly aligns map location links
        and explicitly includes seeker pick-up location for provider dispatch in freight/ride verticals.
        """
        supabase_client: Any = getattr(self.db, 'supabase', getattr(self, 'db', None))

        if supabase_client is None:
            logger.error("Supabase client is not available for dispatch notification.")
            return

        # 1. Atomic Check-and-Lock via Single Conditional Update (Eliminates Race Conditions)
        try:
            atomic_res = await supabase_client.table("deals").update({
                "dispatch_sent": True,
                "updated_at": datetime.now(timezone.utc).isoformat()
            }).eq("id", deal_id).eq("dispatch_sent", False).execute()

            # If no rows were updated, another concurrent thread already claimed/dispatched this deal
            if not atomic_res or not atomic_res.data:
                logger.warning(
                    f"Notification dispatch skipped: Deal {deal_id} was already locked or processed by a concurrent thread."
                )
                return

            logger.info(
                f"🔒 Atomically locked and marked dispatch_sent=True for deal {deal_id} prior to network dispatch.")
        except Exception as lock_err:
            logger.error(f"Failed to execute atomic dispatch lock for deal {deal_id}: {lock_err}")
            return

        # 2. Resolve User Chat IDs
        seeker_chat_id = await self._resolve_chat_id(supabase_client, seeker_id)
        provider_chat_id = await self._resolve_chat_id(supabase_client, provider_id)

        if not seeker_chat_id or not provider_chat_id:
            logger.error(
                f"Cannot dispatch notifications for deal {deal_id}: "
                f"Unresolvable chat IDs (Seeker Chat ID: '{seeker_chat_id}', Provider Chat ID: '{provider_chat_id}')."
            )
            try:
                # Rollback lock only if chat resolution completely fails before sending
                await supabase_client.table("deals").update({"dispatch_sent": False}).eq("id", deal_id).execute()
            except Exception:
                pass
            return

        async def _get_user_lang(
                user_identifier: Union[int, str, None],
                client: Optional[Any] = None
        ) -> str:
            """Safely retrieves user language preference ('en', 'am', 'om')."""
            if not user_identifier:
                return "en"

            clean_id = str(user_identifier).strip()
            if not clean_id or clean_id.lower() == "none":
                return "en"

            active_client: Any = client or getattr(self.db, 'supabase', getattr(self, 'db', None))
            if not active_client or isinstance(active_client, bool):
                return "en"

            def extract_from_dict(d: Any) -> Optional[str]:
                if not isinstance(d, dict):
                    return None
                for lang_key in ["preferred_language", "language", "language_code", "lang", "user_lang"]:
                    lang_val = d.get(lang_key)
                    norm = _normalize_lang_code(lang_val)
                    if norm:
                        return norm
                return None

            if clean_id.isdigit():
                try:
                    res = await active_client.table("users").select("*").eq("telegram_id",
                                                                            int(clean_id)).maybe_single().execute()
                    if res and getattr(res, "data", None):
                        detected = extract_from_dict(res.data)
                        if detected:
                            return detected
                except Exception as errs:
                    logger.debug(f"users select(*) by numeric telegram_id failed for {clean_id}: {errs}")

            if _is_valid_uuid(clean_id):
                try:
                    res = await active_client.table("users").select("*").eq("id", clean_id).maybe_single().execute()
                    if res and getattr(res, "data", None):
                        detected = extract_from_dict(res.data)
                        if detected:
                            return detected
                except Exception as errs:
                    logger.debug(f"users select(*) by UUID id failed for {clean_id}: {errs}")

            try:
                res = await active_client.table("users").select("*").eq("telegram_id",
                                                                        clean_id).maybe_single().execute()
                if res and getattr(res, "data", None):
                    detected = extract_from_dict(res.data)
                    if detected:
                        return detected
            except Exception as errs:
                logger.debug(f"users select(*) by str telegram_id failed for {clean_id}: {errs}")

            return "en"

        def _is_generic_title(title: Optional[str], vertical: Optional[str] = None) -> bool:
            if not title:
                return True
            clean = str(title).strip().lower()
            generic_words = {
                "residential", "commercial", "housing", "vehicle", "freight",
                "ride", "railed", "equipment", "asset", "requested asset", "none", "machinery",
                "agriculture", "farming", "construction", "generators", "power",
                "generators & power", "eventtech", "industrial", "tools"
            }
            if vertical and str(vertical).strip().lower() == clean:
                return True
            return clean in generic_words or len(clean.split()) < 2

        def _normalize_asset_title(title: Optional[str]) -> str:
            if not title:
                return ""
            t = str(title).strip()
            t = re.sub(r'\b[fF][sS][rR]\b', 'FSR', t)
            words = t.split()
            normalized = []
            for w in words:
                w_lower = w.lower()
                if w_lower in ['fsr', 'suv', 'ev', 'abs', 'awd', '4wd']:
                    normalized.append(w.upper())
                else:
                    normalized.append(w)
            return " ".join(normalized)

        # Initialize state variables
        vertical_type: Optional[str] = None
        listing: Optional[Dict[str, Any]] = None
        listing_id: Optional[str] = None
        deal_data: Dict[str, Any] = {}

        # 1. Fetch deal baseline
        try:
            deal_res = await supabase_client.table("deals") \
                .select("*") \
                .eq("id", deal_id) \
                .maybe_single() \
                .execute()

            if deal_res and getattr(deal_res, 'data', None):
                deal_data = deal_res.data
                listing_id = deal_data.get("listing_id")
                vertical_type = deal_data.get("vertical_type")
        except Exception as db_err:
            logger.error(f"Error fetching deal baseline for dispatch (Deal {deal_id}): {db_err}")

        # 2. Comprehensive Seeker Request Fetching across all key schemas
        seeker_request_data: Dict[str, Any] = {}
        try:
            req_id = (
                    deal_data.get("request_id")
                    or deal_data.get("seeker_request_id")
                    or deal_data.get("req_id")
            )
            if req_id:
                try:
                    req_res = await supabase_client.table("seeker_requests") \
                        .select("*") \
                        .eq("id", req_id) \
                        .maybe_single() \
                        .execute()
                    if req_res and getattr(req_res, 'data', None):
                        seeker_request_data = req_res.data
                except Exception as err:
                    logger.debug(f"Direct req_id lookup skipped/failed: {err}")

            if not seeker_request_data and seeker_id:
                clean_seeker_id = str(seeker_id).strip()

                # Primary schema lookup: seeker_id
                if clean_seeker_id.isdigit():
                    try:
                        req_res = await supabase_client.table("seeker_requests") \
                            .select("*") \
                            .eq("seeker_id", int(clean_seeker_id)) \
                            .order("created_at", desc=True) \
                            .limit(1) \
                            .execute()
                        if req_res and getattr(req_res, 'data', None) and isinstance(req_res.data, list) and len(
                                req_res.data) > 0:
                            seeker_request_data = req_res.data[0]
                    except Exception as e0:
                        logger.debug(f"Column 'seeker_id' lookup skipped: {e0}")

                # Secondary schema lookups: telegram_id / user_id
                if not seeker_request_data and clean_seeker_id.isdigit():
                    try:
                        req_res = await supabase_client.table("seeker_requests") \
                            .select("*") \
                            .eq("telegram_id", int(clean_seeker_id)) \
                            .order("created_at", desc=True) \
                            .limit(1) \
                            .execute()
                        if req_res and getattr(req_res, 'data', None) and isinstance(req_res.data, list) and len(
                                req_res.data) > 0:
                            seeker_request_data = req_res.data[0]
                    except Exception as e1:
                        logger.debug(f"Column 'telegram_id' lookup skipped: {e1}")

                if not seeker_request_data and _is_valid_uuid(clean_seeker_id):
                    try:
                        req_res = await supabase_client.table("seeker_requests") \
                            .select("*") \
                            .eq("user_id", clean_seeker_id) \
                            .order("created_at", desc=True) \
                            .limit(1) \
                            .execute()
                        if req_res and getattr(req_res, 'data', None) and isinstance(req_res.data, list) and len(
                                req_res.data) > 0:
                            seeker_request_data = req_res.data[0]
                    except Exception as e2:
                        logger.debug(f"Column 'user_id' lookup skipped: {e2}")

        except Exception as req_fetch_err:
            logger.warning(f"Failed to fetch seeker_requests for dispatch: {req_fetch_err}")

        # 3. Reverse match & Vertical evaluation
        is_reverse_match = (
                str(deal_data.get("match_type", "")).lower() == "reverse" or
                bool(deal_data.get("is_reverse_match")) or
                str(deal_data.get("deal_type", "")).lower() == "reverse_match"
        )

        raw_base_asset_title = ""

        # 4. Dynamically resolve asset details from provider_listings
        if listing_id:
            try:
                listing_res = await supabase_client.table("provider_listings") \
                    .select("*") \
                    .eq("id", listing_id) \
                    .maybe_single() \
                    .execute()

                if listing_res and getattr(listing_res, 'data', None):
                    listing = listing_res.data

                    raw_deal_title = deal_data.get("asset_title") or asset_title or ""
                    raw_deal_title = _clean_asset_title_language_mixup(raw_deal_title)

                    clean_title = re.sub(r'\s*\(.*?\)', '', str(raw_deal_title)).strip()
                    clean_title = re.sub(r'\s*-\s*.*$', '', clean_title).strip()

                    if _is_generic_title(clean_title, vertical_type):
                        clean_title = ""

                    primary_descriptor = (
                            clean_title or
                            listing.get("make_model_year") or
                            listing.get("equipment_type") or
                            listing.get("title") or
                            listing.get("name") or
                            listing.get("model") or
                            listing.get("make")
                    )

                    location = _extract_field(
                        [deal_data, listing],
                        ["location_text", "location", "pickup_location", "origin"]
                    ) or ""

                    trans_val = (
                                    deal_data.get("deal_type") if not is_reverse_match else None
                                ) or listing.get("transaction_type") or listing.get("listing_type")

                    trans_str = f" ({str(trans_val).title()})" if trans_val and not is_reverse_match else ""
                    loc_str = f" - {location}" if location and location not in str(primary_descriptor) else ""

                    if primary_descriptor:
                        raw_base_asset_title = f"{primary_descriptor}{trans_str}{loc_str}"

            except Exception as e:
                logger.error(f"Error dynamically fetching asset details for dispatch: {e}")

        # 5. Fetch user language preferences
        seeker_lang = await _get_user_lang(seeker_id, supabase_client)
        provider_lang = await _get_user_lang(provider_id, supabase_client)

        TRANSACTION_TRANSLATIONS = {
            "am": {
                "rental": "ኪራይ", "rent": "ኪራይ", "sale": "ሽያጭ", "sell": "ሽያጭ",
                "lease": "ሊዝ", "buy": "ግዢ", "service": "አገልግሎት",
                "booking": "ቦታ ማስያዝ", "reverse_match": "የተገላቢጦሽ ግጥሚያ"
            },
            "om": {
                "rental": "Kiraa", "rent": "Kiraa", "sale": "Gurgurtaa", "sell": "Gurgurtaa",
                "lease": "Liizii", "buy": "Bituu", "service": "Tajaajila",
                "booking": "Qabiinsa", "reverse_match": "Walitti-dhufeenya Garagalfamaa"
            },
            "en": {
                "rental": "Rental", "rent": "Rental", "sale": "Sale", "sell": "Sale",
                "lease": "Lease", "buy": "Purchase", "service": "Service",
                "booking": "Booking", "reverse_match": "Reverse Match"
            }
        }

        VERTICAL_TRANSLATIONS = {
            "am": {
                "vehicle": "ተሽከርካሪ", "vehicles": "ተሽከርካሪ", "freight": "ጭነት",
                "ride": "ትራንስፖርት", "rides": "ትራንስፖርት", "railed": "ባቡር",
                "equipment": "መሳሪያ", "machinery": "ማሽነሪ", "housing": "መኖሪያ / ህንፃ",
                "house": "ቤት", "residential": "መኖሪያ", "commercial": "ንግድ",
                "general asset": "ጠቅላላ ንብረት"
            },
            "om": {
                "vehicle": "Konkolaataa", "vehicles": "Konkolaataa", "freight": "Fe'umsaa",
                "ride": "Geejjiba", "rides": "Geejjiba", "railed": "Meraa / Baabura",
                "equipment": "Meeshaa", "machinery": "Maashinariidha", "housing": "Manneen",
                "house": "Mana", "residential": "Jireenyaa", "commercial": "Daldalaa",
                "general asset": "Qabeenya Waligalaa"
            },
            "en": {
                "vehicle": "Vehicle", "vehicles": "Vehicle", "freight": "Freight",
                "ride": "Ride", "rides": "Ride", "railed": "Railed",
                "equipment": "Equipment", "machinery": "Machinery", "housing": "Housing",
                "house": "House", "residential": "Residential", "commercial": "Commercial",
                "general asset": "General Asset"
            }
        }

        VEHICLE_TYPE_TRANSLATIONS = {
            "am": {
                "sedan": "ሴዳን", "suv": "ኤስ.ዩ.ቪ", "pickup": "ፒካፕ",
                "truck": "የጭነት መኪና", "bus": "አውቶቡስ", "minibus": "ሚኒባስ",
                "van": "ቫን", "hatchback": "ሃችባክ", "freight": "የጭነት መኪና", "cargo": "የጭነት መኪና"
            },
            "om": {
                "sedan": "Sedaanii", "suv": "SUV", "pickup": "Piikaappii",
                "truck": "Makkina Fe'umsaa", "bus": "Awtobusiidha", "minibus": "Miinii Baasii",
                "van": "Vaanii", "hatchback": "Haachbaakkii", "freight": "Makkina Fe'umsaa", "cargo": "Makkina Fe'umsaa"
            },
            "en": {
                "sedan": "Sedan", "suv": "SUV", "pickup": "Pickup",
                "truck": "Truck", "bus": "Bus", "minibus": "Minibus",
                "van": "Van", "hatchback": "Hatchback", "freight": "Freight", "cargo": "Cargo"
            }
        }

        FUEL_TRANSLATIONS = {
            "am": {
                "benzine": "ቤንዚን", "petrol": "ቤንዚን", "diesel": "ዲዝል (ናፍጣ)",
                "electric": "ኤሌክትሪክ", "lng_ngv": "ኤል.ኤን.ጂ / የተፈጥሮ ጋዝ (NGV)",
                "lng": "ኤል.ኤን.ጂ / የተፈጥሮ ጋዝ (NGV)", "ngv": "ኤል.ኤን.ጂ / የተፈጥሮ ጋዝ (NGV)",
                "hybrid": "ሃይብሪድ", "hybrid_petrol": "ቤንዚን-ኤሌክትሪክ ሃይብሪድ",
                "hybrid_diesel": "ዲዝል-ኤሌክትሪክ ሃይብሪድ", "hybrid_flex": "ፍሌክስ-ፉኤል (ኢታኖል) ሃይብሪድ",
                "hybrid_gaseous": "ጋዝ-ኤሌክትሪክ (Bi-Fuel) ሃይብሪድ", "hybrid_hydrogen": "ሃይድሮጅን ፉኤል ሴል ሃይብሪድ",
            },
            "om": {
                "benzine": "Beenzinii (Beenzila)", "petrol": "Beenzinii (Beenzila)",
                "diesel": "Dizilii (Naafxaa)", "electric": "Elektiriika",
                "lng_ngv": "LNG / Gaazii Uumamaa (NGV)", "lng": "LNG / Gaazii Uumamaa (NGV)",
                "ngv": "LNG / Gaazii Uumamaa (NGV)", "hybrid": "Haaybiriidii",
                "hybrid_petrol": "Beenzinii-Elektiriika Haaybiriidii",
                "hybrid_diesel": "Dizilii-Elektiriika Haaybiriidii",
                "hybrid_flex": "Flex-Fuel (Etaanoolii) Haaybiriidii",
                "hybrid_gaseous": "Gaazii-Elektiriika Haaybiriidii",
                "hybrid_hydrogen": "Haayidroojiinii Haaybiriidii",
            },
            "en": {
                "benzine": "Benzine (Petrol)", "petrol": "Benzine (Petrol)", "diesel": "Diesel",
                "electric": "Electric", "lng_ngv": "LNG / NGV (Natural Gas)",
                "lng": "LNG / NGV (Natural Gas)", "ngv": "LNG / NGV (Natural Gas)",
                "hybrid": "Hybrid", "hybrid_petrol": "Petrol-Electric Hybrid",
                "hybrid_diesel": "Diesel-Electric Hybrid", "hybrid_flex": "Flex-Fuel (Ethanol) Hybrid",
                "hybrid_gaseous": "Bi-Fuel (Gas-Electric) Hybrid", "hybrid_hydrogen": "Hydrogen Fuel Cell Hybrid",
            }
        }

        DEFAULT_ASSET_NAMES = {
            "en": "Matched Asset Listing",
            "am": "የተጣጣመ ንብረት",
            "om": "Qabeenya Wal-gitaa"
        }

        MAP_LINK_LABELS = {
            "am": {
                "provider": "የአቅራቢውን ቦታ በ Google Maps ይክፈቱ",
                "seeker": "የፈላጊውን (የመጫኛ) ቦታ በ Google Maps ይክፈቱ"
            },
            "om": {
                "provider": "Bakka Dhiheessaa Google Maps irratti banadhu",
                "seeker": "Bakka Barbaadaa (Bakka Fe'umsaa) Google Maps irratti banadhu"
            },
            "en": {
                "provider": "Open Provider Location in Google Maps",
                "seeker": "Open Seeker (Pickup) Location in Google Maps"
            }
        }

        PRICE_LABELS = {
            "am": "የተስማሙበት ዋጋ",
            "om": "Gatiin Waliigaltee",
            "en": "Agreed Price"
        }

        # Resolve localized vertical name
        v_key = str(vertical_type or "general asset").strip().lower()
        vertical_name_en = str(vertical_type or "General Asset").replace("_", " ").title()
        display_vertical_name_seeker = VERTICAL_TRANSLATIONS.get(seeker_lang, VERTICAL_TRANSLATIONS["en"]).get(
            v_key, vertical_name_en
        )
        display_vertical_name_provider = VERTICAL_TRANSLATIONS.get(provider_lang, VERTICAL_TRANSLATIONS["en"]).get(
            v_key, vertical_name_en
        )

        if listing and str(vertical_type).lower() in ["equipment", "machinery"]:
            sub_category = listing.get("category") or listing.get("property_use") or listing.get("property_type")
            equip_val = listing.get("equipment_type")
            sub_val = str(sub_category or equip_val or "").strip()
            if sub_val:
                eq_label_s = "መሳሪያ" if seeker_lang == "am" else "Meeshaa" if seeker_lang == "om" else "Equipment"
                eq_label_p = "መሳሪያ" if provider_lang == "am" else "Meeshaa" if provider_lang == "om" else "Equipment"
                display_vertical_name_seeker = f"{eq_label_s} ({sub_val.title()})"
                display_vertical_name_provider = f"{eq_label_p} ({sub_val.title()})"

        # Resolve Asset Titles independently per recipient language
        def resolve_recipient_asset_title(raw_title: str, target_lang: str) -> str:
            if not raw_title or _is_generic_title(raw_title, vertical_type):
                return DEFAULT_ASSET_NAMES.get(target_lang, DEFAULT_ASSET_NAMES["en"])

            cleaned_t = re.sub(r'\s*\(Plate:[^)]+\)', '', raw_title).strip()
            cleaned_t = _clean_asset_title_language_mixup(cleaned_t)
            return _normalize_asset_title(cleaned_t)

        seeker_asset_title = resolve_recipient_asset_title(raw_base_asset_title, seeker_lang)
        provider_asset_title = resolve_recipient_asset_title(raw_base_asset_title, provider_lang)

        # Image Extraction
        photo_ref: Optional[str] = None
        if listing:
            def _extract_url_or_id(item: Any) -> Optional[str]:
                if not item:
                    return None
                if isinstance(item, str):
                    cleaned = item.strip()
                    if cleaned.startswith("{") and cleaned.endswith("}"):
                        try:
                            parsed_json = json.loads(cleaned)
                            if isinstance(parsed_json, dict):
                                return parsed_json.get("url") or parsed_json.get("file_id") or parsed_json.get("path")
                        except Exception:
                            pass
                    return cleaned if cleaned else None
                if isinstance(item, dict):
                    return item.get("url") or item.get("file_id") or item.get("path") or item.get("image_url")
                return None

            for key in ['photo_url', 'image_url', 'photo_file_id']:
                val = listing.get(key)
                if val:
                    photo_ref = _extract_url_or_id(val[0] if isinstance(val, list) and val else val)
                    if photo_ref:
                        break

        def _clean_field_value(field_val: Any) -> str:
            if field_val is None:
                return ""
            s = str(field_val).strip()
            s = re.sub(r"^(?:Fe'umsaa|ጭነት|Freight)\s*-\s*", "", s, flags=re.IGNORECASE)
            s = re.sub(r"\(Gabatee:[^)]+\)", "", s, flags=re.IGNORECASE)
            s = re.sub(r"📍\s*(?:Karaa|መስመር|Route|Location|Bakka|ቦታ):[^\n]+", "", s, flags=re.IGNORECASE)
            s = re.sub(r"⚖️\s*(?:Ulfaatina|ክብደት|Weight):[^\n]+", "", s, flags=re.IGNORECASE)
            s = re.sub(r"👥\s*(?:Baay'ina Namaa|የሰው ብዛት|Party Size):[^\n]+", "", s, flags=re.IGNORECASE)
            return s.strip()

        def _extract_supabase_attribute(
                sources: List[Dict[str, Any]],
                regex_patterns: List[str],
                fallback_keys: List[str]
        ) -> Optional[str]:
            for src in sources:
                if not isinstance(src, dict):
                    continue

                for fallback_key in fallback_keys:
                    field_val = src.get(fallback_key)
                    if field_val is not None:
                        cleaned = _clean_field_value(field_val)
                        cleaned_lower = cleaned.lower()
                        if (
                                cleaned_lower
                                and cleaned_lower not in INVALID_SENTINELS
                                and cleaned_lower not in INVALID_NUMERIC_SENTINELS
                        ):
                            return cleaned

                amenities_val = src.get('required_amenities') or src.get('amenities') or ''
                desc_val = src.get('description', '')

                amenities_str = str(amenities_val) if isinstance(amenities_val, (list, dict)) else str(amenities_val)
                blob = f"{amenities_str} {desc_val}"

                if blob.strip():
                    for pattern in regex_patterns:
                        match = re.search(pattern, blob, re.IGNORECASE)
                        if match:
                            cleaned = _clean_field_value(match.group(1))
                            cleaned_lower = cleaned.lower()
                            if (
                                    cleaned_lower
                                    and cleaned_lower not in INVALID_SENTINELS
                                    and cleaned_lower not in INVALID_NUMERIC_SENTINELS
                            ):
                                return cleaned

            return None

        def _build_localized_details(
                lang: str,
                current_deal_data: Optional[Dict[str, Any]] = None,
                current_listing: Optional[Dict[str, Any]] = None,
                current_vertical_type: Optional[str] = None
        ) -> Tuple[str, str]:
            target_deal = current_deal_data if current_deal_data is not None else deal_data
            target_listing = current_listing if current_listing is not None else listing
            target_vertical = current_vertical_type if current_vertical_type is not None else (
                    vertical_type or "general")

            raw_transaction_type = (target_deal or {}).get("deal_type") or (
                (target_listing or {}).get("transaction_type") if target_listing else None
            )

            clean_tx_key = str(raw_transaction_type or "").lower().strip()
            loc_tx = TRANSACTION_TRANSLATIONS.get(lang, TRANSACTION_TRANSLATIONS["en"]).get(
                clean_tx_key,
                str(raw_transaction_type).title() if raw_transaction_type else ""
            )

            tx_label = "ግብይት" if lang == "am" else "Daldala" if lang == "om" else "Transaction"
            transaction_display_str = f"🏷️ <b>{tx_label}:</b> {html.escape(str(loc_tx))}\n" if raw_transaction_type and not is_reverse_match else ""

            logistics_details_str = ""
            v_type = str(target_vertical).strip().lower()

            sources = [
                target_listing or {},
                target_deal or {},
                seeker_request_data,
                (target_listing or {}).get("metadata", {}) if isinstance((target_listing or {}).get("metadata"),
                                                                         dict) else {},
                (target_deal or {}).get("metadata", {}) if isinstance((target_deal or {}).get("metadata"),
                                                                      dict) else {},
                (seeker_request_data or {}).get("metadata", {}) if isinstance(
                    (seeker_request_data or {}).get("metadata"), dict) else {}
            ]

            if any(k in v_type for k in
                   ["freight", "ride", "rides", "railed", "rail", "transport", "vehicles", "vehicle", "freight_ride",
                    "cargo", "equipment", "machinery", "housing", "house"]):

                housing_specs_line = ""
                if "housing" in v_type or "house" in v_type:
                    h_prop_use = _extract_supabase_attribute(
                        sources=sources,
                        regex_patterns=[r"(?:Property Use|Property Type|Fayyadama|የአገልግሎት አይነት):\s*([^|\n,]+)"],
                        fallback_keys=["property_use", "property_type", "use", "category"]
                    )
                    use_clean_slug = (
                        str(h_prop_use or "")
                        .lower()
                        .replace('_', ' ')
                        .replace('use ', '')
                        .replace('request for ', '')
                        .split(' in ')[0]
                        .strip()
                    )

                    is_residential = "residential" in use_clean_slug and "plot" not in use_clean_slug
                    is_commercial_or_plot = "commercial" in use_clean_slug or "plot" in use_clean_slug or use_clean_slug in [
                        "commercial", "commercial plot", "commercial_plot", "residential plot", "residential_plot"]

                    if is_residential or (not is_commercial_or_plot and not use_clean_slug):
                        h_bedrooms = _extract_supabase_attribute(
                            sources=sources,
                            regex_patterns=[r"(?:Bedrooms|Bedroom|Rooms|Kutaa|የመተኛ ክፍሎች):\s*([^|\n,]+)"],
                            fallback_keys=["bedrooms", "bedroom_count", "bedrooms_count", "rooms", "num_bedrooms",
                                           "no_of_bedrooms", "bed_rooms", "number_of_bedrooms"]
                        )
                        if h_bedrooms:
                            bed_lbl = "የመተኛ ክፍሎች ብዛት" if lang == "am" else "Baay'ina Kutaa Hirriibaa" if lang == "om" else "Bedrooms"
                            housing_specs_line = f"🛏 <b>{bed_lbl}:</b> <code>{html.escape(str(h_bedrooms))}</code>\n"

                    if is_commercial_or_plot or not housing_specs_line:
                        h_area_sqm = _extract_supabase_attribute(
                            sources=sources,
                            regex_patterns=[
                                r"(?:Area|Size|Sqm|Square Meters|Sq Meters|Sq M|sq_meters|ካሬ ሜትር|Ballina):\s*([^|\n,]+)"],
                            fallback_keys=["sq_meters", "area_sqm", "sqm", "size_sqm", "property_size", "square_meters",
                                           "area", "size", "sq_meter", "sq_m"]
                        )
                        if h_area_sqm and (is_commercial_or_plot or not housing_specs_line):
                            area_lbl = "ስፋት" if lang == "am" else "Ballina" if lang == "om" else "Area"
                            unit_lbl = "ካሬ ሜትር" if lang == "am" else "m²"
                            housing_specs_line += f"📐 <b>{area_lbl}:</b> <code>{html.escape(str(h_area_sqm))} {unit_lbl}</code>\n"

                vehicle_specs_line = ""
                mmy = ""
                deal_asset_title_raw = (target_deal or {}).get('asset_title', '')
                if deal_asset_title_raw and not _is_generic_title(deal_asset_title_raw, v_type):
                    mmy = re.sub(r'\s*\(.*?\)', '', deal_asset_title_raw).strip()
                    mmy = _clean_field_value(mmy)

                if not mmy:
                    mmy = _extract_supabase_attribute(
                        sources=sources,
                        regex_patterns=[r"Model:\s*([^,|]+)",
                                        r"(?:Make/Model/Year|Model|Year|Barasaa|ሞዴል):\s*([^|\n,]+)"],
                        fallback_keys=["make_model_year", "model", "title", "name"]
                    )

                if mmy and str(mmy).strip().lower() not in ["freight", "vehicle", "ride", "railed", "transport",
                                                            "cargo", "equipment"]:
                    mmy = re.sub(r'\s*\(Plate:[^)]+\)', '', str(mmy), flags=re.IGNORECASE).strip()
                    mmy = _clean_field_value(mmy)
                    mmy = _normalize_asset_title(mmy)
                    if mmy.lower() not in INVALID_SENTINELS:
                        asset_label = "የንብረት ዝርዝር" if lang == "am" else "Tarree qabeenyaa" if lang == "om" else "Asset Details"
                        vehicle_specs_line = f"🚙 <b>{asset_label}:</b> <code>{html.escape(mmy)}</code>\n"

                fuel_type_line = ""
                if "vehicle" in v_type or "ride" in v_type or "railed" in v_type:
                    f_type = _extract_supabase_attribute(
                        sources=sources,
                        regex_patterns=[r"(?:Fuel Type|Gosa Bobaa|የነዳጅ አይነት):\s*([^|\n,]+)"],
                        fallback_keys=["fuel_type", "fuel", "engine_fuel"]
                    )
                    if f_type and f_type.lower() not in INVALID_SENTINELS:
                        ft_clean_key = f_type.strip().lower()
                        loc_fuel = FUEL_TRANSLATIONS.get(lang, FUEL_TRANSLATIONS["en"]).get(ft_clean_key,
                                                                                            f_type.title())
                        ft_label = "የነዳጅ አይነት" if lang == "am" else "Gosa Bobaa" if lang == "om" else "Fuel Type"
                        fuel_type_line = f"⛽ <b>{ft_label}:</b> <code>{html.escape(loc_fuel)}</code>\n"

                equip_specs_line = ""
                if "equipment" in v_type or "machinery" in v_type:
                    e_brand = _extract_supabase_attribute(
                        sources=sources,
                        regex_patterns=[r"(?:Brand|Braandii|ብራንድ):\s*([^|\n,]+)"],
                        fallback_keys=["brand", "make", "manufacturer"]
                    )
                    e_prop = _extract_supabase_attribute(
                        sources=sources,
                        regex_patterns=[
                            r"(?:Equipment Type|Gosa Meshaa|የመሳሪያ አይነት|Property Type|Property Use|Fayyadama|የአገልግሎት አይነት):\s*([^|\n,]+)"],
                        fallback_keys=["equipment_type", "equipment_name", "title", "name", "property_type",
                                       "property_use", "category"]
                    )

                    if e_brand:
                        b_label = "ብራንድ" if lang == "am" else "Braandii" if lang == "om" else "Brand"
                        equip_specs_line += f"🏷️ <b>{b_label}:</b> <code>{html.escape(e_brand)}</code>\n"
                    if e_prop:
                        p_label = "የአገልግሎት አይነት" if lang == "am" else "Fayyadama" if lang == "om" else "Property Type"
                        equip_specs_line += f"⚙ <b>{p_label}:</b> <code>{html.escape(e_prop)}</code>\n"

                vehicle_type_line = ""
                if "vehicle" in v_type or "ride" in v_type or "railed" in v_type:
                    v_kind = _extract_supabase_attribute(
                        sources=sources,
                        regex_patterns=[r"(?:Vehicle Type|Car Type|Gosa Konkolaataa|የተሽከርካሪ አይነት):\s*([^|\n,]+)"],
                        fallback_keys=["vehicle_type", "car_type", "type"]
                    )
                    if v_kind:
                        vk_clean_key = v_kind.strip().lower()
                        loc_vkind = VEHICLE_TYPE_TRANSLATIONS.get(lang, VEHICLE_TYPE_TRANSLATIONS["en"]).get(
                            vk_clean_key, v_kind.title()
                        )
                        vk_label = "የተሽከርካሪ አይነት" if lang == "am" else "Gosa Konkolaataa" if lang == "om" else "Vehicle Type"
                        vehicle_type_line = f"🚗 <b>{vk_label}:</b> <code>{html.escape(loc_vkind)}</code>\n"

                l_origin = _extract_supabase_attribute(
                    sources=sources,
                    regex_patterns=[r"(?:Origin|From|Karaa|መስመር|Pickup):\s*([^|\n,]+)"],
                    fallback_keys=["location_text", "pickup_location", "origin", "origin_city", "from_location"]
                )

                l_dest = _extract_supabase_attribute(
                    sources=sources,
                    regex_patterns=[r"(?:Destination_text|Dest|Destination|To|Dropoff):\s*([^|\n,]+)"],
                    fallback_keys=["destination_text", "Destination_text", "destination", "dropoff_location",
                                   "destination_city",
                                   "to_location"]
                )

                route_label = "መስመር" if lang == "am" else "Karaa" if lang == "om" else "Route"
                loc_label = "ቦታ" if lang == "am" else "Bakka" if lang == "om" else "Location"
                dest_label = "መድረሻ" if lang == "am" else "Gahumsa" if lang == "om" else "Destination"

                route_line = ""
                is_route_based = any(
                    kw in v_type for kw in
                    ["freight", "ride", "rides", "railed", "rail", "transport", "cargo", "freight_ride"])

                if is_route_based:
                    if l_origin and l_dest:
                        route_line = f"📍 <b>{route_label}:</b> <code>{html.escape(l_origin)} ➡️ {html.escape(l_dest)}</code>\n"
                    elif l_origin:
                        route_line = f"📍 <b>{loc_label}:</b> <code>{html.escape(l_origin)}</code>\n"
                    elif l_dest:
                        route_line = f"📍 <b>{dest_label}:</b> <code>{html.escape(l_dest)}</code>\n"
                else:
                    actual_location = l_origin or l_dest
                    if actual_location:
                        route_line = f"📍 <b>{loc_label}:</b> <code>{html.escape(actual_location)}</code>\n"

                l_plate = _extract_supabase_attribute(
                    sources=sources,
                    regex_patterns=[r"(?:Plate|Gabatee|ሰሌዳ):\s*([^|\n,]+)"],
                    fallback_keys=["vehicle_plate", "plate_number", "plate"]
                )

                plate_label = "ሰሌዳ" if lang == "am" else "Gabatee" if lang == "om" else "Plate Number"
                plate_line = f"🚙 <b>{plate_label}:</b> <code>{html.escape(l_plate)}</code>\n" if l_plate else ""

                capacity_line = ""
                seeker_sources = [
                    seeker_request_data,
                    target_deal or {},
                    target_listing or {},
                    (target_deal or {}).get("metadata", {}) if isinstance((target_deal or {}).get("metadata"),
                                                                          dict) else {},
                    (seeker_request_data or {}).get("metadata", {}) if isinstance(
                        (seeker_request_data or {}).get("metadata"), dict) else {}
                ]

                if any(k in v_type for k in ["freight", "cargo", "logistics"]):
                    f_weight = _extract_field(
                        seeker_sources,
                        ["cargo_weight_kgs", "cargo_weight", "weight_kgs", "weight", "payload_needed", "payload"]
                    )
                    if not f_weight and seeker_request_data:
                        s_blob = f"{seeker_request_data.get('required_amenities', '')} {seeker_request_data.get('description', '')}"
                        match = re.search(
                            r"(?:Cargo Weight|Ulfaatina Fe’iichaa|የዕቃ ክብደት|Cargo|Weight)\s*[:=]?\s*([\d,.]+\s*(?:kg|kgs|ton|tons|quintal|t)?)",
                            s_blob, re.IGNORECASE
                        )
                        if match:
                            f_weight = match.group(1)

                    if f_weight:
                        formatted_w = _format_weight(f_weight)
                        if formatted_w:
                            weight_label = "ክብደት" if lang == "am" else "Ulfaatina" if lang == "om" else "Cargo Weight"
                            capacity_line += f"⚖️ <b>{weight_label}:</b> <code>{html.escape(formatted_w)}</code>\n"

                if any(k in v_type for k in ["ride", "railed", "rail", "vehicle", "passenger", "taxi"]):
                    r_party = _extract_field(
                        seeker_sources,
                        ["party_size", "seats_needed", "passengers", "num_passengers", "party_count", "seats"]
                    )
                    if not r_party and seeker_request_data:
                        s_blob = f"{seeker_request_data.get('required_amenities', '')} {seeker_request_data.get('description', '')}"
                        match = re.search(
                            r"(?:Party Size|Seats Needed|Passengers|People|Nama|የሰው ብዛት|Baay'ina Namaa)\s*[:=]?\s*(\d+)",
                            s_blob, re.IGNORECASE
                        )
                        if match:
                            r_party = match.group(1)

                    if r_party:
                        formatted_p = _format_party_size(r_party, lang)
                        if formatted_p:
                            party_label = "የሰው ብዛት" if lang == "am" else "Baay'ina Namaa" if lang == "om" else "Party Size"
                            capacity_line += f"👥 <b>{party_label}:</b> <code>{html.escape(formatted_p)}</code>\n"

                logistics_details_str = (
                    f"{housing_specs_line}"
                    f"{equip_specs_line}"
                    f"{vehicle_type_line}"
                    f"{vehicle_specs_line}"
                    f"{fuel_type_line}"
                    f"{route_line}"
                    f"{capacity_line}"
                    f"{plate_line}"
                )

            return transaction_display_str, logistics_details_str

        # ------------------------------------------------------------------------
        # MAP LINK RESOLUTION & CROSS-LINK ALIGNMENT (ROBUST GLOBAL FALLBACK)
        # ------------------------------------------------------------------------

        def _resolve_map_link(
                lat: Optional[Any],
                lon: Optional[Any],
                loc_text: Optional[str],
                operating_region: Optional[str] = None,
                country: Optional[str] = None
        ) -> str:
            """
            Universally robust map link generator supporting coordinates, text strings,
            and regional context anywhere in the world (Ethiopia, Africa, Global).
            Prioritizes precise coordinates (@lat,lon) to eliminate Google Maps search errors.
            """
            # 1. Check if ReservationHelper is available and use it if it implements a valid generator
            if 'ReservationHelper' in globals() and hasattr(ReservationHelper, "generate_map_link"):
                try:
                    helper_link = ReservationHelper.generate_map_link(
                        lat, lon, loc_text, operating_region=operating_region, country=country
                    )
                    if helper_link and str(helper_link).startswith("http"):
                        return str(helper_link)
                except Exception:
                    pass

            # 2. Helper to safely parse and validate coordinates
            def _safe_float(cord_val):
                try:
                    if cord_val is not None:
                        f = float(cord_val)
                        if -90.0 <= f <= 90.0 or -180.0 <= f <= 180.0:
                            return f
                except (ValueError, TypeError):
                    pass
                return None

            latitude = _safe_float(lat)
            longitude = _safe_float(lon)

            # 3. PRIORITY 1: Coordinate-based Universal Google Maps URL (Never fails worldwide)
            if latitude is not None and longitude is not None and not (latitude == 0.0 and longitude == 0.0):
                return f"https://www.google.com/maps/search/?api=1&query={latitude},{longitude}"

            # 4. PRIORITY 2: Clean and structure text-based locations with regional context
            if loc_text:
                clean_text = str(loc_text).strip()
                # Strip routing arrows or duplicate tokens often found in transit routes (e.g., "Ambo ➔ Addis Ababa" -> "Ambo")
                for delimiter in [" ➔ ", " -> ", " - ", " to ", " እስከ "]:
                    if delimiter in clean_text:
                        clean_text = clean_text.split(delimiter, 1)[0].strip()
                        break

                # Append operating region or country context if not already present in the text string
                context_parts = [clean_text]
                reg_str = str(operating_region).strip() if operating_region else ""
                cnt_str = str(country).strip() if country else "Ethiopia"

                if reg_str and reg_str.lower() not in clean_text.lower():
                    context_parts.append(reg_str)
                if cnt_str and cnt_str.lower() not in clean_text.lower():
                    context_parts.append(cnt_str)

                final_query = ", ".join(context_parts)
                encoded_query = urllib.parse.quote(final_query)
                return f"https://www.google.com/maps/search/?api=1&query={encoded_query}"

            # 5. Fallback global center if completely empty
            return "https://www.google.com/maps"

        # 2. Flexible multi-alias resolution for context and entity dictionaries (PyCharm clean)
        listing_dict = (
                locals().get("listing")
                or locals().get("listing_dict")
                or locals().get("provider_record")
                or {}
        )
        request_dict = (
                locals().get("request_data")
                or locals().get("request_dict")
                or locals().get("seeker_request_data")
                or locals().get("seeker_record")
                or {}
        )
        deal_dict = (
                locals().get("deal")
                or locals().get("deal_data")
                or {}
        )

        ctx = locals().get("context")
        user_data = (
                (ctx.user_data if ctx and hasattr(ctx, "user_data") else None)
                or locals().get("ctx_user_data")
                or locals().get("user_data")
                or {}
        )

        # 3. Generate party map URLs leveraging ReservationHelper or falling back securely
        provider_map_link = ""
        seeker_map_link = ""

        if 'ReservationHelper' in globals() and hasattr(ReservationHelper, "generate_party_map_links"):
            try:
                map_links = ReservationHelper.generate_party_map_links(
                    listing=listing_dict,
                    request_data=request_dict,
                    deal=deal_dict,
                    context_user_data=user_data,
                    default_country="Ethiopia"
                )
                if isinstance(map_links, dict):
                    provider_map_link = map_links.get("provider_map_url", "")
                    seeker_map_link = map_links.get("seeker_map_url", "")
            except Exception as helper_err:
                logger.warning(f"Failed party map links via ReservationHelper: {helper_err}")

        # 4. Ultimate localized fallback utilizing the upgraded robust helper function
        if not provider_map_link:
            p_lat = deal_dict.get("provider_lat") or listing_dict.get("latitude") or listing_dict.get("lat")
            p_lon = deal_dict.get("provider_lon") or listing_dict.get("longitude") or listing_dict.get("lon")
            p_loc = deal_dict.get("provider_location_text") or listing_dict.get("location_text") or listing_dict.get(
                "location")
            p_reg = listing_dict.get("operating_region") or deal_dict.get("operating_region")
            provider_map_link = _resolve_map_link(p_lat, p_lon, p_loc, operating_region=p_reg, country="Ethiopia")

        if not seeker_map_link:
            s_lat = deal_dict.get("seeker_lat") or request_dict.get("latitude") or request_dict.get(
                "lat") or request_dict.get("destination_lat")
            s_lon = deal_dict.get("seeker_lon") or request_dict.get("longitude") or request_dict.get(
                "lon") or request_dict.get("destination_lon")
            s_loc = request_dict.get("location_text") or request_dict.get("location") or request_dict.get(
                "destination_text") or deal_dict.get("seeker_location_text")
            s_reg = request_dict.get("operating_region") or deal_dict.get("operating_region")
            seeker_map_link = _resolve_map_link(s_lat, s_lon, s_loc, operating_region=s_reg, country="Ethiopia")

        # ------------------------------------------------------------------------
        # MAP LINK LABELS & HTML ALIGNMENT (CROSS-LINKED TO CORRECT RECIPIENTS)
        # ------------------------------------------------------------------------
        MAP_LINK_LABELS = locals().get("MAP_LINK_LABELS", {
            "am": {
                "provider": "የአቅራቢውን ቦታ በ Google Maps ይክፈቱ",
                "seeker": "የፈላጊውን ቦታ በ Google Maps ይክፈቱ"
            },
            "om": {
                "provider": "Bakka Dhiheessaa Google Maps irratti banadhu",
                "seeker": "Bakka Barbaadaa Google Maps irratti banadhu"
            },
            "en": {
                "provider": "Open Provider Location in Google Maps",
                "seeker": "Open Seeker Location in Google Maps"
            }
        })

        s_lang = locals().get("seeker_lang", user_data.get("lang", "en"))
        p_lang = locals().get("provider_lang", user_data.get("lang", "en"))

        p_map_label_for_seeker = MAP_LINK_LABELS.get(s_lang, MAP_LINK_LABELS["en"]).get(
            "provider", "Open Provider Location in Google Maps"
        )
        s_map_label_for_provider = MAP_LINK_LABELS.get(p_lang, MAP_LINK_LABELS["en"]).get(
            "seeker", "Open Seeker Location in Google Maps"
        )

        # Cross-linked HTML variables:
        # - seeker_map_html goes to the Seeker Payment Confirmed Notification (showing Provider/Asset location)
        seeker_map_html = (
            f"\n📍 <a href='{provider_map_link}'>{p_map_label_for_seeker}</a>\n"
            if provider_map_link else ""
        )

        # - provider_map_html goes to the Provider Match Finalized Notification (showing Seeker/Client location)
        provider_map_html = (
            f"\n📍 <a href='{seeker_map_link}'>{s_map_label_for_provider}</a>\n"
            if seeker_map_link else ""
        )

        transit_mins = deal_data.get('transit_duration_minutes')
        countdown_html_seeker = ""
        countdown_html_provider = ""

        if transit_mins:
            if seeker_lang == "om":
                eta_s = f"{transit_mins // 60} sa'a {transit_mins % 60} daq" if transit_mins >= 60 else f"{transit_mins} daq"
                countdown_html_seeker = f"\n⏱️ <b>Konkolaataan Kan Qabame:</b> <code>{eta_s}</code> (Sa'aatii Gahumsaa + Daandii Fe'umsaa)\n"
            elif seeker_lang == "am":
                eta_s = f"{transit_mins // 60} ሰዓት {transit_mins % 60} ደቂቃ" if transit_mins >= 60 else f"{transit_mins} ደቂቃ"
                countdown_html_seeker = f"\n⏱️ <b>ተሽከርካሪው የተያዘው ለ:</b> <code>{eta_s}</code> (የመድረሻ ጊዜ + የማጓጓዣ መስመር)\n"
            else:
                eta_s = f"{transit_mins // 60} hrs {transit_mins % 60} mins" if transit_mins >= 60 else f"{transit_mins} mins"
                countdown_html_seeker = f"\n⏱️ <b>Vehicle Reserved For:</b> <code>{eta_s}</code> (ETA + Delivery Route)\n"

            if provider_lang == "om":
                eta_p = f"{transit_mins // 60} sa'a {transit_mins % 60} daq" if transit_mins >= 60 else f"{transit_mins} daq"
                countdown_html_provider = f"\n⏱️ <b>Konkolaataan Kan Qabame:</b> <code>{eta_p}</code> (Sa'aatii Gahumsaa + Daandii Fe'umsaa)\n"
            elif provider_lang == "am":
                eta_p = f"{transit_mins // 60} ሰዓት {transit_mins % 60} ደቂቃ" if transit_mins >= 60 else f"{transit_mins} ደቂቃ"
                countdown_html_provider = f"\n⏱️ <b>ተሽከርካሪው የተያዘው ለ:</b> <code>{eta_p}</code> (የመድረሻ ጊዜ + የማጓጓዣ መስመር)\n"
            else:
                eta_p = f"{transit_mins // 60} hrs {transit_mins % 60} mins" if transit_mins >= 60 else f"{transit_mins} mins"
                countdown_html_provider = f"\n⏱ <b>Vehicle Reserved For:</b> <code>{eta_p}</code> (ETA + Delivery Route)\n"

        safe_deal = deal_data or {}
        safe_listing = listing or {}
        general_sources = [
            safe_listing,
            safe_deal,
            seeker_request_data,
            safe_listing.get("metadata", {}) if isinstance(safe_listing.get("metadata"), dict) else {},
            safe_deal.get("metadata", {}) if isinstance(safe_deal.get("metadata"), dict) else {},
            seeker_request_data.get("metadata", {}) if isinstance(seeker_request_data.get("metadata"), dict) else {}
        ]

        extracted_brand = _extract_supabase_attribute(
            sources=general_sources,
            regex_patterns=[r"(?:Brand|Braandii|ብራንድ):\s*([^|\n,]+)"],
            fallback_keys=["brand", "make", "manufacturer"]
        ) or ""

        extracted_equipment_type = _extract_supabase_attribute(
            sources=general_sources,
            regex_patterns=[r"(?:Equipment Type|Gosa Meshaa|የመሳሪያ አይነት):\s*([^|\n,]+)"],
            fallback_keys=["equipment_type", "equipment_name", "title", "name"]
        ) or ""

        if str(vertical_type).lower() in ["equipment", "machinery"]:
            extracted_property_type = extracted_equipment_type or _extract_supabase_attribute(
                sources=general_sources,
                regex_patterns=[r"(?:Property Type|Property Use|Fayyadama|የአገልግሎት አይነት):\s*([^|\n,]+)"],
                fallback_keys=["property_type", "property_use", "category"]
            ) or ""
        else:
            extracted_property_type = _extract_supabase_attribute(
                sources=general_sources,
                regex_patterns=[r"(?:Property Type|Property Use|Fayyadama|የአገልግሎት አይነት):\s*([^|\n,]+)"],
                fallback_keys=["property_type", "property_use", "category"]
            ) or ""

        extracted_vehicle_type = _extract_supabase_attribute(
            sources=general_sources,
            regex_patterns=[r"(?:Vehicle Type|Car Type|Gosa Konkolaataa|የተሽከርካሪ አይነት):\s*([^|\n,]+)"],
            fallback_keys=["vehicle_type", "car_type", "type"]
        ) or ""

        extracted_make_model_year = _extract_supabase_attribute(
            sources=general_sources,
            regex_patterns=[r"(?:Make/Model/Year|Model|Year|Barasaa|ሞዴል):\s*([^|\n,]+)"],
            fallback_keys=["make_model_year", "model_year", "model", "title", "name"]
        ) or ""

        extracted_fuel_type = _extract_supabase_attribute(
            sources=general_sources,
            regex_patterns=[r"(?:Fuel Type|Gosa Bobaa|የነዳጅ አይነት):\s*([^|\n,]+)"],
            fallback_keys=["fuel_type", "fuel", "engine_fuel"]
        ) or ""

        safe_brand = html.escape(str(extracted_brand).strip())
        safe_property_type = html.escape(str(extracted_property_type).strip())
        safe_equipment_type = html.escape(str(extracted_equipment_type).strip())
        safe_vehicle_type = html.escape(str(extracted_vehicle_type).strip())
        safe_make_model_year = html.escape(_normalize_asset_title(str(extracted_make_model_year).strip()))
        safe_fuel_type = html.escape(str(extracted_fuel_type).strip())

        # ========================================================================
        # AGREED PRICE & CURRENCY EXTRACTION (Prioritizing deals table schema)
        # ========================================================================
        raw_agreed_price = (
                deal_data.get("agreed_price")
                or deal_data.get("current_price")
                or deal_data.get("agreed_amount")
                or deal_data.get("final_price")
                or deal_data.get("price")
                or deal_data.get("amount")
                or (listing.get("price") if listing else None)
                or (seeker_request_data.get("budget") if seeker_request_data else None)
                or (seeker_request_data.get("price") if seeker_request_data else None)
        )
        parsed_agreed_price = _safe_parse_float(raw_agreed_price, 0.0)

        deal_currency = str(
            deal_data.get("currency")
            or deal_data.get("fare_currency")
            or deal_data.get("price_currency")
            or (listing.get("currency") if listing else None)
            or (seeker_request_data.get("currency") if seeker_request_data else None)
            or "ETB"
        ).strip().upper()

        def _format_agreed_price(lang_code: str) -> str:
            if parsed_agreed_price > 0:
                formatted_num = f"{int(parsed_agreed_price):,}" if parsed_agreed_price.is_integer() else f"{parsed_agreed_price:,.2f}"
                return f"{formatted_num} {deal_currency}"
            elif raw_agreed_price and str(raw_agreed_price).strip().lower() not in ["none", "nan", "null", "0", "0.00"]:
                return f"{raw_agreed_price} {deal_currency}"
            else:
                return "Danda'amaa" if lang_code == "om" else "ተደራዳሪ" if lang_code == "am" else "Negotiable"

        agreed_price_seeker = _format_agreed_price(seeker_lang)
        agreed_price_provider = _format_agreed_price(provider_lang)

        s_price_label = PRICE_LABELS.get(seeker_lang, PRICE_LABELS["en"])
        p_price_label = PRICE_LABELS.get(provider_lang, PRICE_LABELS["en"])

        seeker_price_line = f"💰 <b>{s_price_label}:</b> <code>{html.escape(agreed_price_seeker)}</code>\n"
        provider_price_line = f"💰 <b>{p_price_label}:</b> <code>{html.escape(agreed_price_provider)}</code>\n"

        seeker_trans_str, seeker_logistics_str = _build_localized_details(seeker_lang)
        provider_trans_str, provider_logistics_str = _build_localized_details(provider_lang)

        # Permanent Default Localized Header Templates
        SEEKER_HEADERS = {
            "am": "ክፍያ ተረጋግጧል!",
            "om": "Kaffaltiin Mirkanaa'eera!",
            "en": "Payment Confirmed!"
        }

        PROVIDER_HEADERS = {
            "am": "ግጥሚያው ተጠናቋል!" if not is_reverse_match else "የተገላቢጦሽ ግጥሚያ ተጠናቋል!",
            "om": "Walitti-dhufeenyi Xumurameera!" if not is_reverse_match else "Garfaggalchi Walitti-dhufeenyaa Xumurameera!",
            "en": "Match Finalized!" if not is_reverse_match else "Reverse Match Finalized!"
        }

        # Helper to replace hardcoded Amharic or English domain prefixes with user's preferred language
        def sanitize_title(title_text: str, target_lang: str) -> str:
            if not title_text:
                return ""

            # Map of prefixes to clean domain words
            prefix_translations = {
                "om": {
                    "የጭነት": "Fe'umsa",
                    "የጉዞ": "Imala",
                    "Freight": "Fe'umsa",
                    "Ride": "Imala",
                    "Transport Route": "Kallattii Imalaa",
                    "የመጓጓዣ መንገድ": "Kallattii Imalaa"
                },
                "am": {
                    "Fe'umsa": "የጭነት",
                    "Imala": "የጉዞ",
                    "Freight": "የጭነት",
                    "Ride": "የጉዞ",
                    "Transport Route": "የመጓጓዣ መንገድ",
                    "Kallattii Imalaa": "የመጓጓዣ መንገድ"
                },
                "en": {
                    "የጭነት": "Freight",
                    "የጉዞ": "Ride",
                    "Fe'umsa": "Freight",
                    "Imala": "Ride",
                    "Kallattii Imalaa": "Transport Route",
                    "የመጓጓዣ መንገድ": "Transport Route"
                },
            }

            clean_title_appends = str(title_text)
            lang_map = prefix_translations.get(target_lang, prefix_translations["en"])

            for old_prefix, new_prefix in lang_map.items():
                if clean_title_appends.startswith(old_prefix):
                    clean_title_appends = clean_title_appends.replace(old_prefix, new_prefix, 1)

            return clean_title_appends.strip()

        # ✅ Safely resolve language preferences without PyCharm 'Unresolved reference' flags
        s_lang = seeker_lang if ('seeker_lang' in locals() and seeker_lang) else locals().get('norm_lang', 'en')
        p_lang = provider_lang if ('provider_lang' in locals() and provider_lang) else locals().get('norm_lang', 'en')

        # Pre-compute language-specific sanitized and HTML-escaped titles
        seeker_title_am = html.escape(sanitize_title(seeker_asset_title, 'am'))
        seeker_title_om = html.escape(sanitize_title(seeker_asset_title, 'om'))
        seeker_title_en = html.escape(sanitize_title(seeker_asset_title, 'en'))

        provider_title_am = html.escape(sanitize_title(provider_asset_title, 'am'))
        provider_title_om = html.escape(sanitize_title(provider_asset_title, 'om'))
        provider_title_en = html.escape(sanitize_title(provider_asset_title, 'en'))

        # Dynamically resolved sanitized title matching recipient's specific language for _safe_get_text
        resolved_seeker_title = (
            seeker_title_om if s_lang == 'om'
            else seeker_title_am if s_lang == 'am'
            else seeker_title_en
        )
        resolved_provider_title = (
            provider_title_om if p_lang == 'om'
            else provider_title_am if p_lang == 'am'
            else provider_title_en
        )

        if is_reverse_match:
            seeker_body_templates = {
                "am": (
                    f"ለተገላቢጦሽ ግጥሚያው የኮሚሽን ክፍያ በተሳካ ሁኔታ ተፈጽሟል:"
                    f" <b>{seeker_title_am}</b> (መለያ:"
                    f" <code>{deal_id[:8]}</code>)።\n💰 <b>የተስማሙበት ዋጋ:</b>"
                    f" <code>{agreed_price_seeker}</code>"
                ),
                "om": (
                    f"Kaffaltiin komishinii garfaggalchaa milkaiinaan raawwatameera:"
                    f" <b>{seeker_title_om}</b>"
                    f" (Koodii: <code>{deal_id[:8]}</code>).\n💰 <b>Gatiin"
                    f" Waliigaltee:</b> <code>{agreed_price_seeker}</code>"
                ),
                "en": (
                    f"Commission settled successfully for reverse match request:"
                    f" <b>{seeker_title_en}</b>"
                    f" (Deal ID: <code>{deal_id[:8]}</code>).\n💰 <b>Agreed Price:</b>"
                    f" <code>{agreed_price_seeker}</code>"
                ),
            }
            provider_body_templates = {
                "am": (
                    f"ለተገላቢጦሽ ግጥሚያዎ የፕላትፎርም ኮሚሽን ክፍያ ተፈጽሟል:"
                    f" <b>{provider_title_am}</b>"
                    f" (መለያ: <code>{deal_id[:8]}</code>)።\n💰 <b>የተስማሙበት ዋጋ:</b>"
                    f" <code>{agreed_price_provider}</code>"
                ),
                "om": (
                    f"Kaffaltiin komishinii garfaggalcha qabeenya keessaniif"
                    f" raawwatameera:"
                    f" <b>{provider_title_om}</b>"
                    f" (Koodii: <code>{deal_id[:8]}</code>).\n💰 <b>Gatiin"
                    f" Waliigaltee:</b> <code>{agreed_price_provider}</code>"
                ),
                "en": (
                    f"Reverse match commission settled for your asset proposal:"
                    f" <b>{provider_title_en}</b>"
                    f" (Deal ID: <code>{deal_id[:8]}</code>).\n💰 <b>Agreed Price:</b>"
                    f" <code>{agreed_price_provider}</code>"
                ),
            }
            seeker_body_key = "dispatch_reverse_seeker_body"
            provider_body_key = "dispatch_reverse_provider_body"
            seeker_header_key = "dispatch_reverse_match_seeker_header"
            provider_header_key = "dispatch_reverse_match_provider_header"
        else:
            seeker_body_templates = {
                "am": (
                    f"ለዚህ ንብረት የኮሚሽን ክፍያ በተሳካ ሁኔታ ተፈጽሟል:"
                    f" <b>{seeker_title_am}</b> (መለያ:"
                    f" <code>{deal_id[:8]}</code>)።\n💰 <b>የተስማሙበት ዋጋ:</b>"
                    f" <code>{agreed_price_seeker}</code>"
                ),
                "om": (
                    f"Kaffaltiin komishinii milkaiinaan raawwatameera:"
                    f" <b>{seeker_title_om}</b>"
                    f" (Koodii: <code>{deal_id[:8]}</code>).\n💰 <b>Gatiin"
                    f" Waliigaltee:</b> <code>{agreed_price_seeker}</code>"
                ),
                "en": (
                    f"Commission settled successfully for:"
                    f" <b>{seeker_title_en}</b>"
                    f" (Deal ID: <code>{deal_id[:8]}</code>).\n💰 <b>Agreed Price:</b>"
                    f" <code>{agreed_price_seeker}</code>"
                ),
            }
            provider_body_templates = {
                "am": (
                    f"የንብረት ፈላጊው ለዝርዝርዎ የፕላትፎርም ኮሚሽን ከፍሏል:"
                    f" <b>{provider_title_am}</b>"
                    f" (መለያ: <code>{deal_id[:8]}</code>)።\n💰 <b>የተስማሙበት ዋጋ:</b>"
                    f" <code>{agreed_price_provider}</code>"
                ),
                "om": (
                    f"Barbaadaan qabeenyaa kaffaltii komishinii dhaabbata keessanii"
                    f" raawwateera:"
                    f" <b>{provider_title_om}</b>"
                    f" (Koodii: <code>{deal_id[:8]}</code>).\n💰 <b>Gatiin"
                    f" Waliigaltee:</b> <code>{agreed_price_provider}</code>"
                ),
                "en": (
                    f"An asset seeker has settled the platform commission for your"
                    f" listing: <b>{provider_title_en}</b>"
                    f" (Deal ID: <code>{deal_id[:8]}</code>).\n💰 <b>Agreed Price:</b>"
                    f" <code>{agreed_price_provider}</code>"
                ),
            }
            seeker_body_key = "dispatch_seeker_body"
            provider_body_key = "dispatch_provider_body"
            seeker_header_key = "dispatch_payment_confirmed"
            provider_header_key = "dispatch_match_finalized"

        def_s_hdr = SEEKER_HEADERS.get(seeker_lang, SEEKER_HEADERS["en"])
        def_p_hdr = PROVIDER_HEADERS.get(provider_lang, PROVIDER_HEADERS["en"])
        def_s_body = seeker_body_templates.get(seeker_lang, seeker_body_templates["en"])
        def_p_body = provider_body_templates.get(provider_lang, provider_body_templates["en"])

        seeker_hdr_raw = _safe_get_text(seeker_lang, seeker_header_key, default=def_s_hdr)
        provider_hdr_raw = _safe_get_text(provider_lang, provider_header_key, default=def_p_hdr)

        if seeker_lang != "en" and ("Payment Confirmed" in seeker_hdr_raw or "MISSING" in seeker_hdr_raw):
            seeker_hdr_raw = def_s_hdr
        if provider_lang != "en" and ("Match Finalized" in provider_hdr_raw or "MISSING" in provider_hdr_raw):
            provider_hdr_raw = def_p_hdr

        seeker_hdr_clean = re.sub(r'^[✅🔄✨\s]+', '', seeker_hdr_raw).strip()
        provider_hdr_clean = re.sub(r'^[✅🔄✨\s]+', '', provider_hdr_raw).strip()

        hdr_icon = "🔄" if is_reverse_match else "✅"
        seeker_header_formatted = f"🏠🚗🏗️<b>SMA</b>│<b>{seeker_hdr_clean}</b>{hdr_icon}\n━━━━━━━━━━━━━━━━━━━━━━"
        provider_header_formatted = f"✨🏠🚗🏗️<b>SMA</b>│<b>{provider_hdr_clean}</b>{hdr_icon}\n━━━━━━━━━━━━━━━━━━━━━━"

        seeker_body = _safe_get_text(
            seeker_lang,
            seeker_body_key,
            default=def_s_body,
            asset_title=resolved_seeker_title,  # ✅ Uses pre-sanitized, language-matched seeker title
            deal_id=deal_id[:8],
            brand=safe_brand,
            equipment_type=safe_equipment_type or safe_property_type,
            property_type=safe_property_type,
            property_use=safe_property_type,
            vehicle_type=safe_vehicle_type,
            make_model_year=safe_make_model_year,
            fuel_type=safe_fuel_type,
            price=agreed_price_seeker,
            currency=deal_currency
        )

        provider_body = _safe_get_text(
            provider_lang,
            provider_body_key,
            default=def_p_body,
            asset_title=resolved_provider_title,  # ✅ Uses pre-sanitized, language-matched provider title
            deal_id=deal_id[:8],
            brand=safe_brand,
            equipment_type=safe_equipment_type or safe_property_type,
            property_type=safe_property_type,
            property_use=safe_property_type,
            vehicle_type=safe_vehicle_type,
            make_model_year=safe_make_model_year,
            fuel_type=safe_fuel_type,
            price=agreed_price_provider,
            currency=deal_currency
        )

        if seeker_lang != "en" and "Commission settled successfully" in seeker_body:
            seeker_body = def_s_body
        if provider_lang != "en" and "An asset seeker has settled" in provider_body:
            provider_body = def_p_body

        def_s_footer = (
            "Yeroo gabaabduu keessatti bilbila isaanii eegaa, yookiin kallattiin isaaniif bilbilaa. Smart Match Africa fayyadamuu keessaniif galatoomaa!" if seeker_lang == "om" else
            "በቅርቡ ጥሪያቸውን ይጠብቁ ወይም ለመጀመር በቀጥታ ያግኙዋቸው። የስማርት ማች አፍሪካን (Smart Match Africa) አገልግሎት ስለተጠቀሙ እናመሰግናለን!" if seeker_lang == "am" else
            "Expect their call shortly, or contact them directly to get started. Thank you for using Smart Match Africa!"
        )

        def_p_footer = (
            "Yeroo gabaabduu keessatti bilbila isaanii eegaa, yookiin kallattiin isaaniif bilbilaa. Smart Match Africa fayyadamuu keessaniif galatoomaa!" if provider_lang == "om" else
            "በቅርቡ ጥሪያቸውን ይጠብቁ ወይም ለመጀመር በቀጥታ ያግኙዋቸው። የስማርት ማች አፍሪካን (Smart Match Africa) አገልግሎት ስለተጠቀሙ እናመሰግናለን!" if provider_lang == "am" else
            "Expect their call shortly, or contact them directly to get started. Thank you for using Smart Match Africa!"
        )

        seeker_footer = (_safe_get_text(seeker_lang, 'dispatch_seeker_footer', default=def_s_footer)).replace(
            "Smart Match Africa", "Smart Match Africa")
        provider_footer = (_safe_get_text(provider_lang, 'dispatch_provider_footer', default=def_p_footer)).replace(
            "Smart Match Africa", "Smart Match Africa")

        cat_label_s = "ምድብ" if seeker_lang == "am" else "Gosa" if seeker_lang == "om" else "Category"
        cat_label_p = "ምድብ" if provider_lang == "am" else "Gosa" if provider_lang == "om" else "Category"

        def_s_phone = (
            f"📞 Teessoo Dhiheessaa: <code>{html.escape(provider_phone)}</code>" if seeker_lang == "om" else
            f"📞 የአቅራቢ ስልክ: <code>{html.escape(provider_phone)}</code>" if seeker_lang == "am" else
            f"📞 Provider Contact: <code>{html.escape(provider_phone)}</code>"
        )

        def_p_phone = (
            f"📞 Teessoo Maamilaa: <code>{html.escape(seeker_phone)}</code>" if provider_lang == "om" else
            f"📞 የደንበኛ ስልክ: <code>{html.escape(seeker_phone)}</code>" if provider_lang == "am" else
            f"📞 Client Contact: <code>{html.escape(seeker_phone)}</code>"
        )

        s_phone_msg = _safe_get_text(seeker_lang, 'dispatch_provider_phone',
                                     default=def_s_phone,
                                     phone=html.escape(provider_phone))
        p_phone_msg = _safe_get_text(provider_lang, 'dispatch_client_phone',
                                     default=def_p_phone,
                                     phone=html.escape(seeker_phone))

        # Build Message Payloads with Map Links
        seeker_message = _clean_markdown_syntax(
            f"{seeker_header_formatted}\n\n"
            f"{seeker_body}\n\n"
            f"📂 <b>{cat_label_s}:</b> {html.escape(str(display_vertical_name_seeker))}\n"
            f"{seeker_trans_str}"
            f"{seeker_price_line}"
            f"{seeker_logistics_str}"
            f"{countdown_html_seeker}"
            f"{s_phone_msg}\n"
            f"{seeker_map_html}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{seeker_footer}"
        )

        provider_message = _clean_markdown_syntax(
            f"{provider_header_formatted}\n\n"
            f"{provider_body}\n\n"
            f"📂 <b>{cat_label_p}:</b> {html.escape(str(display_vertical_name_provider))}\n"
            f"{provider_trans_str}"
            f"{provider_price_line}"
            f"{provider_logistics_str}"
            f"{countdown_html_provider}"
            f"{p_phone_msg}\n"
            f"{provider_map_html}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{provider_footer}"
        )

        presigned_url: Optional[str] = None
        if photo_ref:
            img_service: Any = getattr(self, "image_service", getattr(self, "storage", None))
            if img_service and hasattr(img_service, "get_presigned_url_async"):
                try:
                    presigned_url = await img_service.get_presigned_url_async(photo_ref)
                except Exception as sign_err:
                    logger.warning(f"Failed to generate pre-signed URL for Deal {deal_id}: {sign_err}")

        target_seeker_id = await self._resolve_chat_id(supabase_client, seeker_id)
        target_provider_id = await self._resolve_chat_id(supabase_client, provider_id)

        dispatch_results = await asyncio.gather(
            self._safe_send_telegram_notification(target_seeker_id, seeker_message, presigned_url, role="Seeker"),
            self._safe_send_telegram_notification(target_provider_id, provider_message, presigned_url, role="Provider"),
            return_exceptions=True
        )

        # Evaluate individual results for granular tracking and persistence retry
        seeker_res = dispatch_results[0]
        provider_res = dispatch_results[1]

        seeker_success = (seeker_res is True)
        provider_success = (provider_res is True)
        any_success = seeker_success or provider_success

        if any_success or (not target_seeker_id and not target_provider_id):
            try:
                update_payload = {
                    "dispatch_sent": True,
                    "deal_state": "finalized",
                    "status": "finalized",
                    "updated_at": datetime.now(timezone.utc).isoformat()
                }
                current_payment_ref = payment_reference or deal_data.get("payment_reference")
                if current_payment_ref:
                    update_payload["payment_reference"] = current_payment_ref

                await supabase_client.table("deals").update(update_payload).eq("id", deal_id).execute()
                logger.info(f"🎯 Deal {deal_id} record successfully updated: dispatch_sent=True, status=finalized.")
            except Exception as db_update_err:
                logger.error(f"Failed to update database claim for deal {deal_id}: {db_update_err}")

        # 🛡️ PERSISTENCE RETRY: Push any failed individual dispatches to the database retry queue (Fixed: removed redundant 'self')
        if target_seeker_id and not seeker_success:
            seeker_err = str(seeker_res) if isinstance(seeker_res, Exception) else "Dispatch returned False"
            await self.handle_dispatch_failure(
                deal_id,
                target_seeker_id,
                {"message": seeker_message, "presigned_url": presigned_url, "role": "Seeker"},
                seeker_err
            )

        if target_provider_id and not provider_success:
            provider_err = str(provider_res) if isinstance(provider_res, Exception) else "Dispatch returned False"
            await self.handle_dispatch_failure(
                deal_id,
                target_provider_id,
                {"message": provider_message, "presigned_url": presigned_url, "role": "Provider"},
                provider_err
            )

        if not any_success and (target_seeker_id or target_provider_id):
            logger.error(
                f"CRITICAL: Deal {deal_id} notification dispatches failed to deliver. Failed payloads persisted to retry queue.")

    # ==========================================
    # POST-PAYMENT FINALIZATION & DISPATCH ENGINE
    # ==========================================
    async def process_successful_commission_payment(
            self,
            deal_id: str,
            presigned_url: Optional[str] = None,
            payment_reference: Optional[str] = None,
            _update: Optional[Update] = None,
            _context: Optional[ContextTypes.DEFAULT_TYPE] = None
    ) -> bool:
        """
        Finalizes a deal after verified commission payment, unlocks mutual contact details,
        updates asset/listing status, attaches persistent menus, and dispatches localized confirmation notifications.
        Explicitly guarantees inclusion of agreed_price and complete map location links in both Seeker and Provider messages across all languages.
        """
        logger.info(f"Processing successful commission payment for Deal ID: {deal_id}")

        supabase_client: Any = getattr(self.db, "supabase", getattr(self, "db", None))
        if supabase_client is None:
            logger.error("Supabase database client is not available for payment processing.")
            return False

        try:
            deal_res = (
                await supabase_client.table("deals")
                .select("*")
                .eq("id", deal_id)
                .maybe_single()
                .execute()
            )
            if not deal_res or not deal_res.data:
                logger.error(f"Cannot finalize payment: Deal {deal_id} not found.")
                return False

            deal = deal_res.data

            if deal.get("deal_state") == "finalized" and deal.get("dispatch_sent") is True:
                logger.warning(f"Deal {deal_id} is already finalized and notifications were dispatched.")
                return True

            target_seeker_id = deal.get("seeker_id")
            target_provider_id = deal.get("provider_id")
            listing_id = deal.get("listing_id")
            seeker_request_id = deal.get("seeker_request_id") or deal.get("request_id")

            is_reverse_match = bool(
                seeker_request_id
                or deal.get("match_type") == "reverse"
                or deal.get("deal_type") == "reverse_match"
            )

            # ✅ Ensure commissions table status is explicitly updated to 'paid' upon successful processing
            try:
                await supabase_client.table("commissions").update({
                    "status": "paid",
                    "updated_at": datetime.now(timezone.utc).isoformat()
                }).eq("deal_id", deal_id).execute()
            except Exception as comm_upd_err:
                logger.warning(f"Failed to update commissions table for deal {deal_id}: {comm_upd_err}")

            # ✅ Guardrail: Verify commission status is 'paid' before proceeding with telegram notifications & dispatch
            comm_verify = await supabase_client.table("commissions").select("status").eq("deal_id",
                                                                                         deal_id).maybe_single().execute()
            if not comm_verify or not comm_verify.data or str(comm_verify.data.get("status")).lower() != "paid":
                logger.error(f"❌ Dispatch aborted for Deal {deal_id}: Commission status is unconfirmed or pending.")
                return False

            listing = {}
            if listing_id:
                if hasattr(self.db, "get_listing_by_id"):
                    try:
                        listing = await self.db.get_listing_by_id(listing_id) or {}
                    except Exception:
                        listing = {}
                if not listing:
                    l_res = (
                        await supabase_client.table("provider_listings")
                        .select("*")
                        .eq("id", listing_id)
                        .maybe_single()
                        .execute()
                    )
                    if l_res and l_res.data:
                        listing = l_res.data

            request_data = {}
            if seeker_request_id:
                req_res = (
                    await supabase_client.table("seeker_requests")
                    .select("*")
                    .eq("id", seeker_request_id)
                    .maybe_single()
                    .execute()
                )
                if req_res and req_res.data:
                    request_data = req_res.data

            if not request_data and target_seeker_id:
                try:
                    s_id_str = str(target_seeker_id).strip()
                    q = supabase_client.table("seeker_requests").select("*")
                    if _is_valid_uuid(s_id_str):
                        q = q.eq("seeker_id", s_id_str)
                    elif s_id_str.isdigit():
                        q = q.eq("seeker_id", s_id_str)
                    else:
                        q = q.eq("seeker_id", s_id_str)

                    req_res = await q.order("created_at", desc=True).limit(1).execute()
                    if req_res and req_res.data and isinstance(req_res.data, list) and len(req_res.data) > 0:
                        request_data = req_res.data[0]
                except Exception as req_err:
                    logger.warning(f"Could not load fallback request data for seeker {target_seeker_id}: {req_err}")

            raw_vertical = None
            if is_reverse_match and request_data:
                raw_vertical = request_data.get("vertical_type") or request_data.get(
                    "vertical") or request_data.get("type")

            if not raw_vertical and listing:
                raw_vertical = listing.get("vertical_type") or listing.get("vertical") or listing.get("type")

            if not raw_vertical and request_data:
                raw_vertical = request_data.get("vertical_type") or request_data.get(
                    "vertical") or request_data.get("type")

            if not raw_vertical:
                raw_vertical = deal.get("vertical_type") or deal.get("vertical") or "asset"

            safe_vertical = str(raw_vertical).strip().lower()

            def _extract_url_or_id(item):
                if not item:
                    return None
                if isinstance(item, str):
                    cleaned = item.strip()
                    if cleaned.startswith("{") and cleaned.endswith("}"):
                        try:
                            parsed_json = json.loads(cleaned)
                            if isinstance(parsed_json, dict):
                                return parsed_json.get("url") or parsed_json.get("file_id") or parsed_json.get(
                                    "path")
                        except Exception:
                            pass
                    return cleaned if cleaned else None
                if isinstance(item, dict):
                    return item.get("url") or item.get("file_id") or item.get("path") or item.get("image_url")
                return None

            if not presigned_url:
                for key in ['photo_url', 'image_url', 'photo_file_id', 'images', 'photos', 'vehicle_photo',
                            'media_url']:
                    val = deal.get(key) or listing.get(key) or request_data.get(key)
                    if val:
                        presigned_url = _extract_url_or_id(val[0]) if isinstance(val, list) and len(
                            val) > 0 else _extract_url_or_id(val)
                        if presigned_url:
                            break

            if presigned_url:
                if not (str(presigned_url).startswith("http://") or str(presigned_url).startswith("https://")):
                    img_service = getattr(self, "image_service", getattr(self, "storage", None))
                    if img_service and hasattr(img_service, "get_presigned_url_async"):
                        try:
                            presigned_url = await img_service.get_presigned_url_async(presigned_url)
                        except Exception as sign_err:
                            logger.warning(f"Failed to generate pre-signed URL: {sign_err}")
                            presigned_url = None

            seeker_phone = html.escape(
                _extract_field([deal, request_data], ["seeker_phone", "contact_phone", "phone"],
                               default="Not provided")
            )
            provider_phone = html.escape(
                _extract_field([deal, listing], ["provider_phone", "contact_phone", "phone"],
                               default="Not provided")
            )

            # Robust user language resolution avoiding Postgres UUID query syntax errors
            async def _resolve_user_lang(user_id: Any, explicit_lang: Optional[str]) -> str:
                norm_explicit = _normalize_lang_code(explicit_lang)
                if explicit_lang and norm_explicit in ["am", "om", "en"]:
                    return norm_explicit
                if not user_id:
                    return norm_explicit or "en"

                s_user_id = str(user_id).strip()
                try:
                    query = supabase_client.table("users").select(
                        "preferred_language, language, language_code"
                    )

                    if _is_valid_uuid(s_user_id):
                        query = query.eq("id", s_user_id)
                    elif s_user_id.isdigit():
                        query = query.eq("telegram_id", int(s_user_id))
                    else:
                        query = query.eq("telegram_id", s_user_id)

                    res = await query.maybe_single().execute()

                    if res and res.data:
                        lang = (
                                res.data.get("preferred_language")
                                or res.data.get("language")
                                or res.data.get("language_code")
                        )
                        if lang:
                            return _normalize_lang_code(lang)
                except Exception as lang_err:
                    logger.warning(f"Failed resolving language for user {user_id}: {lang_err}")

                return norm_explicit or "en"

            s_lang = await _resolve_user_lang(target_seeker_id, deal.get("seeker_lang"))
            p_lang = await _resolve_user_lang(target_provider_id, deal.get("provider_lang"))

            # ------------------------------------------------------------------------
            # MULTI-SOURCE AGREED PRICE EXTRACTION & FORMATTING ENGINE (Source-of-Truth prioritized)
            # ------------------------------------------------------------------------
            def _extract_and_format_price(lang_code: str) -> str:
                # Prioritize deals table database attributes first as source of truth to avoid session/context pollution
                raw_price = (
                    deal.get("agreed_price")
                    if deal.get("agreed_price") is not None and str(deal.get("agreed_price")).strip() != ""
                    else deal.get("current_price")
                    if deal.get("current_price") is not None and str(deal.get("current_price")).strip() != ""
                    else deal.get("price")
                    if deal.get("price") is not None and str(deal.get("price")).strip() != ""
                    else listing.get("price") or listing.get("agreed_price") or listing.get("rate")
                    if listing and isinstance(listing, dict)
                    else request_data.get("price") or request_data.get("agreed_price") or request_data.get("budget")
                    if request_data and isinstance(request_data, dict)
                    else None
                )

                # Prioritize currency directly from the deals record first
                curr = str(
                    deal.get("currency")
                    or deal.get("fare_currency")
                    or deal.get("price_currency")
                    or (listing.get("currency") if listing else None)
                    or (request_data.get("currency") if request_data else None)
                    or "ETB"
                ).strip().upper()

                if raw_price is not None and str(raw_price).strip() != "":
                    try:
                        p_val = float(raw_price)
                        if p_val > 0:
                            formatted_num = f"{int(p_val):,}" if p_val.is_integer() else f"{p_val:,.2f}"
                            return f"{formatted_num} {curr}"
                    except (ValueError, TypeError):
                        pass

                    raw_str = str(raw_price).strip()
                    if raw_str.lower() not in ["none", "null", "0", "0.0", "n/a"]:
                        return f"{raw_str} {curr}" if curr not in raw_str else raw_str

                if lang_code == "am":
                    return "በድርድር"
                elif lang_code == "om":
                    return "Marii Dhaaniin"
                else:
                    return "Negotiable"

            agreed_price_str_s = _extract_and_format_price(s_lang)
            agreed_price_str_p = _extract_and_format_price(p_lang)

            ctx_user_data = _context.user_data if (
                    _context and hasattr(_context, "user_data") and isinstance(_context.user_data,
                                                                               dict)) else {}

            sources = [
                listing,
                deal,
                request_data,
                ctx_user_data,
                listing.get("metadata", {}) if isinstance(listing.get("metadata"), dict) else {},
                deal.get("metadata", {}) if isinstance(deal.get("metadata"), dict) else {},
                request_data.get("metadata", {}) if isinstance(request_data.get("metadata"), dict) else {}
            ]

            amenities_blob = " ".join([
                str(listing.get("required_amenities", "") if listing else ""),
                str(listing.get("amenities", "") if listing else ""),
                str(listing.get("description", "") if listing else ""),
                str(deal.get("required_amenities", "")),
                str(deal.get("amenities", "")),
                str(deal.get("description", "")),
                str(request_data.get("required_amenities", "") if request_data else "")
            ])

            def _extract_attribute(fallback_keys: List[str], regex_patterns: List[str]) -> str:
                new_val = _extract_field(sources, fallback_keys, default=None)
                if new_val is not None:
                    return str(new_val).strip()

                if amenities_blob.strip():
                    for pattern in regex_patterns:
                        match = re.search(pattern, amenities_blob, re.IGNORECASE)
                        if match:
                            found_val = match.group(1).strip()
                            if found_val.lower() not in INVALID_SENTINELS and found_val.lower() not in INVALID_NUMERIC_SENTINELS:
                                return found_val
                return ""

            extracted_equipment_type = _extract_attribute(
                fallback_keys=["equipment_type", "equipment_name", "title", "name"],
                regex_patterns=[r"(?:Equipment Type|Gosa Meshaa|የመሳሪያ አይነት):\s*([^|\n,]+)"]
            )

            if str(safe_vertical).lower() in ["equipment", "machinery"]:
                extracted_property_type = extracted_equipment_type or _extract_attribute(
                    fallback_keys=["property_type", "property_use", "category"],
                    regex_patterns=[r"(?:Property Type|Property Use|Fayyadama|የአገልግሎት አይነት):\s*([^|\n,]+)"]
                )
            else:
                extracted_property_type = _extract_attribute(
                    fallback_keys=["property_type", "property_use", "category"],
                    regex_patterns=[r"(?:Property Type|Property Use|Fayyadama|የአገልግሎት አይነት):\s*([^|\n,]+)"]
                )

            extracted_brand = _extract_attribute(
                fallback_keys=["brand", "make", "manufacturer", "preferred_brand"],
                regex_patterns=[r"(?:Brand|Braandii|ብራንድ):\s*([^|\n,]+)"]
            )

            extracted_vehicle_type = _extract_attribute(
                fallback_keys=["vehicle_type", "car_type", "type"],
                regex_patterns=[r"(?:Vehicle Type|Car Type|Gosa Konkolaataa|የተሽከርካሪ አይነት):\s*([^|\n,]+)"]
            )

            extracted_make_model_year = _extract_attribute(
                fallback_keys=["make_model_year", "model_year", "model", "title", "name"],
                regex_patterns=[r"(?:Make/Model/Year|Model|Year|Barasaa|ሞዴል):\s*([^|\n,]+)"]
            )

            extracted_fuel_type = _extract_attribute(
                fallback_keys=["fuel_type", "fuel", "engine_fuel"],
                regex_patterns=[r"(?:Fuel Type|Gosa Bobaa|የነዳጅ አይነት):\s*([^|\n,]+)"]
            )

            safe_brand = html.escape(extracted_brand)
            safe_property_type = html.escape(extracted_property_type)
            safe_equipment_type = html.escape(extracted_equipment_type)
            safe_vehicle_type = html.escape(extracted_vehicle_type)
            safe_make_model_year = html.escape(_normalize_lang_code(extracted_make_model_year))
            safe_fuel_type = html.escape(extracted_fuel_type)

            DEFAULT_ASSET_NAMES = {
                "en": "Matched Asset Listing",
                "am": "የተጣጣመ ንብረት",
                "om": "Qabeenya Wal-gitaa"
            }

            def _get_title(lang_code: str) -> str:
                # Strict normalization helper to handle regional tags or case differences (e.g., 'om-ET', 'OM')
                def _norm_lang(raw: Any) -> str:
                    if not raw:
                        return "en"
                    clean = str(raw).lower().strip().replace("-", "_").split("_")[0]
                    if clean in ("om", "or", "oromoo"):
                        return "om"
                    if clean in ("am", "amharic", "amh"):
                        return "am"
                    return "en"

                # 1. Retrieve raw title from available sources
                t = deal.get("asset_title") or request_data.get("property_use") or request_data.get("title")
                if not t and listing and hasattr(self, 'format_asset_title'):
                    try:
                        t = getattr(self, 'format_asset_title')(listing, safe_vertical)
                    except Exception:
                        t = None

                if not t:
                    normalized_lang = _norm_lang(lang_code)
                    def_t = DEFAULT_ASSET_NAMES.get(normalized_lang, DEFAULT_ASSET_NAMES["en"])
                    t = _safe_get_text(lang_code, "lbl_default_asset", default=def_t)

                # 2. Comprehensive Multi-Language Prefix Stripping
                # List of all possible prefixes across EN, AM, and OM to wipe out completely
                all_known_prefixes = [
                    "Transport Route", "Kallattii Imalaa", "የመጓጓዣ መንገድ",
                    "Freight", "Fe'umsa", "የጭነት",
                    "Ride", "Imala", "የጉዞ"
                ]

                t_str = str(t).strip()

                # Detect category based on existing prefix or content
                detected_cat = "transport_route"
                for p in ["Freight", "Fe'umsa", "የጭነት"]:
                    if t_str.startswith(p):
                        detected_cat = "freight"
                        break
                for p in ["Ride", "Imala", "የጉዞ"]:
                    if t_str.startswith(p):
                        detected_cat = "ride"
                        break
                for p in ["Transport Route", "Kallattii Imalaa", "የመጓጓዣ መንገድ"]:
                    if t_str.startswith(p):
                        detected_cat = "transport_route"
                        break

                # Strip ALL known cross-language prefixes from the start of the string
                stripped_title = t_str
                for prefix in all_known_prefixes:
                    if stripped_title.startswith(prefix):
                        stripped_title = stripped_title[len(prefix):].strip()
                        # Remove any trailing separators like colons, dashes, etc.
                        stripped_title = stripped_title.lstrip(": -–—").strip()
                        break

                # Optional: Run legacy cleaners if available
                if '_clean_asset_title_language_mixup' in globals():
                    stripped_title = _clean_asset_title_language_mixup(stripped_title)

                # 3. Apply target language prefix cleanly
                normalized_lang = _norm_lang(lang_code)

                prefix_dict = {
                    "om": {"freight": "Fe'umsa", "ride": "Imala", "transport_route": "Kallattii Imalaa"},
                    "am": {"freight": "የጭነት", "ride": "የጉዞ", "transport_route": "የመጓጓዣ መንገድ"},
                    "en": {"freight": "Freight", "ride": "Ride", "transport_route": "Transport Route"}
                }
                chosen_prefix = prefix_dict.get(normalized_lang, prefix_dict["en"])[detected_cat]

                if stripped_title:
                    final_title = f"{chosen_prefix}: {stripped_title}"
                else:
                    final_title = chosen_prefix

                return html.escape(final_title)

            def _build_localized_details(target_lang: str):
                VERTICAL_TRANSLATIONS = {
                    "am": {"housing": "ቤት", "house": "ቤት", "vehicle": "ተሽከርካሪ", "vehicles": "ተሽከርካሪ",
                           "equipment": "ዕቃዎች", "machinery": "ማሽነሪ", "freight": "ጭነት", "cargo": "ጭነት",
                           "ride": "ራይድ", "rides": "ራይድ", "railed": "የባቡር/ራይል", "asset": "ንብረት"},
                    "om": {"housing": "Mana", "house": "Mana", "vehicle": "Konkolaataa", "vehicles": "Konkolaataa",
                           "equipment": "Meshaa", "machinery": "Maashineerii", "freight": "Fe'umsaa",
                           "cargo": "Fe'umsaa", "ride": "Imala", "rides": "Imala", "railed": "Gareen",
                           "asset": "Qabeenya"},
                    "en": {"housing": "Housing", "house": "Housing", "vehicle": "Vehicle", "vehicles": "Vehicle",
                           "equipment": "Equipment", "machinery": "Machinery", "freight": "Freight",
                           "cargo": "Freight", "ride": "Ride", "rides": "Ride", "railed": "Railed",
                           "asset": "Asset"}
                }
                TRANSACTION_TRANSLATIONS = {
                    "am": {"rental": "ኪራይ", "rent": "ኪራይ", "sale": "ሽያጭ", "sell": "ሽያጭ", "lease": "ሊዝ",
                           "service": "አገልግሎት", "booking": "ቦታ ማስያዝ"},
                    "om": {"rental": "Kiraa", "rent": "Kiraa", "sale": "Gurgurtaa", "sell": "Gurgurtaa",
                           "lease": "Liizii", "service": "Tajaajila", "booking": "Qabiinsa"},
                    "en": {"rental": "Rental", "rent": "Rental", "sale": "Sale", "sell": "Sale", "lease": "Lease",
                           "service": "Service", "booking": "Booking"}
                }

                # PROPERTY USE TRANSLATIONS (Resolves 'get_prop_val' reference error)
                PROPERTY_USE_NAMES = {
                    "residential": {"am": "መኖሪያ ቤት", "om": "Mana Jireenyaa", "en": "Residential"},
                    "commercial": {"am": "ንግድ ቤት", "om": "Mana Daldalaa", "en": "Commercial"},
                    "residential plot": {"am": "የመኖሪያ ሜዳ/ቦታ", "om": "Lafa Jireenyaa", "en": "Residential Plot"},
                    "commercial plot": {"am": "የንግድ ሜዳ/ቦታ", "om": "Lafa Daldalaa", "en": "Commercial Plot"},
                    "agricultural": {"am": "ግብርና", "om": "Qonnaa", "en": "Agricultural"}
                }

                def get_prop_val(raw_val: str) -> str:
                    if not raw_val:
                        return ""
                    raw = str(raw_val).strip().lower()
                    return (
                            PROPERTY_USE_NAMES.get(raw, {}).get(target_lang)
                            or PROPERTY_USE_NAMES.get(raw, {}).get("en")
                            or raw.title()
                    )

                FUEL_TRANSLATIONS = {
                    "am": {
                        "benzine": "ቤንዚን",
                        "petrol": "ቤንዚን",
                        "diesel": "ዲዝል (ናፍጣ)",
                        "electric": "ኤሌክትሪክ",
                        "lng_ngv": "ኤል.ኤን.ጂ / የተፈጥሮ ጋዝ (NGV)",
                        "lng": "ኤል.ኤን.ጂ / የተፈጥሮ ጋዝ (NGV)",
                        "ngv": "ኤል.ኤን.ጂ / የተፈጥሮ ጋዝ (NGV)",
                        "hybrid": "ሃይብሪድ",
                        "hybrid_petrol": "ቤንዚን-ኤሌክትሪክ ሃይብሪድ",
                        "hybrid_diesel": "ዲዝል-ኤሌክትሪክ ሃይብሪድ",
                        "hybrid_flex": "ፍሌክስ-ፉኤል (ኢታኖል) ሃይብሪድ",
                        "hybrid_gaseous": "ጋዝ-ኤሌክትሪክ (Bi-Fuel) ሃይብሪድ",
                        "hybrid_hydrogen": "ሃይድሮጅን ፉኤል ሴል ሃይብሪድ",
                    },
                    "om": {
                        "benzine": "Beenzinii (Beenzila)",
                        "petrol": "Beenzinii (Beenzila)",
                        "diesel": "Dizilii (Naafxaa)",
                        "electric": "Elektiriika",
                        "lng_ngv": "LNG / Gaazii Uumamaa (NGV)",
                        "lng": "LNG / Gaazii Uumamaa (NGV)",
                        "ngv": "LNG / Gaazii Uumamaa (NGV)",
                        "hybrid": "Haaybiriidii",
                        "hybrid_petrol": "Beenzinii-Elektiriika Haaybiriidii",
                        "hybrid_diesel": "Dizilii-Elektiriika Haaybiriidii",
                        "hybrid_flex": "Flex-Fuel (Etaanoolii) Haaybiriidii",
                        "hybrid_gaseous": "Gaazii-Elektiriika Haaybiriidii",
                        "hybrid_hydrogen": "Haayidroojiinii Haaybiriidii",
                    },
                    "en": {
                        "benzine": "Benzine (Petrol)",
                        "petrol": "Benzine (Petrol)",
                        "diesel": "Diesel",
                        "electric": "Electric",
                        "lng_ngv": "LNG / NGV (Natural Gas)",
                        "lng": "LNG / NGV (Natural Gas)",
                        "ngv": "LNG / NGV (Natural Gas)",
                        "hybrid": "Hybrid",
                        "hybrid_petrol": "Petrol-Electric Hybrid",
                        "hybrid_diesel": "Diesel-Electric Hybrid",
                        "hybrid_flex": "Flex-Fuel (Ethanol) Hybrid",
                        "hybrid_gaseous": "Bi-Fuel (Gas-Electric) Hybrid",
                        "hybrid_hydrogen": "Hydrogen Fuel Cell Hybrid",
                    }
                }
                VEHICLE_TYPE_TRANSLATIONS = {
                    "am": {"sedan": "ሴዳን", "suv": "ኤስ.ዩ.ቪ (SUV)", "truck": "የጭነት ተሽከርካሪ", "pickup": "ፒክ-አፕ",
                           "van": "ቫን", "bus": "አውቶቡስ"},
                    "om": {"sedan": "Sedan (Makiinaa Salphaa)", "suv": "SUV", "truck": "Konkolaataa Fe'umsaa",
                           "pickup": "Pik-aappii", "van": "Vaanii", "bus": "Mootora/Awtobusi"},
                    "en": {"sedan": "Sedan", "suv": "SUV", "truck": "Truck", "pickup": "Pickup", "van": "Van",
                           "bus": "Bus"}
                }

                # Explicitly define listing (provider_listings) and request (seeker_requests) sources
                listing_source = listing if isinstance(listing, dict) else {}
                request_source = request_data if isinstance(request_data, dict) else {}

                # Define localized prefixes for Listing vs Request differentiation
                PREFIX_LABELS = {
                    "am": {"listing": "የአቅራቢ ዝርዝር (Listing)", "request": "የፈላጊ ጥያቄ (Request)"},
                    "om": {"listing": "Tarree Dhiyeessaa (Listing)", "request": "Gaaffii Barbaadaa (Request)"},
                    "en": {"listing": "Listing", "request": "Request"}
                }
                l_tag = PREFIX_LABELS.get(target_lang, PREFIX_LABELS["am"])["listing"]
                r_tag = PREFIX_LABELS.get(target_lang, PREFIX_LABELS["am"])["request"]

                # STRICTLY PRIORITIZE PROVIDER LISTING for asset title and display since agreed asset is from listings
                primary_source = listing_source if listing_source else request_source

                disp_v = VERTICAL_TRANSLATIONS.get(target_lang, VERTICAL_TRANSLATIONS["am"]).get(
                    safe_vertical, safe_vertical.replace("_", " ").title()
                )

                if primary_source and safe_vertical in ["equipment", "machinery"]:
                    sub_cat = primary_source.get("category") or primary_source.get(
                        "property_use") or primary_source.get("property_type")
                    equip_val = primary_source.get("equipment_type")
                    if sub_cat and str(sub_cat).lower() != str(equip_val or "").lower():
                        disp_v = f"{disp_v} ({str(sub_cat).title()})"
                    elif equip_val:
                        disp_v = f"{disp_v} ({str(equip_val).title()})"

                disp_v = html.escape(disp_v)

                raw_tx = deal.get("transaction_type") or (
                        primary_source.get("transaction_type") or
                        primary_source.get("listing_type") or primary_source.get("deal_type")
                ) if primary_source else deal.get("transaction_type")

                clean_tx = str(raw_tx or "Rental").lower().strip()
                loc_tx = html.escape(
                    TRANSACTION_TRANSLATIONS.get(target_lang, TRANSACTION_TRANSLATIONS["am"]).get(
                        clean_tx, str(raw_tx).title() if raw_tx else "Rental"
                    ))

                tx_label = "የግብይት አይነት" if target_lang == "am" else "Gosa Daldalaa" if target_lang == "om" else "Transaction"
                def_tx_str = f"🏷️ <b>{tx_label}:</b> {html.escape(str(loc_tx))}\n"

                tx_str = _safe_get_text(target_lang, "dispatch_transaction", default=def_tx_str,
                                        transaction_type=loc_tx) if raw_tx else ""
                logistics_str = ""

                vt_type = safe_vertical
                if any(kw in vt_type for kw in
                       ["freight", "ride", "rides", "railed", "rail", "transport", "vehicles", "vehicle",
                        "freight_ride", "cargo", "equipment", "machinery", "housing", "house"]):

                    housing_specs_line = ""
                    if "housing" in vt_type or "house" in vt_type:
                        l_prop_use = listing_source.get("property_use") or listing_source.get("property_type")
                        l_bedrooms = listing_source.get("bedrooms")
                        l_sqm = listing_source.get("sq_meters")

                        r_prop_use = request_source.get("property_use") or request_source.get("property_type")
                        r_bedrooms = request_source.get("min_bedrooms") or request_source.get("bedrooms")
                        r_sqm = request_source.get("min_sq_meters") or request_source.get("sq_meters")

                        if l_prop_use:
                            p_label = "የአገልግሎት አይነት" if target_lang == "am" else "Fayyadama" if target_lang == "om" else "Property Use"
                            housing_specs_line += f"🏠 <b>[{l_tag}] {p_label}:</b> <code>{html.escape(get_prop_val(l_prop_use))}</code>\n"
                        elif r_prop_use:
                            p_label = "የአገልግሎት አይነት" if target_lang == "am" else "Fayyadama" if target_lang == "om" else "Property Use"
                            housing_specs_line += f"🏠 <b>[{r_tag}] {p_label}:</b> <code>{html.escape(get_prop_val(r_prop_use))}</code>\n"

                        if l_bedrooms is not None:
                            bed_lbl = "የመተኛ ክፍሎች ብዛት" if target_lang == "am" else "Baay'ina Kutaa Hirriibaa" if target_lang == "om" else "Bedrooms"
                            housing_specs_line += f"🛏 <b>[{l_tag}] {bed_lbl}:</b> <code>{html.escape(str(l_bedrooms))}</code>\n"
                        if r_bedrooms is not None and str(r_bedrooms) != str(l_bedrooms):
                            bed_lbl = "የመተኛ ክፍሎች ብዛት" if target_lang == "am" else "Baay'ina Kutaa Hirriibaa" if target_lang == "om" else "Bedrooms (Min)"
                            housing_specs_line += f"🛏 <b>[{r_tag}] {bed_lbl}:</b> <code>{html.escape(str(r_bedrooms))}</code>\n"

                        if l_sqm is not None:
                            area_lbl = "ስፋት" if target_lang == "am" else "Ballina" if target_lang == "om" else "Area"
                            unit_lbl = "ካሬ ሜትር" if target_lang == "am" else "m²"
                            housing_specs_line += f"📐 <b>[{l_tag}] {area_lbl}:</b> <code>{html.escape(str(l_sqm))} {unit_lbl}</code>\n"
                        if r_sqm is not None and str(r_sqm) != str(l_sqm):
                            area_lbl = "ስፋት" if target_lang == "am" else "Ballina" if target_lang == "om" else "Area (Min)"
                            unit_lbl = "ካሬ ሜትር" if target_lang == "am" else "m²"
                            housing_specs_line += f"📐 <b>[{r_tag}] {area_lbl}:</b> <code>{html.escape(str(r_sqm))} {unit_lbl}</code>\n"

                    equip_specs_line = ""
                    if "equipment" in vt_type or "machinery" in vt_type:
                        l_brand = listing_source.get("brand")
                        r_brand = request_source.get("preferred_brand") or request_source.get("brand")

                        l_eq = listing_source.get("equipment_type") or listing_source.get("property_use")
                        r_eq = request_source.get("equipment_type") or request_source.get("property_use")

                        if l_brand:
                            b_label = "ብራንድ" if target_lang == "am" else "Braandii" if target_lang == "om" else "Brand"
                            equip_specs_line += f"🏷️ <b>[{l_tag}] {b_label}:</b> <code>{html.escape(str(l_brand))}</code>\n"
                        if r_brand and str(r_brand).lower() != str(l_brand or "").lower():
                            b_label = "ተመራጭ ብራንድ" if target_lang == "am" else "Braandii Filatame" if target_lang == "om" else "Preferred Brand"
                            equip_specs_line += f"🏷️ <b>[{r_tag}] {b_label}:</b> <code>{html.escape(str(r_brand))}</code>\n"

                        if l_eq:
                            e_label = "የመሳሪያ አይነት" if target_lang == "am" else "Gosa Meeshaa" if target_lang == "om" else "Equipment Type"
                            equip_specs_line += f"⚙️ <b>[{l_tag}] {e_label}:</b> <code>{html.escape(str(l_eq))}</code>\n"
                        if r_eq and str(r_eq).lower() != str(l_eq or "").lower():
                            e_label = "የተጠየቀ መሳሪያ" if target_lang == "am" else "Meeshaa Gaafatame" if target_lang == "om" else "Requested Equipment"
                            equip_specs_line += f"⚙️ <b>[{r_tag}] {e_label}:</b> <code>{html.escape(str(r_eq))}</code>\n"

                    vehicle_type_line = ""
                    vehicle_specs_line = ""
                    fuel_type_line = ""
                    if "vehicle" in vt_type or "ride" in vt_type or "railed" in vt_type:
                        l_vtype = listing_source.get("vehicle_type")
                        r_vtype = request_source.get("vehicle_type")

                        l_mmy = listing_source.get("make_model_year")
                        r_mmy = request_source.get("make_model_year")

                        l_fuel = listing_source.get("fuel_type")
                        r_fuel = request_source.get("fuel_type")

                        if l_vtype:
                            vk_label = "የተሽከርካሪ አይነት" if target_lang == "am" else "Gosa Konkolaataa" if target_lang == "om" else "Vehicle Type"
                            loc_l_vtype = VEHICLE_TYPE_TRANSLATIONS.get(target_lang,
                                                                        VEHICLE_TYPE_TRANSLATIONS["am"]).get(
                                str(l_vtype).lower(), str(l_vtype)
                            )
                            vehicle_type_line += f"🚗 <b>[{l_tag}] {vk_label}:</b> <code>{html.escape(loc_l_vtype)}</code>\n"
                        if r_vtype and str(r_vtype).lower() != str(l_vtype or "").lower():
                            vk_label = "የተጠየቀ ተሽከርካሪ" if target_lang == "am" else "Konkolaataa Gaafatame" if target_lang == "om" else "Requested Vehicle Type"
                            loc_r_vtype = VEHICLE_TYPE_TRANSLATIONS.get(target_lang,
                                                                        VEHICLE_TYPE_TRANSLATIONS["am"]).get(
                                str(r_vtype).lower(), str(r_vtype)
                            )
                            vehicle_type_line += f"🚗 <b>[{r_tag}] {vk_label}:</b> <code>{html.escape(loc_r_vtype)}</code>\n"

                        if l_mmy:
                            mmy_label = "የተሽከርካሪ ሞዴል" if target_lang == "am" else "Moodeela/Bara" if target_lang == "om" else "Model/Year"
                            vehicle_specs_line += f"🚙 <b>[{l_tag}] {mmy_label}:</b> <code>{html.escape(str(l_mmy))}</code>\n"
                        if r_mmy and str(r_mmy) != str(l_mmy or ""):
                            mmy_label = "የተጠየቀ ሞዴል" if target_lang == "am" else "Moodeela Gaafatame" if target_lang == "om" else "Requested Model"
                            vehicle_specs_line += f"🚙 <b>[{r_tag}] {mmy_label}:</b> <code>{html.escape(str(r_mmy))}</code>\n"

                        if l_fuel:
                            ft_label = "የነዳጅ አይነት" if target_lang == "am" else "Gosa Bobaa" if target_lang == "om" else "Fuel Type"
                            loc_l_fuel = FUEL_TRANSLATIONS.get(target_lang, FUEL_TRANSLATIONS["am"]).get(
                                str(l_fuel).lower(), str(l_fuel)
                            )
                            fuel_type_line += f"⛽ <b>[{l_tag}] {ft_label}:</b> <code>{html.escape(loc_l_fuel)}</code>\n"
                        if r_fuel and str(r_fuel).lower() != str(l_fuel or "").lower():
                            ft_label = "የተጠየቀ ነዳጅ አይነት" if target_lang == "am" else "Gosa Bobaa Gaafatame" if target_lang == "om" else "Requested Fuel Type"
                            loc_r_fuel = FUEL_TRANSLATIONS.get(target_lang, FUEL_TRANSLATIONS["am"]).get(
                                str(r_fuel).lower(), str(r_fuel)
                            )
                            fuel_type_line += f"⛽ <b>[{r_tag}] {ft_label}:</b> <code>{html.escape(loc_r_fuel)}</code>\n"

                    # Routes, Locations, and Plates
                    l_origin = listing_source.get("location_text") or listing_source.get(
                        "origin") or listing_source.get("operating_region")
                    r_origin = request_source.get("location_text") or request_source.get(
                        "origin") or request_source.get("operating_region")

                    l_dest = listing_source.get("destination_text")
                    r_dest = request_source.get("destination_text")

                    l_plate = listing_source.get("vehicle_plate")

                    route_label = "መስመር" if target_lang == "am" else "Karaa" if target_lang == "om" else "Route"
                    location_label = "ቦታ" if target_lang == "am" else "Bakka" if target_lang == "om" else "Location"
                    _dest_label = "መድረሻ" if target_lang == "am" else "Gahumsa" if target_lang == "om" else "Destination"
                    plate_label = "የሰሌዳ ቁጥር" if target_lang == "am" else "Lakk. Gabatee" if target_lang == "om" else "Plate Number"

                    location_route_line = ""
                    is_route_based = any(
                        kw in vt_type for kw in ["freight", "ride", "rides", "railed", "rail", "transport", "cargo"])

                    if is_route_based:
                        if l_origin or l_dest:
                            orig_disp = l_origin or "N/A"
                            dest_disp = l_dest or "N/A"
                            location_route_line += f"📍 <b>[{l_tag}] {route_label}:</b> <code>{html.escape(str(orig_disp))} ➡️ {html.escape(str(dest_disp))}</code>\n"
                        if r_origin or r_dest:
                            if str(r_origin) != str(l_origin) or str(r_dest) != str(l_dest):
                                orig_disp = r_origin or "N/A"
                                dest_disp = r_dest or "N/A"
                                location_route_line += f"📍 <b>[{r_tag}] {route_label}:</b> <code>{html.escape(str(orig_disp))} ➡️ {html.escape(str(dest_disp))}</code>\n"
                    else:
                        if l_origin:
                            location_route_line += f"📍 <b>[{l_tag}] {location_label}:</b> <code>{html.escape(str(l_origin))}</code>\n"
                        if r_origin and str(r_origin) != str(l_origin):
                            location_route_line += f"📍 <b>[{r_tag}] {location_label}:</b> <code>{html.escape(str(r_origin))}</code>\n"

                    plate_line = f"🚙 <b>[{l_tag}] {plate_label}:</b> <code>{html.escape(str(l_plate))}</code>\n" if l_plate else ""

                    logistics_str = f"{housing_specs_line}{equip_specs_line}{vehicle_type_line}{vehicle_specs_line}{fuel_type_line}{location_route_line}{plate_line}"

                return disp_v, clean_tx, tx_str, logistics_str

            seeker_title = _get_title(s_lang)
            provider_title = _get_title(p_lang)

            s_vname, clean_tx_s, seeker_trans_str, seeker_logistics_str = _build_localized_details(s_lang)
            p_vname, clean_tx_p, provider_trans_str, provider_logistics_str = _build_localized_details(p_lang)

            explicit_capacity_s = ""
            explicit_capacity_p = ""

            seeker_req_sources = [
                request_data,
                deal,
                listing,
                ctx_user_data,
                (request_data or {}).get("metadata", {}) if isinstance((request_data or {}).get("metadata"),
                                                                       dict) else {},
                (deal or {}).get("metadata", {}) if isinstance((deal or {}).get("metadata"), dict) else {}
            ]

            is_freight_type = any(k in safe_vertical for k in ["freight", "cargo", "logistics", "payload"])
            is_ride_railed_type = any(
                k in safe_vertical for k in ["ride", "railed", "rail", "passenger", "vehicle", "taxi"])

            if is_freight_type or safe_vertical in ["freight_ride", "transport"]:
                c_weight = _extract_field(
                    seeker_req_sources,
                    ["cargo_weight_kgs", "cargo_weight", "weight_kgs", "weight", "payload_needed", "payload"]
                )
                if not c_weight and request_data:
                    s_blob = f"{request_data.get('required_amenities', '')} {request_data.get('description', '')}"
                    m = re.search(
                        r"(?:Cargo Weight|Ulfaatina Fe’iichaa|የዕቃ ክብደት|Cargo|Weight)\s*[:=]?\s*([\d,.]+\s*(?:kg|kgs|ton|tons|quintal|t)?)",
                        s_blob, re.IGNORECASE
                    )
                    if m:
                        c_weight = m.group(1)

                if c_weight:
                    formatted_w = _format_weight(c_weight)
                    if formatted_w:
                        w_lbl_s = "የዕቃ ክብደት" if s_lang == "am" else "Ulfaatina Fe’iichaa" if s_lang == "om" else "Cargo Weight"
                        w_lbl_p = "የዕቃ ክብደት" if p_lang == "am" else "Ulfaatina Fe’iichaa" if p_lang == "om" else "Cargo Weight"
                        explicit_capacity_s += f"⚖️ <b>{w_lbl_s}:</b> <code>{html.escape(formatted_w)}</code>\n"
                        explicit_capacity_p += f"⚖️ <b>{w_lbl_p}:</b> <code>{html.escape(formatted_w)}</code>\n"

            if is_ride_railed_type or safe_vertical in ["freight_ride", "transport"]:
                p_size = _extract_field(
                    seeker_req_sources,
                    ["party_size", "seats_needed", "passengers", "num_passengers", "party_count", "seats"]
                )
                if not p_size and request_data:
                    s_blob = f"{request_data.get('required_amenities', '')} {request_data.get('description', '')}"
                    m = re.search(
                        r"(?:Party Size|Seats Needed|Passengers|People|Nama|የሰው ብዛት|Baay'ina Namaa)\s*[:=]?\s*(\d+)",
                        s_blob, re.IGNORECASE
                    )
                    if m:
                        p_size = m.group(1)

                if p_size:
                    formatted_ps = _format_party_size(p_size, s_lang)
                    formatted_pp = _format_party_size(p_size, p_lang)
                    if formatted_ps:
                        p_lbl_s = "የሰው ብዛት" if s_lang == "am" else "Baay'ina Namaa" if s_lang == "om" else "Party Size"
                        p_lbl_p = "የሰው ብዛት" if p_lang == "am" else "Baay'ina Namaa" if p_lang == "om" else "Party Size"
                        explicit_capacity_s += f"👥 <b>{p_lbl_s}:</b> <code>{html.escape(formatted_ps)}</code>\n"
                        explicit_capacity_p += f"👥 <b>{p_lbl_p}:</b> <code>{html.escape(formatted_pp)}</code>\n"

            # ========================================================================
            # MAP LINK RESOLUTION & CROSS-LINK ALIGNMENT (ALIGNED WITH reserve__quantity_location.py)
            # ========================================================================

            # 1. Extract context, dictionaries, and Supabase client safely
            ctx = locals().get("context")
            user_data = (
                    (ctx.user_data if ctx and hasattr(ctx, "user_data") else None)
                    or locals().get("ctx_user_data")
                    or locals().get("user_data")
                    or {}
            )

            listing_dict = locals().get("listing") or locals().get("listing_dict") or {}
            request_dict = locals().get("request_data") or locals().get("request_dict") or locals().get(
                "seeker_request_data") or {}
            deal_dict = locals().get("deal") or locals().get("deal_data") or locals().get("deal_record") or {}

            supabase_client = getattr(self, 'db', None)
            if hasattr(supabase_client, 'supabase'):
                supabase_client = supabase_client.supabase

            # 2. Leverage ReservationHelper & LocationService from reserve__quantity_location.py
            provider_map_link = ""
            seeker_map_link = ""

            # Synchronous/Asynchronous Party Map Link Resolution
            if 'ReservationHelper' in globals() and hasattr(ReservationHelper, "generate_party_map_links"):
                try:
                    map_links = ReservationHelper.generate_party_map_links(
                        listing=listing_dict,
                        request_data=request_dict,
                        deal=deal_dict,
                        context_user_data=user_data,
                        default_country="Ethiopia"
                    )
                    if isinstance(map_links, dict):
                        provider_map_link = map_links.get("provider_map_url", "")
                        seeker_map_link = map_links.get("seeker_map_url", "")
                except Exception as helper_err:
                    logger.warning(f"Failed party map links via ReservationHelper: {helper_err}")

            # Fallback generator if map links are empty
            if not provider_map_link and 'LocationService' in globals() and hasattr(LocationService,
                                                                                    "generate_map_link"):
                try:
                    if '_extract_location_data' in globals():
                        p_lat, p_lon, p_text, p_reg = _extract_location_data(listing_dict, is_provider=True)
                    else:
                        p_lat, p_lon, p_text, p_reg = None, None, None, None

                    provider_map_link = LocationService.generate_map_link(
                        lat=p_lat,
                        lon=p_lon,
                        location_text=p_text,
                        operating_region=p_reg,
                        country="Ethiopia"
                    )
                except Exception as e:
                    logger.warning(f"Provider map fallback failed: {e}")

            if not seeker_map_link and 'LocationService' in globals() and hasattr(LocationService, "generate_map_link"):
                try:
                    if '_extract_location_data' in globals():
                        s_lat, s_lon, s_text, s_reg = _extract_location_data(request_dict, is_provider=False)
                    else:
                        s_lat, s_lon, s_text, s_reg = None, None, None, None

                    seeker_map_link = LocationService.generate_map_link(
                        lat=s_lat,
                        lon=s_lon,
                        location_text=s_text,
                        operating_region=s_reg,
                        country="Ethiopia"
                    )
                except Exception as e:
                    logger.warning(f"Seeker map fallback failed: {e}")

            # ------------------------------------------------------------------------
            # MAP LINK LABELS & HTML ALIGNMENT (WITH PLAIN-TEXT URL FALLBACK SUPPORT)
            # ------------------------------------------------------------------------
            MAP_LINK_LABELS = locals().get("MAP_LINK_LABELS", {
                "am": {
                    "provider": "የአቅራቢውን ቦታ በ Google Maps ይክፈቱ",
                    "seeker": "የፈላጊውን ቦታ በ Google Maps ይክፈቱ"
                },
                "om": {
                    "provider": "Bakka Dhiheessaa Google Maps irratti banadhu",
                    "seeker": "Bakka Barbaadaa Google Maps irratti banadhu"
                },
                "en": {
                    "provider": "Open Provider Location in Google Maps",
                    "seeker": "Open Seeker Location in Google Maps"
                }
            })

            s_lang = locals().get("seeker_lang", user_data.get("lang", "en"))
            p_lang = locals().get("provider_lang", user_data.get("lang", "en"))

            p_map_label_for_seeker = MAP_LINK_LABELS.get(s_lang, MAP_LINK_LABELS["en"]).get(
                "provider", "Open Provider Location in Google Maps"
            )
            s_map_label_for_provider = MAP_LINK_LABELS.get(p_lang, MAP_LINK_LABELS["en"]).get(
                "seeker", "Open Seeker Location in Google Maps"
            )

            # 💡 ROBUST DUAL-FORMAT HTML + PLAIN URL FALLBACK:
            if provider_map_link:
                seeker_map_html = f"\n📍 <a href='{provider_map_link}'>{p_map_label_for_seeker}</a>\n🌐 <code>{provider_map_link}</code>\n"
            else:
                seeker_map_html = ""

            if seeker_map_link:
                provider_map_html = f"\n📍 <a href='{seeker_map_link}'>{s_map_label_for_provider}</a>\n🌐 <code>{seeker_map_link}</code>\n"
            else:
                provider_map_html = ""

            # ========================================================================
            # TRANSIT DURATION / ETA COUNTDOWN ALIGNMENT
            # ========================================================================
            transit_mins = deal_dict.get('transit_duration_minutes') or locals().get('transit_mins')
            countdown_html_seeker = ""
            countdown_html_provider = ""

            if transit_mins:
                try:
                    t_mins = int(transit_mins)
                    if s_lang == "om":
                        eta_s = f"{t_mins // 60} sa'a {t_mins % 60} daq" if t_mins >= 60 else f"{t_mins} daq"
                        countdown_html_seeker = f"\n⏱️ <b>Konkolaataan Kan Qabame:</b> <code>{eta_s}</code> (Sa'aatii Gahumsaa + Daandii Fe'umsaa)\n"
                    elif s_lang == "am":
                        eta_s = f"{t_mins // 60} ሰዓት {t_mins % 60} ደቂቃ" if t_mins >= 60 else f"{t_mins} ደቂቃ"
                        countdown_html_seeker = f"\n⏱️ <b>ተሽከርካሪው የተያዘው ለ:</b> <code>{eta_s}</code> (የመድረሻ ጊዜ + የማጓጓዣ መስመር)\n"
                    else:
                        eta_s = f"{t_mins // 60} hrs {t_mins % 60} mins" if t_mins >= 60 else f"{t_mins} mins"
                        countdown_html_seeker = f"\n⏱️ <b>Vehicle Reserved For:</b> <code>{eta_s}</code> (ETA + Delivery Route)\n"
                except (ValueError, TypeError):
                    pass

                if p_lang == "om":
                    eta_p = f"{transit_mins // 60} sa'a {transit_mins % 60} daq" if transit_mins >= 60 else f"{transit_mins} daq"
                    countdown_html_provider = f"\n⏱️ <b>Konkolaataan Kan Qabame:</b> <code>{eta_p}</code> (Sa'aatii Gahumsaa + Daandii Fe'umsaa)\n"
                elif p_lang == "am":
                    eta_p = f"{transit_mins // 60} ሰዓት {transit_mins % 60} ደቂቃ" if transit_mins >= 60 else f"{transit_mins} ደቂቃ"
                    countdown_html_provider = f"\n⏱️ <b>ተሽከርካሪው የተያዘው ለ:</b> <code>{eta_p}</code> (የመድረሻ ጊዜ + የማጓጓዣ መስመር)\n"
                else:
                    eta_p = f"{transit_mins // 60} hrs {transit_mins % 60} mins" if transit_mins >= 60 else f"{transit_mins} mins"
                    countdown_html_provider = f"\n⏱️ <b>Vehicle Reserved For:</b> <code>{eta_p}</code> (ETA + Delivery Route)\n"

            SEEKER_HEADERS = {
                "am": "ክፍያ ተረጋግጧል!",
                "om": "Kaffaltiin Mirkanaa'eera!",
                "en": "Payment Confirmed!"
            }

            PROVIDER_HEADERS = {
                "am": "ግጥሚያው ተጠናቋል!" if not is_reverse_match else "የተገላቢጦሽ ግጥሚያ ተጠናቋል!",
                "om": "Walitti-dhufeenyi Xumurameera!" if not is_reverse_match else "Garfaggalchi Walitti-dhufeenyaa Xumurameera!",
                "en": "Match Finalized!" if not is_reverse_match else "Reverse Match Finalized!"
            }

            # ------------------------------------------------------------------------
            # EXPLICIT AGREED PRICE INTEGRATION IN DISPATCH BODIES & STRUCTURED DETAILS
            # ------------------------------------------------------------------------
            currency = str(
                deal.get("currency")
                or (listing.get("currency") if listing else None)
                or (request_data.get("currency") if request_data else None)
                or "ETB"
            ).strip().upper()

            # Helper to replace hardcoded Amharic/English/Oromo domain prefixes with the target user's preferred language
            def sanitize_title(title_text: str, target_lang: str) -> str:
                if not title_text:
                    return ""

                # Map of domain prefixes to clean localized domain terms
                prefix_translations = {
                    "om": {
                        "የጭነት": "Fe'umsa",
                        "የጉዞ": "Imala",
                        "Freight": "Fe'umsa",
                        "Ride": "Imala",
                        "Transport Route": "Kallattii Imalaa",
                        "የመጓጓዣ መንገድ": "Kallattii Imalaa"
                    },
                    "am": {
                        "Fe'umsa": "የጭነት",
                        "Imala": "የጉዞ",
                        "Freight": "የጭነት",
                        "Ride": "የጉዞ",
                        "Transport Route": "የመጓጓዣ መንገድ",
                        "Kallattii Imalaa": "የመጓጓዣ መንገድ"
                    },
                    "en": {
                        "የጭነት": "Freight",
                        "የጉዞ": "Ride",
                        "Fe'umsa": "Freight",
                        "Imala": "Ride",
                        "Kallattii Imalaa": "Transport Route",
                        "የመጓጓዣ መንገድ": "Transport Route"
                    },
                }

                clean_title = str(title_text)
                lang_map = prefix_translations.get(target_lang, prefix_translations["en"])

                for old_prefix, new_prefix in lang_map.items():
                    if clean_title.startswith(old_prefix):
                        clean_title = clean_title.replace(old_prefix, new_prefix, 1)

                return clean_title.strip()

            # Resolve seeker and provider languages safely for PyCharm analyzer
            s_lang = locals().get("s_lang", locals().get("seeker_lang", "en"))
            p_lang = locals().get("p_lang", locals().get("provider_lang", "en"))

            # HTML escape and sanitize asset titles for each target language
            seeker_title_am = html.escape(sanitize_title(seeker_title, "am"))
            seeker_title_om = html.escape(sanitize_title(seeker_title, "om"))
            seeker_title_en = html.escape(sanitize_title(seeker_title, "en"))

            provider_title_am = html.escape(sanitize_title(provider_title, "am"))
            provider_title_om = html.escape(sanitize_title(provider_title, "om"))
            provider_title_en = html.escape(sanitize_title(provider_title, "en"))

            # 🛠️ FIX: Selecting the appropriate title based on the user's language
            localized_seeker_title = (
                seeker_title_om if s_lang == "om"
                else seeker_title_am if s_lang == "am"
                else seeker_title_en
            )
            localized_provider_title = (
                provider_title_om if p_lang == "om"
                else provider_title_am if p_lang == "am"
                else provider_title_en
            )

            if is_reverse_match:
                seeker_body_templates = {
                    "am": (
                        f"ለተገላቢጦሽ ግጥሚያው የኮሚሽን ክፍያ በተሳካ ሁኔታ ተፈጽሟል:"
                        f" <b>{seeker_title_am}</b> (መለያ: <code>{deal_id[:8]}</code>)።\n💰"
                        f" <b>የተስማሙበት ዋጋ:</b> <code>{agreed_price_str_s}</code>"
                    ),
                    "om": (
                        f"Kaffaltiin komishinii garfaggalchaa milkaiinaan raawwatameera:"
                        f" <b>{seeker_title_om}</b> (Koodii: <code>{deal_id[:8]}</code>).\n💰"
                        f" <b>Gatii Walii Galaa:</b> <code>{agreed_price_str_s}</code>"
                    ),
                    "en": (
                        f"Commission settled successfully for reverse match request:"
                        f" <b>{seeker_title_en}</b> (Deal ID: <code>{deal_id[:8]}</code>).\n💰"
                        f" <b>Agreed Price:</b> <code>{agreed_price_str_s}</code>"
                    ),
                }
                provider_body_templates = {
                    "am": (
                        f"ለተገላቢጦሽ ግጥሚያዎ የፕላትፎርም ኮሚሽን ክፍያ ተፈጽሟል:"
                        f" <b>{provider_title_am}</b> (መለያ: <code>{deal_id[:8]}</code>)።\n💰"
                        f" <b>የተስማሙበት ዋጋ:</b> <code>{agreed_price_str_p}</code>"
                    ),
                    "om": (
                        f"Kaffaltiin komishinii garfaggalcha qabeenya keessaniif"
                        f" raawwatameera: <b>{provider_title_om}</b> (Koodii:"
                        f" <code>{deal_id[:8]}</code>).\n💰 <b>Gatii Walii Galaa:</b>"
                        f" <code>{agreed_price_str_p}</code>"
                    ),
                    "en": (
                        f"Reverse match commission settled for your asset proposal:"
                        f" <b>{provider_title_en}</b> (Deal ID: <code>{deal_id[:8]}</code>).\n💰"
                        f" <b>Agreed Price:</b> <code>{agreed_price_str_p}</code>"
                    ),
                }
                seeker_body_key = "dispatch_reverse_seeker_body"
                provider_body_key = "dispatch_reverse_provider_body"
                seeker_header_key = "dispatch_reverse_match_seeker_header"
                provider_header_key = "dispatch_reverse_match_provider_header"
            else:
                seeker_body_templates = {
                    "am": (
                        f"ለዚህ ንብረት የኮሚሽን ክፍያ በተሳካ ሁኔታ ተፈጽሟል: <b>{seeker_title_am}</b>"
                        f" (መለያ: <code>{deal_id[:8]}</code>)።\n💰 <b>የተስማሙበት ዋጋ:</b>"
                        f" <code>{agreed_price_str_s}</code>"
                    ),
                    "om": (
                        f"Kaffaltiin komishinii milkaiinaan raawwatameera:"
                        f" <b>{seeker_title_om}</b> (Koodii: <code>{deal_id[:8]}</code>).\n💰"
                        f" <b>Gatii Walii Galaa:</b> <code>{agreed_price_str_s}</code>"
                    ),
                    "en": (
                        f"Commission settled successfully for: <b>{seeker_title_en}</b> (Deal"
                        f" ID: <code>{deal_id[:8]}</code>).\n💰 <b>Agreed Price:</b>"
                        f" <code>{agreed_price_str_s}</code>"
                    ),
                }
                provider_body_templates = {
                    "am": (
                        f"የንብረት ፈላጊው ለዝርዝርዎ የፕላትፎርም ኮሚሽን ከፍሏል:"
                        f" <b>{provider_title_am}</b> (መለያ: <code>{deal_id[:8]}</code>)።\n💰"
                        f" <b>የተስማሙበት ዋጋ:</b> <code>{agreed_price_str_p}</code>"
                    ),
                    "om": (
                        f"Barbaadaan qabeenyaa kaffaltii komishinii dhaabbata keessanii"
                        f" raawwateera: <b>{provider_title_om}</b> (Koodii:"
                        f" <code>{deal_id[:8]}</code>).\n💰 <b>Gatii Walii Galaa:</b>"
                        f" <code>{agreed_price_str_p}</code>"
                    ),
                    "en": (
                        f"An asset seeker has settled the platform commission for your"
                        f" listing: <b>{provider_title_en}</b> (Deal ID:"
                        f" <code>{deal_id[:8]}</code>).\n💰 <b>Agreed Price:</b>"
                        f" <code>{agreed_price_str_p}</code>"
                    ),
                }
                seeker_body_key = "dispatch_seeker_body"
                provider_body_key = "dispatch_provider_body"
                seeker_header_key = "dispatch_payment_confirmed"
                provider_header_key = "dispatch_match_finalized"

            def_s_hdr = SEEKER_HEADERS.get(s_lang, SEEKER_HEADERS["en"])
            def_p_hdr = PROVIDER_HEADERS.get(p_lang, PROVIDER_HEADERS["en"])
            def_s_body = seeker_body_templates.get(s_lang, seeker_body_templates["en"])
            def_p_body = provider_body_templates.get(p_lang, provider_body_templates["en"])

            seeker_hdr_raw = _safe_get_text(s_lang, seeker_header_key, default=def_s_hdr)
            provider_hdr_raw = _safe_get_text(p_lang, provider_header_key, default=def_p_hdr)

            if s_lang != "en" and ("Payment Confirmed" in seeker_hdr_raw or "MISSING" in seeker_hdr_raw):
                seeker_hdr_raw = def_s_hdr
            if p_lang != "en" and ("Match Finalized" in provider_hdr_raw or "MISSING" in provider_hdr_raw):
                provider_hdr_raw = def_p_hdr

            seeker_hdr_clean = re.sub(r'^[✅🔄✨\s]+', '', seeker_hdr_raw).strip()
            provider_hdr_clean = re.sub(r'^[✅🔄✨\s]+', '', provider_hdr_raw).strip()

            hdr_icon = "🔄" if is_reverse_match else "✅"
            seeker_header_formatted = f"🏠🚗🏗️<b>SMA</b>│<b>{seeker_hdr_clean}</b>{hdr_icon}\n━━━━━━━━━━━━━━━━━━━━━━"
            provider_header_formatted = f"✨🏠🚗🏗️<b>SMA</b>│<b>{provider_hdr_clean}</b>{hdr_icon}\n━━━━━━━━━━━━━━━━━━━━━━"

            seeker_body = _safe_get_text(
                s_lang,
                seeker_body_key,
                default=def_s_body,
                asset_title=localized_seeker_title,  # ✅ Uses pre-sanitized, language-matched seeker title
                deal_id=deal_id[:8],
                brand=safe_brand,
                equipment_type=safe_equipment_type or safe_property_type,
                property_type=safe_property_type,
                property_use=safe_property_type,
                vehicle_type=safe_vehicle_type,
                make_model_year=safe_make_model_year,
                fuel_type=safe_fuel_type,
                price=agreed_price_str_s,
                agreed_price=agreed_price_str_s,
                currency=currency
            )

            provider_body = _safe_get_text(
                p_lang,
                provider_body_key,
                default=def_p_body,
                asset_title=localized_provider_title,  # ✅ Uses pre-sanitized, language-matched seeker title
                deal_id=deal_id[:8],
                brand=safe_brand,
                equipment_type=safe_equipment_type or safe_property_type,
                property_type=safe_property_type,
                property_use=safe_property_type,
                vehicle_type=safe_vehicle_type,
                make_model_year=safe_make_model_year,
                fuel_type=safe_fuel_type,
                price=agreed_price_str_p,
                agreed_price=agreed_price_str_p,
                currency=currency
            )

            if s_lang != "en" and "Commission settled successfully" in seeker_body:
                seeker_body = def_s_body
            if p_lang != "en" and "An asset seeker has settled" in provider_body:
                provider_body = def_p_body

            # Check if price is present in body strings; if missing, inject standalone price line into detail view
            price_lbl_s = "የተስማሙበት ዋጋ" if s_lang == "am" else "Gatii Walii Galaa" if s_lang == "om" else "Agreed Price"
            price_lbl_p = "የተስማሙበት ዋጋ" if p_lang == "am" else "Gatii Walii Galaa" if p_lang == "om" else "Agreed Price"

            s_has_price_in_body = any(
                kw in seeker_body for kw in ["💰", "የተስማሙበት ዋጋ", "Gatii Walii Galaa", "Agreed Price"])
            p_has_price_in_body = any(
                kw in provider_body for kw in ["💰", "የተስማሙበት ዋጋ", "Gatii Walii Galaa", "Agreed Price"])

            seeker_price_line = "" if s_has_price_in_body else f"💰 <b>{price_lbl_s}:</b> <code>{html.escape(agreed_price_str_s)}</code>\n"
            provider_price_line = "" if p_has_price_in_body else f"💰 <b>{price_lbl_p}:</b> <code>{html.escape(agreed_price_str_p)}</code>\n"

            def_s_footer = (
                "Yeroo gabaabduu keessatti bilbila isaanii eegaa, yookiin kallattiin isaaniif bilbilaa. Smart Match Africa fayyadamuu keessaniif galatoomaa!" if s_lang == "om" else
                "በቅርቡ ጥሪያቸውን ይጠብቁ ወይም ለመጀመር በቀጥታ ያግኙዋቸው። የስማርት ማች አፍሪካን (Smart Match Africa) አገልግሎት ስለተጠቀሙ እናመሰግናለን!" if s_lang == "am" else
                "Expect their call shortly, or contact them directly to get started. Thank you for using Smart Match Africa!"
            )

            def_p_footer = (
                "Yeroo gabaabduu keessatti bilbila isaanii eegaa, yookiin kallattiin isaaniif bilbilaa. Smart Match Africa fayyadamuu keessaniif galatoomaa!" if p_lang == "om" else
                "በቅርቡ ጥሪያቸውን ይጠብቁ ወይም ለመጀመር በቀጥታ ያግኙዋቸው። የስማርት ማች አፍሪካን (Smart Match Africa) አገልግሎት ስለተጠቀሙ እናመሰግናለን!" if p_lang == "am" else
                "Expect their call shortly, or contact them directly to get started. Thank you for using Smart Match Africa!"
            )

            seeker_footer = (_safe_get_text(s_lang, 'dispatch_seeker_footer', default=def_s_footer)).replace(
                "Smart Match Africa", "Smart Match Africa")
            provider_footer = (_safe_get_text(p_lang, 'dispatch_provider_footer', default=def_p_footer)).replace(
                "Smart Match Africa", "Smart Match Africa")

            cat_label_s = "ምድብ" if s_lang == "am" else "Gosa" if s_lang == "om" else "Category"
            cat_label_p = "ምድብ" if p_lang == "am" else "Gosa" if p_lang == "om" else "Category"

            def_s_phone = (
                f"📞 Teessoo Dhiheessaa: <code>{provider_phone}</code>" if s_lang == "om" else
                f"📞 የአቅራቢ ስልክ: <code>{provider_phone}</code>" if s_lang == "am" else
                f"📞 Provider Contact: <code>{provider_phone}</code>"
            )

            def_p_phone = (
                f"📞 Teessoo Maamilaa: <code>{seeker_phone}</code>" if p_lang == "om" else
                f"📞 የደንበኛ ስልክ: <code>{seeker_phone}</code>" if p_lang == "am" else
                f"📞 Client Contact: <code>{seeker_phone}</code>"
            )

            s_phone_msg = _safe_get_text(s_lang, 'dispatch_provider_phone', default=def_s_phone,
                                         phone=provider_phone)
            p_phone_msg = _safe_get_text(p_lang, 'dispatch_client_phone', default=def_p_phone, phone=seeker_phone)

            seeker_message = _clean_markdown_syntax(
                f"{seeker_header_formatted}\n\n"
                f"{seeker_body}\n\n"
                f"📂 <b>{cat_label_s}:</b> {html.escape(str(s_vname))}\n"
                f"{seeker_trans_str}"
                f"{seeker_price_line}"
                f"{seeker_logistics_str}"
                f"{explicit_capacity_s}"
                f"{countdown_html_seeker}"
                f"{s_phone_msg}\n"
                f"{seeker_map_html}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"{seeker_footer}"
            )

            provider_message = _clean_markdown_syntax(
                f"{provider_header_formatted}\n\n"
                f"{provider_body}\n\n"
                f"📂 <b>{cat_label_p}:</b> {html.escape(str(p_vname))}\n"
                f"{provider_trans_str}"
                f"{provider_price_line}"
                f"{provider_logistics_str}"
                f"{explicit_capacity_p}"
                f"{countdown_html_provider}"
                f"{p_phone_msg}\n"
                f"{provider_map_html}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"{provider_footer}"
            )

            # Resolve Chat IDs
            s_chat_id = await self._resolve_chat_id(supabase_client, target_seeker_id)
            p_chat_id = await self._resolve_chat_id(supabase_client, target_provider_id)

            # Build Reply Keyboards / Persistent Main Menu Keyboards
            reply_markup_s = MainMenuHandler.get_main_menu_keyboard(s_lang) if hasattr(MainMenuHandler,
                                                                                       "get_main_menu_keyboard") else None
            reply_markup_p = MainMenuHandler.get_main_menu_keyboard(p_lang) if hasattr(MainMenuHandler,
                                                                                       "get_main_menu_keyboard") else None

            dispatch_results = await asyncio.gather(
                self._safe_send_telegram_notification(
                    s_chat_id, seeker_message, presigned_url, reply_markup=reply_markup_s, role="Seeker"
                ),
                self._safe_send_telegram_notification(
                    p_chat_id, provider_message, presigned_url, reply_markup=reply_markup_p, role="Provider"
                ),
                return_exceptions=True
            )

            any_success = any(res is True for res in dispatch_results if not isinstance(res, Exception))

            if any_success or (not s_chat_id and not p_chat_id):
                try:
                    # 1. Fetch current deal record for synchronization
                    current_deal_record = {}
                    try:
                        deal_fetch_res = await supabase_client.table("deals").select(
                            "listing_id, request_id, seeker_id, provider_id, vertical_type, transaction_type"
                        ).eq("id", deal_id).maybe_single().execute()
                        if deal_fetch_res and getattr(deal_fetch_res, "data", None):
                            current_deal_record = deal_fetch_res.data
                    except Exception as fetch_deal_err:
                        logger.debug(f"Could not pre-fetch deal IDs for status synchronization: {fetch_deal_err}")

                    # 2. Update Deals Table Status to finalized & record payment reference
                    update_payload = {
                        "dispatch_sent": True,
                        "deal_state": "finalized",
                        "status": "finalized",
                        "updated_at": datetime.now(timezone.utc).isoformat()
                    }
                    if payment_reference:
                        update_payload["payment_reference"] = payment_reference

                    await supabase_client.table("deals").update(update_payload).eq("id", deal_id).execute()

                    # 3. 🎯 Seamlessly Sync Provider Listings & Seeker Requests via Centralized Helper
                    if hasattr(self, "_sync_listing_status_for_deal"):
                        await self._sync_listing_status_for_deal(deal_id=deal_id, deal_record=current_deal_record)
                    else:
                        logger.warning(
                            f"⚠️ `_sync_listing_status_for_deal` method not found on handler instance for deal {deal_id}.")

                    logger.info(
                        f"🎯 Payment processing & multi-table finalization completed successfully for Deal {deal_id}.")
                    return True
                except Exception as db_err:
                    logger.error(
                        f"Failed to update database records after payment processing for deal {deal_id}: {db_err}",
                        exc_info=True
                    )
                    return True

            return False

        except Exception as process_err:
            logger.error(f"Error processing successful commission payment for deal {deal_id}: {process_err}",
                         exc_info=True)
            return False

    # ==========================================
    # ROUTE TO MAIN MENU HANDLER (Refactored)
    # ==========================================
    async def route_to_main_menu(
            self,
            update: Optional[Update] = None,
            context: Optional[ContextTypes.DEFAULT_TYPE] = None,
            custom_msg: Optional[str] = None,
            suppress_invalid_prompt: bool = False
    ) -> int:
        """
        Safely clears all active session state keys, cleans up orphan inline buttons,
        and returns the user to the persistent main menu.
        """
        lang = self._get_lang(context) if hasattr(self, '_get_lang') else 'en'
        reply_markup = None

        try:
            if MainMenuHandler and hasattr(MainMenuHandler, "get_persistent_keyboard"):
                reply_markup = MainMenuHandler.get_persistent_keyboard(lang)
        except Exception as e:
            logger.warning(f"Could not load persistent keyboard: {e}")

        # Phase 1: Comprehensive State Sanitization
        if context and hasattr(context, 'user_data') and context.user_data is not None:
            # Clear temporary flow keys
            for key in getattr(self, 'TEMP_FLOW_KEYS', []):
                context.user_data.pop(key, None)

            # Clear active workflow and transactional state keys
            active_keys = (
                "waiting_for_input", "active_negotiation", "in_flow", "current_flow",
                "deal_id", "active_deal_id", "payment_state", "pending_payment_deal_id"
            )
            for key in active_keys:
                context.user_data.pop(key, None)

            stale_flow_keys = [k for k in context.user_data.keys() if k.startswith("flow_step_")]
            for key in stale_flow_keys:
                context.user_data.pop(key, None)

        # Phase 2: Safely Clean Up Inline Callback Message
        if update and update.callback_query:
            try:
                await update.callback_query.answer()
            except Exception:
                pass

            if update.callback_query.message and isinstance(update.callback_query.message, Message):
                try:
                    await update.callback_query.message.delete()
                except Exception:
                    try:
                        # Fallback: Strip inline buttons if deletion is restricted by Telegram
                        await update.callback_query.edit_message_reply_markup(reply_markup=None)
                    except Exception:
                        pass

        fallback_msg = (
                get_text(lang, "msg_return_root")
                or "🏠 <b>Main Menu</b>\n\nSelect an option from the menu below to continue:"
        )

        flow_suppressed = False
        if context and hasattr(context, 'user_data') and context.user_data is not None:
            flow_suppressed = context.user_data.pop("_suppress_invalid_prompt", False) or suppress_invalid_prompt

        if (
                not custom_msg
                and update
                and update.callback_query is None
                and not flow_suppressed
                and update.message
                and update.message.text
                and not update.message.text.startswith('/')
        ):
            custom_msg = f"⚠️ Invalid input detected.\n\n{fallback_msg}"

        async def deliver_message(text_to_send: str):
            if update and update.effective_chat:
                # ALWAYS send a new message for persistent ReplyKeyboardMarkup
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=text_to_send,
                    reply_markup=reply_markup,
                    parse_mode="HTML"
                )

        if custom_msg:
            await deliver_message(custom_msg)
            return ConversationHandler.END

        # Delegate to MainMenuHandler
        menu_handler = context.bot_data.get("main_menu_handler") if (context and hasattr(context, 'bot_data')) else None
        if update and menu_handler and hasattr(menu_handler, 'show_main_menu'):
            try:
                await menu_handler.show_main_menu(update, context)
                return ConversationHandler.END
            except Exception as e:
                logger.error(f"Failed to route to bot_data main menu handler: {e}")

        await deliver_message(fallback_msg)
        return ConversationHandler.END


# ============================================================================
# WEBHOOK INGRESS HANDLER ENGINE
# ============================================================================

router = APIRouter()
WEBHOOK_SECRET = os.getenv("CHAPA_WEBHOOK_SECRET", "")


# ============================================================================
# FASTAPI ROUTES: INTERNATIONAL SETTLEMENT
# ============================================================================

async def process_international_settlement(
        payload: Dict[str, Any],
        db_manager: Optional[Any] = None,
        queue_manager: Optional[Any] = None
) -> None:
    """
    Production-grade background worker for processing validated USD international settlements,
    incorporating established vertical-specific commission calculation rules (housing, vehicle, equipment, ride, freight).
    """
    tx_ref = payload.get("tx_ref")
    payment_status = str(payload.get("status", "")).lower()
    amount_raw = payload.get("amount")
    currency = str(payload.get("currency", "")).upper()

    if not tx_ref:
        logger.error("❌ International settlement aborted: Payload missing 'tx_ref' identifier.")
        return

    if currency != "USD":
        logger.warning(
            f"⚠️ International settlement bypassed: Received currency '{currency}' (expected USD) | TX: {tx_ref}"
        )
        return

    if payment_status not in ["success", "successful", "paid"]:
        logger.warning(
            f"⚠️ International settlement ignored: Payment status is '{payment_status}' | TX: {tx_ref}"
        )
        return

    try:
        amount = float(amount_raw) if amount_raw is not None else 0.0
    except (ValueError, TypeError):
        amount = 0.0
        logger.warning(f"Malformed amount value '{amount_raw}' for TX: {tx_ref}. Defaulted to 0.0")

    logger.info(f"🔄 Processing USD settlement | TX: {tx_ref} | Amount: ${amount:.2f} USD")

    try:
        vertical_type = "vehicle"
        _agreed_price = 0.0
        deal_id = None
        calculated_commission_etb = 0.0

        if db_manager and getattr(db_manager, "supabase", None):
            # 1. Fetch deal metadata associated with the transaction reference
            deal_res = await db_manager.supabase.table("deals") \
                .select("id, vertical_type, agreed_price, currency") \
                .eq("payment_reference", tx_ref) \
                .execute()

            if deal_res.data:
                deal_record = deal_res.data[0]
                deal_id = deal_record.get("id")
                vertical_type = deal_record.get("vertical_type", "vehicle")
                agreed_price = float(deal_record.get("agreed_price") or 0.0)
                deal_currency = str(deal_record.get("currency") or "ETB").upper()

                # 2. Delegate conversion and commission calculation to BillingService
                from src.smartmatch.services.billing_service import BillingService
                billing_service = BillingService(db_client=db_manager)

                conversion_res = await billing_service.convert(
                    amount=agreed_price,
                    from_currency=deal_currency,
                    to_currency="ETB"
                )
                base_agreed_price_etb = conversion_res["converted_amount"]

                vertical_normalization_map = {
                    "freight_ride": "freight_ride", "freight": "freight_ride", "logistics": "freight_ride",
                    "ride": "freight_ride", "housing": "housing", "real_estate": "housing",
                    "equipment": "equipment", "equipment_rental": "equipment",
                    "vehicle": "vehicle", "automotive": "vehicle"
                }
                normalized_domain = vertical_normalization_map.get(str(vertical_type).lower().strip(), "vehicle")

                if normalized_domain == "freight_ride":
                    calculated_commission_etb = billing_service.calculate_logistics_commission(base_agreed_price_etb)
                else:
                    calculated_commission_etb = billing_service.calculate_asset_commission(base_agreed_price_etb)

                logger.info(
                    f"📊 Domain: {normalized_domain} | Agreed Price: {agreed_price} {deal_currency} | "
                    f"Base Agreed Price (ETB): {base_agreed_price_etb:.2f} | "
                    f"Calculated Commission: {calculated_commission_etb:.2f} ETB"
                )

            # 3. Update transaction and deal ledger states
            update_payload = {
                "status": "completed",
                "payment_status": "paid",
                "settled_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "amount_usd": amount,
                "vertical_type": vertical_type,
                "calculated_commission_etb": calculated_commission_etb
            }

            result = await db_manager.supabase.table("transactions") \
                .update(update_payload) \
                .eq("tx_ref", tx_ref) \
                .execute()

            if hasattr(result, "data") and not result.data:
                await (
                    db_manager.supabase.table("deals")
                    .update({
                        "payment_status": "paid",
                        "updated_at": datetime.now(timezone.utc).isoformat()
                    })
                    .eq("payment_reference", tx_ref)
                    .execute()
                )

            if deal_id:
                await (
                    db_manager.supabase.table("commissions")
                    .update({
                        "status": "paid",
                        "commission_fee": calculated_commission_etb,
                        "updated_at": datetime.now(timezone.utc).isoformat()
                    })
                    .eq("deal_id", str(deal_id))
                    .execute()
                )

            logger.info(
                f"✅ Supabase ledger state marked as 'completed' with verified vertical commission | TX: {tx_ref}")
        else:
            logger.warning(f"⚠️ Supabase client reference unavailable on db_manager. Skipping DB update | TX: {tx_ref}")

        event_payload = json.dumps({
            "event": "settlement_completed",
            "tx_ref": tx_ref,
            "amount": amount,
            "currency": currency,
            "vertical_type": vertical_type,
            "calculated_commission_etb": calculated_commission_etb,
            "payload": payload,
            "timestamp": datetime.now(timezone.utc).isoformat()
        })

        event_published = False

        if db_manager:
            async def _publish_to_redis(redis_client):
                return await redis_client.rpush("settlement_completed_queue", event_payload)

            redis_res = await db_manager.execute_redis(_publish_to_redis)
            if redis_res is not None:
                event_published = True
                logger.info(f"🚀 Settlement event successfully published to Redis queue | TX: {tx_ref}")

        if not event_published and queue_manager:
            priority_val = getattr(queue_manager, "TaskPriority", None)
            critical_priority = priority_val.CRITICAL if priority_val else 0

            await queue_manager.enqueue(
                func=_fallback_dispatch_logger,
                priority=critical_priority,
                tx_ref=tx_ref,
                amount=amount
            )
            logger.info(f"🚀 Settlement event buffered via QueueManager priority queue | TX: {tx_ref}")

        logger.info(f"🎉 International settlement lifecycle completed successfully for ${amount:.2f} USD | TX: {tx_ref}")

    except Exception as exc:
        logger.error(
            f"❌ CRITICAL EXECUTION FAILURE during settlement processing for TX {tx_ref}: {str(exc)}",
            exc_info=True
        )

        if db_manager and getattr(db_manager, "supabase", None):
            try:
                dlq_record = {
                    "tx_ref": tx_ref,
                    "payload": json.dumps(payload),
                    "error_message": str(exc),
                    "failed_at": datetime.now(timezone.utc).isoformat()
                }
                await db_manager.supabase.table("dead_letter_queue").insert(dlq_record).execute()
                logger.info(f"📥 Transaction failure persisted to Dead-Letter Queue (DLQ) | TX: {tx_ref}")
            except Exception as dlq_err:
                logger.critical(f"🔥 DLQ persistence failed for TX {tx_ref}: {str(dlq_err)}")


async def _fallback_dispatch_logger(tx_ref: str, amount: float) -> None:
    logger.info(f"Fulfillment dispatch executed via fallback queue for TX: {tx_ref} (${amount:.2f})")


# ============================================================================
# TELEGRAM BOT: PAYMENT SELECTION HANDLER
# ============================================================================
async def handle_payment_selection_callback(
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        webhook_handler=None
) -> None:
    """
    Processes payment method selection by delegating commission fee calculation,
    currency conversions, and database recording strictly to BillingService.
    """
    query = update.callback_query
    if query:
        try:
            await query.answer()
        except Exception as e:
            logger.warning(f"Failed to answer payment selection query: {e}")

    # Resolve WebhookHandler dependency
    if webhook_handler is None:
        webhook_handler = (
                context.bot_data.get("webhook_handler")
                or getattr(context.application, "bot_data", {}).get("webhook_handler")
        )

    db = getattr(webhook_handler, "db", None) or getattr(webhook_handler, "db_client", None)
    if not webhook_handler or not db:
        logger.error("handle_payment_selection_callback: Valid WebhookHandler or DB instance unavailable.")
        if query and query.message:
            await query.edit_message_text("❌ Payment service is temporarily unavailable. Please try again later.")
        return

    data = query.data
    user = query.from_user
    billing_service = getattr(webhook_handler, "billing_service", None)

    try:
        if data.startswith("pay_local_") or data.startswith("pay_intl_"):
            is_local = data.startswith("pay_local_")
            deal_id = data.replace("pay_local_", "") if is_local else data.replace("pay_intl_", "")
            target_currency = "ETB" if is_local else "USD"

            # 1. Fetch deal record from 'deals' table with fallbacks
            deal_data = await db.get_deal_by_id(deal_id) if hasattr(db, "get_deal_by_id") else None
            if not deal_data:
                res = await db.table("deals").select("*").eq("id", deal_id).maybe_single().execute()
                deal_data = getattr(res, "data", {}) or {}

            if not deal_data:
                req_res = await db.table("seeker_requests").select("*").eq("id", deal_id).maybe_single().execute()
                deal_data = getattr(req_res, "data", {}) or {}

            if not deal_data:
                list_res = await db.table("provider_listings").select("*").eq("id", deal_id).maybe_single().execute()
                deal_data = getattr(list_res, "data", {}) or {}

            if not deal_data:
                raise ValueError(f"Could not locate active deal record for ID: {deal_id}")

            # 2. Extract gross agreed_price as the single source of truth
            agreed_price = float(
                deal_data.get("agreed_price")
                or deal_data.get("current_price")
                or deal_data.get("price")
                or deal_data.get("max_budget")
                or deal_data.get("gross_amount")
                or 0.0
            )

            if agreed_price <= 0:
                raise ValueError(f"Invalid agreed contract price found for deal {deal_id}: {agreed_price}")

            # 3. Delegate checkout orchestration to BillingService
            if billing_service:
                checkout_url = await billing_service.initiate_commission_billing(
                    deal_id=str(deal_id),
                    agreed_price=agreed_price,
                    seeker_id=user.id,
                    target_payment_currency=target_currency
                )
            else:
                # Direct fallback initialization
                tx_ref = f"sm_{deal_id}_{int(datetime.now().timestamp())}"
                email = getattr(user, "email", None) or f"user_{user.id}@smartmatch.africa"
                first_name = user.first_name or "Client"

                if is_local:
                    checkout_url = await webhook_handler.initialize_ethiopian_payment(
                        amount=agreed_price,
                        email=email,
                        first_name=first_name,
                        tx_ref=tx_ref,
                        currency="ETB",
                        custom_title="Smart Match Africa - Local Wallet Payment"
                    )
                else:
                    checkout_url = await webhook_handler.initialize_dollar_payment(
                        amount=agreed_price,
                        email=email,
                        first_name=first_name,
                        tx_ref=tx_ref,
                        currency="USD",
                        custom_title="Smart Match Africa - Global Card Payment"
                    )

            # 4. Render response message and inline action button
            if is_local:
                button_label = "🇪🇹 Open Telebirr / CBE Checkout"
                header_text = "🔗 <b>Local Payment Ready</b>\n\nClick the secure button below to complete your payment."
            else:
                button_label = "🌍 Open Global Checkout"
                header_text = "🔗 <b>International Payment Ready</b>\n\nClick the secure button below to pay via Visa or Mastercard."

            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton(button_label, url=checkout_url)]
            ])

            await query.edit_message_text(
                text=f"{header_text}\n\n<b>Checkout Currency:</b> {target_currency}",
                reply_markup=keyboard,
                parse_mode="HTML"
            )

    except Exception as err:
        logger.error(f"Failed to process payment selection callback: {err}", exc_info=True)
        if query and query.message:
            await query.edit_message_text("❌ Failed to generate payment link. Please try again or contact support.")


class WebhookHandler(DispatchService):
    """
    Production-grade edge handler and payment gateway orchestrator.
    """

    def __init__(
            self,
            billing_service: 'BillingService',
            db_manager: 'DatabaseManager',
            queue_manager: 'QueueManager',
            telegram_app: Any,
            webhook_secret: Optional[str] = None
    ):
        self.billing = billing_service
        self.billing_service = billing_service
        self.db = db_manager
        self.queue = queue_manager
        self.telegram_app = telegram_app
        self.dispatch_service = DispatchService()
        self.dispatch_service.db = db_manager
        self.dispatch_service.telegram_app = telegram_app

        # Strong reference set to prevent asyncio background task garbage collection
        self._background_tasks: Set[asyncio.Task] = set()

        # Resolve the bot instance
        self.bot = getattr(telegram_app, "bot", None) if telegram_app else None

        self.secret_key = settings.CHAPA_SECRET_KEY
        self.webhook_secret = webhook_secret or getattr(settings, 'CHAPA_WEBHOOK_SECRET', self.secret_key)
        self.chapa_init_url = "https://api.chapa.co/v1/transaction/initialize"
        self.chapa_verify_base = "https://api.chapa.co/v1/transaction/verify/"

    async def initialize_payment(
            self,
            *args: Any,
            amount: float = 0.0,
            email: str = "",
            first_name: str = "",
            tx_ref: str = "",
            currency: str = "ETB",
            custom_title: Optional[str] = None,
            deal_id: Optional[str] = None,
            **kwargs: Any
    ) -> str:
        """
        Unified gateway router for Chapa payment initialization supporting ETB, USD,
        and flexible argument forwarding for backward compatibility.
        """
        # Map positional arguments if passed via *args
        arg_names = ["amount", "email", "first_name", "tx_ref", "currency", "custom_title", "deal_id"]
        for name, val in zip(arg_names, args):
            if name == "amount":
                amount = val
            elif name == "email":
                email = val
            elif name == "first_name":
                first_name = val
            elif name == "tx_ref":
                tx_ref = val
            elif name == "currency":
                currency = val
            elif name == "custom_title":
                custom_title = val
            elif name == "deal_id":
                deal_id = val

        # Override or supplement with keyword arguments via **kwargs
        amount = kwargs.get("amount", amount)
        email = kwargs.get("email", email)
        first_name = kwargs.get("first_name", first_name)
        tx_ref = kwargs.get("tx_ref", tx_ref)
        currency = kwargs.get("currency", currency)
        custom_title = kwargs.get("custom_title", custom_title)
        deal_id = kwargs.get("deal_id", deal_id)

        gateway_currency = (currency or "ETB").upper()

        try:
            # Ensure amount is rounded to 2 decimal places for payment gateways
            clean_amount = round(float(amount), 2)

            logger.info(
                f"Initializing Chapa payment link" + (f" for Deal {deal_id}" if deal_id else "") +
                f". Amount: {clean_amount} {gateway_currency}"
            )

            if gateway_currency == "USD":
                try:
                    checkout_url = await self.initialize_dollar_payment(
                        amount=clean_amount,
                        email=email,
                        first_name=first_name,
                        tx_ref=tx_ref,
                        custom_title=custom_title
                    )
                except Exception as usd_err:
                    logger.warning(f"USD Chapa gateway unreachable: {usd_err}. Falling back to ETB conversion.")
                    conversion_result = await self.billing.convert(
                        amount=clean_amount,
                        from_currency="USD",
                        to_currency="ETB"
                    )
                    etb_amount = round(float(conversion_result.get("converted_amount", clean_amount * 155.42)), 2)
                    checkout_url = await self.initialize_ethiopian_payment(
                        amount=etb_amount,
                        email=email,
                        first_name=first_name,
                        tx_ref=f"{tx_ref}_fb",
                        currency="ETB",
                        custom_title=custom_title or "SMA Fallback Pay"
                    )
            else:
                checkout_url = await self.initialize_ethiopian_payment(
                    amount=clean_amount,
                    email=email,
                    first_name=first_name,
                    tx_ref=tx_ref,
                    currency=gateway_currency,
                    custom_title=custom_title
                )

            return checkout_url

        except Exception as he:
            logger.critical(f"Chapa gateway refusal or network timeout: {str(he)}")
            raise RuntimeError("Payment infrastructure service is temporarily unreachable.")

    # ============================================================================
    # UPDATED WEBHOOK HANDLER: DOLLAR & ETHIOPIAN PAYMENT INITIALIZATION
    # ============================================================================
    async def initialize_ethiopian_payment(
            self,
            amount: float,
            email: str,
            first_name: str,
            tx_ref: str,
            currency: str = "ETB",
            custom_title: Optional[str] = None,
            # Updated to fallback to Render's native URL or a clean production default
            base_url: str = (
                    os.environ.get("BASE_URL") or
                    os.environ.get("RENDER_EXTERNAL_URL") or
                    "https://guzoplus.onrender.com"
            )
    ) -> str:
        headers = {
            "Authorization": f"Bearer {self.secret_key}",
            "Content-Type": "application/json"
        }

        source_currency = (currency or "ETB").upper()
        clean_amount = round(float(amount), 2)

        if source_currency != "ETB":
            conversion_result = await self.billing.convert(
                amount=clean_amount,
                from_currency=source_currency,
                to_currency="ETB"
            )
            chapa_amount = round(float(conversion_result.get("converted_amount", clean_amount)), 2)
        else:
            chapa_amount = clean_amount

        formatted_chapa_amount = f"{chapa_amount:.2f}"
        callback_url = f"{base_url}/payment-callback"

        # Enforce Chapa strict limits: title <= 16 chars, description <= 50 chars
        safe_title = str(custom_title or "SMA Commission")[:16]
        safe_description = str("Smart Match Africa Payment Portal")[:50]

        payload = {
            "amount": formatted_chapa_amount,
            "currency": "ETB",
            "email": email,
            "first_name": first_name,
            "tx_ref": tx_ref,
            "callback_url": callback_url,
            "meta": {
                "original_price": str(clean_amount),
                "original_currency": source_currency,
                "converted_etb_amount": formatted_chapa_amount
            },
            "customization": {
                "title": safe_title,
                "description": safe_description
            }
        }

        max_retries = 3
        backoff_factor = 1.5
        response = None

        for attempt in range(1, max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.post(self.chapa_init_url, json=payload, headers=headers)
                    break
            except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.NetworkError, httpx.TimeoutException) as net_err:
                if attempt == max_retries:
                    logger.error(f"Chapa initialization network timeout after {max_retries} attempts: {net_err}")
                    raise HTTPException(status_code=500, detail="Payment gateway unreachable")
                logger.warning(f"Chapa connection attempt {attempt} timed out/failed, retrying... Error: {net_err}")
                await asyncio.sleep(backoff_factor ** attempt)
            except Exception as e:
                logger.error(f"Unexpected error initializing Chapa payment: {str(e)}")
                raise HTTPException(status_code=500, detail="Payment gateway unreachable")

        if response.status_code != 200:
            logger.error(f"Chapa API error: {response.text}")
            raise HTTPException(status_code=400, detail="Chapa initialization failed")

        data = response.json()
        return data["data"]["checkout_url"]

    async def initialize_dollar_payment(
            self,
            amount: float,
            email: str,
            first_name: str,
            tx_ref: str,
            custom_title: Optional[str] = None,
            # Updated to fallback to Render's native URL or a clean production default
            base_url: str = (
                    os.environ.get("BASE_URL") or
                    os.environ.get("RENDER_EXTERNAL_URL") or
                    "https://guzoplus.onrender.com"
            )
    ) -> str:
        """
        Initializes direct USD payment invoices with Chapa for international card / PayPal payers,
        preserving exact USD amounts without forcing unexpected ETB conversion multipliers.
        """
        headers = {
            "Authorization": f"Bearer {self.secret_key}",
            "Content-Type": "application/json"
        }

        clean_amount = round(float(amount), 2)
        formatted_chapa_amount = f"{clean_amount:.2f}"
        callback_url = f"{base_url}/payment-callback"

        safe_title = str(custom_title or "SMA USD Pay")[:16]
        safe_description = str("Smart Match Africa International Portal")[:50]

        payload = {
            "amount": formatted_chapa_amount,
            "currency": "USD",
            "email": email,
            "first_name": first_name,
            "tx_ref": tx_ref,
            "callback_url": callback_url,
            "meta": {
                "original_price": str(clean_amount),
                "original_currency": "USD",
                "settlement_currency": "USD"
            },
            "customization": {
                "title": safe_title,
                "description": safe_description
            }
        }

        max_retries = 3
        backoff_factor = 1.5
        response = None

        for attempt in range(1, max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.post(self.chapa_init_url, json=payload, headers=headers)
                    break
            except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.NetworkError, httpx.TimeoutException) as net_err:
                if attempt == max_retries:
                    logger.error(
                        f"Chapa USD initialization network timeout after {max_retries} attempts: {net_err}")
                    raise HTTPException(status_code=500, detail="Payment gateway unreachable")
                logger.warning(
                    f"Chapa USD connection attempt {attempt} timed out/failed, retrying... Error: {net_err}")
                await asyncio.sleep(backoff_factor ** attempt)
            except Exception as e:
                logger.error(f"Unexpected error initializing Chapa USD payment: {str(e)}")
                raise HTTPException(status_code=500, detail="Payment gateway unreachable")

        if response.status_code != 200:
            logger.error(f"Chapa API error (USD): {response.text}")
            raise HTTPException(status_code=400, detail="Chapa USD initialization failed")

        data = response.json()
        return data["data"]["checkout_url"]

    @staticmethod
    async def handle_payment_selection_callback(
            update: Update,
            context: ContextTypes.DEFAULT_TYPE,
            webhook_handler=None
    ) -> None:
        query = update.callback_query
        if query:
            try:
                await query.answer()
            except Exception as e:
                logger.warning(f"Failed to answer payment selection query: {e}")

        if webhook_handler is None:
            webhook_handler = (
                    context.bot_data.get("webhook_handler")
                    or getattr(context.application, "bot_data", {}).get("webhook_handler")
            )

        db = getattr(webhook_handler, "db", None) or getattr(webhook_handler, "db_client", None)
        if not webhook_handler or not db:
            logger.error("handle_payment_selection_callback: Valid WebhookHandler or DB instance unavailable.")
            if query and query.message:
                await query.edit_message_text("❌ Payment service is temporarily unavailable. Please try again later.")
            return

        data = query.data
        user = query.from_user
        billing_service = getattr(webhook_handler, "billing_service", None) or getattr(webhook_handler, "billing", None)

        try:
            if data.startswith("pay_local_") or data.startswith("pay_intl_"):
                is_local = data.startswith("pay_local_")
                deal_id = data.replace("pay_local_", "") if is_local else data.replace("pay_intl_", "")
                target_currency = "ETB" if is_local else "USD"

                deal_data = await db.get_deal_by_id(deal_id) if hasattr(db, "get_deal_by_id") else None
                if not deal_data:
                    res = await db.table("deals").select("*").eq("id", deal_id).maybe_single().execute()
                    deal_data = getattr(res, "data", {}) or {}

                if not deal_data:
                    req_res = await db.table("seeker_requests").select("*").eq("id", deal_id).maybe_single().execute()
                    deal_data = getattr(req_res, "data", {}) or {}

                if not deal_data:
                    list_res = await db.table("provider_listings").select("*").eq("id",
                                                                                  deal_id).maybe_single().execute()
                    deal_data = getattr(list_res, "data", {}) or {}

                if not deal_data:
                    raise ValueError(f"Could not locate active deal record for ID: {deal_id}")

                agreed_price = float(
                    deal_data.get("agreed_price")
                    or deal_data.get("current_price")
                    or deal_data.get("price")
                    or deal_data.get("max_budget")
                    or deal_data.get("gross_amount")
                    or 0.0
                )

                if agreed_price <= 0:
                    raise ValueError(f"Invalid agreed contract price found for deal {deal_id}: {agreed_price}")

                checkout_url = None
                tx_ref = f"sm_{deal_id}_{int(datetime.now().timestamp())}"

                if billing_service:
                    try:
                        checkout_url = await billing_service.initiate_commission_billing(
                            deal_id=str(deal_id),
                            agreed_price=agreed_price,
                            seeker_id=user.id,
                            target_payment_currency=target_currency
                        )
                    except Exception as bill_err:
                        logger.warning(
                            f"Billing service initiation failed: {bill_err}. Falling back to manual handler.")

                if not checkout_url:
                    tx_ref = f"sm_{deal_id[:8]}_{int(datetime.now().timestamp())}"
                    email = getattr(user, "email", None) or f"user_{user.id}@smartmatch.africa"
                    first_name = user.first_name or "Client"

                    # FIX: Persist payment_reference into deals table so webhooks can locate the deal record
                    db_client = getattr(webhook_handler, "db", None) or getattr(webhook_handler, "db_client", None)
                    if db_client:
                        supabase_table = db_client.supabase.table("deals") if hasattr(db_client,
                                                                                      "supabase") else db_client.table(
                            "deals")
                        try:
                            await supabase_table.update({"payment_reference": tx_ref}).eq("id", str(deal_id)).execute()
                        except Exception as db_err:
                            logger.warning(f"Could not persist payment_reference {tx_ref} to deal {deal_id}: {db_err}")

                    if is_local:
                        checkout_url = await webhook_handler.initialize_ethiopian_payment(
                            amount=agreed_price, email=email, first_name=first_name, tx_ref=tx_ref, currency="ETB"
                        )
                    else:
                        try:
                            checkout_url = await webhook_handler.initialize_dollar_payment(
                                amount=agreed_price, email=email, first_name=first_name, tx_ref=tx_ref
                            )
                        except Exception as usd_fallback_err:
                            logger.error(
                                f"USD gateway failed in callback ({usd_fallback_err}), falling back to ETB conversion.")
                            conv_res = await webhook_handler.billing.convert(
                                amount=agreed_price, from_currency="USD", to_currency="ETB"
                            ) if hasattr(webhook_handler, "billing") and hasattr(webhook_handler.billing,
                                                                                 "convert") else {}

                            fallback_amount = conv_res.get("converted_amount", agreed_price * 155.42) if isinstance(
                                conv_res, dict) else agreed_price * 155.42
                            etb_amount = round(float(fallback_amount), 2)

                            tx_ref = f"{tx_ref}_fb"
                            checkout_url = await webhook_handler.initialize_ethiopian_payment(
                                amount=etb_amount, email=email, first_name=first_name, tx_ref=tx_ref, currency="ETB",
                                custom_title="SMA Fallback"
                            )
                            target_currency = "ETB"
                            is_local = True

                db_client = getattr(webhook_handler, "db", None) or getattr(webhook_handler, "db_client", None)
                if hasattr(db_client, "supabase"):
                    db_client = db_client.supabase

                if db_client and hasattr(db_client, "table"):
                    try:
                        await db_client.table("deals").update({
                            "payment_tx_ref": tx_ref,
                            "payment_reference": tx_ref,
                            "currency": target_currency
                        }).eq("id", str(deal_id)).execute()
                    except Exception as update_err:
                        logger.warning(f"Failed to update payment_tx_ref on deal {deal_id}: {update_err}")

                display_fee_str = f"{agreed_price:,.2f} {target_currency}"
                if db_client and hasattr(db_client, "table"):
                    inv_res = await db_client.table('invoices').select('amount, currency').eq('deal_id',
                                                                                              str(deal_id)).order(
                        'created_at', desc=True).limit(1).execute()
                    if inv_res and inv_res.data:
                        inv = inv_res.data[0]
                        display_fee_str = f"{float(inv['amount']):,.2f} {inv['currency']}"

                if is_local:
                    button_label = "🇪🇹 Open Telebirr / CBE Checkout"
                    header_text = f"🔗 <b>Local Payment Ready</b>\n\nCommission Fee: <b>{display_fee_str}</b>\n\nClick the secure button below to complete your payment."
                else:
                    button_label = "🌍 Open Global USD Checkout"
                    header_text = f"🔗 <b>International Payment Ready (USD)</b>\n\nExact Commission: <b>{display_fee_str}</b> (No hidden conversion rate)\n\nClick the secure button below to pay via Visa, Mastercard, or PayPal."

                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton(button_label, url=checkout_url)]
                ])

                await query.edit_message_text(
                    text=header_text,
                    reply_markup=keyboard,
                    parse_mode="HTML"
                )

        except Exception as err:
            logger.error(f"Failed to process payment selection callback: {err}", exc_info=True)
            if query and query.message:
                await query.edit_message_text("❌ Failed to generate payment link. Please try again or contact support.")

    async def reconcile_unfulfilled_paid_deals(self) -> int:
        """
        SERVER RECOVERY WORKER:
        Executes on server restart to scan for deals that were paid but missed
        reservation confirmation or dispatch notification due to a restart/crash.
        """
        logger.info("🔄 Initiating startup recovery: Scanning for paid deals missing dispatch...")
        supabase_client = getattr(self.db, 'supabase', self.db)
        try:
            # Query deals where payment status is paid
            res = await supabase_client.table("deals").select("*").eq("payment_status", "paid").execute()
            if not res or not hasattr(res, 'data') or not res.data:
                logger.info("✅ Startup recovery complete: No unfulfilled paid deals found.")
                return 0

            recovered_count = 0
            for deal in res.data:
                # Skip deals that are already completely dispatched and finalized
                if deal.get("dispatch_sent") is True and deal.get("deal_state") == "finalized":
                    continue

                deal_id = str(deal.get("id"))
                # FIX: Check 'payment_reference' column first, with fallbacks for legacy schemas
                tx_ref = deal.get("payment_reference") or deal.get("payment_tx_ref") or deal.get("tx_ref") or deal_id
                logger.info(f"🔄 Recovering unfulfilled paid Deal {deal_id} (TX Ref: {tx_ref})...")

                event_data = {
                    "tx_ref": tx_ref,
                    "status": "success",
                    "amount": float(deal.get("agreed_price") or deal.get("price") or 0.0),
                    "currency": deal.get("currency") or "ETB",
                    # FIX: Explicitly populate meta with deal_id for downstream dispatch handlers
                    "meta": {
                        "deal_id": deal_id,
                        "seeker_id": deal.get("seeker_id"),
                        "provider_id": deal.get("provider_id")
                    }
                }
                # Re-run process asynchronously to finalize reservation and dispatch
                await self._process_payment_async(event_data=event_data, retry_count=0)
                recovered_count += 1

            logger.info(f"✅ Startup recovery complete: Successfully recovered {recovered_count} deals.")
            return recovered_count
        except Exception as recovery_err:
            logger.error(f"❌ Error during startup payment recovery: {recovery_err}", exc_info=True)
            return 0

    async def verify_payment(self, tx_ref: str) -> Dict[str, Any]:
        """
        Polls upstream Chapa API to directly verify payment validity with network fault tolerance.
        """
        if not tx_ref:
            raise HTTPException(status_code=400, detail="Missing critical transaction reference (tx_ref)")

        verify_url = f"{self.chapa_verify_base}{tx_ref}"
        headers = {"Authorization": f"Bearer {self.secret_key}"}

        max_retries = 3
        backoff_factor = 1.5
        response = None

        for attempt in range(1, max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.get(verify_url, headers=headers)
                    break
            except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.NetworkError, httpx.TimeoutException) as net_error:
                if attempt == max_retries:
                    logger.error(
                        f"Network exception during verification poll for {tx_ref} after {max_retries} attempts: {str(net_error)}"
                    )
                    raise HTTPException(
                        status_code=503,
                        detail="Upstream payment gateway verification service timed out"
                    )
                logger.warning(
                    f"Verification connection attempt {attempt} timed out for {tx_ref}, retrying... Error: {net_error}"
                )
                await asyncio.sleep(backoff_factor ** attempt)

        if response is None or response.status_code != 200:
            raise HTTPException(status_code=400, detail="Payment verification unsuccessful with upstream gateway")

        result = response.json()
        if result.get("status") == "success" or (
                result.get("data") and result["data"].get("status") == "success"):
            return {
                "status": "success",
                "message": "Transaction verified successfully via Chapa",
                "raw_data": result.get("data", {})
            }
        raise HTTPException(status_code=400, detail="Payment transaction status is not marked as successful")

    async def verify_and_confirm_transaction(self, tx_ref: str) -> Dict[str, Any]:
        """
        Directly verifies transaction state with Chapa and processes deal fulfillment.
        Includes a database-level idempotency guard to prevent racing against the webhook.
        """
        verification_res = await self.verify_payment(tx_ref)
        raw_data = verification_res.get("raw_data", {})
        meta = raw_data.get("meta") or {}

        supabase_client = getattr(self.db, 'supabase', self.db)
        deal_id = meta.get("deal_id")

        try:
            # Pre-flight DB check: Look up by deal_id (if in meta) OR tx_ref
            query = supabase_client.table("deals").select("id, seeker_id, provider_id, deal_state")
            if deal_id:
                deal_res = await query.eq("id", deal_id).maybe_single().execute()
            else:
                deal_res = await query.eq("payment_reference", tx_ref).maybe_single().execute()

            if deal_res and getattr(deal_res, "data", None):
                ddata = deal_res.data

                # 🛑 IDEMPOTENCY GUARD: Short-circuit if the webhook already finalized this deal
                if ddata.get("deal_state") == "finalized":
                    logger.info(
                        f"Transaction {tx_ref} is already finalized in DB. Skipping duplicate async processing.")
                    return {
                        "status": "success",
                        "message": f"Transaction {tx_ref} was already verified and processed.",
                        "data": raw_data
                    }

                # FIX: If deal_id was missing from gateway meta, resolve it here
                if not deal_id:
                    meta["deal_id"] = ddata.get("id")
                    meta["seeker_id"] = ddata.get("seeker_id")
                    meta["provider_id"] = ddata.get("provider_id")

        except Exception as look_err:
            logger.warning(f"Failed to lookup deal state for idempotency check on {tx_ref}: {look_err}")

        event_data = {
            "tx_ref": tx_ref,
            "status": "success",
            "amount": raw_data.get("amount", 0.0),
            "currency": raw_data.get("currency", "ETB"),
            "meta": meta
        }

        # Only execute if the DB check confirmed the deal is NOT finalized
        await self._process_payment_async(event_data=event_data, retry_count=0)

        return {
            "status": "success",
            "message": f"Transaction {tx_ref} verified and confirmed.",
            "data": raw_data
        }

    async def verify_signature(self, payload: bytes, signature: Optional[str]) -> bool:
        """
        Timing-attack safe HMAC validation for Chapa webhook events.
        """
        if not signature:
            logger.warning("🚨 SECURITY ALERT: Inbound webhook payload received without an HMAC signature header.")
            return False

        if not self.webhook_secret:
            logger.critical("⚙️ CONFIGURATION ERROR: CHAPA_WEBHOOK_SECRET is uninitialized. Rejecting all webhooks.")
            return False

        try:
            computed_signature = hmac.new(
                self.webhook_secret.encode('utf-8'),
                payload,
                hashlib.sha256
            ).hexdigest()

            return hmac.compare_digest(computed_signature.lower(), signature.lower())
        except Exception as e:
            logger.error(f"❌ Cryptographic error during signature verification: {e}")
            return False

    async def handle_payment_event(
            self,
            request: Request,
            signature: Optional[str]
    ) -> Dict[str, str]:
        """
        Master payment webhook ingestion endpoint handler.
        Verifies cryptographic identity, parses JSON data, and queues async handling safely.
        """
        payload_bytes = await request.body()

        # Safely handle both async and sync verify_signature implementations
        if inspect.iscoroutinefunction(self.verify_signature):
            is_valid = await self.verify_signature(payload_bytes, signature)
        else:
            is_valid = self.verify_signature(payload_bytes, signature)

        if not is_valid:
            logger.critical("❌ SECURITY BREACH ATTEMPT: Unauthorized webhook signature rejected.")
            raise HTTPException(
                status_code=401,
                detail="Unauthorized: Cryptographic Signature Verification Failed"
            )

        try:
            event_data = json.loads(payload_bytes.decode('utf-8'))
        except (json.JSONDecodeError, UnicodeDecodeError):
            logger.error("Malformed JSON string received from payment gateway.")
            raise HTTPException(
                status_code=400,
                detail="Bad Request: Invalid JSON Structure"
            )

        tx_ref = event_data.get("tx_ref") or event_data.get("trx_ref") or event_data.get("reference")
        logger.info(f"📥 Validated webhook received. TX Ref: {tx_ref}")

        try:
            if self.queue:
                from src.smartmatch.utils.queue_manager import TaskPriority

                # Resolve enum priority safely
                priority_attr = getattr(TaskPriority, 'HIGH', 0)
                priority_val = priority_attr.value if hasattr(priority_attr, 'value') else priority_attr

                # Queue with explicitly separated target kwargs
                await self.queue.enqueue(
                    self._process_payment_async,
                    priority=priority_val,
                    kwargs={"event_data": event_data, "retry_count": 0}
                )
            else:
                logger.warning("Queue manager unattached. Dispatching payment task directly.")

                # Retain strong reference to prevent garbage collection mid-flight
                task = asyncio.create_task(
                    self._process_payment_async(event_data=event_data, retry_count=0)
                )
                self._background_tasks.add(task)

                # Clean up memory upon completion
                task.add_done_callback(self._background_tasks.discard)

            return {"status": "success", "message": "Event received and queued safely."}

        except Exception as queue_error:
            logger.critical(f"Failed to ingest payment event into task queue: {str(queue_error)}", exc_info=True)
            raise HTTPException(
                status_code=500,
                detail="Internal Server Error: Concurrency ingestion layer saturated"
            )

    async def handle_successful_payment(self, deal_id: str) -> Dict[str, Any]:
        """
        RECOVERABLE IDEMPOTENCY CHECK:
        Distinguishes between a deal that is fully finalized/dispatched,
        one paid but missing dispatch, and one entirely fresh.
        """
        db_client = getattr(self.db, 'supabase', self.db)

        deal = await db_client.table('deals').select(
            'deal_state, status, payment_status, dispatch_sent'
        ).eq('id', deal_id).maybe_single().execute()

        if deal and deal.data:
            d_data = deal.data
            is_paid = (
                    d_data.get('deal_state') in ['finalized', 'completed'] or
                    d_data.get('status') in ['paid', 'completed', 'escrowed', 'confirmed', 'finalized'] or
                    d_data.get('payment_status') == 'paid'
            )
            is_dispatched = d_data.get('dispatch_sent') is True

            if is_paid and is_dispatched:
                return {"status": "FULLY_FINALIZED", "already_paid": True}
            elif is_paid and not is_dispatched:
                return {"status": "NEEDS_DISPATCH_ONLY", "already_paid": True}

        now_iso = datetime.now(timezone.utc).isoformat()
        await db_client.table('deals').update({
            'deal_state': 'finalized',
            'status': 'finalized',
            'payment_status': 'paid',
            'updated_at': now_iso
        }).eq('id', deal_id).execute()

        return {"status": "PROCESS_ALL", "already_paid": False}

    async def process_payment_async(
            self,
            event_data: Optional[Dict[str, Any]] = None,
            retry_count: int = 0,
            **kwargs
    ) -> None:
        """
        Public proxy wrapper for _process_payment_async to prevent
        PyCharm protected member access warnings while absorbing task runner kwargs.
        """
        await self._process_payment_async(event_data=event_data, retry_count=retry_count, **kwargs)

    async def _process_payment_async(
            self,
            event_data: Optional[Dict[str, Any]] = None,
            retry_count: int = 0,
            **kwargs
    ) -> None:
        """
        Asynchronous payment event processor with strict atomic idempotency locking,
        schema-aligned database lookups, commission verification, and exponential backoff retry handling.
        """
        # Unpack event_data defensively if passed as nested kwargs from queue worker
        if not event_data:
            event_data = kwargs.get("event_data") or kwargs.get("payload") or kwargs.get("kwargs", {})
            if isinstance(event_data, dict) and "event_data" in event_data:
                event_data = event_data["event_data"]

        if not isinstance(event_data, dict):
            event_data = {}

        retry_count = retry_count or kwargs.get("retry_count", 0)

        raw_tx_ref = event_data.get("tx_ref") or event_data.get("trx_ref") or event_data.get("order_id") or ""
        tx_ref = raw_tx_ref.split("?")[0].strip()
        pay_async_status = str(event_data.get("status", "")).lower()

        if not tx_ref:
            logger.error("Payment event missing critical 'tx_ref' identifier. Aborting execution line.")
            return

        try:
            if pay_async_status in ["success", "successful", "paid", "completed"]:
                logger.info(f"Financial success signal confirmed for TX: {tx_ref}. Reconciling ledger states.")

                db_client = getattr(self.db, 'supabase', self.db)

                # 1. Resolve Deal Baseline
                deal: Optional[Dict[str, Any]] = None
                deal_id_str: Optional[str] = None
                chat_id: Optional[Union[int, str]] = None

                if hasattr(self, 'billing') and hasattr(self.billing, 'confirm_payment'):
                    try:
                        deal_id, chat_id = await self.billing.confirm_payment(tx_ref)
                        if deal_id:
                            deal_id_str = str(deal_id)
                    except Exception as billing_err:
                        logger.warning(f"Billing confirm_payment fallback for {tx_ref}: {billing_err}")

                if not deal_id_str:
                    deal_res = await db_client.table("deals").select("*").eq(
                        "payment_reference", tx_ref
                    ).maybe_single().execute()
                    if deal_res and getattr(deal_res, 'data', None):
                        deal = deal_res.data
                        deal_id_str = str(deal.get("id"))
                    else:
                        deal_res_id = await db_client.table("deals").select("*").eq(
                            "id", tx_ref
                        ).maybe_single().execute()
                        if deal_res_id and getattr(deal_res_id, 'data', None):
                            deal = deal_res_id.data
                            deal_id_str = str(deal.get("id"))

                if not deal_id_str:
                    logger.warning(f"⚠️ No matching deal found for transaction reference '{tx_ref}'.")
                    return

                # 2. Check Idempotency Gate
                state = await self.handle_successful_payment(deal_id_str)
                if state["status"] == "FULLY_FINALIZED":
                    logger.info(f"🛑 Idempotency Gate: Deal {deal_id_str} is already fully finalized and dispatched.")
                    return

                # 3. Finalize Financial Ledger Transaction & Commissions Table
                if state["status"] == "PROCESS_ALL" and not state["already_paid"]:
                    meta = event_data.get("meta") or {}
                    commission_paid = float(event_data.get("amount", 0.0))

                    raw_orig_price = meta.get("original_price")
                    original_price = float(raw_orig_price) if raw_orig_price is not None else commission_paid
                    original_currency = meta.get("original_currency") or event_data.get("currency", "ETB")

                    if chat_id is None and deal:
                        chat_id = deal.get("seeker_id") or deal.get("seeker_telegram_id")

                    if hasattr(self, 'billing') and hasattr(self.billing, 'finalize_transaction'):
                        try:
                            await self.billing.finalize_transaction(
                                deal_id=deal_id_str,
                                user_id=str(chat_id) if chat_id else "",
                                amount=original_price,
                                commission=commission_paid,
                                currency=original_currency
                            )
                        except Exception as fin_err:
                            logger.warning(f"Billing finalize_transaction note for {deal_id_str}: {fin_err}")

                    # ✅ ROBUST FIX: Explicitly update commissions table status to 'paid'
                    try:
                        await db_client.table("commissions").update({
                            "status": "paid",
                            "commission_fee": commission_paid,
                            "updated_at": datetime.now(timezone.utc).isoformat()
                        }).eq("deal_id", deal_id_str).execute()
                        logger.info(f"✅ Commission record for deal {deal_id_str} marked as 'paid'.")
                    except Exception as comm_err:
                        logger.error(f"Failed to update commission status to 'paid' for deal {deal_id_str}: {comm_err}")

                    # Confirm Asset Reservation
                    try:
                        await ReservationHelper.confirm_reservation(db_client, deal_id_str)
                        logger.info(f"✅ Reservation for deal {deal_id_str} confirmed.")
                    except Exception as res_err:
                        logger.warning(f"⚠️ Reservation confirmation note for deal {deal_id_str}: {res_err}")

                # ✅ GUARDRAIL CHECK: Enforce that dispatch happens ONLY IF commission status is verified as 'paid'
                try:
                    comm_check = await db_client.table("commissions").select("status").eq("deal_id",
                                                                                          deal_id_str).maybe_single().execute()
                    is_commission_paid = comm_check and comm_check.data and str(
                        comm_check.data.get("status")).lower() == "paid"
                except Exception as check_err:
                    logger.warning(f"Failed to verify commission paid status for deal {deal_id_str}: {check_err}")
                    is_commission_paid = False

                if not is_commission_paid:
                    logger.error(f"❌ Dispatch aborted for Deal {deal_id_str}: Commission table status is not 'paid'.")
                    return

                # 4. Fetch Latest Deal Record for Dispatch
                deal_fetch = await db_client.table("deals").select("*").eq(
                    "id", deal_id_str
                ).maybe_single().execute()
                if deal_fetch and getattr(deal_fetch, 'data', None):
                    deal = deal_fetch.data

                if deal:
                    seeker_id = deal.get("seeker_id") or deal.get("seeker_telegram_id")
                    provider_id = deal.get("provider_id") or deal.get("provider_telegram_id")
                    seeker_phone = deal.get("seeker_phone") or "Not provided"
                    provider_phone = deal.get("provider_phone") or "Not provided"

                    # Dual lookup using OR filter to support UUID or integer Telegram ID
                    if seeker_phone in ["Not provided", None, ""] and seeker_id:
                        try:
                            u_s = await db_client.table("users").select("phone_number, phone").or_(
                                f"telegram_id.eq.{seeker_id},id.eq.{seeker_id}"
                            ).maybe_single().execute()
                            if u_s and getattr(u_s, 'data', None):
                                seeker_phone = u_s.data.get("phone_number") or u_s.data.get("phone") or seeker_phone
                        except Exception:
                            pass

                    if provider_phone in ["Not provided", None, ""] and provider_id:
                        try:
                            u_p = await db_client.table("users").select("phone_number, phone").or_(
                                f"telegram_id.eq.{provider_id},id.eq.{provider_id}"
                            ).maybe_single().execute()
                            if u_p and getattr(u_p, 'data', None):
                                provider_phone = u_p.data.get("phone_number") or u_p.data.get("phone") or provider_phone
                        except Exception:
                            pass

                    asset_title = deal.get("asset_title") or deal.get("title")

                    if seeker_id and provider_id:
                        # 1. Pre-check if already dispatched to prevent duplicate triggers
                        if deal.get("dispatch_sent") is True:
                            logger.info(
                                f"🛑 Race Condition Caught: Deal {deal_id_str} was already dispatched."
                            )
                            return

                        # 2. Update status to finalized WITHOUT setting dispatch_sent=True prematurely
                        now_iso = datetime.now(timezone.utc).isoformat()
                        await db_client.table("deals").update({
                            "status": "finalized",
                            "updated_at": now_iso
                        }).eq("id", deal_id_str).execute()

                        # Safe attribute resolution for telegram_app and bot injection
                        telegram_app = (
                                getattr(self, "telegram_app", None) or
                                getattr(getattr(self, "billing_service", None), "telegram_app", None) or
                                getattr(getattr(self, "billing", None), "telegram_app", None)
                        )
                        self.dispatch_service.telegram_app = telegram_app

                        # Ensure DispatchService has a direct reference to the bot instance
                        if telegram_app:
                            if hasattr(telegram_app, "bot"):
                                self.dispatch_service.bot = telegram_app.bot
                            elif hasattr(telegram_app, "updater") and telegram_app.updater and hasattr(
                                    telegram_app.updater, "bot"):
                                self.dispatch_service.bot = telegram_app.updater.bot

                        # Fallback: if self has a bot directly
                        if not getattr(self.dispatch_service, "bot", None) and hasattr(self, "bot"):
                            self.dispatch_service.bot = self.bot

                        # 3. Trigger network dispatch (this method will lock dispatch_sent=True upon success)
                        await self.dispatch_service._send_dispatch_notification(
                            deal_id=deal_id_str,
                            seeker_id=seeker_id,
                            provider_id=provider_id,
                            seeker_phone=str(seeker_phone),
                            provider_phone=str(provider_phone),
                            asset_title=asset_title
                        )

                        logger.info(
                            f"🎯 Deal {deal_id_str} fully finalized and dispatched. Transaction lifecycle complete."
                        )
                    else:
                        logger.warning(
                            f"⚠️ Missing seeker_id ({seeker_id}) or provider_id ({provider_id}) for deal {deal_id_str}. Dispatch skipped."
                        )
                else:
                    logger.warning(f"🎯 Ledger reconciled for Deal {deal_id_str} with TX {tx_ref}.")

            else:
                logger.warning(
                    f"Payment callback for TX Ref {tx_ref} resolved with unsuccessful status: '{pay_async_status}'."
                )

        except Exception as async_error:
            logger.error(f"Error processing payment execution step for {tx_ref}: {str(async_error)}", exc_info=True)

            max_retries = 3
            if retry_count < max_retries:
                next_retry = retry_count + 1
                sleep_delay = 2 ** retry_count
                logger.warning(
                    f"Re-queueing failed dispatch line for {tx_ref}. Attempt {next_retry} of {max_retries} (sleeping {sleep_delay}s)..."
                )

                await asyncio.sleep(sleep_delay)

                if hasattr(self, 'queue') and self.queue:
                    from src.smartmatch.utils.queue_manager import TaskPriority
                    await self.queue.enqueue(
                        self.process_payment_async,
                        priority=TaskPriority.HIGH if hasattr(TaskPriority, 'HIGH') else 0,
                        event_data=event_data,
                        retry_count=next_retry
                    )
                else:
                    asyncio.create_task(self.process_payment_async(event_data=event_data, retry_count=next_retry))
            else:
                logger.critical(
                    f"🚨 CRITICAL ESCALATION: Max retries ({max_retries}) exceeded for transaction matching for TX: {tx_ref}"
                )
