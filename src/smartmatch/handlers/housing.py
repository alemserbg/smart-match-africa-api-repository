import logging
import uuid
import html
import json
import asyncio
import re
from difflib import SequenceMatcher
from typing import Any, Optional
from datetime import datetime, timezone

from deep_translator import GoogleTranslator
from telegram.error import BadRequest, TelegramError, NetworkError, TimedOut
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters
)

# Import translation gateway
from src.smartmatch.utils.translations import get_text

# Production telemetry logger for the real estate domain
logger = logging.getLogger("smartmatch.handlers.housing")

# ==========================================
# LEXICON & TRANSLATION NORMALIZATION ENGINE
# ==========================================

REGIONAL_HOUSING_LEXICON = {
    "mana jireenyaa": "Residential House",
    "mana kireeffamu": "Rental House",
    "mana gurguramu": "House for Sale",
    "viilaa": "Villa",
    "apaartaamaa": "Apartment",
    "kondomiiniyaam": "Condominium",
    "mana daldalaa": "Commercial Property",
    "dookkaana": "Commercial Shop",
    "kutaa ciree": "Studio Apartment",
    "mana baadiyyaa": "Rural Property",
    "finfinnee": "Addis Ababa",
    "adaamaa": "Adama",
    "bisnooftuu": "Bishoftu",
    "awaasaa": "Hawassa",
    "jimmaa": "Jimma",
    "dasee": "Dese",
    "assoosaa": "Assosa",
    "matakal": "Metekel"
}

ETHIOPIAN_LOCATIONS = {
    "gudar": "Guder", "adaamaa": "Adama", "addis ababa": "Addis Ababa",
    "finfinne": "Addis Ababa", "adama": "Adama", "nazret": "Adama",
    "bishoftu": "Bishoftu", "debre zeyit": "Bishoftu", "hawassa": "Hawassa",
    "awasa": "Hawassa", "jimma": "Jimma", "jima": "Jimma",
    "mekelle": "Mekelle", "mekele": "Mekelle", "dire dawa": "Dire Dawa",
    "bahir dar": "Bahir Dar", "gondar": "Gondar", "gonder": "Gondar"
}


def safe_float(val: Any, default: float = 0.0) -> float:
    if val is None:
        return default
    try:
        clean_val = str(val).replace(',', '').strip()
        return float(clean_val) if clean_val else default
    except (ValueError, TypeError):
        return default


def apply_city_fixes(location: Optional[str]) -> Optional[str]:
    if not location or not isinstance(location, str):
        return None

    cleaned = location.strip()
    raw_parts = [p.strip() for p in re.split(r'[,\u1363]+', cleaned) if p.strip()]

    if not raw_parts:
        return cleaned if cleaned else None

    # Normalize each part individually against city/subcity dictionaries
    # while retaining the full hierarchical structure.
    fixed_parts = []
    for part in raw_parts:
        lower_part = part.lower()
        if lower_part in ETHIOPIAN_LOCATIONS:
            fixed_parts.append(ETHIOPIAN_LOCATIONS[lower_part])
        else:
            fixed_parts.append(part.title())

    return ", ".join(fixed_parts)


def translate_property_type(raw_type: str, lang: str = "en") -> str:
    """
    Translates raw housing/property types into English, Amharic, or Afaan Oromo.
    """
    if not raw_type:
        return ""

    clean_key = str(raw_type).strip().lower()

    # Seerri jijjiirraa 'residential_plot' fi 'commercial_plot' gara space qabutti (underscore hiikuu)
    if clean_key == "residential_plot":
        clean_key = "residential plot"
    elif clean_key == "commercial_plot":
        clean_key = "commercial plot"

    translations = {
        "commercial": {
            "en": "Commercial",
            "am": "የንግድ ቤት",
            "om": "Daldala"
        },
        "residential": {
            "en": "Residential",
            "am": "መኖሪያ ቤት",
            "om": "Mana Jireenyaa"
        },
        "commercial plot": {
            "en": "Commercial Plot",
            "am": "የንግድ መሬት",
            "om": "Lafa Daldalaa"
        },
        "residential plot": {
            "en": "Residential Plot",
            "am": "የመኖሪያ መሬት",
            "om": "Lafa Jireenyaa"
        },
        "plot": {
            "en": "Plot / Land",
            "am": "ቦታ / መሬት",
            "om": "Lafa"
        },
        "land": {
            "en": "Plot / Land",
            "am": "ቦታ / መሬት",
            "om": "Lafa"
        }
    }

    if clean_key in translations:
        return translations[clean_key].get(lang, translations[clean_key]["en"])

    # Partial substring check for terms like "Commercial Building" or "Residential Villa"
    if "commercial plot" in clean_key:
        return translations["commercial plot"].get(lang, "Commercial Plot")
    if "residential plot" in clean_key:
        return translations["residential plot"].get(lang, "Residential Plot")
    if "commercial" in clean_key:
        return translations["commercial"].get(lang, "Commercial")
    if "residential" in clean_key:
        return translations["residential"].get(lang, "Residential")

    return str(raw_type).title()


def safe_int(value: Any, default: int = 0) -> int:
    """Module-level safe integer parser for user IDs and numerical IDs."""
    if value is None:
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


