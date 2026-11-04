import asyncio
import html
import json
import logging
import re
import uuid
from typing import Optional, Dict, List, Any, cast, Tuple

from deep_translator import GoogleTranslator

try:
    from googletrans import Translator
except ImportError:
    Translator = None

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatAction
from telegram.error import BadRequest, TelegramError, TimedOut, NetworkError, RetryAfter
from telegram.ext import (
    ContextTypes, ConversationHandler, CommandHandler,
    MessageHandler, CallbackQueryHandler, filters
)

# Import the translation service
from src.smartmatch.utils.translations import get_text

logger = logging.getLogger("smartmatch.handlers.vehicle")

# ==============================================================================
# OPTIMIZED MODULE-LEVEL REGEX PATTERNS & CONSTANTS
# ==============================================================================
NUMERIC_CLEAN_RE = re.compile(r"[^\d.]")
BOLD_MARKDOWN_RE = re.compile(r"\*\*(.*?)\*\*")

# ==============================================================================
# KNOWN ACRONYMS & ABBREVIATION PRESERVATION TOOLKIT
# ==============================================================================
KNOWN_ACRONYMS = {
    "MCI", "BMW", "CAT", "EV", "LNG", "NGV", "CNG", "LPG", "ETB", "USD", "EUR",
    "GBP", "FCEV", "4WD", "AWD", "2WD", "SUV", "VAN", "ATV", "UTV", "AC", "GPS",
    "ABS", "LED", "ID", "VIN", "JCB", "MAN", "DAF", "J4500", "FZR", "V8", "V6"
}


def format_title_preserve_acronyms(text: str) -> str:
    """
    Capitalizes words in a string while preserving domain acronyms and abbreviations
    (e.g., MCI, BMW, CAT, EV, LNG, NGV, 4WD, J4500, etc.) in uppercase.
    """
    if not text:
        return ""

    words = text.strip().split()
    formatted_words = []

    for word in words:
        # Extract clean alphanumeric token without surrounding punctuation
        clean_word = re.sub(r'^\W+|\W+$', '', word).upper()

        prefix_len = len(word) - len(word.lstrip('({["\''))
        prefix = word[:prefix_len]
        suffix_len = len(word) - len(word.rstrip(')}]"\''))
        suffix = word[len(word) - suffix_len:] if suffix_len > 0 else ""

        # Keep uppercase if it is a known acronym or already all-caps token (>= 2 chars)
        if clean_word in KNOWN_ACRONYMS or (word.isupper() and len(clean_word) >= 2 and not clean_word.isdigit()):
            formatted_words.append(f"{prefix}{clean_word}{suffix}")
        elif word.islower():
            if clean_word in KNOWN_ACRONYMS:
                formatted_words.append(f"{prefix}{clean_word}{suffix}")
            else:
                formatted_words.append(word.capitalize())
        else:
            # Standard capitalization preserving inner uppercase numbers (e.g., J4500)
            formatted_words.append(word[0].upper() + word[1:])

    return " ".join(formatted_words)


# ==============================================================================
# FAST O(1) STATE EVALUATION LOOKUP SETS
# ==============================================================================

SEEKER_KEYS = {
    "is_seeker",
    "listing_type",
    "vehicle_type",
    "make_model_year",
    "min_used_years",
    "acceptable_fuel_type",
    "must_have_features",
    "location_text",
    "latitude",
    "longitude",
    "max_budget",
    "currency",
}

PROVIDER_KEYS = {
    "is_provider",
    "listing_type",
    "vehicle_type",
    "make_model_year",
    "years_vehicle_used",
    "fuel_type",
    "features",
    "location_text",
    "latitude",
    "longitude",
    "price",
    "currency",
    "asset_photo_url",
}

FUEL_LAYOUT: Tuple[Tuple[Tuple[str, str], ...], ...] = (
    (("fuel_benzine", "vehicle.fuel.benzine"), ("fuel_diesel", "vehicle.fuel.diesel")),
    (("fuel_electric", "vehicle.fuel.electric"), ("fuel_lng_ngv", "vehicle.fuel.lng_ngv")),
    (("fuel_hybrid_petrol", "vehicle.fuel.hybrid_petrol"), ("fuel_hybrid_diesel", "vehicle.fuel.hybrid_diesel")),
    (("fuel_hybrid_flex", "vehicle.fuel.hybrid_flex"), ("fuel_hybrid_gaseous", "vehicle.fuel.hybrid_gaseous")),
    (("fuel_hybrid_hydrogen", "vehicle.fuel.hybrid_hydrogen"),),
)

PLACEHOLDER_STRINGS = {
    "standard vehicle request",
    "የመደበኛ ተሽከርካሪ ጥያቄ",
    "gaaffii konkolaataa idilee",
    "standard vehicle",
    "offered vehicle",
    "konkolaataa dhiyaate",
    "የተዘረዘረ ተሽከርካሪ",
    "vehicle",
    "konkolaataa",
    "ተሽከርካሪ",
    "none",
    "null",
    "n/a",
    "na",
    "not_specified",
    "not specified",
    "undefined",
    ""
}


def safe_str(val: Any, default: str = "") -> str:
    """Safely converts dynamic inputs to stripped strings with default fallback."""
    if val is None:
        return default
    s = str(val).strip()
    return s if s else default


def safe_float(val: Any, default: float = 0.0) -> float:
    """Safely converts dynamic inputs to float, handling native types and noisy strings."""
    if val is None:
        return default
    if isinstance(val, (int, float)):
        return float(val)
    try:
        clean_val = NUMERIC_CLEAN_RE.sub("", str(val).replace(",", ""))
        return float(clean_val) if clean_val else default
    except (ValueError, TypeError):
        return default


def safe_int(value: Any, default: int = 0) -> int:
    """Module-level safe integer parser for user IDs and numerical IDs."""
    if value is None:
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def _clean_val(val: Any, default: Optional[str] = None) -> Optional[str]:
    """Sanitizes inputs and strips out generic placeholder strings."""
    if val is None:
        return default
    s_val = str(val).strip()
    if not s_val or s_val.lower() in PLACEHOLDER_STRINGS:
        return default
    return s_val


def _format_mmy(data: Dict[str, Any], _keys: Optional[List[str]] = None, default: str = "Vehicle") -> str:
    """
    Constructs or extracts a clean Make/Model/Year string from dictionary records across
    provider_listings, seeker_requests, and deals tables. Preserves all acronyms in uppercase.
    """
    if not data or not isinstance(data, dict):
        return default

    # Tier 1: Check full descriptive make/model/year fields across schemas
    full_title_keys = [
        'make_model_year', 'preferred_make_model_year', 'make_model',
        'title', 'asset_title', 'asset_name'
    ]
    for k in full_title_keys:
        val = _clean_val(data.get(k))
        if val:
            return format_title_preserve_acronyms(val)

    # Tier 2: Construct from individual brand/make, model, and year fields
    brand = _clean_val(data.get('brand') or data.get('make') or data.get('preferred_brand'))
    model = _clean_val(data.get('model'))
    year = _clean_val(data.get('year'))

    constructed = " ".join(filter(None, [brand, model, year])).strip()
    cleaned = _clean_val(constructed)
    if cleaned:
        return format_title_preserve_acronyms(cleaned)

    # Tier 3: Category or vehicle_type descriptors
    v_type = _clean_val(data.get('vehicle_type') or data.get('property_use') or data.get('category'))
    if v_type:
        return format_title_preserve_acronyms(v_type)

    return default


def _is_tx_compatible(tx1: str, tx2: str) -> bool:
    """Validates transaction type compatibility across rental and sale operations."""
    if not tx1 or not tx2:
        return True
    t1, t2 = safe_str(tx1).lower(), safe_str(tx2).lower()
    if not t1 or not t2:
        return True
    if t1 == t2:
        return True

    rentals = {'rent', 'rental', 'lease', 'hire'}
    sales = {'sell', 'buy', 'sale', 'purchase'}

    return (t1 in rentals and t2 in rentals) or (t1 in sales and t2 in sales)


