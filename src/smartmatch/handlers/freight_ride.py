import logging
import uuid
import asyncio
import html
import httpx
from typing import Dict, Any, Optional, Tuple
import re
import json

import time
from deep_translator import GoogleTranslator, MyMemoryTranslator

from telegram.error import NetworkError, TimedOut
from telegram import (
    Update, InlineKeyboardButton,
    InlineKeyboardMarkup, KeyboardButton,
    ReplyKeyboardMarkup, ReplyKeyboardRemove, Message
)
from telegram.ext import (
    ContextTypes, ConversationHandler, CommandHandler,
    MessageHandler, CallbackQueryHandler, filters
)

# Centralized Localization Import
from src.smartmatch.utils.translations import get_text

# Late import for MainMenuHandler to avoid circular dependencies at initialization
try:
    from src.smartmatch.handlers.main_menu import MainMenuHandler
except ImportError:
    MainMenuHandler = None

# Instantiating production telemetry logs
logger = logging.getLogger("smartmatch.handlers.freight_ride")


class LocationTelemetryHandler:

    @staticmethod
    def parse_coordinates(text: str) -> Optional[Tuple[float, float]]:
        """
        Parses manual coordinate strings in various formats:
        - DMS with full (Deg, Min, Sec), partial (Deg, Min), or only Degrees:
          "13° N, 39° E", "13° 29′ N, 39° 28′ E", or "8° 30' 52\" N, 39° 16' 9\" E"
        - Labeled DD with 'and' or commas: "Latitude 13.497° N and Longitude 39.471° E"
        - Unlabeled DD: "13.497° N, 39.471° E"

        Returns (latitude, longitude) as floats rounded to 6 decimal places, or None if unparseable.
        """
        if not text:
            return None

        clean_text = text.strip()

        # 1. Try DMS Format (Supports Full DMS, Degrees & Minutes, or Only Degrees with Directions)
        # Example: "13° N, 39° E" or "13° 29′ N, 39° 28′ E" or "8° 30' 52\" N, 39° 16' 9\" E"
        dms_flexible_pattern = (
            r"(?:Lat(?:itude)?\s*:?\s*)?"
            r"(\d+)\s*°(?:\s*(\d+)\s*['′])?(?:\s*(\d+(?:\.\d+)?)\s*[\"″]?)?\s*([NSns])[\s,and]+"
            r"(?:Lon(?:gitude)?\s*:?\s*)?"
            r"(\d+)\s*°(?:\s*(\d+)\s*['′])?(?:\s*(\d+(?:\.\d+)?)\s*[\"″]?)?\s*([EWew])"
        )
        dms_match = re.search(dms_flexible_pattern, clean_text, re.IGNORECASE)

        if dms_match:
            try:
                lat_deg, lat_min, lat_sec, lat_dir, lon_deg, lon_min, lon_sec, lon_dir = dms_match.groups()

                lat_min_val = float(lat_min) if lat_min else 0.0
                lat_sec_val = float(lat_sec) if lat_sec else 0.0
                lon_min_val = float(lon_min) if lon_min else 0.0
                lon_sec_val = float(lon_sec) if lon_sec else 0.0

                lat = float(lat_deg) + (lat_min_val / 60.0) + (lat_sec_val / 3600.0)
                if lat_dir.upper() == 'S':
                    lat = -abs(lat)

                lon = float(lon_deg) + (lon_min_val / 60.0) + (lon_sec_val / 3600.0)
                if lon_dir.upper() == 'W':
                    lon = -abs(lon)

                if -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0:
                    return round(lat, 6), round(lon, 6)
            except (ValueError, TypeError):
                pass

        # 2. Try Decimal Degrees (DD) Format with optional labels, 'and', degree symbols, and directional indicators
        # Example: "Latitude 13.497° N and Longitude 39.471° E" or "13.497° N, 39.471° E"
        dd_pattern = (
            r"(?:Lat(?:itude)?\s*:?\s*)?([+-]?\d+(?:\.\d+)?)\s*°?\s*([NSns])?[\s,and]+"
            r"(?:Lon(?:gitude)?\s*:?\s*)?([+-]?\d+(?:\.\d+)?)\s*°?\s*([EWew])?"
        )
        dd_match = re.search(dd_pattern, clean_text, re.IGNORECASE)

        if dd_match:
            try:
                lat_str, lat_dir, lon_str, lon_dir = dd_match.groups()
                lat = float(lat_str)
                lon = float(lon_str)

                if lat_dir and lat_dir.upper() == 'S':
                    lat = -abs(lat)
                elif lat_dir and lat_dir.upper() == 'N':
                    lat = abs(lat)

                if lon_dir and lon_dir.upper() == 'W':
                    lon = -abs(lon)
                elif lon_dir and lon_dir.upper() == 'E':
                    lon = abs(lon)

                if -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0:
                    return round(lat, 6), round(lon, 6)
            except (ValueError, TypeError):
                pass

        return None


def _get_localized_domain_and_title(domain: str, raw_title: str, lang_code: str) -> str:
    """
    Cleans inline hardcoded/mixed language artifacts (e.g., 'Fe'umsa', 'ጭነት', 'Freight')
    and returns a seamlessly localized title aligned with the target user's preferred language.
    """
    norm_lang = str(lang_code).lower().strip().split('_')[0] if lang_code else 'en'
    if norm_lang not in ('en', 'am', 'om'):
        norm_lang = 'en'

    # 1. Clean any hardcoded or mixed-language prefixes (like 'Fe'umsa', 'Fe'umsaa', 'ጭነት', 'Freight')
    cleaned_title = re.sub(
        r"^(?:Fe'umsaa|Fe'umsa|ጭነት|Freight)\s*[-:]?\s*",
        "",
        str(raw_title or ""),
        flags=re.IGNORECASE
    ).strip()

    # 2. Clean common metadata artifacts if present
    cleaned_title = re.sub(r"\((?:Plate|Gabatee|ሰሌዳ):[^)]+\)", "", cleaned_title, flags=re.IGNORECASE).strip()

    # 3. Map domain names cleanly per language fallback
    domain_map = {
        'freight': {'am': 'ጭነት', 'om': "Fe'umsa", 'en': 'Freight'},
        'ride': {'am': 'ትራንስፖርት', 'om': 'Imala', 'en': 'Ride'}
    }
    clean_domain = str(domain or "freight").lower().strip()
    localized_dom = domain_map.get(clean_domain, {}).get(norm_lang, clean_domain.title())

    if not cleaned_title or cleaned_title.lower() in ('n/a', 'none', 'null', 'unspecified', clean_domain):
        return localized_dom

    return cleaned_title


def safe_float(val: Any, default: float = 0.0) -> float:
    """Safely converts dynamic inputs to float, handling native types and noisy strings."""
    if val is None:
        return default
    if isinstance(val, (int, float)):
        return float(val)
    try:
        clean_val = re.sub(r"[^\d.]", "", str(val).replace(",", ""))
        return float(clean_val) if clean_val else default
    except (ValueError, TypeError):
        return default


def apply_city_fixes(city_text: Optional[str]) -> str:
    """Normalizes regional city strings for the logistics DB matching engine."""
    if not city_text or not isinstance(city_text, str):
        return "Addis Ababa"

    cleaned = city_text.strip().lower()
    CITY_MAPPINGS = {
        "finfinne": "Addis Ababa",
        "addis ababa": "Addis Ababa",
        "adaamaa": "Adama",
        "adama": "Adama",
        "nazret": "Adama",
        "hawassa": "Hawassa",
        "awasa": "Hawassa",
        "jimma": "Jimma",
        "bahir dar": "Bahir Dar",
        "mekelle": "Mekelle",
        "dire dawa": "Dire Dawa",
        "bishoftu": "Bishoftu"
    }
    return CITY_MAPPINGS.get(cleaned, city_text.strip().title())


def apply_brand_fixes(brand_text: Optional[str]) -> str:
    """Standardizes vehicle/truck branding for accurate taxonomy matching."""
    if not brand_text or not isinstance(brand_text, str):
        return "Generic"

    cleaned = brand_text.strip().lower()
    BRAND_MAPPINGS = {
        "isuzu": "Isuzu",
        "sinotruk": "Sinotruk",
        "howo": "Sinotruk HOWO",
        "volvo": "Volvo",
        "scania": "Scania",
        "toyota": "Toyota",
        "nissan": "Nissan",
        "mercedes": "Mercedes-Benz"
    }
    return BRAND_MAPPINGS.get(cleaned, brand_text.strip().title())


# Standardized Regional Transport Lexicon
REGIONAL_TRANSPORT_LEXICON: Dict[str, str] = {
    "konkolaataa fe'umsaa": "Cargo Truck",
    "fe'umsaa": "Freight",
    "taaksii": "Taxi",
    "minibaasii": "Minibus",
    "bajajii": "Bajaj",
    "ilaalcha": "Standard Ride",
    "finfinnee": "Addis Ababa",
    "adaamaa": "Adama",
    "hawassa": "Hawassa"
}

# ==========================================
# GLOBAL IN-MEMORY TRANSLATION CACHE DICTIONARY
# ==========================================
_TRANSLATION_CACHE: Dict[Tuple[str, str, str], str] = {}