class HousingHandler:
    """
    Production-Grade Real Estate & Housing Component for Smart Match Africa.
    Handles intake for complex matching vectors while dynamically branching
    between residential assets and commercial spaces.
    Fully localized and integrated with Backblaze B2 and Chapa checkout.
    """
    # Finite State Machine Constants
    (
        AWAITING_HOUSING_OFFER,
        AWAITING_COUNTER_OFFER,
        NEGOTIATION_MENU,
        LISTING_TYPE,
        PROPERTY_USE,
        LOCATION_DATA,
        PRICE_BUDGET,
        SIZE_SPECS,
        AMENITIES_DATA,
        PHOTO_UPLOAD,
        BULK_QUANTITY,
        CONTACT_INFO,
        SELECTING_MATCH,
        ENTERING_OFFER
    ) = range(14)

    def __init__(
            self,
            db_manager: Any,
            ai_engine: Any,
            matching_engine: Any,
            billing_service: Any,
            image_service: Any,
            deals_handler: Any,
            queue_manager: Any
    ):
        """
        Thread-safe constructor managing system resource dependencies via injection.
        """
        self.db = db_manager
        self.ai = ai_engine
        self.matching = matching_engine
        self.billing = billing_service
        self.image_service = image_service
        self.storage = image_service
        self.deals_handler = deals_handler
        self.deals = deals_handler
        self.queue = queue_manager
        self.logger = logger

    @classmethod
    def _get_lang(cls, context: ContextTypes.DEFAULT_TYPE) -> str:
        """Resolves the user's localized language code. Defaults to English."""
        return context.user_data.get("language", "en")

    async def handle_deal_counter(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        query = update.callback_query
        if query:
            await query.answer()

        cb_data = query.data if query else ""
        parts = cb_data.split(":")
        deal_id = parts[1] if len(parts) > 1 else ""

        context.user_data['active_deal_id'] = deal_id
        context.user_data['active_housing_deal_id'] = deal_id
        context.user_data['is_counter_offer'] = True

        # ✅ Ensure listing_id is properly backed up or resolved if missing in session
        database = getattr(self, 'db', getattr(self, 'db_manager', None))
        if database and not context.user_data.get('last_created_listing_id'):
            try:
                # Try to fetch listing_id from the deals table using the deal_id
                deal_res = await database.table('deals').select('listing_id, provider_id, seeker_id').eq('id',
                                                                                                         deal_id).maybe_single().execute()
                if deal_res and getattr(deal_res, 'data', None):
                    deal_data = deal_res.data
                    if deal_data.get('listing_id'):
                        context.user_data['last_created_listing_id'] = deal_data.get('listing_id')
                    if deal_data.get('provider_id'):
                        context.user_data['provider_id'] = deal_data.get('provider_id')
                    if deal_data.get('seeker_id'):
                        context.user_data['target_seeker_id'] = deal_data.get('seeker_id')
            except Exception as e:
                logger.warning(f"Failed to auto-resolve deal context in handle_deal_counter: {e}")

        lang = self._get_lang(context)
        prompt_texts = {
            "en": "📉 <b>Enter Your Counter-Offer Amount:</b>\n\nPlease type the numerical rate/price you would like to propose:",
            "am": "📉 <b>የሐሳብ ዋጋዎን ያስገቡ፡</b>\n\nእባክዎን ማቅረብ የሚፈልጉትን አዲስ ዋጋ በቁጥር ይፃፉ፡",
            "om": "📉 <b>Gatii Yaada Deebii Keessan Galchaa:</b>\n\nMaaloo gatii dhiheessuu barbaaddan lakkoofsaan barreessaa:"
        }
        msg = prompt_texts.get(lang, prompt_texts["en"])

        if query and query.message:
            try:
                await query.edit_message_text(text=msg, parse_mode="HTML")
            except Exception:
                await context.bot.send_message(chat_id=update.effective_chat.id, text=msg, parse_mode="HTML")
        else:
            await update.effective_chat.send_message(text=msg, parse_mode="HTML")

        return self.ENTERING_OFFER

    async def handle_deal_accept(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        query = update.callback_query
        if query:
            await query.answer()

        cb_data = query.data if query else ""
        parts = cb_data.split(":")
        deal_id_str = parts[1] if len(parts) > 1 else ""

        if not deal_id_str:
            logger.error("❌ Deal Accept Error: deal_id could not be extracted from callback data.")
            return await self.route_to_main_menu(update, context)

        # 1. Fetch the deal record from Supabase to pass into execute_deal_acceptance
        database = getattr(self, 'db', getattr(self, 'db_manager', None))
        supabase_client = getattr(database, 'supabase', None) if database else None

        deal_record = None
        if supabase_client:
            try:
                res = await supabase_client.table('deals').select('*').eq('id', deal_id_str).maybe_single().execute()
                if res and getattr(res, 'data', None):
                    deal_record = res.data
            except Exception as e:
                logger.error(f"Failed to fetch deal {deal_id_str} for acceptance: {e}")

        if not deal_record:
            lang = self._get_lang(context)
            err_msg = {
                "en": "⚠️ Deal record not found or expired.",
                "am": "⚠️ የስምምነት መዝገብ አልተገኘም ወይም ጊዜው አልፏል።",
                "om": "⚠️ Galmeen waliigaltee hin argamne ykn yeroon darbeera."
            }.get(lang, "⚠️ Deal record not found or expired.")
            if query and query.message:
                try:
                    await query.edit_message_text(text=err_msg, parse_mode="HTML")
                except Exception:
                    pass
            return await self.route_to_main_menu(update, context)

        # 2. Seamlessly endorse/delegate the execution to execute_deal_acceptance from deals_handler.py
        deals_module = getattr(self, "deals", getattr(self, "deals_handler", self))
        if hasattr(deals_module, "execute_deal_acceptance"):
            return await deals_module.execute_deal_acceptance(update, context, deal_id_str, deal_record)
        else:
            logger.error("❌ execute_deal_acceptance method missing on deals handler.")
            return await self.route_to_main_menu(update, context)

    async def handle_deal_decline(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        query = update.callback_query
        if query:
            await query.answer("❌ Deal declined.")

        cb_data = query.data if query else ""
        parts = cb_data.split(":")
        deal_id_str = parts[1] if len(parts) > 1 else ""

        # Optional: Update deal state in database to 'declined' if desired
        supabase_client = getattr(getattr(self, 'db', None), 'supabase', None)
        if supabase_client and deal_id_str:
            try:
                await supabase_client.table('deals').update({
                    'deal_state': 'declined',
                    'status': 'declined',
                    'updated_at': datetime.now(timezone.utc).isoformat()
                }).eq('id', deal_id_str).execute()
            except Exception as e:
                logger.warning(f"Failed to mark deal {deal_id_str} as declined in DB: {e}")

        lang = self._get_lang(context)
        decline_texts = {
            "en": "❌ <b>Deal Declined</b>\n\nYou have successfully declined this offer.",
            "am": "❌ <b>ውል ተሰርዟል / ውድቅ ተደርጓል</b>\n\nይህንን ማቅረቢያ በተሳካ ሁኔታ ውድቅ አድርገውታል።",
            "om": "❌ <b>Waliigaltichi Didameera</b>\n\nDhiheessii kanamilkaa'inaan ditteesseetta."
        }
        msg = decline_texts.get(lang, decline_texts["en"])

        if query and query.message:
            try:
                await query.edit_message_text(text=msg, parse_mode="HTML")
            except Exception:
                await context.bot.send_message(chat_id=update.effective_chat.id, text=msg, parse_mode="HTML")

        return await self.route_to_main_menu(update, context)

    # ==========================================
    # INTER-MODULE LINKS & ROUTING
    # ==========================================
    @classmethod
    async def route_to_main_menu(cls, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """
        Flushes workflow session data while preserving user preferences
        and re-instantiates the main menu hub.
        """
        temp_flow_keys = [
            'housing_top_matches',
            'seeker_top_matches',
            'is_reverse_match',
            'active_housing_deal_id',
            'housing_price',
            'provider_id',
            'target_seeker_id',
            'last_created_listing_id',
            'asset_title',
            'listing_type',
            'property_use',
            'location_data',
            'price_budget',
            'bulk_quantity'
        ]
        for key in temp_flow_keys:
            context.user_data.pop(key, None)

        lang = cls._get_lang(context)

        if update.callback_query:
            try:
                await update.callback_query.answer()
            except Exception:
                pass

        menu_handler = context.bot_data.get("main_menu_handler")
        if menu_handler and hasattr(menu_handler, "show_main_menu"):
            return await menu_handler.show_main_menu(update, context)

        fallback_msg = (
                get_text(lang, "msg_return_root") or
                "🏠 <b>Main Menu</b>\n\nSelect an option below to continue:"
        )

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

    @staticmethod
    async def safe_reply(
            update: Update,
            context: ContextTypes.DEFAULT_TYPE,
            text: str,
            retries: int = 3,
            **kwargs
    ):
        """
        Safely dispatches a reply message with exponential backoff retries
        to prevent network dropouts from crashing conversation handlers.
        """
        effective_msg = update.effective_message
        chat_id = update.effective_chat.id if update.effective_chat else None

        for attempt in range(1, retries + 1):
            try:
                if effective_msg:
                    return await effective_msg.reply_text(text, **kwargs)
                elif chat_id:
                    return await context.bot.send_message(chat_id=chat_id, text=text, **kwargs)
                else:
                    logger.error("Unable to dispatch reply: Update lacks valid chat context.")
                    return None
            except (NetworkError, TimedOut) as err:
                logger.warning(
                    f"⚠️ Network dispatch attempt {attempt}/{retries} failed in housing handler: {err}"
                )
                if attempt == retries:
                    raise err
                # Correct exponential backoff delay calculation (1.0s, 2.0s, 4.0s...)
                await asyncio.sleep(1.0 * (2 ** (attempt - 1)))
            except Exception as e:
                logger.warning(f"⚠️ Unexpected dispatch error on attempt {attempt}/{retries}: {e}")
                if attempt == retries:
                    raise e
                await asyncio.sleep(1.0 * (2 ** (attempt - 1)))

    # ==========================================
    # 1. APPLICATION ENTRY PATHWAYS
    # ==========================================
    async def start_list_house(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        query = update.callback_query
        if query:
            try:
                await query.answer()
            except Exception:
                pass

        context.user_data.clear()
        context.user_data['flow'] = 'list'
        lang = self._get_lang(context)

        keyboard = [
            [
                InlineKeyboardButton(get_text(lang, "btn_rent_out"), callback_data="action_rent"),
                InlineKeyboardButton(get_text(lang, "btn_sell"), callback_data="action_sell")
            ],
            [InlineKeyboardButton(get_text(lang, "btn_return_main"), callback_data="cancel_to_main")]
        ]

        text = get_text(lang, "msg_list_house_prompt")
        reply_markup = InlineKeyboardMarkup(keyboard)

        # Edit existing message if triggered via button callback; reply if triggered via command/text
        if query:
            try:
                await query.edit_message_text(text, reply_markup=reply_markup, parse_mode="Markdown")
            except Exception:
                await query.edit_message_text(text, reply_markup=reply_markup)
        elif update.message:
            await self.safe_reply(update, context, text, reply_markup=reply_markup, parse_mode="Markdown")

        return self.LISTING_TYPE

    async def start_find_house(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        query = update.callback_query
        if query:
            try:
                await query.answer()
            except Exception:
                pass

        context.user_data.clear()
        context.user_data['flow'] = 'find'
        lang = self._get_lang(context)

        keyboard = [
            [
                InlineKeyboardButton(get_text(lang, "btn_rent_in"), callback_data="action_rent"),
                InlineKeyboardButton(get_text(lang, "btn_buy"), callback_data="action_buy")
            ],
            [InlineKeyboardButton(get_text(lang, "btn_return_main"), callback_data="cancel_to_main")]
        ]

        text = get_text(lang, "msg_find_house_prompt")
        reply_markup = InlineKeyboardMarkup(keyboard)

        # Edit existing message if triggered via button callback; reply if triggered via command/text
        if query:
            try:
                await query.edit_message_text(text, reply_markup=reply_markup, parse_mode="Markdown")
            except Exception:
                await query.edit_message_text(text, reply_markup=reply_markup)
        elif update.message:
            await self.safe_reply(update, context, text, reply_markup=reply_markup, parse_mode="Markdown")

        return self.LISTING_TYPE

    # ==========================================
    # 2. SECTOR CLASSIFICATION & DATA INGESTION
    # ==========================================
    async def handle_listing_type(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        query = update.callback_query
        if query:
            try:
                await query.answer()
            except Exception:
                pass

        lang = self._get_lang(context)

        if not query or not query.data:
            return self.LISTING_TYPE

        if query.data == "cancel_to_main":
            return await self.route_to_main_menu(update, context)

        action_type = query.data.split('_', 1)[1] if '_' in query.data else query.data
        context.user_data['listing_type'] = action_type

        keyboard = [
            [InlineKeyboardButton(get_text(lang, "btn_residential"), callback_data="use_residential")],
            [InlineKeyboardButton(get_text(lang, "btn_commercial"), callback_data="use_commercial")],
            [InlineKeyboardButton(get_text(lang, "btn_residential_plot"), callback_data="use_residential_plot")],
            [InlineKeyboardButton(get_text(lang, "btn_commercial_plot"), callback_data="use_commercial_plot")],
            [InlineKeyboardButton(get_text(lang, "btn_return_main"), callback_data="cancel_to_main")]
        ]

        try:
            await query.edit_message_text(
                get_text(lang, "msg_property_sector"),
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML"
            )
        except Exception:
            # Fallback without parse_mode if formatting fails
            await query.edit_message_text(
                get_text(lang, "msg_property_sector"),
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

        return self.PROPERTY_USE

    async def handle_property_use(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        query = update.callback_query
        if query:
            try:
                await query.answer()
            except Exception:
                pass

        lang = self._get_lang(context)

        if not query or not query.data:
            return self.PROPERTY_USE

        if query.data == "cancel_to_main":
            return await self.route_to_main_menu(update, context)

        # Extracts 'residential', 'commercial', 'residential_plot', or 'commercial_plot'
        property_use = query.data.split('_', 1)[1] if '_' in query.data else query.data

        # ── FOOYYA'IINSA BARBAADAMU: 'residential_plot' fi 'commercial_plot' gara space qabutti jijjiiruu ──
        if property_use == "residential_plot":
            property_use = "residential plot"
        elif property_use == "commercial_plot":
            property_use = "commercial plot"

        context.user_data['property_use'] = property_use
        flow = context.user_data.get('flow', 'find')

        prompt = get_text(lang, "msg_location_list") if flow == 'list' else get_text(lang, "msg_location_find")

        try:
            await query.edit_message_text(prompt, parse_mode="HTML")
        except Exception:
            # Prevents uncaught parse errors from breaking conversation state transitions
            await query.edit_message_text(prompt)

        return self.LOCATION_DATA

    async def handle_location_data(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """
        Processes GPS coordinates or complex multi-level text location inputs.
        Seamlessly parses standard (,) and Ethiopic (၊) comma-separated location hierarchies.
        """
        lang = self._get_lang(context)
        effective_msg = update.effective_message

        # Resilient dispatch helper leveraging safe_reply
        async def send_response(text_payload: str, **kwargs):
            await self.safe_reply(update, context, text_payload, **kwargs)

        try:
            # 1. Handle native Telegram GPS Location share
            if effective_msg and effective_msg.location:
                context.user_data['latitude'] = effective_msg.location.latitude
                context.user_data['longitude'] = effective_msg.location.longitude
                context.user_data[
                    'location_text'] = f"GPS: {effective_msg.location.latitude}, {effective_msg.location.longitude}"

            # 2. Handle Text Location Input (including Amharic Ethiopic comma hierarchies)
            elif effective_msg and effective_msg.text:
                raw_text = effective_msg.text.strip()

                # Regex split on both standard commas (,) and Ethiopic commas (၊ \u1363)
                raw_parts = re.split(r'[,\u1363]+', raw_text)
                location_parts = [part.strip() for part in raw_parts if part.strip()]

                # Reconstruct normalized string format: "City, Subcity, Kebele, Neighborhood"
                clean_location_text = ", ".join(location_parts) if location_parts else raw_text

                if len(clean_location_text) < 3:
                    err_msg = (
                            get_text(lang, "err_location_short")
                            or "⚠️ Location details are too short. Please specify city, subcity, or neighborhood."
                    )
                    await send_response(err_msg)
                    return getattr(self, "LOCATION_DATA", 1)

                context.user_data['latitude'] = None
                context.user_data['longitude'] = None

                # Apply global city normalizations safely
                try:
                    if 'apply_city_fixes' in globals():
                        context.user_data['location_text'] = apply_city_fixes(clean_location_text)
                    elif hasattr(self, 'apply_city_fixes'):
                        context.user_data['location_text'] = self.apply_city_fixes(clean_location_text)
                    else:
                        context.user_data['location_text'] = clean_location_text
                except Exception as fix_err:
                    logger.warning(f"City fix normalization skipped: {fix_err}")
                    context.user_data['location_text'] = clean_location_text

                # Granular structural breakdown for database & search indexing
                if location_parts:
                    context.user_data['city'] = location_parts[0]
                    if len(location_parts) > 1:
                        context.user_data['subcity'] = location_parts[1]
                    if len(location_parts) > 2:
                        context.user_data['kebele'] = location_parts[2]
                    if len(location_parts) > 3:
                        context.user_data['neighborhood'] = " ".join(location_parts[3:])

            else:
                err_msg = (
                        get_text(lang, "err_location_short")
                        or "⚠️ Invalid location input. Please send text or GPS location."
                )
                await send_response(err_msg)
                return getattr(self, "LOCATION_DATA", 1)

            flow = context.user_data.get('flow')
            prompt = get_text(lang, "msg_price_list") if flow == 'list' else get_text(lang, "msg_budget_find")
            if not prompt or "[msg_price_list]" in prompt or "[msg_budget_find]" in prompt:
                prompt = "💵 Please state your price or budget (e.g., 25000 ETB or 500 USD):"

            await send_response(prompt, parse_mode="Markdown")
            return getattr(self, "PRICE_BUDGET", 2)

        except Exception as err:
            logger.error(
                f"Unexpected error in handle_location_data for text '{getattr(effective_msg, 'text', '')}': {err}",
                exc_info=True)
            err_msg = "⚠️ An unexpected error occurred while processing your location. Please try sending it again."
            await send_response(err_msg)
            return getattr(self, "LOCATION_DATA", 1)

    async def handle_price_budget(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        lang = self._get_lang(context)
        effective_msg = update.effective_message

        # Resilient dispatch helper leveraging safe_reply
        async def send_response(text_payload: str, **kwargs):
            await self.safe_reply(update, context, text_payload, **kwargs)

        try:
            if not effective_msg or not effective_msg.text:
                if lang == "om":
                    err_msg = "⚠️ Lakkoofsi galchitan sirrii miti. Maaloo gatii ykn baajata keessan irra deebi'anii galchaa (fkn. 25,000 ETB ykn 500 USD):"
                elif lang == "am":
                    err_msg = "⚠️ ያስገቡት ዋጋ ትክክል አይደለም። እባክዎ ዋጋዎን ወይም በጀትዎን እንደገና ያስገቡ (ለምሳሌ 25,000 ETB ወይም 500 USD):"
                else:
                    err_msg = "⚠️ Invalid price or budget. Please re-enter your price or budget (e.g., 25,000 ETB or 500 USD):"

                await send_response(err_msg)
                return self.PRICE_BUDGET

            text = effective_msg.text.strip()
            upper_text = text.upper()

            # 1. Detect currency preference from the input text or fall back to existing context/ETB
            if "$" in text or "USD" in upper_text:
                currency = "USD"
            elif "ETB" in upper_text or "ብር" in text or "QARSHII" in upper_text:
                currency = "ETB"
            else:
                currency = context.user_data.get('currency', 'ETB')

            # 2. Clean numerical value by stripping currency symbols, labels, and commas
            raw_val = (
                text.replace('$', '')
                .replace('USD', '').replace('usd', '')
                .replace('ETB', '').replace('etb', '').replace('birr', '').replace('Birr', '')
                .replace('ብር', '')
                .replace('Qarshii', '').replace('qarshii', '').replace('QARSHII', '')
                .replace(',', '')
                .strip()
            )

            try:
                amount = float(raw_val)
                if amount <= 0:
                    raise ValueError
            except ValueError:
                if lang == "om":
                    err_msg = "⚠️ Gatiin galchitan lakkoofsa sirrii ol'aanaa ta'uu qaba. Maaloo gatii keessan irra deebi'anii galchaa:"
                elif lang == "am":
                    err_msg = "⚠️ ያስገቡት ዋጋ ከዜሮ በላይ መሆን አለበት። እባክዎ ዋጋዎን እንደገና ያስገቡ:"
                else:
                    err_msg = "⚠️ Please enter a valid positive number. Please re-enter your price or budget:"

                await send_response(err_msg)
                return self.PRICE_BUDGET

            flow = context.user_data.get('flow')
            property_use = context.user_data.get('property_use')

            # 3. Store both amount and currency in user_data for Supabase persistence
            context.user_data['currency'] = currency
            if flow == 'list':
                context.user_data['price'] = amount
            else:
                context.user_data['max_budget'] = amount

            if property_use == 'residential':
                prompt = get_text(lang, "msg_beds_list") if flow == 'list' else get_text(lang, "msg_beds_find")
                if not prompt or "[msg_beds_list]" in prompt or "[msg_beds_find]" in prompt:
                    prompt = "🛏️ Please enter the number of bedrooms:"
            else:
                prompt = get_text(lang, "msg_sqm_list") if flow == 'list' else get_text(lang, "msg_sqm_find")
                if not prompt or "[msg_sqm_list]" in prompt or "[msg_sqm_find]" in prompt:
                    prompt = "📐 Please enter the total size in square meters (m²):"

            await send_response(prompt, parse_mode="Markdown")
            return self.SIZE_SPECS

        except Exception as err:
            logger.error(f"Unexpected error in handle_price_budget: {err}", exc_info=True)
            if lang == "om":
                err_msg = "⚠️ Dogoggorri uumameera. Maaloo gatii ykn baajata keessan irra deebi'anii galchaa:"
            elif lang == "am":
                err_msg = "⚠️ ስህተት አጋጥሟል። እባክዎ ዋጋዎን ወይም በጀትዎን እንደገና ያስገቡ:"
            else:
                err_msg = "⚠️ An unexpected error occurred. Please re-enter your price or budget:"

            await send_response(err_msg)
            return self.PRICE_BUDGET

    async def handle_size_specs(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """
        Handles property area/size inputs.
        Seamlessly extracts numerical values from inputs containing English or Amharic
        area units such as 'ካሬ', 'ካሬ ሜትር', 'ካ.ሜ', 'ካ/ሜ', 'm2', 'sqm', etc.
        """
        lang = self._get_lang(context)
        effective_msg = update.effective_message

        # Resilient dispatch helper leveraging safe_reply
        async def send_response(text_payload: str, **kwargs):
            await self.safe_reply(update, context, text_payload, **kwargs)

        try:
            if not effective_msg or not effective_msg.text:
                err_msg = (
                        get_text(lang, "err_invalid_whole_number")
                        or "⚠️ Please enter a valid number (e.g., 150, 200 ካሬ, 100 ካሬ ሜትር)."
                )
                await send_response(err_msg)
                return getattr(self, "SIZE_SPECS", 3)

            raw_text = effective_msg.text.strip()
            # Remove thousand separators (e.g., "1,500 ካሬ" -> "1500 ካሬ")
            cleaned_text = raw_text.replace(',', '')

            # Regex search extracts first positive integer or decimal value from input string
            match = re.search(r'\d+(?:\.\d+)?', cleaned_text)

            if not match:
                err_msg = (
                        get_text(lang, "err_invalid_whole_number")
                        or "⚠️ Please enter a valid numerical size (e.g., 150, 200 ካሬ)."
                )
                await send_response(err_msg)
                return getattr(self, "SIZE_SPECS", 3)

            # Convert extracted digits to integer value safely
            size_val = int(round(float(match.group())))

            if size_val <= 0:
                err_msg = (
                        get_text(lang, "err_invalid_whole_number")
                        or "⚠️ Please enter a valid number greater than zero."
                )
                await send_response(err_msg)
                return getattr(self, "SIZE_SPECS", 3)

            flow = context.user_data.get('flow')
            property_use = context.user_data.get('property_use')

            if property_use == 'residential':
                bedroom_key = 'number_of_bedrooms' if flow == 'list' else 'min_bedrooms'

                if bedroom_key not in context.user_data:
                    context.user_data[bedroom_key] = size_val
                    prompt = get_text(lang, "msg_sqm_list") if flow == 'list' else get_text(lang, "msg_sqm_find")
                    if not prompt or "[msg_sqm_list]" in prompt or "[msg_sqm_find]" in prompt:
                        prompt = "What is the total land/property area in square meters (m² / ካሬ ሜትር)?"
                    await send_response(prompt, parse_mode="Markdown")
                    return getattr(self, "SIZE_SPECS", 3)
                else:
                    if flow == 'list':
                        context.user_data['sq_m'] = size_val
                    else:
                        context.user_data['min_sq_m'] = size_val
            else:
                if flow == 'list':
                    context.user_data['sq_m'] = size_val
                else:
                    context.user_data['min_sq_m'] = size_val

            prompt = get_text(lang, "msg_amenities_list") if flow == 'list' else get_text(lang, "msg_amenities_find")
            if not prompt or "[msg_amenities_list]" in prompt or "[msg_amenities_find]" in prompt:
                prompt = "List any key amenities separated by commas (or type 'None'):"

            await send_response(prompt, parse_mode="Markdown")
            return getattr(self, "AMENITIES_DATA", 4)

        except Exception as err:
            logger.error(f"Error in handle_size_specs: {err}", exc_info=True)
            err_net = {
                "en": "⚠️ Connection timeout occurred. Please re-enter your size specification:",
                "am": "⚠️ የግንኙነት መቆራረጥ አጋጥሟል። እባክዎ መጠኑን እንደገና ያስገቡ:",
                "om": "⚠️ Rakkoo walqunnamtiitu mudate. Maaloo safkuu keessaa irra deebi'anii galchaa:"
            }.get(lang, "⚠️ Connection timeout occurred. Please re-enter your size specification:")
            await send_response(err_net)
            return getattr(self, "SIZE_SPECS", 3)

    async def handle_amenities_data(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        lang = self._get_lang(context)
        effective_msg = update.effective_message

        try:
            text = effective_msg.text.strip() if effective_msg and effective_msg.text else ""
            flow = context.user_data.get('flow')
            none_keyword = (get_text(lang, "keyword_none") or "none").lower()

            if flow == 'list':
                context.user_data['amenities'] = (
                    [a.strip() for a in text.split(',') if a.strip()]
                    if text.lower() != none_keyword else []
                )
                await self.safe_reply(update, context, get_text(lang, "msg_photo_prompt"), parse_mode="Markdown")
                return self.PHOTO_UPLOAD
            else:
                context.user_data['must_have_amenities'] = (
                    [a.strip() for a in text.split(',') if a.strip()]
                    if text.lower() != none_keyword else []
                )
                await self.safe_reply(update, context, get_text(lang, "msg_contact_find"), parse_mode="Markdown")
                return self.CONTACT_INFO

        except Exception as err:
            logger.error(f"Error in handle_amenities_data: {err}", exc_info=True)
            await self.safe_reply(update, context,
                                  "⚠️ An unexpected error occurred. Please try entering your details again.")
            return getattr(self, "AMENITIES", 1)  # Keep user safely in the current state

    # ==========================================
    # 5. MEDIA UPLOAD PIPELINE
    # ==========================================
    async def handle_photo_upload(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        lang = self._get_lang(context)
        skip_keyword = (get_text(lang, "keyword_skip") or "skip").lower()
        flow = context.user_data.get('flow')
        effective_msg = update.effective_message

        # Resilient dispatch helper leveraging safe_reply
        async def send_response(text_payload: str, **kwargs):
            await self.safe_reply(update, context, text_payload, **kwargs)

        try:
            if effective_msg and effective_msg.photo:
                progress_msg = None
                upload_text = get_text(lang, "msg_uploading_b2") or "⏳ Uploading photo..."
                try:
                    progress_msg = await self.safe_reply(update, context, upload_text)
                except Exception:
                    pass

                try:
                    # 1. Safely retrieve and download file bytes with network timeout protection
                    file = await effective_msg.photo[-1].get_file()
                    file_bytes = await file.download_as_bytearray()
                    filename = f"real_estate/{uuid.uuid4().hex[:12]}.jpg"

                    storage_svc = getattr(self, "storage", getattr(self, "image_service", None))
                    if storage_svc and hasattr(storage_svc, "upload_buffer"):
                        s3_url = await storage_svc.upload_buffer(file_bytes, filename)
                    else:
                        s3_url = None

                    context.user_data['asset_photo_url'] = s3_url
                    context.user_data['photo_url'] = s3_url

                    if progress_msg:
                        try:
                            await progress_msg.edit_text(
                                get_text(lang, "msg_photo_success") or "✅ Photo uploaded successfully!")
                        except Exception:
                            pass

                except Exception as net_err:
                    logger.error(f"Network error during photo download/upload: {net_err}", exc_info=True)
                    context.user_data['asset_photo_url'] = None
                    context.user_data['photo_url'] = None

                    err_timeout_msg = (
                            get_text(lang, "msg_photo_timeout")
                            or "⚠️ Photo upload timed out or failed. Please try sending the photo again or type 'skip'."
                    )
                    if progress_msg:
                        try:
                            await progress_msg.edit_text(err_timeout_msg)
                        except Exception:
                            await send_response(err_timeout_msg)
                    else:
                        await send_response(err_timeout_msg)

                    return getattr(self, "PHOTO_UPLOAD", 8)
            else:
                msg_text = effective_msg.text.lower() if effective_msg and effective_msg.text else ""
                if msg_text and msg_text not in ['skip', skip_keyword, 'kuta']:
                    err_invalid = get_text(lang,
                                           "err_photo_invalid") or "⚠️ Invalid input. Please send a photo or type 'skip'."
                    await send_response(err_invalid)
                    return getattr(self, "PHOTO_UPLOAD", 8)

                context.user_data['asset_photo_url'] = None
                context.user_data['photo_url'] = None

            # Route to next conversation step based on flow type
            if flow == 'list':
                prompt = get_text(lang,
                                  "msg_bulk_qty") or "How many identical units are you registering? (Enter a number, e.g., 1)"
                await send_response(prompt, parse_mode="Markdown")
                return getattr(self, "BULK_QUANTITY", 9)
            else:
                prompt = get_text(lang, "msg_contact_find") or "Please enter your contact phone number:"
                await send_response(prompt, parse_mode="Markdown")
                return getattr(self, "CONTACT_INFO", 10)

        except Exception as exc:
            logger.error(f"Unexpected error in handle_photo_upload: {exc}", exc_info=True)
            err_sys = "⚠️ An unexpected error occurred while processing your photo. Please try re-sending it or type 'skip'."
            await send_response(err_sys)
            return getattr(self, "PHOTO_UPLOAD", 8)

    async def handle_bulk_quantity(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Parses identical unit volume and routes to contact processing safely."""
        lang = self._get_lang(context)
        effective_msg = update.effective_message

        async def send_err(msg: str):
            await self.safe_reply(update, context, msg)

        try:
            if not effective_msg or not effective_msg.text:
                await send_err(get_text(lang, "err_invalid_whole_number") or "⚠️ Please enter a valid whole number.")
                return self.BULK_QUANTITY

            raw_val = effective_msg.text.strip()

            if not raw_val.isdigit() or int(raw_val) <= 0:
                await send_err(get_text(lang, "err_invalid_whole_number") or "⚠️ Please enter a valid whole number.")
                return self.BULK_QUANTITY

            context.user_data['bulk_quantity'] = int(raw_val)

            prompt_text = get_text(lang, "msg_contact_list") or "Please enter your contact phone number:"
            await self.safe_reply(update, context, prompt_text)

            return self.CONTACT_INFO

        except Exception as err:
            logger.error(f"Error in handle_bulk_quantity: {err}", exc_info=True)
            await send_err("⚠️ An unexpected error occurred. Please enter a valid whole number.")
            return self.BULK_QUANTITY

    # ==========================================
    # TERMINAL PERSISTENCE & MATCH ENGINE
    # ==========================================

    async def handle_contact_info(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        lang = self._get_lang(context)
        phone = update.message.text.strip() if update.message and update.message.text else ""

        if not phone.replace('+', '').isdigit() or len(phone) < 9:
            err_phone = get_text(lang,
                                 "err_invalid_phone") or "⚠️ Invalid phone format. Please enter a valid number:"
            await update.message.reply_text(err_phone)
            return self.CONTACT_INFO

        context.user_data['contact_phone'] = phone
        user_id = str(update.effective_user.id)
        flow = context.user_data.get('flow', 'find')

        processing_text = get_text(lang,
                                   "msg_processing_asset") or "⏳ Compiling property profile and syncing nodes..."
        processing_msg = await update.message.reply_text(processing_text)

        try:
            # 1. Defensive Data Normalization (Consistent keys)
            for numeric_key in ['price', 'max_budget', 'number_of_bedrooms', 'min_bedrooms', 'sq_m', 'min_sq_m',
                                'sq_meters', 'bulk_quantity', 'quantity']:
                if numeric_key in context.user_data and context.user_data[numeric_key] is not None:
                    context.user_data[numeric_key] = safe_float(context.user_data[numeric_key])

            # 2. SHARED DATA EXTRACTION (Ensures Seeker & Provider inputs are identical)
            raw_tx_type = str(
                context.user_data.get('listing_type', context.user_data.get('transaction_type', 'rent'))).lower()
            transaction_type_val = 'sale' if raw_tx_type in ['sell', 'buy', 'sale'] else 'rent'

            raw_prop_use = str(
                context.user_data.get('property_use', context.user_data.get('housing_type', 'Residential'))).strip()

            # ── FOOYYA'IINSA: Underscore qabaannoo isaa sirreessuuf ──
            if raw_prop_use.lower() == "residential_plot":
                raw_prop_use = "residential plot"
            elif raw_prop_use.lower() == "commercial_plot":
                raw_prop_use = "commercial plot"

            is_lexicon_match = False
            cleaned_lower = raw_prop_use.lower()
            if cleaned_lower in REGIONAL_HOUSING_LEXICON:
                raw_prop_use = REGIONAL_HOUSING_LEXICON[cleaned_lower]
                is_lexicon_match = True

            # Capture Full Hierarchy for Location data by taking raw input directly and applying city fixes
            raw_location_input = (
                    context.user_data.get('location_text') or
                    context.user_data.get('location') or
                    context.user_data.get('raw_location') or
                    "Not Specified"
            )
            location_val = apply_city_fixes(str(raw_location_input))

            latitude_val = safe_float(context.user_data.get('latitude')) or 0.0
            longitude_val = safe_float(context.user_data.get('longitude')) or 0.0
            currency_val = context.user_data.get('currency', 'ETB')
            quantity_val = int(context.user_data.get('bulk_quantity', context.user_data.get('quantity', 1)))

            # ----------------------------------------------------
            # CASE A: PROVIDER REGISTRATION (Property Owners)
            # ----------------------------------------------------
            if flow == 'list':
                raw_amenities = context.user_data.get('amenities', [])
                amenities_payload = ', '.join(raw_amenities) if isinstance(raw_amenities, list) else str(
                    raw_amenities or "")

                final_photo_url = context.user_data.get('photo_url') or context.user_data.get(
                    'asset_photo_url') or ""

                price_val = safe_float(context.user_data.get('price')) or 0.0
                sq_meters_val = safe_float(context.user_data.get('sq_meters', context.user_data.get('sq_m', 0.0)))
                beds_val = int(
                    safe_float(context.user_data.get('number_of_bedrooms', context.user_data.get('bedrooms', 0))))

                # Explicitly mapped to prevent payload pollution and NULL columns
                listing_data = {
                    "provider_id": user_id,
                    "vertical_type": "housing",
                    "transaction_type": transaction_type_val,
                    "property_use": raw_prop_use,
                    "location_text": location_val,
                    "latitude": latitude_val,
                    "longitude": longitude_val,
                    "price": price_val,
                    "currency": currency_val,
                    "bedrooms": beds_val,
                    "sq_meters": sq_meters_val,
                    "amenities": amenities_payload,
                    "photo_url": final_photo_url,
                    "contact_phone": phone,
                    "bulk_quantity": quantity_val,
                    "status": "active"
                }

                # AI Pipeline Translation & Normalization Safeguard
                if hasattr(self, 'ai') and self.ai and hasattr(self.ai, 'parse_listing_to_english'):
                    try:
                        # Optimized timeout (7.0s) for faster fallback response
                        english_parsed_data = await asyncio.wait_for(
                            self.ai.parse_listing_to_english(listing_data),
                            timeout=7.0
                        )

                        if isinstance(english_parsed_data, str):
                            match_json = re.search(r'\{.*}', english_parsed_data, re.DOTALL)
                            if match_json:
                                try:
                                    english_parsed_data = json.loads(match_json.group(0))
                                except json.JSONDecodeError:
                                    pass

                        if isinstance(english_parsed_data, dict) and english_parsed_data:
                            # SEPARATION OF CONCERN: Prevent AI from merging location into property_use
                            if title := english_parsed_data.get('title'):
                                if not is_lexicon_match:
                                    clean_title = str(title).title()
                                    loc_lower = listing_data['location_text'].lower()
                                    if loc_lower and loc_lower != "not specified":
                                        pattern = re.compile(rf'\b(?:in|at|near|around)\s+{re.escape(loc_lower)}\b',
                                                             re.IGNORECASE)
                                        clean_title = pattern.sub('', clean_title).strip()
                                        clean_title = clean_title.replace(listing_data['location_text'], '').strip()
                                    listing_data['property_use'] = clean_title if clean_title else listing_data[
                                        'property_use']

                            # Prevent AI from truncating the full hierarchical location string to a single leaf node
                            parsed_loc = english_parsed_data.get('location')
                            if parsed_loc and len(str(parsed_loc)) >= len(raw_location_input):
                                listing_data['location_text'] = apply_city_fixes(parsed_loc)
                            else:
                                listing_data['location_text'] = location_val  # Retains full hierarchy

                            if 'bedrooms' in english_parsed_data:
                                listing_data['bedrooms'] = int(safe_float(english_parsed_data['bedrooms']))

                            if 'sq_meters' in english_parsed_data or 'area' in english_parsed_data:
                                sq_val = english_parsed_data.get('sq_meters') or english_parsed_data.get('area')
                                listing_data['sq_meters'] = safe_float(sq_val)

                            specs = english_parsed_data.get('specifications', {})
                            if isinstance(specs, dict) and specs:
                                if spec_beds := (specs.get('bedrooms') or specs.get('beds')):
                                    listing_data['bedrooms'] = int(safe_float(spec_beds))
                                if spec_sq := (specs.get('sq_meters') or specs.get('area') or specs.get('size')):
                                    listing_data['sq_meters'] = safe_float(spec_sq)

                            desc = str(english_parsed_data.get('description', '')).strip()
                            amenities_parts = []
                            if desc and desc.lower() not in ['none', 'null', 'n/a']:
                                amenities_parts.append(desc)
                            if isinstance(specs, dict) and specs:
                                amenities_parts.append(f"Extracted Specs: {specs}")
                            if amenities_parts:
                                listing_data['amenities'] = " | ".join(amenities_parts)

                    except asyncio.TimeoutError:
                        # Downgraded from WARNING to DEBUG since local heuristics seamlessly take over
                        logger.debug("Housing AI parsing timedout (6s); using local heuristic fallback.")
                    except Exception as ai_err:
                        # Downgraded from WARNING to DEBUG to prevent log clutter during transient API errors
                        logger.debug(f"Housing AI parsing skipped due to transient error: {ai_err}")

                # Automatic Bedroom / Sq_M Fallback Extractor
                combined_search_text = f"{listing_data.get('property_use', '')} {listing_data.get('amenities', '')}"
                if listing_data.get('bedrooms', 0) == 0:
                    bed_match = re.search(r'(\d+)\s*(bed|bedroom|bedrooms|bdr|bdrm)\b', combined_search_text,
                                          re.IGNORECASE)
                    if bed_match:
                        listing_data['bedrooms'] = int(safe_float(bed_match.group(1)))

                if listing_data.get('sq_meters', 0.0) == 0.0:
                    sq_match = re.search(r'(\d+(?:\.\d+)?)\s*(sqm|sq m|sq meters|square meters|m2|m²)\b',
                                         combined_search_text, re.IGNORECASE)
                    if sq_match:
                        listing_data['sq_meters'] = safe_float(sq_match.group(1))

                # Concurrent Translation Safeguard
                try:
                    fields_to_check = ['property_use', 'location_text', 'amenities']

                    def safe_translate_sync(text: str) -> str:
                        if not text:
                            return text
                        cleaned_lower_lexicon = text.strip().lower()
                        if cleaned_lower_lexicon in REGIONAL_HOUSING_LEXICON:
                            return REGIONAL_HOUSING_LEXICON[cleaned_lower_lexicon]

                        has_non_ascii = any(ord(char) > 127 for char in text)
                        if not has_non_ascii and not any(
                                k in cleaned_lower_lexicon for k in ["mana", "viilaa", "kireeffamu"]):
                            return text

                        try:
                            lang_translator = GoogleTranslator(source='auto', target='en')
                            translation_result = lang_translator.translate(text)
                            if not translation_result or any(
                                    err in translation_result for err in ["Error 500", "Error 400", "Exception"]):
                                return text
                            return translation_result.split('(')[0].strip()
                        except Exception as transErr:
                            logger.error(f"Housing translation failure for '{text}': {transErr}")
                            return text

                    def translate_mixed_string(text: str) -> str:
                        if not text:
                            return text
                        if '|' in text:
                            parts = [p.strip() for p in text.split('|')]
                            return ' | '.join([safe_translate_sync(p) for p in parts])
                        return safe_translate_sync(text)

                    translation_tasks = []
                    fields_queued = []
                    for field in fields_to_check:
                        val = listing_data.get(field)
                        if val and isinstance(val, str):
                            if field == 'location_text':
                                # Keep full hierarchy string intact or apply known city structure fixes
                                continue
                            fields_queued.append(field)
                            translation_tasks.append(asyncio.to_thread(translate_mixed_string, val))

                    if translation_tasks:
                        results = await asyncio.gather(*translation_tasks, return_exceptions=True)
                        for field, res in zip(fields_queued, results):
                            if not isinstance(res, Exception) and res:
                                if field == 'property_use' and is_lexicon_match:
                                    continue
                                listing_data[field] = str(res)

                except Exception as safeguard_err:
                    logger.warning(f"Housing translation safeguard skipped: {safeguard_err}")

                # Persistence Layer
                listing_record = None
                if hasattr(self, 'db') and self.db:
                    for _ in range(listing_data['bulk_quantity']):
                        if hasattr(self.db, 'create_listing'):
                            listing_record = await self.db.create_listing(listing_data)
                        elif hasattr(self.db, 'create_provider_listing'):
                            listing_record = await self.db.create_provider_listing("provider_listings", user_id,
                                                                                   listing_data)

                        if not listing_record:
                            raise Exception("Supabase create_listing failed to return a record.")

                    logger.info(
                        f"{listing_data['bulk_quantity']} Housing listing(s) successfully created for User ID: {user_id}")

                    if hasattr(self, 'matching') and self.matching and hasattr(self.matching,
                                                                               'run_provider_matching') and listing_record:
                        await self.matching.run_provider_matching(listing_record.get('id'), "housing")

                await processing_msg.delete()

                if listing_record:
                    return await self.trigger_reverse_matching(update, context, listing_record)

                success_msg = get_text(lang,
                                       "msg_listing_success") or f"✅ {listing_data['bulk_quantity']} Property Asset(s) Successfully Registered!"
                await update.message.reply_text(success_msg, parse_mode="Markdown")
                return await self.route_to_main_menu(update, context)

            # ----------------------------------------------------
            # CASE B: SEEKER LIVE MATCHING (Renters / Buyers)
            # ----------------------------------------------------
            else:
                scan_msg = get_text(lang, "msg_scanning_housing_nodes") or "🔍 Scanning available properties..."
                await processing_msg.edit_text(scan_msg)

                # 1. Smart Local Heuristic Sanitization (Always runs as a reliable baseline)
                if raw_prop_use:
                    clean_seeker_use = str(raw_prop_use).strip().title()
                    loc_lower = str(location_val or "").lower()
                    if loc_lower and loc_lower not in ["not specified", "none", "n/a"]:
                        pattern = re.compile(rf'\b(?:in|at|near|around)\s+{re.escape(loc_lower)}\b', re.IGNORECASE)
                        clean_seeker_use = pattern.sub('', clean_seeker_use).strip()
                        clean_seeker_use = clean_seeker_use.replace(location_val, '').strip()
                    if clean_seeker_use:
                        raw_prop_use = clean_seeker_use

                # 2. Optional AI Enhancement with Graceful Fallback
                if hasattr(self, 'ai') and self.ai and hasattr(self.ai, 'parse_listing_to_english'):
                    try:
                        seeker_draft = {
                            "vertical_type": "housing",
                            "transaction_type": transaction_type_val,
                            "property_use": raw_prop_use,
                            "location_text": location_val
                        }
                        parsed_seeker = await asyncio.wait_for(
                            self.ai.parse_listing_to_english(seeker_draft),
                            timeout=8.0
                        )
                        if isinstance(parsed_seeker, dict) and parsed_seeker.get('title'):
                            ai_clean_title = str(parsed_seeker['title']).title()
                            loc_lower = str(location_val or "").lower()
                            if loc_lower and loc_lower not in ["not specified", "none", "n/a"]:
                                pattern = re.compile(rf'\b(?:in|at|near|around)\s+{re.escape(loc_lower)}\b',
                                                     re.IGNORECASE)
                                ai_clean_title = pattern.sub('', ai_clean_title).strip()
                                ai_clean_title = ai_clean_title.replace(location_val, '').strip()
                            if ai_clean_title:
                                raw_prop_use = ai_clean_title
                    except asyncio.TimeoutError:
                        logger.debug("Housing seeker AI parsing timed out (8s); using smart local fallback.")
                    except Exception as ai_err:
                        # Downgraded to DEBUG since the local heuristic fallback seamlessly takes over
                        logger.debug(f"Housing seeker AI parsing skipped due to transient API error: {ai_err}")

                parsed_max_budget = safe_float(
                    context.user_data.get('max_budget') or context.user_data.get('budget') or context.user_data.get(
                        'price')
                ) or 0.0
                min_beds = int(safe_float(
                    context.user_data.get('min_bedrooms', context.user_data.get('number_of_bedrooms', 0))
                ))
                min_sqm = safe_float(context.user_data.get('min_sq_m', context.user_data.get('sq_m', 0.0)))

                req_amenities = context.user_data.get('must_have_amenities', context.user_data.get('amenities', []))
                req_amenities_str = ', '.join(req_amenities) if isinstance(req_amenities, list) else str(
                    req_amenities or "")

                if hasattr(self, 'db') and self.db and hasattr(self.db, 'create_request'):
                    try:
                        req_payload = {
                            'seeker_id': user_id,
                            'vertical_type': 'housing',
                            'transaction_type': transaction_type_val,
                            'property_use': raw_prop_use,
                            'location_text': location_val,
                            'latitude': latitude_val,
                            'longitude': longitude_val,
                            'max_budget': parsed_max_budget,
                            'currency': currency_val,
                            'min_bedrooms': min_beds,
                            'min_sq_meters': min_sqm,
                            'required_amenities': req_amenities_str,
                            'contact_phone': phone,
                            'bulk_quantity': quantity_val,
                            'status': 'active'
                        }
                        await self.db.create_request(req_payload)
                    except Exception as db_err:
                        logger.error(f"Failed to persist housing seeker request: {db_err}", exc_info=True)

                candidates = []
                if hasattr(self, 'db') and self.db:
                    if hasattr(self.db, 'fetch_active_candidates'):
                        candidates = await self.db.fetch_active_candidates("housing", location_val)
                    elif hasattr(self.db, 'fetch_housing_candidates'):
                        candidates = await self.db.fetch_housing_candidates(
                            listing_type=transaction_type_val,
                            housing_type=raw_prop_use
                        )

                if not candidates and hasattr(self, 'db') and self.db and hasattr(self.db,
                                                                                  'fetch_active_candidates'):
                    candidates = await self.db.fetch_active_candidates("housing", "")

                # --- DYNAMIC DICTIONARY ALIGNMENT WITH HousingScorer ---
                prefs = dict(context.user_data)
                prefs['vertical_type'] = 'housing'
                prefs['housing_type'] = raw_prop_use
                prefs['property_use'] = raw_prop_use
                prefs['deal_type'] = transaction_type_val
                prefs['transaction_type'] = transaction_type_val
                prefs['max_budget'] = parsed_max_budget
                prefs['min_bedrooms'] = min_beds
                prefs['min_sq_meters'] = min_sqm
                prefs['min_sq_m'] = min_sqm
                prefs['location_text'] = location_val
                prefs['location'] = location_val
                prefs['currency'] = currency_val
                prefs['required_amenities'] = req_amenities

                candidates = [c for c in candidates if
                              str(c.get('user_id') or c.get('provider_id')) != str(user_id)]

                seen_ids = set()
                unique_candidates = []
                for c in candidates:
                    cid = c.get('id')
                    if cid not in seen_ids:
                        seen_ids.add(cid)
                        c['vertical_type'] = 'housing'
                        c['housing_type'] = c.get('housing_type') or c.get('property_use') or 'Residential'
                        c['property_use'] = c.get('property_use') or c['housing_type']
                        c['deal_type'] = c.get('deal_type') or c.get('transaction_type') or 'rent'
                        c['transaction_type'] = c['deal_type']
                        c['price'] = safe_float(c.get('price', c.get('cost', 0.0)))
                        c['location_text'] = str(c.get('location_text') or c.get('location') or '').strip()
                        c['location'] = c['location_text']
                        c['bedrooms'] = int(safe_float(c.get('bedrooms', c.get('number_of_bedrooms', 0))))
                        c['sq_meters'] = safe_float(c.get('sq_meters', c.get('sq_m', 0.0)))
                        c['sq_m'] = c['sq_meters']
                        unique_candidates.append(c)
                candidates = unique_candidates

                # Strict & Semantic Evaluation
                matches = []
                if hasattr(self, 'matching') and self.matching and hasattr(self.matching, 'evaluate_matches'):
                    matches = await self.matching.evaluate_matches('housing', prefs, candidates)

                if not matches and candidates and hasattr(self, 'matching') and self.matching and hasattr(
                        self.matching,
                        'evaluate_matches'):
                    logger.info("Strict housing matching returned 0 results. Triggering Semantic-Model Fallback...")
                    prefs['allow_semantic_fallback'] = True
                    prefs['housing_type'] = 'General'
                    if hasattr(self, 'db') and self.db and hasattr(self.db, 'fetch_active_candidates'):
                        broad_candidates = await self.db.fetch_active_candidates("housing", "")
                        if broad_candidates:
                            candidates = broad_candidates
                    matches = await self.matching.evaluate_matches('housing', prefs, candidates)

                if not matches:
                    broadcast_txt = get_text(lang,
                                             "msg_request_broadcasted") or "📢 No immediate housing match found. Request broadcasted."
                    await update.message.reply_text(broadcast_txt)
                    return await self.route_to_main_menu(update, context)

                matches = sorted(matches, key=lambda x: safe_float(x.get('_match_score', 0)), reverse=True)
                top_matches = matches[:3]

                localized_top_matches = []
                for match_item in top_matches:
                    if lang != 'en' and hasattr(self, 'ai') and self.ai and hasattr(self.ai,
                                                                                    'translate_asset_to_target_language'):
                        try:
                            localized = await asyncio.wait_for(
                                self.ai.translate_asset_to_target_language(match_item, lang),
                                timeout=10.0
                            )
                            localized_top_matches.append(localized if localized else match_item)
                        except Exception:
                            localized_top_matches.append(match_item)
                    else:
                        localized_top_matches.append(match_item)

                top_matches = localized_top_matches
                context.user_data['housing_top_matches'] = top_matches

                # Construct Localized Buttons for Top Matches
                keyboard = []
                msg = get_text(lang, "MSG_TOP_HOUSING_MATCHES", count=len(
                    top_matches)) or f"🎯 **Top {len(top_matches)} Housing Matches Found!**\n\nPlease select a property below to review and send an offer:"

                # Localized prepositions for 'in' across languages
                in_prep = {"en": "in", "am": "አካባቢ", "om": "iddoo"}.get(lang, "in")

                for i, match_item in enumerate(top_matches, start=1):
                    match_id = str(match_item.get('id', uuid.uuid4()))
                    raw_title = match_item.get('property_use') or match_item.get('housing_type') or 'Property'

                    # Localize property type (Commercial, Residential, Plots, etc.)
                    display_title = translate_property_type(raw_title, lang)

                    loc = match_item.get('location_text') or match_item.get('location') or 'Addis Ababa'
                    cost = safe_float(match_item.get('price', match_item.get('cost', 0.0)))
                    score = int(safe_float(match_item.get('_match_score', 95)))

                    if lang == "am":
                        button_label = f"🏠 #{i}: {display_title} በ {loc} ({cost:,.2f} ETB - {score}%)"
                    elif lang == "om":
                        button_label = f"🏠 #{i}: {display_title} {loc} keessatti ({cost:,.2f} ETB - {score}%)"
                    else:
                        button_label = f"🏠 #{i}: {display_title} in {loc} ({cost:,.2f} ETB - {score}%)"

                    keyboard.append(
                        [InlineKeyboardButton(button_label, callback_data=f"select_house_match:{match_id}")])

                btn_decline = get_text(lang, "BTN_DECLINE_RETURN") or "❌ Decline & Return"
                keyboard.append([InlineKeyboardButton(btn_decline, callback_data="cancel_match")])

                # Clean up temporary status message if present
                if 'processing_msg' in locals() and processing_msg:
                    try:
                        await processing_msg.delete()
                    except Exception:
                        pass

                # Network-resilient dispatch via safe_reply helper
                await self.safe_reply(
                    update,
                    context,
                    text=msg,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="Markdown"
                )
                return getattr(self, "SELECTING_MATCH", 99)

        except Exception as e:
            logger.error(f"Housing Pipeline failure: {e}", exc_info=True)

            # Retrieve language context safely
            lang = self._get_lang(context) if hasattr(self, '_get_lang') else context.user_data.get('lang', 'en')
            err_alert = (
                    get_text(lang, "err_internal_pipeline")
                    or get_text(lang, "err_pipeline_failed")
                    or "⚠️ An unexpected error occurred. Please try again."
            )

            # Attempt to edit existing processing message; fallback to safe_reply if editing fails
            message_edited = False
            if 'processing_msg' in locals() and processing_msg:
                try:
                    await processing_msg.edit_text(err_alert, parse_mode="Markdown")
                    message_edited = True
                except Exception:
                    message_edited = False

            if not message_edited:
                await self.safe_reply(update, context, text=err_alert)

            if hasattr(self, "route_to_main_menu"):
                return await self.route_to_main_menu(update, context)

            return getattr(self, "MAIN_MENU", 0)

    async def trigger_reverse_matching(self, update: Update, context: ContextTypes.DEFAULT_TYPE,
                                       listing_record: dict) -> int:
        """
        Scans seeker_requests for users looking for the newly listed property.
        Fully robust, mirroring forward-matching precision with Strict & Semantic Evaluation,
        AI/heuristic sanitization, and graceful semantic fallback.
        """
        lang = self._get_lang(context)
        listing_id = listing_record.get('id')
        logger.info(f"🔍 Starting Strict & Semantic reverse matching scan for housing listing ID: {listing_id}")

        loading_text = get_text(lang,
                                "msg_scanning_seekers") or "🔍 Scanning active Seeker Requests for your property..."

        # Ensure we have a valid message object to reply or edit
        try:
            if update.message:
                msg_loading = await update.message.reply_text(loading_text)
            elif update.effective_chat:
                msg_loading = await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=loading_text
                )
            else:
                msg_loading = None
        except Exception as msg_err:
            logger.warning(f"Failed to dispatch loading message in reverse matching: {msg_err}")
            msg_loading = None

        listing_type = listing_record.get('transaction_type', 'rent')
        listing_currency = str(listing_record.get('currency') or 'ETB').strip().upper()

        try:
            if not hasattr(self, 'db') or not self.db:
                raise Exception("Database client not initialized in handler.")

            # 1. Standardize listing record data fields for the scoring engine
            listing_record['vertical_type'] = 'housing'
            raw_prop_use = str(listing_record.get('property_use') or 'Residential').strip()
            location_val = str(listing_record.get('location_text') or listing_record.get('location') or '').strip()

            listing_record['location_text'] = location_val
            listing_record['location'] = location_val
            listing_record['latitude'] = safe_float(listing_record.get('latitude', 0.0))
            listing_record['longitude'] = safe_float(listing_record.get('longitude', 0.0))
            listing_record['sq_meters'] = safe_float(listing_record.get('sq_meters', listing_record.get('sq_m', 0.0)))
            listing_record['price'] = safe_float(listing_record.get('price', 0.0))

            # 2. Fetch candidates from SEEKER_REQUESTS table (CORRECTED: queries seeker requests instead of listings)
            candidates = []
            if hasattr(self.db, 'fetch_active_seeker_requests'):
                candidates = await self.db.fetch_active_seeker_requests("housing", location_val)
            elif hasattr(self.db, 'fetch_housing_seeker_requests'):
                candidates = await self.db.fetch_housing_seeker_requests(
                    listing_type=listing_type,
                    housing_type=raw_prop_use
                )

            if not candidates and hasattr(self.db, 'fetch_active_seeker_requests'):
                candidates = await self.db.fetch_active_seeker_requests("housing", "")

            # Fallback direct table query to seeker_requests table if helper methods are absent
            if not candidates:
                res = await self.db.table('seeker_requests').select('*').eq(
                    'vertical_type', 'housing'
                ).eq('transaction_type', listing_type).eq('status', 'active').execute()
                candidates = res.data if res and hasattr(res, 'data') and res.data else []

            logger.info(f"📊 Found {len(candidates)} active housing seeker candidates for reverse match scan.")

            # Exclude the listing owner from candidates (checking seeker_id)
            candidates = [c for c in candidates if str(c.get('seeker_id')) != str(update.effective_user.id)]

            # Normalize candidate dictionaries for scoring
            unique_candidates = []
            seen_ids = set()
            for c in candidates:
                cid = c.get('id')
                if cid and cid not in seen_ids:
                    seen_ids.add(cid)
                    c['vertical_type'] = 'housing'
                    c['housing_type'] = c.get('housing_type') or c.get('property_use') or 'Residential'
                    c['property_use'] = c.get('property_use') or c['housing_type']
                    c['deal_type'] = c.get('deal_type') or c.get('transaction_type') or listing_type
                    c['transaction_type'] = c['deal_type']
                    c['max_budget'] = safe_float(c.get('max_budget', 0.0))
                    c['min_bedrooms'] = int(safe_float(c.get('min_bedrooms', c.get('number_of_bedrooms', 0))))
                    c['min_sq_meters'] = safe_float(c.get('min_sq_meters', c.get('min_sq_m', 0.0)))
                    c['location_text'] = str(c.get('location_text') or c.get('location') or '').strip()
                    c['location'] = c['location_text']
                    c['latitude'] = safe_float(c.get('latitude', 0.0))
                    c['longitude'] = safe_float(c.get('longitude', 0.0))
                    unique_candidates.append(c)
            candidates = unique_candidates

            # Construct listing preferences payload for HousingScorer
            listing_prefs = {
                'vertical_type': 'housing',
                'housing_type': raw_prop_use,
                'property_use': raw_prop_use,
                'deal_type': listing_type,
                'transaction_type': listing_type,
                'max_budget': listing_record.get('price', 0.0),
                'currency': listing_currency,
                'min_bedrooms': listing_record.get('bedrooms', 0),
                'min_sq_meters': listing_record.get('sq_meters', 0.0),
                'location_text': location_val,
                'location': location_val,
                'latitude': listing_record['latitude'],
                'longitude': listing_record['longitude'],
                'required_amenities': listing_record.get('amenities', '')
            }

            # 3. Strict & Semantic Evaluation (Mirrored from Forward Matching)
            matches = []
            matching_engine = self.matching if hasattr(self, 'matching') and hasattr(self.matching,
                                                                                     'evaluate_matches') else None

            if matching_engine and candidates:
                matches = await matching_engine.evaluate_matches('housing', listing_prefs, candidates)

            # 4. Semantic-Model Fallback if strict match returns 0 results
            if not matches and candidates and matching_engine:
                logger.info("Strict reverse housing matching returned 0 results. Triggering Semantic-Model Fallback...")
                listing_prefs['allow_semantic_fallback'] = True
                listing_prefs['housing_type'] = 'General'

                if hasattr(self.db, 'fetch_active_seeker_requests'):
                    broad_candidates = await self.db.fetch_active_seeker_requests("housing", "")
                    if broad_candidates:
                        candidates = [c for c in broad_candidates if
                                      str(c.get('seeker_id')) != str(update.effective_user.id)]
                elif not candidates:
                    res = await self.db.table('seeker_requests').select('*').eq(
                        'vertical_type', 'housing'
                    ).eq('status', 'active').execute()
                    candidates = res.data if res and hasattr(res, 'data') and res.data else []
                    candidates = [c for c in candidates if str(c.get('seeker_id')) != str(update.effective_user.id)]

                matches = await matching_engine.evaluate_matches('housing', listing_prefs, candidates)

            matches = sorted(matches, key=lambda x: safe_float(x.get('_match_score', 0)), reverse=True)
            top_matches = matches[:3]

            if not top_matches:
                fallback_msg = get_text(
                    lang, "msg_no_seeker_matches"
                ) or "✅ **Listing is Live!**\nCurrently, no pending seekers match this exact property. We will notify you when a match arises."
                if msg_loading:
                    await msg_loading.edit_text(fallback_msg, parse_mode="Markdown")
                else:
                    await update.effective_chat.send_message(fallback_msg, parse_mode="Markdown")
                return await self.route_to_main_menu(update, context)

            # 5. Localization of Top Matches
            localized_top_matches = []
            for match_item in top_matches:
                if lang != 'en' and hasattr(self, 'ai') and self.ai and hasattr(self.ai,
                                                                                'translate_asset_to_target_language'):
                    try:
                        localized = await asyncio.wait_for(
                            self.ai.translate_asset_to_target_language(match_item, lang),
                            timeout=10.0
                        )
                        localized_top_matches.append(localized if localized else match_item)
                    except Exception:
                        localized_top_matches.append(match_item)
                else:
                    localized_top_matches.append(match_item)

            top_matches = localized_top_matches

            # Save top matches consistently as a list for universal lookup across handlers
            context.user_data['seeker_top_matches'] = top_matches
            context.user_data['last_created_listing_id'] = listing_id
            context.user_data['listing_sq_meters'] = listing_record.get('sq_meters') or listing_record.get('sq_m')

            # Localized button terms
            seeker_terms = {"om": "Barbaadaa", "am": "ፈላጊ", "en": "Seeker"}
            budget_terms = {"om": "Baajata", "am": "በጀት", "en": "Budget"}

            s_term = seeker_terms.get(lang, seeker_terms["en"])
            b_term = budget_terms.get(lang, budget_terms["en"])

            keyboard = []
            for i, match in enumerate(top_matches, start=1):
                m_id = str(match.get('id'))

                # Multi-tier fallback prioritizing 'max_budget' from seeker_requests schema
                budget = safe_float(
                    match.get('max_budget') or
                    match.get('budget') or
                    match.get('price') or
                    match.get('offered_price') or
                    0.0
                )
                score = int(safe_float(match.get('_match_score', 95)))
                curr = str(match.get('currency') or listing_currency).strip().upper()

                btn_label = f"👤 #{i} {s_term} ({b_term}: {budget:,.2f} {curr} - {score}%)"

                # ✅ FIXED: Keep callback_data under Telegram's 64-byte limit by passing only m_id
                keyboard.append([InlineKeyboardButton(btn_label, callback_data=f"select_seeker_match:{m_id}")])

            btn_decline = get_text(lang, "BTN_SKIP") or "⏭️ Skip & Return to Menu"
            keyboard.append([InlineKeyboardButton(btn_decline, callback_data="cancel_match")])

            title_text = get_text(
                lang, "msg_top_seekers_found", count=len(top_matches)
            ) or f"🎯 **Top {len(top_matches)} Seekers Found!**\nThese users are actively requesting a property like yours. Select a Seeker below to send an offer:"

            if msg_loading:
                await msg_loading.edit_text(
                    title_text,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="Markdown"
                )
            else:
                await update.effective_chat.send_message(
                    title_text,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="Markdown"
                )
            return getattr(self, "SELECTING_MATCH", 99)

        except Exception as e:
            logger.error(f"❌ Housing Reverse matching failed for listing {listing_id}: {e}", exc_info=True)
            success_fallback = get_text(lang,
                                        "msg_listing_success") or "✅ Listing created successfully! You can manage it from the main menu."
            if msg_loading:
                try:
                    await msg_loading.edit_text(success_fallback, parse_mode="Markdown")
                except Exception:
                    await update.effective_chat.send_message(success_fallback, parse_mode="Markdown")
            else:
                await update.effective_chat.send_message(success_fallback, parse_mode="Markdown")
            return await self.route_to_main_menu(update, context)

    async def handle_match_selection(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        query = update.callback_query
        if not query:
            return getattr(self, "SELECTING_MATCH", 99)

        # 1. Answer callback immediately to halt client-side UI spinner
        try:
            await query.answer()
        except Exception as e:
            logger.debug(f"Callback query answer exception in handle_match_selection: {e}")

        cb_data = query.data or ""
        parts = cb_data.split(":")

        # ✅ Robustly unpack callback data supporting both 2-part and 3-part formats (prefix:listing_id:match_id or prefix:match_id)
        match_id = ""
        embedded_listing_id = None
        if len(parts) >= 3:
            embedded_listing_id = parts[1]
            match_id = parts[2]
        elif len(parts) >= 2:
            match_id = parts[1]

        # 2. Prevent double-processing race conditions from rapid multi-clicking
        last_processed = context.user_data.get('last_processed_match')
        if match_id and last_processed == match_id:
            return getattr(self, "SELECTING_MATCH", 99)

        context.user_data['last_processed_match'] = match_id

        lang = self._get_lang(context)
        is_reverse = cb_data.startswith("select_seeker_match:")
        context.user_data['is_reverse_match'] = is_reverse

        if embedded_listing_id and embedded_listing_id not in ("none", "None", ""):
            context.user_data['last_created_listing_id'] = embedded_listing_id

        # Initialize target_seeker_id with a safe default to prevent unassigned variable flags
        _target_seeker_id = None

        if is_reverse:
            raw_top = context.user_data.get('seeker_top_matches', [])
            top_matches = list(raw_top.values()) if isinstance(raw_top, dict) else list(raw_top or [])
            selected_request = next((m for m in top_matches if str(m.get('id', '')) == match_id), None)

            if not selected_request:
                err_msg_dict = {
                    "am": "⚠️ የተመረጠው ጥያቄ አሁን አገልግሎት ላይ አይደለም።",
                    "om": "⚠️ Gaaffiin filatame ammaan tana hojii irra hin jiru.",
                    "en": "⚠️ Selected request is no longer active."
                }
                err_msg = get_text(lang, "err_request_not_found") or err_msg_dict.get(lang, err_msg_dict["en"])
                try:
                    await query.edit_message_text(err_msg, parse_mode="HTML")
                except Exception:
                    pass
                return await self.route_to_main_menu(update, context)

            # ✅ Robust Multi-Tier Budget Extraction aligned with seeker_requests table ('max_budget')
            budget = safe_float(
                selected_request.get('max_budget') or
                selected_request.get('budget') or
                selected_request.get('price') or
                selected_request.get('offered_price') or
                0.0
            )
            currency = str(selected_request.get('currency') or 'ETB').strip().upper()
            target_seeker_id = selected_request.get('seeker_id')

            raw_price = context.user_data.get('housing_price') or context.user_data.get('price') or budget
            provider_asking_price = safe_float(raw_price)

            raw_req_prop = str(selected_request.get('property_use', 'Residential Property')).title()
            translated_req_prop = translate_property_type(raw_req_prop,
                                                          lang) if 'translate_property_type' in globals() or hasattr(
                self, 'translate_property_type') else raw_req_prop
            if hasattr(self, 'translate_property_type'):
                translated_req_prop = self.translate_property_type(raw_req_prop, lang)

            req_loc = str(
                selected_request.get('location_text') or selected_request.get('location') or 'Any Location')
            asset_title = f"Request for {translated_req_prop} in {req_loc}"

            # Extract Seeker Request Area checking all possible variants and fallbacks
            seeker_req_sqm = (
                    selected_request.get('min_sq_meters') or
                    selected_request.get('min_sq_m') or
                    selected_request.get('sq_meters') or
                    selected_request.get('sq_m') or
                    selected_request.get('area')
            )
            provider_listing_sqm = context.user_data.get('listing_sq_meters')

            # Ensure last_created_listing_id is properly retained or auto-resolved from DB
            if not context.user_data.get('last_created_listing_id') and hasattr(self, 'db') and self.db:
                try:
                    lat_res = await self.db.table('provider_listings').select('id').eq(
                        'provider_id', str(update.effective_user.id)
                    ).eq('vertical_type', 'housing').order('created_at', desc=True).limit(1).execute()
                    if lat_res and getattr(lat_res, 'data', None):
                        context.user_data['last_created_listing_id'] = lat_res.data[0].get('id')
                except Exception as e:
                    logger.warning(f"Failed to fetch last listing ID in handle_match_selection: {e}")

            # ✅ CRITICAL: Explicitly store seeker request ID and listing ID separately in context
            context.user_data['seeker_request_id'] = match_id
            context.user_data['target_seeker_id'] = target_seeker_id
            context.user_data['active_housing_deal_id'] = match_id
            context.user_data['housing_price'] = provider_asking_price
            context.user_data['asset_title'] = asset_title
            context.user_data['seeker_sq_meters'] = seeker_req_sqm

            # Ensure listing_id is NEVER identical to match_id (seeker request ID)
            listing_id = (
                embedded_listing_id
                if embedded_listing_id and embedded_listing_id not in ("none", "None", "")
                else context.user_data.get('last_created_listing_id')
            )

            if (not listing_id or listing_id == match_id) and hasattr(self, 'db') and self.db:
                try:
                    lat_res = await self.db.table('provider_listings').select('id').eq(
                        'provider_id', str(update.effective_user.id)
                    ).eq('vertical_type', 'housing').order('created_at', desc=True).limit(1).execute()
                    if lat_res and getattr(lat_res, 'data', None) and len(lat_res.data) > 0:
                        listing_id = lat_res.data[0].get('id')
                        context.user_data['last_created_listing_id'] = listing_id
                except Exception as e:
                    logger.warning(f"Failed to auto-resolve provider listing ID: {e}")

            header_texts = {
                "am": "👤 <b>የፈላጊ ጥያቄ ተመርጧል፡</b>",
                "om": "👤 <b>Gaaffiin Barbaadaa Filatameera:</b>",
                "en": "👤 <b>Seeker Request Selected:</b>"
            }
            loc_labels = {
                "am": "📍 <b>የተመረጠ ቦታ፡</b>",
                "om": "📍 <b>Iddoo Barbaadame:</b>",
                "en": "📍 <b>Preferred Location:</b>"
            }
            budget_labels = {
                "am": "💰 <b>ከፍተኛ በጀት፡</b>",
                "om": "💰 <b>Baajata Ol'aanaa:</b>",
                "en": "💰 <b>Max Budget:</b>"
            }
            seeker_sqm_labels = {
                "am": "📐 <b>የፈላጊው የጥያቄ ስፋት (Seeker Request):</b>",
                "om": "📐 <b>Bal'ina Gaaffii Barbaadaa:</b>",
                "en": "📐 <b>Seeker Requested Area:</b>"
            }
            provider_sqm_labels = {
                "am": "📐 <b>የእርስዎ ንብረት ስፋት:</b>",
                "om": "📐 <b>Bal'ina Qabeenya Keessanii:</b>",
                "en": "📐 <b>Your Listing Area:</b>"
            }
            prompts = {
                "am": "<i>ለዚህ ፈላጊ የዋጋ ማቅረቢያዎን ለመላክ ከታች ያለውን አማራጭ ይምረጡ፡</i>",
                "om": "<i>Dhiheessii keessan barbaadaa kanaaf erguuf filannoo armaan gadii caqaasaa:</i>",
                "en": "<i>Select an option below to submit your offer to this seeker:</i>"
            }

            header_text = get_text(lang, "msg_seeker_request_selected_housing") or header_texts.get(lang,
                                                                                                    header_texts["en"])
            loc_lbl = loc_labels.get(lang, loc_labels["en"])
            bud_lbl = budget_labels.get(lang, budget_labels["en"])
            s_sqm_lbl = seeker_sqm_labels.get(lang, seeker_sqm_labels["en"])
            p_sqm_lbl = provider_sqm_labels.get(lang, provider_sqm_labels["en"])
            prompt_txt = prompts.get(lang, prompts["en"])

            seeker_sqm_line = f"{s_sqm_lbl} <code>{seeker_req_sqm} m²</code>\n" if seeker_req_sqm else ""
            provider_sqm_line = f"{p_sqm_lbl} <code>{provider_listing_sqm} m²</code>\n" if provider_listing_sqm else ""

            card_msg = (
                f"{header_text} {html.escape(asset_title)}\n"
                f"{loc_lbl} <code>{html.escape(req_loc)}</code>\n"
                f"{bud_lbl} <code>{budget:,.2f} {currency}</code>\n"
                f"{seeker_sqm_line}"
                f"{provider_sqm_line}\n"
                f"{prompt_txt}"
            )

            btn_custom_dict = {
                "am": "🤝 የራስን ዋጋ ማቅረቢያ አስገባ",
                "om": "🤝 Gatii Mataa Keessan Galchaa",
                "en": "🤝 Enter Custom Offer"
            }
            btn_asking_dict = {
                "am": f"✅ የመጠየቂያ ዋጋ ላክ ({provider_asking_price:,.0f} {currency})",
                "om": f"✅ Gatii Gaafatame Ergi ({provider_asking_price:,.0f} {currency})",
                "en": f"✅ Send Asking Rate ({provider_asking_price:,.0f} {currency})"
            }
            btn_decline_dict = {
                "am": "❌ ሰርዝ እና ተመለስ",
                "om": "❌ Dhiisi Deebi'i",
                "en": "❌ Decline & Return"
            }

            btn_custom = get_text(lang, "BTN_SEND_OFFER") or btn_custom_dict.get(lang, btn_custom_dict["en"])
            btn_asking = btn_asking_dict.get(lang, btn_asking_dict["en"])
            btn_decline = get_text(lang, "BTN_DECLINE_RETURN") or btn_decline_dict.get(lang, btn_decline_dict["en"])

            # ✅ FIXED: Keep callback data under Telegram's 64-byte limit using single match_id
            keyboard = [
                [InlineKeyboardButton(btn_custom, callback_data=f"prompt_offer:{match_id}")],
                [InlineKeyboardButton(btn_asking, callback_data=f"lock_housing_deal:{match_id}")],
                [InlineKeyboardButton(btn_decline, callback_data="back_to_top_matches")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            try:
                if query.message and getattr(query.message, 'photo', None):
                    try:
                        await query.edit_message_caption(caption=card_msg, reply_markup=reply_markup,
                                                         parse_mode="HTML")
                    except Exception:
                        await context.bot.delete_message(chat_id=update.effective_chat.id,
                                                         message_id=query.message.message_id)
                        await context.bot.send_message(chat_id=update.effective_chat.id, text=card_msg,
                                                       reply_markup=reply_markup, parse_mode="HTML")
                else:
                    await query.edit_message_text(text=card_msg, reply_markup=reply_markup, parse_mode="HTML")
            except Exception:
                await context.bot.send_message(chat_id=update.effective_chat.id, text=card_msg,
                                               reply_markup=reply_markup, parse_mode="HTML")

            return getattr(self, "SELECTING_MATCH", 99)

        else:
            raw_top = context.user_data.get('housing_top_matches', [])
            top_matches = list(raw_top.values()) if isinstance(raw_top, dict) else list(raw_top or [])
            selected_property = next((m for m in top_matches if str(m.get('id', '')) == match_id), None)

            if not selected_property:
                err_msg_dict = {
                    "am": "⚠️ የተመረጠው ንብረት አሁን አገልግሎት ላይ አይደለም።",
                    "om": "⚠️ Qabeenyi filatame ammaan tana hojii irra hin jiru.",
                    "en": "⚠️ Selected property is no longer active."
                }
                err_msg = get_text(lang, "err_property_not_found") or err_msg_dict.get(lang, err_msg_dict["en"])
                try:
                    await query.edit_message_text(err_msg, parse_mode="HTML")
                except Exception:
                    pass
                return await self.route_to_main_menu(update, context)

            # Forward match selection block
            price = safe_float(selected_property.get('price', selected_property.get('cost', 0.0)))
            currency = str(selected_property.get('currency', 'ETB')).upper()

            # ✅ Ensure provider_id is robustly extracted and saved
            provider_id = safe_int(
                selected_property.get('provider_id') or
                selected_property.get('user_id') or
                selected_property.get('owner_id') or 0
            )

            raw_use = str(
                selected_property.get('property_use', selected_property.get('housing_type', 'Property'))).title()

            if hasattr(self, 'translate_property_type'):
                translated_use = self.translate_property_type(raw_use, lang)
            elif 'translate_property_type' in globals():
                translated_use = translate_property_type(raw_use, lang)
            else:
                translated_use = raw_use

            raw_loc = str(
                selected_property.get('location_text') or selected_property.get('location') or 'Addis Ababa')

            if lang == "am":
                asset_title = f"{translated_use} በ {raw_loc}"
            elif lang == "om":
                asset_title = f"{translated_use} {raw_loc} keessatti"
            else:
                asset_title = f"{translated_use} in {raw_loc}"

            provider_listing_sqm = selected_property.get('sq_meters') or selected_property.get('sq_m')
            seeker_req_sqm = context.user_data.get('seeker_sq_meters') or context.user_data.get('min_sq_meters')

            context.user_data['active_housing_deal_id'] = match_id
            context.user_data['housing_price'] = price
            context.user_data['provider_id'] = provider_id
            context.user_data['asset_title'] = asset_title
            context.user_data['location_text'] = raw_loc
            context.user_data['listing_sq_meters'] = provider_listing_sqm
            context.user_data['last_created_listing_id'] = match_id

            beds = selected_property.get('bedrooms', 'N/A')

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
                                return parsed_json.get("url") or parsed_json.get("file_id") or parsed_json.get(
                                    "path")
                        except Exception:
                            pass
                    return cleaned if cleaned else None
                if isinstance(item, dict):
                    return item.get("url") or item.get("file_id") or item.get("path") or item.get("image_url")
                return None

            for key in ['photo_url', 'image_url', 'photo_file_id', 'images', 'photos']:
                val = selected_property.get(key)
                if val:
                    photo_ref = _extract_url_or_id(val[0]) if isinstance(val, list) and len(
                        val) > 0 else _extract_url_or_id(val)
                    if photo_ref:
                        break

            header_texts = {
                "am": "🏠 <b>ንብረት ተመርጧል፡</b>",
                "om": "🏠 <b>Qabeenyi Filatameera:</b>",
                "en": "🏠 <b>Property Selected:</b>"
            }
            location_labels = {
                "am": "📍 <b>አካባቢ፡</b>",
                "om": "📍 <b>Iddoo:</b>",
                "en": "📍 <b>Location:</b>"
            }
            rate_texts = {
                "am": "💰 <b>የሚጠየቀው ዋጋ፡</b>",
                "om": "💰 <b>Gatii Gaafatame:</b>",
                "en": "💰 <b>Asking Price:</b>"
            }
            provider_sqm_labels = {
                "am": "📐 <b>የንብረት ስፋት (Provider Listing Area):</b>",
                "om": "📐 <b>Bal'ina Qabeenyaa (Listing Area):</b>",
                "en": "📐 <b>Provider Listing Area:</b>"
            }
            seeker_sqm_labels = {
                "am": "📐 <b>የእርስዎ የጥያቄ ስፋት (Your Request Area):</b>",
                "om": "📐 <b>Bal'ina Gaaffii Keessanii:</b>",
                "en": "📐 <b>Your Requested Area:</b>"
            }
            beds_labels = {
                "am": "🛏️ <b>የመኝታ ክፍሎች፡</b>",
                "om": "🛏️ <b>Kutaalee ciisichaa:</b>",
                "en": "🛏️ <b>Bedrooms:</b>"
            }
            prompts = {
                "am": "ለባለቤቱ የዋጋ ማቅረቢያዎን ለመላክ ከታች ያለውን አማራጭ ይምረጡ፡",
                "om": "Abbaa qabeenyaaf dhiheessii keessan erguuf filannoo armaan gadii fayyadamaa:",
                "en": "Select an option below to submit your offer to the owner:"
            }

            header_text = get_text(lang, "lbl_property_selected") or header_texts.get(lang, header_texts["en"])
            location_label = get_text(lang, "msg_location") or location_labels.get(lang, location_labels["en"])
            rate_text = get_text(lang, "lbl_asking_price") or rate_texts.get(lang, rate_texts["en"])
            p_sqm_lbl = provider_sqm_labels.get(lang, provider_sqm_labels["en"])
            s_sqm_lbl = seeker_sqm_labels.get(lang, seeker_sqm_labels["en"])
            beds_label = get_text(lang, "lbl_bedrooms") or beds_labels.get(lang, beds_labels["en"])
            prompt_txt = prompts.get(lang, prompts["en"])

            provider_sqm_line = f"{p_sqm_lbl} <code>{provider_listing_sqm} m²</code>\n" if provider_listing_sqm else ""
            seeker_sqm_line = f"{s_sqm_lbl} <code>{seeker_req_sqm} m²</code>\n" if seeker_req_sqm else ""
            beds_line = f"{beds_label} <code>{beds}</code>\n" if beds and str(beds) != 'N/A' else ""

            card_msg = (
                f"{header_text} <i>{html.escape(asset_title)}</i>\n"
                f"{location_label} <code>{html.escape(raw_loc)}</code>\n"
                f"{rate_text} <code>{price:,.2f} {currency}</code>\n"
                f"{provider_sqm_line}"
                f"{seeker_sqm_line}"
                f"{beds_line}\n"
                f"{prompt_txt}"
            )

            btn_custom_dict = {
                "am": "🤝 የራስን ዋጋ ማቅረቢያ አስገባ",
                "om": "🤝 Gatii Mataa Keessan Galchaa",
                "en": "🤝 Enter Custom Offer"
            }
            btn_asking_dict = {
                "am": f"✅ የመጠየቂያ ዋጋ ተቀበል ({price:,.0f} {currency})",
                "om": f"✅ Gatii Gaafatame Fudhachuuf ({price:,.0f} {currency})",
                "en": f"✅ Accept Asking Price ({price:,.0f} {currency})"
            }
            btn_decline_dict = {
                "am": "❌ ወደ ተስማሚ ውጤቶች ተመለስ",
                "om": "❌ Gara Filannoo Ol'aanaatti Deebi'i",
                "en": "❌ Back to Top Matches"
            }

            btn_custom = get_text(lang, "BTN_SEND_OFFER") or btn_custom_dict.get(lang, btn_custom_dict["en"])
            btn_asking = btn_asking_dict.get(lang, btn_asking_dict["en"])
            btn_decline = get_text(lang, "BTN_DECLINE_RETURN") or btn_decline_dict.get(lang, btn_decline_dict["en"])

            target_seeker_id = update.effective_user.id if update.effective_user else 0
            context.user_data['seeker_request_id'] = match_id
            context.user_data['target_seeker_id'] = target_seeker_id

            listing_id = match_id

            keyboard = [
                [InlineKeyboardButton(btn_custom, callback_data=f"prompt_offer:{listing_id}")],
                [InlineKeyboardButton(btn_asking, callback_data=f"lock_housing_deal:{listing_id}")],
                [InlineKeyboardButton(btn_decline, callback_data="back_to_top_matches")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            presigned_url = None
            if photo_ref:
                img_service = getattr(self, "image_service", getattr(self, "storage", None))
                if img_service and hasattr(img_service, "get_presigned_url_async"):
                    try:
                        presigned_url = await img_service.get_presigned_url_async(photo_ref)
                    except Exception as sign_err:
                        logger.warning(f"Failed to generate pre-signed URL for '{photo_ref}': {sign_err}")

            photo_dispatched = False
            if presigned_url and query and query.message:
                try:
                    try:
                        await context.bot.delete_message(chat_id=update.effective_chat.id,
                                                         message_id=query.message.message_id)
                    except (BadRequest, TelegramError):
                        pass

                    await context.bot.send_photo(
                        chat_id=update.effective_chat.id,
                        photo=presigned_url,
                        caption=card_msg,
                        reply_markup=reply_markup,
                        parse_mode="HTML"
                    )
                    photo_dispatched = True
                except (BadRequest, TelegramError) as photo_err:
                    logger.warning(f"Failed to dispatch photo card: {photo_err}")

            if not photo_dispatched and query and query.message:
                try:
                    await query.edit_message_text(text=card_msg, reply_markup=reply_markup, parse_mode="HTML")
                except BadRequest:
                    await context.bot.send_message(chat_id=update.effective_chat.id, text=card_msg,
                                                   reply_markup=reply_markup, parse_mode="HTML")

            return getattr(self, "SELECTING_MATCH", 99)

    async def handle_back_to_top_matches(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        query = update.callback_query
        if query:
            try:
                await query.answer()
            except Exception as e:
                logger.debug(f"Callback answer exception in handle_back_to_top_matches: {e}")

        # Clear last processed match lock to enable immediate reselection across all matches
        context.user_data.pop('last_processed_match', None)

        lang = self._get_lang(context)
        is_reverse = context.user_data.get('is_reverse_match', False)

        raw_top = context.user_data.get('seeker_top_matches', []) if is_reverse else context.user_data.get(
            'housing_top_matches', [])
        top_matches = list(raw_top.values()) if isinstance(raw_top, dict) else list(raw_top or [])

        if not top_matches:
            return await self.route_to_main_menu(update, context)

        keyboard = []
        if is_reverse:
            context.user_data.pop('active_seeker_deal_id', None)
            header_text = get_text(
                lang, "msg_top_seekers_found", count=len(top_matches)
            ) or f"🎯 <b>Top {len(top_matches)} Seeker Matches Found!</b>\n\nThese users are actively requesting a property matching your listing. Select a Seeker below to send your offer:"

            for i, match in enumerate(top_matches, start=1):
                match_id = str(match.get('id', ''))

                # Multi-tier fallback prioritizing 'max_budget' from seeker_requests schema
                budget = safe_float(
                    match.get('max_budget') or
                    match.get('budget') or
                    match.get('price') or
                    match.get('offered_price') or
                    0.0
                )
                currency = str(match.get('currency') or 'ETB').strip().upper()
                score = int(safe_float(match.get('_match_score', 95)))

                btn_label = f"👤 #{i} Seeker (Budget: {budget:,.2f} {currency} - {score}%)"

                # ✅ FIXED: Keep callback_data under Telegram's 64-byte limit by passing only match_id
                keyboard.append([
                    InlineKeyboardButton(
                        btn_label,
                        callback_data=f"select_seeker_match:{match_id}"
                    )
                ])

            btn_decline = get_text(lang, "BTN_DECLINE_RETURN") or get_text(lang, "BTN_SKIP") or "❌ Decline & Return"
            keyboard.append([InlineKeyboardButton(btn_decline, callback_data="cancel_match")])
        else:
            context.user_data.pop('active_housing_deal_id', None)
            header_text = get_text(
                lang, "msg_select_housing_match"
            ) or "🏠 <b>Top Matching Housing Options:</b>\n\nPlease select one of the options below:"

            for idx, match in enumerate(top_matches, start=1):
                raw_title = str(match.get('property_use', match.get('housing_type', 'Property'))).title()
                display_title = translate_property_type(raw_title,
                                                        lang) if 'translate_property_type' in globals() or hasattr(self,
                                                                                                                   'translate_property_type') else raw_title
                if hasattr(self, 'translate_property_type'):
                    display_title = self.translate_property_type(raw_title, lang)

                raw_loc = str(match.get('location_text', match.get('location', 'Addis Ababa')))
                price = safe_float(match.get('price', match.get('cost', 0.0)))
                currency = str(match.get('currency', 'ETB')).strip().upper()
                match_id = str(match.get('id', ''))

                if lang == "am":
                    btn_text = f"{idx}. {display_title} በ {raw_loc} ({price:,.0f} {currency})"
                elif lang == "om":
                    btn_text = f"{idx}. {display_title} {raw_loc} keessatti ({price:,.0f} {currency})"
                else:
                    btn_text = f"{idx}. {display_title} in {raw_loc} ({price:,.0f} {currency})"

                keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"select_house_match:{match_id}")])

            btn_cancel = get_text(lang, "BTN_CANCEL") or "❌ Cancel Search"
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
                            await context.bot.delete_message(chat_id=chat_id, message_id=query.message.message_id)
                        except Exception:
                            pass
                        await context.bot.send_message(chat_id=chat_id, text=header_text, reply_markup=reply_markup,
                                                       parse_mode="HTML")
                        return getattr(self, "SELECTING_MATCH", 99)

                await query.edit_message_text(text=header_text, reply_markup=reply_markup, parse_mode="HTML")
            elif chat_id:
                await context.bot.send_message(chat_id=chat_id, text=header_text, reply_markup=reply_markup,
                                               parse_mode="HTML")
        except Exception as e:
            logger.error(f"Failed to refresh housing top matches UI block: {e}")
            if chat_id:
                try:
                    await context.bot.send_message(chat_id=chat_id, text=header_text, reply_markup=reply_markup,
                                                   parse_mode="HTML")
                except Exception as send_err:
                    logger.error(f"Failed to send fallback housing top matches message: {send_err}")

        return getattr(self, "SELECTING_MATCH", 99)

    @staticmethod
    def is_location_mismatched(loc1: str, loc2: str) -> bool:
        """
        Normalizes location strings by stripping punctuation and descriptor filler words
        (e.g., 'near', 'areas', 'around') to evaluate if locations match semantically.
        """
        clean1 = re.sub(r'[^\w\s]', ' ', loc1.lower())
        clean2 = re.sub(r'[^\w\s]', ' ', loc2.lower())

        noise_words = {'near', 'area', 'areas', 'around', 'opposite', 'behind', 'beside', 'close', 'to', 'next'}
        tokens1 = [w for w in clean1.split() if w not in noise_words]
        tokens2 = [w for w in clean2.split() if w not in noise_words]

        str1 = " ".join(tokens1)
        str2 = " ".join(tokens2)

        if not str1 or not str2:
            return loc1.strip().lower() != loc2.strip().lower()

        if str1 == str2 or str1 in str2 or str2 in str1:
            return False

        similarity = SequenceMatcher(None, str1, str2).ratio()
        return similarity < 0.65

    async def prompt_custom_offer(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        query = update.callback_query
        if query:
            await query.answer()

        # Helper to normalize language code
        def _normalize_lang(raw_lang: Any) -> Optional[str]:
            if not raw_lang:
                return None
            clean = str(raw_lang).lower().strip().replace("-", "_").split("_")[0]
            if clean in ("om", "or", "oromoo"):
                return "om"
            if clean in ("am", "amharic", "amh"):
                return "am"
            if clean in ("en", "english"):
                return "en"
            return None

        # Session override takes absolute priority for mid-conversation /setlang switches
        session_lang = (
                context.user_data.get('lang') or
                context.user_data.get('language') or
                context.user_data.get('preferred_language')
        )
        user_lang = _normalize_lang(session_lang)
        if not user_lang and hasattr(self, '_get_lang'):
            user_lang = _normalize_lang(self._get_lang(context))
        user_lang = user_lang or "en"

        is_reverse = context.user_data.get('is_reverse_match', False)
        asset_title = context.user_data.get('asset_title', 'Selected Property')
        active_deal_id = str(context.user_data.get("active_housing_deal_id", "pending"))

        # Helper: Filter out all invalid placeholder strings and zero values
        INVALID_PLACEHOLDERS = {
            None, "", "n/a", "none", "null", "not specified",
            "undefined", "unknown", "0", 0, 0.0, "0.0", "nan"
        }

        def clean_val(val):
            if val is None:
                return None
            s_val = str(val).strip()
            if s_val.lower() in INVALID_PLACEHOLDERS:
                return None
            return s_val

        def get_valid_value(source_list: list, keys: list):
            for src in source_list:
                if isinstance(src, dict):
                    for key in keys:
                        cleaned = clean_val(src.get(key))
                        if cleaned is not None:
                            return cleaned
            return None

        # Containers for DB data
        l_data = {}
        r_data = {}

        # DB Lookup for Locations, Currency, Transaction Type, Property Use & Spec Details
        if active_deal_id != "pending" and hasattr(self, 'db'):
            try:
                deal_res = await self.db.table('deals').select(
                    'current_price, listing_id, request_id, currency'
                ).eq('id', active_deal_id).maybe_single().execute()

                if deal_res and hasattr(deal_res, 'data') and deal_res.data:
                    d_data = deal_res.data
                    if clean_val(d_data.get('current_price')) is not None:
                        try:
                            context.user_data['housing_price'] = float(d_data['current_price'])
                        except (ValueError, TypeError):
                            pass
                    if clean_val(d_data.get('currency')):
                        context.user_data['currency'] = str(d_data['currency']).upper()

                    # 1. Fetch Provider Specs & Metadata
                    if d_data.get('listing_id'):
                        listing_res = await self.db.table('provider_listings').select(
                            'property_use, transaction_type, location_text, currency, bedrooms, sq_meters'
                        ).eq('id', d_data['listing_id']).maybe_single().execute()
                        if listing_res and hasattr(listing_res, 'data') and listing_res.data:
                            l_data = listing_res.data
                            if clean_val(l_data.get('location_text')):
                                context.user_data['provider_location'] = l_data['location_text']
                            if clean_val(l_data.get('currency')):
                                context.user_data['currency'] = str(l_data['currency']).upper()

                    # 2. Fetch Seeker Specs & Metadata
                    if d_data.get('request_id'):
                        req_res = await self.db.table('seeker_requests').select(
                            'location_text, request_type, transaction_type, property_use, currency, bedrooms, sq_meters'
                        ).eq('id', d_data['request_id']).maybe_single().execute()
                        if req_res and hasattr(req_res, 'data') and req_res.data:
                            r_data = req_res.data
                            if clean_val(r_data.get('location_text')):
                                context.user_data['seeker_location'] = r_data['location_text']
            except Exception as e:
                logger.warning(f"Could not fetch latest deal metadata for locations: {e}")

        # Prioritize DB data (provider_listings -> seeker_requests -> user_data)
        sources = [l_data, r_data, context.user_data]

        # Extract Currency
        currency = get_valid_value(sources, ['currency', 'housing_currency']) or 'ETB'
        currency = currency.upper()

        logger.info(
            f"Prompting custom offer for deal ID: {active_deal_id} [Lang: {user_lang}] | Reverse Match: {is_reverse}"
        )

        # Determine Base Price
        if is_reverse:
            raw_price = get_valid_value(
                sources,
                ['max_budget', 'seeker_budget', 'budget', 'current_price', 'housing_price', 'price']
            )
        else:
            raw_price = get_valid_value(sources, ['housing_price', 'current_price', 'price'])

        try:
            asking_price = float(raw_price) if raw_price is not None else 0.0
        except (ValueError, TypeError):
            asking_price = 0.0

        location_text = get_valid_value(sources, ['location_text', 'provider_location']) or 'Addis Ababa'
        property_use = get_valid_value(sources, ['property_use']) or asset_title
        transaction_type = get_valid_value(sources, ['transaction_type', 'listing_type', 'request_type']) or 'rent'

        # Extract Specifications (Bedrooms vs sq_meters)
        bedrooms = get_valid_value(
            sources,
            ['bedrooms', 'bedrooms_count', 'rooms', 'min_bedrooms', 'num_bedrooms']
        )
        area_sqm = get_valid_value(
            sources,
            ['sq_meters', 'area_sqm', 'size_sqm', 'property_size', 'sqm', 'min_sq_meters', 'area']
        )

        # Normalization & Localization Helpers
        def format_tx_type(tx: str, lang: str) -> str:
            tx_clean = str(tx).lower().replace('action_', '').strip()
            if tx_clean in ['sale', 'sell', 'buy', 'purchase']:
                tx_key = 'sale' if tx_clean in ['sale', 'sell'] else 'buy'
            elif tx_clean in ['rent', 'rental']:
                tx_key = 'rent'
            else:
                tx_key = tx_clean

            tx_map = {
                'rent': {'en': 'Rent', 'am': 'ኪራይ', 'om': 'Kiree'},
                'sale': {'en': 'Sale', 'am': 'ሽያጭ', 'om': 'Gurgurtaa'},
                'buy': {'en': 'Purchase', 'am': 'ግዢ', 'om': 'Bittaa'}
            }
            return tx_map.get(tx_key, {}).get(lang, tx_clean.title())

        def format_prop_use(use: str, lang: str) -> str:
            use_clean = str(use).lower().replace('use_', '').replace('request for ', '').split(' in ')[0].strip()
            use_map = {
                'residential': {'en': 'Residential', 'am': 'መኖሪያ', 'om': 'Jireenyaa'},
                'commercial': {'en': 'Commercial', 'am': 'ንግድ', 'om': 'Daldalaa'},
                'residential_plot': {'en': 'Residential Plot', 'am': 'መኖሪያ ቦታ', 'om': 'Lafa Jireenyaa'},
                'commercial_plot': {'en': 'Commercial Plot', 'am': 'የንግድ ቦታ', 'om': 'Lafa Daldalaa'},
                'residential plot': {'en': 'Residential Plot', 'am': 'መኖሪያ ቦታ', 'om': 'Lafa Jireenyaa'},
                'commercial plot': {'en': 'Commercial Plot', 'am': 'የንግድ ቦታ', 'om': 'Lafa Daldalaa'}
            }
            return use_map.get(use_clean, {}).get(lang, use_clean.replace('_', ' ').title())

        formatted_price = f"{asking_price:,.2f} {currency}"
        esc_tx_type = html.escape(format_tx_type(transaction_type, user_lang))
        esc_prop_use = html.escape(format_prop_use(property_use, user_lang))

        # Dynamic Specification Note Construction with Explicit Seeker vs Provider Area Differentiation[cite: 6]
        use_slug = str(property_use).lower().replace('use_', '').replace('request for ', '').split(' in ')[
            0].strip().replace(' ', '_')
        spec_note_en, spec_note_am, spec_note_om = "", "", ""

        seeker_sqm_val = l_data.get('min_sq_meters') or r_data.get('sq_meters') or context.user_data.get('seeker_sqm')
        provider_sqm_val = l_data.get('sq_meters') or context.user_data.get('listing_sq_meters') or area_sqm

        if use_slug == "residential" and bedrooms is not None:
            bed_str = html.escape(bedrooms)
            spec_note_en = f"🛏 <b>Bedrooms:</b> {bed_str}\n"
            spec_note_am = f"🛏 <b>የመኝታ ክፍሎች ብዛት፡</b> {bed_str}\n"
            spec_note_om = f"🛏 <b>Baay'ina Kutaalee Ciisichaa:</b> {bed_str}\n"
        elif use_slug in ["commercial", "commercial_plot", "residential_plot"]:
            if seeker_sqm_val and provider_sqm_val and str(seeker_sqm_val) != str(provider_sqm_val):
                s_sqm = html.escape(str(seeker_sqm_val))
                p_sqm = html.escape(str(provider_sqm_val))
                spec_note_en = f"📐 <b>Seeker Requested Area:</b> {s_sqm} m² | 📐 <b>Provider Listing Area:</b> {p_sqm} m²\n"
                spec_note_am = f"📐 <b>የፈላጊው ጥያቄ ስፋት:</b> {s_sqm} ካሬ ሜትር | 📐 <b>የንብረት ስፋት:</b> {p_sqm} ካሬ ሜትር\n"
                spec_note_om = f"📐 <b>Bal'ina Gaaffii:</b> {s_sqm} m² | 📐 <b>Bal'ina Qabeenyaa:</b> {p_sqm} m²\n"
            elif provider_sqm_val is not None:
                sqm_str = html.escape(str(provider_sqm_val))
                spec_note_en = f"📐 <b>Area:</b> {sqm_str} m²\n"
                spec_note_am = f"📐 <b>ስፋት፡</b> {sqm_str} ካሬ ሜትር\n"
                spec_note_om = f"📐 <b>Ballina:</b> {sqm_str} m²\n"

        if is_reverse:
            seeker_loc = get_valid_value([context.user_data], ['seeker_location'])
            if not seeker_loc and " in " in asset_title:
                seeker_loc = asset_title.split(" in ")[-1].strip()
            seeker_loc = clean_val(seeker_loc) or "Specified Location"

            provider_loc = clean_val(
                get_valid_value([context.user_data], ['provider_location']) or location_text
            ) or "Alternative Location"

            has_mismatch = self.is_location_mismatched(seeker_loc, provider_loc)

            context.user_data['seeker_location'] = seeker_loc
            context.user_data['provider_location'] = provider_loc
            context.user_data['has_location_mismatch'] = has_mismatch

            esc_seeker = html.escape(seeker_loc)
            esc_provider = html.escape(provider_loc)

            if has_mismatch:
                location_note_en = (
                    f"📍 <b>Seeker's Preferred Location:</b> <code>{esc_seeker}</code>\n"
                    f"📍 <b>Proposed Location (Your Listing):</b> <code>{esc_provider}</code>\n"
                    f"⚠️ <i>Note: You are proposing an alternative location which differs from the seeker's preference. The seeker will be notified.</i>\n\n"
                )
                location_note_am = (
                    f"📍 <b>የፈላጊው ተመራጭ ቦታ፡</b> <code>{esc_seeker}</code>\n"
                    f"📍 <b>የቀረበው አማራጭ ቦታ፡</b> <code>{esc_provider}</code>\n"
                    f"⚠️ <i>ማስታወሻ፡ ከፈላጊው ምርጫ ውጭ አማራጭ ቦታ እያቀረቡ ነው። ፈላጊው ይህንን ልዩነት እንዲያውቅ ይደረጋል።</i>\n\n"
                )
                location_note_om = (
                    f"📍 <b>Iddoo Barbaadaan Filate:</b> <code>{esc_seeker}</code>\n"
                    f"📍 <b>Iddoo Dhihaate (Filannoo):</b> <code>{esc_provider}</code>\n"
                    f"⚠️ <i>Hubachiisa: Filannoo barbaadaa irraa adda ta'ee iddoo filannoo dhiheessaa jirtu. Barbaadaan garaagarummaa kana ni beeksifama.</i>\n\n"
                )
            else:
                location_note_en = f"📍 <b>Location Match:</b> <code>{esc_provider}</code>\n\n"
                location_note_am = f"📍 <b>የቦታ ስምምነት፡</b> <code>{esc_provider}</code>\n\n"
                location_note_om = f"📍 <b>Iddoo Wal simatu:</b> <code>{esc_provider}</code>\n\n"

            prompts = {
                "en": (
                    f"💬 <b>Enter Your Proposed Offer</b>\n\n"
                    f"📑 <b>Transaction Type:</b> {esc_tx_type}\n"
                    f"🏢 <b>Property Sector/Use:</b> {esc_prop_use}\n"
                    f"{spec_note_en}"
                    f"💰 <b>Seeker Budget:</b> <code>{formatted_price}</code>\n\n"
                    f"{location_note_en}"
                    f"Please reply with your offer amount in {currency} (numbers only, e.g., <code>500000</code>):"
                ),
                "am": (
                    f"💬 <b>የእርስዎን የዋጋ ማቅረቢያ ያስገቡ</b>\n\n"
                    f"📑 <b>የግብይት ዓይነት፡</b> {esc_tx_type}\n"
                    f"🏢 <b>የንብረት ዓይነት/አገልግሎት፡</b> {esc_prop_use}\n"
                    f"{spec_note_am}"
                    f"💰 <b>የፈላጊው በጀት፡</b> <code>{formatted_price}</code>\n\n"
                    f"{location_note_am}"
                    f"እባክዎን የቅርብ ጊዜ የዋጋ ማቅረቢያዎን በ {currency} ይጻፉ:"
                ),
                "om": (
                    f"💬 <b>Gatii dhiheessii keessan galchaa</b>\n\n"
                    f"📑 <b>Gosa Daddabarsaa:</b> {esc_tx_type}\n"
                    f"🏢 <b>Gosa Qabeenyaa/Tajaajila:</b> {esc_prop_use}\n"
                    f"{spec_note_om}"
                    f"{location_note_om}"
                    f"Maaloo hamma gatii dhiheessii keessanii qarshii {currency} tiin deebisaa:"
                )
            }
        else:
            escaped_location = html.escape(str(location_text))
            location_note_en = f"📍 <b>Location:</b> <code>{escaped_location}</code>\n\n"
            location_note_am = f"📍 <b>ቦታ፡</b> <code>{escaped_location}</code>\n\n"
            location_note_om = f"📍 <b>Iddoo:</b> <code>{escaped_location}</code>\n\n"

            prompts = {
                "en": (
                    f"💬 <b>Enter Your Proposed Offer</b>\n\n"
                    f"📑 <b>Transaction Type:</b> {esc_tx_type}\n"
                    f"🏢 <b>Property Sector/Use:</b> {esc_prop_use}\n"
                    f"{spec_note_en}"
                    f"{location_note_en}"
                    f"💰 <b>Asking Price:</b> <code>{formatted_price}</code>\n\n"
                    f"Please reply with your offer amount in {currency} (numbers only, e.g., <code>500000</code>):"
                ),
                "am": (
                    f"💬 <b>የእርስዎን የዋጋ ማቅረቢያ ያስገቡ</b>\n\n"
                    f"📑 <b>የግብይት ዓይነት፡</b> {esc_tx_type}\n"
                    f"🏢 <b>የንብረት ዓይነት/አገልግሎት፡</b> {esc_prop_use}\n"
                    f"{spec_note_am}"
                    f"{location_note_am}"
                    f"💰 <b>የሚጠየቀው ዋጋ፡</b> <code>{formatted_price}</code>\n\n"
                    f"እባክዎን የዋጋ ማቅረቢያዎን በ {currency} ይጻፉ:"
                ),
                "om": (
                    f"💬 <b>Gatii dhiheessii keessan galchaa</b>\n\n"
                    f"📑 <b>Gosa Daddabarsaa:</b> {esc_tx_type}\n"
                    f"🏢 <b>Gosa Qabeenyaa/Tajaajila:</b> {esc_prop_use}\n"
                    f"{spec_note_om}"
                    f"{location_note_om}"
                    f"💰 <b>Gatii Gaafatame:</b> <code>{formatted_price}</code>\n\n"
                    f"Maaloo hamma gatii dhiheessii keessanii qarshii {currency} tiin deebisaa:"
                )
            }

        prompt_msg = prompts.get(user_lang, prompts["en"])
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

        return self.ENTERING_OFFER

    async def receive_custom_offer(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        session_lang = (
                context.user_data.get('lang') or
                context.user_data.get('language') or
                context.user_data.get('preferred_language')
        )
        lang = session_lang or (self._get_lang(context) if hasattr(self, '_get_lang') else "en")
        if not update.message or not update.message.text:
            return self.ENTERING_OFFER

        clean_text = (
            update.message.text.lower()
            .replace(',', '')
            .replace(' ', '')
            .replace('etb', '')
            .replace('usd', '')
            .replace('birr', '')
            .strip()
        )

        try:
            custom_offer = float(clean_text)
            if custom_offer <= 0:
                raise ValueError("Price must be greater than zero")
        except ValueError:
            err_text = get_text(lang,
                                "err_invalid_price") or "⚠️ **Invalid amount format.**\nPlease enter numbers only without spaces or text."
            await update.message.reply_text(err_text, parse_mode="Markdown")
            return self.ENTERING_OFFER

        context.user_data['housing_price'] = custom_offer
        active_deal_id = str(context.user_data.get("active_housing_deal_id", "pending"))

        if active_deal_id != "pending" and hasattr(self, 'db') and self.db:
            try:
                await self.db.table('deals').update({'current_price': custom_offer, 'status': 'negotiating'}).eq('id',
                                                                                                                 active_deal_id).execute()
            except Exception as e:
                logger.error(f"Error updating counter-offer price in DB: {e}")

        return await self._process_deal_lock(update, context, active_deal_id, custom_offer)

    async def execute_deal_lock(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        query = update.callback_query
        if query:
            try:
                await query.answer()
            except Exception as e:
                logger.debug(f"Callback answer exception in execute_deal_lock: {e}")

        cb_data = query.data if query and query.data else ""
        parts = cb_data.split(":")
        match_id = parts[1] if len(parts) > 1 else ""

        lang = self._get_lang(context) if hasattr(self, "_get_lang") else "en"
        user_id = str(update.effective_user.id) if update and update.effective_user else ""

        # 1. Resolve Match Direction and Seeker/Target Context
        is_reverse = context.user_data.get('is_reverse_match', False)
        seeker_request_id = context.user_data.get('seeker_request_id') or (match_id if is_reverse else None)
        target_seeker_id = context.user_data.get('target_seeker_id') or (
            update.effective_user.id if update and update.effective_user and not is_reverse else None)

        if is_reverse and match_id:
            context.user_data['seeker_request_id'] = match_id
            context.user_data['active_housing_deal_id'] = match_id

        # 2. ✅ ROBUST MULTI-TIER LISTING RESOLUTION
        _listing_id = None
        if not is_reverse:
            # Forward match: match_id IS the listing_id
            listing_id = match_id if match_id else context.user_data.get('active_housing_deal_id')
        else:
            # Reverse match: listing_id belongs to the provider
            listing_id = context.user_data.get('last_created_listing_id')

        # Fallback DB Lookup: If listing_id is missing, invalid, or incorrectly matches seeker_request_id
        if (not listing_id or listing_id == "None" or (is_reverse and listing_id == seeker_request_id)) and hasattr(
                self, 'db') and self.db:
            try:
                # Fallback 1: Lookup active deal record if match_id exists
                if match_id:
                    deal_check = await self.db.table('deals').select('listing_id, provider_id, seeker_id') \
                        .or_(f"id.eq.{match_id},seeker_request_id.eq.{match_id}") \
                        .maybe_single().execute()
                    if deal_check and getattr(deal_check, 'data', None):
                        listing_id = deal_check.data.get('listing_id')
                        if deal_check.data.get('provider_id'):
                            context.user_data['provider_id'] = deal_check.data.get('provider_id')

                # Fallback 2: Fetch provider's latest active housing listing from provider_listings
                if not listing_id and user_id:
                    lat_res = await self.db.table('provider_listings').select('id, provider_id') \
                        .eq('provider_id', user_id) \
                        .eq('vertical_type', 'housing') \
                        .order('created_at', desc=True) \
                        .limit(1).execute()
                    if lat_res and getattr(lat_res, 'data', None) and len(lat_res.data) > 0:
                        listing_id = lat_res.data[0].get('id')

                if listing_id:
                    context.user_data['last_created_listing_id'] = listing_id

            except Exception as resolve_err:
                logger.warning(f"Failed auto-resolving listing ID in execute_deal_lock: {resolve_err}")

        if listing_id:
            context.user_data['last_created_listing_id'] = listing_id

        # 3. Validation Guardrail
        if not listing_id or listing_id == "None" or (is_reverse and listing_id == seeker_request_id):
            logger.error(f"❌ Deal Lock Error: True provider listing_id could not be resolved for User {user_id}.")
            err_msg = (
                          get_text(lang, "err_invalid_listing_reference")
                          if 'get_text' in globals() else None
                      ) or "⚠️ System Error: Invalid listing reference. Please restart your search."
            if query and query.message:
                try:
                    await query.edit_message_text(err_msg, parse_mode="HTML")
                except Exception:
                    pass
            return await self.route_to_main_menu(update, context)

        # 4. Resolve Prices & Session State
        agreed_price = safe_float(
            context.user_data.get('housing_price') or context.user_data.get('price') or 0.0
        )
        provider_id = safe_int(
            context.user_data.get('provider_id') or (
                update.effective_user.id if is_reverse and update.effective_user else 0)
        )

        # 5. Execute DB Deal Upsert / Processing Pipeline
        if hasattr(self, '_process_deal_lock'):
            return await self._process_deal_lock(update, context, str(listing_id), agreed_price)

        # Direct Fallback for handlers directly invoking payment options
        try:
            deal_payload = {
                "listing_id": str(listing_id),
                "seeker_request_id": seeker_request_id if is_reverse else None,
                "seeker_id": target_seeker_id,
                "provider_id": provider_id,
                "agreed_price": agreed_price,
                "status": "pending_payment",
                "deal_state": "negotiating",
                "vertical_type": "housing",
                "updated_at": datetime.now(timezone.utc).isoformat()
            }

            deal_id = match_id
            if hasattr(self, 'db') and self.db:
                upsert_res = await self.db.table('deals').upsert(deal_payload).execute()
                if upsert_res and getattr(upsert_res, 'data', None) and len(upsert_res.data) > 0:
                    deal_id = upsert_res.data[0].get('id', match_id)

            context.user_data['active_deal_id'] = deal_id
            context.user_data['active_housing_deal_id'] = deal_id

            if hasattr(self, 'present_payment_options'):
                return await self.present_payment_options(update, context, deal_id=deal_id)

        except Exception as lock_err:
            logger.error(f"Failed in execute_deal_lock fallback execution: {lock_err}", exc_info=True)
            err_msg = "⚠️ Unable to lock deal. Please try again."
            if query and query.message:
                try:
                    await query.edit_message_text(err_msg, parse_mode="HTML")
                except Exception:
                    pass
            return getattr(self, "SELECTING_MATCH", 99)

        return await self.route_to_main_menu(update, context)

    async def _process_deal_lock(
            self, update: Update, context: ContextTypes.DEFAULT_TYPE, target_id: str, agreed_price: float
    ) -> int:
        callback_query = update.callback_query
        is_reverse = context.user_data.get('is_reverse_match', False)

        # ✅ Initialize req_id here to satisfy PyCharm's static analyzer
        req_id = context.user_data.get('seeker_request_id') or context.user_data.get('active_housing_deal_id')

        # Helper to resolve and normalize language code strictly
        def _normalize_lang(raw_lang: Any) -> Optional[str]:
            if not raw_lang:
                return None
            clean = str(raw_lang).lower().strip().replace("-", "_").split("_")[0]
            if clean in ("om", "or", "oromoo"):
                return "om"
            if clean in ("am", "amharic", "amh"):
                return "am"
            if clean in ("en", "english"):
                return "en"
            return None

        # Robust database language resolver supporting both Telegram ID and internal DB ID/UUID
        async def get_db_user_lang(uid: Any, default_lang: str = "en") -> str:
            if not uid:
                return default_lang
            dm_database = getattr(self, 'db', getattr(self, 'db_manager', None))
            if not dm_database or not hasattr(dm_database, 'table'):
                return default_lang

            try:
                int_uid = int(uid)
                response = await dm_database.table('users').select(
                    'preferred_language, language_code, language').eq(
                    'telegram_id', int_uid
                ).maybe_single().execute()
                if response and getattr(response, 'data', None):
                    db_data = response.data
                    for col in ['preferred_language', 'language_code', 'language']:
                        norm = _normalize_lang(db_data.get(col))
                        if norm:
                            return norm
            except (ValueError, TypeError):
                pass
            except Exception as lang_err:
                logger.warning(f"Could not fetch user language by telegram_id for {uid}: {lang_err}")

            try:
                response = await dm_database.table('users').select(
                    'preferred_language, language_code, language').eq(
                    'id', str(uid)
                ).maybe_single().execute()
                if response and getattr(response, 'data', None):
                    db_data = response.data
                    for col in ['preferred_language', 'language_code', 'language']:
                        norm = _normalize_lang(db_data.get(col))
                        if norm:
                            return norm
            except Exception as lang_err:
                logger.warning(f"Could not fetch user language by internal id for {uid}: {lang_err}")

            return default_lang

        user_id = update.effective_user.id if update.effective_user else 0
        database = getattr(self, 'db', getattr(self, 'db_manager', None))

        # Initialize variables to prevent unbound reference warnings
        listing_id = target_id
        provider_id = safe_int(context.user_data.get("provider_id"))
        seeker_id = None

        # 🛡️ PERMANENT FIX: Universal Database-First ID Resolution for both Forward & Reverse Matches
        active_deal_id = context.user_data.get("active_negotiation_id") or context.user_data.get("deal_id")

        if database and active_deal_id:
            try:
                deal_lookup = await database.table('deals').select(
                    'listing_id, provider_id, seeker_id, vertical_type').eq('id',
                                                                            str(active_deal_id)).maybe_single().execute()
                if deal_lookup and getattr(deal_lookup, 'data', None):
                    d_data = deal_lookup.data
                    if not listing_id or listing_id in ("None", "none", ""):
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

        if is_reverse:
            provider_id = user_id

            # ✅ Respect target_id (the resolved provider listing_id passed in)
            listing_id = listing_id if listing_id and listing_id not in ("None", "none", "") else (
                    context.user_data.get("last_created_listing_id") or context.user_data.get("listing_id")
            )

            req_id = context.user_data.get('seeker_request_id') or context.user_data.get('active_housing_deal_id')

            # Resolve Seeker ID from database request record if not already resolved via active deal
            if not seeker_id:
                seeker_id = (
                        safe_int(context.user_data.get("target_seeker_id")) or
                        safe_int(context.user_data.get("active_seeker_id"))
                )

            if not seeker_id and req_id and database:
                try:
                    req_res = await database.table('seeker_requests').select('seeker_id').eq(
                        'id', str(req_id)
                    ).maybe_single().execute()
                    if req_res and getattr(req_res, 'data', None):
                        seeker_id = safe_int(req_res.data.get('seeker_id'))
                        context.user_data['target_seeker_id'] = seeker_id
                except Exception as err:
                    logger.warning(f"Failed to resolve seeker_id from request {req_id}: {err}")

            # Fallback auto-resolve listing ID if still missing
            if (not listing_id or listing_id == req_id or listing_id in ("None", "none", "")) and database:
                try:
                    lat_listing_res = await database.table('provider_listings').select('id').eq(
                        'provider_id', str(provider_id)
                    ).order('created_at', desc=True).limit(1).execute()
                    if lat_listing_res and getattr(lat_listing_res, 'data', None):
                        listing_id = lat_listing_res.data[0].get('id')
                        context.user_data['last_created_listing_id'] = listing_id
                except Exception as l_err:
                    logger.warning(f"Failed to auto-resolve latest listing ID for provider {provider_id}: {l_err}")
        else:
            seeker_id = user_id or seeker_id
            provider_id = provider_id or safe_int(context.user_data.get("provider_id"))
            listing_id = listing_id or target_id

            # ✅ Fallback: Auto-resolve provider_id from database if missing from context
            if not provider_id and listing_id and listing_id not in ("None", "none", "") and database:
                try:
                    list_res = await database.table('provider_listings').select('provider_id').eq(
                        'id', str(listing_id)
                    ).maybe_single().execute()
                    if list_res and getattr(list_res, 'data', None):
                        provider_id = safe_int(list_res.data.get('provider_id'))
                        context.user_data['provider_id'] = provider_id
                except Exception as p_err:
                    logger.warning(f"Failed to auto-resolve provider_id for listing {listing_id}: {p_err}")

        # Ensure IDs are integers for consistent comparison and DB querying
        if seeker_id:
            try:
                seeker_id = int(seeker_id)
            except ValueError:
                pass
        if provider_id:
            try:
                provider_id = int(provider_id)
            except ValueError:
                pass

        # Database-First Ground-Truth Language Resolution
        db_user_lang = await get_db_user_lang(user_id, default_lang="")
        session_lang = (
                context.user_data.get(f'lang_{user_id}') or
                context.user_data.get('language_code') or
                context.user_data.get('language') or
                context.user_data.get('preferred_language')
        )
        user_lang = _normalize_lang(db_user_lang) or _normalize_lang(session_lang) or "en"

        db_seeker_lang = await get_db_user_lang(seeker_id, default_lang="") if seeker_id else ""
        seeker_session = (
                context.user_data.get('seeker_lang_cache') or
                context.user_data.get(f'lang_{seeker_id}') or
                (seeker_id == user_id and session_lang)
        )
        seeker_lang = _normalize_lang(db_seeker_lang) or _normalize_lang(seeker_session) or "en"

        db_provider_lang = await get_db_user_lang(provider_id, default_lang="") if provider_id else ""
        provider_session = (
                context.user_data.get('provider_lang_cache') or
                context.user_data.get(f'lang_{provider_id}') or
                (provider_id == user_id and session_lang)
        )
        provider_lang = _normalize_lang(db_provider_lang) or _normalize_lang(provider_session) or "en"

        # Robust send_response helper checking for photo/caption to prevent errors
        async def send_response(text: str):
            if callback_query and callback_query.message:
                try:
                    await callback_query.edit_message_caption(caption=text, parse_mode="HTML")
                except Exception:
                    try:
                        await callback_query.edit_message_text(text=text, parse_mode="HTML")
                    except Exception:
                        try:
                            await callback_query.edit_message_caption(caption=text, parse_mode="HTML")
                        except Exception:
                            await context.bot.send_message(chat_id=update.effective_chat.id, text=text,
                                                           parse_mode="HTML")
            else:
                await context.bot.send_message(chat_id=update.effective_chat.id, text=text, parse_mode="HTML")

        if is_reverse and (
                not seeker_id or not listing_id or listing_id == req_id or listing_id in ("None", "none", "")):
            err_msg = {
                "en": "⚠️ System Error: Missing Seeker or Listing ID for Reverse Match.",
                "am": "⚠️ የስርዓት ስህተት፡ ለተቃራኒ ማዛመድ የፈላጊ ወይም የንብረት መለያ አልተገኘም።",
                "om": "⚠️ Dogoggora Sirnaa: Seeker ykn Listing ID dhabame."
            }.get(user_lang, "⚠️ System Error: Missing Seeker or Listing ID for Reverse Match.")
            await send_response(err_msg)
            return await self.route_to_main_menu(update, context)

        if not is_reverse and not provider_id:
            err_msg = {
                "en": "⚠️ System Error: Could not verify property owner.",
                "am": "⚠️ የስርዓት ስህተት፡ የንብረት ባለቤቱን ማረጋገጥ አልተቻለም።",
                "om": "⚠️ Dogoggora Sirnaa: Abbaa qabeenyaa mirkaneessuu hin danda'amne."
            }.get(user_lang, "⚠️ System Error: Could not verify property owner.")
            await send_response(err_msg)
            return await self.route_to_main_menu(update, context)

        transmitting_texts = {
            "en": "🔄 Transmitting offer...",
            "am": "🔄 የዋጋ ማቅረቢያውን በማስተላለፍ ላይ...",
            "om": "🔄 Dhiheessicha dabarsaa jira..."
        }
        await send_response(transmitting_texts.get(user_lang, transmitting_texts.get("en", "🔄 Transmitting offer...")))

        try:
            raw_prop_type = str(
                context.user_data.get('property_use') or
                context.user_data.get('housing_type') or
                context.user_data.get('asset_title') or
                'Property'
            ).strip()

            tx_type_raw = str(
                context.user_data.get('transaction_type', context.user_data.get('listing_type', 'rent'))
            ).lower()

            tx_type_display = {
                "rent": {"en": "For Rent", "am": "ለኪራይ", "om": "Kiraaf"},
                "rental": {"en": "For Rent", "am": "ለኪራይ", "om": "Kiraaf"},
                "sale": {"en": "For Sale", "am": "ለሽያጭ", "om": "Gurgurtaaf"},
                "sell": {"en": "For Sale", "am": "ለሽያጭ", "om": "Gurgurtaaf"},
                "buy": {"en": "For Buy", "am": "ለግዢ", "om": "Bitachuuf"},
                "buying": {"en": "For Buy", "am": "ለግዢ", "om": "Bitachuuf"},
                "purchase": {"en": "For Purchase", "am": "ለግዢ", "om": "Bitachuuf"}
            }

            deals_module = getattr(self, "deals", getattr(self, "deals_handler", None))

            # 1. FETCH DATABASE GROUND-TRUTH CURRENCY & METADATA FIRST BEFORE INITIATING NEGOTIATION
            listing_data = {}
            seeker_req_data = {}
            db_currency = None

            if database:
                if listing_id and listing_id not in ("None", "none", "") and hasattr(database, 'table'):
                    try:
                        res = await database.table('provider_listings').select('*').eq('id',
                                                                                       str(listing_id)).maybe_single().execute()
                        if res and getattr(res, 'data', None):
                            listing_data = res.data
                            if listing_data.get('currency'):
                                db_currency = listing_data.get('currency')
                    except Exception as db_err:
                        logger.warning(f"Failed to query provider_listings table for listing {listing_id}: {db_err}")

                req_id = context.user_data.get('seeker_request_id') or context.user_data.get('active_housing_deal_id')
                if req_id and hasattr(database, 'table'):
                    try:
                        sr_res = await database.table('seeker_requests').select('*').eq('id',
                                                                                        str(req_id)).maybe_single().execute()
                        if sr_res and getattr(sr_res, 'data', None):
                            seeker_req_data = sr_res.data
                            if not db_currency and seeker_req_data.get('currency'):
                                db_currency = seeker_req_data.get('currency')
                    except Exception as req_err:
                        logger.warning(f"Failed to fetch seeker request details for request {req_id}: {req_err}")

            # Enforce Database Ground-Truth Currency with strict fallback to ETB
            currency = str(
                db_currency or
                listing_data.get('currency') or
                seeker_req_data.get('currency') or
                context.user_data.get('currency') or
                context.user_data.get('housing_currency') or
                'ETB'
            ).strip().upper()

            # 2. INITIATE NEGOTIATION WITH GROUND-TRUTH RESOLVED 'currency'
            deal_id_str = None
            if deals_module and hasattr(deals_module, "initiate_negotiation"):
                deal_id_str = await deals_module.initiate_negotiation(
                    seeker_id=int(seeker_id),
                    provider_id=int(provider_id),
                    listing_id=str(listing_id),
                    vertical_type="housing",
                    initial_offer=agreed_price,
                    currency=currency,
                    asset_title=raw_prop_type,
                    is_reverse=is_reverse,
                    context=context
                )

            if deal_id_str:
                formatted_price = f"{agreed_price:,.2f} {currency}"

                prop_use = str(
                    context.user_data.get('property_use') or
                    context.user_data.get('housing_type') or
                    listing_data.get('property_use') or
                    listing_data.get('housing_type') or
                    listing_data.get('category') or
                    'Property'
                ).lower().strip()

                location_val = str(
                    context.user_data.get('location_text') or
                    context.user_data.get('location') or
                    listing_data.get('location_text') or
                    listing_data.get('location') or
                    'Addis Ababa'
                ).strip()

                def build_localized_asset_title(lang_code: str) -> str:
                    t_lang = _normalize_lang(lang_code) or "en"
                    translated_use = translate_property_type(prop_use, t_lang) if hasattr(
                        self, 'translate_property_type') or 'translate_property_type' in globals() else prop_use.title()
                    if t_lang == "am":
                        return f"{translated_use} በ {location_val}"
                    elif t_lang == "om":
                        return f"{translated_use} {location_val} keessatti"
                    else:
                        return f"{translated_use} in {location_val}"

                listing_beds = listing_data.get('bedrooms') or context.user_data.get('listing_bedrooms')
                seeker_beds = seeker_req_data.get('min_bedrooms') or seeker_req_data.get(
                    'bedrooms') or context.user_data.get('seeker_bedrooms') or context.user_data.get('min_bedrooms')

                listing_sqm = (
                        listing_data.get('sq_meters') or
                        listing_data.get('sq_m') or
                        listing_data.get('area') or
                        listing_data.get('sqmeters') or
                        context.user_data.get('listing_sq_meters')
                )
                seeker_sqm = (
                        seeker_req_data.get('min_sq_meters') or
                        seeker_req_data.get('min_sq_m') or
                        seeker_req_data.get('sq_meters') or
                        seeker_req_data.get('sq_m') or
                        seeker_req_data.get('area') or
                        context.user_data.get('seeker_sq_meters') or
                        context.user_data.get('min_sq_meters')
                )

                if not listing_beds and not seeker_beds:
                    general_beds = context.user_data.get('bedrooms') or context.user_data.get('number_of_bedrooms')
                    if general_beds:
                        if is_reverse:
                            listing_beds = general_beds
                        else:
                            seeker_beds = general_beds

                if not listing_sqm and not seeker_sqm:
                    general_sqm = context.user_data.get('sq_meters') or context.user_data.get('area')
                    if general_sqm:
                        if is_reverse:
                            listing_sqm = general_sqm
                        else:
                            seeker_sqm = general_sqm

                spec_lines = {"en": [], "am": [], "om": []}

                if listing_sqm:
                    spec_lines["en"].append(f"📐 <b>Listing Area:</b> {listing_sqm} m²")
                    spec_lines["am"].append(f"📐 <b>የንብረት ስፋት (Listing Area):</b> {listing_sqm} ካሬ ሜትር")
                    spec_lines["om"].append(f"📐 <b>Bal'ina Qabeenyaa (Listing Area):</b> {listing_sqm} m²")

                if seeker_sqm:
                    spec_lines["en"].append(f"📐 <b>Requested Area:</b> {seeker_sqm} m²")
                    spec_lines["am"].append(f"📐 <b>የተጠየቀው ስፋት (Requested Area):</b> {seeker_sqm} ካሬ ሜትር")
                    spec_lines["om"].append(f"📐 <b>Bal'ina Gaafatame (Requested Area):</b> {seeker_sqm} m²")

                # Helper function to convert numeric values (e.g., 3.0 or "3.0") cleanly to integer strings ("3")
                def _format_int_beds(bed_val):
                    try:
                        return str(int(float(bed_val)))
                    except (ValueError, TypeError):
                        return str(bed_val)

                if listing_beds and str(listing_beds) != 'N/A':
                    formatted_listing_beds = _format_int_beds(listing_beds)
                    spec_lines["en"].append(f"🛏️ <b>Listing Bedrooms:</b> {formatted_listing_beds}")
                    spec_lines["am"].append(f"🛏️ <b>የንብረት መኝታ ክፍሎች:</b> {formatted_listing_beds}")
                    spec_lines["om"].append(f"🛏️ <b>Kutaalee Ciisichaa Qabeenyaa:</b> {formatted_listing_beds}")

                if seeker_beds and str(seeker_beds) != 'N/A':
                    formatted_seeker_beds = _format_int_beds(seeker_beds)
                    spec_lines["en"].append(f"🛏️ <b>Requested Bedrooms:</b> {formatted_seeker_beds}")
                    spec_lines["am"].append(f"🛏️ <b>የተጠየቁ መኝታ ክፍሎች:</b> {formatted_seeker_beds}")
                    spec_lines["om"].append(f"🛏️ <b>Kutaalee Ciisichaa Gaafataman:</b> {formatted_seeker_beds}")

                spec_info = {
                    "en": ("\n" + "\n".join(spec_lines["en"])) if spec_lines["en"] else "",
                    "am": ("\n" + "\n".join(spec_lines["am"])) if spec_lines["am"] else "",
                    "om": ("\n" + "\n".join(spec_lines["om"])) if spec_lines["om"] else ""
                }

                if is_reverse:
                    tx_disp_prov = tx_type_display.get(tx_type_raw, {"en": f"For {tx_type_raw.capitalize()}",
                                                                     "am": f"ለ{tx_type_raw}", "om": f"{tx_type_raw}f"})
                    local_asset_title_user = build_localized_asset_title(user_lang)
                    provider_transmit_texts = {
                        "en": (
                            f"✅ <b>Provider Offer Transmitted!</b>\n\n"
                            f"Your proposed rate of <b>{formatted_price}</b> ({tx_disp_prov['en']}) for "
                            f"<i>{html.escape(local_asset_title_user)}</i> has been successfully sent to the seeker."
                            f"{spec_info['en']}"
                        ),
                        "am": (
                            f"✅ <b>የአቅራቢው ማቅረቢያ ተላልፏል!</b>\n\n"
                            f"ለ <i>{html.escape(local_asset_title_user)}</i> ({tx_disp_prov['am']}) ያቀረቡት ዋጋ <b>{formatted_price}</b> ለፈላጊው በተሳካ ሁኔታ ተልኳል።"
                            f"{spec_info['am']}"
                        ),
                        "om": (
                            f"✅ <b>Dhiheessiin Dhiyeessaa Darbeera!</b>\n\n"
                            f"Gatiin dhiheessitan <b>{formatted_price}</b> ({tx_disp_prov['om']}) <i>{html.escape(local_asset_title_user)}</i>uf barbaadaaf milkaa'inaan ergameera."
                            f"{spec_info['om']}"
                        )
                    }
                    await send_response(provider_transmit_texts.get(user_lang, provider_transmit_texts.get("en", "")))

                    try:
                        has_mismatch = context.user_data.get('has_location_mismatch', False)
                        seeker_loc = html.escape(context.user_data.get('seeker_location', 'Your Requested Location'))
                        provider_loc = html.escape(context.user_data.get('provider_location', 'Alternative Location'))

                        if has_mismatch:
                            loc_alerts = {
                                "en": f"\n\n📍 <b>Your Preferred Location:</b> <code>{seeker_loc}</code>\n📍 <b>Provider's Proposed Location:</b> <code>{provider_loc}</code>\n⚠️ <i>Note: This offer is for a property in an alternative location.</i>",
                                "am": f"\n\n📍 <b>የእርስዎ ተመራጭ ቦታ፡</b> <code>{seeker_loc}</code>\n📍 <b>የአቅራቢው የቀረበ ቦታ፡</b> <code>{provider_loc}</code>\n⚠️ <i>ማስታወሻ፡ ይህ ማቅረቢያ በተለየ አካባቢ ለሚገኝ ንብረት ነው።</i>",
                                "om": f"\n\n📍 <b>Iddoo Filatanii:</b> <code>{seeker_loc}</code>\n📍 <b>Iddoo Dhihaate:</b> <code>{provider_loc}</code>\n⚠️ <i>Hubachiisa: Dhiheessiin kun iddoo biraa irratti argama.</i>"
                            }
                        else:
                            loc_alerts = {
                                "en": f"\n\n📍 <b>Location Match:</b> <code>{provider_loc}</code>",
                                "am": f"\n\n📍 <b>የቦታ ስምምነት፡</b> <code>{provider_loc}</code>",
                                "om": f"\n\n📍 <b>Iddoo Wal simatu:</b> <code>{provider_loc}</code>"
                            }

                        location_alert = loc_alerts.get(seeker_lang, loc_alerts.get("en", ""))
                        tx_disp_seek = tx_type_display.get(tx_type_raw, {"en": f"For {tx_type_raw.capitalize()}",
                                                                         "am": f"ለ{tx_type_raw}",
                                                                         "om": f"{tx_type_raw}f"})
                        local_asset_title_seeker = build_localized_asset_title(seeker_lang)

                        notify_texts = {
                            "en": (
                                f"🔔 <b>New Property Offer Received!</b>\n\n"
                                f"A property owner has proposed a rate for your request:\n\n"
                                f"🏷 <b>Property:</b> <i>{html.escape(local_asset_title_seeker)}</i>\n"
                                f"🤝 <b>Type:</b> {tx_disp_seek['en']}\n"
                                f"💰 <b>Proposed Rate:</b> <b>{formatted_price}</b>"
                                f"{spec_info['en']}"
                                f"{location_alert}\n\n"
                                f"👇 Please choose an action below to respond:"
                            ),
                            "am": (
                                f"🔔 <b>አዲስ የንብረት ማቅረቢያ ደርሶዎታል!</b>\n\n"
                                f"አንድ የንብረት ባለቤት ለጠየቁት ጥያቄ ዋጋ አቅርቧል፡\n\n"
                                f"🏷 <b>ንብረት፡</b> <i>{html.escape(local_asset_title_seeker)}</i>\n"
                                f"🤝 <b>ዓይነት፡</b> {tx_disp_seek['am']}\n"
                                f"💰 <b>የቀረበው ዋጋ፡</b> <b>{formatted_price}</b>"
                                f"{spec_info['am']}"
                                f"{location_alert}\n\n"
                                f"👇 እባክዎን ምላሽ ለመስጠት ከታች ካሉት አማራጮች አንዱን ይምረጡ፡"
                            ),
                            "om": (
                                f"🔔 <b>Dhiheessiin Qabeenya Haaraa Argamera!</b>\n\n"
                                f"Abbaan qabeenyaa gaaffii keessaniif gatii dhiheesseera:\n\n"
                                f"🏷 <b>Qabeenya:</b> <i>{html.escape(local_asset_title_seeker)}</i>\n"
                                f"🤝 <b>Gosa:</b> {tx_disp_seek['om']}\n"
                                f"💰 <b>Gatii Dhihaate:</b> <b>{formatted_price}</b>"
                                f"{spec_info['om']}"
                                f"{location_alert}\n\n"
                                f"👇 Deebii kennuuf maaloo armaan gadii keessaa filadhaa:"
                            )
                        }
                        seeker_notify_msg = notify_texts.get(seeker_lang, notify_texts.get("en", ""))

                        btn_texts = {
                            "en": ("✅ Accept Price", "📉 Counter Offer", "❌ Decline Deal"),
                            "am": ("✅ ዋጋ ተቀበል", "📉 የሐሳብ ዋጋ አቅርብ", "❌ ውድቅ አድርግ"),
                            "om": ("✅ Gatii Fudhachuu", "📉 Gatii yaada deebii", "❌ Diduu")
                        }
                        b_acc, b_cnt, b_dec = btn_texts.get(seeker_lang,
                                                            btn_texts.get("en", ("Accept", "Counter", "Decline")))

                        keyboard = [
                            [
                                InlineKeyboardButton(b_acc, callback_data=f"deal_accept:{deal_id_str}"),
                                InlineKeyboardButton(b_cnt, callback_data=f"deal_counter:{deal_id_str}")
                            ],
                            [
                                InlineKeyboardButton(b_dec, callback_data=f"deal_decline:{deal_id_str}")
                            ]
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
                                            return parsed_json.get("url") or parsed_json.get(
                                                "file_id") or parsed_json.get("path")
                                    except Exception:
                                        pass
                                return cleaned if cleaned else None
                            if isinstance(item, dict):
                                return item.get("url") or item.get("file_id") or item.get("path") or item.get(
                                    "image_url")
                            return None

                        for key in ['photo_url', 'image_url', 'photo_file_id', 'images', 'photos']:
                            val = listing_data.get(key) or context.user_data.get(key)
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
                                    logger.warning(f"Failed to generate pre-signed URL for '{photo_ref}': {sign_err}")
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
                                logger.warning(f"Failed to dispatch photo card to seeker {seeker_id}: {photo_err}")

                        if not photo_dispatched:
                            await context.bot.send_message(
                                chat_id=int(seeker_id),
                                text=seeker_notify_msg,
                                parse_mode="HTML",
                                reply_markup=reply_markup
                            )
                    except Exception as notify_err:
                        logger.error(f"Failed to notify housing seeker {seeker_id}: {notify_err}")
                else:
                    tx_disp_seek = tx_type_display.get(tx_type_raw, {"en": f"For {tx_type_raw.capitalize()}",
                                                                     "am": f"ለ{tx_type_raw}",
                                                                     "om": f"{tx_type_raw}f"})
                    local_asset_title_user = build_localized_asset_title(user_lang)
                    seeker_transmit_texts = {
                        "en": (
                            f"✅ <b>Offer Transmitted!</b>\n\n"
                            f"Your proposed offer of <b>{formatted_price}</b> ({tx_disp_seek['en']}) for "
                            f"<i>{html.escape(local_asset_title_user)}</i> "
                            f"has been securely forwarded.{spec_info['en']}\n\n"
                            f"The owner will be notified immediately."
                        ),
                        "am": (
                            f"✅ <b>የዋጋ ማቅረቢያ ተልኳል!</b>\n\n"
                            f"ለ <i>{html.escape(local_asset_title_user)}</i> ({tx_disp_seek['am']}) ያቀረቡት ዋጋ <b>{formatted_price}</b> ደህንነቱ በተጠበቀ ሁኔታ ተላልፏል።{spec_info['am']}\n\n"
                            f"ባለቤቱ ወዲያውኑ ይነገረዋል።"
                        ),
                        "om": (
                            f"✅ <b>Dhiheessiin Ergameera!</b>\n\n"
                            f"Gatiin dhiheessitan <b>{formatted_price}</b> ({tx_disp_seek['om']}) <i>{html.escape(local_asset_title_user)}</i>f milkaa'inaan ergameera.{spec_info['om']}\n\n"
                            f"Abbaan qabeenyaa saffisaan ni beeksifama."
                        )
                    }
                    await send_response(seeker_transmit_texts.get(user_lang, seeker_transmit_texts.get("en", "")))

                    try:
                        tx_disp_prov = tx_type_display.get(tx_type_raw, {"en": f"For {tx_type_raw.capitalize()}",
                                                                         "am": f"ለ{tx_type_raw}",
                                                                         "om": f"{tx_type_raw}f"})
                        local_asset_title_provider = build_localized_asset_title(provider_lang)
                        provider_notify_texts = {
                            "en": (
                                f"📩 <b>New Housing Offer Received!</b>\n\n"
                                f"A seeker has submitted an offer for your listing:\n\n"
                                f"🏷 <b>Property:</b> <i>{html.escape(local_asset_title_provider)}</i>\n"
                                f"🤝 <b>Type:</b> {tx_disp_prov['en']}\n"
                                f"💰 <b>Offered Rate:</b> <b>{formatted_price}</b>"
                                f"{spec_info['en']}\n\n"
                                f"👇 Please choose an action below:"
                            ),
                            "am": (
                                f"📩 <b>አዲስ የንብረት ማቅረቢያ ደርሶዎታል!</b>\n\n"
                                f"ፈላጊ ለንብረትዎ ዋጋ አቅርቧል፡\n\n"
                                f"🏷 <b>ንብረት፡</b> <i>{html.escape(local_asset_title_provider)}</i>\n"
                                f"🤝 <b>ዓይነት፡</b> {tx_disp_prov['am']}\n"
                                f"💰 <b>የቀረበው ዋጋ፡</b> <b>{formatted_price}</b>"
                                f"{spec_info['am']}\n\n"
                                f"👇 እባክዎን ከታች ካሉት አማራጮች አንዱን ይምረጡ፡"
                            ),
                            "om": (
                                f"📩 <b>Dhiheessiin Qabeenya Haaraa Argamera!</b>\n\n"
                                f"Barbaadaan qabeenya keessaniif gatii dhiheesseera:\n\n"
                                f"🏷 <b>Qabeenya:</b> <i>{html.escape(local_asset_title_provider)}</i>\n"
                                f"🤝 <b>Gosa:</b> {tx_disp_prov['om']}\n"
                                f"💰 <b>Gatii Dhihaate:</b> <b>{formatted_price}</b>"
                                f"{spec_info['om']}\n\n"
                                f"👇 Maaloo armaan gadii keessaa filadhaa:"
                            )
                        }
                        provider_notify_msg = provider_notify_texts.get(provider_lang,
                                                                        provider_notify_texts.get("en", ""))

                        btn_texts = {
                            "en": ("✅ Accept Offer", "📉 Counter Offer", "❌ Decline"),
                            "am": ("✅ ዋጋ ተቀበል", "📉 የሐሳብ ዋጋ አቅርብ", "❌ ውድቅ አድርግ"),
                            "om": ("✅ Fudhachuu", "📉 Gatii yaada deebii", "❌ Diduu")
                        }
                        b_acc, b_cnt, b_dec = btn_texts.get(provider_lang,
                                                            btn_texts.get("en", ("Accept", "Counter", "Decline")))

                        keyboard = [
                            [
                                InlineKeyboardButton(b_acc, callback_data=f"deal_accept:{deal_id_str}"),
                                InlineKeyboardButton(b_cnt, callback_data=f"deal_counter:{deal_id_str}")
                            ],
                            [
                                InlineKeyboardButton(b_dec, callback_data=f"deal_decline:{deal_id_str}")
                            ]
                        ]
                        await context.bot.send_message(
                            chat_id=int(provider_id),
                            text=provider_notify_msg,
                            parse_mode="HTML",
                            reply_markup=InlineKeyboardMarkup(keyboard)
                        )
                    except Exception as notify_err:
                        logger.error(f"Failed to notify housing provider {provider_id}: {notify_err}")
            else:
                blocked_texts = {
                    "en": "🚫 <b>Negotiation Blocked</b>\n\nThis property is currently under active negotiation.",
                    "am": "🚫 <b>ድርድር ታግዷል</b>\n\nይህ ንብረት አሁን በድርድር ላይ ይገኛል።",
                    "om": "🚫 <b>Mariin Cufameera</b>\n\nQabeenyi kun ammaan tana marii irra jira."
                }
                await send_response(blocked_texts.get(user_lang, blocked_texts.get("en", "")))

        except Exception as e:
            logger.error(f"Unexpected error in housing negotiation: {str(e)}", exc_info=True)
            err_sys = {
                "en": "⚠️ System Error: Could not initiate negotiation.",
                "am": "⚠️ የስርዓት ስህተት፡ ድርድር መጀመር አልተቻለም።",
                "om": "⚠️ Dogoggora Sirnaa: Marii eegaluu hin danda'amne."
            }.get(user_lang, "⚠️ System Error: Could not initiate negotiation.")
            await send_response(err_sys)

        context.user_data.pop('is_reverse_match', None)
        return await self.route_to_main_menu(update, context)

    # ==========================================
    # ESCAPE & CANCELLATION PATHS
    # ==========================================

    async def handle_match_cancellation(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        query = update.callback_query
        if query:
            try:
                await query.answer("Search cancelled.")
            except Exception as e:
                logger.debug(f"Could not answer expired callback query: {e}")

        lang = self._get_lang(context)

        context.user_data.pop('housing_top_matches', None)
        context.user_data.pop('seeker_top_matches', None)
        context.user_data.pop('is_reverse_match', None)
        cancel_msg = get_text(lang, "msg_match_cancelled") or "❌ Search cancelled. Returning to main menu."

        try:
            if query and query.message:
                message = query.message
                if hasattr(message, "text") and message.text:
                    await query.edit_message_text(cancel_msg, parse_mode="HTML")
                elif hasattr(message, "caption") and message.caption:
                    await query.edit_message_caption(cancel_msg, parse_mode="HTML")
                else:
                    await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=message.message_id)
                    await context.bot.send_message(chat_id=update.effective_chat.id, text=cancel_msg, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Failed to cancel match: {e}")

        return await self.route_to_main_menu(update, context)

    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        lang = self._get_lang(context)
        cancel_text = get_text(lang,
                               "msg_process_cancelled") or "🛑 <b>Process cancelled.</b> Returning to main menu..."
        chat_id = update.effective_chat.id if update.effective_chat else None

        if update.callback_query:
            query = update.callback_query
            try:
                await query.answer()
            except Exception as e:
                logger.debug(f"Could not answer expired callback query: {e}")

            try:
                await query.edit_message_text(text=cancel_text, parse_mode="HTML")
            except Exception:
                if chat_id:
                    await context.bot.send_message(chat_id=chat_id, text=cancel_text, parse_mode="HTML")
        elif update.message:
            await update.message.reply_text(cancel_text, parse_mode="HTML")

        return await self.route_to_main_menu(update, context)

    def get_conversation_handler(self) -> ConversationHandler:
        """
        Constructs and returns an optimized ConversationHandler for housing rental/sale flows.
        """
        from src.smartmatch.handlers.main_menu import MainMenuHandler

        list_house_labels = filters.Text(MainMenuHandler.get_labels_for_action("btn_list_house"))
        find_house_labels = filters.Text(MainMenuHandler.get_labels_for_action("btn_find_house"))

        # Dedicated handlers for active deal negotiations (Accept, Counter, Decline)
        deal_handlers = [
            CallbackQueryHandler(self.handle_deal_accept, pattern=r"^deal_accept:.*$", block=False),
            CallbackQueryHandler(self.handle_deal_counter, pattern=r"^deal_counter:.*$", block=False),
            CallbackQueryHandler(self.handle_deal_decline, pattern=r"^deal_decline:.*$", block=False),
        ]

        return ConversationHandler(
            entry_points=[
                CommandHandler('list_house', self.start_list_house, block=False),
                CommandHandler('find_house', self.start_find_house, block=False),
                MessageHandler(list_house_labels, self.start_list_house, block=False),
                MessageHandler(find_house_labels, self.start_find_house, block=False),
                CallbackQueryHandler(self.start_list_house, pattern=r"^housing_list$", block=False),
                CallbackQueryHandler(self.start_find_house, pattern=r"^housing_find$", block=False),
                # ✅ Global deal action entry points to catch counter-offers/negotiations anywhere
                *deal_handlers,
            ],

            states={
                self.LISTING_TYPE: [
                    CallbackQueryHandler(
                        self.handle_listing_type,
                        pattern=r"^(action_rent|action_sell|action_buy)$",
                        block=False
                    ),
                    *deal_handlers,
                ],
                self.PROPERTY_USE: [
                    CallbackQueryHandler(
                        self.handle_property_use,
                        pattern=r"^use_",  # Matches any 'use_*' callback prefix
                        block=False
                    ),
                    *deal_handlers,
                ],
                self.LOCATION_DATA: [
                    MessageHandler(
                        filters.LOCATION | (filters.TEXT & ~filters.COMMAND),
                        self.handle_location_data,
                        block=False
                    ),
                    *deal_handlers,
                ],
                self.PRICE_BUDGET: [
                    MessageHandler(
                        filters.TEXT & ~filters.COMMAND,
                        self.handle_price_budget,
                        block=False
                    ),
                    *deal_handlers,
                ],
                self.SIZE_SPECS: [
                    MessageHandler(
                        filters.TEXT & ~filters.COMMAND,
                        self.handle_size_specs,
                        block=False
                    ),
                    *deal_handlers,
                ],
                self.AMENITIES_DATA: [
                    MessageHandler(
                        filters.TEXT & ~filters.COMMAND,
                        self.handle_amenities_data,
                        block=False
                    ),
                    *deal_handlers,
                ],
                self.PHOTO_UPLOAD: [
                    MessageHandler(
                        filters.PHOTO | (filters.TEXT & ~filters.COMMAND),
                        self.handle_photo_upload,
                        block=False
                    ),
                    *deal_handlers,
                ],
                self.BULK_QUANTITY: [
                    MessageHandler(
                        filters.TEXT & ~filters.COMMAND,
                        self.handle_bulk_quantity,
                        block=False
                    ),
                    *deal_handlers,
                ],
                self.CONTACT_INFO: [
                    MessageHandler(
                        filters.TEXT & ~filters.COMMAND,
                        self.handle_contact_info,
                        block=False
                    ),
                    *deal_handlers,
                ],
                self.SELECTING_MATCH: [
                    CallbackQueryHandler(
                        self.handle_match_selection,
                        pattern=r"^select_(?:house|seeker)_match:.*$",
                        block=False
                    ),
                    CallbackQueryHandler(
                        self.prompt_custom_offer,
                        pattern=r"^prompt_offer:.*$",
                        block=False
                    ),
                    CallbackQueryHandler(
                        self.execute_deal_lock,
                        pattern=r"^lock_housing_deal:.*$",
                        block=False
                    ),
                    CallbackQueryHandler(
                        self.handle_back_to_top_matches,
                        pattern=r"^back_to_top_matches$",
                        block=False
                    ),
                    CallbackQueryHandler(
                        self.handle_match_cancellation,
                        pattern=r"^cancel_match$",
                        block=False
                    ),
                    *deal_handlers,
                ],
                self.ENTERING_OFFER: [
                    MessageHandler(
                        filters.TEXT & ~filters.COMMAND,
                        self.receive_custom_offer,
                        block=False
                    ),
                    CallbackQueryHandler(
                        self.execute_deal_lock,
                        pattern=r"^lock_housing_deal:.*$",
                        block=False
                    ),
                    CallbackQueryHandler(
                        self.handle_back_to_top_matches,
                        pattern=r"^back_to_top_matches$",
                        block=False
                    ),
                    *deal_handlers,
                ]
            },
            fallbacks=[
                CommandHandler('cancel', self.cancel, block=False),
                CommandHandler('start', self.route_to_main_menu, block=False),
                CallbackQueryHandler(
                    self.handle_match_cancellation,
                    pattern=r"^cancel_match$",
                    block=False
                ),
                CallbackQueryHandler(
                    self.route_to_main_menu,
                    pattern=r"^(cancel_to_main|cancel_vertical_offer)$",
                    block=False
                ),
                *deal_handlers,
            ],
            name="housing_rental_conversation",
            persistent=True,
            per_user=True,
            per_chat=True,
            per_message=False,
            allow_reentry=True,
            conversation_timeout=None
        )


def setup_housing_handler(db, ai, matching, billing, storage, deals_handler, queue) -> ConversationHandler:
    handler = HousingHandler(db, ai, matching, billing, storage, deals_handler, queue)
    return handler.get_conversation_handler()