class VehicleHandler:
    """
    Manages conversational flows for vehicle transactions on Smart Match Africa.
    Enforces acronym preservation, database schema alignment, and multilingual support.
    """
    (
        AWAITING_VEHICLE_OFFER,
        TRANSACTION_TYPE,  # Step 1: Sell/Rent vs Buy/Rent
        VEHICLE_TYPE,  # Step 2: sedan, suv, van, bus, truck, moto
        MAKE_MODEL_YEAR,  # Step 3: Brand, model, production year
        CONDITION_YEARS,  # Step 4: years_vehicle_used vs min_used_years
        FUEL_TYPE,  # Step 5: Benzine, Diesel, Electric, Hybrid
        FEATURE_SPECS,  # Step 6: Core equipment / mandatory options
        LOCATION_DATA,  # Step 7: Parking location vs Intended area of use
        PRICE_BUDGET,  # Step 8: Price vs Max Budget
        PHOTO_UPLOAD,  # Step 9: S3 Object Streaming Buffer
        CONTACT_INFO,  # Step 10: Final Direct Routing Vector
        AWAITING_MATCH_SELECTION,  # Step 11: Displaying match cards
        ENTERING_OFFER,  # Step 12: Custom offer capture
    ) = range(13)

    def __init__(self, db_manager: Any, ai_engine: Any, matching_engine: Any,
                 billing_service: Any, image_service: Any, deals_handler: Any,
                 queue_manager: Any):

        self.db: Any = db_manager
        self.ai: Any = ai_engine
        self.matching: Any = matching_engine
        self.billing: Any = billing_service
        self.storage: Any = image_service
        self.image_service: Any = image_service
        self.deals_handler: Any = deals_handler
        self.queue: Any = queue_manager

    @staticmethod
    def _get_user_lang(context: Optional[ContextTypes.DEFAULT_TYPE] = None) -> str:
        """Retrieves the current user's preferred language code from session data."""
        if context and context.user_data:
            return (
                    context.user_data.get('language') or
                    context.user_data.get('lang') or
                    'en'
            ).lower()
        return 'en'

    @classmethod
    def _tx(
            cls,
            context: ContextTypes.DEFAULT_TYPE,
            key: str,
            lang: Optional[str] = None,
            **kwargs
    ) -> str:
        """Retrieves the localized string based on target language."""
        target_lang = (
                lang or
                kwargs.pop('lang', None) or
                (context.user_data.get('language') or context.user_data.get(
                    'lang') if context and context.user_data else None) or
                'en'
        ).lower()

        text = get_text(target_lang, key, **kwargs)
        if text is not None:
            return text
        return f"[{key}]"

    # ==========================================
    # OFFER NEGOTIATION PATHWAY (DEALS HANDLER)
    # ==========================================

    async def prompt_initial_offer(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """
        Entry Point: Triggered when a user clicks 'vehicle_offer:uuid_here'.
        Transitions the user into an input state to type their starting price.
        """
        query = update.callback_query
        if query:
            await query.answer()

        try:
            _, listing_id = query.data.split(":", 1)
        except (ValueError, AttributeError):
            logger.error(f"Malformed callback data: {query.data if query else None}")
            return ConversationHandler.END

        context.user_data["target_listing_id"] = listing_id
        context.user_data["vertical_type"] = "vehicle"

        prompt_text = self._tx(context, "vehicle.offer.prompt")

        keyboard = [
            [
                InlineKeyboardButton(
                    self._tx(context, "vehicle.btn.cancel_offer"),
                    callback_data="cancel_vertical_offer"
                )
            ]
        ]

        await query.edit_message_text(
            text=prompt_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        return self.AWAITING_VEHICLE_OFFER

    async def process_initial_offer(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """
        Data Capture: Captures numerical text, validates it, and spawns the Deal State Machine.
        """
        if not update.message or not update.message.text:
            return self.AWAITING_VEHICLE_OFFER

        user_id = update.effective_user.id
        text_payload = update.message.text.strip()
        listing_id = context.user_data.get("target_listing_id")
        vertical = context.user_data.get("vertical_type")

        try:
            clean_numeric = text_payload.replace(",", "").replace("ETB", "").replace("birr", "").strip()
            initial_offer = safe_float(clean_numeric)
            if initial_offer <= 0:
                raise ValueError("Price must be positive.")
        except ValueError:
            await update.message.reply_text(self._tx(context, "vehicle.error.invalid_number"))
            return self.AWAITING_VEHICLE_OFFER

        listing = await self.db.get_listing_by_id(listing_id)
        if not listing:
            await update.message.reply_text(self._tx(context, "vehicle.error.listing_unavailable"))
            context.user_data.clear()
            return ConversationHandler.END

        provider_id = int(listing.get("provider_id", 0))

        if provider_id == user_id:
            await update.message.reply_text(self._tx(context, "vehicle.error.cannot_offer_own"))
            context.user_data.clear()
            return ConversationHandler.END

        deals_handler = context.bot_data.get("deals_handler") or self.deals_handler
        if not deals_handler:
            logger.error("DealsHandler not found in bot_data. System offline.")
            await update.message.reply_text(self._tx(context, "vehicle.error.system_offline"))
            return ConversationHandler.END

        deal_id = await deals_handler.initiate_negotiation(
            seeker_id=user_id,
            provider_id=provider_id,
            listing_id=listing_id,
            vertical_type=vertical,
            initial_offer=initial_offer
        )

        if deal_id:
            msg = self._tx(context, "vehicle.offer.sent_success", initial_offer=f"{initial_offer:,.2f}")
            await update.message.reply_text(msg, parse_mode="Markdown")
        else:
            await update.message.reply_text(self._tx(context, "vehicle.offer.sent_fail"))

        context.user_data.clear()
        return ConversationHandler.END

    # ==========================================
    # INTER-MODULE LINKS & ROUTING
    # ==========================================

    async def show_main_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Helper method to navigate the user directly back to the primary menu interface."""
        menu_handler = context.bot_data.get("main_menu_handler")
        if menu_handler and hasattr(menu_handler, "show_main_menu"):
            return await menu_handler.show_main_menu(update, context)

        lang = self._get_user_lang(context)
        try:
            fallback_msg = get_text(lang,
                                    "msg_return_root") or "🏠 <b>Main Menu</b>\n\nSelect an option below to continue:"
        except Exception:
            fallback_msg = "🏠 <b>Main Menu</b>\n\nSelect an option below to continue:"

        try:
            from src.smartmatch.handlers.main_menu import MainMenuHandler
            reply_markup = MainMenuHandler.get_persistent_keyboard(lang)
        except Exception:
            reply_markup = None

        if update.effective_chat:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=fallback_msg,
                reply_markup=reply_markup,
                parse_mode="HTML"
            )

        return ConversationHandler.END

    @classmethod
    async def route_to_main_menu(cls, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """
        Safely drops the active conversation frame, flushes vehicle-specific session data
        (while preserving user preferences like language), and re-instantiates the main menu hub.
        """
        # Flush temporary flow state only
        temp_flow_keys = [
            'flow',
            'vehicle_top_matches',
            'active_vehicle_deal_id',
            'transaction_type',
            'vehicle_type',
            'make_model',
            'year',
            'mileage',
            'condition',
            'price',
            'price_budget',
            'quantity',
            'provider_id',
            'asset_title',
            'asset_photo_url',
            'photo_url',
            'location_data'
        ]
        for key in temp_flow_keys:
            context.user_data.pop(key, None)

        lang = (
                context.user_data.get('language') or
                context.user_data.get('lang') or
                'en'
        ).lower()

        if update.callback_query:
            try:
                await update.callback_query.answer()
            except Exception:
                pass

        menu_handler = context.bot_data.get("main_menu_handler")
        if menu_handler and hasattr(menu_handler, "show_main_menu"):
            return await menu_handler.show_main_menu(update, context)

        try:
            fallback_msg = get_text(lang,
                                    "msg_return_root") or "🏠 <b>Main Menu</b>\n\nSelect an option below to continue:"
        except Exception:
            fallback_msg = "🏠 <b>Main Menu</b>\n\nSelect an option below to continue:"

        try:
            from src.smartmatch.handlers.main_menu import MainMenuHandler
            reply_markup = MainMenuHandler.get_persistent_keyboard(lang)
        except Exception:
            reply_markup = None

        if update.effective_chat:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=fallback_msg,
                reply_markup=reply_markup,
                parse_mode="HTML"
            )

        return ConversationHandler.END

    # ==========================================
    # PROVIDER / SEEKER MAIN PIPELINE (OPTIMIZED)
    # ==========================================

    @staticmethod
    def _get_active_flow(
            context: ContextTypes.DEFAULT_TYPE, update: Optional[Update] = None
    ) -> str:
        """Determines active flow ('find' vs 'list') in O(1) time."""
        user_data = context.user_data
        if user_data is None:
            context.user_data = user_data = {}

        flow = user_data.get("flow")
        if flow in ("list", "find"):
            return flow

        if update and update.callback_query and update.callback_query.data:
            cb_data = str(update.callback_query.data).lower()
            if cb_data.startswith("find_") or any(term in cb_data for term in ("buy", "search", "seeker")):
                user_data["flow"] = "find"
                return "find"
            if cb_data.startswith("list_") or any(term in cb_data for term in ("sell", "rent_out", "provider")):
                user_data["flow"] = "list"
                return "list"

        user_keys = set(user_data.keys())
        if user_data.get("is_seeker") or not SEEKER_KEYS.isdisjoint(user_keys):
            recovered_flow = "find"
        elif user_data.get("is_provider") or not PROVIDER_KEYS.isdisjoint(user_keys):
            recovered_flow = "list"
        else:
            recovered_flow = "find" if user_data.get("is_seeker") else "list"

        user_data["flow"] = recovered_flow
        return recovered_flow

    @staticmethod
    async def _safe_reply_text(
            update: Update,
            context: ContextTypes.DEFAULT_TYPE,
            text: str,
            reply_markup: Optional[InlineKeyboardMarkup] = None,
            parse_mode: Optional[str] = "HTML",
            max_retries: int = 2,
    ) -> bool:
        """Sends reply message synchronously over the HTTP pool without task pool starvation."""
        chat_id = update.effective_chat.id if update.effective_chat else None
        if not chat_id:
            return False

        for attempt in range(1, max_retries + 1):
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    reply_markup=reply_markup,
                    parse_mode=parse_mode,
                )
                return True
            except BadRequest as br:
                if "can't parse entities" in str(br).lower():
                    try:
                        await context.bot.send_message(
                            chat_id=chat_id,
                            text=text,
                            reply_markup=reply_markup,
                            parse_mode=None,
                        )
                        return True
                    except Exception:
                        break
                break
            except RetryAfter as ra:
                await asyncio.sleep(ra.retry_after)
            except (TimedOut, NetworkError):
                if attempt == max_retries:
                    break
                await asyncio.sleep(0.1 * attempt)
            except Exception as e:
                logger.error(f"Unexpected error in _safe_reply_text: {e}")
                break

        return False

    async def _safe_edit_message(
            self,
            update: Update,
            context: ContextTypes.DEFAULT_TYPE,
            text: str,
            reply_markup: Optional[InlineKeyboardMarkup] = None,
            parse_mode: Optional[str] = "HTML",
            max_retries: int = 2,
    ) -> bool:
        """Edits active message with fast direct retries and automatic fallback to new message."""
        query = update.callback_query
        if not query:
            return False

        for attempt in range(1, max_retries + 1):
            try:
                await query.edit_message_text(
                    text=text, reply_markup=reply_markup, parse_mode=parse_mode
                )
                return True
            except BadRequest as br:
                err_msg = str(br)
                if "message is not modified" in err_msg.lower():
                    return True
                if "can't parse entities" in err_msg.lower() or "entity" in err_msg.lower():
                    try:
                        await query.edit_message_text(
                            text=text, reply_markup=reply_markup, parse_mode=None
                        )
                        return True
                    except Exception:
                        break
                break
            except RetryAfter as ra:
                await asyncio.sleep(ra.retry_after)
            except (TimedOut, NetworkError):
                if attempt == max_retries:
                    break
                await asyncio.sleep(0.1 * attempt)
            except Exception as e:
                logger.error(f"Unexpected error in _safe_edit_message: {e}")
                break

        return await self._safe_reply_text(
            update, context, text, reply_markup=reply_markup, parse_mode=parse_mode
        )

    async def start_list_vehicle(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Entry Point: Initializes provider registration workflow with non-blocking callback ack."""
        # Answer callback concurrently in background if triggered via button
        if update.callback_query:
            asyncio.create_task(update.callback_query.answer())

        lang = context.user_data.get("lang", "en") if context.user_data else "en"
        context.user_data.clear()
        context.user_data.update({
            "lang": lang,
            "flow": "list",
            "is_provider": True,
            "is_seeker": False
        })

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(self._tx(context, "vehicle.btn.sell_vehicle"),
                                     callback_data="list_trans_sell"),
                InlineKeyboardButton(self._tx(context, "vehicle.btn.rent_out"), callback_data="list_trans_rent")
            ],
            [InlineKeyboardButton(self._tx(context, "vehicle.btn.return_main"), callback_data="cancel_to_main")]
        ])

        if update.callback_query:
            await self._safe_edit_message(update, context, self._tx(context, "vehicle.list.welcome"),
                                          reply_markup=keyboard)
        else:
            await self._safe_reply_text(update, context, self._tx(context, "vehicle.list.welcome"),
                                        reply_markup=keyboard)

        return self.TRANSACTION_TYPE

    async def start_find_vehicle(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Entry Point: Initializes seeker matching pipeline with non-blocking callback ack."""
        # Answer callback concurrently in background if triggered via button
        if update.callback_query:
            asyncio.create_task(update.callback_query.answer())

        lang = context.user_data.get("lang", "en") if context.user_data else "en"
        context.user_data.clear()
        context.user_data.update({
            "lang": lang,
            "flow": "find",
            "is_seeker": True,
            "is_provider": False
        })

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(self._tx(context, "vehicle.btn.buy_vehicle"), callback_data="find_trans_buy"),
                InlineKeyboardButton(self._tx(context, "vehicle.btn.rent_lease"), callback_data="find_trans_rent")
            ],
            [InlineKeyboardButton(self._tx(context, "vehicle.btn.return_main"), callback_data="cancel_to_main")]
        ])

        if update.callback_query:
            await self._safe_edit_message(update, context, self._tx(context, "vehicle.find.welcome"),
                                          reply_markup=keyboard)
        else:
            await self._safe_reply_text(update, context, self._tx(context, "vehicle.find.welcome"),
                                        reply_markup=keyboard)

        return self.TRANSACTION_TYPE

    # ==========================================
    # PIPELINE STEP HANDLERS
    # ==========================================
    async def handle_transaction_type(
            self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        query = update.callback_query
        if query:
            try:
                await query.answer()
            except Exception:
                pass

        raw_data = str(query.data) if query and query.data else ""

        if raw_data.startswith("find_"):
            flow = "find"
            context.user_data["is_seeker"] = True
            context.user_data["is_provider"] = False
        elif raw_data.startswith("list_"):
            flow = "list"
            context.user_data["is_provider"] = True
            context.user_data["is_seeker"] = False
        else:
            flow = context.user_data.get(
                "flow",
                self._get_active_flow(context, update)
                if hasattr(self, "_get_active_flow")
                else "list",
            )

        context.user_data["flow"] = flow

        # Fast string prefix removal
        cleaned_tx = (
            raw_data.removeprefix("find_trans_")
            .removeprefix("list_trans_")
            .removeprefix("trans_")
        )
        context.user_data["listing_type"] = cleaned_tx

        keyboard = [
            [
                InlineKeyboardButton(
                    self._tx(context, "vehicle.vtype.sedan"),
                    callback_data="vtype_sedan",
                ),
                InlineKeyboardButton(
                    self._tx(context, "vehicle.vtype.suv"),
                    callback_data="vtype_suv",
                ),
            ],
            [
                InlineKeyboardButton(
                    self._tx(context, "vehicle.vtype.van"),
                    callback_data="vtype_van",
                ),
                InlineKeyboardButton(
                    self._tx(context, "vehicle.vtype.bus"),
                    callback_data="vtype_bus",
                ),
            ],
            [
                InlineKeyboardButton(
                    self._tx(context, "vehicle.vtype.truck"),
                    callback_data="vtype_truck",
                ),
                InlineKeyboardButton(
                    self._tx(context, "vehicle.vtype.moto"),
                    callback_data="vtype_moto",
                ),
            ],
        ]

        prompt_key = (
            "vehicle.list.vtype_prompt"
            if flow == "list"
            else "vehicle.find.vtype_prompt"
        )
        prompt_text = self._tx(context, prompt_key)
        title_text = self._tx(context, "vehicle.vtype.title")
        full_message = f"{title_text}\n\n{prompt_text}"
        clean_html = re.sub(r"\*\*(.*?)\*\*", r"<b>\1</b>", str(full_message))

        # Swift non-blocking dispatch replacing _safe_edit_message
        if query:
            if query.message:
                try:
                    await query.edit_message_text(
                        clean_html,
                        reply_markup=InlineKeyboardMarkup(keyboard),
                        parse_mode="HTML",
                    )
                except Exception:
                    await context.bot.send_message(
                        chat_id=query.message.chat.id,
                        text=clean_html,
                        reply_markup=InlineKeyboardMarkup(keyboard),
                        parse_mode="HTML",
                    )
            elif query.from_user:
                await context.bot.send_message(
                    chat_id=query.from_user.id,
                    text=clean_html,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="HTML",
                )

        return self.VEHICLE_TYPE

    async def handle_vehicle_type(
            self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        query = update.callback_query
        if query:
            try:
                await query.answer()
            except Exception:
                pass

        if query and query.data:
            context.user_data["vehicle_type"] = (
                query.data.split("_", 1)[1] if "_" in query.data else query.data
            )

        # Read flow directly from in-memory user_data to eliminate IO calls
        flow = context.user_data.get("flow", "list")
        prompt_key = (
            "vehicle.list.make_model_prompt"
            if flow == "list"
            else "vehicle.find.make_model_prompt"
        )
        prompt_text = str(self._tx(context, prompt_key))
        clean_html = re.sub(r"\*\*(.*?)\*\*", r"<b>\1</b>", prompt_text)

        # Swift non-blocking dispatch replacing _safe_edit_message
        if query:
            if query.message:
                try:
                    await query.edit_message_text(clean_html, parse_mode="HTML")
                except Exception:
                    await context.bot.send_message(
                        chat_id=query.message.chat.id,
                        text=clean_html,
                        parse_mode="HTML",
                    )
            elif query.from_user:
                await context.bot.send_message(
                    chat_id=query.from_user.id,
                    text=clean_html,
                    parse_mode="HTML",
                )

        return self.MAKE_MODEL_YEAR

    async def handle_make_model_year(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        if not update.message or not update.message.text:
            return self.MAKE_MODEL_YEAR

        text_input = update.message.text.strip()
        if len(text_input) < 3:
            err_msg = self._tx(context, "vehicle.error.input_too_short")
            # Non-blocking error response dispatch
            asyncio.create_task(
                self._safe_reply_text(update, context, err_msg, parse_mode="HTML")
            )
            return self.MAKE_MODEL_YEAR

        # Immediate state update
        context.user_data['make_model_year'] = text_input
        flow = self._get_active_flow(context, update)

        prompt_key = "vehicle.list.condition_prompt" if flow == 'list' else "vehicle.find.condition_prompt"
        prompt_text = self._tx(context, prompt_key)

        # Fast formatting conversion
        if prompt_text and "**" in prompt_text:
            prompt_text = re.sub(r"\*\*(.*?)\*\*", r"<b>\1</b>", prompt_text)

        # Non-blocking async dispatch eliminates Telegram API network latency from the handler return path
        asyncio.create_task(
            self._safe_reply_text(update, context, prompt_text, parse_mode="HTML")
        )

        return self.CONDITION_YEARS

    async def handle_condition_years(
            self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        """Handles condition/years input with non-blocking async dispatch and O(1) state assignment."""
        msg = update.message
        if not msg or not msg.text:
            return self.CONDITION_YEARS

        raw_val = msg.text.strip()

        # Fast numeric input validation
        if not raw_val.isdigit():
            err_msg = self._tx(context, "vehicle.error.invalid_format_number")
            asyncio.create_task(
                self._safe_reply_text(update, context, err_msg, parse_mode="HTML")
            )
            return self.CONDITION_YEARS

        years_val = int(raw_val)

        # In-memory state assignment & active flow recovery
        user_data = context.user_data if context.user_data is not None else {}
        flow = user_data.get("flow") or self._get_active_flow(context, update)

        if flow == "list":
            user_data["years_vehicle_used"] = years_val
            prompt_key = "vehicle.list.fuel_prompt"
        else:
            user_data["min_used_years"] = years_val
            prompt_key = "vehicle.find.fuel_prompt"

        # Single-pass inline keyboard construction
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(self._tx(context, key), callback_data=cb)
                for cb, key in row
            ]
            for row in FUEL_LAYOUT
        ])

        title_text = self._tx(context, "vehicle.fuel.title")
        prompt_text = self._tx(context, prompt_key)
        full_text_html = f"<b>{title_text}</b>\n\n{prompt_text}"

        # Non-blocking background task prevents outbound network latency from delaying conversation state
        asyncio.create_task(
            self._safe_reply_text(
                update,
                context,
                full_text_html,
                reply_markup=keyboard,
                parse_mode="HTML",
            )
        )

        return self.FUEL_TYPE

    async def handle_fuel_type(
            self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        query = update.callback_query

        # 1. Acknowledge callback immediately to eliminate loading spinners and gateway timeouts
        if query:
            try:
                await query.answer()
            except Exception:
                pass

        # 2. Extract selected fuel code safely
        callback_data = query.data if query and query.data else ""
        selected_fuel = (
            callback_data.split("_", 1)[1] if "_" in callback_data else callback_data
        )

        flow = (
            self._get_active_flow(context, update)
            if hasattr(self, "_get_active_flow")
            else "list"
        )

        if flow == "list":
            context.user_data["fuel_type"] = selected_fuel
            prompt_key = "vehicle.list.features_prompt"
        else:
            context.user_data["acceptable_fuel_type"] = selected_fuel
            prompt_key = "vehicle.find.features_prompt"

        # 3. Retrieve localized prompt with safe fallback
        raw_prompt = (
            self._tx(context, prompt_key)
            if hasattr(self, "_tx")
            else "Please enter or describe additional features and specifications:"
        )

        # 4. Convert legacy Markdown **bold** tags to HTML <b> tags to avoid parsing errors
        clean_text_html = re.sub(r"\*\*(.*?)\*\*", r"<b>\1</b>", str(raw_prompt))

        # 5. Update message with HTML parse mode, falling back to context.bot.send_message if edit fails
        if query:
            if query.message:
                try:
                    await query.edit_message_text(clean_text_html, parse_mode="HTML")
                except Exception:
                    await context.bot.send_message(
                        chat_id=query.message.chat.id,
                        text=clean_text_html,
                        parse_mode="HTML",
                    )
            elif query.from_user:
                await context.bot.send_message(
                    chat_id=query.from_user.id,
                    text=clean_text_html,
                    parse_mode="HTML",
                )

        return self.FEATURE_SPECS

    async def handle_feature_specs(
            self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        """Handles Step 6: Core equipment specs with low-latency direct async delivery."""
        if not update.message or not update.message.text:
            err_msg = "⚠️ Please enter features as text (e.g. 'AC, Automatic') or type 'none'."
            await self._safe_reply_text(update, context, err_msg)
            return self.FEATURE_SPECS

        try:
            text = update.message.text.strip()
            flow = self._get_active_flow(context, update)
            none_word = self._tx(context, "vehicle.keyword.none").lower()

            parsed_features = (
                [f.strip() for f in text.split(",") if f.strip()]
                if text.lower() != none_word
                else []
            )

            if flow == "list":
                context.user_data["features"] = parsed_features
                prompt_key = "vehicle.list.location_prompt"
            else:
                context.user_data["must_have_features"] = parsed_features
                prompt_key = "vehicle.find.location_prompt"

            prompt_text = self._tx(context, prompt_key)
            if prompt_text and "**" in prompt_text:
                prompt_text = BOLD_MARKDOWN_RE.sub(r"<b>\1</b>", prompt_text)

            await self._safe_reply_text(update, context, prompt_text, parse_mode="HTML")
            return self.LOCATION_DATA

        except Exception as e:
            logger.error(f"Unhandled error in handle_feature_specs: {e}", exc_info=True)
            err_msg = "⚠️ An unexpected error occurred. Please re-enter the features to proceed."
            await self._safe_reply_text(update, context, err_msg)
            return self.FEATURE_SPECS

    async def handle_location_data(
            self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        """Handles Step 7: Location Data with instantaneous state transition and response delivery."""
        msg = update.message
        if not msg or (not msg.text and not msg.location):
            err_msg = "⚠️ Please send a valid text location or share a GPS location pin."
            await self._safe_reply_text(update, context, err_msg)
            return self.LOCATION_DATA

        try:
            if msg.location:
                context.user_data["latitude"] = msg.location.latitude
                context.user_data["longitude"] = msg.location.longitude
                context.user_data["location_text"] = self._tx(
                    context, "vehicle.location.gps_pin"
                )
            else:
                context.user_data["latitude"] = None
                context.user_data["longitude"] = None
                context.user_data["location_text"] = msg.text.strip()

            flow = self._get_active_flow(context, update)
            transaction_type = context.user_data.get("listing_type", "sell")

            if flow == "list":
                prompt_key = (
                    "vehicle.list.price_sell_prompt"
                    if transaction_type in ("sell", "buy")
                    else "vehicle.list.price_rent_prompt"
                )
            else:
                prompt_key = (
                    "vehicle.find.budget_buy_prompt"
                    if transaction_type in ("buy", "sell")
                    else "vehicle.find.budget_rent_prompt"
                )

            raw_prompt = self._tx(context, prompt_key)
            if raw_prompt and "**" in raw_prompt:
                raw_prompt = BOLD_MARKDOWN_RE.sub(r"<b>\1</b>", raw_prompt)

            await self._safe_reply_text(update, context, raw_prompt, parse_mode="HTML")
            return self.PRICE_BUDGET

        except Exception as e:
            logger.error(f"Unhandled error in handle_location_data: {e}", exc_info=True)
            err_msg = "⚠️ An error occurred. Please re-type your location."
            await self._safe_reply_text(update, context, err_msg)
            return self.LOCATION_DATA

    async def handle_price_budget(
            self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        """Parses and stores price/budget input flexibly across ETB and USD with swift non-blocking dispatch."""
        if not update or not update.message:
            return self.PRICE_BUDGET

        if not update.message.text:
            err_msg = self._tx(context, "vehicle.error.invalid_price") if hasattr(self,
                                                                                  "_tx") else "⚠️ Invalid price format."
            await update.message.reply_text(err_msg, parse_mode="HTML")
            return self.PRICE_BUDGET

        try:
            text = update.message.text.strip()
            upper_text = text.upper()

            # 1. Fast Currency Detection
            if "$" in text or "USD" in upper_text:
                currency = "USD"
            elif "ብር" in text or any(kw in upper_text for kw in ("ETB", "BIRR", "QARSHII")):
                currency = "ETB"
            else:
                currency = context.user_data.get("currency", "ETB")

            # 2. Fast Number Sanitization
            cleaned_num = NUMERIC_CLEAN_RE.sub("", text.replace(",", ""))

            # Fast fix for multiple decimal points (e.g. 1.000.00 -> 1000.00)
            if cleaned_num.count(".") > 1:
                first_dot = cleaned_num.find(".")
                cleaned_num = cleaned_num[:first_dot + 1] + cleaned_num[first_dot + 1:].replace(".", "")

            if not cleaned_num or cleaned_num == ".":
                raise ValueError("No valid digits found")

            amount = float(cleaned_num)
            if amount <= 0:
                raise ValueError("Amount must be greater than zero")

            # 3. State Update
            flow = self._get_active_flow(context, update) if hasattr(self, "_get_active_flow") else "list"
            context.user_data["currency"] = currency

            if flow == "list":
                context.user_data["price"] = amount
                prompt_key = "vehicle.list.photo_prompt"
                next_state = self.PHOTO_UPLOAD
            else:
                context.user_data["max_budget"] = amount
                prompt_key = "vehicle.find.contact_prompt"
                next_state = self.CONTACT_INFO

            prompt_text = self._tx(context, prompt_key) if hasattr(self, "_tx") else "Please proceed to next step."

            # Convert Markdown syntax **bold** into native HTML <b>bold</b> for faster processing
            prompt_html = prompt_text.replace("**", "<b>", 1).replace("**", "</b>",
                                                                      1) if "**" in prompt_text else prompt_text

            # 4. Swift Direct Async Send (Eliminates _safe_reply_text retry delays)
            await update.message.reply_text(prompt_html, parse_mode="HTML")
            return next_state

        except ValueError:
            err_msg = self._tx(context, "vehicle.error.invalid_price") if hasattr(self,
                                                                                  "_tx") else "⚠️ Please enter a " \
                                                                                              "valid number."
            await update.message.reply_text(err_msg, parse_mode="HTML")
            return self.PRICE_BUDGET

        except Exception as e:
            logger.error(f"Unhandled exception in handle_price_budget: {e}", exc_info=True)
            await update.message.reply_text(
                "⚠️ An unexpected error occurred. Please re-enter the amount.",
                parse_mode="HTML",
            )
            return self.PRICE_BUDGET

    async def handle_photo_upload(
            self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        """Handles asset photo upload buffer securely with swift non-blocking dispatch."""
        if not update or not update.message:
            return self.PHOTO_UPLOAD

        msg = update.message

        try:
            # 1. Handle Text / Skip Flow Instantly
            if msg.text:
                text = msg.text.strip().lower()
                if text == "skip":
                    context.user_data["asset_photo_url"] = None
                    prompt_text = (
                        self._tx(context, "vehicle.list.contact_prompt")
                        if hasattr(self, "_tx")
                        else "📸 Photo skipped. Please enter your contact phone number:"
                    )
                    clean_html = re.sub(
                        r"\*\*(.*?)\*\*", r"<b>\1</b>", str(prompt_text)
                    )
                    await msg.reply_text(clean_html, parse_mode="HTML")
                    return self.CONTACT_INFO

                await msg.reply_text(
                    "⚠️ Please upload a photo/image or type 'skip'.",
                    parse_mode="HTML",
                )
                return self.PHOTO_UPLOAD

            # 2. Extract Media Object & Extension
            media_obj = None
            file_ext = ".jpg"

            if msg.photo:
                media_obj = msg.photo[-1]
            elif msg.document:
                doc = msg.document
                mime_type = doc.mime_type or ""
                file_name = (doc.file_name or "").lower()

                if mime_type.startswith("image/") or file_name.endswith(
                        (".webp", ".png", ".jpg", ".jpeg")
                ):
                    media_obj = doc
                    if "." in file_name:
                        file_ext = f".{file_name.rsplit('.', 1)[-1]}"
                else:
                    await msg.reply_text(
                        "⚠️ Unsupported file type. Please upload a valid image (JPG, PNG, WEBP) or type 'skip'.",
                        parse_mode="HTML",
                    )
                    return self.PHOTO_UPLOAD

            elif msg.sticker:
                media_obj = msg.sticker
                file_ext = ".webm" if msg.sticker.is_video else ".webp"

            if media_obj:
                # Send immediate non-blocking typing/upload status indicator to Telegram client
                try:
                    await context.bot.send_chat_action(
                        chat_id=msg.chat_id, action=ChatAction.UPLOAD_PHOTO
                    )
                except Exception:
                    pass

                # 3. Fast Metadata Retrieval with Tight Timeouts (5s connect / 10s read)
                target_file = None
                for attempt in range(1, 3):
                    try:
                        target_file = await media_obj.get_file(
                            read_timeout=10.0, connect_timeout=5.0
                        )
                        break
                    except (TimedOut, TelegramError) as err:
                        if attempt == 2:
                            raise err
                        await asyncio.sleep(0.5)

                # 4. Fast Byte Download
                file_bytes = None
                if target_file:
                    for dl_attempt in range(1, 3):
                        try:
                            file_bytes = await target_file.download_as_bytearray(
                                read_timeout=15.0
                            )
                            break
                        except (TimedOut, TelegramError) as dl_err:
                            if dl_attempt == 2:
                                raise dl_err
                            await asyncio.sleep(0.5)

                # 5. S3 Upload & State Update
                filename = f"vehicles/{uuid.uuid4().hex[:12]}{file_ext}"
                s3_url = None
                if (
                        file_bytes
                        and hasattr(self, "storage")
                        and hasattr(self.storage, "upload_buffer")
                ):
                    s3_url = await self.storage.upload_buffer(file_bytes, filename)

                context.user_data["asset_photo_url"] = s3_url

                # 6. Single Final Response Dispatch
                prompt_text = (
                    self._tx(context, "vehicle.list.contact_prompt")
                    if hasattr(self, "_tx")
                    else "✅ Photo uploaded! Please enter your contact phone number:"
                )
                clean_html = re.sub(
                    r"\*\*(.*?)\*\*", r"<b>\1</b>", str(prompt_text)
                )
                await msg.reply_text(clean_html, parse_mode="HTML")
                return self.CONTACT_INFO

            await msg.reply_text(
                "⚠️ Unsupported input. Please upload a standard photo, image document, or type 'skip'.",
                parse_mode="HTML",
            )
            return self.PHOTO_UPLOAD

        except Exception as file_err:
            logger.error(
                f"Failed to process media or file upload: {file_err}", exc_info=True
            )
            context.user_data["asset_photo_url"] = None
            err_msg = (
                "⚠️ Photo processing encountered an issue. Proceeding without photo.\n\n"
                "Please enter your contact phone number:"
            )
            await msg.reply_text(err_msg, parse_mode="HTML")
            return self.CONTACT_INFO

    # ==========================================
    # TERMINAL PERSISTENCE & MATCH ENGINE
    # ==========================================
    async def handle_contact_info(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        context.user_data['contact_phone'] = update.message.text.strip()
        user = update.effective_user
        user_id = str(user.id)
        user_lang = context.user_data.get('lang', 'en')

        # Immediate visual indicator (typing action + instant status message)
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)

        processing_msg = await update.message.reply_text(
            self._tx(context, "vehicle.sync.fleet_records") or "⚙️ Processing your request..."
        )

        try:
            # Defensive Data Normalization
            for num_key in ['price', 'max_budget']:
                if num_key in context.user_data:
                    try:
                        context.user_data[num_key] = float(context.user_data[num_key])
                    except (ValueError, TypeError):
                        context.user_data[num_key] = 0.0

            for int_key in ['years_vehicle_used', 'max_years_old']:
                if int_key in context.user_data:
                    try:
                        context.user_data[int_key] = int(context.user_data[int_key])
                    except (ValueError, TypeError):
                        context.user_data[int_key] = 0

            # Helper Sanitizers
            def contact_safe_float(saf_float_val, default=0.0):
                try:
                    return float(saf_float_val) if saf_float_val is not None else default
                except (ValueError, TypeError):
                    return default

            # Global Lexicon & Strict Database Category Mapping
            REGIONAL_VEHICLE_LEXICON = {
                "makiinaa": "Car", "makina": "Car", "baajajii": "Bajaj", "bajaj": "Bajaj",
                "motora": "Motorcycle", "motorcycle": "Motorcycle", "bus": "Bus", "otobusii": "Bus",
                "lorii": "Truck", "loli": "Truck",
                "finfinnee": "Addis Ababa", "adaamaa": "Adama", "dirree dawaa": "Dire Dawa",
                "jimmaa": "Jimma", "awaasaa": "Hawassa"
            }

            VEHICLE_CATEGORY_MAP = {
                "sedan": "sedan", "car": "sedan", "makiinaa": "sedan", "makina": "sedan",
                "suv": "suv", "offroad": "suv",
                "van": "van/minivan", "minivan": "van/minivan", "van/minivan": "van/minivan",
                "bus": "bus/minibus", "minibus": "bus/minibus", "bus/minibus": "bus/minibus",
                "otobusii": "bus/minibus",
                "truck": "truck", "lorii": "truck", "loli": "truck",
                "moto": "moto", "motorcycle": "moto", "motora": "moto", "bajaj": "moto", "baajajii": "moto"
            }

            # Common User & Context Attributes
            user_first_name = getattr(user, "first_name", "") or ""
            user_last_name = getattr(user, "last_name", "") or ""
            full_name = f"{user_first_name} {user_last_name}".strip() or "Valued User"
            phone = context.user_data.get("contact_phone") or getattr(user, "phone_number", None) or "N/A"

            raw_lat = context.user_data.get("latitude")
            raw_lng = context.user_data.get("longitude")

            # Robust Fallback Retrieval for Consistent Location & Currency
            location_text = safe_str(
                context.user_data.get("location_text") or
                context.user_data.get("location") or
                context.user_data.get("origin"),
                "Addis Ababa"
            )
            currency = safe_str(context.user_data.get("currency"), "ETB").upper()

            # Enforce fuel_type not to remain empty when valid context data is present
            fuel_type = safe_str(
                context.user_data.get("fuel_type") or
                context.user_data.get("acceptable_fuel_type") or
                context.user_data.get("fuel"),
                "Not Specified"
            )
            context.user_data['fuel_type'] = fuel_type

            # Unified Transaction Type Normalization
            raw_tx_type = safe_str(
                context.user_data.get("listing_type") or
                context.user_data.get("transaction_type"),
                "rent"
            ).lower()
            normalized_tx_type = "sell" if raw_tx_type in ["sell", "sale", "buy"] else "rental"
            context.user_data['transaction_type'] = normalized_tx_type
            context.user_data['listing_type'] = normalized_tx_type

            # Extract and Normalize Vehicle Type & Category
            raw_v_type = (
                    context.user_data.get("vehicle_type") or
                    context.user_data.get("property_use") or
                    context.user_data.get("category") or
                    "Sedan"
            )
            is_lexicon_match = False
            resolved_vehicle_type = "Sedan"
            if raw_v_type and isinstance(raw_v_type, str):
                cleaned_lower = raw_v_type.strip().lower()
                if cleaned_lower in REGIONAL_VEHICLE_LEXICON:
                    resolved_vehicle_type = REGIONAL_VEHICLE_LEXICON[cleaned_lower]
                    is_lexicon_match = True
                else:
                    resolved_vehicle_type = raw_v_type.strip()

            v_type_detail = safe_str(resolved_vehicle_type, "Sedan")
            context.user_data['vehicle_type'] = v_type_detail
            cleaned_vtype_key = v_type_detail.lower()

            normalized_property_use = VEHICLE_CATEGORY_MAP.get(
                cleaned_vtype_key,
                cleaned_vtype_key if cleaned_vtype_key in VEHICLE_CATEGORY_MAP.values() else "sedan"
            )
            context.user_data['property_use'] = normalized_property_use

            raw_m_year = (
                    context.user_data.get("make_model_year") or
                    context.user_data.get("model") or
                    context.user_data.get("brand")
            )
            make_model_year = safe_str(raw_m_year, "Any")
            context.user_data['make_model_year'] = make_model_year

            # SMART FLOW RECOVERY
            flow = context.user_data.get('flow')
            if not flow:
                if 'listing_data' in context.user_data or 'quantity' in context.user_data or 'price' in \
                        context.user_data:
                    flow = 'list'
                    context.user_data['flow'] = 'list'
                    logger.warning(
                        f"⚠️ Auto-recovered missing 'flow' as 'list' for user {user_id} from session data.")
                elif 'max_budget' in context.user_data or context.user_data.get('property_use'):
                    flow = 'find'
                    context.user_data['flow'] = 'find'
                    logger.warning(
                        f"⚠️ Auto-recovered missing 'flow' as 'find' for user {user_id} from session data.")

            # ----------------------------------------------------
            # PROVIDER LISTING PIPELINE
            # ----------------------------------------------------
            if flow == "list":
                try:
                    new_processing_text = self._tx(context,
                                                   "vehicle.sync.fleet_records") or "⚙️ Daataa walsimsiisaa jira..."

                    # ✅ Defensive check to prevent editing with identical content
                    if processing_msg and processing_msg.text != new_processing_text:
                        await processing_msg.edit_text(new_processing_text)

                except TelegramError as edit_err:
                    # Silently ignore harmless 'Message is not modified' errors, log others
                    if "Message is not modified" not in str(edit_err):
                        logger.warning(
                            f"Could not update provider processing message for user {user_id}: {edit_err}")

                await self.db.ensure_user_exists(int(user_id), full_name=full_name, phone_number=phone)

                quantity = int(context.user_data.get("quantity", 1))
                raw_price = (
                        context.user_data.get("price") or
                        context.user_data.get("hourly_rate") or
                        context.user_data.get("rate")
                )
                price = contact_safe_float(raw_price, 0.0)

                raw_features = context.user_data.get("features", [])
                features_str = ", ".join(raw_features) if isinstance(raw_features, list) else str(
                    raw_features or ""
                )

                # Explicitly extract years_vehicle_used stored during handle_condition_years
                years_used = int(context.user_data.get("years_vehicle_used", 0))

                listing_data = {
                    "provider_id": int(user_id),
                    "vertical_type": "vehicle",
                    "transaction_type": normalized_tx_type,
                    "property_use": normalized_property_use,
                    "vehicle_type": v_type_detail,
                    "make_model_year": make_model_year,
                    "fuel_type": fuel_type,
                    "years_vehicle_used": years_used,  # Explicitly included here
                    "location_text": location_text,
                    "latitude": contact_safe_float(raw_lat, 0.0),
                    "longitude": contact_safe_float(raw_lng, 0.0),
                    "price": price,
                    "currency": currency,
                    "amenities": f"Model: {make_model_year}, Years Used: {years_used} yrs, Fuel: {fuel_type}, "
                                 f"Features: {features_str}",
                    "photo_url": safe_str(
                        context.user_data.get("asset_photo_url") or context.user_data.get("photo_url"), ""
                    ),
                    "contact_phone": phone,
                    "status": "active",
                    "bulk_quantity": quantity,
                }

                context.user_data['price'] = listing_data['price']

                # INBOUND AI PARSING PIPELINE (Reduced timeout to 7.0s for swift responses)
                if hasattr(self, 'ai') and hasattr(self.ai, 'parse_listing_to_english'):
                    try:
                        english_parsed_data = await asyncio.wait_for(
                            self.ai.parse_listing_to_english(listing_data), timeout=7.0
                        )

                        if isinstance(english_parsed_data, str):
                            match_json = re.search(r'\{.*}', english_parsed_data, re.DOTALL)
                            if match_json:
                                try:
                                    english_parsed_data = json.loads(match_json.group(0))
                                except json.JSONDecodeError:
                                    pass

                        if english_parsed_data and isinstance(english_parsed_data, dict):
                            if title := english_parsed_data.get('title'):
                                if not is_lexicon_match:
                                    listing_data['vehicle_type'] = title
                                    listing_data['property_use'] = VEHICLE_CATEGORY_MAP.get(title.lower(),
                                                                                            listing_data[
                                                                                                'property_use'])

                            if location := english_parsed_data.get('location'):
                                listing_data['location_text'] = location

                            if english_parsed_data.get('vehicle_type'):
                                ext_vtype = str(english_parsed_data['vehicle_type'])
                                listing_data['vehicle_type'] = ext_vtype
                                listing_data['property_use'] = VEHICLE_CATEGORY_MAP.get(ext_vtype.lower(),
                                                                                        listing_data[
                                                                                            'property_use'])

                            root_model = english_parsed_data.get('make_model_year') or english_parsed_data.get(
                                'model')
                            if root_model:
                                listing_data['make_model_year'] = str(root_model)

                            if ext_fuel := english_parsed_data.get('fuel_type'):
                                listing_data['fuel_type'] = str(ext_fuel)

                            specs = english_parsed_data.get('specifications', {})
                            if isinstance(specs, dict) and specs:
                                if extracted_vtype := specs.get('vehicle_type') or specs.get('type') or specs.get(
                                        'category'):
                                    ext_vtype_str = str(extracted_vtype).strip().lower()
                                    listing_data['vehicle_type'] = ext_vtype_str
                                    listing_data['property_use'] = VEHICLE_CATEGORY_MAP.get(ext_vtype_str,
                                                                                            listing_data[
                                                                                                'property_use'])

                                if extracted_model := specs.get('make_model_year') or specs.get(
                                        'model') or specs.get('make'):
                                    listing_data['make_model_year'] = str(extracted_model)

                                if extracted_fuel := specs.get('fuel_type'):
                                    listing_data['fuel_type'] = str(extracted_fuel)

                            desc = str(english_parsed_data.get('description', '')).strip()
                            if desc or specs:
                                listing_data['amenities'] = f"{desc} | Extracted Specs: {specs}".strip(" |")

                    except Exception as ai_err:
                        logger.warning(f"AI parsing mapping skipped: {ai_err}")

                # CONCURRENT TRANSLATION SAFEGUARD
                try:
                    fields_to_check = ['vehicle_type', 'location_text', 'amenities', 'fuel_type']

                    def safe_translate_sync(text: str) -> str:
                        if not text:
                            return text
                        cleaned_lower_lexicon = text.strip().lower()
                        if cleaned_lower_lexicon in REGIONAL_VEHICLE_LEXICON:
                            return REGIONAL_VEHICLE_LEXICON[cleaned_lower_lexicon]

                        has_non_ascii = any(ord(char) > 127 for char in text)
                        if not has_non_ascii:
                            return text

                        try:
                            lang_translator = GoogleTranslator(source='auto', target='en')
                            translation_result = lang_translator.translate(text)
                            if not translation_result or "Error" in translation_result:
                                return text
                            return translation_result.split('(')[0].strip()
                        except Exception as transErr:
                            logger.error(f"Translation fallback failure for '{text}': {transErr}")
                            return text

                    def translate_mixed_string(text: str) -> str:
                        if not text:
                            return text
                        if '|' in text:
                            return ' | '.join([safe_translate_sync(p.strip()) for p in text.split('|')])
                        return safe_translate_sync(text)

                    translation_tasks = []
                    fields_queued = []

                    for field in fields_to_check:
                        val = listing_data.get(field)
                        if val and isinstance(val, str):
                            fields_queued.append(field)
                            translation_tasks.append(asyncio.to_thread(translate_mixed_string, val))

                    if translation_tasks:
                        results = await asyncio.gather(*translation_tasks, return_exceptions=True)
                        for field, res in zip(fields_queued, results):
                            if isinstance(res, Exception):
                                continue
                            if res:
                                listing_data[field] = str(res)
                                if field == 'vehicle_type':
                                    listing_data['property_use'] = VEHICLE_CATEGORY_MAP.get(
                                        str(res).strip().lower(),
                                        listing_data['property_use'])

                except Exception as safeguard_err:
                    logger.warning(f"Translation safeguard skipped: {safeguard_err}")

                # DATABASE INSERTION & TRIGGER WITH TIMEOUT SAFETY
                listing_record: dict | None = None
                if hasattr(self, 'db'):
                    try:
                        async def execute_listing_flow():
                            nonlocal listing_record
                            for _ in range(quantity):
                                if hasattr(self.db, 'create_listing'):
                                    listing_record = await self.db.create_listing(listing_data)
                                else:
                                    listing_record = await self.db.create_provider_listing(
                                        "provider_listings", int(user_id), listing_data
                                    )

                                if not listing_record:
                                    raise Exception("Supabase create_listing failed to return a record.")

                            assert listing_record is not None

                            if hasattr(self, 'matching') and hasattr(self.matching, 'run_provider_matching'):
                                await self.matching.run_provider_matching(listing_record.get('id'), "vehicle")

                        await asyncio.wait_for(execute_listing_flow(), timeout=10.0)

                    except asyncio.TimeoutError:
                        logger.error(f"Database timeout while creating vehicle listing for user {user_id}")
                        try:
                            await processing_msg.edit_text("⚠️ Database operation timed out. Please try again.")
                        except Exception:
                            await update.message.reply_text("⚠️ Database operation timed out. Please try again.")
                        context.user_data.clear()
                        return await self.route_to_main_menu(update, context)
                    except Exception as db_exc:
                        logger.error(f"Database insertion error for user {user_id}: {db_exc}", exc_info=True)
                        try:
                            await processing_msg.edit_text(
                                "⚠️ An error occurred while saving your listing. Please try again.")
                        except Exception:
                            await update.message.reply_text(
                                "⚠️ An error occurred while saving your listing. Please try again.")
                        context.user_data.clear()
                        return await self.route_to_main_menu(update, context)

                try:
                    await processing_msg.delete()
                except TelegramError as del_err:
                    logger.warning(f"Could not delete provider processing message for user {user_id}: {del_err}")

                if listing_record:
                    assert isinstance(listing_record, dict)
                    return await self.trigger_reverse_matching(update, context, listing_record)

                success_msg = self._tx(
                    context, "vehicle.list.success", year_model=listing_data.get("make_model_year", "Vehicle")
                ) or f"✅ {quantity} Vehicle Successfully Registered!"

                await update.message.reply_text(success_msg, parse_mode="HTML")
                return await self.route_to_main_menu(update, context)

            # ----------------------------------------------------
            # SEEKER LIVE MATCHING PIPELINE
            # ----------------------------------------------------
            elif flow == 'find':
                # IMMEDIATE USER NOTIFICATION: Turn status message into processing/scanning mode right away!
                try:
                    await processing_msg.edit_text(
                        self._tx(context, "vehicle.find.interrogating") or "🔍 Konkolaataa walsimatu barbaadaa jira..."
                    )
                except TelegramError as edit_err:
                    logger.warning(f"Could not update status message for seeker {user_id}: {edit_err}")

                candidates = []
                matches = []

                try:
                    async def execute_seeker_flow():
                        nonlocal candidates, matches

                        # Explicitly extract min_used_years stored during handle_condition_years
                        min_used_years = int(context.user_data.get('min_used_years', 0))

                        if hasattr(self.db, 'create_request'):
                            req_payload = {
                                "seeker_id": int(user_id),
                                "vertical_type": "vehicle",
                                "transaction_type": normalized_tx_type,
                                "property_use": normalized_property_use,
                                "vehicle_type": v_type_detail,
                                "make_model_year": make_model_year,
                                "fuel_type": fuel_type,
                                "min_used_years": min_used_years,  # Included explicitly in database request payload
                                "max_budget": contact_safe_float(context.user_data.get('max_budget', 0.0)),
                                "currency": currency,
                                "location_text": location_text,
                                "latitude": contact_safe_float(raw_lat, 0.0),
                                "longitude": contact_safe_float(raw_lng, 0.0),
                                "contact_phone": phone,
                                "status": "active"
                            }
                            await self.db.create_request(req_payload)

                        candidates = await self.db.fetch_vehicle_candidates(
                            listing_type=normalized_tx_type,
                            vehicle_type=normalized_property_use
                        )

                        if not candidates and hasattr(self.db, 'fetch_vehicle_candidates'):
                            candidates = await self.db.fetch_vehicle_candidates(
                                listing_type=normalized_tx_type, vehicle_type=None
                            )

                        prefs = {
                            "vertical_type": "vehicle",
                            "transaction_type": normalized_tx_type,
                            "vehicle_type": v_type_detail,
                            "property_use": normalized_property_use,
                            "make_model_year": make_model_year,
                            "fuel_type": fuel_type,
                            "min_used_years": min_used_years,  # Included explicitly for matching evaluation engine
                            "max_budget": contact_safe_float(context.user_data.get('max_budget', 0.0)),
                            "currency": currency,
                            "location_text": location_text,
                            "latitude": contact_safe_float(raw_lat, 0.0),
                            "longitude": contact_safe_float(raw_lng, 0.0),
                            "contact_phone": phone
                        }

                        raw_must_haves = (
                                context.user_data.get('must_have_features') or
                                context.user_data.get('required_amenities') or
                                context.user_data.get('amenities') or []
                        )
                        if isinstance(raw_must_haves, str):
                            prefs['required_amenities'] = [f.strip() for f in raw_must_haves.split(',') if f.strip()]
                        else:
                            prefs['required_amenities'] = raw_must_haves

                        for c in candidates:
                            if not c.get('vertical_type'):
                                c['vertical_type'] = 'vehicle'
                            if not c.get('transaction_type'):
                                c['transaction_type'] = normalized_tx_type
                            c['price'] = contact_safe_float(c.get('price', 0.0))
                            c['currency'] = str(c.get('currency') or 'ETB').strip().upper()
                            if not c.get('fuel_type'):
                                c['fuel_type'] = fuel_type

                            raw_c_features = c.get('features') or c.get('amenities', '')
                            if isinstance(raw_c_features, str):
                                c['amenities'] = raw_c_features
                            elif isinstance(raw_c_features, list):
                                c['amenities'] = ", ".join([str(f).strip() for f in raw_c_features if f])

                        if hasattr(self, 'matching') and hasattr(self.matching, 'evaluate_matches'):
                            matches = await self.matching.evaluate_matches('vehicle', prefs, candidates)

                        if not matches and candidates and hasattr(self, 'matching') and hasattr(self.matching,
                                                                                                'evaluate_matches'):
                            prefs['make_model_year'] = 'Any'
                            prefs['vehicle_type'] = normalized_property_use
                            prefs['allow_semantic_fallback'] = True

                            broad_candidates = await self.db.fetch_vehicle_candidates(
                                listing_type=normalized_tx_type, vehicle_type=None
                            )
                            if broad_candidates:
                                candidates = broad_candidates
                                for c in candidates:
                                    if not c.get('vertical_type'):
                                        c['vertical_type'] = 'vehicle'
                                    if not c.get('transaction_type'):
                                        c['transaction_type'] = normalized_tx_type
                                    c['price'] = contact_safe_float(c.get('price', 0.0))
                                    c['currency'] = str(c.get('currency') or 'ETB').strip().upper()
                                    if not c.get('fuel_type'):
                                        c['fuel_type'] = fuel_type

                                matches = await self.matching.evaluate_matches('vehicle', prefs, candidates)

                    await asyncio.wait_for(execute_seeker_flow(), timeout=12.0)

                except asyncio.TimeoutError:
                    logger.error(f"Search matching timeout for vehicle seeker {user_id}")
                    try:
                        await processing_msg.edit_text(
                            "⚠️ Search and matching operation timed out. Please try again.")
                    except Exception:
                        await update.message.reply_text(
                            "⚠️ Search and matching operation timed out. Please try again.")
                    context.user_data.clear()
                    return await self.route_to_main_menu(update, context)
                except Exception as seeker_exc:
                    logger.error(f"Error in seeker database/matching pipeline for user {user_id}: {seeker_exc}",
                                 exc_info=True)
                    try:
                        await processing_msg.edit_text(
                            "⚠️ An error occurred while searching for vehicles. Please try again.")
                    except Exception:
                        await update.message.reply_text(
                            "⚠️ An error occurred while searching for vehicles. Please try again.")
                    context.user_data.clear()
                    return await self.route_to_main_menu(update, context)

                if not matches:
                    broadcast_txt = self._tx(context,
                                             "vehicle.find.no_match") or "📢 No suitable vehicle match found. Request " \
                                                                         "" \
                                                                         "broadcasted."
                    try:
                        await processing_msg.edit_text(broadcast_txt)
                    except Exception:
                        await update.message.reply_text(broadcast_txt)
                    context.user_data.clear()
                    return await self.route_to_main_menu(update, context)

                top_matches = matches[:3]
                context.user_data['vehicle_top_matches'] = top_matches

                if user_lang != 'en':
                    localized_matches = []
                    for match in top_matches:
                        try:
                            if hasattr(self.ai, 'translate_listing_to_target'):
                                loc_match = await self.ai.translate_listing_to_target(match, target_lang=user_lang)
                                localized_matches.append(loc_match)
                            else:
                                localized_matches.append(match)
                        except Exception:
                            localized_matches.append(match)
                    top_matches = localized_matches

                keyboard = []
                intro_msg = self._tx(context,
                                     "vehicle.find.top_matches_found") or f"🎯 **Walsimannaa gaarii argineera. Maaloo" \
                                                                          f" filadhaa:**"

                for i, match in enumerate(top_matches, start=1):
                    match_id = str(match.get('id'))
                    make_model = str(match.get('make_model_year', match.get('vehicle_type', 'Vehicle'))).title()
                    price = contact_safe_float(match.get('price', 0.0))
                    currency = str(match.get('currency') or 'ETB').strip().upper()
                    score = int(match.get('_match_score', 95))

                    button_label = f"🚗 #{i}: {make_model} ({price:,.2f} {currency} - {score}%)"
                    keyboard.append(
                        [InlineKeyboardButton(button_label, callback_data=f"select_vehicle_match:{match_id}")])

                keyboard.append([InlineKeyboardButton(
                    self._tx(context, "vehicle.btn.decline_match") or "❌ Hin Barbaadu",
                    callback_data="cancel_vehicle_deal"
                )])

                # Seamlessly replace processing message with top matches inline menu
                try:
                    await processing_msg.edit_text(intro_msg, reply_markup=InlineKeyboardMarkup(keyboard),
                                                   parse_mode="Markdown")
                except TelegramError:
                    try:
                        await processing_msg.delete()
                    except TelegramError:
                        pass
                    await update.message.reply_text(intro_msg, reply_markup=InlineKeyboardMarkup(keyboard),
                                                    parse_mode="Markdown")

                return getattr(self, "AWAITING_MATCH_SELECTION", 99)

            else:
                logger.error(
                    f"CRITICAL: Unknown or missing flow '{flow}' for user {user_id} in handle_contact_info.")
                try:
                    await processing_msg.edit_text(
                        "⚠️ Session flow expired or not recognized. Please use /start to restart.")
                except Exception:
                    await update.message.reply_text(
                        "⚠️ Session flow expired or not recognized. Please use /start to restart.")
                context.user_data.clear()
                return await self.route_to_main_menu(update, context)

        except TimedOut as to_err:
            logger.warning(f"Telegram network timeout in vehicle pipeline for user {user_id}: {to_err}")
            timeout_msg = self._tx(
                context, "vehicle.error.timeout"
            ) or "⚠️ Network timeout while connecting to Telegram. Your request was preserved; please try again."
            try:
                await update.message.reply_text(timeout_msg)
            except Exception:
                pass
            context.user_data.clear()
            return await self.route_to_main_menu(update, context)

        except TelegramError as tg_err:
            logger.error(f"Telegram API error in vehicle pipeline for user {user_id}: {tg_err}", exc_info=True)
            err_msg = self._tx(
                context, "vehicle.error.telegram_api"
            ) or "⚠️ Telegram communication failure occurred. Please try again."
            try:
                await update.message.reply_text(err_msg)
            except Exception:
                pass
            context.user_data.clear()
            return await self.route_to_main_menu(update, context)

        except Exception as e:
            logger.error(f"Vehicle pipeline terminal crash for user {user_id}: {e}", exc_info=True)
            err_msg = self._tx(
                context, "vehicle.error.pipeline_crash"
            ) or "⚠️ An unexpected internal pipeline error occurred."
            try:
                await processing_msg.edit_text(err_msg)
            except Exception:
                try:
                    await update.message.reply_text(err_msg)
                except Exception:
                    pass
            context.user_data.clear()
            return await self.route_to_main_menu(update, context)

    # ==========================================
    # REVERSE MATCHING TRIGGER & NAVIGATION
    # ==========================================
    async def trigger_reverse_matching(
            self, update: Update, context: ContextTypes.DEFAULT_TYPE, listing_record: dict
    ) -> int:
        """Scans active seeker requests against newly created vehicle listing and renders top matches."""
        # Immediate Chat Action
        if update.effective_chat:
            await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)

        lang = str(
            context.user_data.get("language")
            or context.user_data.get("lang")
            or (self._get_user_lang(context) if hasattr(self, "_get_user_lang") else "en")
        ).lower()

        if "am" in lang:
            default_loading = "🔍 <i>ለዝርዝርዎ ንቁ የፈላጊ ጥያቄዎችን በመፈተሽ ላይ...</i>"
        elif "om" in lang:
            default_loading = (
                "🔍 <i>Gaaffiiwwan barbaadamaa tarreeffama keessaniif hojiirra jiran qorachaa jira...</i>"
            )
        else:
            default_loading = "🔍 <i>Scanning active Seeker Requests for your listing...</i>"

        loading_text = (
                           self._tx(context, "msg_scanning_seekers", lang=lang)
                           if hasattr(self, "_tx")
                           else None
                       ) or default_loading

        # Send instant loading indicator
        msg_loading = None
        if update.effective_message:
            msg_loading = await update.effective_message.reply_text(
                loading_text, parse_mode="HTML"
            )

        vertical = str(listing_record.get("vertical_type", "vehicle")).lower()
        listing_type = listing_record.get("transaction_type", "rental")
        listing_record["vertical_type"] = vertical

        offered_mmy = _format_mmy(listing_record, ["make_model_year", "brand", "model"])
        context.user_data["listing_make_model_year"] = offered_mmy
        context.user_data["listing_fuel_type"] = _clean_val(listing_record.get("fuel_type"))

        try:
            res = (
                await self.db.table("seeker_requests")
                .select("*")
                .eq("vertical_type", vertical)
                .eq("status", "active")
                .execute()
            )

            candidates = res.data if res and hasattr(res, "data") and res.data else []
            matches = []

            # 1. DEFENSIVE LISTING NORMALIZATION (INCORPORATING VEHICLE CONDITION / YEARS USED)
            normalized_listing = dict(listing_record)
            normalized_listing["vertical_type"] = vertical
            normalized_listing["transaction_type"] = listing_type
            normalized_listing["price"] = safe_float(normalized_listing.get("price", 0.0))
            normalized_listing["currency"] = str(normalized_listing.get("currency") or "ETB").strip().upper()

            # Explicitly extract and normalize years_vehicle_used for reverse scoring
            raw_years = (
                normalized_listing.get("years_vehicle_used")
                if normalized_listing.get("years_vehicle_used") is not None
                else context.user_data.get("years_vehicle_used", 0)
            )
            try:
                normalized_listing["years_vehicle_used"] = int(raw_years)
            except (ValueError, TypeError):
                normalized_listing["years_vehicle_used"] = 0

            if not normalized_listing.get("fuel_type"):
                normalized_listing["fuel_type"] = context.user_data.get("listing_fuel_type", "")

            raw_amenities = normalized_listing.get("amenities") or normalized_listing.get("features", "")
            if isinstance(raw_amenities, list):
                normalized_listing["amenities"] = ", ".join([str(a).strip() for a in raw_amenities if a])

            for req in candidates:
                if str(req.get("seeker_id")) == str(update.effective_user.id):
                    continue

                req_tx = safe_str(req.get("transaction_type"))
                if not _is_tx_compatible(req_tx, listing_type):
                    continue

                location_text = (
                        req.get("location_text")
                        or req.get("location")
                        or req.get("city")
                        or req.get("region")
                        or ""
                )

                def _first_val(d: dict, *keys, default=""):
                    for k in keys:
                        val = d.get(k)
                        if val:
                            return val
                    return default

                # Parse min_used_years defensively from seeker request
                raw_min_used = req.get("min_used_years")
                try:
                    min_used_years = int(raw_min_used) if raw_min_used is not None else 0
                except (ValueError, TypeError):
                    min_used_years = 0

                seeker_prefs = {
                    **req,
                    "vertical_type": vertical,
                    "transaction_type": req_tx or listing_type,
                    "property_use": _first_val(req, "property_use", "vehicle_type", "category"),
                    "vehicle_type": _first_val(req, "vehicle_type", "property_use", "category"),
                    "category": _first_val(req, "category", "property_use", "vehicle_type"),
                    "make_model_year": _first_val(
                        req, "make_model_year", "preferred_make_model_year", "make_model"
                    ),
                    "preferred_make_model_year": _first_val(
                        req, "preferred_make_model_year", "make_model_year", "make_model"
                    ),
                    "fuel_type": req.get("fuel_type", ""),
                    "min_used_years": min_used_years,  # Included explicitly for condition/age matching
                    "max_budget": safe_float(req.get("max_budget") or req.get("budget", 0.0)),
                    "currency": str(req.get("currency") or "ETB").strip().upper(),
                    "location_text": location_text,
                    "location": location_text,
                    "latitude": safe_float(req.get("latitude", 0.0)),
                    "longitude": safe_float(req.get("longitude", 0.0)),
                    "contact_phone": req.get("contact_phone") or req.get("phone", ""),
                    "required_amenities": _first_val(
                        req, "required_amenities", "must_have_features", "amenities", default=""
                    ),
                }

                # 2. TWO-PASS SCORING WITH SEMANTIC FALLBACK
                try:
                    score = 0.0
                    if self.matching and hasattr(self.matching, "evaluate_matches"):
                        eval_results = await self.matching.evaluate_matches(
                            vertical, seeker_prefs, [normalized_listing]
                        )
                        if eval_results:
                            score = safe_float(eval_results[0].get("_match_score", 0.0))

                        if score == 0.0:
                            relaxed_prefs = dict(seeker_prefs)
                            relaxed_prefs["make_model_year"] = "Any"
                            relaxed_prefs["preferred_make_model_year"] = "Any"
                            relaxed_prefs["allow_semantic_fallback"] = True

                            eval_results_fallback = await self.matching.evaluate_matches(
                                vertical, relaxed_prefs, [normalized_listing]
                            )
                            if eval_results_fallback:
                                score = safe_float(eval_results_fallback[0].get("_match_score", 0.0))

                    if score > 0.0:
                        req_match = dict(req)
                        req_match["_match_score"] = score
                        req_match["id"] = str(req.get("id"))
                        matches.append(req_match)
                except Exception as score_err:
                    logger.error(
                        f"Reverse match scoring failure for seeker request {req.get('id')}: {score_err}"
                    )

            matches.sort(key=lambda x: x.get("_match_score", 0), reverse=True)
            top_matches = matches[:3]

            if not top_matches:
                if "am" in lang:
                    default_no_match = (
                        "✅ <b>ዝርዝርዎ በሥራ ላይ ውሏል!</b>\n\n"
                        "አሁን ምንም ንቁ ፈላጊዎች ከዚህ ዝርዝር ጋር አይዛመዱም። የሚዛመድ ጥያቄ ሲቀርብ እናሳውቅዎታለን።"
                    )
                elif "om" in lang:
                    default_no_match = (
                        "✅ <b>Tarreeffamni Kun Hojiirra Jira!</b>\n\n"
                        "Ammaaf, barbaadtonni konkolaataa kana waliin wal simatan hin jiran. "
                        "Yeroo gaaffiin wal simatu dhiyaatu ibsa isiiniif ni kennina."
                    )
                else:
                    default_no_match = (
                        "✅ <b>Listing is Live!</b>\n\n"
                        "Currently, no active seekers match this exact listing. "
                        "We will notify you when a matching request is submitted."
                    )

                fallback_msg = (
                                   self._tx(context, "msg_no_seeker_matches", lang=lang)
                                   if hasattr(self, "_tx")
                                   else None
                               ) or default_no_match

                if msg_loading:
                    try:
                        await msg_loading.edit_text(fallback_msg, parse_mode="HTML")
                    except Exception as edit_err:
                        logger.warning(f"⚠ Could not edit loading message in-place: {edit_err}")

                if hasattr(self, "show_main_menu"):
                    return await self.show_main_menu(update, context)
                elif hasattr(self, "route_to_main_menu"):
                    return await self.route_to_main_menu(update, context)

                return ConversationHandler.END

            # 3. DYNAMIC AI TRANSLATION FOR TOP MATCHES
            if lang != "en":
                localized_matches = []
                for match in top_matches:
                    try:
                        if hasattr(self, "ai") and hasattr(self.ai, "translate_listing_to_target"):
                            loc_match = await self.ai.translate_listing_to_target(
                                match, target_lang=lang
                            )
                            localized_matches.append(loc_match)
                        else:
                            localized_matches.append(match)
                    except Exception:
                        localized_matches.append(match)
                top_matches = localized_matches

            context.user_data["seeker_top_matches"] = top_matches
            context.user_data["last_created_listing_id"] = str(listing_record.get("id"))
            context.user_data["is_reverse_match"] = True

            keyboard = []
            for i, match in enumerate(top_matches, start=1):
                m_id = str(match.get("id"))
                budget = safe_float(match.get("max_budget", 0.0))
                currency = str(match.get("currency") or "ETB").strip().upper()
                score = int(match.get("_match_score", 95))

                req_mmy = _format_mmy(match)

                # Fixed multi-line f-string syntax errors
                if "am" in lang:
                    default_btn_lbl = f"👤 #{i} {req_mmy} (በጀት: {budget:,.0f} {currency} - {score}%)"
                elif "om" in lang:
                    default_btn_lbl = f"👤 #{i} {req_mmy} (Baajata: {budget:,.0f} {currency} - {score}%)"
                else:
                    default_btn_lbl = f"👤 #{i} {req_mmy} (Budget: {budget:,.0f} {currency} - {score}%)"

                btn_label = (
                                self._tx(
                                    context,
                                    "BTN_SEEKER_MATCH_ITEM",
                                    index=i,
                                    budget=f"{budget:,.2f}",
                                    currency=currency,
                                    score=score,
                                    mmy=req_mmy,
                                    lang=lang,
                                )
                                if hasattr(self, "_tx")
                                else None
                            ) or default_btn_lbl

                keyboard.append(
                    [
                        InlineKeyboardButton(
                            btn_label, callback_data=f"select_seeker_match:{m_id}"
                        )
                    ]
                )

            if "am" in lang:
                default_skip = "⏭️ ዝለል እና ወደ ምናሌ ተመለስ"
            elif "om" in lang:
                default_skip = "⏭ Darbii & Gara Menu Deebi'i"
            else:
                default_skip = "⏭️ Skip & Return to Menu"

            btn_skip = (
                           self._tx(context, "BTN_DECLINE_RETURN", lang=lang)
                           if hasattr(self, "_tx")
                           else None
                       ) or default_skip

            keyboard.append([InlineKeyboardButton(btn_skip, callback_data="cancel_vehicle_deal")])

            def _sanitize_to_html(text: str) -> str:
                if not text:
                    return ""
                return re.sub(r"\*\*(.*?)\*\*", r"<b>\1</b>", text)

            localized_vertical = (
                                     self._tx(context, f"domain_{vertical}", lang=lang)
                                     if hasattr(self, "_tx")
                                     else None
                                 ) or vertical.title()

            raw_title = (
                self._tx(
                    context,
                    "MSG_TOP_VEHICLE_SEEKER_MATCHES",
                    count=len(top_matches),
                    vertical=localized_vertical,
                    lang=lang,
                )
                if hasattr(self, "_tx")
                else None
            )

            if raw_title:
                title_text = _sanitize_to_html(raw_title)
            else:
                count = len(top_matches)
                if "am" in lang:
                    title_text = (
                        f"🎯 <b>ከፍተኛ {count} የ{localized_vertical} ፈላጊዎች ተገኝተዋል!</b>\n"
                        "────────────────\n"
                        "እነዚህ ፈላጊዎች ተሽከርካሪ ይፈልጋሉ። የራስዎን ተሽከርካሪ ለማቅረብ ከታች ካሉት የፈላጊ ጥያቄዎች አንዱን ይምረጡ፡"
                    )
                elif "om" in lang:
                    title_text = (
                        f"🎯 <b>Barbaadtonni {localized_vertical} Hirmaatoon {count} Argamaniiru!</b>\n"
                        "────────────────\n"
                        "Barbaadtonni kunniin konkolaataa barbaadaa jiru. Konkolaataa keessan dhiheessuuf gaaffilee "
                        "barbaaddotaa gadii keessaa filadhaa:"
                    )
                else:
                    title_text = (
                        f"🎯 <b>Top {count} Active {localized_vertical} Seekers Found!</b>\n"
                        "────────────────\n"
                        "These seekers are requesting a vehicle. Select a seeker request below to propose your vehicle:"
                    )

            if msg_loading:
                await msg_loading.edit_text(
                    title_text,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="HTML",
                )

            return getattr(self, "AWAITING_MATCH_SELECTION", self.AWAITING_MATCH_SELECTION)

        except Exception as e:
            logger.error(f"Reverse matching failed: {e}", exc_info=True)
            if msg_loading:
                if "am" in lang:
                    err_fallback = (
                        "✅ <b>ዝርዝርዎ በተሳካ ሁኔታ ተፈጥሯል።</b>\n"
                        "<i>(በስርዓቱ ላይ ባጋጠመ ጊዜያዊ ጫና ምክንያት የኋሊዮሽ ፍለጋው ተዘሏል።)</i>"
                    )
                elif "om" in lang:
                    err_fallback = (
                        "✅ <b>Tarreeffamni milkaa'inaan uumameera.</b>\n"
                        "<i>(Rakkoo yeroof uumameen sakatta’insi duuboo darbeera.)</i>"
                    )
                else:
                    err_fallback = (
                        "✅ <b>Listing created successfully.</b>\n"
                        "<i>(Reverse scan skipped due to temporary system load).</i>"
                    )

                try:
                    await msg_loading.edit_text(err_fallback, parse_mode="HTML")
                except Exception:
                    pass

            if hasattr(self, "show_main_menu"):
                return await self.show_main_menu(update, context)
            elif hasattr(self, "route_to_main_menu"):
                return await self.route_to_main_menu(update, context)

            return ConversationHandler.END

    async def handle_back_to_top_matches(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Allows users to return from an asset card to the top 3 list for both forward and reverse matching with
        full multilingual support."""
        query = update.callback_query
        await query.answer()

        is_reverse = context.user_data.get('is_reverse_match', False)
        lang = str(context.user_data.get('language') or context.user_data.get('lang') or 'en').lower()

        if is_reverse:
            top_matches = context.user_data.get('seeker_top_matches', [])
            if not top_matches:
                return await self.route_to_main_menu(update, context)

            context.user_data.pop('active_vehicle_deal_id', None)

            if 'am' in lang:
                header_text = (
                    "🎯 <b>ዋና ዋና የሚዛመዱ የፈላጊ ጥያቄዎች</b>\n"
                    "────────────────\n"
                    "የንጽጽር ዝርዝሮችን ለማየት እና የዋጋ ማቅረቢያ ለማቅረብ ከታች ያለውን ጥያቄ ይምረጡ:"
                )
                cancel_label = "❌ ፍለጋውን ሰርዝ"
            elif 'om' in lang:
                header_text = (
                    "🎯 <b>Gaaffiiwwan Barbaadamaa Wal Simatan Gubbaa</b>\n"
                    "────────────────\n"
                    "Gamaaggama ilaaluuf fi dhiyaate dhiyeessuuf gadii irraa filadhaa:"
                )
                cancel_label = "❌ Barbaacha Haqaa"
            else:
                header_text = (
                    "🎯 <b>Top Matching Seeker Requests</b>\n"
                    "────────────────\n"
                    "Select a request below to view comparison details and present an offer:"
                )
                cancel_label = "❌ Cancel Search"

            keyboard = []
            for idx, match in enumerate(top_matches, start=1):
                req_mmy = _format_mmy(match)
                budget = safe_float(match.get('max_budget', 0.0))
                currency = str(match.get('currency') or 'ETB').upper()
                score = match.get('_match_score', 95)
                match_id = match.get('id')
                btn_text = f"👤 #{idx} {req_mmy} ({budget:,.0f} {currency} • {score}%)"
                keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"select_seeker_match:{match_id}")])

            keyboard.append([InlineKeyboardButton(cancel_label, callback_data="cancel_vehicle_deal")])
        else:
            top_matches = context.user_data.get('vehicle_top_matches', [])
            if not top_matches:
                return await self.route_to_main_menu(update, context)

            context.user_data.pop('active_vehicle_deal_id', None)

            if 'am' in lang:
                header_text = (
                    "🚗 <b>ዋና ዋና የሚዛመዱ የተሽከርካሪ አማራጮች</b>\n"
                    "────────────────\n"
                    "ዝርዝር መግለጫዎችን ለመገምገም እና የዋጋ ማቅረቢያ ለማስገባት ከታች ያለውን ተሽከርካሪ ይምረጡ:"
                )
                cancel_label = "❌ ፍለጋውን ሰርዝ"
            elif 'om' in lang:
                header_text = (
                    "🚗 <b>Filannoowwan Konkolaataa Wal Simatan Gubbaa</b>\n"
                    "────────────────\n"
                    "Ibsa ilaaluuf fi dhiyaate dhiyeessuuf konkolaataa gadii irraa filadhaa:"
                )
                cancel_label = "❌ Barbaacha Haqaa"
            else:
                header_text = (
                    "🚗 <b>Top Matching Vehicle Options</b>\n"
                    "────────────────\n"
                    "Select a vehicle listing below to review specs and submit an offer:"
                )
                cancel_label = "❌ Cancel Search"

            keyboard = []
            for idx, match in enumerate(top_matches, start=1):
                make_model = _format_mmy(match, ['make_model_year', 'vehicle_type', 'property_use'])
                price = safe_float(match.get('price', 0.0))
                currency = str(match.get('currency') or 'ETB').upper()
                match_id = match.get('id')
                btn_text = f"🚘 #{idx} {make_model} ({price:,.0f} {currency})"
                keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"select_vehicle_match:{match_id}")])

            keyboard.append([InlineKeyboardButton(cancel_label, callback_data="cancel_vehicle_deal")])

        reply_markup = InlineKeyboardMarkup(keyboard)

        try:
            if query.message and hasattr(query.message, "text") and query.message.text:
                await query.edit_message_text(text=header_text, reply_markup=reply_markup, parse_mode="HTML")
            else:
                await context.bot.send_message(chat_id=update.effective_chat.id, text=header_text,
                                               reply_markup=reply_markup, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Failed to return to top matches list: {e}")

        return self.AWAITING_MATCH_SELECTION

    @staticmethod
    def _format_fuel_display(fuel_raw: Optional[str], lang: str = "en") -> str:
        """Formats raw or pre-localized database/callback fuel values into clean,

        exact human-readable labels matching vehicle.fuel.* translations.
        """
        if not fuel_raw:
            return ""

        clean_raw = str(fuel_raw).strip().lower()

        # 1. Strip 'fuel_' prefix from callback payloads (e.g. 'fuel_hybrid_petrol' -> 'hybrid_petrol')
        if clean_raw.startswith("fuel_"):
            clean_raw = clean_raw[5:]

        clean = re.sub(r"[()/\\_\-]", " ", clean_raw)
        clean = re.sub(r"\s+", " ", clean).strip()

        clean_base = re.sub(r"\(.*?\)", "", clean_raw).replace("/", " ").strip()
        clean_base = re.sub(r"\s+", " ", clean_base)

        ALIAS_MAP = {
            # ==========================================
            # Benzine / Petrol (የቤንዚን መኪና / Boba'aa Beenzinii)
            # ==========================================
            "benzine": "benzine",
            "petrol": "benzine",
            "gasoline": "benzine",
            "beenzinii": "benzine",
            "beenzila": "benzine",
            "ቤንዚን": "benzine",
            "ፔትሮል": "benzine",

            # ==========================================
            # Diesel (የናፍጣ መኪና / Boba'aa Diizilii / Naafxaa)
            # ==========================================
            "diesel": "diesel",
            "diizili": "diesel",
            "dizilii": "diesel",
            "naafxaa": "diesel",
            "ዲዝኤል": "diesel",
            "ዲዜል": "diesel",
            "ዲዝል": "diesel",
            "ናፍጣ": "diesel",  # Added: Standard Amharic term for diesel

            # ==========================================
            # Electric (የኤሌክትሪክ መኪና / Elektiriikii)
            # ==========================================
            "electric": "electric (EV)",
            "ev": "electric (EV)",
            "elektirikii": "electric (EV)",
            "elektiriika": "electric (EV)",
            "ኤሌክትሪክ": "electric (EV)",
            "ኢቪ": "electric (EV)",

            # ==========================================
            # LNG / NGV / Natural Gas (የተፈጥሮ ጋዝ መኪና / Gaasii Uumamaa)
            # ==========================================
            "lng ngv": "lng_ngv",
            "lng": "lng_ngv",
            "ngv": "lng_ngv",
            "cng": "lng_ngv",
            "lpg": "lng_ngv",
            "gas": "lng_ngv",
            "natural gas": "lng_ngv",
            "gaazii uumamaa": "lng_ngv",
            "የተፈጥሮ ጋዝ": "lng_ngv",
            "ጋዝ": "lng_ngv",

            # ==========================================
            # Hybrid Petrol (ቤንዚን ኤሌክትሪክ ሃይብሪድ / Haayibriidii Beenzinii)
            # ==========================================
            "hybrid petrol": "hybrid_petrol",
            "hybrid benzine": "hybrid_petrol",
            "petrol electric hybrid": "hybrid_petrol",
            "beenzinii elektiriika haaybiriidii": "hybrid_petrol",
            "ሀይብሪድ ቤንዚን": "hybrid_petrol",
            "ቤንዚን ኤሌክትሪክ ሃይብሪድ": "hybrid_petrol",
            "hayibriidii beenzinii": "hybrid_petrol",

            # ==========================================
            # Hybrid Diesel (ናፍጣ ኤሌክትሪክ ሃይብሪድ / Haayibriidii Diizilii)
            # ==========================================
            "hybrid diesel": "hybrid_diesel",
            "diesel electric hybrid": "hybrid_diesel",
            "dizilii elektiriika haaybiriidii": "hybrid_diesel",
            "ሀይብሪድ ዲዝኤል": "hybrid_diesel",
            "ዲዝል ኤሌክትሪክ ሃይብሪድ": "hybrid_diesel",
            "ናፍጣ ኤሌክትሪክ ሃይብሪድ": "hybrid_diesel",  # Added: Natural Amharic phrasing
            "hayibriidii diizili": "hybrid_diesel",
            "hayibriidii naafxaa": "hybrid_diesel",  # Added: Standard Afaan Oromo variation

            # ==========================================
            # Hybrid Flex / Ethanol (ኢታኖል ሃይብሪድ / Haayibriidii Eetaanoolii)
            # ==========================================
            "hybrid flex": "hybrid_flex",
            "flex fuel": "hybrid_flex",
            "etaanoolii haaybiriidii": "hybrid_flex",
            "haayibriidii eetaanoolii": "hybrid_flex",  # Added: Natural grammar structure
            "ሀይብሪድ ፍሌክስ ፊውል": "hybrid_flex",
            "ፍሌክስ ፉኤል ኢታኖል ሃይብሪድ": "hybrid_flex",
            "የኢታኖል ሃይብሪድ": "hybrid_flex",  # Added: Natural Amharic definition

            # ==========================================
            # Hybrid Gaseous / Bi-Fuel (ጋዝ ኤሌክትሪክ ሃይብሪድ / Haayibriidii Boba'aa Lameen)
            # ==========================================
            "hybrid gaseous": "hybrid_gaseous",
            "bi fuel": "hybrid_gaseous",
            "gaazii elektiriika haaybiriidii": "hybrid_gaseous",
            "haayibriidii boba'aa lameen": "hybrid_gaseous",  # Added: Authentic Oromo term validated earlier
            "haayibriidii gaasii": "hybrid_gaseous",  # Added: Simplified Oromo option
            "ሀይብሪድ ባይ ፊውል": "hybrid_gaseous",
            "ባለሁለት-ነዳጅ ሃይብሪድ": "hybrid_gaseous",  # Added: Professional Amharic automotive term
            "ጋዝ ኤሌክትሪክ bi fuel ሃይብሪድ": "hybrid_gaseous",
            "ጋዝ ኤሌክትሪክ ሃይብሪድ": "hybrid_gaseous",  # Added: Natural Amharic shorthand

            # ==========================================
            # Hybrid Hydrogen (ሃይድሮጅን ሃይብሪድ / Haayibriidii Haayidroojiinii)
            # ==========================================
            "hybrid hydrogen": "hybrid_hydrogen",
            "haayidroojiinii haaybiriidii": "hybrid_hydrogen",
            "haayibriidii haayidroojiinii": "hybrid_hydrogen",  # Added: Correct modifier order
            "ሀይብሪድ ሃይድሮጅን": "hybrid_hydrogen",
            "ሃይድሮጅን ሃይብሪድ": "hybrid_hydrogen",  # Added: Cleaned up Amharic
            "ሃይድሮጅን ፉኤል ሴል ሃይብሪድ": "hybrid_hydrogen",

            # ==========================================
            # Hydrogen (የሃይድሮጅን መኪና / Boba'aa Haayidroojiinii)
            # ==========================================
            "hydrogen": "hydrogen",
            "fcev": "hydrogen",
            "hydrogen fuel cell": "hydrogen",
            "ሃይድሮጅን": "hydrogen",  # Added: Simple base term
            "ሃይድሮጅን ፊውል ሴል": "hydrogen",
            "የሃይድሮጅን ነዳጅ": "hydrogen",  # Added: Descriptive Amharic phrase
            "boba'aa haayidroojiinii": "hydrogen",  # Added: Authentic Afaan Oromo phrase
        }

        # Exact match dictionary aligned with vehicle.fuel.* translation strings
        CANONICAL_LABELS = {
            "benzine": {
                "en": "⛽ Benzine (Petrol)",
                "am": "⛽ ቤንዚን",
                "om": "⛽ Beenzinii (Beenzila)",
            },
            "diesel": {
                "en": "🛢️ Diesel",
                "am": "🛢️ ዲዝል (ናፍጣ)",
                "om": "🛢️ Dizilii (Naafxaa)",
            },
            "electric": {
                "en": "⚡ Electric (EV)",
                "am": "⚡ ኤሌክትሪክ",
                "om": "⚡ Elektiriika",
            },
            "lng_ngv": {
                "en": "🌐 LNG / NGV (Natural Gas)",
                "am": "🌐 ኤል.ኤን.ጂ / የተፈጥሮ ጋዝ (NGV)",
                "om": "🌐 LNG / Gaazii Uumamaa (NGV)",
            },
            "hybrid_petrol": {
                "en": "🔋 Petrol-Electric Hybrid",
                "am": "🔋 ቤንዚን-ኤሌክትሪክ ሃይብሪድ",
                "om": "🔋 Beenzinii-Elektiriika Haaybiriidii",
            },
            "hybrid_diesel": {
                "en": "🔋 Diesel-Electric Hybrid",
                "am": "🔋 ዲዝል-ኤሌክትሪክ ሃይብሪድ",
                "om": "🔋 Dizilii-Elektiriika Haaybiriidii",
            },
            "hybrid_flex": {
                "en": "🌱 Flex-Fuel (Ethanol) Hybrid",
                "am": "🌱 ፍሌክስ-ፉኤል (ኢታኖል) ሃይብሪድ",
                "om": "🌱 Flex-Fuel (Etaanoolii) Haaybiriidii",
            },
            "hybrid_gaseous": {
                "en": "🔥 Bi-Fuel (Gas-Electric) Hybrid",
                "am": "🔥 ጋዝ-ኤሌክትሪክ ሃይብሪድ",
                "om": "🔥 Gaazii-Elektiriika Haaybiriidii",
            },
            "hybrid_hydrogen": {
                "en": "💧 Hydrogen Fuel Cell Hybrid",
                "am": "💧 ሃይድሮጅን ፉኤል ሴል ሃይብሪድ",
                "om": "💧 Haayidroojiinii Haaybiriidii",
            },
            "hydrogen": {
                "en": "💧 Hydrogen Fuel Cell Hybrid",
                "am": "💧 ሃይድሮጅን ፉኤል ሴል ሃይብሪድ",
                "om": "💧 Haayidroojiinii Haaybiriidii",
            },
        }

        lang_key = (
            "am"
            if "am" in lang
            else ("om" if ("om" in lang or "or" in lang) else "en")
        )

        canonical_key = ALIAS_MAP.get(clean) or ALIAS_MAP.get(clean_base)

        if canonical_key and canonical_key in CANONICAL_LABELS:
            return CANONICAL_LABELS[canonical_key].get(
                lang_key, CANONICAL_LABELS[canonical_key]["en"]
            )

        return str(fuel_raw).strip().title()

    async def _evaluate_forward_matches(
            self,
            seeker_prefs: dict,
            candidate_listings: list,
            target_count: int = 3,
    ) -> list:
        """Executes a Two-Pass Evaluation for forward matching:
        Pass 1: Strict match using requested specs & make_model_year.
        Pass 2: Functional/Semantic similarity fallback (relaxing make_model_year)
                to fill up to target_count matches when strict matches < target_count.
        """
        vertical = str(seeker_prefs.get("vertical_type", "vehicle")).lower()
        scored_matches = []
        seen_ids = set()

        # PASS 1: Strict Evaluation
        for listing in candidate_listings:
            listing_id = str(listing.get("id"))
            score = 0.0
            if (
                    hasattr(self, "matching")
                    and self.matching
                    and hasattr(self.matching, "evaluate_matches")
            ):
                try:
                    eval_res = await self.matching.evaluate_matches(
                        vertical, seeker_prefs, [listing]
                    )
                    if eval_res:
                        score = safe_float(eval_res[0].get("_match_score", 0.0))
                except Exception as ex:
                    logger.warning(
                        f"Pass 1 scoring failed for listing {listing_id}: {ex}"
                    )

            if score > 0.0:
                l_match = dict(listing)
                l_match["_match_score"] = score
                l_match["id"] = listing_id
                scored_matches.append(l_match)
                seen_ids.add(listing_id)

        # PASS 2: Semantic / Functional Fallback (if strict matches < target_count)
        if len(scored_matches) < target_count:
            relaxed_prefs = dict(seeker_prefs)
            relaxed_prefs["make_model_year"] = "Any"
            relaxed_prefs["preferred_make_model_year"] = "Any"
            relaxed_prefs["allow_semantic_fallback"] = True

            for listing in candidate_listings:
                listing_id = str(listing.get("id"))
                if listing_id in seen_ids:
                    continue

                score = 0.0
                if (
                        hasattr(self, "matching")
                        and self.matching
                        and hasattr(self.matching, "evaluate_matches")
                ):
                    try:
                        eval_res = await self.matching.evaluate_matches(
                            vertical, relaxed_prefs, [listing]
                        )
                        if eval_res:
                            score = safe_float(
                                eval_res[0].get("_match_score", 0.0)
                            )
                    except Exception as ex:
                        logger.warning(
                            f"Pass 2 scoring failed for listing {listing_id}: {ex}"
                        )

                if score > 0.0:
                    l_match = dict(listing)
                    l_match["_match_score"] = score
                    l_match["id"] = listing_id
                    scored_matches.append(l_match)
                    seen_ids.add(listing_id)

        # Sort descending by match score
        scored_matches.sort(
            key=lambda x: safe_float(x.get("_match_score", 0.0)), reverse=True
        )
        return scored_matches[:target_count]

    async def handle_match_selection(
            self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        query = update.callback_query
        if query:
            try:
                await query.answer()
            except Exception:
                pass

        # Immediate Chat Action for rapid UI feedback on selection
        if update.effective_chat:
            try:
                await context.bot.send_chat_action(
                    chat_id=update.effective_chat.id, action=ChatAction.TYPING
                )
            except Exception:
                pass

        cb_data = query.data if query else ""

        is_reverse = cb_data.startswith("select_seeker_match:")
        context.user_data["is_reverse_match"] = is_reverse
        match_id = cb_data.split(":")[1] if ":" in cb_data else ""
        lang = str(
            context.user_data.get("language")
            or context.user_data.get("lang")
            or "en"
        ).lower()

        supabase_client = getattr(self, "db", None)
        if hasattr(supabase_client, "supabase"):
            supabase_client = supabase_client.supabase

        if is_reverse:
            top_matches = context.user_data.get("seeker_top_matches", [])
            selected_request = next(
                (m for m in top_matches if str(m.get("id")) == match_id), None
            )

            if not selected_request and supabase_client:
                try:
                    res = (
                        await supabase_client.table("seeker_requests")
                        .select("*")
                        .eq("id", match_id)
                        .maybe_single()
                        .execute()
                    )
                    if res and res.data:
                        selected_request = res.data
                except Exception as e:
                    logger.warning(
                        f"Failed to fetch seeker request {match_id}: {e}"
                    )

            if not selected_request:
                if "am" in lang:
                    msg_inactive = (
                        "⚠️ <b>የተመረጠው የፈላጊ ጥያቄ አሁን ንቁ አይደለም።</b>"
                    )
                elif "om" in lang:
                    msg_inactive = "⚠️ <b>Gaaffiin barbaadamaa filatame ammaa hojiirra hin jiru.</b>"
                else:
                    msg_inactive = (
                            self._tx(context, "msg_request_inactive")
                            or "⚠️ <b>Selected seeker request is no longer active.</b>"
                    )

                if query:
                    try:
                        await asyncio.wait_for(
                            query.edit_message_text(
                                msg_inactive, parse_mode="HTML"
                            ),
                            timeout=3.0,
                        )
                    except Exception:
                        pass
                return await self.route_to_main_menu(update, context)

            budget = safe_float(selected_request.get("max_budget", 0.0))
            currency = str(selected_request.get("currency") or "ETB").upper()
            context.user_data["currency"] = currency
            target_seeker_id = selected_request.get("seeker_id")

            provider_asking_price = safe_float(
                context.user_data.get("price")
                or context.user_data.get("vehicle_price")
                or budget
                or 0.0
            )

            requested_mmy = _format_mmy(selected_request)
            raw_req_fuel = selected_request.get(
                "fuel_type"
            ) or selected_request.get("preferred_fuel_type")
            requested_fuel = self._format_fuel_display(raw_req_fuel, lang=lang)

            offered_mmy = _clean_val(
                context.user_data.get("listing_make_model_year")
            )
            raw_offered_fuel = context.user_data.get("listing_fuel_type")
            offered_fuel = self._format_fuel_display(raw_offered_fuel, lang=lang)

            if (
                    not offered_mmy
                    and context.user_data.get("last_created_listing_id")
                    and supabase_client
            ):
                try:
                    p_res = (
                        await supabase_client.table("provider_listings")
                        .select("*")
                        .eq("id", context.user_data["last_created_listing_id"])
                        .maybe_single()
                        .execute()
                    )
                    if p_res and p_res.data:
                        offered_mmy = _format_mmy(
                            p_res.data, ["make_model_year", "brand", "model"]
                        )
                        offered_fuel = self._format_fuel_display(
                            p_res.data.get("fuel_type"), lang=lang
                        )
                except Exception as ex:
                    logger.warning(
                        f"Failed to fetch provider listing details: {ex}"
                    )

            if "am" in lang:
                fallback_veh = "የእርስዎ የተዘረዘረ ተሽከርካሪ"
            elif "om" in lang:
                fallback_veh = "Konkolaataa Keessan Tarreeffame"
            else:
                fallback_veh = "Your Listed Vehicle"

            offered_mmy = (
                    offered_mmy
                    or self._tx(context, "lbl_your_listed_vehicle")
                    or fallback_veh
            )

            request_location = _clean_val(
                selected_request.get("origin")
                or selected_request.get("destination")
                or selected_request.get("route_string")
                or selected_request.get("location_text")
            )

            context.user_data["target_seeker_id"] = target_seeker_id
            context.user_data["active_vehicle_deal_id"] = match_id
            context.user_data["vehicle_price"] = provider_asking_price
            context.user_data["asset_title"] = offered_mmy
            context.user_data["preferred_make_model_year"] = requested_mmy
            context.user_data["preferred_fuel_type"] = requested_fuel or ""
            context.user_data["listing_make_model_year"] = offered_mmy
            context.user_data["listing_fuel_type"] = offered_fuel or ""

            if "am" in lang:
                lbl_header = "👤 የፈላጊ ተዛመጃ ዝርዝሮች"
                lbl_req_spec = "🎯 የተጠየቀ ዝርዝር:"
                lbl_prop_spec = "🚙 የእርስዎ የቀረበ ዝርዝር:"
                lbl_fuel = "⛽ የነዳጅ ዓይነት:"
                lbl_your_veh = "የእርስዎ ተሽከርካሪ"
                lbl_loc = "📍 ቦታ/መስመር:"
                lbl_budget = "💰 የፈላጊ በጀት:"
                lbl_note = "💡 ማስታወሻ፡ ዝርዝር መግለጫዎች ቢለያዩም ይህ ማዛመጃ ተሽከርካሪዎን እንዲያቀርቡ ያስችሎታል።"
                select_prompt = (
                    "የዋጋ ማቅረቢያዎን ለማስገባት ከታች ያለውን አማራጭ ይምረጡ:"
                )
                btn_custom = "💬 የግል ዋጋ ማቅረቢያ አስገባ"
                btn_back = "⬅️ ወደ ዋናዎቹ ተዛመጃዎች ተመለስ"
                btn_cancel = "❌ ሰርዝ"
            elif "om" in lang:
                lbl_header = "👤 IBSA WAL-SIMANNAA BARBAADDATAA"
                lbl_req_spec = "🎯 Gosa Gaafatame:"
                lbl_prop_spec = "🚙 Gosa Dhiyeessitan:"
                lbl_fuel = "⛽ Gosa Boba'aa:"
                lbl_your_veh = "Konkolaataa Keessan"
                lbl_loc = "📍 Bakka/Karaan:"
                lbl_budget = "💰 Baajata Barbaadamaa:"
                lbl_note = "💡 Yaadachiisa: Kunis yoo xiqqoo wal hin simne ta'eyyuu konkolaataa keessan dhiyeessuu " \
                           "dandeessisu."
                select_prompt = (
                    "Gatii keessan galchuuf filannoowwan gadii irraa filadhaa:"
                )
                btn_custom = "💬 Gatii Mataa Keessanii Galchaa"
                btn_back = "⬅️ Gara wal-fakkiiwwan olaanootti deebi'aa"
                btn_cancel = "❌ Haqaa"
            else:
                lbl_header = "👤 SEEKER MATCH DETAILS"
                lbl_req_spec = "🎯 Requested Spec:"
                lbl_prop_spec = "🚙 Your Proposed Spec:"
                lbl_fuel = "⛽ Fuel Type:"
                lbl_your_veh = "Your Vehicle"
                lbl_loc = "📍 Location/Route:"
                lbl_budget = "💰 Seeker Budget:"
                lbl_note = "💡 Note: This match allows proposing your vehicle even if specifications vary slightly."
                select_prompt = "Select an option below to submit your rate:"
                btn_custom = "💬 Enter Custom Offer"
                btn_back = "⬅️ Back to Top Matches"
                btn_cancel = "❌ Cancel"

            FUEL_TRANSLATIONS = {
                "en": {
                    "benzine": "⛽ Benzine (Petrol)",
                    "petrol": "⛽ Benzine (Petrol)",
                    "diesel": "🛢️ Diesel",
                    "electric": "⚡ Electric (EV)",
                    "lng_ngv": "🌐 LNG / NGV (Natural Gas)",
                    "lng": "🌐 LNG / NGV (Natural Gas)",
                    "ngv": "🌐 LNG / NGV (Natural Gas)",
                    "hybrid_petrol": "🔋 Petrol-Electric Hybrid",
                    "hybrid_diesel": "🔋 Diesel-Electric Hybrid",
                    "hybrid_flex": "🌱 Flex-Fuel (Ethanol) Hybrid",
                    "hybrid_gaseous": "🔥 Bi-Fuel (Gas-Electric) Hybrid",
                    "hybrid_hydrogen": "💧 Hydrogen Fuel Cell Hybrid",
                },
                "am": {
                    "benzine": "⛽ ቤንዚን",
                    "petrol": "⛽ ቤንዚን",
                    "diesel": "🛢️ ዲዝል (ናፍጣ)",
                    "electric": "⚡ ኤሌክትሪክ",
                    "lng_ngv": "🌐 ኤል.ኤን.ጂ / የተፈጥሮ ጋዝ (NGV)",
                    "lng": "🌐 ኤል.ኤን.ጂ / የተፈጥሮ ጋዝ (NGV)",
                    "ngv": "🌐 ኤል.ኤን.ጂ / የተፈጥሮ ጋዝ (NGV)",
                    "hybrid_petrol": "🔋 ቤንዚን-ኤሌክትሪክ ሃይብሪድ",
                    "hybrid_diesel": "🔋 ዲዝል-ኤሌክትሪክ ሃይብሪድ",
                    "hybrid_flex": "🌱 ፍሌክስ-ፉኤል (ኢታኖል) ሃይብሪድ",
                    "hybrid_gaseous": "🔥 ባለሁለት-ነዳጅ (ጋዝ-ኤሌክትሪክ) ሃይብሪድ",
                    "hybrid_hydrogen": "💧 ሃይድሮጅን ፉኤል ሴል ሃይብሪድ",
                },
                "om": {
                    "benzine": "⛽ Beenzinii (Beenzila)",
                    "petrol": "⛽ Beenzinii (Beenzila)",
                    "diesel": "🛢️ Dizilii (Naafxaa)",
                    "electric": "⚡ Elektiriika",
                    "lng_ngv": "🌐 LNG / Gaazii Uumamaa (NGV)",
                    "lng": "🌐 LNG / Gaazii Uumamaa (NGV)",
                    "ngv": "🌐 LNG / Gaazii Uumamaa (NGV)",
                    "hybrid_petrol": "🔋 Beenzinii-Elektiriika Haaybiriidii",
                    "hybrid_diesel": "🔋 Dizilii-Elektiriika Haaybiriidii",
                    "hybrid_flex": "🌱 Flex-Fuel (Etaanoolii) Haaybiriidii",
                    "hybrid_gaseous": "🔥 Haayibriidii Boba'aa Lameen (Gaasii-Elektiriikii)",
                    "hybrid_hydrogen": "💧 Haayidroojiinii Haaybiriidii",
                }
            }

            def format_fuel_type(fuel_val: Optional[str], lang_code: str) -> str:
                if not fuel_val or str(fuel_val).strip() == "":
                    return ""

                clean_lang = "am" if "am" in lang_code else ("om" if "om" in lang_code else "en")
                slug = str(fuel_val).strip().lower()

                # Check localized mapping
                lang_map = FUEL_TRANSLATIONS.get(clean_lang, FUEL_TRANSLATIONS["en"])
                if slug in lang_map:
                    return lang_map[slug]

                # Fallback lookup in English mapping
                if slug in FUEL_TRANSLATIONS["en"]:
                    return FUEL_TRANSLATIONS["en"][slug]

                # Clean up unmapped raw slugs (e.g. "lng_ngv" -> "Lng Ngv")
                return slug.replace("_", " ").title()

            fuel_section = ""
            if requested_fuel or offered_fuel:
                # Resolve human-readable localized text for fuel types
                fmt_req_fuel = format_fuel_type(requested_fuel, lang) if requested_fuel else None
                fmt_off_fuel = format_fuel_type(offered_fuel, lang) if offered_fuel else None

                flex_lbl = (
                    "ተለዋዋጭ"
                    if "am" in lang
                    else ("Jijjiiramaa" if "om" in lang else "Flexible")
                )

                # Use localized fuel display or fallback label
                disp_req_fuel = fmt_req_fuel or flex_lbl
                fuel_section = f"{lbl_fuel} {html.escape(disp_req_fuel)}"

                if (
                        fmt_off_fuel
                        and (offered_fuel or "").lower() != (requested_fuel or "").lower()
                ):
                    fuel_section += (
                        f" <i>({lbl_your_veh}: {html.escape(fmt_off_fuel)})</i>"
                    )
                fuel_section += "\n"

            loc_section = (
                f"{lbl_loc} {html.escape(request_location)}\n"
                if request_location
                else ""
            )

            card_msg = (
                f"<b>{lbl_header}</b>\n"
                f"────────────────\n"
                f"{lbl_req_spec} {html.escape(requested_mmy)}\n"
                f"{lbl_prop_spec} {html.escape(offered_mmy)}\n"
                f"{fuel_section}"
                f"{loc_section}"
                f"{lbl_budget} <code>{budget:,.2f} {currency}</code>\n"
                f"────────────────\n"
                f"<i>{lbl_note}</i>\n\n"
                f"{select_prompt}"
            )

            formatted_price = f"{provider_asking_price:,.2f}"
            if "am" in lang:
                btn_rate_text = f"⚡ ዋጋ ላክ ({formatted_price} {currency})"
            elif "om" in lang:
                btn_rate_text = f"⚡ Gatii Ergi ({formatted_price} {currency})"
            else:
                btn_rate_text = f"⚡ Send Rate ({formatted_price} {currency})"

            keyboard = [
                [
                    InlineKeyboardButton(
                        btn_custom, callback_data=f"prompt_offer:{match_id}"
                    )
                ],
                [
                    InlineKeyboardButton(
                        btn_rate_text,
                        callback_data=f"lock_vehicle_deal:{match_id}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        btn_back, callback_data="back_to_top_matches"
                    )
                ],
                [
                    InlineKeyboardButton(
                        btn_cancel, callback_data="cancel_vehicle_deal"
                    )
                ],
            ]

            if query:
                try:
                    await asyncio.wait_for(
                        query.edit_message_text(
                            text=card_msg,
                            reply_markup=InlineKeyboardMarkup(keyboard),
                            parse_mode="HTML",
                        ),
                        timeout=3.0,
                    )
                except Exception as edit_err:
                    logger.warning(
                        f"Reverse match UI edit failed or timed out: {edit_err}"
                    )

            return self.AWAITING_MATCH_SELECTION

        else:
            # FORWARD MATCHING
            top_matches = context.user_data.get("vehicle_top_matches", [])
            selected_vehicle = next(
                (m for m in top_matches if str(m.get("id")) == match_id), None
            )

            if (
                    len(top_matches) <= 1
                    and supabase_client
                    and context.user_data.get("preferred_make_model_year")
            ):
                try:
                    category = context.user_data.get(
                        "vehicle_type"
                    ) or context.user_data.get("property_use")
                    v_query = (
                        supabase_client.table("provider_listings")
                        .select("*")
                        .eq("status", "active")
                    )
                    if category:
                        v_query = v_query.ilike("vehicle_type", f"%{category}%")

                    raw_listings_res = await v_query.execute()
                    if raw_listings_res and raw_listings_res.data:
                        seeker_prefs = {
                            "vertical_type": "vehicle",
                            "vehicle_type": category or "bus/minibus",
                            "make_model_year": context.user_data.get(
                                "preferred_make_model_year"
                            ),
                            "preferred_make_model_year": context.user_data.get(
                                "preferred_make_model_year"
                            ),
                            "fuel_type": context.user_data.get(
                                "preferred_fuel_type", ""
                            ),
                            "max_budget": safe_float(
                                context.user_data.get("max_budget", 0.0)
                            ),
                        }
                        two_pass_matches = await self._evaluate_forward_matches(
                            seeker_prefs, raw_listings_res.data, target_count=3
                        )
                        if two_pass_matches:
                            context.user_data["vehicle_top_matches"] = (
                                two_pass_matches
                            )
                            top_matches = two_pass_matches
                            selected_vehicle = next(
                                (
                                    m
                                    for m in top_matches
                                    if str(m.get("id")) == match_id
                                ),
                                None,
                            )
                except Exception as tp_err:
                    logger.warning(
                        f"Forward match two-pass expansion failed: {tp_err}"
                    )

            if not selected_vehicle and supabase_client:
                try:
                    res = (
                        await supabase_client.table("provider_listings")
                        .select("*")
                        .eq("id", match_id)
                        .maybe_single()
                        .execute()
                    )
                    if res and res.data:
                        selected_vehicle = res.data
                except Exception as e:
                    logger.warning(
                        f"Failed to fetch provider listing {match_id}: {e}"
                    )

            if not selected_vehicle:
                if "am" in lang:
                    msg_vehicle_inactive = (
                        "⚠️ <b>የተመረጠው ተሽከርካሪ አሁን ንቁ አይደለም።</b>"
                    )
                elif "om" in lang:
                    msg_vehicle_inactive = "⚠️ <b>Konkolaataan filatame ammaa hojiirra hin jiru.</b>"
                else:
                    msg_vehicle_inactive = (
                            self._tx(context, "msg_vehicle_inactive")
                            or "⚠️ <b>Selected vehicle is no longer active.</b>"
                    )

                if query:
                    try:
                        await asyncio.wait_for(
                            query.edit_message_text(
                                msg_vehicle_inactive, parse_mode="HTML"
                            ),
                            timeout=3.0,
                        )
                    except Exception:
                        pass
                return await self.route_to_main_menu(update, context)

            price = safe_float(selected_vehicle.get("price", 0.0))
            currency = str(selected_vehicle.get("currency") or "ETB").upper()
            context.user_data["currency"] = currency

            veh_fallback = (
                "ተሽከርካሪ"
                if "am" in lang
                else ("Konkolaataa" if "om" in lang else "Vehicle")
            )
            vehicle_type = _clean_val(
                selected_vehicle.get("vehicle_type")
                or selected_vehicle.get("property_use"),
                veh_fallback,
            )
            listing_mmy = _format_mmy(
                selected_vehicle, ["make_model_year", "brand", "model"]
            )
            listing_fuel = self._format_fuel_display(
                selected_vehicle.get("fuel_type"), lang=lang
            )
            plate = _clean_val(
                selected_vehicle.get("vehicle_plate")
                or selected_vehicle.get("plate_number")
            )

            display_mmy = listing_mmy
            if plate:
                display_mmy += f" (ሰሌዳ/Plate: {plate})"

            vehicle_location = _clean_val(
                selected_vehicle.get("location_text")
                or selected_vehicle.get("origin")
                or selected_vehicle.get("city")
                or selected_vehicle.get("operating_region")
            )

            seeker_requested_mmy = _clean_val(
                context.user_data.get("preferred_make_model_year")
            )

            context.user_data["active_vehicle_deal_id"] = match_id
            context.user_data["vehicle_price"] = price
            context.user_data["provider_id"] = selected_vehicle.get("provider_id")
            context.user_data["asset_title"] = display_mmy
            context.user_data["vehicle_type"] = vehicle_type
            context.user_data["listing_make_model_year"] = listing_mmy
            context.user_data["listing_fuel_type"] = listing_fuel or ""

            if "am" in lang:
                lbl_v_header = "🚗 የተሽከርካሪ አማራጭ ዝርዝሮች"
                lbl_your_req = "🎯 የእርስዎ የጠየቁት ዝርዝር:"
                lbl_offered_veh = "🚜 የቀረበው ተሽከርካሪ ዝርዝር:"
                lbl_category = "🚘 ምድብ:"
                lbl_fuel_v = "⛽ የነዳጅ ዓይነት:"
                lbl_loc_v = "📍 ቦታ:"
                lbl_asking_price = "💰 የሚጠየቀው ዋጋ:"
                select_prompt_v = "ለመቀጠል ከታች ያለውን አማራጭ ይምረጡ:"
                btn_custom = "💬 የግል ዋጋ ማቅረቢያ አስገባ"
                btn_back = "⬅️ ወደ ዋናዎቹ ተዛመጃዎች ተመለስ"
            elif "om" in lang:
                lbl_v_header = "🚗 IBSI FILANNOO KOKOLAATAA"
                lbl_your_req = "🎯 Gosa Gaafatani:"
                lbl_offered_veh = "🚜 Konkolaataa Dhiyaate:"
                lbl_category = "🚘 Ramaddii:"
                lbl_fuel_v = "⛽ Gosa Boba'aa:"
                lbl_loc_v = "📍 Bakka:"
                lbl_asking_price = "💰 Gatii Gaafatame:"
                select_prompt_v = "Itti fufaaf filannoowwan gadii irraa filadhaa:"
                btn_custom = "💬 Gatii Mataa Keessanii Galchaa"
                btn_back = "⬅️ Gara wal-fakkiiwwan olaanootti deebi'aa"
            else:
                lbl_v_header = "🚗 VEHICLE OPTION DETAILS"
                lbl_your_req = "🎯 Your Requested Spec:"
                lbl_offered_veh = "🚜 Offered Vehicle Spec:"
                lbl_category = "🚘 Category:"
                lbl_fuel_v = "⛽ Fuel Type:"
                lbl_loc_v = "📍 Location:"
                lbl_asking_price = "💰 Asking Price:"
                select_prompt_v = "Select an option below to proceed:"
                btn_custom = "💬 Enter Custom Offer"
                btn_back = "⬅️ Back to Top Matches"

            requested_section = ""
            if seeker_requested_mmy:
                requested_section = (
                    f"{lbl_your_req} {html.escape(seeker_requested_mmy)}\n"
                )

            fuel_line = (
                f"{lbl_fuel_v} {html.escape(listing_fuel)}\n" if listing_fuel else ""
            )
            loc_line = (
                f"{lbl_loc_v} {html.escape(vehicle_location)}\n"
                if vehicle_location
                else ""
            )

            card_msg = (
                f"<b>{lbl_v_header}</b>\n"
                f"────────────────\n"
                f"{requested_section}"
                f"{lbl_offered_veh} {html.escape(listing_mmy)}\n"
                f"{lbl_category} {html.escape(vehicle_type.upper())}\n"
                f"{fuel_line}"
                f"{loc_line}"
                f"{lbl_asking_price} <code>{price:,.2f} {currency}</code>\n"
                f"────────────────\n"
                f"{select_prompt_v}"
            )

            formatted_price = f"{price:,.2f}"
            if "am" in lang:
                btn_accept_text = (
                    f"✅ የዋጋ ተመኑን ይቀበሉ ({formatted_price} {currency})"
                )
            elif "om" in lang:
                btn_accept_text = (
                    f"✅ Gatii dhaha fudhadhaa ({formatted_price} {currency})"
                )
            else:
                btn_accept_text = f"✅ Accept Rate ({formatted_price} {currency})"

            keyboard = [
                [
                    InlineKeyboardButton(
                        btn_custom, callback_data=f"prompt_offer:{match_id}"
                    )
                ],
                [
                    InlineKeyboardButton(
                        btn_accept_text,
                        callback_data=f"lock_vehicle_deal:{match_id}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        btn_back, callback_data="back_to_top_matches"
                    )
                ],
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

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
                                return (
                                        parsed_json.get("file_id")
                                        or parsed_json.get("url")
                                        or parsed_json.get("path")
                                        or parsed_json.get("image_url")
                                )
                        except Exception:
                            pass
                    return cleaned
                elif isinstance(item, dict):
                    return (
                            item.get("file_id")
                            or item.get("url")
                            or item.get("path")
                            or item.get("image_url")
                    )
                return None

            # 1. Prioritize native cached photo_file_id
            for key in ["photo_file_id", "photo_url", "image_url"]:
                val = selected_vehicle.get(key)
                if val:
                    photo_ref = (
                        _extract_url_or_id(val[0])
                        if isinstance(val, list) and len(val) > 0
                        else _extract_url_or_id(val)
                    )
                    if photo_ref:
                        break

            # 2. Fix missing protocol on domain paths
            if (
                    photo_ref
                    and not photo_ref.startswith(("http://", "https://"))
                    and "." in photo_ref
                    and "/" in photo_ref
            ):
                photo_ref = f"https://{photo_ref}"

            presigned_url = None
            if photo_ref:
                is_external = (
                        photo_ref.startswith(("http://", "https://")) or "/" in photo_ref
                )

                if is_external:
                    img_service = cast(
                        Any,
                        getattr(
                            self,
                            "image_service",
                            getattr(self, "storage", None),
                        ),
                    )
                    if img_service:
                        try:
                            presigned_url = await asyncio.wait_for(
                                img_service.get_presigned_url_async(photo_ref),
                                timeout=2.0,
                            )
                        except Exception as sign_err:
                            logger.warning(
                                f"Failed to generate pre-signed URL or timed out: {sign_err}"
                            )
                            presigned_url = photo_ref
                    else:
                        presigned_url = photo_ref
                else:
                    presigned_url = photo_ref

            photo_dispatched = False
            if presigned_url and update.effective_chat:
                try:
                    # Strict 3.0s timeout on photo sending to avoid blocking
                    await asyncio.wait_for(
                        context.bot.send_photo(
                            chat_id=update.effective_chat.id,
                            photo=presigned_url,
                            caption=card_msg,
                            reply_markup=reply_markup,
                            parse_mode="HTML",
                        ),
                        timeout=3.0,
                    )
                    photo_dispatched = True

                    # Delete previous interactive menu in background
                    if query and query.message:
                        asyncio.create_task(
                            context.bot.delete_message(
                                chat_id=update.effective_chat.id,
                                message_id=query.message.message_id,
                            )
                        )
                except Exception as send_err:
                    logger.warning(
                        f"Photo dispatch failed or timed out: {send_err}"
                    )

            # Fallback to text card if photo failed or timed out
            if not photo_dispatched and query and query.message:
                try:
                    await asyncio.wait_for(
                        query.edit_message_text(
                            text=card_msg,
                            reply_markup=reply_markup,
                            parse_mode="HTML",
                        ),
                        timeout=3.0,
                    )
                except Exception:
                    if update.effective_chat:
                        try:
                            await asyncio.wait_for(
                                context.bot.send_message(
                                    chat_id=update.effective_chat.id,
                                    text=card_msg,
                                    reply_markup=reply_markup,
                                    parse_mode="HTML",
                                ),
                                timeout=3.0,
                            )
                        except Exception as msg_err:
                            logger.warning(
                                f"Text message fallback failed: {msg_err}"
                            )

            return self.AWAITING_MATCH_SELECTION

    async def prompt_custom_offer(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        query = update.callback_query
        if query:
            try:
                await query.answer()
            except Exception:
                pass
            if query.data and ":" in query.data:
                identifier = query.data.split(":", 1)[1]
                if identifier:
                    context.user_data['active_vehicle_deal_id'] = identifier
                    context.user_data['target_listing_id'] = identifier

        is_reverse = context.user_data.get('is_reverse_match', False)
        active_id = str(
            context.user_data.get("active_vehicle_deal_id")
            or context.user_data.get("target_listing_id")
            or context.user_data.get("last_created_listing_id")
            or "pending"
        )

        # Active user's language preference
        lang = str(
            context.user_data.get('language') or context.user_data.get('lang') or 'en'
        ).lower()

        currency = str(
            context.user_data.get('currency')
            or context.user_data.get('vehicle_currency')
            or 'ETB'
        ).upper()

        if active_id != "pending" and hasattr(self, 'db') and self.db:
            try:
                db_client = self.db.supabase if hasattr(self.db, 'supabase') else self.db
                if is_reverse:
                    req_res = await db_client.table('seeker_requests').select('*').eq('id',
                                                                                      active_id).maybe_single(

                    ).execute()
                    if req_res and hasattr(req_res, 'data') and req_res.data:
                        r_data = req_res.data
                        if r_data.get('currency'):
                            currency = str(r_data['currency']).upper()
                            context.user_data['currency'] = currency
                        if r_data.get('max_budget') is not None:
                            context.user_data['max_budget'] = safe_float(r_data['max_budget'])

                        pref_mmy = _format_mmy(r_data)
                        pref_fuel = _clean_val(r_data.get('fuel_type') or r_data.get('preferred_fuel_type'))
                        context.user_data['preferred_make_model_year'] = pref_mmy
                        context.user_data['preferred_fuel_type'] = pref_fuel or ""
                else:
                    listing_res = await db_client.table('provider_listings').select('*').eq('id',
                                                                                            active_id).maybe_single(

                    ).execute()
                    if listing_res and hasattr(listing_res, 'data') and listing_res.data:
                        l_data = listing_res.data
                        if l_data.get('currency'):
                            currency = str(l_data['currency']).upper()
                            context.user_data['currency'] = currency
                        if l_data.get('price') is not None:
                            context.user_data['vehicle_price'] = safe_float(l_data['price'])

                        list_mmy = _format_mmy(l_data, ['make_model_year', 'brand', 'model'])
                        list_fuel = _clean_val(l_data.get('fuel_type'))
                        context.user_data['listing_make_model_year'] = list_mmy
                        context.user_data['listing_fuel_type'] = list_fuel or ""
            except Exception as e:
                logger.warning(f"Could not fetch dynamic specs metadata for target ID {active_id}: {e}")

        # Fallbacks per locale
        default_req_fallback = "የመደበኛ ተሽከርካሪ ጥያቄ" if 'am' in lang else (
            "Gaaffii Konkolaataa Idilee" if 'om' in lang or 'or' in lang else "Standard Vehicle Request")
        default_offer_fallback = "የቀረበው ተሽከርካሪዎ" if 'am' in lang else (
            "Konkolaataa Dhiyaate" if 'om' in lang or 'or' in lang else "Your Proposed Vehicle")

        req_mmy = _clean_val(context.user_data.get('preferred_make_model_year'), default_req_fallback)
        offered_mmy = _clean_val(context.user_data.get('listing_make_model_year'), default_offer_fallback)

        # Localized labels and prompts
        if 'am' in lang:
            title_text = "የተሽከርካሪ / የዋጋ ማቅረቢያ ማስተካከያ" if is_reverse else "የግል የዋጋ ማቅረቢያ ማስተካከያ"
            req_fuel_label = "የተጠየቀው ነዳጅ ዓይነት"
            offered_fuel_label = "የእርስዎ ነዳጅ ዓይነት"
            fuel_label = "የነዳጅ ዓይነት"
            target_budget_label = "የፈላጊው የበጀት ገደብ"
            asking_rate_label = "የተጠየቀው ዋጋ"
            desired_spec_label = "የፈላጊው የተጠየቀ መግለጫ" if is_reverse else "የእርስዎ የሚፈለግ መግለጫ"
            offered_spec_label = "የእርስዎ የቀረበ ተሽከርካሪ" if is_reverse else "የተዘረዘረው ተሽከርካሪ መግለጫ"
            reply_prompt = "👉 <i>እባክዎን የቁጥር ዋጋ በቁጥሮች ብቻ በመጻፍ ይመልሱ (ለምሳሌ 25000):</i>"
        elif 'om' in lang or 'or' in lang:
            title_text = "DHIYEESSII KONKOLAATAA / GATII" if is_reverse else "DHIYEESSII GATII CUSTOM"
            req_fuel_label = "Boba'aa Gaafatame"
            offered_fuel_label = "Gosa Boba'aa Keessanii"
            fuel_label = "Gosa Boba'aa"
            target_budget_label = "Baajata Barbaadame"
            asking_rate_label = "Gatii Gaafatame"
            desired_spec_label = "Fedhii Barbaadaa" if is_reverse else "Fedhii Keessan"
            offered_spec_label = "Konkolaataa Dhiyaate" if is_reverse else "Ibsa Konkolaataa Tarreeffame"
            reply_prompt = "👉 <i>Maaloo gatii barbaaddan lakkoofsaan deebisaa (fakkeenyaaf 25000):</i>"
        else:
            title_text = "SUBMIT VEHICLE PROPOSAL" if is_reverse else "SUBMIT CUSTOM OFFER"
            req_fuel_label = "Requested Fuel"
            offered_fuel_label = "Your Fuel Type"
            fuel_label = "Fuel Type"
            target_budget_label = "Seeker Budget Target"
            asking_rate_label = "Asking Rate"
            desired_spec_label = "Seeker Requested Spec" if is_reverse else "Your Desired Spec"
            offered_spec_label = "Your Offered Vehicle" if is_reverse else "Listed Vehicle Spec"
            reply_prompt = "👉 <i>Please reply with your offer amount in numerical digits (e.g. 25000):</i>"

        if is_reverse:
            raw_price = safe_float(context.user_data.get('max_budget') or context.user_data.get('vehicle_price', 0.0))
            req_fuel = _clean_val(context.user_data.get('preferred_fuel_type'))
            offered_fuel = _clean_val(context.user_data.get('listing_fuel_type'))

            fuel_line = f"⛽ <b>{req_fuel_label}:</b> {html.escape(req_fuel)}\n" if req_fuel else ""
            offered_fuel_line = f"⛽ <b>{offered_fuel_label}:</b> {html.escape(offered_fuel)}\n" if offered_fuel else ""

            prompt_msg = (
                f"💬 <b>{title_text}</b>\n"
                f"────────────────\n"
                f"🎯 <b>{desired_spec_label}:</b> {html.escape(req_mmy)}\n"
                f"{fuel_line}"
                f"🚜 <b>{offered_spec_label}:</b> {html.escape(offered_mmy)}\n"
                f"{offered_fuel_line}"
                f"💰 <b>{target_budget_label}:</b> <code>{raw_price:,.2f} {currency}</code>\n"
                f"────────────────\n"
                f"{reply_prompt}"
            )
        else:
            raw_price = safe_float(context.user_data.get('vehicle_price', 0.0))
            offered_fuel = _clean_val(context.user_data.get('listing_fuel_type'))
            fuel_line = f"⛽ <b>{fuel_label}:</b> {html.escape(offered_fuel)}\n" if offered_fuel else ""

            if req_mmy != default_req_fallback:
                req_spec_line = f"🎯 <b>{desired_spec_label}:</b> {html.escape(req_mmy)}\n"
            else:
                req_spec_line = ""

            prompt_msg = (
                f"💬 <b>{title_text}</b>\n"
                f"────────────────\n"
                f"{req_spec_line}"
                f"🚜 <b>{offered_spec_label}:</b> {html.escape(offered_mmy)}\n"
                f"{fuel_line}"
                f"🏷 <b>{asking_rate_label}:</b> <code>{raw_price:,.2f} {currency}</code>\n"
                f"────────────────\n"
                f"{reply_prompt}"
            )

        # Clean message dispatch avoiding message duplication
        try:
            if query and query.message:
                if getattr(query.message, 'photo', None):
                    try:
                        await query.edit_message_caption(caption=prompt_msg, parse_mode="HTML")
                    except Exception:
                        await context.bot.send_message(chat_id=update.effective_chat.id, text=prompt_msg,
                                                       parse_mode="HTML")
                else:
                    try:
                        await query.edit_message_text(text=prompt_msg, parse_mode="HTML")
                    except Exception:
                        await context.bot.send_message(chat_id=update.effective_chat.id, text=prompt_msg,
                                                       parse_mode="HTML")
            else:
                await context.bot.send_message(chat_id=update.effective_chat.id, text=prompt_msg, parse_mode="HTML")
        except Exception as send_err:
            logger.error(f"Failed to deliver custom offer prompt: {send_err}")

        return self.ENTERING_OFFER

    async def receive_custom_offer(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        if not update.message or not update.message.text:
            return self.ENTERING_OFFER

        clean_text = update.message.text.lower()
        for token in [',', ' ', 'etb', 'usd', 'eur', 'gbp', 'birr', 'ብር']:
            clean_text = clean_text.replace(token, '')
        clean_text = clean_text.strip()

        lang = str(context.user_data.get('language') or context.user_data.get('lang') or 'en').lower()

        try:
            custom_offer = float(clean_text)
            if custom_offer <= 0:
                raise ValueError
        except ValueError:
            if 'am' in lang:
                err_msg = "⚠️ <b>ልክ ያልሆነ መጠን!</b> እባክዎን ከዜሮ በላይ ትክክለኛ የቁጥር ዋጋ ያስገቡ።"
            elif 'om' in lang or 'or' in lang:
                err_msg = "⚠️ <b>Hanga Dogoggoraa!</b> Maaloo gatii sirrii 0 ol ta'e galchaa."
            else:
                err_msg = self._tx(context, 'invalid_amount') or (
                    "⚠️ <b>Invalid Amount!</b> Please enter a valid "
                    "numeric value greater than zero."
                )

            await update.message.reply_text(err_msg, parse_mode="HTML")
            return self.ENTERING_OFFER

        raw_curr = str(
            context.user_data.get('currency') or context.user_data.get('deal_currency') or 'ETB'
        ).upper().strip()
        clean_curr = raw_curr.split('/')[0] if '/' in raw_curr else raw_curr
        context.user_data['currency'] = clean_curr

        context.user_data['vehicle_price'] = custom_offer
        active_deal_id = str(
            context.user_data.get("active_vehicle_deal_id")
            or context.user_data.get("target_listing_id")
            or "pending"
        )

        if active_deal_id != "pending" and hasattr(self, 'db') and self.db:
            try:
                db_client = self.db.supabase if hasattr(self.db, 'supabase') else self.db
                deal_check = await db_client.table('deals').select('id').eq('id',
                                                                            active_deal_id).maybe_single().execute()
                if deal_check and deal_check.data:
                    await db_client.table('deals').update(
                        {'current_price': custom_offer, 'status': 'negotiating', 'currency': clean_curr}
                    ).eq('id', active_deal_id).execute()
            except Exception as e:
                logger.error(f"Error updating counter-offer price in DB: {e}")

        await self._process_deal_lock(update, context, active_deal_id, custom_offer)
        return ConversationHandler.END

    async def execute_vehicle_lock(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        query = update.callback_query
        if query:
            try:
                await query.answer()
            except Exception:
                pass

        match_id = query.data.split(":")[1] if (query and query.data and ":" in query.data) else context.user_data.get(
            'active_vehicle_deal_id')

        agreed_price = safe_float(
            context.user_data.get('vehicle_price')
            or context.user_data.get('asking_price')
            or context.user_data.get('listing_price')
            or context.user_data.get('target_price')
            or 0.0
        )

        raw_curr = str(
            context.user_data.get('currency') or context.user_data.get('deal_currency') or 'ETB'
        ).upper().strip()
        clean_curr = raw_curr.split('/')[0] if '/' in raw_curr else raw_curr
        context.user_data['currency'] = clean_curr

        if agreed_price <= 0.0:
            logger.warning(f"No valid price found for lock_vehicle_deal:{match_id}. Prompting custom offer.")
            return await self.prompt_custom_offer(update, context)

        await self._process_deal_lock(update, context, str(match_id), agreed_price)

        keys_to_clear = [
            "target_seeker_id",
            "active_flow",
            "is_reverse_match",
            "seeker_top_matches",
            "is_session_orphaned"
        ]
        for key in keys_to_clear:
            context.user_data.pop(key, None)

        return ConversationHandler.END

    async def _process_deal_lock(
            self,
            update: Update,
            context: ContextTypes.DEFAULT_TYPE,
            target_id: str,
            agreed_price: float
    ) -> int:
        callback_query = update.callback_query

        sender_lang = str(
            context.user_data.get('language') or context.user_data.get('lang') or 'en'
        ).lower()

        def get_t_lang(lan_key: str, lang: str = sender_lang, **kwargs) -> str:
            if hasattr(self, '_tx'):
                return self._tx(context, lan_key, lang=lang, **kwargs)
            return get_text(lang, lan_key, **kwargs)

        # Localized immediate processing feedback toast
        if 'am' in sender_lang:
            processing_toast = "⏳ በመካሄድ ላይ ነው... እባክዎ ይቆዩ"
        elif 'om' in sender_lang or 'or' in sender_lang:
            processing_toast = "⏳ Hojjetamaa jira... maaloo eegaa"
        else:
            processing_toast = "⏳ Processing... please wait"

        if callback_query:
            cb_id = callback_query.id
            if context.user_data.get('last_processed_deal_cb_id') == cb_id:
                logger.debug(f"ℹ Duplicate callback query {cb_id} detected. Skipping re-execution.")
                try:
                    await callback_query.answer()
                except Exception:
                    pass
                return await self.route_to_main_menu(update, context)

            context.user_data['last_processed_deal_cb_id'] = cb_id

            # Immediately give toast feedback on click to prevent user confusion and double clicks
            try:
                await callback_query.answer(text=processing_toast, show_alert=False)
            except Exception:
                pass

        def _md_to_html(text: str) -> str:
            if not text:
                return ""
            text = re.sub(r'\*(.*?)\*', r'<b>\1</b>', text)
            text = re.sub(r'_(.*?)_', r'<i>\1</i>', text)
            text = re.sub(r'(</b>|</i>)([^\s\n])', r'\1 \2', text)
            return text

        async def send_response(text: str):
            sanitized_text = _md_to_html(text)
            if callback_query and callback_query.message:
                try:
                    await callback_query.edit_message_caption(caption=sanitized_text, parse_mode="HTML")
                except Exception:
                    try:
                        await callback_query.edit_message_text(text=sanitized_text, parse_mode="HTML")
                    except Exception:
                        await context.bot.send_message(
                            chat_id=update.effective_chat.id, text=sanitized_text, parse_mode="HTML"
                        )
            else:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id, text=sanitized_text, parse_mode="HTML"
                )

        # Immediately update UI state to processing BEFORE executing DB queries
        await send_response(get_t_lang('transmitting', sender_lang))

        is_reverse = context.user_data.get('is_reverse_match', False)
        user_id = update.effective_user.id if update.effective_user else 0

        currency = str(
            context.user_data.get('currency')
            or context.user_data.get('deal_currency')
            or context.user_data.get('vehicle_currency')
            or 'ETB'
        ).upper()

        supabase_client = getattr(self, 'db', None)
        if hasattr(supabase_client, 'supabase'):
            supabase_client = supabase_client.supabase

        # Initialize base roles
        listing_id = target_id
        provider_id = safe_int(context.user_data.get("provider_id"))
        seeker_id = None

        # -----------------------------------------------------------------
        # 🛡️ PERMANENT FIX: Universal Database-First ID Resolution
        # -----------------------------------------------------------------
        active_deal_id = context.user_data.get("active_negotiation_id") or context.user_data.get("deal_id")
        if supabase_client and active_deal_id:
            try:
                deal_lookup = (
                    await supabase_client.table('deals')
                    .select('listing_id, provider_id, seeker_id')
                    .eq('id', str(active_deal_id))
                    .maybe_single()
                    .execute()
                )
                if deal_lookup and getattr(deal_lookup, 'data', None):
                    d_data = deal_lookup.data
                    if not listing_id or listing_id in ("None", "none", "", "pending"):
                        if d_data.get('listing_id'):
                            listing_id = d_data.get('listing_id')
                            context.user_data['listing_id'] = listing_id
                    if not provider_id and d_data.get('provider_id'):
                        provider_id = safe_int(d_data.get('provider_id'))
                        context.user_data['provider_id'] = provider_id
                    if d_data.get('seeker_id'):
                        seeker_id = safe_int(d_data.get('seeker_id'))
                        context.user_data['target_seeker_id'] = seeker_id
            except Exception as deal_err:
                logger.warning(f"Failed to auto-resolve IDs from active deal {active_deal_id}: {deal_err}")

        # Clean roles extraction with fallback safety
        if is_reverse:
            provider_id = user_id
            listing_id = listing_id if listing_id and listing_id not in ("None", "none", "", "pending") else (
                    context.user_data.get('last_created_listing_id') or context.user_data.get('active_vehicle_deal_id')
            )
            if not seeker_id:
                seeker_id = safe_int(context.user_data.get('target_seeker_id'))
        else:
            seeker_id = user_id or seeker_id
            provider_id = provider_id or safe_int(context.user_data.get('provider_id'))
            listing_id = listing_id or target_id

        # -----------------------------------------------------------------
        # 🛡️ PERMANENT FIX: Align with provider_listings & seeker_requests
        # -----------------------------------------------------------------
        if supabase_client and listing_id and listing_id not in ("None", "none", "", "pending"):
            try:
                # Step 1: Check if listing_id exists directly in provider_listings
                check_listing = (
                    await supabase_client
                    .table('provider_listings')
                    .select('id')
                    .eq('id', str(listing_id))
                    .maybe_single()
                    .execute()
                )

                if check_listing and getattr(check_listing, 'data', None):
                    # It is a valid listing ID
                    pass
                else:
                    # Step 2: Check if listing_id is actually a seeker_request ID (common in reverse matching)
                    check_request = (
                        await supabase_client
                        .table('seeker_requests')
                        .select('id, seeker_id')
                        .eq('id', str(listing_id))
                        .maybe_single()
                        .execute()
                    )

                    if check_request and getattr(check_request, 'data', None):
                        # It's a seeker request ID! Store it in request_id context
                        req_data = check_request.data
                        context.user_data['seeker_request_id'] = req_data.get('id')
                        if not seeker_id and req_data.get('seeker_id'):
                            seeker_id = safe_int(req_data.get('seeker_id'))
                            context.user_data['target_seeker_id'] = seeker_id

                        # Resolve a valid provider listing ID for the deals table foreign key
                        if provider_id:
                            latest_list = (
                                await supabase_client
                                .table('provider_listings')
                                .select('id')
                                .eq('provider_id', int(provider_id))
                                .order('created_at', desc=True)
                                .limit(1)
                                .execute()
                            )
                            if latest_list and getattr(latest_list, 'data', None):
                                listing_id = latest_list.data[0].get('id')
                                context.user_data['listing_id'] = listing_id
                    else:
                        # Step 3: Generic fallback to provider's most recent active listing
                        if provider_id:
                            latest_list = (
                                await supabase_client
                                .table('provider_listings')
                                .select('id')
                                .eq('provider_id', int(provider_id))
                                .order('created_at', desc=True)
                                .limit(1)
                                .execute()
                            )
                            if latest_list and getattr(latest_list, 'data', None):
                                listing_id = latest_list.data[0].get('id')
                                context.user_data['listing_id'] = listing_id
            except Exception as val_err:
                logger.warning(f"Failed to validate/align listing_id and request_id existence: {val_err}")

        # Guard against self-negotiation attempts
        if seeker_id and provider_id and int(seeker_id) == int(provider_id):
            self_error_msg = {
                "en": "⚠️ <b>Action Denied:</b> You cannot initiate a negotiation on your own asset listing.",
                "am": "⚠️ <b>ክዋኔው አልተፈቀደም፦</b> በራስዎ የንብረት ማስታወቂያ ላይ ውድር መጀመር አይችሉም።",
                "om": "⚠️ <b>Gochi Kun Dhorkamaadha:</b> Qabeenya mataa keessanii irratti daldala jalqabuu hin dandeessan."
            }.get(sender_lang[:2],
                  "⚠️ <b>Action Denied:</b> You cannot initiate a negotiation on your own asset listing.")
            await send_response(self_error_msg)
            return await self.route_to_main_menu(update, context)

        def _format_mmy_strict(data: Dict[str, Any], default: Optional[str] = None) -> Optional[str]:
            """
            Extracts and formats make/model/year from record dictionaries across provider_listings,
            seeker_requests, and deals, preserving uppercase acronyms (e.g., MCI, BMW, CAT, J4500).
            """
            if not data or not isinstance(data, dict):
                return default

            # Tier 1: Check full descriptive make/model/year and specialized asset title fields
            full_title_keys = [
                'make_model_year', 'preferred_make_model_year', 'equipment_type',
                'make_model', 'title', 'asset_title', 'asset_name'
            ]
            for k in full_title_keys:
                title_val = _clean_val(data.get(k))
                if title_val:
                    return format_title_preserve_acronyms(title_val)

            # Tier 2: Construct from individual brand/make, model, and year fields
            brand = _clean_val(data.get('brand') or data.get('make') or data.get('preferred_brand'))
            model = _clean_val(data.get('model'))
            year = _clean_val(data.get('year'))

            constructed = " ".join(filter(None, [brand, model, year])).strip()
            cleaned = _clean_val(constructed)
            if cleaned:
                return format_title_preserve_acronyms(cleaned)

            # Tier 3: Category or vehicle_type descriptors
            v_type = _clean_val(data.get('vehicle_type') or data.get('property_use') or data.get('category'))
            if v_type:
                return format_title_preserve_acronyms(v_type)

            return default

        # -----------------------------------------------------------------
        # 1. IN-MEMORY CONTEXT EXTRACTION & ACRONYM PRESERVATION
        # -----------------------------------------------------------------
        raw_list_mmy = _clean_val(
            context.user_data.get('listing_make_model_year') or
            context.user_data.get('provider_make_model_year') or
            context.user_data.get('selected_listing_title') or
            context.user_data.get('asset_title') or
            (context.user_data.get('make_model_year') if is_reverse else None)
        )
        list_mmy = format_title_preserve_acronyms(raw_list_mmy) if raw_list_mmy else None

        list_fuel_raw = _clean_val(
            context.user_data.get('listing_fuel_type') or
            context.user_data.get('provider_fuel_type') or
            (context.user_data.get('fuel_type') if is_reverse else None)
        )

        raw_pref_mmy = _clean_val(
            context.user_data.get('preferred_make_model_year') or
            context.user_data.get('vehicle_pref') or
            context.user_data.get('search_query') or
            (context.user_data.get('make_model_year') if not is_reverse else None)
        )
        pref_mmy = format_title_preserve_acronyms(raw_pref_mmy) if raw_pref_mmy else None

        pref_fuel_raw = _clean_val(
            context.user_data.get('preferred_fuel_type') or
            (context.user_data.get('fuel_type') if not is_reverse else None)
        )

        raw_transaction_type = (
                context.user_data.get('transaction_type') or
                context.user_data.get('deal_transaction_type') or
                'sell'
        )

        # -----------------------------------------------------------------
        # 2. DATABASE ENTITY RESOLUTION & ENRICHMENT
        # -----------------------------------------------------------------
        l_data = {}
        r_data = {}
        try:
            if supabase_client and target_id and target_id != "pending":

                # Match Proposal Resolution Fallback (If target_id points to a match proposal)
                resolved_listing_id = listing_id
                resolved_request_id = target_id if is_reverse else None

                if target_id and not target_id.isdigit():
                    try:
                        mp_res = await (
                            supabase_client
                            .table('match_proposals')
                            .select('*')
                            .eq('id', target_id)
                            .maybe_single()
                            .execute()
                        )

                        if mp_res and mp_res.data:
                            mp_data = mp_res.data
                            resolved_listing_id = mp_data.get('listing_id') or resolved_listing_id
                            resolved_request_id = mp_data.get('request_id') or resolved_request_id
                            if mp_data.get('provider_id') and not provider_id:
                                provider_id = mp_data['provider_id']
                            if mp_data.get('seeker_id') and not seeker_id:
                                seeker_id = mp_data['seeker_id']
                    except Exception as mp_err:
                        logger.debug(f"Target ID {target_id} is not a match_proposal: {mp_err}")

                # Fetch Provider Listing Data
                active_list_id = resolved_listing_id or listing_id
                if active_list_id and active_list_id != "pending":
                    list_res = await (
                        supabase_client
                        .table('provider_listings')
                        .select('*')
                        .eq('id', active_list_id)
                        .maybe_single()
                        .execute()
                    )

                    if list_res and list_res.data:
                        l_data = list_res.data
                        if l_data.get('currency'):
                            currency = str(l_data['currency']).upper()
                            context.user_data['currency'] = currency
                        if l_data.get('transaction_type'):
                            raw_transaction_type = l_data['transaction_type']
                        if l_data.get('provider_id'):
                            provider_id = l_data['provider_id']

                        db_list_mmy = _format_mmy_strict(l_data)
                        if db_list_mmy:
                            list_mmy = db_list_mmy

                        db_list_fuel = _clean_val(l_data.get('fuel_type'))
                        if db_list_fuel:
                            list_fuel_raw = db_list_fuel

                # Fetch Seeker Request Data
                active_req_id = resolved_request_id or (target_id if is_reverse else None)
                if active_req_id and active_req_id != "pending":
                    req_res = await (
                        supabase_client
                        .table('seeker_requests')
                        .select('*')
                        .eq('id', active_req_id)
                        .maybe_single()
                        .execute()
                    )

                    if req_res and req_res.data:
                        r_data = req_res.data
                        if r_data.get('currency'):
                            currency = str(r_data['currency']).upper()
                            context.user_data['currency'] = currency
                        if r_data.get('transaction_type'):
                            raw_transaction_type = r_data['transaction_type']
                        if r_data.get('seeker_id'):
                            seeker_id = r_data['seeker_id']

                        db_pref_mmy = _format_mmy_strict(r_data)
                        if db_pref_mmy:
                            pref_mmy = db_pref_mmy

                        db_pref_fuel = _clean_val(r_data.get('fuel_type'))
                        if db_pref_fuel:
                            pref_fuel_raw = db_pref_fuel

        except Exception as fetch_err:
            logger.warning(f"Failed to enrich deal lock specs: {fetch_err}")

        # Fallback lookup for Seeker Request by Seeker ID if pref_mmy is still missing
        if not pref_mmy and seeker_id:
            try:
                req_lookup = await supabase_client.table('seeker_requests').select('*').eq(
                    'seeker_id', int(seeker_id)
                ).order('created_at', desc=True).limit(1).maybe_single().execute()

                if req_lookup and req_lookup.data:
                    r_data = req_lookup.data
                    s_pref_mmy = _format_mmy_strict(r_data)
                    if s_pref_mmy:
                        pref_mmy = s_pref_mmy
                    if not pref_fuel_raw:
                        pref_fuel_raw = _clean_val(r_data.get('fuel_type'))
            except Exception as e:
                logger.warning(f"Could not fetch seeker request fallback: {e}")

        # Fallback lookup for Provider Listing by Provider ID if list_mmy is still missing
        if not list_mmy and provider_id:
            try:
                list_lookup = await supabase_client.table('provider_listings').select('*').eq(
                    'provider_id', int(provider_id)
                ).order('created_at', desc=True).limit(1).maybe_single().execute()

                if list_lookup and list_lookup.data:
                    l_data = list_lookup.data
                    db_list_mmy = _format_mmy_strict(l_data)
                    if db_list_mmy:
                        list_mmy = db_list_mmy
                    if not list_fuel_raw:
                        list_fuel_raw = _clean_val(l_data.get('fuel_type'))
            except Exception as l_err:
                logger.warning(f"Could not fetch provider listing fallback: {l_err}")

        if is_reverse and (not seeker_id or not listing_id):
            await send_response(get_t_lang('err_seeker_listing', sender_lang))
            return await self.route_to_main_menu(update, context)
        elif not is_reverse and not provider_id:
            await send_response(get_t_lang('err_provider', sender_lang))
            return await self.route_to_main_menu(update, context)

        recipient_id = int(seeker_id if is_reverse else provider_id)
        recipient_lang = 'en'

        if supabase_client:
            try:
                user_res = await supabase_client.table('users').select('language').eq(
                    'telegram_id', recipient_id
                ).maybe_single().execute()
                if user_res and user_res.data and user_res.data.get('language'):
                    recipient_lang = str(user_res.data['language']).lower()
            except Exception as lang_err:
                logger.warning(f"Failed to fetch language preference for user {recipient_id}: {lang_err}")

        # Explicit fallback labels matching recipient language
        if 'am' in recipient_lang:
            pref_mmy_final = pref_mmy or "የመደበኛ ተሽከርካሪ ጥያቄ"
            list_mmy_final = list_mmy or "የተዘረዘረ ተሽከርካሪ"
            deal_type_label = "የግብይት ዓይነት"
        elif 'om' in recipient_lang or 'or' in recipient_lang:
            pref_mmy_final = pref_mmy or "Gaaffii Konkolaataa Idilee"
            list_mmy_final = list_mmy or "Konkolaataa Tarreeffame"
            deal_type_label = "Gosa Daldalaa"
        else:
            pref_mmy_final = pref_mmy or "Standard Vehicle Request"
            list_mmy_final = list_mmy or "Listed Vehicle"
            deal_type_label = "Transaction Type"

        # Enforce acronym capitalization across all target strings
        pref_mmy_final = format_title_preserve_acronyms(pref_mmy_final)
        list_mmy_final = format_title_preserve_acronyms(list_mmy_final)

        # Normalize transaction type
        norm_tx_type = str(raw_transaction_type).lower().strip()
        if norm_tx_type in ('sell', 'sale'):
            deal_type_val = "ሽያጭ" if 'am' in recipient_lang else (
                "Gurgurtaa" if 'om' in recipient_lang or 'or' in recipient_lang else "Sell")
        elif norm_tx_type in ('rental', 'rent'):
            deal_type_val = "ኪራይ" if 'am' in recipient_lang else (
                "Kiraa" if 'om' in recipient_lang or 'or' in recipient_lang else "Rent / Hire")
        elif norm_tx_type == 'service':
            deal_type_val = "አገልግሎት" if 'am' in recipient_lang else (
                "Tajaajila" if 'om' in recipient_lang or 'or' in recipient_lang else "Service")
        else:
            deal_type_val = format_title_preserve_acronyms(str(raw_transaction_type))

        # Format fuel display targeting recipient_lang
        if hasattr(self, '_format_fuel_display'):
            pref_fuel_fmt = self._format_fuel_display(pref_fuel_raw, lang=recipient_lang)
            list_fuel_fmt = self._format_fuel_display(list_fuel_raw, lang=recipient_lang)
        else:
            pref_fuel_fmt = pref_fuel_raw
            list_fuel_fmt = list_fuel_raw

        # Extract location_text from provider listing and seeker request tables
        listing_location = _clean_val(l_data.get('location_text') or l_data.get('location'))
        seeker_location = _clean_val(
            r_data.get('location_text') or r_data.get('location') or context.user_data.get('location_text'))

        try:
            raw_asset_title = list_mmy_final or context.user_data.get('asset_title') or pref_mmy_final
            raw_asset_title = format_title_preserve_acronyms(raw_asset_title)

            deal_id_str = await self.deals_handler.initiate_negotiation(
                context=context,
                seeker_id=int(seeker_id),
                provider_id=int(provider_id),
                listing_id=listing_id,
                vertical_type="vehicle",
                initial_offer=agreed_price,
                asset_title=raw_asset_title,
                is_reverse=is_reverse,
                notify=False
            )

            if deal_id_str:
                if supabase_client:
                    try:
                        await supabase_client.table('deals').update({
                            'currency': currency,
                            'transaction_type': raw_transaction_type,
                            'asset_title': raw_asset_title
                        }).eq('id', deal_id_str).execute()
                    except Exception as update_err:
                        logger.warning(f"Could not update deal currency/tx_type: {update_err}")

                formatted_price = f"{agreed_price:,.2f} {currency}"

                seeker_fuel_display = f" ({html.escape(pref_fuel_fmt.strip())})" if pref_fuel_fmt else ""
                offered_fuel_display = f" ({html.escape(list_fuel_fmt.strip())})" if list_fuel_fmt else ""

                # Action buttons
                if 'am' in recipient_lang:
                    lbl_note = "ማስታወሻ: ከመጀመሪያው ጥያቄዎ ሊለያይ ስለሚችል በጥንቃቄ ይመርምሩ።"
                    lbl_action = "እባክዎ ለመመለስ ከታች ያለውን እርምጃ ይምረጡ:"
                    btn_accept = "✅ የተጠየቀውን ዋጋ ተቀበሉ"
                    btn_counter = "💬 የመደራደሪያ ዋጋዎን ያቅርቡ"
                    btn_decline = "❌ ውድቅ አድርግ"
                    label_seeker_loc = "የባለጥያቄው ቦታ"
                    label_provider_loc = "የንብረት/አቅራቢው ቦታ"
                elif 'om' in recipient_lang or 'or' in recipient_lang:
                    lbl_note = "Hubachiisa: Gaaffii keessan isa jalqabaa irraa adda ta'uu waan danda'uuf of " \
                               "eeggannoon sakatta'aa."
                    lbl_action = "Maaloo deebii kennuuf tarkaanfii armaan gadii filadhu:"
                    btn_accept = "✅ Gatii gaafatame fudhadhaa"
                    btn_counter = "💬 Gatii madaallii keessan dhiyeessaa"
                    btn_decline = "❌ Kuffisi"
                    label_seeker_loc = "Bakka Barbaadaa"
                    label_provider_loc = "Bakka Dhiheessaa"
                else:
                    lbl_note = "Note: Please review carefully as details may differ from your initial request."
                    lbl_action = "Please select an action below to respond:"
                    btn_accept = "✅ Accept Asking Price"
                    btn_counter = "💬 Counter"
                    btn_decline = "❌ Decline"
                    label_seeker_loc = "Seeker Location"
                    label_provider_loc = "Listing Location"

                keyboard = [
                    [
                        InlineKeyboardButton(btn_accept, callback_data=f"deal_accept:{deal_id_str}"),
                        InlineKeyboardButton(btn_counter, callback_data=f"deal_counter:{deal_id_str}")
                    ],
                    [InlineKeyboardButton(btn_decline, callback_data=f"deal_decline:{deal_id_str}")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)

                # 1. REVERSE MATCHING
                if is_reverse:
                    provider_msg = get_t_lang(
                        'offer_transmitted', sender_lang, formatted_price=formatted_price,
                        asset_title=html.escape(list_mmy_final)
                    )
                    await send_response(provider_msg)

                    if 'am' in recipient_lang:
                        notify_title = "አዲስ የቀረበ ተሽከርካሪ ዋጋ"
                        notify_desc = "አንድ አቅራቢ ለጥያቄዎ ተሽከርካሪ አቅርቧል!"
                        label_rate = "የቀረበው መጠን"
                        label_offered_asset = "የቀረበው ተሽከርካሪ"
                        label_seeker_req = "የባለጥያቄው ፍላጎት"
                    elif 'om' in recipient_lang or 'or' in recipient_lang:
                        notify_title = "Dhiyeessii Konkolaataa Haaraa"
                        notify_desc = "Dhiyeessaan tokko gaaffii keessaniif konkolaataa dhiyeesseera!"
                        label_rate = "Gatii Dhiyaate"
                        label_offered_asset = "Konkolaataa Dhiyaate"
                        label_seeker_req = "Fedhii Barbaadaa"
                    else:
                        notify_title = "New Vehicle Offer Received"
                        notify_desc = "A provider has submitted a vehicle proposal for your request!"
                        label_rate = "Offered Amount"
                        label_offered_asset = "Offered Vehicle"
                        label_seeker_req = "Seeker Requirement"

                    provider_loc_line = f"📍 <b>{label_provider_loc}:</b> {html.escape(listing_location)}\n" if \
                        listing_location else ""
                    seeker_loc_line = f"📍 <b>{label_seeker_loc}:</b> {html.escape(seeker_location)}\n" if \
                        seeker_location else ""

                    seeker_notify_msg = _md_to_html(
                        f"🔔 <b>{notify_title}</b>\n"
                        f"────────────────\n"
                        f"{notify_desc}\n"
                        f"🏷 <b>{deal_type_label}:</b> {deal_type_val}\n"
                        f"💰 <b>{label_rate}:</b> <code>{formatted_price}</code>\n"
                        f"📝 <b>{label_offered_asset}:</b> 🚗 <b>{html.escape(list_mmy_final)}</b>"
                        f"{offered_fuel_display}\n"
                        f"🎯 <b>{label_seeker_req}:</b> 🚙 <b>{html.escape(pref_mmy_final)}</b>{seeker_fuel_display}\n"
                        f"{provider_loc_line}"
                        f"{seeker_loc_line}"
                        f"────────────────\n"
                        f"💡 <b>{lbl_note}</b>\n"
                        f"{lbl_action}"
                    )

                    try:
                        # Robust Photo Extraction & Pre-signed URL Resolution for Vehicle Listing
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
                                            return parsed_json.get("url") or parsed_json.get(
                                                "file_id") or parsed_json.get("path")
                                    except Exception:
                                        pass
                                return cleaned if cleaned else None
                            if isinstance(item, dict):
                                return item.get("url") or item.get("file_id") or item.get("path") or item.get(
                                    "image_url")
                            return None

                        for key in ['photo_url', 'image_url', 'photo_file_id', 'images', 'photos', 'photo']:
                            val = l_data.get(key) or context.user_data.get(key)
                            if val:
                                photo_ref = _extract_url_or_id(val[0]) if isinstance(val, list) and len(
                                    val) > 0 else _extract_url_or_id(val)
                                if photo_ref:
                                    break

                        presigned_url = None
                        if photo_ref:
                            img_service = getattr(self, "image_service", getattr(self, "storage", None))
                            if img_service and hasattr(img_service, "get_presigned_url_async"):
                                try:
                                    presigned_url = await img_service.get_presigned_url_async(photo_ref)
                                except Exception as sign_err:
                                    logger.warning(
                                        f"Failed to generate pre-signed URL for vehicle photo '{photo_ref}': "
                                        f"{sign_err}")
                            else:
                                presigned_url = photo_ref

                        photo_dispatched = False
                        if presigned_url:
                            try:
                                await context.bot.send_photo(
                                    chat_id=int(seeker_id),
                                    photo=presigned_url,
                                    caption=seeker_notify_msg,
                                    parse_mode="HTML",
                                    reply_markup=reply_markup
                                )
                                photo_dispatched = True
                            except Exception as photo_err:
                                logger.warning(
                                    f"Failed to dispatch photo card to vehicle seeker {seeker_id}: {photo_err}")

                        if not photo_dispatched:
                            await context.bot.send_message(
                                chat_id=int(seeker_id),
                                text=seeker_notify_msg,
                                parse_mode="HTML",
                                reply_markup=reply_markup
                            )
                    except Exception as notify_err:
                        logger.error(f"Failed to notify vehicle seeker {seeker_id}: {notify_err}")

                # 2. FORWARD MATCHING
                else:
                    seeker_msg = get_t_lang(
                        'forward_offer_transmitted', sender_lang, formatted_price=formatted_price,
                        asset_title=html.escape(raw_asset_title)
                    )
                    await send_response(seeker_msg)

                    if 'am' in recipient_lang:
                        notify_title = "በተሽከርካሪዎ ላይ አዲስ ዋጋ ቀርቧል"
                        notify_desc = "አንድ ባለጥያቄ በተሽከርካሪዎ ዝርዝር ላይ ዋጋ አቅርቧል!"
                        label_rate = "የቀረበው መጠን"
                        label_listed_asset = "የእርስዎ የተዘረዘረ ተሽከርካሪ"
                        label_seeker_req = "የባለጥያቄው ፍላጎት"
                    elif 'om' in recipient_lang or 'or' in recipient_lang:
                        notify_title = "Gatii Haaraa Dhiyaateera"
                        notify_desc = "Barbaadaan tokko tarree konkolaataa keessan irratti gatii dhiyeesseera!"
                        label_rate = "Gatii Dhiyaate"
                        label_listed_asset = "Konkolaataa Keessan Tarreeffame"
                        label_seeker_req = "Fedhii Barbaadaa"
                    else:
                        notify_title = "New Price Offer Received"
                        notify_desc = "A seeker has made a price offer on your vehicle listing!"
                        label_rate = "Offered Amount"
                        label_listed_asset = "Your Listed Vehicle"
                        label_seeker_req = "Seeker Requirement"

                    provider_loc_line = f"📍 <b>{label_provider_loc}:</b> {html.escape(listing_location)}\n" if \
                        listing_location else ""
                    seeker_loc_line = f"📍 <b>{label_seeker_loc}:</b> {html.escape(seeker_location)}\n" if \
                        seeker_location else ""

                    provider_notify_msg = _md_to_html(
                        f"🔔 <b>{notify_title}</b>\n"
                        f"────────────────\n"
                        f"{notify_desc}\n"
                        f"🏷️ <b>{deal_type_label}:</b> {deal_type_val}\n"
                        f"💰 <b>{label_rate}:</b> <code>{formatted_price}</code>\n"
                        f"📝 <b>{label_listed_asset}:</b> 🚗 <b>{html.escape(list_mmy_final)}</b>"
                        f"{offered_fuel_display}\n"
                        f"🎯 <b>{label_seeker_req}:</b> 🚙 <b>{html.escape(pref_mmy_final)}</b>{seeker_fuel_display}\n"
                        f"{provider_loc_line}"
                        f"{seeker_loc_line}"
                        f"────────────────\n"
                        f"💡 <b>{lbl_note}</b>\n"
                        f"{lbl_action}"
                    )

                    try:
                        await context.bot.send_message(
                            chat_id=int(provider_id),
                            text=provider_notify_msg,
                            parse_mode="HTML",
                            reply_markup=reply_markup
                        )
                    except Exception as notify_err:
                        logger.error(f"Failed to notify provider {provider_id}: {notify_err}")
            else:
                await send_response(get_t_lang('deal_blocked', sender_lang))

        except Exception as e:
            logger.error(f"Unexpected error in bidirectional negotiation: {str(e)}", exc_info=True)
            await send_response(get_t_lang('system_err', sender_lang))

        context.user_data.pop('is_reverse_match', None)
        return await self.route_to_main_menu(update, context)

    # ==========================================
    # ESCAPE PATHS
    # ==========================================

    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        cancel_msg = "🛑 <b>Process cancelled.</b> Returning to the marketplace..."
        chat_id = update.effective_chat.id if update.effective_chat else None

        if update.callback_query:
            query = update.callback_query
            try:
                await query.answer()
            except Exception:
                pass

            try:
                await query.edit_message_text(text=cancel_msg, parse_mode="HTML")
            except BadRequest as edit_err:
                err_msg = str(edit_err).lower()
                if "no text in the message" in err_msg or "message to edit" in err_msg:
                    try:
                        await query.edit_message_caption(caption=cancel_msg, parse_mode="HTML")
                    except BadRequest:
                        if chat_id:
                            await context.bot.send_message(chat_id=chat_id, text=cancel_msg, parse_mode="HTML")
                else:
                    if chat_id:
                        await context.bot.send_message(chat_id=chat_id, text=cancel_msg, parse_mode="HTML")
        elif update.message:
            await update.message.reply_text(cancel_msg, parse_mode="HTML")

        if chat_id:
            logger.info(f"User {chat_id} triggered cancellation. Routing to main menu.")

        return await self.route_to_main_menu(update, context)

    async def exit_conversation_for_deal(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """
        Exits vehicle conversation flow and forwards deal callbacks to the global DealsHandler.
        Converted from staticmethod to instance method to allow direct delegation to self.deals_handler.
        """
        user_id = update.effective_user.id if update.effective_user else "unknown"
        logger.info(f"Exiting vehicle conversation state for user {user_id} for deal callback execution.")

        # Forward callback handling directly to DealsHandler if attached to instance
        if hasattr(self, 'deals_handler') and self.deals_handler:
            if hasattr(self.deals_handler, 'handle_deal_callback'):
                await self.deals_handler.handle_deal_callback(update, context)
            elif hasattr(self.deals_handler, 'process_deal_callback'):
                await self.deals_handler.process_deal_callback(update, context)

        return ConversationHandler.END

    def get_conversation_handler(self) -> ConversationHandler:
        from src.smartmatch.handlers.main_menu import MainMenuHandler

        return ConversationHandler(
            entry_points=[
                CommandHandler('list_vehicle', self.start_list_vehicle),
                CommandHandler('find_vehicle', self.start_find_vehicle),
                MessageHandler(
                    filters.Text(MainMenuHandler.get_labels_for_action("btn_list_vehicle")),
                    self.start_list_vehicle
                ),
                MessageHandler(
                    filters.Text(MainMenuHandler.get_labels_for_action("btn_find_vehicle")),
                    self.start_find_vehicle
                ),
                CallbackQueryHandler(self.prompt_initial_offer, pattern=r'^vehicle_offer:'),
                MessageHandler(
                    filters.Text(MainMenuHandler.get_labels_for_action("LIST_VEHICLE")),
                    self.start_list_vehicle
                ),
                MessageHandler(
                    filters.Text(MainMenuHandler.get_labels_for_action("FIND_VEHICLE")),
                    self.start_find_vehicle
                ),
                # Key Fix: Intercept deal callback triggers in entry_points when conversation state is None
                CallbackQueryHandler(
                    self.exit_conversation_for_deal,
                    pattern=r'^deal_(accept|counter|decline):'
                )
            ],
            states={
                self.AWAITING_VEHICLE_OFFER: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.process_initial_offer),
                    CallbackQueryHandler(self.cancel, pattern=r'^cancel_vertical_offer$')
                ],
                self.TRANSACTION_TYPE: [
                    CallbackQueryHandler(self.handle_transaction_type, pattern=r'^(find_trans_|list_trans_|trans_)')
                ],
                self.VEHICLE_TYPE: [
                    CallbackQueryHandler(self.handle_vehicle_type, pattern=r'^vtype_')
                ],
                self.MAKE_MODEL_YEAR: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_make_model_year)
                ],
                self.CONDITION_YEARS: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_condition_years)
                ],
                self.FUEL_TYPE: [
                    CallbackQueryHandler(self.handle_fuel_type, pattern=r'^fuel_')
                ],
                self.FEATURE_SPECS: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_feature_specs)
                ],
                self.LOCATION_DATA: [
                    MessageHandler(filters.LOCATION, self.handle_location_data),
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_location_data)
                ],
                self.PRICE_BUDGET: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_price_budget)
                ],
                self.PHOTO_UPLOAD: [
                    MessageHandler(
                        filters.ATTACHMENT | (filters.TEXT & ~filters.COMMAND),
                        self.handle_photo_upload
                    )
                ],
                self.CONTACT_INFO: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_contact_info)
                ],
                self.AWAITING_MATCH_SELECTION: [
                    # Consolidated pattern matching all match selection patterns
                    CallbackQueryHandler(
                        self.handle_match_selection,
                        pattern=r"^select_(seeker_|vehicle_)?match:"
                    ),
                    CallbackQueryHandler(self.prompt_custom_offer, pattern=r"^prompt_offer:"),
                    CallbackQueryHandler(self.execute_vehicle_lock, pattern=r"^lock_vehicle_deal:"),
                    CallbackQueryHandler(self.handle_back_to_top_matches, pattern=r"^back_to_top_matches$"),
                    # Supports both cancel_vehicle_deal and cancel_match callback triggers
                    CallbackQueryHandler(self.cancel, pattern=r"^cancel_(vehicle_deal|match)$"),
                ],
                self.ENTERING_OFFER: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.receive_custom_offer),
                    CallbackQueryHandler(self.cancel, pattern=r"^cancel_(vehicle_deal|match)$")
                ]
            },
            fallbacks=[
                CommandHandler('cancel', self.cancel),
                CallbackQueryHandler(self.cancel, pattern=r'^cancel_'),
                CallbackQueryHandler(self.exit_conversation_for_deal, pattern=r'^deal_(accept|counter|decline):')
            ],
            allow_reentry=True,
            per_user=True,
            per_chat=True,
            per_message=False,
            name="vehicle_conversation_flow",
            persistent=True
        )