class FreightRideHandler:
    """
    Production-Grade Unified Handler for Freight Logistics & Urban Ride-Sharing.
    Implements multi-step, localized state preservation for precise AI-driven matching
    and seamlessly bridges Deals, Billing, Webhooks, and the Main Menu.
    """

    # State Machine Identifiers (Finite State Automata)
    (
        AWAITING_FREIGHT_OFFER,
        ROLE_SELECTION,
        LOCATION_ORIGIN,
        LOCATION_DESTINATION,
        LOCATION_REGION,
        GPS_LOCATION,
        CAPACITY_SPECS,
        CLASS_TYPE,
        AMENITIES_EXTRAS,
        BUDGET_FARE,
        CONTACT_INFO,
        PLATE_REGISTRATION,
        PHOTO_UPLOAD,
        SELECTING_MATCH,
        ENTERING_OFFER
    ) = range(15)

    # Centralized keys for targeted state cleanup to prevent wiping global data
    TEMP_FLOW_KEYS = [
        'domain', 'role', 'max_budget', 'estimated_fare', 'shipping_quote',
        'cargo_weight_kgs', 'max_weight_kgs', 'party_size', 'available_seats',
        'price', 'quantity', 'vehicle_plate', 'destination', 'ride_class',
        'vehicle_type', 'trailer_type', 'truck_type', 'has_hazmat_license',
        'origin', 'location_text', 'latitude', 'longitude', 'currency',
        'photo_url', 'vehicle_photo', 'asset_photo_url',
        'freight_top_matches', 'freight_ride_top_matches', 'active_deal_id',
        'asking_price', 'provider_id', 'asset_title', 'operating_region',
        'is_hazardous', 'required_trailer_type', 'target_listing_id',
        'route_string', 'required_region', 'carrier_rating', 'desired_amenities',
        'amenities', 'driver_background_cleared', 'seeker_top_matches',
        'last_created_listing_id', 'is_reverse_match', 'target_seeker_id',
        'active_freight_deal_id', 'freight_fare', 'vertical_type'
    ]

    def __init__(self, db_manager, ai_engine, matching_engine, billing_service, deals_handler, image_service,
                 queue_manager):
        self.db = db_manager
        self.ai = ai_engine
        self.matching = matching_engine
        self.billing = billing_service
        self.deals_handler = deals_handler
        self.image_service = image_service
        self.queue = queue_manager
        # Initialize translation cache on instance initialization
        self._translation_cache: Dict[Tuple[str, str, str], str] = {}

    def _sanitize_and_cache(
            self,
            cache_key: Tuple[str, str, str],
            clean_text: str,
            translated_output: Optional[str]
    ) -> str:
        """Sanitize translation output and save valid non-error strings to the instance cache."""
        if not translated_output or any(
                err_str in str(translated_output)
                for err_str in ["Error 500", "Error 400", "Exception", "MYMEMORY WARNING"]
        ):
            return clean_text

        cleaned_res = str(translated_output).split('(')[0].strip()
        if cleaned_res:
            self._translation_cache[cache_key] = cleaned_res
            return cleaned_res
        return clean_text

    def safe_translate_sync(
            self,
            text: str,
            source_lang: str = 'auto',
            target_lang: str = 'en'
    ) -> str:
        """
        Robust, multi-tier synchronous translation worker.
        Invoked via asyncio.to_thread() from async handlers.
        """
        if not text or not str(text).strip():
            return text

        clean_text = str(text).strip()
        cache_key = (clean_text.lower(), source_lang, target_lang)

        # 0. Safety check for instance translation cache
        if not hasattr(self, '_translation_cache'):
            self._translation_cache = {}

        # 1. Check instance cache dictionary
        if cache_key in self._translation_cache:
            return self._translation_cache[cache_key]

        # 2. Check local transport lexicon for inbound regional terms to English
        cleaned_lower_lexicon = clean_text.lower()
        if target_lang == 'en' and cleaned_lower_lexicon in REGIONAL_TRANSPORT_LEXICON:
            translated_lexicon = REGIONAL_TRANSPORT_LEXICON[cleaned_lower_lexicon]
            self._translation_cache[cache_key] = translated_lexicon
            return translated_lexicon

        # 3. Fast bypass for English strings, numeric inputs, or matching source/target
        known_english = {
            "freight", "ride", "service", "standard", "active", "flatbed", "box",
            "truck", "van", "bus", "sedan", "suv", "carrier", "driver", "passenger",
            "shipper", "any"
        }
        words = set(re.findall(r'\b\w+\b', cleaned_lower_lexicon))
        has_non_ascii = any(ord(char) > 127 for char in clean_text)
        has_local_terms = bool(words - known_english) and not clean_text.isnumeric()

        if (target_lang == 'en' and not has_non_ascii and not has_local_terms) or (source_lang == target_lang):
            self._translation_cache[cache_key] = clean_text
            return clean_text

        # 4. PRIMARY PROVIDER: GoogleTranslator with Exponential Backoff
        max_attempts = 3
        base_delay = 0.8  # Initial delay in seconds (0.8s -> 1.6s)

        for attempt in range(max_attempts):
            try:
                translator = GoogleTranslator(source=source_lang, target=target_lang)
                res_trans = translator.translate(clean_text)
                if res_trans:
                    return self._sanitize_and_cache(cache_key, clean_text, res_trans)
            except Exception as trans_err:
                err_msg = str(trans_err).lower()
                is_rate_limited = any(
                    term in err_msg
                    for term in ["too many requests", "5 requests", "429", "quota", "rate limit"]
                )

                if is_rate_limited and attempt < max_attempts - 1:
                    time.sleep(base_delay * (2 ** attempt))
                    continue

                # Fall through to secondary engine on non-rate-limit error or retries exhausted
                break

        # 5. SECONDARY PROVIDER FALLBACK: MyMemoryTranslator
        try:
            fallback_src = 'en' if source_lang == 'auto' else source_lang
            fallback_translator = MyMemoryTranslator(source=fallback_src, target=target_lang)
            fallback_res = fallback_translator.translate(clean_text)

            if fallback_res and fallback_res.lower() != clean_text.lower():
                return self._sanitize_and_cache(cache_key, clean_text, fallback_res)
        except Exception as fallback_err:
            logger.debug(f"Secondary translation engine (MyMemory) skipped: {fallback_err}")

        # 6. ABSOLUTE FAILOVER: Return clean text and cache to prevent redundant failing calls
        self._translation_cache[cache_key] = clean_text
        return clean_text

    async def _async_translate(self, text: str, src: str = 'auto', dest: str = 'en') -> str:
        """Helper to run synchronous translations asynchronously without blocking the event loop."""
        if not text or str(text).strip().upper() in ("", "N/A", "NONE", "NULL", "UNSPECIFIED", "ANY", "NOE"):
            return str(text) if text else ""
        try:
            translated = await asyncio.to_thread(self.safe_translate_sync, text, src, dest)
            return str(translated) if translated else str(text)
        except Exception as e:
            logger.warning(f"Translation attempt skipped for '{text}': {e}")
            return str(text)

    @classmethod
    def _get_lang(cls, context: ContextTypes.DEFAULT_TYPE) -> str:
        """Determines active session language safely."""
        raw_lang = context.user_data.get('language') or context.user_data.get('lang') or 'en'
        clean_lang = str(raw_lang).strip().lower()
        if "am" in clean_lang:
            return "am"
        if "or" in clean_lang or "om" in clean_lang:
            return "om"
        return "en"

    async def sync_session_language(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> str:
        """Syncs the language profile from DB or Telegram User object into session context."""
        lang = context.user_data.get('language') or context.user_data.get('lang')

        if lang in ['en', 'am', 'om']:
            return lang

        if not lang and update.effective_user:
            try:
                user_id = str(update.effective_user.id)
                user_profile = await self.db.get_user_profile(user_id) or await self.db.get_user(user_id)
                if user_profile and isinstance(user_profile, dict):
                    lang = user_profile.get('language') or user_profile.get('lang')
            except Exception as e:
                logger.debug(f"DB language sync fallback lookup bypassed: {e}")

        if not lang and update.effective_user and update.effective_user.language_code:
            lang = update.effective_user.language_code

        normalized_lang = "en"
        if lang:
            clean = str(lang).strip().lower()
            if "am" in clean:
                normalized_lang = "am"
            elif "or" in clean or "om" in clean:
                normalized_lang = "om"

        context.user_data['language'] = normalized_lang
        return normalized_lang

    async def route_to_main_menu(
            self,
            update: Update,
            context: ContextTypes.DEFAULT_TYPE,
            custom_msg: Optional[str] = None,
            suppress_invalid_prompt: bool = False
    ) -> int:
        """
        Safely clears active temporary flow session keys and returns the user
        to the persistent main menu, safeguarding against unmatched text input crashes.
        """
        lang = self._get_lang(context)
        reply_markup = None

        try:
            if MainMenuHandler:
                reply_markup = MainMenuHandler.get_persistent_keyboard(lang)
        except Exception as e:
            logger.warning(f"Could not load persistent keyboard: {e}")

        # Targeted state wipe
        for key in self.TEMP_FLOW_KEYS:
            context.user_data.pop(key, None)

        # 1. Safely acknowledge if the trigger was an inline button
        if update.callback_query:
            try:
                await update.callback_query.answer()
            except Exception:
                pass

        fallback_msg = (
                get_text(lang, "msg_return_root") or
                "🏠 <b>Main Menu</b>\n\nSelect an option below to continue:"
        )

        # Consume suppression flag set by upstream text-input handlers
        flow_suppressed = context.user_data.pop("_suppress_invalid_prompt", False) or suppress_invalid_prompt

        # 2. Intercept unmatched text input ONLY if it's a true stray message outside active flows.
        is_active_workflow = context.user_data.get("waiting_for_input") or context.user_data.get("active_negotiation")

        if (
                not custom_msg
                and not update.callback_query
                and not is_active_workflow
                and not flow_suppressed
                and update.message
                and update.message.text
                and not update.message.text.startswith('/')
        ):
            custom_msg = f"⚠️ Invalid input detected.\n\n{fallback_msg}"

        # 3. Direct Message Delivery for custom/fallback messages
        if custom_msg and update.effective_chat:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=custom_msg,
                reply_markup=reply_markup,
                parse_mode="HTML"
            )
            return ConversationHandler.END

        # 4. Standard external routing
        menu_handler = context.bot_data.get("main_menu_handler")
        if menu_handler and hasattr(menu_handler, 'show_main_menu'):
            try:
                return await menu_handler.show_main_menu(update, context)
            except Exception as e:
                logger.error(f"Failed to route to bot_data main menu handler: {e}")

        # 5. Ultimate Fallback
        if update.effective_chat:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=fallback_msg,
                reply_markup=reply_markup,
                parse_mode="HTML"
            )

        return ConversationHandler.END

    async def prompt_initial_offer(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handles deep-linking/callbacks for proposing an initial offer to a listing."""
        query = update.callback_query
        await query.answer()
        lang = self._get_lang(context)

        try:
            _, listing_id = query.data.split(":", 1)
        except ValueError:
            logger.error(f"Malformed offer callback payload: {query.data}")
            return await self.route_to_main_menu(update, context, custom_msg="⚠️ Invalid asset link.")

        context.user_data["target_listing_id"] = listing_id
        context.user_data["vertical_type"] = "freight_ride"

        prompt_text = get_text(lang, "FREIGHT_OFFER_PROMPT") or "Please input your offer rate for this resource (ETB):"
        btn_txt = get_text(lang, "BTN_CANCEL_OFFER") or "❌ Cancel Offer"

        keyboard = [
            [InlineKeyboardButton(btn_txt, callback_data="cancel_vertical_offer")]
        ]

        await query.edit_message_text(
            text=prompt_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        return self.AWAITING_FREIGHT_OFFER

    async def process_initial_offer(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Processes the proposed financial offer and bridges to DealsHandler."""
        if not update.message or not update.message.text:
            return self.AWAITING_FREIGHT_OFFER

        lang = self._get_lang(context)
        user_id = update.effective_user.id
        text_payload = update.message.text.strip()
        listing_id = context.user_data.get("target_listing_id")
        vertical = context.user_data.get("vertical_type", "freight")

        try:
            clean_numeric = text_payload.replace(",", "").replace("ETB", "").replace("birr", "").strip()
            initial_offer = float(clean_numeric)
            if initial_offer <= 0:
                raise ValueError("Price must be strictly positive.")
        except ValueError:
            err_num = get_text(lang,
                               "ERR_INVALID_NUMBER") or "⚠️ Numerical evaluation error. Provide a valid integer figure:"
            await update.message.reply_text(err_num)
            return self.AWAITING_FREIGHT_OFFER

        try:
            listing = await self.db.get_listing_by_id(listing_id)
        except Exception as e:
            logger.error(f"Failed to fetch target listing {listing_id}: {e}")
            listing = None

        if not listing:
            err_list = get_text(lang,
                                "ERR_LISTING_UNAVAILABLE") or "⚠️ Listing resource is no longer active or available."
            return await self.route_to_main_menu(update, context, custom_msg=err_list)

        provider_id = int(listing.get("provider_id", 0))
        if provider_id == user_id:
            err_own = get_text(
                lang, "ERR_OWN_LISTING"
            ) or (
                "⚠️ Self-negotiation anomalies are restricted. "
                "You cannot bid on your own listing."
            )
            return await self.route_to_main_menu(update, context, custom_msg=err_own)

        deals_handler = context.bot_data.get("deals_handler") or self.deals_handler
        if not deals_handler:
            logger.error("DealsHandler node offline. Cannot bridge negotiation.")
            err_offline = get_text(lang,
                                   "ERR_SYS_NEGOTIATION_OFFLINE") or "⚠️ Negotiation systems are temporarily offline."
            return await self.route_to_main_menu(update, context, custom_msg=err_offline)

        try:
            deal_id = await deals_handler.initiate_negotiation(
                seeker_id=user_id,
                provider_id=provider_id,
                listing_id=listing_id,
                vertical_type=vertical,
                initial_offer=initial_offer
            )
        except Exception as e:
            logger.exception(f"Deal initialization crash for {listing_id}: {e}")
            deal_id = None

        if deal_id:
            msg = get_text(lang, "MSG_OFFER_SENT",
                           amount=f"{initial_offer:,.2f}") or f"✅ Offer of {initial_offer:,.2f} ETB logged and forwarded securely!"
            return await self.route_to_main_menu(update, context, custom_msg=msg)
        else:
            err_fail = get_text(lang,
                                "ERR_OFFER_FAILED") or "⚠️ Transmission error while logging your offer. Please try again later."
            return await self.route_to_main_menu(update, context, custom_msg=err_fail)

    @staticmethod
    async def safe_send_message(
            context: ContextTypes.DEFAULT_TYPE,
            chat_id: int,
            text: str,
            retries: int = 3,
            parse_mode: Optional[str] = "HTML",
            reply_markup: Optional[Any] = None,
            **kwargs
    ) -> Optional[Message]:
        """
        Executes context.bot.send_message with exponential backoff retries,
        explicit timeouts, and robust error handling.
        """
        for attempt in range(1, retries + 1):
            try:
                return await context.bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    parse_mode=parse_mode,
                    reply_markup=reply_markup,
                    read_timeout=7.0,  # Fail fast on network sluggishness
                    write_timeout=7.0,
                    connect_timeout=5.0,
                    **kwargs
                )
            except (NetworkError, TimedOut, httpx.HTTPError) as err:
                logger.warning(
                    f"⚠️ Network dispatch attempt {attempt}/{retries} failed for chat {chat_id}: {err}"
                )
                if attempt == retries:
                    raise err
                # ✅ Corrected exponential backoff calculation with multiplication operator
                await asyncio.sleep(1.0 * (2 ** (attempt - 1)))
        return None

    async def safe_edit_message(
            self,
            update: Update,
            context: ContextTypes.DEFAULT_TYPE,
            text: str,
            parse_mode: Optional[str] = "HTML",
            reply_markup: Optional[Any] = None
    ) -> bool:
        """Safely edits a callback query message with timeout and fallback."""
        query = update.callback_query
        chat_id = update.effective_chat.id if update.effective_chat else None

        if not query or not query.message:
            if chat_id:
                await self.safe_send_message(
                    context, chat_id, text, parse_mode=parse_mode,
                    reply_markup=reply_markup
                )
            return False

        try:
            if getattr(query.message, 'photo', None):
                await query.edit_message_caption(
                    caption=text,
                    reply_markup=reply_markup,
                    parse_mode=parse_mode
                )
            else:
                await query.edit_message_text(
                    text=text,
                    reply_markup=reply_markup,
                    parse_mode=parse_mode
                )
            return True
        except Exception as e:
            logger.warning(f"Failed to edit message smoothly, falling back to new message dispatch: {e}")
            if chat_id:
                await self.safe_send_message(
                    context, chat_id, text, parse_mode=parse_mode,
                    reply_markup=reply_markup
                )
            return False

    async def start_freight(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        lang = self._get_lang(context)
        for key in self.TEMP_FLOW_KEYS:
            context.user_data.pop(key, None)

        context.user_data['language'] = lang
        context.user_data['domain'] = 'freight'

        btn_shp = get_text(lang, "BTN_ROLE_SHIPPER") or "Shipper (Cargo Owner)"
        btn_crr = get_text(lang, "BTN_ROLE_CARRIER") or "Carrier (Vehicle Operator)"

        keyboard = [
            [InlineKeyboardButton(btn_shp, callback_data="role_shipper")],
            [InlineKeyboardButton(btn_crr, callback_data="role_carrier")]
        ]

        start_msg = get_text(
            lang, "MSG_FREIGHT_START"
        ) or "🚛 Welcome to Freight Logistics Management. Please clarify your operations role:"

        await self._send_entry_message(update, context, start_msg, InlineKeyboardMarkup(keyboard))
        return self.ROLE_SELECTION

    async def start_ride(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        lang = self._get_lang(context)
        for key in self.TEMP_FLOW_KEYS:
            context.user_data.pop(key, None)

        context.user_data['language'] = lang
        context.user_data['domain'] = 'ride'

        btn_psg = get_text(lang, "BTN_ROLE_PASSENGER") or "Passenger"
        btn_drv = get_text(lang, "BTN_ROLE_DRIVER") or "Driver"

        keyboard = [
            [InlineKeyboardButton(btn_psg, callback_data="role_passenger")],
            [InlineKeyboardButton(btn_drv, callback_data="role_driver")]
        ]

        start_msg = get_text(
            lang, "MSG_RIDE_START"
        ) or "🚗 Welcome to Mobility Ride-Sharing. Select your operational layout role:"

        await self._send_entry_message(update, context, start_msg, InlineKeyboardMarkup(keyboard))
        return self.ROLE_SELECTION

    async def _send_entry_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE, text: str,
                                  markup: InlineKeyboardMarkup):
        """Helper to send the entry message safely, acknowledging any active callback query first."""
        if update.callback_query:
            try:
                await update.callback_query.answer()
            except Exception as e:
                logger.warning(f"Failed to answer callback query in entry helper: {e}")

        chat_id = update.effective_chat.id if update.effective_chat else None
        if chat_id:
            if update.callback_query and update.callback_query.message:
                await self.safe_edit_message(update, context, text, parse_mode="Markdown", reply_markup=markup)
            else:
                await self.safe_send_message(context, chat_id, text, parse_mode="Markdown", reply_markup=markup)

    async def handle_role(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        query = update.callback_query
        if query:
            try:
                await query.answer()
            except Exception as e:
                logger.warning(f"Failed to answer role callback query: {e}")

        lang = self._get_lang(context)
        role = query.data.split('_')[1] if query and query.data and '_' in query.data else 'shipper'
        context.user_data['role'] = role

        if 'domain' not in context.user_data:
            context.user_data['domain'] = 'ride' if role in ('passenger', 'driver') else 'freight'

        domain = context.user_data.get('domain', 'freight')

        if domain == 'freight':
            prompt = get_text(lang, "PROMPT_ORIGIN_SHIPPER") if role == 'shipper' else get_text(lang,
                                                                                                "PROMPT_ORIGIN_CARRIER")
            prompt = prompt or "📍 Please define your cargo dispatch origin or hub location:"
        else:
            prompt = get_text(lang, "PROMPT_ORIGIN_PASSENGER") if role == 'passenger' else get_text(lang,
                                                                                                    "PROMPT_ORIGIN_DRIVER")
            prompt = prompt or "📍 State your immediate dispatch pick-up point location:"

        chat_id = update.effective_chat.id if update.effective_chat else None
        if query and query.message:
            await self.safe_edit_message(update, context, prompt, parse_mode="Markdown")
        elif chat_id:
            await self.safe_send_message(context, chat_id, prompt, parse_mode="Markdown")

        return self.LOCATION_ORIGIN

    async def handle_origin(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        lang = self._get_lang(context)
        origin = update.message.text.strip() if update.message and update.message.text else ""
        chat_id = update.effective_chat.id if update.effective_chat else None

        if len(origin) < 2:
            err_vague = get_text(lang, "ERR_VAGUE_ADDRESS") or "⚠️ Location description too brief. Try again:"
            if chat_id:
                await self.safe_send_message(context, chat_id, err_vague, parse_mode=None)
            return self.LOCATION_ORIGIN

        context.user_data['origin'] = origin
        domain = context.user_data.get('domain', 'freight')
        role = context.user_data.get('role', 'passenger')

        if domain == 'freight':
            prompt = get_text(lang, "PROMPT_DEST_SHIPPER", origin=origin) if role == 'shipper' else get_text(lang,
                                                                                                             "PROMPT_DEST_CARRIER",
                                                                                                             origin=origin)
            prompt = prompt or f"📍 Origin verified as {html.escape(origin)}. Define destination sector bounds:"
        else:
            if role == 'passenger':
                prompt = get_text(lang, "PROMPT_DEST_PASSENGER",
                                  origin=origin) or "📍 Pickup verified. State targeted destination point:"
            else:
                prompt = get_text(lang, "PROMPT_DEST_DRIVER",
                                  origin=origin) or "📍 Hub location indexed. Identify operating terminal sector bounds:"

        if chat_id:
            await self.safe_send_message(context, chat_id, prompt, parse_mode="Markdown")
        return self.LOCATION_DESTINATION

    async def handle_destination(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        lang = self._get_lang(context)
        destination = update.message.text.strip() if update.message and update.message.text else ""
        chat_id = update.effective_chat.id if update.effective_chat else None

        if len(destination) < 2:
            err_dest = get_text(lang,
                                "ERR_INVALID_DESTINATION") or "⚠️ Invalid location notation formatting. Re-enter destination:"
            if chat_id:
                await self.safe_send_message(context, chat_id, err_dest, parse_mode=None)
            return self.LOCATION_DESTINATION

        context.user_data['destination'] = destination
        context.user_data['destination_text'] = destination
        context.user_data['dropoff_location'] = destination

        origin_val = (
                context.user_data.get('origin')
                or context.user_data.get('origin_text')
                or context.user_data.get('pickup_location')
                or 'Origin'
        )
        route_str = f"{origin_val} -> {destination}"
        context.user_data['route_string'] = route_str
        context.user_data['operating_route'] = route_str

        prompt_region = get_text(
            lang, "PROMPT_ENTER_OPERATING_REGION"
        ) or "📍 Please enter your specific operating region (e.g., Somalia) or route (e.g., Jijiga to Addis Ababa):"

        if chat_id:
            await self.safe_send_message(context, chat_id, prompt_region, parse_mode=None)
        return self.LOCATION_REGION

    async def handle_region(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        lang = self._get_lang(context)
        region_input = update.message.text.strip() if update.message and update.message.text else ""
        chat_id = update.effective_chat.id if update.effective_chat else None

        if len(region_input) < 2:
            err_region = get_text(lang,
                                  "ERR_INVALID_REGION") or "⚠️ Invalid region formatting. Re-enter operating region or route:"
            if chat_id:
                await self.safe_send_message(context, chat_id, err_region, parse_mode=None)
            return self.LOCATION_REGION

        route_separators = [" to ", "->", " - "]
        is_route = any(sep in region_input.lower() for sep in route_separators)

        if is_route:
            context.user_data['route_string'] = region_input
            context.user_data['operating_route'] = region_input
            context.user_data['operating_region'] = region_input
            context.user_data['required_region'] = region_input

            for sep in route_separators:
                if sep in region_input.lower():
                    parts = region_input.split(sep, 1)
                    if len(parts) == 2:
                        orig = parts[0].strip()
                        dest = parts[1].strip()
                        context.user_data['origin'] = orig
                        context.user_data['origin_text'] = orig
                        context.user_data['pickup_location'] = orig
                        context.user_data['destination'] = dest
                        context.user_data['destination_text'] = dest
                        context.user_data['dropoff_location'] = dest
                    break
        else:
            context.user_data['operating_region'] = region_input
            context.user_data['required_region'] = region_input
            context.user_data['region'] = region_input

        return await self._request_gps_telemetry(update, context)

    async def _request_gps_telemetry(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        lang = self._get_lang(context)
        btn_gps = get_text(lang, "BTN_SHARE_GPS") or "📍 Share GPS Location"
        keyboard = [[KeyboardButton(btn_gps, request_location=True)]]

        prompt_gps = (
                get_text(lang, "PROMPT_GPS_CALIBRATION")
                or "🛰️ **GPS Calibration**\n\n"
                   "• **Mobile users:** Tap the button below to share your GPS location.\n"
                   "• **Desktop / Manual:** Type your neighborhood or paste coordinates\n"
                   "  *(e.g., `Latitude 8.514477° N, Longitude 39.269257° E`)*."
        )

        chat_id = update.effective_chat.id if update.effective_chat else None
        if chat_id:
            await self.safe_send_message(
                context, chat_id, prompt_gps,
                reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True),
                parse_mode="Markdown"
            )

        return self.GPS_LOCATION

    async def handle_gps(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        lang = self._get_lang(context)
        msg = update.message or update.edited_message
        location = msg.location if msg else None
        chat_id = update.effective_chat.id if update.effective_chat else None

        if location:
            context.user_data['latitude'] = location.latitude
            context.user_data['longitude'] = location.longitude
            context.user_data['is_live_location'] = getattr(location, 'live_period', None) is not None

            ok_gps = get_text(lang, "MSG_GPS_LOCKED") or "🛰️ GPS Telemetry successfully synced."
            if chat_id:
                await self.safe_send_message(context, chat_id, ok_gps, reply_markup=ReplyKeyboardRemove(),
                                             parse_mode=None)
        else:
            text_input = update.message.text.strip() if update.message and update.message.text else None
            parsed_coords = LocationTelemetryHandler.parse_coordinates(text_input) if text_input else None

            if parsed_coords:
                lat, lon = parsed_coords
                context.user_data['latitude'] = lat
                context.user_data['longitude'] = lon
                context.user_data['manual_location_str'] = text_input
                context.user_data['is_live_location'] = False

                template = get_text(lang, "MSG_COORDS_LOCKED")
                ok_coords = (
                    template.format(lat=lat, lon=lon)
                    if template
                    else f"🛰️ Coordinates locked: **{lat:.6f}° N, {lon:.6f}° E**"
                )

                if chat_id:
                    await self.safe_send_message(context, chat_id, ok_coords, reply_markup=ReplyKeyboardRemove(),
                                                 parse_mode="Markdown")
            else:
                if text_input and not text_input.startswith('/'):
                    context.user_data['manual_location_str'] = text_input

                context.user_data['latitude'] = None
                context.user_data['longitude'] = None

                skip_gps = get_text(lang,
                                    "MSG_GPS_BYPASSED") or "⚠️ GPS mapping bypassed. Proceeding via location text parsing."
                if chat_id:
                    await self.safe_send_message(context, chat_id, skip_gps, reply_markup=ReplyKeyboardRemove(),
                                                 parse_mode=None)

        domain = context.user_data.get('domain', 'freight')
        role = context.user_data.get('role', 'passenger')

        if domain == 'freight':
            prompt = (
                         get_text(lang, "PROMPT_CAPACITY_SHIPPER") if role == 'shipper'
                         else get_text(lang, "PROMPT_CAPACITY_CARRIER")
                     ) or "📦 State maximum load capacity mass requirements (in KGs):"
        else:
            prompt = (
                         get_text(lang, "PROMPT_CAPACITY_PASSENGER") if role == 'passenger'
                         else get_text(lang, "PROMPT_CAPACITY_DRIVER")
                     ) or "👥 Define total capacity headcount allocation configuration parameters:"

        if chat_id:
            await self.safe_send_message(context, chat_id, prompt, parse_mode="Markdown")

        return self.CAPACITY_SPECS

    async def handle_capacity(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        lang = self._get_lang(context)
        text = update.message.text.strip() if update.message and update.message.text else ""
        chat_id = update.effective_chat.id if update.effective_chat else None

        number_match = re.search(r'[\d,]+', text)
        raw_text = number_match.group(0).replace(',', '') if number_match else ""

        if not raw_text.isdigit():
            err_clean = get_text(lang,
                                 "ERR_INVALID_NUMBER_CLEAN") or "⚠️ Numeric input matching validation failed. Input integer figures only:"
            if chat_id:
                await self.safe_send_message(context, chat_id, err_clean, parse_mode=None)
            return self.CAPACITY_SPECS

        val = int(raw_text)
        domain = context.user_data.get('domain', 'freight')
        role = context.user_data.get('role', 'passenger')

        if domain == 'freight':
            context.user_data['cargo_weight_kgs' if role == 'shipper' else 'max_weight_kgs'] = val
            prompt = get_text(lang,
                              "PROMPT_CLASS_FREIGHT") or "🚛 Detail required asset trailer class profile type layout (e.g., Flatbed, Box):"
        else:
            context.user_data['party_size' if role == 'passenger' else 'available_seats'] = val
            prompt = get_text(lang,
                              "PROMPT_CLASS_RIDE") or "🚗 Identify preferred vehicle classification specification tier (e.g., Premium, Economy):"

        if chat_id:
            await self.safe_send_message(context, chat_id, prompt, parse_mode="Markdown")
        return self.CLASS_TYPE

    async def handle_class_type(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        lang = self._get_lang(context)
        text = " ".join(update.message.text.split()) if update.message and update.message.text else ""
        chat_id = update.effective_chat.id if update.effective_chat else None

        if len(text) < 3:
            err_brief = get_text(lang,
                                 "ERR_DESC_TOO_BRIEF") or "⚠️ Operational notation description too short. Expand inputs:"
            if chat_id:
                await self.safe_send_message(context, chat_id, err_brief, parse_mode=None)
            return self.CLASS_TYPE

        domain = context.user_data.get('domain', 'freight')
        role = context.user_data.get('role', 'passenger')

        if domain == 'freight':
            context.user_data['required_trailer_type' if role == 'shipper' else 'trailer_type'] = text
            prompt = get_text(lang,
                              "PROMPT_EXTRAS_FREIGHT") or "📝 Identify any specialized parameter profiles or notes (e.g., Hazmat, None):"
        else:
            context.user_data['preferred_class' if role == 'passenger' else 'ride_class'] = text
            prompt = get_text(lang,
                              "PROMPT_EXTRAS_RIDE") or "📝 Define custom requirements constraints or write 'None':"

        if chat_id:
            await self.safe_send_message(context, chat_id, prompt, parse_mode="Markdown")
        return self.AMENITIES_EXTRAS

    async def handle_extras(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        lang = self._get_lang(context)
        text = update.message.text.strip().lower() if update.message and update.message.text else ""
        domain = context.user_data.get('domain', 'freight')
        role = context.user_data.get('role', 'passenger')
        chat_id = update.effective_chat.id if update.effective_chat else None

        hazmat_keyword = (get_text(lang, "KEYWORD_HAZMAT") or "hazmat").lower()
        none_keyword = (get_text(lang, "KEYWORD_NONE") or "none").lower()

        if domain == 'freight':
            is_hazmat = hazmat_keyword in text or 'hazmat' in text
            extras = text.replace('hazmat', '').replace(hazmat_keyword, '').strip(', ')

            if role == 'shipper':
                context.user_data['is_hazardous'] = is_hazmat
                context.user_data['handling_notes'] = extras if extras not in [none_keyword, 'none'] else ""
            else:
                context.user_data['has_hazmat_license'] = is_hazmat
                context.user_data['carrier_rating'] = 5.0

            prompt = get_text(lang, "PROMPT_BUDGET_SHIPPER") if role == 'shipper' else get_text(lang,
                                                                                                "PROMPT_BUDGET_CARRIER")
            prompt = prompt or "💰 Declare targeted operations pricing allocation budget threshold structure (ETB):"
        else:
            amenities = [p.strip() for p in text.split(',')] if text not in [none_keyword, 'none'] else []
            if role == 'passenger':
                context.user_data['desired_amenities'] = amenities
            else:
                context.user_data['amenities'] = amenities
                context.user_data['driver_background_cleared'] = True

            prompt = get_text(lang, "PROMPT_BUDGET_PASSENGER") if role == 'passenger' else get_text(lang,
                                                                                                    "PROMPT_BUDGET_DRIVER")
            prompt = prompt or "💰 Declare estimated execution cost parameter bounds (ETB):"

        if chat_id:
            await self.safe_send_message(context, chat_id, prompt, parse_mode="Markdown")
        return self.BUDGET_FARE

    async def handle_budget(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        lang = self._get_lang(context)
        chat_id = update.effective_chat.id if update.effective_chat else None

        if not update.message or not update.message.text:
            err_budget = get_text(lang,
                                  "ERR_INVALID_BUDGET") or "⚠️ Financial metric computation matching parsing failed. Re-enter number digits:"
            if chat_id:
                await self.safe_send_message(context, chat_id, err_budget, parse_mode=None)
            return self.BUDGET_FARE

        text = update.message.text.strip()
        upper_text = text.upper()

        # 1. Robust Currency Detection
        if "$" in text or any(token in upper_text for token in ["USD", "DOLLAR"]):
            currency = "USD"
        elif any(token in upper_text for token in ["ETB", "BIRR", "BR", "ብር", "QARSHII"]):
            currency = "ETB"
        else:
            currency = context.user_data.get('currency', 'ETB')

        # 2. Robust Numeric Extraction using Regex (strips out all letters, currency symbols, and commas)
        # Removes everything except digits and decimal points
        cleaned_numeric = re.sub(r'[^\d.]', '', text.replace(',', ''))

        val = None
        try:
            if cleaned_numeric:
                val = float(cleaned_numeric)
        except (ValueError, TypeError):
            val = None

        if val is None or val <= 0:
            err_budget = get_text(lang,
                                  "ERR_INVALID_BUDGET") or "⚠️ Financial metric computation matching parsing failed. Re-enter number digits:"
            if chat_id:
                await self.safe_send_message(context, chat_id, err_budget, parse_mode=None)
            return self.BUDGET_FARE

        domain = context.user_data.get('domain', 'freight')
        role = context.user_data.get('role', 'passenger')

        context.user_data['currency'] = currency
        if domain == 'freight':
            if role == 'shipper':
                context.user_data['max_budget'] = val
            else:
                context.user_data['shipping_quote'] = val
        else:
            if role == 'passenger':
                context.user_data['max_budget'] = val
            else:
                context.user_data['estimated_fare'] = val

        prompt_contact = get_text(lang,
                                  "PROMPT_CONTACT_INFO") or "📞 Provide verified contact telephone connectivity number strings to finish:"
        if chat_id:
            await self.safe_send_message(context, chat_id, prompt_contact, parse_mode="Markdown")
        return self.CONTACT_INFO

    async def handle_contact_info(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        lang = self._get_lang(context)
        contact = update.message.text.strip() if update.message and update.message.text else ""
        context.user_data['contact_phone'] = contact
        chat_id = update.effective_chat.id if update.effective_chat else None

        role = context.user_data.get('role', 'passenger')

        if role in ['carrier', 'driver'] and not context.user_data.get('vehicle_plate'):
            prompt_text = get_text(lang,
                                   "MSG_REQUEST_PLATE") or "🚗 Please enter your official vehicle plate registration number (e.g., AA-3-B12345):"
            if chat_id:
                await self.safe_send_message(context, chat_id, prompt_text, parse_mode=None)
            return self.PLATE_REGISTRATION

        return await self._finalize_pipeline(update, context)

    async def handle_vehicle_plate(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        lang = self._get_lang(context)
        plate = update.message.text.strip().upper() if update.message and update.message.text else ""
        chat_id = update.effective_chat.id if update.effective_chat else None

        if not plate or len(plate) < 2:
            error_text = get_text(lang,
                                  "ERR_INVALID_PLATE") or "⚠️ Invalid plate registration format. Please enter a valid alphanumeric plate number:"
            if chat_id:
                await self.safe_send_message(context, chat_id, error_text, parse_mode=None)
            return self.PLATE_REGISTRATION

        context.user_data['vehicle_plate'] = plate

        prompt_text = get_text(lang,
                               "prompt_vehicle_photo") or "📸 Please send a photo of your vehicle/truck (or type 'skip' to bypass):"
        if chat_id:
            await self.safe_send_message(context, chat_id, prompt_text, parse_mode="Markdown")
        return self.PHOTO_UPLOAD

    async def handle_unsupported_photo_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        lang = self._get_lang(context)
        chat_id = update.effective_chat.id if update.effective_chat else None

        err_text = get_text(lang,
                            "err_photo_invalid") or "⚠️ Please send a valid vehicle photo or type 'Skip' to continue."
        if chat_id:
            await self.safe_send_message(context, chat_id, err_text, parse_mode=None)

        # Explicitly return PHOTO_UPLOAD state so conversation stays active
        return getattr(self, "PHOTO_UPLOAD", 11)

    async def handle_photo_upload(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        logger.info(
            f"🟢 [ENTER] handle_photo_upload invoked for user: {update.effective_user.id if update.effective_user else 'Unknown'}"
        )
        lang = self._get_lang(context)
        skip_keyword = (get_text(lang, "keyword_skip") or "skip").lower()
        domain = context.user_data.get('domain', 'freight')
        chat_id = update.effective_chat.id if update.effective_chat else None
        photo_upload_state = getattr(self, "PHOTO_UPLOAD", 11)

        try:
            # Check for either a compressed photo or an uncompressed image document/file
            is_photo_present = bool(update.message and (update.message.photo or update.message.document))
            is_skip_text = False

            if not is_photo_present and update.message and update.message.text:
                message_text = update.message.text.strip().lower()
                if message_text in ['skip', skip_keyword, '/skip']:
                    is_skip_text = True

            if is_photo_present:
                progress_msg = None
                try:
                    # 1. Send progress message FIRST so the user instantly sees feedback
                    progress_text = get_text(lang, "msg_uploading_b2") or "⏳ Uploading photo to secure storage..."
                    if chat_id:
                        progress_msg = await self.safe_send_message(context, chat_id, progress_text, parse_mode=None)

                    # 2. Safely fetch file using context.bot with a strict 15-second timeout
                    if update.message.photo:
                        photo = update.message.photo[-1]
                        file_id = photo.file_id
                        logger.info(f"Initiating photo download for file_id: {file_id}")
                    else:
                        doc = update.message.document
                        file_id = doc.file_id
                        logger.info(f"Initiating document image download for file_id: {file_id}")

                    file = await asyncio.wait_for(context.bot.get_file(file_id), timeout=15.0)
                    file_bytes = await asyncio.wait_for(file.download_as_bytearray(), timeout=15.0)
                    filename = f"{domain}_assets/{uuid.uuid4().hex[:12]}.jpg"

                    # 3. Upload buffer to B2/S3 storage service with a strict 20-second timeout
                    logger.info(f"Uploading buffer to storage service: {filename}")
                    if not hasattr(self, 'image_service') or not self.image_service:
                        raise AttributeError("image_service is uninitialized on handler instance.")

                    s3_url = await asyncio.wait_for(
                        self.image_service.upload_buffer(file_bytes, filename),
                        timeout=20.0
                    )

                    context.user_data['asset_photo_url'] = s3_url
                    context.user_data['photo_url'] = s3_url
                    logger.info(f"Photo successfully uploaded to storage: {s3_url}")

                    success_text = get_text(lang, "msg_photo_success") or "✅ Photo uploaded successfully!"

                    if progress_msg:
                        try:
                            await progress_msg.edit_text(success_text)
                        except Exception:
                            await self.safe_send_message(context, chat_id, success_text, parse_mode=None)

                except asyncio.TimeoutError:
                    logger.error("Photo download or S3 upload timed out after 15-20 seconds.")
                    context.user_data['asset_photo_url'] = None
                    context.user_data['photo_url'] = None
                    timeout_text = get_text(lang,
                                            "msg_photo_timeout") or "⚠️ Photo upload timed out. Continuing without photo."

                    if progress_msg:
                        try:
                            await progress_msg.edit_text(timeout_text)
                        except Exception:
                            await self.safe_send_message(context, chat_id, timeout_text, parse_mode=None)
                    elif chat_id:
                        await self.safe_send_message(context, chat_id, timeout_text, parse_mode=None)

                except Exception as e:
                    logger.exception(f"Photo download or storage upload failed with exception: {e}")
                    context.user_data['asset_photo_url'] = None
                    context.user_data['photo_url'] = None
                    error_text = get_text(lang,
                                          "msg_photo_timeout") or "⚠️ Photo upload failed. Continuing without photo."

                    if progress_msg:
                        try:
                            await progress_msg.edit_text(error_text)
                        except Exception:
                            await self.safe_send_message(context, chat_id, error_text, parse_mode=None)
                    elif chat_id:
                        await self.safe_send_message(context, chat_id, error_text, parse_mode=None)

            elif is_skip_text:
                context.user_data['asset_photo_url'] = None
                context.user_data['photo_url'] = None
                logger.info("User skipped photo upload.")
            else:
                # Invalid input (e.g. text that isn't skip, stickers, audio, etc.)
                err_text = get_text(lang,
                                    "err_photo_invalid") or "⚠️ Please send a valid photo or type 'Skip' to continue."
                if chat_id:
                    await self.safe_send_message(context, chat_id, err_text, parse_mode=None)
                # ✅ Explicitly return photo upload state so conversation doesn't drop to None
                return photo_upload_state

            # ✅ Protected finalization dispatch ensuring smooth transition to _finalize_pipeline
            logger.info("Proceeding to execute _finalize_pipeline after photo upload gate.")
            pipeline_result = await self._finalize_pipeline(update, context)
            logger.info(f"🟢 [EXIT] _finalize_pipeline returned state: {pipeline_result}")

            # Ensure we never return None/END unless explicitly intended
            if pipeline_result is None:
                logger.warning("_finalize_pipeline returned None. Routing back to main menu safely.")
                return await self.route_to_main_menu(update, context)

            return pipeline_result

        except Exception as pipeline_err:
            logger.exception(f"Critical error in handle_photo_upload pipeline: {pipeline_err}")
            err_msg = get_text(lang,
                               "ERR_SYS_PIPELINE_FAILED") or "⚠️ An internal pipeline error occurred. Returning to main menu."
            if chat_id:
                await self.safe_send_message(context, chat_id, err_msg, parse_mode=None)
            return await self.route_to_main_menu(update, context)

    # ==========================================
    # FINALIZATION PIPELINE (REGISTRATION & FORWARD MATCHING)
    # ==========================================

    async def _finalize_pipeline(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        if not hasattr(self, '_translation_cache'):
            self._translation_cache = {}

        lang = self._get_lang(context)
        domain = context.user_data.get('domain', 'freight')
        role = context.user_data.get('role', 'passenger')
        user_id = update.effective_user.id if update.effective_user else 0
        chat_id = update.effective_chat.id if update.effective_chat else user_id
        effective_msg = update.effective_message

        compiling_msg = (
                get_text(lang, "MSG_COMPILING_PROFILE")
                or "⏳ Compiling profile and syncing logistics nodes..."
        )
        processing_msg = None

        if effective_msg:
            try:
                processing_msg = await effective_msg.reply_text(compiling_msg)
            except Exception as e:
                logger.warning(f"Failed to dispatch preliminary compilation status message: {e}")

        try:
            # Safe floating-point normalization preventing DB schema type errors
            for numeric_key in [
                'max_budget', 'estimated_fare', 'shipping_quote', 'cargo_weight_kgs',
                'max_weight_kgs', 'party_size', 'available_seats', 'price'
            ]:
                if numeric_key in context.user_data:
                    context.user_data[numeric_key] = safe_float(context.user_data[numeric_key])

            invalid_placeholders = {"", "N/A", "NOT_SPECIFIED", "NONE", "NULL", "UNSPECIFIED", "ANY", "NOE"}

            def get_fallback_val(keys: list, default: Any = "") -> Any:
                for k_val in keys:
                    fal_val = context.user_data.get(k_val)
                    if fal_val is not None:
                        if isinstance(fal_val, (int, float, bool)):
                            return fal_val
                        cleaned = str(fal_val).strip()
                        if cleaned and cleaned.upper() not in invalid_placeholders:
                            return cleaned
                return default

            # Multi-key parameter extraction
            phone = get_fallback_val(['contact_phone', 'phone', 'mobile'], '')
            currency = str(get_fallback_val(['currency'], 'ETB')).upper()
            origin_loc = apply_city_fixes(
                get_fallback_val(['location_text', 'origin', 'origin_text', 'departure_text'], '')
            )
            dest_loc = apply_city_fixes(
                get_fallback_val(['destination_text', 'destination', 'dest_text'], '')
            )
            operating_region = get_fallback_val(
                ['operating_region', 'required_region', 'route_string', 'region'], ''
            )
            lat = safe_float(get_fallback_val(['latitude', 'lat'], 0.0))
            lon = safe_float(get_fallback_val(['longitude', 'lon'], 0.0))

            # ----------------------------------------------------
            # CASE A: PROVIDER REGISTRATION (Carriers & Drivers)
            # ----------------------------------------------------
            if role in ['carrier', 'driver']:
                vertical = 'freight' if domain == 'freight' else 'ride'
                quantity = int(safe_float(get_fallback_val(['quantity', 'bulk_quantity', 'count'], 1)))

                price = safe_float(
                    get_fallback_val(['shipping_quote', 'estimated_fare', 'price', 'cost', 'rate'], 0.0)
                )
                context.user_data['price'] = price

                if domain == 'ride':
                    raw_class = get_fallback_val(['ride_class', 'vehicle_type', 'property_use'], 'Standard')
                    raw_amenities = context.user_data.get('amenities', [])
                    clean_amenities = (
                        ", ".join([str(a).strip() for a in raw_amenities if a])
                        if isinstance(raw_amenities, list)
                        else str(raw_amenities or "")
                    )
                else:
                    raw_class = get_fallback_val(['trailer_type', 'truck_type', 'property_use'], 'Standard')
                    clean_amenities = get_fallback_val(['handling_notes', 'amenities', 'notes'], '')

                prop_use = str(raw_class).strip()
                is_lexicon_match = False
                if prop_use.lower() in REGIONAL_TRANSPORT_LEXICON:
                    prop_use = REGIONAL_TRANSPORT_LEXICON[prop_use.lower()]
                    is_lexicon_match = True

                final_photo_url = get_fallback_val(
                    ['photo_url', 'asset_photo_url', 'vehicle_photo', 'image_url'], ''
                )
                vehicle_plate = get_fallback_val(['vehicle_plate', 'plate_number', 'plate'], 'N/A')

                resolved_operating_val = (
                        context.user_data.get('operating_region')
                        or context.user_data.get('operating_route')
                        or context.user_data.get('route_string')
                        or operating_region
                )

                listing_data = {
                    "provider_id": user_id,
                    "vertical_type": vertical,
                    "transaction_type": "service",
                    "property_use": prop_use,
                    "location_text": origin_loc,
                    "destination_text": dest_loc,
                    "operating_region": resolved_operating_val,
                    "latitude": lat,
                    "longitude": lon,
                    "price": price,
                    "currency": currency,
                    "amenities": clean_amenities,
                    "vehicle_plate": vehicle_plate,
                    "photo_url": final_photo_url,
                    "contact_phone": phone,
                    "status": "active",
                    "bulk_quantity": quantity,
                }

                if vertical == 'freight':
                    listing_data['max_weight_kgs'] = safe_float(
                        get_fallback_val(['max_weight_kgs', 'cargo_weight_kgs', 'capacity_kgs', 'weight_kgs'], 0.0)
                    )
                    listing_data['has_hazmat_license'] = bool(context.user_data.get('has_hazmat_license', False))
                elif vertical == 'ride':
                    listing_data['available_seats'] = int(
                        safe_float(
                            get_fallback_val(['available_seats', 'seats_available', 'party_size', 'seats_needed'], 1)
                        )
                    )

                # AI PIPELINE ENHANCEMENT (With strict timeout safeguard)
                if hasattr(self, 'ai') and hasattr(self.ai, 'parse_listing_to_english'):
                    try:
                        english_parsed_data = await asyncio.wait_for(
                            self.ai.parse_listing_to_english(listing_data),
                            timeout=7
                        )

                        if isinstance(english_parsed_data, str):
                            match_json = re.search(r'\{.*}', english_parsed_data, re.DOTALL)
                            if match_json:
                                try:
                                    english_parsed_data = json.loads(match_json.group(0))
                                except json.JSONDecodeError:
                                    pass

                        if isinstance(english_parsed_data, dict) and english_parsed_data:
                            extracted_title = english_parsed_data.get('title')
                            if extracted_title and not is_lexicon_match and " from " not in extracted_title.lower():
                                listing_data['property_use'] = extracted_title

                            location = english_parsed_data.get('location')
                            if location:
                                listing_data['location_text'] = apply_city_fixes(location)

                            specs = english_parsed_data.get('specifications', {})
                            if isinstance(specs, dict) and specs:
                                if vertical == 'freight':
                                    root_w = specs.get('max_weight_kgs') or specs.get('weight') or specs.get('capacity')
                                    if root_w:
                                        listing_data['max_weight_kgs'] = safe_float(
                                            str(root_w).replace(',', '').split()[0]
                                        )
                                elif vertical == 'ride':
                                    root_s = specs.get('available_seats') or specs.get('seats')
                                    if root_s:
                                        listing_data['available_seats'] = int(
                                            str(root_s).replace(',', '').split()[0]
                                        )

                            desc = str(english_parsed_data.get('description', '')).strip()
                            if desc and desc.lower() not in ['none', 'null', 'n/a']:
                                listing_data['amenities'] = desc
                    except asyncio.TimeoutError:
                        logger.warning("AI parsing mapping timed out (>7s). Skipping AI enhancement for speed.")
                    except Exception as ai_err:
                        logger.warning(f"AI parsing mapping skipped due to validation block: {ai_err}")

                # PARALLEL INBOUND TRANSLATION SAFEGUARD (With strict 2s timeout)
                try:
                    fields_to_translate = ['property_use', 'amenities']
                    tasks = []
                    keys_to_update = []

                    for field in fields_to_translate:
                        val = listing_data.get(field)
                        if val and isinstance(val, str):
                            keys_to_update.append(field)
                            tasks.append(asyncio.wait_for(self._async_translate(val, 'auto', 'en'), timeout=2.0))

                    if tasks:
                        translated_results = await asyncio.gather(*tasks, return_exceptions=True)
                        for k, res in zip(keys_to_update, translated_results):
                            if isinstance(res, str) and res:
                                listing_data[k] = res

                    # Direct city fix normalizations
                    for loc_field in ['location_text', 'destination_text']:
                        if listing_data.get(loc_field):
                            listing_data[loc_field] = apply_city_fixes(listing_data[loc_field])

                except Exception as safeguard_err:
                    logger.warning(f"Translation safeguard skipped: {safeguard_err}")

                # Database Persistence
                listing_record = None
                if hasattr(self, 'db'):
                    for _ in range(quantity):
                        if hasattr(self.db, 'create_listing'):
                            listing_record = await self.db.create_listing(listing_data)
                        else:
                            listing_record = await self.db.create_provider_listing("provider_listings", user_id,
                                                                                   listing_data)

                        if not listing_record:
                            raise Exception(f"Supabase persistence creation failed for {vertical}.")

                    logger.info(f"{quantity} {vertical.title()} listing(s) successfully created for User ID: {user_id}")

                    if hasattr(self, 'matching') and hasattr(self.matching, 'run_provider_matching') and listing_record:
                        asyncio.create_task(self.matching.run_provider_matching(listing_record.get('id'), vertical))

                if processing_msg:
                    try:
                        await processing_msg.delete()
                    except Exception:
                        pass

                if listing_record:
                    return await self.trigger_reverse_matching(update, context, listing_record, vertical)

                success_text = (
                        get_text(lang, "MSG_REGISTRATION_COMPLETE", domain=domain.title())
                        or f"✅ **{domain.title()} Asset Registration Complete!**"
                )
                return await self.route_to_main_menu(update, context, custom_msg=success_text)

            # ----------------------------------------------------
            # CASE B: SEEKER REQUESTS (Passengers & Shippers)
            # ----------------------------------------------------
            else:
                if processing_msg:
                    # Dynamically retrieve localized domain label based on user language preference
                    domain_label = (
                            get_text(lang, f"domain_{domain.lower()}")
                            or ("Ride" if domain == 'ride' else "Freight")
                    )

                    raw_text = (
                            get_text(lang, "MSG_SCANNING_NODES")
                            or "🔍 Scanning <b>{domain}</b> nodes and interrogating the match matrix..."
                    )
                    try:
                        scanning_text = raw_text.format(domain=domain_label)
                        await processing_msg.edit_text(scanning_text, parse_mode="HTML")
                    except Exception:
                        pass

                if domain == 'ride':
                    specific_type = get_fallback_val(['preferred_class', 'vehicle_type', 'property_use', 'ride_class'],
                                                     'Any')
                else:
                    specific_type = get_fallback_val(['required_trailer_type', 'truck_type', 'property_use'], 'Any')

                raw_amenities = (
                        context.user_data.get('desired_amenities')
                        or context.user_data.get('amenities')
                        or context.user_data.get('handling_notes')
                        or []
                )

                if isinstance(raw_amenities, list):
                    formatted_amenities = ", ".join([str(a).strip() for a in raw_amenities if a])
                else:
                    formatted_amenities = str(raw_amenities).strip()

                resolved_operating_val = (
                        context.user_data.get('operating_region')
                        or context.user_data.get('operating_route')
                        or context.user_data.get('route_string')
                        or operating_region
                )

                # Parallel translation for seeker requirements with 5s timeout safeguard
                try:
                    translated_specific_type, translated_amenities = await asyncio.wait_for(
                        asyncio.gather(
                            self._async_translate(specific_type, 'auto', 'en'),
                            self._async_translate(formatted_amenities, 'auto', 'en')
                        ),
                        timeout=5.0
                    )
                except Exception:
                    translated_specific_type, translated_amenities = specific_type, formatted_amenities

                req_payload = {
                    "seeker_id": user_id,
                    "vertical_type": domain,
                    "transaction_type": "service",
                    "property_use": translated_specific_type,
                    "location_text": origin_loc,
                    "destination_text": dest_loc,
                    "operating_region": resolved_operating_val,
                    "required_region": resolved_operating_val,
                    "latitude": lat,
                    "longitude": lon,
                    "max_budget": safe_float(get_fallback_val(['max_budget', 'budget', 'price'], 0.0)),
                    "currency": currency,
                    "required_amenities": translated_amenities,
                    "amenities": translated_amenities,
                    "contact_phone": phone,
                    "status": "active"
                }

                if domain == 'freight':
                    req_payload['cargo_weight_kgs'] = safe_float(
                        get_fallback_val(['cargo_weight_kgs', 'max_weight_kgs', 'weight_kgs'], 0.0)
                    )
                    req_payload['is_hazardous'] = bool(get_fallback_val(['is_hazardous', 'has_hazmat'], False))
                    req_payload['handling_notes'] = translated_amenities
                elif domain == 'ride':
                    req_payload['party_size'] = int(
                        safe_float(get_fallback_val(['party_size', 'seats_needed', 'available_seats'], 1))
                    )

                if hasattr(self, 'db') and hasattr(self.db, 'create_request'):
                    try:
                        await self.db.create_request(req_payload)
                    except Exception as db_err:
                        logger.error(f"Failed to persist {domain} seeker request: {db_err}", exc_info=True)

                prefs = {
                    "domain": domain,
                    "role": role,
                    "property_use": translated_specific_type,
                    "origin_text": origin_loc,
                    "destination_text": dest_loc,
                    "route_string": f"{origin_loc} -> {dest_loc}" if origin_loc and dest_loc else "",
                    "latitude": lat,
                    "longitude": lon,
                    "max_budget": req_payload['max_budget'],
                    "currency": currency,
                    "required_amenities": translated_amenities,
                    "amenities": translated_amenities,
                }

                matches = []

                if domain == 'ride':
                    prefs['party_size'] = req_payload.get('party_size', 1)
                    prefs['seats_needed'] = prefs['party_size']
                    prefs['vehicle_type'] = translated_specific_type
                    prefs['departure_lat'] = lat
                    prefs['departure_lon'] = lon
                    prefs['departure_text'] = origin_loc
                elif domain == 'freight':
                    prefs['weight_kgs'] = req_payload.get('cargo_weight_kgs', 0.0)
                    prefs['cargo_type'] = 'hazardous' if req_payload.get('is_hazardous') else 'general'
                    prefs['required_truck_type'] = translated_specific_type
                    prefs['origin_lat'] = lat
                    prefs['origin_lon'] = lon
                    prefs['origin_text'] = origin_loc

                candidates = []
                try:
                    if hasattr(self.db, 'fetch_freight_ride_candidates'):
                        candidates = await self.db.fetch_freight_ride_candidates(
                            domain=domain,
                            location_query=origin_loc,
                            destination_query=dest_loc,
                            operating_region=resolved_operating_val,
                            specific_type=translated_specific_type
                        )
                    elif hasattr(self.db, 'supabase') and self.db.supabase:
                        query = (
                            self.db.supabase.table("provider_listings")
                            .select("*")
                            .eq("vertical_type", domain)
                            .eq("status", "active")
                        )
                        res = await query.execute()
                        candidates = res.data if res and res.data else []
                except Exception as eval_err:
                    logger.error(f"Error fetching candidates for seeker: {eval_err}", exc_info=True)

                if candidates and hasattr(self, 'matching') and hasattr(self.matching, 'evaluate_matches'):
                    try:
                        matches = await self.matching.evaluate_matches(domain, prefs, candidates)
                    except Exception as e:
                        logger.error(f"Match evaluation failure: {e}", exc_info=True)

                if not matches and hasattr(self, 'matching') and hasattr(self.matching, 'evaluate_semantic_matches'):
                    logger.info("Strict matching returned zero results. Activating Semantic model fallback...")
                    prefs['allow_semantic_fallback'] = True
                    try:
                        if hasattr(self.db, 'get_active_listings_by_domain'):
                            all_domain_candidates = await self.db.get_active_listings_by_domain(domain)
                            if all_domain_candidates:
                                candidates = all_domain_candidates

                        if candidates:
                            matches = await self.matching.evaluate_semantic_matches(domain, prefs, candidates)
                    except Exception as e:
                        logger.error(f"Semantic match evaluation failure: {e}", exc_info=True)

                if not matches:
                    localized_domain = get_text(lang, f"domain_{domain.lower()}") or domain.title()
                    broadcast_template = (
                            get_text(lang, "MSG_REQUEST_BROADCASTED")
                            or "📡 Request broadcasted to the {domain} network. No immediate matches found, but we will notify you when a match is secured."
                    )
                    broadcast_text = broadcast_template.format(domain=localized_domain)

                    if processing_msg:
                        try:
                            await processing_msg.delete()
                        except Exception:
                            pass
                    return await self.route_to_main_menu(update, context, custom_msg=broadcast_text)

                top_matches = matches[:3]
                context.user_data['freight_top_matches'] = top_matches

                domain_emoji = "🚚" if domain == 'freight' else "🚗"

                # 1. ቋንቋን በደህና ሁኔታ ማስተካከል
                norm_lang = str(lang).lower().strip().split('_')[0] if lang else 'en'
                if norm_lang not in ('en', 'am', 'om'):
                    norm_lang = 'en'

                # =====================================================================
                # STANDALONE INLINE-FIRST DOMAIN TRANSLATIONS (አስቀድሞ የሚተረጎም)
                # =====================================================================
                DOMAIN_TRANSLATIONS = {
                    'freight': {'am': 'የጭነት', 'om': "Fe'umsa", 'en': 'Freight'},
                    'ride': {'am': 'የጉዞ', 'om': 'Imala', 'en': 'Ride'}
                }

                # የጎራውን ስም (Domain) አስቀድሞ በቋንቋው መሰረት መተርጎም
                localized_domain = (
                        DOMAIN_TRANSLATIONS.get(domain.lower(), {}).get(norm_lang)
                        or DOMAIN_TRANSLATIONS.get(domain.lower(), {}).get('en')
                        or get_text(lang, f"domain_{domain.lower()}")
                        or domain.title()
                )
                if not localized_domain or str(localized_domain).startswith("domain_"):
                    localized_domain = DOMAIN_TRANSLATIONS.get(domain.lower(), {}).get('en', domain.title())

                # =====================================================================
                # BULLETPROOF TEXT CLEANER (Removes bare [domain_freight] or placeholders)
                # =====================================================================
                def sanitize_text(text: str) -> str:
                    if not text:
                        return ""
                    return (
                        str(text)
                        .replace("[domain_freight]", localized_domain)
                        .replace("[domain_ride]", localized_domain)
                        .replace("{domain_freight}", localized_domain)
                        .replace("{domain}", localized_domain)
                    )

                # =====================================================================
                # 2. 🔍 የሎዲንግ/ስካኒንግ መልእክት ዘላቂ መፍትሔ (Fix for MSG_SCANNING_NODES)
                # =====================================================================
                SCANNING_TEMPLATES = {
                    'am': f"🔍 የ<b>{localized_domain}</b> ኖዶችን በመቃኘት እና የማዛመጃ ማትሪክስ በመመርመር ላይ...",
                    'om': f"🔍 Nudoota <b>{localized_domain}</b> sakatta'aa fi maatriksii wal-simannaa qorachaa jira...",
                    'en': f"🔍 Scanning <b>{localized_domain}</b> nodes and interrogating the match matrix..."
                }

                dict_scanning = get_text(lang, "MSG_SCANNING_NODES", domain=localized_domain)
                scanning_msg_text = (
                        SCANNING_TEMPLATES.get(norm_lang)
                        or (dict_scanning if dict_scanning and not str(dict_scanning).startswith("MSG_") else None)
                        or SCANNING_TEMPLATES['en']
                )

                if processing_msg:
                    try:
                        formatted_text = sanitize_text(scanning_msg_text)
                        await processing_msg.edit_text(text=formatted_text, parse_mode="HTML")
                    except Exception:
                        pass

                # =====================================================================
                # 3. 🎯 የራስጌ መልእክት አብነቶች (HEADER TEMPLATES)
                # =====================================================================
                HEADER_TEMPLATES = {
                    'am': f"🎯 **ምርጥ {len(top_matches)} የ{localized_domain} ተስማሚዎች ተገኝተዋል!**\n\nእባክዎ አቅርቦት ለመላክ ከታች ከሚገኙት አማራጮች አንዱን ይምረጡ፡",
                    'om': f"🎯 **Filannoo {len(top_matches)} {localized_domain} Barbaadaman Argamaniiru!**\n\nMaaloo dhiheessii ergachuuf karaa armaan gadii irraa filadhu:",
                    'en': f"🎯 **Top {len(top_matches)} {localized_domain} Matches Found!**\n\nPlease select an option below to review and send an offer:"
                }

                dict_msg = get_text(lang, "MSG_TOP_FREIGHT_MATCHES", count=len(top_matches), domain=localized_domain)
                msg = sanitize_text(
                    HEADER_TEMPLATES.get(norm_lang)
                    or (dict_msg if dict_msg and not str(dict_msg).startswith("MSG_") else None)
                    or HEADER_TEMPLATES['en']
                )

                # PARALLEL OUTBOUND LABEL TRANSLATIONS (ከ2 ሰከንድ ማለቂያ ጋር)
                label_tasks = []
                for match in top_matches:
                    raw_label = (
                            match.get('vehicle_plate')
                            or match.get('plate')
                            or match.get('license_plate')
                            or match.get('property_use')
                            or match.get('trailer_type')
                            or match.get('truck_type')
                            or match.get('vehicle_type')
                    )
                    if raw_label and str(raw_label).strip().upper() not in ("", "N/A", "NONE", "NULL"):
                        label_str = str(raw_label).strip()
                    else:
                        carrier_fallbacks = {
                            'am': 'የጭነት አቅራቢ' if domain == 'freight' else 'የጉዞ አቅራቢ',
                            'om': "Dhiyeessaa Fe'umsaa" if domain == 'freight' else "Dhiyeessaa Imalaa",
                            'en': f"{localized_domain} Carrier"
                        }
                        label_str = carrier_fallbacks.get(norm_lang, carrier_fallbacks['en'])

                    label_tasks.append(
                        asyncio.wait_for(self._async_translate(label_str, 'auto', norm_lang), timeout=2.0)
                    )

                try:
                    translated_labels = await asyncio.gather(*label_tasks, return_exceptions=True)
                except Exception:
                    fallback_defaults = {
                        'am': 'የጭነት አቅራቢ' if domain == 'freight' else 'የጉዞ አቅራቢ',
                        'om': "Dhiyeessaa Fe'umsaa" if domain == 'freight' else "Dhiyeessaa Imalaa",
                        'en': f"{localized_domain} Carrier"
                    }
                    fallback_default = fallback_defaults.get(norm_lang, fallback_defaults['en'])
                    translated_labels = [fallback_default] * len(top_matches)

                keyboard = []
                for i, (match, translated_label) in enumerate(zip(top_matches, translated_labels), start=1):
                    match_id = str(match.get('id', uuid.uuid4()))

                    display_label = (
                        str(translated_label)
                        if (isinstance(translated_label, str) and translated_label and not isinstance(translated_label,
                                                                                                      Exception))
                        else localized_domain
                    )

                    fare = safe_float(match.get('price', 0.0))
                    score = int(match.get('_match_score', 95))

                    currency_val = (
                            match.get('currency')
                            or match.get('fare_currency')
                            or match.get('price_currency')
                            or currency
                    )
                    currency_val = str(currency_val).strip().upper()

                    button_label = f"{domain_emoji} #{i}: {display_label} ({fare:,.2f} {currency_val} - {score}%)"
                    keyboard.append(
                        [InlineKeyboardButton(button_label, callback_data=f"select_freight_match:{match_id}")]
                    )

                btn_decline_defaults = {
                    'am': '❌ አልፈልግም (ተመለስ)',
                    'om': "❌ Dhiisittii Deebi'i",
                    'en': '❌ Decline & Return'
                }
                btn_decline = get_text(lang, "BTN_DECLINE_RETURN") or btn_decline_defaults.get(norm_lang,
                                                                                               btn_decline_defaults[
                                                                                                   'en'])
                keyboard.append([InlineKeyboardButton(btn_decline, callback_data="cancel_match")])

                if processing_msg:
                    try:
                        await processing_msg.delete()
                    except Exception:
                        pass

                # የመጨረሻውን የፍለጋ ውጤት መላክ (በ sanitize_text የተጠበቀ)
                await self.safe_send_message(
                    context=context,
                    chat_id=chat_id,
                    text=sanitize_text(msg),
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="Markdown"
                )
                return getattr(self, "SELECTING_MATCH", 99)

        except Exception as e:
            logger.exception(f"Critical operational pipeline system fault for user {user_id}: {e}")
            system_err_text = (
                    get_text(lang, "ERR_SYS_PIPELINE_FAILED")
                    or "⚠️ An internal pipeline anomaly occurred. Please restart the process."
            )
            if processing_msg:
                try:
                    await processing_msg.delete()
                except Exception:
                    pass
            return await self.route_to_main_menu(update, context, custom_msg=system_err_text)

    # ==========================================
    # REVERSE MATCHING TRIGGER (Provider -> Seeker)
    # ==========================================

    async def trigger_reverse_matching(
            self,
            update: Update,
            context: ContextTypes.DEFAULT_TYPE,
            listing_record: dict,
            vertical: str
    ) -> int:
        if not hasattr(self, '_translation_cache'):
            self._translation_cache = {}

        lang = self._get_lang(context)
        norm_lang = str(lang).lower().strip().split('_')[0] if lang else 'en'
        if norm_lang not in ('en', 'am', 'om'):
            norm_lang = 'en'

        user_id = update.effective_user.id if update.effective_user else 0
        chat_id = update.effective_chat.id if update.effective_chat else user_id
        effective_msg = update.effective_message

        # =====================================================================
        # 🌐 DOMAIN & LOCALIZED LABEL RESOLUTION
        # =====================================================================
        DOMAIN_TRANSLATIONS = {
            'freight': {'am': 'የጭነት', 'om': "Fe'umsa", 'en': 'Freight'},
            'ride': {'am': 'የጉዞ', 'om': 'Imala', 'en': 'Ride'}
        }

        localized_vertical = (
                DOMAIN_TRANSLATIONS.get(vertical.lower(), {}).get(norm_lang)
                or DOMAIN_TRANSLATIONS.get(vertical.lower(), {}).get('en')
                or get_text(lang, f"domain_{vertical.lower()}")
                or vertical.title()
        )

        def sanitize_text(text: str) -> str:
            if not text:
                return ""
            return (
                str(text)
                .replace("[domain_freight]", localized_vertical)
                .replace("[domain_ride]", localized_vertical)
                .replace("{domain_freight}", localized_vertical)
                .replace("{domain}", localized_vertical)
                .replace("{vertical}", localized_vertical)
            )

        # Scanning message
        SCANNING_TEMPLATES = {
            'am': f"🔍 ንቁ የ<b>{localized_vertical}</b> ፈላጊ ጥያቄዎችን በመቃኘት ላይ...",
            'om': f"🔍 Gaaffiiwwan barbaadota <b>{localized_vertical}</b> sakatta'aa jira...",
            'en': f"🔍 Scanning active <b>{localized_vertical}</b> Seeker Requests across the matrix..."
        }
        loading_text = sanitize_text(
            SCANNING_TEMPLATES.get(norm_lang)
            or get_text(lang, "msg_scanning_seekers")
            or SCANNING_TEMPLATES['en']
        )

        processing_msg = None
        if effective_msg:
            try:
                processing_msg = await effective_msg.reply_text(loading_text, parse_mode="HTML")
            except Exception as e:
                logger.warning(f"Failed to dispatch preliminary reverse matching status message: {e}")

        listing_type = listing_record.get('transaction_type', 'service')
        listing_record['vertical_type'] = vertical

        try:
            # Fetch active seeker requests from database
            res = await self.db.table('seeker_requests').select('*').eq(
                'vertical_type', vertical
            ).eq('transaction_type', listing_type).eq('status', 'active').execute()

            candidates = res.data if res and hasattr(res, 'data') and res.data else []
            matches = []

            matching_engine = getattr(self, 'matching', None)

            if matching_engine and hasattr(matching_engine, 'evaluate_matches'):
                for req in candidates:
                    if str(req.get('seeker_id')) == str(user_id):
                        continue

                    # Map seeker preferences to align with MatchingEngine standard schema
                    seeker_prefs = {
                        'vertical_type': vertical,
                        'property_use': req.get('property_use') or req.get('vehicle_type'),
                        'max_budget': safe_float(req.get('max_budget', 0.0)),
                        'currency': str(req.get('currency') or 'ETB').strip().upper(),
                        'required_amenities': req.get('required_amenities') or req.get('amenities') or '',
                        'party_size': safe_float(req.get('party_size', 1.0)),
                        'seats_needed': safe_float(req.get('party_size', 1.0)),
                        'cargo_weight_kgs': safe_float(req.get('cargo_weight_kgs', 0.0)),
                        'weight_kgs': safe_float(req.get('cargo_weight_kgs', 0.0)),
                        'origin_text': req.get('location_text') or req.get('origin_text') or '',
                        'destination_text': req.get('destination_text') or '',
                        'latitude': safe_float(req.get('latitude', 0.0)),
                        'longitude': safe_float(req.get('longitude', 0.0))
                    }

                    try:
                        eval_results = await matching_engine.evaluate_matches(vertical, seeker_prefs,
                                                                              [listing_record])
                        if eval_results:
                            score = safe_float(eval_results[0].get('_match_score', 0.0))
                            if score > 0.0:
                                req['_match_score'] = score
                                matches.append(req)
                    except Exception as e:
                        logger.error(f"Reverse match scoring failure for seeker request {req.get('id')}: {e}")

            # =====================================================================
            # 🔄 SEMANTIC FALLBACK ACTIVATION (If strict matching returns zero)
            # =====================================================================
            if not matches and matching_engine and hasattr(matching_engine, 'evaluate_semantic_matches'):
                logger.info(
                    "Strict reverse matching returned zero results. Activating Semantic model fallback...")
                for req in candidates:
                    if str(req.get('seeker_id')) == str(user_id):
                        continue
                    seeker_prefs = {
                        'vertical_type': vertical,
                        'property_use': req.get('property_use') or req.get('vehicle_type'),
                        'max_budget': safe_float(req.get('max_budget', 0.0)),
                        'currency': str(req.get('currency') or 'ETB').strip().upper(),
                        'allow_semantic_fallback': True,
                        'origin_text': req.get('location_text') or req.get('origin_text') or '',
                        'destination_text': req.get('destination_text') or '',
                    }
                    try:
                        sem_results = await matching_engine.evaluate_semantic_matches(vertical, seeker_prefs,
                                                                                      [listing_record])
                        if sem_results:
                            score = safe_float(sem_results[0].get('_match_score', 50.0))
                            req['_match_score'] = max(score, 45.0)  # Semantic baseline
                            matches.append(req)
                    except Exception as sem_err:
                        logger.error(f"Semantic reverse match evaluation failure: {sem_err}")

            matches.sort(key=lambda x: x.get('_match_score', 0), reverse=True)
            top_matches = matches[:3]

            if processing_msg:
                try:
                    await processing_msg.delete()
                except Exception:
                    pass

            if not top_matches:
                fallback_msgs = {
                    "am": f"✅ **ማስታወቂያዎ ተለቀዋል!**\nአሁን ላይ ይህን የ{localized_vertical} ንብረት በግልጽ የጠየቀ ፈላጊ የለም። ሲገኝ በራስ-ሰር እናሳውቆታለን።",
                    "om": f"✅ **Beeksifni Keessan Gad-lakkifameera!**\nAmma tana namni qabeenya {localized_vertical} kana barbaadu hin jiru. Yeroo argamu ofumaan isaa sin beeksifna.",
                    "en": f"✅ **Listing is Live!**\nCurrently, no pending {localized_vertical} seekers explicitly request this class. You will be notified automatically."
                }
                fallback_msg = sanitize_text(
                    get_text(lang, "msg_no_seeker_matches")
                    or fallback_msgs.get(norm_lang, fallback_msgs["en"])
                )
                await self.safe_send_message(context, chat_id, fallback_msg, parse_mode="Markdown")
                return await self.route_to_main_menu(update, context)

            context.user_data['seeker_top_matches'] = top_matches
            context.user_data['last_created_listing_id'] = listing_record.get('id')
            context.user_data['is_reverse_match'] = True

            # Header Templates
            HEADER_TEMPLATES = {
                'am': f"🎯 **ምርጥ {len(top_matches)} የ{localized_vertical} ፈላጊ ተስማሚዎች ተገኝተዋል!**\n\nእነዚህ ተጠቃሚዎች ንብረትዎን የሚያሟላ የትራንስፖርት ጥያቄ አቅርበዋል። አቅርቦትዎን ለመላክ ከታች አንዱን ይምረጡ፡",
                'om': f"🎯 **Barbaadota {localized_vertical} {len(top_matches)} Filataman Argamaniiru!**\n\nNamoonni kunniin tajaajila geejjibaa qabeenya keessan waliin wal-simu gaafachaa jiru. Dhiheessii keessan ergachuuf barbaadaa armaan gadii keessaa filadhu:",
                'en': f"🎯 **Top {len(top_matches)} {localized_vertical} Seeker Matches Found!**\n\nThese users are actively requesting transport or service logic matching your asset. Select a Seeker below to send your offer:"
            }

            msg = sanitize_text(
                HEADER_TEMPLATES.get(norm_lang)
                or get_text(lang, "MSG_TOP_SEEKER_MATCHES", count=len(top_matches), vertical=localized_vertical)
                or HEADER_TEMPLATES['en']
            )

            keyboard = []
            for i, match in enumerate(top_matches, start=1):
                match_id = str(match.get('id', uuid.uuid4()))
                budget = safe_float(match.get('max_budget', 0.0))
                currency = str(match.get('currency') or 'ETB').upper()
                score = int(match.get('_match_score', 95))

                item_template = {
                    'am': f"👤 #{i} ፈላጊ (ባጀት: {budget:,.2f} {currency} - {score}%)",
                    'om': f"👤 #{i} Barbaadaa (Baajata: {budget:,.2f} {currency} - {score}%)",
                    'en': f"👤 #{i} Seeker (Budget: {budget:,.2f} {currency} - {score}%)"
                }
                button_label = (
                        get_text(lang, "BTN_SEEKER_MATCH_ITEM", index=i, budget=f"{budget:,.2f}",
                                 currency=currency, score=score)
                        or item_template.get(norm_lang, item_template['en'])
                )

                keyboard.append(
                    [InlineKeyboardButton(button_label, callback_data=f"fr_select_seeker_match:{match_id}")]
                )

            btn_decline_defaults = {
                'am': '❌ አልፈልግም (ተመለስ)',
                'om': "❌ Dhiisittii Deebi'i",
                'en': '❌ Decline & Return'
            }
            btn_decline = (
                    get_text(lang, "BTN_DECLINE_RETURN")
                    or get_text(lang, "BTN_SKIP")
                    or btn_decline_defaults.get(norm_lang, btn_decline_defaults['en'])
            )
            keyboard.append([InlineKeyboardButton(btn_decline, callback_data="cancel_match")])

            # Send response using robust safe_send_message
            await self.safe_send_message(
                context=context,
                chat_id=chat_id,
                text=msg,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
            )
            return getattr(self, "SELECTING_MATCH", 99)

        except Exception as e:
            logger.exception(f"Critical operational reverse matching pipeline fault for user {user_id}: {e}")
            system_err_defaults = {
                'am': '⚠️ የሲስተም ስህተት አጋጥሟል። እባክዎ እንደገና ይሞክሩ።',
                'om': '⚠️ Dogoggorri sirnaa mudateera. Maaloo irra deebii yaali.',
                'en': '⚠️ An internal pipeline anomaly occurred. Please restart the process.'
            }
            system_err_text = get_text(lang, "ERR_SYS_PIPELINE_FAILED") or system_err_defaults.get(norm_lang,
                                                                                                   system_err_defaults[
                                                                                                       'en'])

            if processing_msg:
                try:
                    await processing_msg.delete()
                except Exception:
                    pass
            return await self.route_to_main_menu(update, context, custom_msg=system_err_text)

    # ==========================================
    # BIDIRECTIONAL MATCH SELECTION & DEAL LOCKING
    # ==========================================

    async def handle_match_selection(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        query = update.callback_query
        if query:
            try:
                await query.answer()
            except Exception as e:
                logger.debug(f"Callback answer exception: {e}")

        lang = self._get_lang(context)
        domain = context.user_data.get('domain', 'freight').lower()
        cb_data = query.data if query else ""

        # ✅ Recognize both reverse match callback prefixes
        is_reverse = cb_data.startswith("select_seeker_match:") or cb_data.startswith(
            "fr_select_seeker_match:") or "seeker" in cb_data
        context.user_data['is_reverse_match'] = is_reverse

        try:
            match_id = cb_data.split(":")[1]
        except IndexError:
            logger.error(f"Malformed match selection payload: {cb_data}")
            return await self.route_to_main_menu(update, context, custom_msg="⚠️ Invalid match data payload.")

        # Normalize language preference safely
        norm_lang = str(lang).lower().strip().split('_')[0] if lang else 'en'
        if norm_lang not in ('en', 'am', 'om'):
            norm_lang = 'en'

        # Database client resolution
        db_client = getattr(self, 'db', None)
        supabase_client = getattr(db_client, 'supabase', None) if db_client else None

        # =====================================================================
        # STANDALONE INLINE-FIRST DOMAIN TRANSLATIONS (Prioritized)
        # =====================================================================
        DOMAIN_TRANSLATIONS = {
            'freight': {'am': 'የጭነት', 'om': "Fe'umsa", 'en': 'Freight'},
            'ride': {'am': 'የጉዞ', 'om': 'Imala', 'en': 'Ride'}
        }

        localized_domain = (
                DOMAIN_TRANSLATIONS.get(domain, {}).get(norm_lang)
                or DOMAIN_TRANSLATIONS.get(domain, {}).get('en')
                or get_text(lang, f"domain_{domain}")
                or domain.title()
        )
        if not localized_domain or str(localized_domain).startswith("domain_"):
            localized_domain = DOMAIN_TRANSLATIONS.get(domain, {}).get('en', domain.title())

        # =====================================================================
        # BULLETPROOF TEXT CLEANER & ACRONYM PRESERVATION HELPER
        # =====================================================================
        def sanitize_text(text: str) -> str:
            if not text:
                return ""

            cleaned = (
                str(text)
                .replace("[domain_freight]", localized_domain)
                .replace("[domain_ride]", localized_domain)
                .replace("{domain_freight}", localized_domain)
                .replace("{domain}", localized_domain)
            )

            cleaned = re.sub(r'\*(.*?)\*', r'<b>\1</b>', cleaned)
            cleaned = re.sub(r'_(.*?)_', r'<i>\1</i>', cleaned)
            cleaned = re.sub(r'`(.*?)`', r'<code>\1</code>', cleaned)

            # Permanently preserve vehicle acronyms & abbreviations
            acronym_replacements = {
                r'\bCr-v\b': 'CR-V',
                r'\bCr v\b': 'CR-V',
                r'\bSuv\b': 'SUV',
                r'\bEv\b': 'EV',
                r'\bVip\b': 'VIP',
                r'\bAbs\b': 'ABS',
                r'\bV6\b': 'V6',
                r'\bV8\b': 'V8',
            }
            for pattern, replacement in acronym_replacements.items():
                cleaned = re.sub(pattern, replacement, cleaned, flags=re.IGNORECASE)

            return cleaned

        def get_valid_val(sources: list, default: str = "") -> str:
            invalid_placeholders = {"", "N/A", "NOT_SPECIFIED", "NONE", "NULL", "UNSPECIFIED", "ANY", "NOEREQUEST",
                                    "NOE"}
            for val_val in sources:
                if val_val is not None:
                    cleaned = str(val_val).strip()
                    if cleaned and cleaned.upper() not in invalid_placeholders:
                        return cleaned
            return default

        async def dispatch_card_message(text_msg: str, markup: Optional[InlineKeyboardMarkup] = None):
            target_chat_id = update.effective_chat.id if update.effective_chat else None
            if not target_chat_id:
                return

            cleaned_text = sanitize_text(text_msg)

            if query and query.message:
                if getattr(query.message, 'photo', None):
                    try:
                        await query.edit_message_caption(caption=cleaned_text, reply_markup=markup, parse_mode="HTML")
                        return
                    except Exception:
                        pass

                    try:
                        await context.bot.delete_message(chat_id=target_chat_id, message_id=query.message.message_id)
                    except Exception:
                        pass

                    await context.bot.send_message(chat_id=target_chat_id, text=cleaned_text, reply_markup=markup,
                                                   parse_mode="HTML")
                    return

                try:
                    await query.edit_message_text(text=cleaned_text, reply_markup=markup, parse_mode="HTML")
                    return
                except Exception:
                    pass

            await context.bot.send_message(chat_id=target_chat_id, text=cleaned_text, reply_markup=markup,
                                           parse_mode="HTML")

        # =========================================================================
        # MULTILINGUAL ATTRIBUTE LABELS
        # =========================================================================
        ATTR_LABELS = {
            "en": {
                "cargo_weight": "Cargo Weight",
                "freight_capacity": "Freight Capacity",
                "party_size": "Number of Passengers (Party Size)",
                "available_seats": "Available Seats",
                "req_vehicle": "Required Vehicle",
                "supp_vehicle": "Supplied Vehicle"
            },
            "am": {
                "cargo_weight": "የጭነት ክብደት (Cargo Weight)",
                "freight_capacity": "የጭነት አቅም (Freight Capacity)",
                "party_size": "የተሳፋሪ ብዛት (Party Size)",
                "available_seats": "ያሉ መቀመጫዎች (Available Seats)",
                "req_vehicle": "የተጠየቀው ተሽከርካሪ",
                "supp_vehicle": "የቀረበው ተሽከርካሪ"
            },
            "om": {
                "cargo_weight": "Ulfaatina Fe'umsaa (Cargo Weight)",
                "freight_capacity": "Dandettii Fe'umsaa (Freight Capacity)",
                "party_size": "Baayyina Imaltootaa (Party Size)",
                "available_seats": "Bakkeewwan Taa'umsaa (Available Seats)",
                "req_vehicle": "Konkolaataa Barbaadame",
                "supp_vehicle": "Konkolaataa Dhihaate"
            }
        }
        lbls = ATTR_LABELS.get(norm_lang, ATTR_LABELS["en"])

        if is_reverse:
            # =====================================================================
            # REVERSE MATCHING: Provider selecting Seeker Request
            # =====================================================================
            top_matches = context.user_data.get('seeker_top_matches', []) or context.user_data.get(
                'freight_top_matches', [])
            selected_request = next((m for m in top_matches if str(m.get('id')) == match_id), None)

            if not selected_request:
                err_msg = get_text(lang, "ERR_REQUEST_NOT_FOUND") or "⚠️ Selected request is deprecated or resolved."
                await dispatch_card_message(err_msg, None)
                return await self.route_to_main_menu(update, context)

            db_req_data = {}
            db_listing_data = {}
            if supabase_client:
                try:
                    req_res = await supabase_client.table("seeker_requests").select("*").eq("id",
                                                                                            match_id).maybe_single().execute()
                    if req_res and req_res.data:
                        db_req_data = req_res.data

                    p_id = update.effective_user.id
                    list_res = await supabase_client.table("provider_listings").select("*").eq("provider_id",
                                                                                               p_id).order("created_at",
                                                                                                           desc=True).limit(
                        1).execute()
                    if list_res and list_res.data:
                        db_listing_data = list_res.data[0]
                except Exception as db_err:
                    logger.warning(f"Failed fetching explicit attributes for reverse match: {db_err}")

            vertical_type = get_valid_val([
                db_req_data.get('vertical_type'), db_req_data.get('domain'), selected_request.get('vertical_type'),
                domain
            ], 'freight').lower()

            req_v_type = sanitize_text(
                str(db_req_data.get('vehicle_type') or db_req_data.get('property_use') or selected_request.get(
                    'vehicle_type') or 'Standard'))
            supp_v_type = sanitize_text(
                str(db_listing_data.get('vehicle_type') or db_listing_data.get('property_use') or context.user_data.get(
                    'vehicle_type') or context.user_data.get('property_use') or 'Standard Transport Asset'))

            budget = safe_float(selected_request.get('max_budget', 0.0))
            currency = get_valid_val([selected_request.get('currency'), context.user_data.get('currency')],
                                     'ETB').upper()
            target_seeker_id = selected_request.get('seeker_id')
            provider_asking_price = safe_float(context.user_data.get('price') or budget)

            req_origin = get_valid_val([
                selected_request.get('location_text'), selected_request.get('origin'), context.user_data.get('origin')
            ], 'Origin Point')

            req_dest = get_valid_val([
                selected_request.get('destination_text'), selected_request.get('destination')
            ], 'Destination Point')

            details_line = ""
            if vertical_type in ['ride', 'rides', 'vehicle', 'vehicles']:
                p_size = get_valid_val([db_req_data.get('party_size'), selected_request.get('party_size')])
                if p_size:
                    details_line += f"👥 <b>{lbls['party_size']}:</b> <code>{int(safe_float(p_size))}</code>\n"
                av_seats = get_valid_val([db_listing_data.get('available_seats')])
                if av_seats:
                    details_line += f"💺 <b>{lbls['available_seats']}:</b> <code>{int(safe_float(av_seats))}</code>\n"
            else:
                c_weight = get_valid_val(
                    [db_req_data.get('cargo_weight_kgs'), selected_request.get('cargo_weight_kgs')])
                if c_weight:
                    details_line += f"⚖️ <b>{lbls['cargo_weight']}:</b> <code>{safe_float(c_weight):,.0f} kg</code>\n"
                max_w = get_valid_val([db_listing_data.get('max_weight_kgs'), db_listing_data.get('cargo_weight_kgs')])
                if max_w:
                    details_line += f"⚖️ <b>{lbls['freight_capacity']}:</b> <code>{safe_float(max_w):,.0f} kg</code>\n"

            details_line += f"🚛 <b>{lbls['req_vehicle']}:</b> <code>{req_v_type}</code>\n"
            details_line += f"🚛 <b>{lbls['supp_vehicle']}:</b> <code>{supp_v_type}</code>\n"

            title_template = get_text(lang,
                                      "TITLE_TRANSPORT_ROUTE_FREIGHT") or "Transport Route: {origin} ➡️ {destination}"
            try:
                asset_title = title_template.format(origin=req_origin, destination=req_dest)
            except Exception:
                asset_title = f"Transport Route: {req_origin} ➡️ {req_dest}"

            context.user_data['target_seeker_id'] = target_seeker_id
            context.user_data['active_freight_deal_id'] = match_id
            context.user_data['freight_fare'] = provider_asking_price
            context.user_data['asset_title'] = asset_title

            card_msg_template = get_text(lang, "MSG_CARD_SEEKER_SELECTED_FREIGHT") or (
                "👤 <b>Seeker Request Selected:</b> {title}\n"
                "📍 <b>Route:</b> <code>{origin} ➡️ {destination}</code>\n"
                "{details}"
                "💰 <b>Max Budget:</b> <code>{budget} {currency}</code>\n\n"
                "<i>Select an option below to submit your offer to this seeker:</i>"
            )

            try:
                card_msg = card_msg_template.format(
                    title=html.escape(asset_title),
                    origin=html.escape(req_origin),
                    destination=html.escape(req_dest),
                    details=details_line,
                    budget=f"{budget:,.2f}" if budget > 0 else (
                        "ተደራዳሪ" if norm_lang == "am" else "Marii'atamaa" if norm_lang == "om" else "Negotiable"),
                    currency=currency
                )
            except Exception:
                card_msg = (
                    f"👤 <b>Seeker Request Selected:</b> {html.escape(asset_title)}\n"
                    f"📍 <b>Route:</b> <code>{html.escape(req_origin)} ➡️ {html.escape(req_dest)}</code>\n"
                    f"{details_line}"
                    f"💰 <b>Max Budget:</b> <code>{budget:,.2f} {currency}</code>\n\n"
                    f"<i>Select an option below to submit your offer to this seeker:</i>"
                )

            btn_custom = get_text(lang, "BTN_SEND_OFFER") or (
                "🤝 የራስዎን ዋጋ ያስገቡ" if norm_lang == "am" else "🤝 Gatii Keessan Galchaa" if norm_lang == "om" else "🤝 Enter Custom Offer")
            btn_asking_template = get_text(lang, "BTN_SEND_ASKING_RATE") or (
                "✅ የሚጠየቀውን ዋጋ ይላኩ ({price} {currency})" if norm_lang == "am" else "✅ Gatii Gaafatame Ergaa ({price} {currency})" if norm_lang == "om" else "✅ Send Asking Rate ({price} {currency})")

            formatted_price = f"{provider_asking_price:,.0f}" if provider_asking_price > 0 else "0"
            try:
                btn_asking = btn_asking_template.format(price=formatted_price, currency=currency)
            except Exception:
                btn_asking = f"✅ Send Asking Rate ({formatted_price} {currency})"

            btn_decline_defaults = {'am': '❌ አልፈልግም (ተመለስ)', 'om': "❌ Dhiisittii Deebi'i", 'en': '❌ Decline & Return'}
            btn_decline = get_text(lang, "BTN_BACK_TOP_MATCHES") or get_text(lang,
                                                                             "BTN_DECLINE_RETURN") or btn_decline_defaults.get(
                norm_lang, btn_decline_defaults['en'])

            keyboard = [
                [InlineKeyboardButton(btn_custom, callback_data=f"fr_prompt_offer:{match_id}")],
                [InlineKeyboardButton(btn_asking, callback_data=f"lock_freight_deal:{match_id}")],
                [InlineKeyboardButton(btn_decline, callback_data="fr_back_to_top_matches")]
            ]

            await dispatch_card_message(card_msg, InlineKeyboardMarkup(keyboard))
            return getattr(self, "SELECTING_MATCH", 99)

        else:
            # =====================================================================
            # FORWARD MATCHING: Seeker selecting Provider Listing
            # =====================================================================
            top_matches = context.user_data.get('freight_top_matches', []) or context.user_data.get(
                'seeker_top_matches', [])
            selected_vehicle = next((m for m in top_matches if str(m.get('id')) == match_id), None)

            if not selected_vehicle:
                err_msg = get_text(lang,
                                   "ERR_VEHICLE_NOT_FOUND") or "⚠️ Selected vehicle match is no longer available in the registry."
                await dispatch_card_message(err_msg, None)
                return await self.route_to_main_menu(update, context)

            db_listing_data = {}
            db_req_data = {}
            if supabase_client:
                try:
                    list_res = await supabase_client.table("provider_listings").select("*").eq("id",
                                                                                               match_id).maybe_single().execute()
                    if list_res and list_res.data:
                        db_listing_data = list_res.data

                    s_id = update.effective_user.id
                    req_res = await supabase_client.table("seeker_requests").select("*").eq("seeker_id", s_id).order(
                        "created_at", desc=True).limit(1).execute()
                    if req_res and req_res.data:
                        db_req_data = req_res.data[0]
                except Exception as db_err:
                    logger.warning(f"Failed fetching explicit attributes for forward match: {db_err}")

            fare = safe_float(selected_vehicle.get('price', 0.0))
            provider_id = selected_vehicle.get('provider_id')

            raw_plate = get_valid_val([selected_vehicle.get('vehicle_plate'), selected_vehicle.get('plate'),
                                       selected_vehicle.get('license_plate')])
            plate = html.escape(raw_plate) if raw_plate else ""

            supp_v_type = sanitize_text(
                str(db_listing_data.get('vehicle_type') or db_listing_data.get('property_use') or selected_vehicle.get(
                    'property_use') or selected_vehicle.get('vehicle_type') or 'Standard Carrier'))
            req_v_type = sanitize_text(
                str(db_req_data.get('vehicle_type') or db_req_data.get('property_use') or context.user_data.get(
                    'vehicle_type') or 'Standard'))

            # ✅ FIXED: Avoided .title() to prevent casing destruction on acronyms like CR-V
            property_use = html.escape(supp_v_type)

            listing_details_line = ""
            if domain in ['ride', 'rides', 'vehicle', 'vehicles']:
                p_size = get_valid_val([db_req_data.get('party_size')])
                if p_size:
                    listing_details_line += f"👥 <b>{lbls['party_size']}:</b> <code>{int(safe_float(p_size))}</code>\n"
                av_seats = get_valid_val(
                    [db_listing_data.get('available_seats'), selected_vehicle.get('available_seats')])
                if av_seats:
                    listing_details_line += f"💺 <b>{lbls['available_seats']}:</b> <code>{int(safe_float(av_seats))}</code>\n"
            else:
                c_weight = get_valid_val([db_req_data.get('cargo_weight_kgs')])
                if c_weight:
                    listing_details_line += f"⚖️ <b>{lbls['cargo_weight']}:</b> <code>{safe_float(c_weight):,.0f} kg</code>\n"
                max_w = get_valid_val([db_listing_data.get('max_weight_kgs'), db_listing_data.get('cargo_weight_kgs'),
                                       selected_vehicle.get('max_weight_kgs')])
                if max_w:
                    listing_details_line += f"⚖️ <b>{lbls['freight_capacity']}:</b> <code>{safe_float(max_w):,.0f} kg</code>\n"

            listing_details_line += f"🚛 <b>{lbls['req_vehicle']}:</b> <code>{req_v_type}</code>\n"
            listing_details_line += f"🚛 <b>{lbls['supp_vehicle']}:</b> <code>{supp_v_type}</code>\n"

            photo_ref = None

            def _extract_url_or_id(item):
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

            for key in ['photo_url', 'image_url', 'photo_file_id', 'images', 'photos', 'vehicle_photo', 'media_url']:
                val = db_listing_data.get(key) or selected_vehicle.get(key)
                if val:
                    photo_ref = _extract_url_or_id(val[0]) if isinstance(val, list) and len(
                        val) > 0 else _extract_url_or_id(val)
                    if photo_ref:
                        break

            seeker_origin = html.escape(
                get_valid_val([
                    context.user_data.get('location_text'), context.user_data.get('origin'),
                    selected_vehicle.get('origin_text')
                ], 'Origin Point')
            )

            seeker_dest = html.escape(
                get_valid_val([
                    context.user_data.get('destination_text'), context.user_data.get('destination'),
                    selected_vehicle.get('destination')
                ], 'Destination Point')
            )

            if plate:
                asset_title = f"{localized_domain}\n{property_use} (Plate: {plate})"
            else:
                asset_title = f"{localized_domain}\n{property_use}"

            context.user_data['active_freight_deal_id'] = match_id
            context.user_data['freight_fare'] = fare
            context.user_data['provider_id'] = provider_id
            context.user_data['asset_title'] = asset_title

            currency = get_valid_val([selected_vehicle.get('currency'), context.user_data.get('currency')],
                                     'ETB').upper()

            card_msg_template = get_text(lang, "MSG_CARD_MATCH_SUMMARY_FREIGHT") or (
                "🚚 <b>Selected Match Summary:</b>\n\n"
                "{title}\n"
                "📍 <b>Route:</b> <code>{origin} ➡️ {destination}</code>\n"
                "{details}"
                "💰 <b>Estimated Fare:</b> <b>{fare} {currency}</b>\n\n"
                "<i>Select an option below to submit your transport bid:</i>"
            )

            try:
                card_msg = card_msg_template.format(
                    title=html.escape(asset_title),
                    origin=seeker_origin,
                    destination=seeker_dest,
                    details=listing_details_line,
                    fare=f"{fare:,.2f}" if fare > 0 else (
                        "ተደራዳሪ" if norm_lang == "am" else "Marii'atamaa" if norm_lang == "om" else "Negotiable"),
                    currency=currency
                )
            except Exception:
                card_msg = (
                    f"🚚 <b>Selected Match Summary:</b>\n\n"
                    f"{html.escape(asset_title)}\n"
                    f"📍 <b>Route:</b> <code>{seeker_origin} ➡️ {seeker_dest}</code>\n"
                    f"{listing_details_line}"
                    f"💰 <b>Estimated Fare:</b> <b>{fare:,.2f} {currency}</b>\n\n"
                    f"<i>Select an option below to submit your transport bid:</i>"
                )

            btn_custom = get_text(lang, "BTN_SEND_OFFER") or (
                "🤝 የራስዎን ዋጋ ያስገቡ" if norm_lang == "am" else "🤝 Gatii Keessan Galchaa" if norm_lang == "om" else "🤝 Enter Custom Offer")
            btn_asking_template = get_text(lang, "BTN_ACCEPT_FARE") or (
                "✅ ሂሳቡን ይቀበሉ ({fare} {currency})" if norm_lang == "am" else "✅ Kaffaltii Fadhadhaa ({fare} {currency})" if norm_lang == "om" else "✅ Accept Fare ({fare} {currency})")

            formatted_fare = f"{fare:,.0f}" if fare > 0 else "0"
            try:
                btn_asking = btn_asking_template.format(fare=formatted_fare, currency=currency)
            except Exception:
                btn_asking = f"✅ Accept Fare ({formatted_fare} {currency})"

            btn_decline_defaults = {
                'am': '❌ ተመለስ (ወደ ዋና ዝርዝር)',
                'om': "❌ Gara Tarree Guddaatti Deebi'i",
                'en': '❌ Back to Top Matches'
            }
            btn_decline = get_text(lang, "BTN_BACK_TOP_MATCHES") or btn_decline_defaults.get(norm_lang,
                                                                                             btn_decline_defaults['en'])

            keyboard = [
                [InlineKeyboardButton(btn_custom, callback_data=f"fr_prompt_offer:{match_id}")],
                [InlineKeyboardButton(btn_asking, callback_data=f"lock_freight_deal:{match_id}")],
                [InlineKeyboardButton(btn_decline, callback_data="fr_back_to_top_matches")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            cleaned_card_msg = sanitize_text(card_msg)

            presigned_url = None
            if photo_ref:
                img_service = getattr(self, "image_service", getattr(self, "storage", None))
                if img_service and hasattr(img_service, "get_presigned_url_async"):
                    try:
                        presigned_url = await img_service.get_presigned_url_async(photo_ref)
                    except Exception as sign_err:
                        logger.warning(f"Failed to generate pre-signed URL for '{photo_ref}': {sign_err}")

            photo_dispatched = False
            chat_id = update.effective_chat.id if update.effective_chat else None

            if presigned_url and chat_id:
                try:
                    if query and query.message:
                        try:
                            await context.bot.delete_message(chat_id=chat_id, message_id=query.message.message_id)
                        except Exception:
                            pass

                    await context.bot.send_photo(
                        chat_id=chat_id,
                        photo=presigned_url,
                        caption=cleaned_card_msg,
                        reply_markup=reply_markup,
                        parse_mode="HTML"
                    )
                    photo_dispatched = True
                except Exception as photo_err:
                    logger.warning(f"Failed to dispatch photo card: {photo_err}")

            if not photo_dispatched:
                await dispatch_card_message(cleaned_card_msg, reply_markup)

            return getattr(self, "SELECTING_MATCH", 99)

    async def handle_back_to_top_matches(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        query = update.callback_query
        if query:
            try:
                await query.answer()
            except Exception as e:
                logger.debug(f"Callback answer exception: {e}")

        lang = self._get_lang(context)
        norm_lang = str(lang).lower().strip().split('_')[0] if lang else 'en'
        if norm_lang not in ('en', 'am', 'om'):
            norm_lang = 'en'

        is_reverse = context.user_data.get('is_reverse_match', False)
        top_matches = context.user_data.get('seeker_top_matches', []) if is_reverse else context.user_data.get(
            'freight_top_matches', [])

        if not top_matches:
            return await self.route_to_main_menu(update, context)

        keyboard = []
        if is_reverse:
            context.user_data.pop('active_seeker_deal_id', None)
            vertical = context.user_data.get('current_vertical', context.user_data.get('domain', 'transport'))
            localized_vertical = get_text(lang, f"domain_{vertical.lower()}") or vertical.title()
            header_text = get_text(
                lang, "MSG_TOP_SEEKER_MATCHES",
                count=len(top_matches), vertical=localized_vertical
            ) or f"🎯 <b>Top {len(top_matches)} {localized_vertical} Seeker Matches Found!</b>\n\nThese users are actively requesting transport or service logic matching your asset. Select a Seeker below to send your offer:"

            for i, match in enumerate(top_matches, start=1):
                match_id = str(match.get('id', ''))
                budget = safe_float(match.get('max_budget', 0.0))
                currency = str(match.get('currency') or 'ETB').upper()
                score = int(match.get('_match_score', 95))

                button_label = get_text(
                    lang, "BTN_SEEKER_MATCH_ITEM",
                    index=i, budget=f"{budget:,.2f}", currency=currency, score=score
                ) or f"👤 #{i} Seeker (Budget: {budget:,.2f} {currency} - {score}%)"

                keyboard.append(
                    [InlineKeyboardButton(button_label, callback_data=f"fr_select_seeker_match:{match_id}")])

            btn_decline_defaults = {
                'am': '❌ አልፈልግም (ተመለስ)',
                'om': "❌ Dhiisittii Deebi'i",
                'en': '❌ Decline & Return'
            }
            btn_decline = get_text(lang, "BTN_DECLINE_RETURN") or get_text(lang,
                                                                           "BTN_SKIP") or btn_decline_defaults.get(
                norm_lang, btn_decline_defaults['en'])
            keyboard.append([InlineKeyboardButton(btn_decline, callback_data="cancel_match")])
        else:
            context.user_data.pop('active_freight_deal_id', None)
            domain = context.user_data.get('domain', 'freight')
            localized_domain = get_text(lang, f"domain_{domain.lower()}") or domain.title()
            header_text = get_text(
                lang, "msg_select_transport_match"
            ) or f"🚚 <b>Top Matching {localized_domain} Options:</b>\n\nPlease select one of the options below:"

            for idx, match in enumerate(top_matches, start=1):
                plate = match.get('vehicle_plate', match.get('plate', 'N/A'))
                price = safe_float(match.get('price', 0.0))
                currency = str(match.get('currency', 'ETB')).upper()
                match_id = match.get('id')

                plate_labels = {
                    "am": f"#{idx}. ሰሌዳ: {plate} ({price:,.0f} {currency})",
                    "om": f"#{idx}. Gabatee: {plate} ({price:,.0f} {currency})",
                    "en": f"#{idx}. Plate: {plate} ({price:,.0f} {currency})"
                }
                btn_text = plate_labels.get(norm_lang, plate_labels["en"])
                keyboard.append(
                    [InlineKeyboardButton(btn_text, callback_data=f"select_freight_match:{match_id}")])

            cancel_labels = {
                "am": "❌ ፍለጋውን ሰርዝ",
                "om": "❌ Barbaacha Haquu",
                "en": "❌ Cancel Search"
            }
            btn_cancel = get_text(lang, "BTN_CANCEL") or cancel_labels.get(norm_lang, cancel_labels["en"])
            keyboard.append([InlineKeyboardButton(btn_cancel, callback_data="cancel_match")])

        reply_markup = InlineKeyboardMarkup(keyboard)
        chat_id = update.effective_chat.id if update.effective_chat else None

        try:
            if query and query.message:
                if getattr(query.message, 'photo', None):
                    try:
                        await query.edit_message_caption(caption=header_text, reply_markup=reply_markup,
                                                         parse_mode="HTML")
                        return getattr(self, "SELECTING_MATCH", 99)
                    except Exception:
                        pass
                    if chat_id:
                        try:
                            await context.bot.delete_message(chat_id=chat_id,
                                                             message_id=query.message.message_id)
                        except Exception:
                            pass
                        await context.bot.send_message(chat_id=chat_id, text=header_text,
                                                       reply_markup=reply_markup,
                                                       parse_mode="HTML")
                        return getattr(self, "SELECTING_MATCH", 99)

                await query.edit_message_text(text=header_text, reply_markup=reply_markup, parse_mode="HTML")
            elif chat_id:
                await context.bot.send_message(chat_id=chat_id, text=header_text, reply_markup=reply_markup,
                                               parse_mode="HTML")
        except Exception as e:
            logger.error(f"Failed to refresh top matches UI block: {e}")
            if chat_id:
                try:
                    await context.bot.send_message(chat_id=chat_id, text=header_text, reply_markup=reply_markup,
                                                   parse_mode="HTML")
                except Exception as send_err:
                    logger.error(f"Failed to send fallback top matches message: {send_err}")

        return getattr(self, "SELECTING_MATCH", 99)

    # ==========================================
    # CUSTOM OFFER & DEAL LOCKING PIPELINE
    # ==========================================

    async def prompt_custom_offer(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """
        Prompts the user to enter a custom monetary offer.
        Supports dynamic DB syncing, reverse matching, cargo weight (freight), and party size (ride).
        """
        query = update.callback_query
        if query:
            try:
                await query.answer()
            except Exception as e:
                logger.debug(f"Callback answer exception: {e}")

            if query.data and ":" in query.data:
                _, _, extracted_id = query.data.partition(":")
                if extracted_id:
                    context.user_data["active_freight_deal_id"] = extracted_id

        user_lang = self._get_lang(context)
        norm_lang = str(user_lang).lower().strip().split('_')[0] if user_lang else 'en'
        if norm_lang not in ('en', 'am', 'om'):
            norm_lang = 'en'

        is_reverse = context.user_data.get('is_reverse_match', False)
        vertical_type = str(
            context.user_data.get('vertical_type') or
            context.user_data.get('service_type') or
            'freight'
        ).lower().strip()
        asset_title = context.user_data.get('asset_title', 'Selected Transport Service')
        active_deal_id = str(context.user_data.get("active_freight_deal_id", "pending"))

        currency = str(
            context.user_data.get('currency')
            or context.user_data.get('freight_currency')
            or 'ETB'
        ).upper()

        if is_reverse:
            raw_price = (
                    context.user_data.get('max_budget')
                    or context.user_data.get('seeker_budget')
                    or context.user_data.get('budget')
                    or context.user_data.get('freight_fare', 0.0)
            )
        else:
            raw_price = context.user_data.get('freight_fare') or context.user_data.get('budget', 0.0)

        asking_price = safe_float(raw_price)

        origin = context.user_data.get('origin_text') or context.user_data.get('origin') or ''
        destination = context.user_data.get('destination_text') or context.user_data.get('destination') or ''
        cargo_weight_kgs = context.user_data.get('cargo_weight_kgs') or context.user_data.get('weight_kg')
        party_size = context.user_data.get('party_size') or context.user_data.get('passenger_count')

        # Mandate correct table querying based on whether it is a reverse match or listing match
        if active_deal_id != "pending" and hasattr(self, 'db') and hasattr(self.db, 'table'):
            try:
                target_table = 'seeker_requests' if is_reverse else 'provider_listings'
                record_res = await self.db.table(target_table).select('*').eq(
                    'id', active_deal_id
                ).maybe_single().execute()

                if record_res and hasattr(record_res, 'data') and record_res.data:
                    rdata = record_res.data
                    if is_reverse:
                        asking_price = safe_float(rdata.get('max_budget') or rdata.get('price') or asking_price)
                    else:
                        asking_price = safe_float(rdata.get('price') or asking_price)

                    context.user_data['freight_fare'] = asking_price

                    if rdata.get('currency'):
                        currency = str(rdata['currency']).upper()
                        context.user_data['currency'] = currency

                    origin = rdata.get('origin_text') or rdata.get('location_text') or origin
                    destination = rdata.get('destination_text') or destination
                    cargo_weight_kgs = rdata.get('cargo_weight_kgs', cargo_weight_kgs)
                    party_size = rdata.get('party_size', party_size)
                    vertical_type = rdata.get('vertical_type', vertical_type)

                    context.user_data.update({
                        'origin_text': origin,
                        'destination_text': destination,
                        'cargo_weight_kgs': cargo_weight_kgs,
                        'party_size': party_size,
                        'vertical_type': vertical_type
                    })
            except Exception as e:
                logger.warning(f"Could not fetch latest metadata for custom offer prompt: {e}")

        formatted_price = f"{asking_price:,.2f}"
        escaped_title = html.escape(str(asset_title))
        escaped_origin = html.escape(str(origin)) if origin else "N/A"
        escaped_dest = html.escape(str(destination)) if destination else "N/A"

        spec_line_am = ""
        spec_line_om = ""
        spec_line_en = ""

        if origin and destination:
            spec_line_am += f"📍 <b>መስመር፡</b> {escaped_origin} ➔ {escaped_dest}\n"
            spec_line_om += f"📍 <b>Kallattii:</b> {escaped_origin} ➔ {escaped_dest}\n"
            spec_line_en += f"📍 <b>Route:</b> {escaped_origin} ➔ {escaped_dest}\n"

        if vertical_type == 'freight' and cargo_weight_kgs not in [None, "", "0", 0]:
            formatted_weight = f"{safe_float(cargo_weight_kgs):,.1f}".rstrip('0').rstrip('.')
            spec_line_am += f"⚖️ <b>የዕቃ ክብደት፡</b> {formatted_weight} ኪ.ግ\n"
            spec_line_om += f"⚖️ <b>Ulfaatina Fe’iichaa:</b> {formatted_weight} kg\n"
            spec_line_en += f"⚖️ <b>Cargo Weight:</b> {formatted_weight} kg\n"
        elif vertical_type == 'ride' and party_size not in [None, "", "0", 0]:
            spec_line_am += f"👥 <b>ተሳፋሪዎች (Party Size)፡</b> {party_size}\n"
            spec_line_om += f"👥 <b>Imaltoota (Party Size):</b> {party_size}\n"
            spec_line_en += f"👥 <b>Party Size:</b> {party_size}\n"

        sample_num = "50" if currency == "USD" else "5000"

        if is_reverse:
            prompts = {
                "am": (
                    f"💬 <b>ለዚህ ፈላጊ የመደራደሪያ ዋጋዎን ያስገቡ</b>\n\n"
                    f"🏠 <b>ጥያቄ፡</b> <i>{escaped_title}</i>\n"
                    f"{spec_line_am}"
                    f"💰 <b>የፈላጊው ባጀት፡</b> <code>{formatted_price} {currency}</code>\n\n"
                    f"እባክዎ የመወዳደሪያ ዋጋዎን በ {currency} ያስገቡ (ቁጥር ብቻ፣ ለምሳሌ <code>{sample_num}</code>)፦"
                ),
                "om": (
                    f"💬 <b>Gatii Dhiheessii Barbaadaaf Galchaa</b>\n\n"
                    f"🏠 <b>Gaaffii:</b> <i>{escaped_title}</i>\n"
                    f"{spec_line_om}"
                    f"💰 <b>Baajata Barbaadaa:</b> <code>{formatted_price} {currency}</code>\n\n"
                    f"Maaloo gatii dhiheessitan {currency} n galchaa (lakkoofsa qofa, fakkeenyaaf <code>{sample_num}</code>):"
                ),
                "en": (
                    f"💬 <b>Enter Your Proposed Offer for this Seeker</b>\n\n"
                    f"🏠 <b>Request:</b> <i>{escaped_title}</i>\n"
                    f"{spec_line_en}"
                    f"💰 <b>Seeker Budget:</b> <code>{formatted_price} {currency}</code>\n\n"
                    f"Please reply with your offer amount in {currency} (numbers only, e.g., <code>{sample_num}</code>):"
                )
            }
        else:
            prompts = {
                "am": (
                    f"💬 <b>የመወዳደሪያ ዋጋዎን ያስገቡ</b>\n\n"
                    f"🏠 <b>አገልግሎት፡</b> <i>{escaped_title}</i>\n"
                    f"{spec_line_am}"
                    f"💰 <b>የተጠየቀ ዋጋ፡</b> <code>{formatted_price} {currency}</code>\n\n"
                    f"እባክዎ የመወዳደሪያ ዋጋዎን በ {currency} ያስገቡ (ቁጥር ብቻ፣ ለምሳሌ <code>{sample_num}</code>)፦"
                ),
                "om": (
                    f"💬 <b>Gatii dhiheessitan galchaa</b>\n\n"
                    f"🏠 <b>Tajaajila:</b> <i>{escaped_title}</i>\n"
                    f"{spec_line_om}"
                    f"💰 <b>Gatii gaafatame:</b> <code>{formatted_price} {currency}</code>\n\n"
                    f"Maaloo gatii dhiheessitan {currency} n galchaa (lakkoofsa qofa, fakkeenyaaf <code>{sample_num}</code>):"
                ),
                "en": (
                    f"💬 <b>Enter Your Proposed Offer</b>\n\n"
                    f"🏠 <b>Service:</b> <i>{escaped_title}</i>\n"
                    f"{spec_line_en}"
                    f"💰 <b>Asking Rate:</b> <code>{formatted_price} {currency}</code>\n\n"
                    f"Please reply with your offer amount in {currency} (numbers only, e.g., <code>{sample_num}</code>):"
                )
            }

        prompt_msg = prompts.get(norm_lang, prompts["en"])
        chat_id = update.effective_chat.id if update.effective_chat else None

        try:
            if query and query.message and getattr(query.message, 'photo', None):
                try:
                    await query.edit_message_caption(caption=prompt_msg, parse_mode="HTML")
                except Exception:
                    if chat_id:
                        await context.bot.delete_message(chat_id=chat_id, message_id=query.message.message_id)
                        await context.bot.send_message(chat_id=chat_id, text=prompt_msg, parse_mode="HTML")
            elif query:
                await query.edit_message_text(prompt_msg, parse_mode="HTML")
            elif chat_id:
                await context.bot.send_message(chat_id=chat_id, text=prompt_msg, parse_mode="HTML")
        except Exception:
            if chat_id:
                await context.bot.send_message(chat_id=chat_id, text=prompt_msg, parse_mode="HTML")

        return getattr(self, "ENTERING_OFFER", 14)

    async def receive_custom_offer(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """
        Captures the custom text input, verifies numeric validity using strict parsing,
        and automatically delegates to deal execution.
        """
        lang = self._get_lang(context)
        norm_lang = str(lang).lower().strip().split('_')[0] if lang else 'en'
        if norm_lang not in ('en', 'am', 'om'):
            norm_lang = 'en'

        if not update.message or not update.message.text:
            return getattr(self, "ENTERING_OFFER", 14)

        raw_text = update.message.text

        try:
            clean_text = re.sub(r"[^\d.]", "", raw_text.replace(",", ""))
            custom_offer = float(clean_text)
            if custom_offer <= 0:
                raise ValueError("Price strictly requires positive numbers.")
        except (ValueError, TypeError):
            err_msg = {
                "am": "⚠️ <b>የተሳሳተ ዋጋ።</b>\nእባክዎ ትክክለኛ የቁጥር ዋጋ ብቻ ያስገቡ (ለምሳሌ: 5000)።",
                "om": "⚠️ <b>Dogoggora gatii.</b>\nMaaloo lakkoofsa sirrii qofa galchaa (Fkn: 5000).",
                "en": "⚠️ <b>Invalid input.</b>\nPlease enter valid numerical characters strictly without text prefixes.\n<i>(Example: 5000)</i>"
            }
            await update.message.reply_text(err_msg.get(norm_lang, err_msg["en"]), parse_mode="HTML")
            return getattr(self, "ENTERING_OFFER", 14)

        context.user_data['freight_fare'] = custom_offer
        active_deal_id = str(context.user_data.get("active_freight_deal_id", "pending"))
        context.user_data['active_freight_deal_id'] = active_deal_id

        if active_deal_id != "pending" and hasattr(self, 'db') and hasattr(self.db, 'table'):
            try:
                await self.db.table('deals').update({
                    'current_price': custom_offer,
                    'status': 'negotiating'
                }).eq('id', active_deal_id).execute()
            except Exception as e:
                logger.error(f"Error persisting counter-offer sync patch in DB: {e}")

        return await self._process_freight_ride_lock(update, context)

    async def execute_freight_ride_lock(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """
        Triggered when a user clicks 'Send Asking Rate'. Processes idempotency and safely proxies to execution.
        """
        query = update.callback_query

        if query:
            processed_callbacks = context.user_data.setdefault('_processed_callbacks', set())
            if query.id in processed_callbacks:
                logger.warning(f"Bypassing duplicate callback execution for Query ID: {query.id}")
                try:
                    await query.answer()
                except Exception:
                    pass
                return ConversationHandler.END

            processed_callbacks.add(query.id)
            if len(processed_callbacks) > 50:
                processed_callbacks.clear()

            try:
                await query.answer()
            except Exception as err:
                logger.debug(f"Callback query already answered or expired: {err}")

            parts = query.data.split(':')
            match_id = parts[1] if len(parts) >= 2 else context.user_data.get('active_freight_deal_id')
            extracted_provider_id = parts[2] if len(parts) >= 3 else None
        else:
            match_id = context.user_data.get('active_freight_deal_id')
            extracted_provider_id = None

        is_reverse = context.user_data.get('is_reverse_match', False)

        if is_reverse:
            provider_id = update.effective_user.id
        else:
            provider_id = extracted_provider_id or context.user_data.get('provider_id')
            if provider_id:
                context.user_data['provider_id'] = provider_id

        is_valid_provider = False
        if provider_id:
            db_client = getattr(self, "db", getattr(self, "db_manager", None))
            if db_client and hasattr(db_client, "verify_transport_provider"):
                try:
                    is_valid_provider = await db_client.verify_transport_provider(provider_id)
                except Exception as db_err:
                    logger.error(f"Error executing transport provider verification: {db_err}")
                    is_valid_provider = False
            else:
                is_valid_provider = True

        lang = self._get_lang(context)
        norm_lang = str(lang).lower().strip().split('_')[0] if lang else 'en'
        if norm_lang not in ('en', 'am', 'om'):
            norm_lang = 'en'

        if not provider_id or not is_valid_provider:
            err_msg = {
                "am": "⚠️ የሲስተም ስህተት፡ የትራንስፖርት አቅራቢውን ማረጋገጥ አልተቻለም።",
                "om": "⚠️ Dogoggorri Sirnaaa: Dhiheessaa geejjibaa mirkaneessuu hin danda'amne.",
                "en": "⚠️ System Integrity Fault: Could not firmly verify transport provider."
            }
            out_msg = err_msg.get(norm_lang, err_msg["en"])

            if query:
                try:
                    await query.edit_message_text(text=out_msg, parse_mode="HTML")
                except Exception:
                    await context.bot.send_message(chat_id=update.effective_chat.id, text=out_msg,
                                                   parse_mode="HTML")
            else:
                await context.bot.send_message(chat_id=update.effective_chat.id, text=out_msg,
                                               parse_mode="HTML")

            return await self.route_to_main_menu(update, context)

        try:
            agreed_price = safe_float(context.user_data.get('freight_fare', 0.0))
        except (ValueError, TypeError):
            agreed_price = 0.0

        context.user_data['active_freight_deal_id'] = str(match_id)
        context.user_data['freight_fare'] = agreed_price

        return await self._process_freight_ride_lock(update, context)

    async def _process_freight_ride_lock(
            self,
            update: Update,
            context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        """
        Final execution gate with foolproof Deal ID / Seeker Request ID vs Listing ID safety guard and session interceptor.
        Dispatches structured UI alerts securely and automatically routes back to the main menu.
        """
        # =========================================================================
        # 🛡️ PERMANENT GUARD: Prevent Deal IDs or Seeker Request IDs from being treated as Listing IDs
        # =========================================================================
        callback_query = update.callback_query
        cb_data = callback_query.data if callback_query and callback_query.data else ""
        target_id = None
        if ":" in cb_data:
            try:
                target_id = cb_data.split(":", 1)[1]
            except IndexError:
                pass

        # Check if target_id is actually an existing Deal ID rather than a Listing ID
        db_client = getattr(self, 'db', None)
        supabase_client = getattr(db_client, 'supabase', None) if db_client else None

        if target_id and supabase_client:
            try:
                # 1. Check if target_id exists in the 'deals' table
                deal_check = await supabase_client.table("deals").select("id, listing_id").eq("id",
                                                                                              target_id).maybe_single().execute()
                if deal_check and deal_check.data:
                    logger.info(
                        f"🛡️ Guard caught Deal ID {target_id} in lock handler. Redirecting to counter-offer processor.")
                    context.user_data['active_negotiation_id'] = target_id
                    context.user_data['awaiting_counter_input'] = True
                    context.user_data['intent_to_counter'] = True
                    deals_handler_ref = getattr(self, 'deals_handler', self)
                    if update.message:
                        return await deals_handler_ref.process_counter_message(update, context)
            except Exception as guard_err:
                logger.debug(f"Deal ID guard check note: {guard_err}")

        # =========================================================================
        # 🛡️ INTERCEPTOR GUARD: Prevent counter-offer inputs from triggering deal creation
        # =========================================================================
        if context.user_data.get('awaiting_counter_input') or context.user_data.get('active_negotiation_id'):
            logger.info(
                "Interceptor caught active counter-offer session inside freight/ride lock handler. Routing to process_counter_message.")
            deals_handler_ref = getattr(self, 'deals_handler', self)
            return await deals_handler_ref.process_counter_message(update, context)

        if callback_query:
            try:
                await callback_query.answer()
            except Exception:
                pass

        is_reverse = context.user_data.get('is_reverse_match', False)
        lang = context.user_data.get('lang', 'en')
        norm_lang = str(lang).lower().strip().split('_')[0] if lang else 'en'
        if norm_lang not in ('en', 'am', 'om'):
            norm_lang = 'en'

        if not target_id:
            target_id = context.user_data.get('active_freight_deal_id')

        # 🛡️ FORWARD MATCH ID CORRECTION: Ensure target_id points to provider_listings, not seeker_request
        if not is_reverse and target_id and supabase_client:
            try:
                listing_verify = await supabase_client.table("provider_listings").select("id").eq("id",
                                                                                                  target_id).maybe_single().execute()
                if not listing_verify or not listing_verify.data:
                    # It's not in provider_listings. Let's check if it's a seeker request ID and resolve the associated listing ID
                    req_verify = await supabase_client.table("seeker_requests").select(
                        "vertical_type, destination_text, location_text").eq("id", target_id).maybe_single().execute()
                    if req_verify and req_verify.data:
                        logger.warning(
                            f"⚠️ Forward Match received Seeker Request ID {target_id} instead of Listing ID. Resolving matching provider listing...")
                        # Find a matching active provider listing
                        match_res = await supabase_client.table("provider_listings").select("id").eq("status",
                                                                                                     "active").limit(
                            1).execute()
                        if match_res and match_res.data:
                            target_id = match_res.data[0].get("id")
                            logger.info(f"💡 Successfully resolved corrected listing_id: {target_id}")
            except Exception as id_fix_err:
                logger.debug(f"Forward match ID correction check note: {id_fix_err}")

        agreed_price = safe_float(
            context.user_data.get('freight_fare') or
            context.user_data.get('price') or
            0.0
        )

        context.user_data["_suppress_invalid_prompt"] = True

        raw_currency = (
                context.user_data.get('selected_match_currency') or
                context.user_data.get('fare_currency') or
                context.user_data.get('price_currency') or
                context.user_data.get('currency') or
                'ETB'
        )
        currency = str(raw_currency).strip().upper()

        if currency == "ETB" and (
                str(context.user_data.get('freight_fare', '')).endswith("USD") or
                str(context.user_data.get('price', '')).endswith("USD") or
                "USD" in str(context.user_data.get('asset_title', ''))
        ):
            currency = "USD"

        async def send_response(text: str, reply_markup=None):
            if callback_query:
                try:
                    await callback_query.edit_message_text(text=text, parse_mode="HTML",
                                                           reply_markup=reply_markup)
                    return
                except Exception as edit_err:
                    logger.debug(f"Could not edit callback message, falling back to new message: {edit_err}")

            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=text,
                parse_mode="HTML",
                reply_markup=reply_markup
            )

        loading_messages = {
            "am": "⏳ <b>ጥያቄዎ በመከናወን ላይ...</b>",
            "om": "⏳ <b>Gaaffiin keessan hojjetamaa jira...</b>",
            "en": "⏳ <b>Processing request...</b>"
        }

        await send_response(loading_messages.get(norm_lang, loading_messages["en"]))

        # =========================================================================
        # 🌐 CLEAN ASSET TITLE SANITIZATION & ACRONYM CASE PRESERVATION
        # =========================================================================
        def sanitize_asset_title(title_text: str, _target_lang_code: str) -> str:
            if not title_text:
                return ""

            t_str = str(title_text).strip()
            all_prefixes = [
                "Transport Route", "Kallattii Imalaa", "የመጓጓዣ መንገድ",
                "Freight", "Fe'umsa", "የጭነት",
                "Ride", "Imala", "የጉዞ"
            ]

            stripped_title = t_str
            for prefix in all_prefixes:
                if stripped_title.startswith(prefix):
                    stripped_title = stripped_title[len(prefix):].strip()
                    stripped_title = stripped_title.lstrip(": -–—").strip()
                    break

            if not stripped_title:
                stripped_title = t_str

            acronym_replacements = {
                r'\bCr-v\b': 'CR-V',
                r'\bCr v\b': 'CR-V',
                r'\bSuv\b': 'SUV',
                r'\bEv\b': 'EV',
                r'\bVip\b': 'VIP',
                r'\bAbs\b': 'ABS',
                r'\bV6\b': 'V6',
                r'\bV8\b': 'V8',
            }
            for pattern, replacement in acronym_replacements.items():
                stripped_title = re.sub(pattern, replacement, stripped_title, flags=re.IGNORECASE)

            return stripped_title

        # =========================================================================
        # 📸 PHOTO REFERENCE EXTRACTION
        # =========================================================================
        photo_ref = None

        def _extract_url_or_id(item):
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

        for p_key in ['asset_photo_url', 'photo_url', 'image_url', 'photo_file_id', 'vehicle_photo']:
            val = context.user_data.get(p_key)
            if val:
                photo_ref = _extract_url_or_id(val[0]) if isinstance(val, list) and len(
                    val) > 0 else _extract_url_or_id(val)
                if photo_ref:
                    break

        try:
            if is_reverse:
                provider_id = update.effective_user.id
                top_seeker_matches = context.user_data.get('seeker_top_matches', [])
                selected_req = next((m for m in top_seeker_matches if str(m.get('id')) == str(target_id)), {})

                seeker_id = selected_req.get('seeker_id') or context.user_data.get('target_seeker_id')
                listing_id = context.user_data.get('last_created_listing_id') or context.user_data.get(
                    'active_freight_deal_id')

                if not seeker_id or not listing_id:
                    err_msg = {
                        "am": "⚡ <b>ስህተት:</b> የተረጋገጠ ፈላጊ ወይም የንብረት መለያ (Listing ID) ጠፍቷል።",
                        "om": "⚡ <b>Dogoggora:</b> Seeker ykn Listing ID dhabameera.",
                        "en": "⚡ <b>Error:</b> Missing verified Seeker or Provider Listing ID."
                    }
                    await send_response(err_msg.get(norm_lang, err_msg["en"]))
                    return await self.route_to_main_menu(update, context, suppress_invalid_prompt=True)
            else:
                seeker_id = update.effective_user.id
                provider_id = context.user_data.get('provider_id')
                listing_id = target_id
                if not provider_id:
                    err_msg = {
                        "am": "⚡ <b>ስህተት:</b> አቅራቢውን ማረጋገጥ አልተቻለም።",
                        "om": "⚡ <b>Dogoggora:</b> Dhiheessaa mirkaneessuu hin danda'amne.",
                        "en": "⚡ <b>Error:</b> Could not verify transport provider."
                    }
                    await send_response(err_msg.get(norm_lang, err_msg["en"]))
                    return await self.route_to_main_menu(update, context, suppress_invalid_prompt=True)

            deals_module = getattr(self, "deals_handler", getattr(self, "deals", None))
            if not deals_module or not hasattr(deals_module, "initiate_negotiation"):
                logger.error("Deals module handle is uninitialized or missing initiate_negotiation.")
                err_msg = {
                    "am": "⚡ <b>ስህተት:</b> የድርድር አገልግሎቱ በአሁኑ ጊዜ አይገኝም።",
                    "om": "⚡ <b>Dogoggora:</b> Tajaajilli mariichaa ammatti hin jiru.",
                    "en": "⚡ <b>Error:</b> Negotiation service is currently unavailable."
                }
                await send_response(err_msg.get(norm_lang, err_msg["en"]))
                return await self.route_to_main_menu(update, context, suppress_invalid_prompt=True)

            # =========================================================================
            # 🗄️ STRICT DATABASE-FIRST TRUTH EXTRACTION (ZERO SESSION/RESTART POLLUTION)
            # =========================================================================
            r_data = {}
            l_data = {}
            if supabase_client:
                try:
                    if target_id and is_reverse:
                        req_res = await supabase_client.table("seeker_requests").select("*").eq("id",
                                                                                                target_id).maybe_single().execute()
                        if req_res and req_res.data:
                            r_data = req_res.data

                    if not r_data and seeker_id:
                        req_res = await supabase_client.table("seeker_requests").select("*").eq("seeker_id",
                                                                                                seeker_id).order(
                            'created_at', desc=True).limit(1).maybe_single().execute()
                        if req_res and req_res.data:
                            r_data = req_res.data

                    if listing_id:
                        list_res = await supabase_client.table("provider_listings").select("*").eq("id",
                                                                                                   listing_id).maybe_single().execute()
                        if list_res and list_res.data:
                            l_data = list_res.data
                except Exception as db_fetch_err:
                    logger.warning(f"Failed fetching explicit attributes from database tables: {db_fetch_err}")

            domain_type = str(
                r_data.get('vertical_type') or l_data.get('vertical_type') or context.user_data.get('domain',
                                                                                                    'freight')).lower().strip()
            raw_asset_title = r_data.get('property_use') or l_data.get('property_use') or r_data.get(
                'vehicle_type') or l_data.get('vehicle_type') or "Transport Asset"

            clean_core_title = sanitize_asset_title(str(raw_asset_title), norm_lang)
            localized_asset_title = clean_core_title

            deal_id_str = await deals_module.initiate_negotiation(
                seeker_id=int(seeker_id),
                provider_id=int(provider_id),
                listing_id=listing_id,
                vertical_type=domain_type,
                initial_offer=agreed_price,
                currency=currency,
                asset_title=clean_core_title,
                is_reverse=is_reverse,
                notify_peer=False,
                notify_seeker=False,
                silent=True
            )

            if deal_id_str:
                context.user_data['active_deal_id'] = deal_id_str
                formatted_price = f"{agreed_price:,.2f}"
                escaped_title = html.escape(str(localized_asset_title))

                labels = {
                    "am": {"asset": "ንብረት", "rate": "የቀረበው ዋጋ"},
                    "om": {"asset": "Qabeenya", "rate": "Gatii Dhihaate"},
                    "en": {"asset": "Asset", "rate": "Offered Rate"}
                }
                lbl = labels.get(norm_lang, labels["en"])

                target_peer_id = int(seeker_id) if is_reverse else int(provider_id)

                async def resolve_recipient_lang(uid: int) -> str:
                    try:
                        if supabase_client:
                            user_res = await supabase_client.table("users").select(
                                "preferred_language, language, language_code").eq("telegram_id",
                                                                                  uid).maybe_single().execute()
                            if user_res and user_res.data:
                                u_data = user_res.data
                                raw_lang = u_data.get('preferred_language') or u_data.get('language') or u_data.get(
                                    'language_code')
                                if raw_lang:
                                    lng = str(raw_lang).lower().strip()
                                    if lng in ["or", "om", "oromoo", "afaan_oromoo", "afaan-oromoo"]:
                                        return "om"
                                    if lng in ["am", "amharic"]:
                                        return "am"
                                    return "en"

                        if hasattr(self, "_get_target_user_lang_isolated"):
                            raw_lang = await self._get_target_user_lang_isolated(uid)
                            lng = str(raw_lang).lower().strip() if raw_lang else "en"
                            if lng in ["or", "om", "oromoo", "afaan_oromoo", "afaan-oromoo"]:
                                return "om"
                            if lng in ["am", "amharic"]:
                                return "am"

                        return "en"
                    except Exception as lang_err:
                        logger.warning(f"Failed resolving recipient language for user {uid}: {lang_err}")
                        return "en"

                recipient_lang = await resolve_recipient_lang(target_peer_id)
                recipient_localized_asset_title = sanitize_asset_title(str(raw_asset_title), recipient_lang)
                escaped_recipient_title = html.escape(str(recipient_localized_asset_title))

                raw_req_v_type = r_data.get('vehicle_type') or r_data.get('property_use') or "Economy"
                raw_supp_v_type = l_data.get('vehicle_type') or l_data.get('property_use') or raw_req_v_type

                req_v_type = sanitize_asset_title(str(raw_req_v_type), norm_lang)
                supp_v_type = sanitize_asset_title(str(raw_supp_v_type), norm_lang)

                escaped_req_vehicle = html.escape(str(req_v_type).strip())
                escaped_supp_vehicle = html.escape(str(supp_v_type).strip())

                def extract_driver_route(listing: dict) -> str:
                    dep = listing.get('location_text') or listing.get('origin')
                    dest = listing.get('destination_text')
                    op_reg = listing.get('operating_region')
                    if dep and dest:
                        return f"{dep} ➔ {dest}"
                    if op_reg:
                        return str(op_reg).strip()
                    if dep:
                        return str(dep).strip()
                    return "Addis Ababa ➔ Adama"

                def extract_requested_route(req: dict) -> str:
                    dep = req.get('location_text') or req.get('origin_text')
                    dest = req.get('destination_text')
                    op_reg = req.get('operating_region')
                    if dep and dest:
                        return f"{dep} ➔ {dest}"
                    if op_reg:
                        return str(op_reg).strip()
                    if dep:
                        return str(dep).strip()
                    return "Adama ➔ Addis Ababa"

                driver_current_route = extract_driver_route(l_data)
                requested_route = extract_requested_route(r_data)

                escaped_driver_route = html.escape(str(driver_current_route))
                escaped_requested_route = html.escape(str(requested_route))

                ROUTE_LABELS = {
                    "en": {
                        "driver_route": "Driver Current Route",
                        "req_route": "Requested Route",
                        "req_vehicle": "Requested Vehicle",
                        "supp_vehicle": "Supplied Vehicle",
                        "party_size": "Number of Passengers (Party Size)",
                        "cargo_weight": "Cargo Weight"
                    },
                    "am": {
                        "driver_route": "የአሽከርካሪው የአሁን መሥመር",
                        "req_route": "የተጠየቀው መሥመር",
                        "req_vehicle": "የተጠየቀው ተሽከርካሪ",
                        "supp_vehicle": "የቀረበው ተሽከርካሪ",
                        "party_size": "የተሳፋሪ ብዛት (Party Size)",
                        "cargo_weight": "የጭነት ክብደት (Cargo Weight)"
                    },
                    "om": {
                        "driver_route": "Kallattii Ammaa Konkolaachisaa",
                        "req_route": "Kallattii Gaafatame",
                        "req_vehicle": "Konkolaataa Barbaadame",
                        "supp_vehicle": "Konkolaataa Dhihaate",
                        "party_size": "Baayyina Imaltootaa (Party Size)",
                        "cargo_weight": "Ulfaatina Fe'umsaa (Cargo Weight)"
                    }
                }

                r_lbls = ROUTE_LABELS.get(recipient_lang, ROUTE_LABELS["en"])
                sender_lbls = ROUTE_LABELS.get(norm_lang, ROUTE_LABELS["en"])

                sender_attr_line = ""
                if domain_type in ['ride', 'rides', 'vehicle', 'vehicles']:
                    p_size = r_data.get('party_size') or l_data.get('available_seats') or 3
                    if p_size and safe_float(p_size) > 0:
                        sender_attr_line += f"👥 <b>{sender_lbls['party_size']}:</b> {int(safe_float(p_size))}\n"
                else:
                    c_weight = r_data.get('cargo_weight_kgs')
                    if c_weight and safe_float(c_weight) > 0:
                        sender_attr_line += f"⚖️ <b>{sender_lbls['cargo_weight']}:</b> {safe_float(c_weight):,.0f} kg\n"

                sender_attr_line += f"📍 <b>{sender_lbls['req_route']}:</b> {escaped_requested_route}\n"
                sender_attr_line += f"📍 <b>{sender_lbls['driver_route']}:</b> {escaped_driver_route}\n"
                sender_attr_line += f"🚛 <b>{sender_lbls['req_vehicle']}:</b> {escaped_req_vehicle}\n"
                sender_attr_line += f"🚛 <b>{sender_lbls['supp_vehicle']}:</b> {escaped_supp_vehicle}\n"

                recipient_attr_line = ""
                if domain_type in ['ride', 'rides', 'vehicle', 'vehicles']:
                    p_size = r_data.get('party_size') or l_data.get('available_seats') or 3
                    if p_size and safe_float(p_size) > 0:
                        recipient_attr_line += f"👥 <b>{r_lbls['party_size']}:</b> {int(safe_float(p_size))}\n"
                else:
                    c_weight = r_data.get('cargo_weight_kgs')
                    if c_weight and safe_float(c_weight) > 0:
                        recipient_attr_line += f"⚖️ <b>{r_lbls['cargo_weight']}:</b> {safe_float(c_weight):,.0f} kg\n"

                recipient_attr_line += f"📍 <b>{r_lbls['req_route']}:</b> {escaped_requested_route}\n"
                recipient_attr_line += f"📍 <b>{r_lbls['driver_route']}:</b> {escaped_driver_route}\n"
                recipient_attr_line += f"🚛 <b>{r_lbls['req_vehicle']}:</b> {escaped_req_vehicle}\n"
                recipient_attr_line += f"🚛 <b>{r_lbls['supp_vehicle']}:</b> {escaped_supp_vehicle}\n"

                if is_reverse:
                    provider_confirm_msgs = {
                        "am": (
                            "⚡ <b>ስማርት ማች ስምምነት</b> ⚡\n"
                            "───────────────\n"
                            "📩 <b>የአቅራቢ ዋጋ ተልኳል</b>\n"
                            "━━━━━━━━━━━━━━━\n"
                            f"🚜 <b>{lbl['asset']}:</b> <i>{escaped_title}</i>\n"
                            f"{sender_attr_line}"
                            f"💵 <b>{lbl['rate']}:</b> <code>{formatted_price} {currency}</code>\n\n"
                            "⚡ <i>የመወዳደሪያ ዋጋው እና የንብረቱ ፎቶ ለፈላጊው በተሳካ ሁኔታ ደርሷል።</i>"
                        ),
                        "om": (
                            "⚡ <b>SMART MATCH DEAL</b> ⚡\n"
                            "───────────────\n"
                            "📩 <b>Dhiheessiin Ergameera</b>\n"
                            "━━━━━━━━━━━━━━━\n"
                            f"🚜 <b>{lbl['asset']}:</b> <i>{escaped_title}</i>\n"
                            f"{sender_attr_line}"
                            f"💵 <b>{lbl['rate']}:</b> <code>{formatted_price} {currency}</code>\n\n"
                            "⚡ <i>Dhiheessiin kee fi suuraan qabeenya keetii milkaa'inaan barbaadaaf ergameera.</i>"
                        ),
                        "en": (
                            "⚡ <b>SMART MATCH DEAL</b> ⚡\n"
                            "───────────────\n"
                            "📩 <b>Provider Offer Sent</b>\n"
                            "━━━━━━━━━━━━━━━\n"
                            f"🚜 <b>{lbl['asset']}:</b> <i>{escaped_title}</i>\n"
                            f"{sender_attr_line}"
                            f"💵 <b>{lbl['rate']}:</b> <code>{formatted_price} {currency}</code>\n\n"
                            "⚡ <i>Fulfillment rate and asset photo securely relayed to seeker.</i>"
                        )
                    }
                else:
                    provider_confirm_msgs = {
                        "am": (
                            "⚡ <b>ስማርት ማች ስምምነት</b> ⚡\n"
                            "───────────────\n"
                            "📩 <b>የፈላጊ ዋጋ ተልኳል</b>\n"
                            "━━━━━━━━━━━━━━━\n"
                            f"🚜 <b>{lbl['asset']}:</b> <i>{escaped_title}</i>\n"
                            f"{sender_attr_line}"
                            f"💵 <b>{lbl['rate']}:</b> <code>{formatted_price} {currency}</code>\n\n"
                            "⚡ <i>ለመጓጓዥ አገልግሎቱ ያቀረቡት የዋጋ ተመን በተሳካ ሁኔታ ተልኳል።</i>"
                        ),
                        "om": (
                            "⚡ <b>SMART MATCH DEAL</b> ⚡\n"
                            "───────────────\n"
                            "📩 <b>Gatii Ergameera</b>\n"
                            "━━━━━━━━━━━━━━━\n"
                            f"🚜 <b>{lbl['asset']}:</b> <i>{escaped_title}</i>\n"
                            f"{sender_attr_line}"
                            f"💵 <b>{lbl['rate']}:</b> <code>{formatted_price} {currency}</code>\n\n"
                            "⚡ <i>Gatiin tajaajila geejjibaaf dhiyeessitan milkiidhaan ergameera.</i>"
                        ),
                        "en": (
                            "⚡ <b>SMART MATCH DEAL</b> ⚡\n"
                            "───────────────\n"
                            "📩 <b>Deal Bid Transmitted</b>\n"
                            "━━━━━━━━━━━━━━━\n"
                            f"🚜 <b>{lbl['asset']}:</b> <i>{escaped_title}</i>\n"
                            f"{sender_attr_line}"
                            f"💵 <b>{lbl['rate']}:</b> <code>{formatted_price} {currency}</code>\n\n"
                            "⚡ <i>Your price offer for the transport service has been successfully transmitted.</i>"
                        )
                    }

                provider_text = provider_confirm_msgs.get(norm_lang, provider_confirm_msgs["en"])
                await send_response(provider_text, reply_markup=None)

                btn_accept = get_text(recipient_lang, "BTN_ACCEPT_DEAL") or (
                    "✅ ዋጋውን ተቀበል" if recipient_lang == "am" else "✅ Fudhadhu" if recipient_lang == "om" else "✅ Accept Offer")
                btn_counter = get_text(recipient_lang, "BTN_COUNTER_OFFER") or (
                    "📉 የመደራደሪያ ዋጋ አቅርብ" if recipient_lang == "am" else "📉 Gatii Kaadhimuu" if recipient_lang == "om" else "📉 Counter Offer")
                btn_decline = get_text(recipient_lang, "BTN_DECLINE_DEAL") or (
                    "❌ ውድቅ አድርግ" if recipient_lang == "am" else "❌ Dhiisi" if recipient_lang == "om" else "❌ Decline")

                deal_markup = InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(btn_accept, callback_data=f"deal_accept:{deal_id_str}"),
                        InlineKeyboardButton(btn_counter, callback_data=f"deal_counter:{deal_id_str}")
                    ],
                    [
                        InlineKeyboardButton(btn_decline, callback_data=f"deal_decline:{deal_id_str}")
                    ]
                ])

                presigned_url = None
                if photo_ref and is_reverse:
                    img_service = getattr(self, "image_service", getattr(self, "storage", None))
                    if img_service and hasattr(img_service, "get_presigned_url_async"):
                        try:
                            presigned_url = await img_service.get_presigned_url_async(photo_ref)
                        except Exception as sign_err:
                            logger.warning(f"Failed to generate pre-signed URL for photo_ref '{photo_ref}': {sign_err}")

                reverse_descriptions = {
                    "en": "ℹ️ <i>The provider offered a price on your service request:</i>\n",
                    "am": "ℹ️ <i>አቅራቢው ባቀረቡት የአገልግሎት ጥያቄ ላይ ዋጋ አቅርቧል:</i>\n",
                    "om": "ℹ️ <i>Dhiheessaan kun tajaajila gaafattan irratti gatii dhiheesseera:</i>\n"
                }
                current_reverse_desc = reverse_descriptions.get(recipient_lang, reverse_descriptions["en"])

                peer_notice_msgs = {
                    "am": (
                        f"🔄 <b>አዲስ {'የተገላቢጦሽ' if is_reverse else 'የማቅረቢያ'} ጥያቄ ({escaped_recipient_title})!</b>\n"
                        f"{f'💬 _{current_reverse_desc}_' if is_reverse else ''}\n\n"
                        f"🏷️ <b>ግብይት:</b> አገልግሎት\n"
                        f"📦 <b>ንብረት/ዕቃ:</b> {escaped_recipient_title}\n"
                        f"{recipient_attr_line}"
                        f"💰 <b>የቀረበ ዋጋ:</b> <b>{formatted_price} {currency}</b>\n\n"
                        "<i>እባክዎ የቀረበውን ዋጋ ተመልክተው ከታች ካሉት አማራጮች ውሳኔ ይስጡ።</i>"
                    ),
                    "om": (
                        f"🔄 <b>Dhiheessii {'Garagalfamaa' if is_reverse else 'Haaraa'} ({escaped_recipient_title})!</b>\n"
                        f"{f'💬 _{current_reverse_desc}_' if is_reverse else ''}\n\n"
                        f"🏷️ <b>Gosa Daldalaa:</b> Tajaajila\n"
                        f"📦 <b>Qabeenya/Fe'umsa:</b> {escaped_recipient_title}\n"
                        f"{recipient_attr_line}"
                        f"💰 <b>Gatii Dhihaate:</b> <b>{formatted_price} {currency}</b>\n\n"
                        "<i>Maaloo gatii dhihaate madaaluun filannoo armaan gadii irraa murtoo kennaa.</i>"
                    ),
                    "en": (
                        f"🔄 <b>New {'Reverse Match' if is_reverse else ''} Offer ({escaped_recipient_title})!</b>\n"
                        f"{f'💬 _{current_reverse_desc}_' if is_reverse else ''}\n\n"
                        f"🏷️ <b>Transaction:</b> Service\n"
                        f"📦 <b>Asset/Item:</b> {escaped_recipient_title}\n"
                        f"{recipient_attr_line}"
                        f"💰 <b>Offered Price:</b> <b>{formatted_price} {currency}</b>\n\n"
                        "<i>Please evaluate the offered rate and select an action below.</i>"
                    )
                }
                peer_notice_text = peer_notice_msgs.get(recipient_lang, peer_notice_msgs["en"])

                if presigned_url and target_peer_id and is_reverse:
                    try:
                        await context.bot.send_photo(
                            chat_id=target_peer_id,
                            photo=presigned_url,
                            caption=peer_notice_text,
                            reply_markup=deal_markup,
                            parse_mode="HTML"
                        )
                    except Exception as photo_dispatch_err:
                        logger.warning(f"Failed to send photo to target peer {target_peer_id}: {photo_dispatch_err}")
                        await context.bot.send_message(
                            chat_id=target_peer_id,
                            text=peer_notice_text,
                            reply_markup=deal_markup,
                            parse_mode="HTML"
                        )
                elif target_peer_id:
                    await context.bot.send_message(
                        chat_id=target_peer_id,
                        text=peer_notice_text,
                        reply_markup=deal_markup,
                        parse_mode="HTML"
                    )

            else:
                block_msgs = {
                    "am": "🚫 <b>የድርድር መስመር ታግዷል:</b> ንብረቱ በሌላ ድርድር ላይ ነው ወይም የራስዎ ማስታወቂያ ነው።",
                    "om": "🚫 <b>Mariin Cufameera:</b> Qabeenyi kun ammaan tana marii irra jira.",
                    "en": "🚫 <b>Pipeline Blocked:</b> Target asset is actively engaged in another negotiation."
                }
                await send_response(block_msgs.get(norm_lang, block_msgs["en"]), reply_markup=None)

        except Exception as e:
            logger.exception(f"Unexpected faulting in bidirectional negotiation lock execution: {e}")
            err_msg = {
                "am": "⚠️ <b>የሲስተም ስህተት:</b> የድርድር ፍሰት መጀመር አልተቻለም።",
                "om": "⚠️ <b>Dogoggora:</b> Adeemsa marii jalqabuu hin danda'amne.",
                "en": "⚠️ <b>System Error:</b> Unable to initiate synchronized negotiation stream."
            }
            await send_response(err_msg.get(norm_lang, err_msg["en"]))

        finally:
            context.user_data.pop('is_reverse_match', None)
            context.user_data.pop('awaiting_counter_offer', None)
            context.user_data.pop('_lock_in_progress', None)

        return await self.route_to_main_menu(update, context, suppress_invalid_prompt=True)

    async def handle_match_cancellation(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        query = update.callback_query
        await query.answer("Matrix search query cancelled.")
        lang = self._get_lang(context)

        context.user_data.pop('freight_top_matches', None)
        context.user_data.pop('seeker_top_matches', None)
        context.user_data.pop('is_reverse_match', None)

        cancel_msg = get_text(lang,
                              "msg_match_cancelled") or "❌ Process search sequence terminated. Returning to unified main menu."
        try:
            await query.edit_message_text(cancel_msg, parse_mode="HTML")
        except Exception:
            pass

        return await self.route_to_main_menu(update, context)

    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        lang = self._get_lang(context)
        cancel_txt = get_text(lang, "MSG_PROCESS_CANCELLED") or "❌ Operational interaction sequence reliably cancelled."

        if update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.edit_message_text(cancel_txt)
        elif update.message:
            await update.message.reply_text(cancel_txt)

        return await self.route_to_main_menu(update, context)

    @staticmethod
    async def fallback_unmatched_callback(
            update: Update, _context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        """
        Safety net catch-all handler for stale, out-of-order, or unrecognized callback queries
        to prevent bot warning logs and UI button spinner hangs.
        """
        query = update.callback_query
        if query:
            try:
                # ✅ Add explicit timeouts to fail fast and prevent network hangs
                await query.answer(
                    "⚠️ Action expired or unavailable.",
                    show_alert=False,
                    read_timeout=5.0,
                    write_timeout=5.0,
                    connect_timeout=3.0
                )
            except Exception as err:
                err_msg = str(err).lower()
                # ✅ Gracefully suppress noisy warnings for queries that are already expired or answered
                if any(keyword in err_msg for keyword in ["too old", "expired", "invalid", "already answered"]):
                    logger.debug(f"Ignored stale/expired callback query: {err}")
                else:
                    logger.warning(f"Failed answering fallback query: {err}")
        return ConversationHandler.END

    def get_conversation_handler(self) -> ConversationHandler:
        """
        Constructs a complete, unified, robust, and production-ready ConversationHandler
        for freight logistics and ride-sharing. Utilizes strict, namespaced regex patterns
        and isolated callback prefixes to permanently eliminate cross-domain interception
        and stale callback fallthroughs.
        """
        from src.smartmatch.handlers.main_menu import MainMenuHandler

        return ConversationHandler(
            entry_points=[
                CommandHandler("freight", self.start_freight),
                CommandHandler("ride", self.start_ride),
                MessageHandler(
                    filters.Text(MainMenuHandler.get_labels_for_action("btn_freight")),
                    self.start_freight
                ),
                MessageHandler(
                    filters.Text(MainMenuHandler.get_labels_for_action("btn_ride")),
                    self.start_ride
                ),
                CallbackQueryHandler(self.start_freight, pattern="^(btn_freight|start_freight)$"),
                CallbackQueryHandler(self.start_ride, pattern="^(btn_ride|start_ride)$"),
                CallbackQueryHandler(self.handle_role, pattern="^role_(shipper|carrier|passenger|driver)$"),
                CallbackQueryHandler(self.prompt_initial_offer, pattern="^fr_offer:"),
                CallbackQueryHandler(self.prompt_initial_offer, pattern="^offer_freight:"),
                # ✅ Entry points for reverse and forward match selections
                CallbackQueryHandler(self.handle_match_selection, pattern="^fr_select_seeker_match:"),
                CallbackQueryHandler(self.handle_match_selection, pattern="^select_seeker_match:"),
                CallbackQueryHandler(self.handle_match_selection, pattern="^select_freight_match:"),
            ],
            states={
                None: [
                    CallbackQueryHandler(self.handle_role, pattern="^role_(shipper|carrier|passenger|driver)$"),
                    CallbackQueryHandler(self.start_freight, pattern="^(btn_freight|start_freight)$"),
                    CallbackQueryHandler(self.start_ride, pattern="^(btn_ride|start_ride)$"),
                    CommandHandler("freight", self.start_freight),
                    CommandHandler("ride", self.start_ride),
                    MessageHandler(
                        filters.Text(MainMenuHandler.get_labels_for_action("btn_freight")),
                        self.start_freight
                    ),
                    MessageHandler(
                        filters.Text(MainMenuHandler.get_labels_for_action("btn_ride")),
                        self.start_ride
                    ),
                    # ✅ Fallback match selection handlers when conversation state is None
                    CallbackQueryHandler(self.handle_match_selection, pattern="^fr_select_seeker_match:"),
                    CallbackQueryHandler(self.handle_match_selection, pattern="^select_seeker_match:"),
                    CallbackQueryHandler(self.handle_match_selection, pattern="^select_freight_match:"),
                ],
                self.ROLE_SELECTION: [
                    CallbackQueryHandler(self.handle_role, pattern="^role_(shipper|carrier|passenger|driver)$"),
                    CallbackQueryHandler(self.route_to_main_menu,
                                         pattern="^(cancel_match|cancel_vertical_offer|back_to_main)$")
                ],
                self.LOCATION_ORIGIN: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_origin),
                    CallbackQueryHandler(self.route_to_main_menu,
                                         pattern="^(cancel_match|cancel_vertical_offer|back_to_main)$")
                ],
                self.LOCATION_DESTINATION: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_destination),
                    CallbackQueryHandler(self.route_to_main_menu,
                                         pattern="^(cancel_match|cancel_vertical_offer|back_to_main)$")
                ],
                self.LOCATION_REGION: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_region),
                    CallbackQueryHandler(self.route_to_main_menu,
                                         pattern="^(cancel_match|cancel_vertical_offer|back_to_main)$")
                ],
                self.GPS_LOCATION: [
                    MessageHandler(
                        (filters.LOCATION | filters.TEXT) & ~filters.COMMAND,
                        self.handle_gps
                    ),
                    # Captures live location position stream updates sent as edited messages
                    MessageHandler(
                        filters.UpdateType.EDITED_MESSAGE & filters.LOCATION,
                        self.handle_gps
                    ),
                    CallbackQueryHandler(self.route_to_main_menu,
                                         pattern="^(cancel_match|cancel_vertical_offer|back_to_main)$")
                ],
                self.CAPACITY_SPECS: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_capacity),
                    CallbackQueryHandler(self.route_to_main_menu,
                                         pattern="^(cancel_match|cancel_vertical_offer|back_to_main)$")
                ],
                self.CLASS_TYPE: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_class_type),
                    CallbackQueryHandler(self.route_to_main_menu,
                                         pattern="^(cancel_match|cancel_vertical_offer|back_to_main)$")
                ],
                self.AMENITIES_EXTRAS: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_extras),
                    CallbackQueryHandler(self.route_to_main_menu,
                                         pattern="^(cancel_match|cancel_vertical_offer|back_to_main)$")
                ],
                self.BUDGET_FARE: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_budget),
                    CallbackQueryHandler(self.route_to_main_menu,
                                         pattern="^(cancel_match|cancel_vertical_offer|back_to_main)$")
                ],
                self.CONTACT_INFO: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_contact_info),
                    CallbackQueryHandler(self.route_to_main_menu,
                                         pattern="^(cancel_match|cancel_vertical_offer|back_to_main)$")
                ],
                self.PLATE_REGISTRATION: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_vehicle_plate),
                    CallbackQueryHandler(self.route_to_main_menu,
                                         pattern="^(cancel_match|cancel_vertical_offer|back_to_main)$")
                ],
                self.PHOTO_UPLOAD: [
                    # Catch compressed photos and uncompressed image files/documents
                    MessageHandler(filters.PHOTO | filters.Document.IMAGE, self.handle_photo_upload),

                    # Catch text inputs (e.g., 'skip' or invalid text)
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_photo_upload),

                    # Catch cancellation buttons
                    CallbackQueryHandler(self.route_to_main_menu,
                                         pattern="^(cancel_match|cancel_vertical_offer|back_to_main)$"),

                    # Fallback handler to prevent dropping to None on stickers/audio/etc.
                    MessageHandler(filters.ALL & ~filters.COMMAND, self.handle_unsupported_photo_input)
                ],
                self.SELECTING_MATCH: [
                    CallbackQueryHandler(self.handle_match_selection, pattern="^select_freight_match:"),
                    CallbackQueryHandler(self.handle_match_selection, pattern="^select_seeker_match:"),
                    # ✅ Explicitly register reverse matching seeker selection prefix
                    CallbackQueryHandler(self.handle_match_selection, pattern="^fr_select_seeker_match:"),
                    CallbackQueryHandler(self.prompt_custom_offer, pattern="^fr_prompt_offer:"),
                    CallbackQueryHandler(self._process_freight_ride_lock, pattern="^lock_freight_deal:"),
                    CallbackQueryHandler(self.handle_back_to_top_matches, pattern="^fr_back_to_top_matches$"),
                    CallbackQueryHandler(self.route_to_main_menu,
                                         pattern="^(cancel_match|cancel_vertical_offer|back_to_main)$"),
                ],
                self.ENTERING_OFFER: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.receive_custom_offer),
                    CallbackQueryHandler(self.route_to_main_menu,
                                         pattern="^(cancel_match|cancel_vertical_offer|back_to_main)$"),
                ],
                self.AWAITING_FREIGHT_OFFER: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.process_initial_offer),
                    CallbackQueryHandler(self.route_to_main_menu,
                                         pattern="^(cancel_match|cancel_vertical_offer|back_to_main)$"),
                ]
            },
            fallbacks=[
                CommandHandler("cancel", self.route_to_main_menu),
                CommandHandler("menu", self.route_to_main_menu),
                CommandHandler("start", self.route_to_main_menu),
                CallbackQueryHandler(self.route_to_main_menu,
                                     pattern="^(cancel_match|cancel_vertical_offer|fr_back_to_top_matches|back_to_main)$"),
                # Catch-all fallback to safely answer any unmatched/expired callbacks and prevent spinner hangs
                CallbackQueryHandler(self.fallback_unmatched_callback, pattern=".*"),
            ],
            name="freight_ride_registration_flow",
            persistent=True,
            per_user=True,
            per_chat=True,
            per_message=False,
            allow_reentry=True,
            conversation_timeout=None
        )


def setup_freight_ride_handler(db, ai, matching, billing, storage, deals_handler, queue) -> ConversationHandler:
    handler = FreightRideHandler(
        db_manager=db,
        ai_engine=ai,
        matching_engine=matching,
        billing_service=billing,
        image_service=storage,
        deals_handler=deals_handler,
        queue_manager=queue
    )
    return handler.get_conversation_handler()
