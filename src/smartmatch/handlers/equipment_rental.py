import logging
import uuid
import json
import re
from typing import Dict, Any, Optional, List, Tuple
import asyncio
from deep_translator import GoogleTranslator
import html
import telegram
from telegram.error import BadRequest, TelegramError
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardRemove,
    ReplyKeyboardMarkup
)
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters
)

from src.smartmatch.utils.translations import get_text

# Initialize production-grade telemetry logging
logger = logging.getLogger("smartmatch.handlers.equipment")

# ==========================================
# MODULE-LEVEL CONSTANTS & SAFE HELPERS
# ==========================================


# ==========================================
# PRE-COMPILED REGEX PATTERNS (FAST PATH)
# ==========================================
_TRANSACTION_PATTERN = re.compile(
    r"\b(rental|renting|rent|for rent|for sale|sale|leasing|lease|hire|for hire)\b",
    re.IGNORECASE,
)
_CLEAN_SPACES_PATTERN = re.compile(r"[\s\-_]+")
_NUMERIC_PATTERN = re.compile(r"(\d+(?:\.\d+)?)")
_NON_ASCII_PATTERN = re.compile(r"[^\x00-\x7F]")
_WORDS_PATTERN = re.compile(r"\b\w+\b")
_JSON_EXTRACT_PATTERN = re.compile(r"\{.*}", re.DOTALL)
_CAPACITY_MATCH_PATTERN = re.compile(
    r"(\d+(?:\.\d+)?)\s*(hp|horsepower|ton|tons|t|kva|kw|m3|l|liter|liters)\b",
    re.IGNORECASE,
)

PHONETIC_FIXES_COMPILED = [
    (re.compile(r"\b(john\s*dir|jon\s*dir|john\s*dr)\b", re.IGNORECASE), "John Deere"),
    (re.compile(r"\b(commens|cumins|comins|cummens)\b", re.IGNORECASE), "Cummins"),
    (re.compile(r"\b(caterpilar|caterpiller)\b", re.IGNORECASE), "Caterpillar"),
    (re.compile(r"\b(komatso)\b", re.IGNORECASE), "Komatsu"),
]

REGIONAL_EQUIPMENT_LEXICON = {
    "maashiina haamaa": "Combine Harvester",
    "maashina haamaa": "Combine Harvester",
    "maashiina-haamaa": "Combine Harvester",
    "maashina-haamaa": "Combine Harvester",
    "maashinii haamaa": "Combine Harvester",
    "haamaa": "Harvester",
    "traaktera": "Tractor",
    "traktara": "Tractor",
    "kombayini": "Combine Harvester",
    "parkiins": "Perkins",
    "ifa waltajjii fi sagalee pro": "Pro Stage Lighting and Audio",
    "pro ifa waltajjii fi sagalee": "Pro Stage Lighting and Audio",
    "meeshaa sagalee fi ifa ayyaanaa": "Event Lighting and Sound Equipment",
    "meeshaa sagaleef ifa ayyaanaa": "Event Lighting and Sound Equipment",
    "ifa waltajjii": "Stage Lighting",
    "sagalee": "Audio/Sound System",
    "komaatsuu": "Komatsu",
}

ETHIOPIAN_LOCATIONS = {
    "gudar": "Guder", "adaamaa": "Adama", "addis ababa": "Addis Ababa",
    "finfinne": "Addis Ababa", "adama": "Adama", "nazret": "Adama",
    "bishoftu": "Bishoftu", "debre zeyit": "Bishoftu", "hawassa": "Hawassa",
    "awasa": "Hawassa", "jimma": "Jimma", "jima": "Jimma",
    "mekelle": "Mekelle", "mekele": "Mekelle", "dire dawa": "Dire Dawa",
    "bahir dar": "Bahir Dar", "gondar": "Gondar", "gonder": "Gondar",
}

KNOWN_ENGLISH_WORDS = {
    "equipment", "rental", "pro", "stage", "lighting", "audio", "sound",
    "general", "active", "extracted", "specs", "brand", "capacity",
    "features", "standard", "any", "unknown", "none",
}

CITY_FIXES: Dict[str, str] = {
    "adam": "Adama",
    "bishoftu": "Bishoftu",
    "deberezeyit": "Bishoftu",
    "hawasa": "Hawassa"
}

BRAND_FIXES: Dict[str, str] = {
    "john dir": "John Deere",
    "commens": "Cummins",
    "caterpilar": "Caterpillar",
    "komatso": "Komatsu"
}

KNOWN_BRANDS: List[str] = [
    "John Deere", "Caterpillar", "CAT", "Massey Ferguson", "Komatsu",
    "JCB", "JBL", "Sany", "Hitachi", "Kubota", "Perkins", "Cummins", "XCMG",
    "Hyundai", "Volvo", "Mahindra", "Tafe", "New Holland", "Case", "Shantui"
]


def apply_city_fixes(loc_string: Optional[str]) -> Optional[str]:
    """Normalizes known city alias edge-cases in O(1) lookup time."""
    if not loc_string:
        return loc_string
    return CITY_FIXES.get(str(loc_string).strip().lower(), loc_string)


def apply_brand_fixes(brand_string: Optional[str]) -> Optional[str]:
    """Normalizes transliterated phonetic brand names in O(1) lookup time."""
    if not brand_string:
        return brand_string
    return BRAND_FIXES.get(str(brand_string).strip().lower(), brand_string)


def safe_float(value: Any, default: float = 0.0) -> float:
    """
    Module-level safe float parser.
    Extracts numeric floats from strings like "20 Tons", "500 kVA", "1,200 ETB".
    """
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    try:
        val_str = str(value).replace(',', '').strip()
        match = re.search(r'(\d+(?:\.\d+)?)', val_str)
        if match:
            return float(match.group(1))
        return float(val_str) if val_str else default
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


class EquipmentRentalHandler:
    """
    Production-Grade Industrial & Commercial Equipment Rental Handler.
    Acts as the domain controller linking main_menu, deals, matching, billing, and webhooks.
    Optimized for Supabase/PostgreSQL async operations and Backblaze B2 object storage.
    """

    # Finite State Automata Definition
    (
        AWAITING_EQUIPMENT_OFFER,
        FLOW_SELECTION,
        EQUIPMENT_CATEGORY,
        EQUIPMENT_TYPE,
        CAPACITY_OUTPUT,
        BRAND_FEATURES,
        LOCATION_DATA,
        RATES_BUDGET,
        CONDITION_PREFS,
        PHOTO_UPLOAD,
        CONTACT_INFO,
        SELECTING_MATCH,
        ENTERING_OFFER
    ) = range(13)

    def __init__(self, db_manager, ai_engine, matching_engine, billing_service, image_service, deals_handler,
                 queue_manager):
        """
        Thread-safe injection of centralized shared application infrastructure layers.
        """
        self.db = db_manager
        self.ai = ai_engine
        self.matching = matching_engine
        self.billing = billing_service
        self.storage = image_service
        self.queue = queue_manager
        self.deals = deals_handler

    async def deal_accept_handler(
            self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> Optional[int]:
        """
        Handles the 'equip_deal_accept:' callback.
        Extracts the deal ID, fetches the fresh deal record from Supabase,
        and delegates execution to _execute_deal_acceptance without silent failures.
        """
        query = update.callback_query
        if not query:
            return None

        await query.answer()

        deal_id_str = None
        if query.data and ":" in query.data:
            deal_id_str = query.data.split(":")[1].strip()

        if not deal_id_str:
            logger.warning("deal_accept_handler received callback without a valid deal ID.")
            return None

        # Fetch deal record directly from Supabase to ensure complete metadata
        deal_record = None
        supabase_client = getattr(self.db, 'supabase', None) or getattr(self, 'db', None)
        if supabase_client and hasattr(supabase_client, "table"):
            try:
                deal_res = await supabase_client.table('deals').select('*').eq('id',
                                                                               deal_id_str).maybe_single().execute()
                if deal_res and getattr(deal_res, 'data', None):
                    deal_record = deal_res.data
            except Exception as d_err:
                logger.warning(f"Failed to fetch deal {deal_id_str} in deal_accept_handler: {d_err}")

        if not deal_record:
            logger.error(f"Cannot accept deal {deal_id_str}: Record not found in database.")
            try:
                await query.answer(text="⚠️ Deal record not found or expired.", show_alert=True)
            except Exception:
                pass
            return None

        # Delegate directly to the robust centralized deal acceptance executor
        if hasattr(self, "execute_deal_acceptance"):
            return await self.execute_deal_acceptance(update, context, deal_id_str, deal_record)
        elif hasattr(self.deals, "execute_deal_acceptance"):
            return await self.deals.execute_deal_acceptance(update, context, deal_id_str, deal_record)
        else:
            logger.critical("execute_deal_acceptance method not found in service context.")
            return None

    async def deal_counter_handler(
            self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        """
        Handles the 'equip_deal_counter:' callback with robust multilingual support
        (English, Amharic, and Afaan Oromo) fetching directly from ground-truth database
        tables (deals, provider_listings, seeker_requests) and distinguishing Asset Location
        from Deployment Location.
        """
        query = update.callback_query
        if not query:
            return getattr(self, "ENTERING_OFFER", 1)

        await query.answer()
        if query.data and ":" in query.data:
            match_id = query.data.split(":")[1]
            context.user_data["active_equipment_deal_id"] = match_id
            context.user_data["active_deal_id"] = match_id
            context.user_data["current_negotiation_deal_id"] = match_id

        # Ground-Truth Language Resolution (DB -> Context -> Default)
        async def resolve_user_language(uid: int) -> str:
            if uid and hasattr(self, "db") and self.db:
                try:
                    user_res = (
                        await self.db.table("users")
                        .select("preferred_language, language_code, language")
                        .eq("telegram_id", int(uid))
                        .maybe_single()
                        .execute()
                    )
                    if user_res and hasattr(user_res, "data") and user_res.data:
                        udata = user_res.data
                        pref = (
                                udata.get("preferred_language")
                                or udata.get("language_code")
                                or udata.get("language")
                                or ""
                        ).strip().lower()
                        if pref in ["en", "am", "om"]:
                            return pref
                except Exception as l_err:
                    logger.warning(f"Failed to fetch language from DB for user {uid}: {l_err}")

            ctx_lang = (
                    context.user_data.get("lang")
                    or context.user_data.get("language")
                    or "en"
            ).strip().lower()
            if ctx_lang in ["en", "am", "om"]:
                return ctx_lang
            return "en"

        effective_uid = update.effective_user.id if update.effective_user else 0
        user_lang = await resolve_user_language(effective_uid)

        active_deal_id = str(
            context.user_data.get("active_equipment_deal_id")
            or context.user_data.get("active_deal_id")
            or "pending"
        )

        # Initialize Ground Truth Grounding Variables (bypassing stale context)
        is_reverse = False
        asking_price: float = 0.0
        currency = "ETB"
        model_name = "Selected Equipment"
        brand_name = None
        property_use = None
        asset_location = None
        deployment_location = None
        raw_capacity_val = None

        req_eq = None
        list_eq = None

        listing_id = None
        _request_id = None
        _deal_data = {}

        # SINGLE SOURCE OF TRUTH: Query DB explicitly
        if active_deal_id != "pending" and hasattr(self, "db") and self.db:
            try:
                deal_res = (
                    await self.db.table("deals")
                    .select("id, current_price, listing_id, request_id, currency, seeker_id, provider_id, asset_title")
                    .eq("id", active_deal_id)
                    .maybe_single()
                    .execute()
                )

                if deal_res and hasattr(deal_res, "data") and deal_res.data:
                    deal_data = deal_res.data
                    current_user_id = effective_uid

                    if str(deal_data.get("seeker_id")) == str(current_user_id):
                        is_reverse = True

                    if deal_data.get("currency"):
                        currency = str(deal_data["currency"]).upper()

                    if deal_data.get("current_price") is not None:
                        asking_price = safe_float(deal_data["current_price"]) or 0.0

                    if deal_data.get("asset_title"):
                        model_name = deal_data.get("asset_title")

                    listing_id = deal_data.get("listing_id")
                    request_id = deal_data.get("request_id")

                    # Fetch Seeker Request Ground-Truth (Deployment Location & Requirements)
                    if request_id:
                        req_res = (
                            await self.db.table("seeker_requests")
                            .select(
                                "equipment_type, property_use, vehicle_type, capacity_output, "
                                "currency, location_text, preferred_brand"
                            )
                            .eq("id", request_id)
                            .maybe_single()
                            .execute()
                        )
                        if req_res and hasattr(req_res, "data") and req_res.data:
                            rdata = req_res.data
                            deployment_location = rdata.get("location_text")
                            req_eq = (
                                    rdata.get("equipment_type")
                                    or rdata.get("property_use")
                                    or rdata.get("vehicle_type")
                            )
                            raw_capacity_val = rdata.get("capacity_output")
                            if rdata.get("preferred_brand") and not brand_name:
                                brand_name = rdata.get("preferred_brand")

            except Exception as e:
                logger.warning(f"Could not fetch deal ground-truth metadata in deal_counter_handler: {e}")

        # ========================================================================
        # ROBUST GROUND-TRUTH METADATA FETCHING (WITH ERROR SHIELDING)
        # ========================================================================
        listing_data = {}
        try:
            if listing_id and hasattr(self, "db") and self.db:
                list_res = (
                    await self.db.table("provider_listings")
                    .select(
                        "id, vertical_type, property_use, equipment_type, capacity_output, max_weight_kgs, price, currency, location_text, operating_region, brand")
                    .eq("id", str(listing_id))
                    .maybe_single()
                    .execute()
                )
                if list_res and getattr(list_res, "data", None):
                    listing_data = list_res.data
                    asset_location = listing_data.get("location_text")
                    list_eq = (
                            listing_data.get("equipment_type")
                            or listing_data.get("property_use")
                            or listing_data.get("vehicle_type")
                    )
                    if list_eq:
                        model_name = list_eq
                        property_use = list_eq

                    if listing_data.get("brand"):
                        brand_name = listing_data.get("brand")

                    list_cap = listing_data.get("capacity_output") or listing_data.get("max_weight_kgs")
                    if list_cap:
                        raw_capacity_val = list_cap

                    if listing_data.get("property_use"):
                        property_use = listing_data.get("property_use")
        except Exception as db_meta_err:
            logger.warning(f"⚠️ Non-critical metadata fetch warning in deal_counter_handler: {db_meta_err}")
            listing_data = {}

        # Safely extract capacity or metrics using schema-aligned keys (avoiding non-existent columns)
        equipment_capacity = (
                listing_data.get("capacity_output")
                or listing_data.get("max_weight_kgs")
                or raw_capacity_val
                or "N/A"
        )
        if raw_capacity_val is None:
            raw_capacity_val = equipment_capacity

        # Synchronize resolved DB states cleanly into context for session memory without cross-contamination
        context.user_data["is_reverse_match"] = is_reverse
        context.user_data["currency"] = currency
        context.user_data["asset_title"] = model_name
        if asset_location:
            context.user_data["asset_location"] = asset_location
        if deployment_location:
            context.user_data["deployment_location"] = deployment_location

        # Price Fallback if pending or zero
        if asking_price == 0.0:
            raw_price = (
                    context.user_data.get("max_budget")
                    or context.user_data.get("seeker_budget")
                    or context.user_data.get("equipment_price")
                    or 0.0
            )
            asking_price = safe_float(raw_price) or 0.0

        formatted_price = f"{asking_price:,.2f} {currency}"

        # Safe HTML Escaping
        escaped_model = html.escape(str(model_name))
        escaped_brand = html.escape(str(brand_name)) if brand_name else None
        escaped_property_use = html.escape(str(property_use)) if property_use else None
        escaped_asset_loc = html.escape(str(asset_location)) if asset_location else None
        escaped_deploy_loc = html.escape(str(deployment_location)) if deployment_location else None

        # Capacity Formatting
        formatted_capacity = self._format_capacity_output(raw_capacity_val, str(model_name))
        escaped_capacity = html.escape(formatted_capacity) if formatted_capacity else None

        cap_line_am = f"⚙️ አቅም፦ <b>{escaped_capacity}</b>\n" if escaped_capacity else ""
        cap_line_om = f"⚙️ Dandeettii: <b>{escaped_capacity}</b>\n" if escaped_capacity else ""
        cap_line_en = f"⚙️ Capacity: <b>{escaped_capacity}</b>\n" if escaped_capacity else ""

        # Equipment Difference Check
        eq_differs = bool(req_eq and list_eq and str(req_eq).strip().lower() != str(list_eq).strip().lower())
        if eq_differs:
            req_esc = html.escape(str(req_eq))
            list_esc = html.escape(str(list_eq))
            eq_display_en = f"🚜 <b>Requested:</b> <i>{req_esc}</i>\n🔄 <b>Proposed Alternative:</b> <i>{list_esc}</i>"
            eq_display_am = f"🚜 <b>የተጠየቀው፦</b> <i>{req_esc}</i>\n🔄 <b>የቀረበው አማራጭ፦</b> <i>{list_esc}</i>"
            eq_display_om = f"🚜 <b>Kan gaafatame:</b> <i>{req_esc}</i>\n🔄 <b>Filannoo dhihaate:</b> <i>{list_esc}</i>"
        else:
            eq_display_en = f"🚜 <b>Equipment:</b> <i>{escaped_model}</i>"
            eq_display_am = f"🚜 <b>መሳሪያ፦</b> <i>{escaped_model}</i>"
            eq_display_om = f"🚜 <b>Meeshaa:</b> <i>{escaped_model}</i>"

        # Property Use & Brand Lines
        use_line_am = f"🛠️ አገልግሎት/ዓይነት፡ <b>{escaped_property_use}</b>\n" if escaped_property_use else ""
        use_line_om = f"🛠️ Fayyadamina/Gosa: <b>{escaped_property_use}</b>\n" if escaped_property_use else ""
        use_line_en = f"🛠️ Property Use: <b>{escaped_property_use}</b>\n" if escaped_property_use else ""

        brand_line_am = f"🏷️ ብራንድ፡ <b>{escaped_brand}</b>\n" if escaped_brand else ""
        brand_line_om = f"🏷️ Brandii: <b>{escaped_brand}</b>\n" if escaped_brand else ""
        brand_line_en = f"🏷️ Brand: <b>{escaped_brand}</b>\n" if escaped_brand else ""

        # Differentiated Location Line Generation
        loc_lines_am = []
        loc_lines_om = []
        loc_lines_en = []

        if escaped_asset_loc:
            loc_lines_am.append(f"📍 <b>የመሳሪያው ቦታ፦</b> <b>{escaped_asset_loc}</b>")
            loc_lines_om.append(f"📍 <b>Bakka Meeshaa:</b> <b>{escaped_asset_loc}</b>")
            loc_lines_en.append(f"📍 <b>Asset Location:</b> <b>{escaped_asset_loc}</b>")

        if escaped_deploy_loc:
            loc_lines_am.append(f"🏗️ <b>የስራ/ማሰማሪያ ቦታ፦</b> <b>{escaped_deploy_loc}</b>")
            loc_lines_om.append(f"🏗️ <b>Bakka Hojii/Bobbaa:</b> <b>{escaped_deploy_loc}</b>")
            loc_lines_en.append(f"🏗️ <b>Deployment Location:</b> <b>{escaped_deploy_loc}</b>")

        # Fallback if no location exists in DB
        if not loc_lines_en:
            loc_lines_am.append("📍 <b>ቦታ፦</b> <b>Addis Ababa</b>")
            loc_lines_om.append("📍 <b>Bakka:</b> <b>Addis Ababa</b>")
            loc_lines_en.append("📍 <b>Location:</b> <b>Addis Ababa</b>")

        loc_block_am = "\n".join(loc_lines_am) + "\n"
        loc_block_om = "\n".join(loc_lines_om) + "\n"
        loc_block_en = "\n".join(loc_lines_en) + "\n"

        # Multilingual Telegram Messages
        prompts = {
            "am": (
                f"⚡ <b>ስማርት ማች አስተማማኝ ድርድር</b> ⚡\n"
                f"───────────────────────\n"
                f"💬 <b>የመወዳደሪያ ዋጋዎን ያስገቡ</b>\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"{eq_display_am}\n"
                f"{use_line_am}"
                f"{brand_line_am}"
                f"{cap_line_am}"
                f"{loc_block_am}"
                f"💰 <b>የቀረበ ዋጋ፦</b> <code>{formatted_price}</code>\n\n"
                f"👇 <i>እባክዎ የመወዳደሪያ ዋጋዎን በ {currency} ያስገቡ (ለምሳሌ <code>5000</code>)፦</i>"
            ),
            "om": (
                f"⚡ <b>SMART MATCH SECURE DEAL</b> ⚡\n"
                f"───────────────────────\n"
                f"💬 <b>Gati dhiheessitan galchaa</b>\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"{eq_display_om}\n"
                f"{use_line_om}"
                f"{brand_line_om}"
                f"{cap_line_om}"
                f"{loc_block_om}"
                f"💰 <b>Gati dhihaate:</b> <code>{formatted_price}</code>\n\n"
                f"👇 <i>Maaloo gatii dhiheessitan {currency} n galchaa (fakkeenyaaf <code>5000</code>):</i>"
            ),
            "en": (
                f"⚡ <b>SMART MATCH SECURE DEAL</b> ⚡\n"
                f"───────────────────────\n"
                f"💬 <b>Enter Proposed Counter-Offer Rate</b>\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"{eq_display_en}\n"
                f"{use_line_en}"
                f"{brand_line_en}"
                f"{cap_line_en}"
                f"{loc_block_en}"
                f"💰 <b>Current Proposed Rate:</b> <code>{formatted_price}</code>\n\n"
                f"👇 <i>Reply with your custom counter-offer in {currency} (e.g., <code>5000</code>):</i>"
            ),
        }

        prompt_msg = prompts.get(user_lang, prompts["en"])
        chat_id = update.effective_chat.id if update.effective_chat else None

        try:
            if (
                    query
                    and query.message
                    and getattr(query.message, "photo", None)
            ):
                try:
                    await query.edit_message_caption(
                        caption=prompt_msg, parse_mode="HTML"
                    )
                except Exception:
                    if chat_id:
                        await context.bot.delete_message(
                            chat_id=chat_id, message_id=query.message.message_id
                        )
                        await context.bot.send_message(
                            chat_id=chat_id, text=prompt_msg, parse_mode="HTML"
                        )
            elif query:
                await query.edit_message_text(prompt_msg, parse_mode="HTML")
            elif chat_id:
                await context.bot.send_message(
                    chat_id=chat_id, text=prompt_msg, parse_mode="HTML"
                )
        except Exception:
            if chat_id:
                await context.bot.send_message(
                    chat_id=chat_id, text=prompt_msg, parse_mode="HTML"
                )

        return getattr(self, "ENTERING_OFFER", 1)

    async def deal_decline_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """
        Handles the 'equip_deal_decline:' callback.
        Terminates the current equipment deal negotiation and cleanly exits the conversation.
        """
        # 1. Delegate cancellation to the centralized deals handler
        await self.deals.handle_deal_decline(update, context)

        # 2. Clear active deal context to prevent stale session pollution
        context.user_data.pop("active_equipment_deal_id", None)
        context.user_data.pop("active_deal_id", None)
        context.user_data.pop("current_negotiation_deal_id", None)

        # 3. Cleanly terminate the conversation handler state (returns ConversationHandler.END)
        # Alternatively, you can call self.route_to_main_menu(update, context) if you prefer routing to main menu.
        return await self.route_to_main_menu(update, context)

    # ==========================================
    # 1. DEALS HANDLER INTEGRATION
    # ==========================================
    async def prompt_initial_offer(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """
        Triggered when a user clicks 'equipment_offer:uuid_here'.
        Transitions the user into an input state to type their starting price.
        """
        query = update.callback_query
        if query:
            await query.answer()

        lang = context.user_data.get('lang', 'en')
        callback_data = query.data if query else ""
        listing_id = callback_data.split(":", 1)[1] if ":" in callback_data else ""

        context.user_data["target_listing_id"] = listing_id
        context.user_data["vertical_type"] = "equipment_rental"

        prompt_text = get_text(lang, "equip_offer_prompt")

        keyboard = [
            [InlineKeyboardButton(get_text(lang, "btn_cancel_offer"), callback_data="cancel_vertical_offer")]
        ]

        if query:
            await query.edit_message_text(
                text=prompt_text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
            )
        return self.AWAITING_EQUIPMENT_OFFER

    async def process_initial_offer(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """
        Captures the numerical text, validates it, and spawns the Deal State Machine via DealsHandler.
        """
        if not update.message or not update.message.text:
            return self.AWAITING_EQUIPMENT_OFFER

        lang = context.user_data.get('lang', 'en')
        user_id = update.effective_user.id if update.effective_user else 0
        text_payload = update.message.text.strip()
        listing_id = context.user_data.get("target_listing_id")
        vertical = context.user_data.get("vertical_type", "equipment_rental")

        try:
            clean_numeric = text_payload.replace(",", "").replace("ETB", "").replace("birr", "").strip()
            initial_offer = float(clean_numeric)
            if initial_offer <= 0:
                raise ValueError("Price must be positive.")
        except ValueError:
            await update.message.reply_text(get_text(lang, "err_invalid_number"))
            return self.AWAITING_EQUIPMENT_OFFER

        # Retrieve Supabase listing
        listing = await self.db.get_listing_by_id(listing_id)
        if not listing:
            await update.message.reply_text(get_text(lang, "err_listing_unavailable"))
            context.user_data.clear()
            return ConversationHandler.END

        provider_id = safe_int(
            listing.get("provider_id") if isinstance(listing, dict) else getattr(listing, "provider_id", 0))

        if provider_id == user_id:
            await update.message.reply_text(get_text(lang, "err_offer_own_listing"))
            context.user_data.clear()
            return ConversationHandler.END

        # Handoff to DealsHandler for complete negotiation lifecycle management
        deal_id = await self.deals.initiate_negotiation(
            seeker_id=user_id,
            provider_id=provider_id,
            listing_id=listing_id,
            vertical_type=vertical,
            initial_offer=initial_offer
        )

        if deal_id:
            msg = get_text(lang, "msg_offer_sent").format(initial_offer=f"{initial_offer:,.2f}")
            await update.message.reply_text(msg, parse_mode="Markdown")
        else:
            await update.message.reply_text(get_text(lang, "err_offer_failed"))

        context.user_data.clear()
        return ConversationHandler.END

    # ==========================================
    # INTER-MODULE LINKS & ROUTING
    # ==========================================
    @staticmethod
    async def route_to_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """
        Safely drops the active conversation frame, flushes equipment-specific session data
        (while preserving user preferences like language), and re-instantiates the main menu hub.
        """
        is_start_command = bool(
            update.message
            and update.message.text
            and update.message.text.strip().startswith('/start')
        )

        # Preserve essential user preferences before deletion
        lang = context.user_data.get('lang', 'en')
        saved_phone = context.user_data.get('contact_phone')

        if is_start_command:
            context.user_data.clear()
            context.user_data['lang'] = lang
            if saved_phone:
                context.user_data['contact_phone'] = saved_phone
        else:
            temp_flow_keys = [
                'flow', 'active_equip_deal_id', 'equip_top_matches', 'deal_type', 'equipment_type',
                'requires_compliance', 'is_compliant', 'capacity_output', 'min_required_capacity',
                'cost', 'max_budget', 'condition', 'preferred_condition', 'hours_used',
                'max_hours_used', 'brand', 'preferred_brands', 'features', 'desired_features',
                'provider_id', 'asset_title', 'asset_photo_url', 'photo_url'
            ]
            for key in temp_flow_keys:
                context.user_data.pop(key, None)

        if update.callback_query:
            try:
                await update.callback_query.answer()
            except Exception:
                pass

        menu_handler = context.bot_data.get("main_menu_handler")
        if menu_handler:
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
    # 0. ROBUST FLOW HELPER (Permanently fix early-stage flow detection)
    # ==========================================
    @staticmethod
    def _get_flow(context: ContextTypes.DEFAULT_TYPE, update: Optional[Update] = None) -> str:
        """
        Robustly retrieves the current flow. Checks user_data first, infers from
        callback data or session keys, and defaults safely.
        """
        flow = context.user_data.get('flow')
        if flow in ['list', 'find']:
            return flow

        # Check explicit entry action stored in session
        entry_action = context.user_data.get('entry_action')
        if entry_action in ['list', 'find']:
            context.user_data['flow'] = entry_action
            return entry_action

        # Early-stage inference if user clicked a find callback or prompt
        if update and update.callback_query and update.callback_query.data:
            if "find" in update.callback_query.data:
                context.user_data['flow'] = 'find'
                context.user_data['entry_action'] = 'find'
                return 'find'

        # Intelligent fallback inference based on stored seeker-specific keys
        seeker_keys = ['min_required_capacity_output', 'max_acceptable_hourly_rate', 'preferred_brand',
                       'preferred_features', 'max_budget']
        if any(k in context.user_data for k in seeker_keys):
            context.user_data['flow'] = 'find'
            context.user_data['entry_action'] = 'find'
            return 'find'

        # Check incoming message text or query text for find keywords if update is available
        if update:
            msg_text = ""
            if update.callback_query and update.callback_query.message:
                # Safely check if 'text' attribute exists (guards against MaybeInaccessibleMessage)
                msg = update.callback_query.message
                if hasattr(msg, "text") and msg.text:
                    msg_text = msg.text
            elif update.message and update.message.text:
                msg_text = update.message.text

            lang = context.user_data.get('lang', 'en')
            find_menu_text = get_text(lang, "menu_find_equip")
            if find_menu_text and find_menu_text in msg_text:
                context.user_data['flow'] = 'find'
                context.user_data['entry_action'] = 'find'
                return 'find'

        # Absolute final safety fallback check: default to whatever flow is in context or 'list'
        fallback_flow = context.user_data.get('flow', 'list')
        context.user_data['flow'] = fallback_flow
        return fallback_flow

    # ==========================================
    # 2. APPLICATION ENTRY PATHWAYS
    # ==========================================
    async def start_list_equipment(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        if update.callback_query:
            try:
                await update.callback_query.answer()
            except Exception:
                pass
        lang = context.user_data.get('lang', 'en')

        context.user_data['flow'] = 'list'
        context.user_data['entry_action'] = 'list'
        context.user_data.pop('min_required_capacity_output', None)
        context.user_data.pop('max_acceptable_hourly_rate', None)

        return await self._show_category_menu(update, get_text(lang, "menu_list_equip"), lang, context, flow='list')

    async def start_find_equipment(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        if update.callback_query:
            try:
                await update.callback_query.answer()
            except Exception:
                pass
        lang = context.user_data.get('lang', 'en')

        context.user_data['flow'] = 'find'
        context.user_data['entry_action'] = 'find'
        context.user_data.pop('hourly_rate', None)
        context.user_data.pop('capacity_output', None)
        context.user_data.pop('brand', None)

        return await self._show_category_menu(update, get_text(lang, "menu_find_equip"), lang, context, flow='find')

    async def _show_category_menu(self, update: Update, prompt_text: str, lang: str,
                                  context: ContextTypes.DEFAULT_TYPE, flow: str = 'list') -> int:
        # 🔒 Encode flow directly into callback_data to make state tracking bulletproof
        keyboard = [
            [
                InlineKeyboardButton(get_text(lang, "btn_cat_construction"), callback_data=f"cat_{flow}_construction"),
                InlineKeyboardButton(get_text(lang, "btn_cat_farming"), callback_data=f"cat_{flow}_farming")
            ],
            [
                InlineKeyboardButton(get_text(lang, "btn_cat_generators"), callback_data=f"cat_{flow}_generators"),
                InlineKeyboardButton(get_text(lang, "btn_cat_eventtech"), callback_data=f"cat_{flow}_eventtech")
            ]
        ]
        markup = InlineKeyboardMarkup(keyboard)
        if update.message:
            await update.message.reply_text(prompt_text, reply_markup=markup, parse_mode="Markdown")
        elif update.callback_query:
            try:
                await update.callback_query.edit_message_text(prompt_text, reply_markup=markup, parse_mode="Markdown")
            except Exception:
                if update.effective_chat:
                    await context.bot.send_message(chat_id=update.effective_chat.id, text=prompt_text,
                                                   reply_markup=markup, parse_mode="Markdown")

        return self.EQUIPMENT_CATEGORY

    # ==========================================
    # 3. DATA INTAKE PIPELINE
    # ==========================================
    async def handle_category(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        query = update.callback_query
        if query:
            try:
                await query.answer()
            except Exception:
                pass
        lang = context.user_data.get('lang', 'en')

        try:
            # Parse callback data format: cat_<flow>_<suffix> (e.g. cat_find_construction) or fallback to legacy format
            parts = query.data.split('_') if query and query.data else ["cat", "list", "general"]

            if len(parts) >= 3 and parts[1] in ['list', 'find']:
                flow = parts[1]
                category_suffix = parts[2]
            else:
                category_suffix = parts[1] if len(parts) > 1 else "general"
                flow = self._get_flow(context, update)

            trans_key = f"btn_cat_{category_suffix}"
            localized_category = get_text(lang, trans_key)

            canonical_db_mapping = {
                "construction": "construction & earthmoving",
                "farming": "agriculture & irrigation",
                "generators": "generator & power",
                "eventtech": "event tech & communication"
            }
            canonical_category = canonical_db_mapping.get(category_suffix, category_suffix)

            context.user_data['category_raw'] = category_suffix
            context.user_data['category'] = canonical_category
            context.user_data['flow'] = flow  # Permanently lock flow in user_data

            template_key = "prompt_sourcing_req" if flow == 'find' else "prompt_equip_profile"
            prompt_template = get_text(lang, template_key)

            try:
                prompt = prompt_template.format(category=localized_category)
            except (KeyError, ValueError, IndexError) as format_err:
                logger.error(f"String formatting failed for translation key '{template_key}': {format_err}")
                prompt = f"📂 *Category:* {localized_category}\n\nPlease type the specific type or model of the equipment:"

            if query:
                await query.edit_message_text(prompt, parse_mode="Markdown")
            return self.EQUIPMENT_TYPE

        except Exception as global_err:
            logger.critical(f"Unhandled pipeline crash in handle_category: {str(global_err)}", exc_info=True)
            fallback_prompt = "📂 *Category selected successfully.*\n\nPlease enter the specific model or type of the equipment to proceed:"
            if query:
                await query.edit_message_text(fallback_prompt, parse_mode="Markdown")
            return self.EQUIPMENT_TYPE

    async def handle_equipment_type(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        if not update.message or not update.message.text:
            return self.EQUIPMENT_TYPE

        lang = context.user_data.get('lang', 'en')
        equip_type = update.message.text.strip()
        if len(equip_type) < 2:
            await update.message.reply_text(get_text(lang, "err_name_too_short"))
            return self.EQUIPMENT_TYPE

        context.user_data['equipment_type'] = equip_type
        flow = self._get_flow(context)

        prompt = get_text(lang, "prompt_capacity_list" if flow == 'list' else "prompt_capacity_find").format(
            equip_type=equip_type)
        await update.message.reply_text(prompt, parse_mode="Markdown")
        return self.CAPACITY_OUTPUT

    # Multilingual mapping for inbound unit normalization
    CAPACITY_TO_ENG = {
        "ton": "Tons", "tons": "Tons", "ቶን": "Tons", "tooni": "Tons",
        "kva": "kVA", "kv": "kV",
        "l/s": "L/s", "lit/s": "L/s", "liter/s": "L/s", "ሊትር/ሰ": "L/s",
        "m3/h": "m3/hr", "m3/hr": "m3/hr", "m3/sec": "m3/s", "m3/s": "m3/s",
        "kw": "kW",
        "hp": "HP", "ፈረስ ጉልበት": "HP",
        "kg": "kg", "ኪ.ግ": "kg", "ኪሎ": "kg", "kiiloo": "kg",
        "kg/hr": "kg/hr", "kg/h": "kg/hr", "ኪ.ግ/ሰ": "kg/hr",
        "m3": "m³", "m^3": "m³", "ሜትር ኩብ": "m³", "metira kiyuubii": "m³"
    }

    def _format_capacity_output(self, capacity_val, equip_type: str = "") -> str:
        """
        Formats capacity_output with thousands separators and its corresponding unit.
        Extracts unit from string or context user data, or maps default unit based on equipment type.
        """
        if capacity_val is None:
            return ""

        cap_str = str(capacity_val).strip()
        if not cap_str or cap_str.lower() in ["any", "none", "n/a", "ማንኛውም", "kamiyyuu"]:
            return ""

        match = re.search(r'^\s*([0-9]+(?:\s*,\s*[0-9]+)*(?:\.\d+)?)\s*(.*)$', cap_str)
        if match:
            num_str, raw_unit = match.groups()
            num_clean = num_str.replace(',', '')
            try:
                num_val = float(num_clean)
                if num_val.is_integer():
                    formatted_num = f"{int(num_val):,}"
                else:
                    formatted_num = f"{num_val:,.2f}".rstrip('0').rstrip('.')
            except ValueError:
                formatted_num = num_str

            raw_unit = raw_unit.strip()
            unit_lower = raw_unit.lower()

            standard_unit = self.CAPACITY_TO_ENG.get(unit_lower, raw_unit)

            if not standard_unit and equip_type:
                eq_lower = equip_type.lower()
                if any(k in eq_lower for k in ["generator", "gen", "ጂኔሬተር"]):
                    standard_unit = "kVA"
                elif any(k in eq_lower for k in ["tractor", "excavator", "loader", "dozer", "ትራክተር", "ኤክስካቫተር"]):
                    standard_unit = "HP"
                elif any(k in eq_lower for k in ["pump", "ፓምፕ"]):
                    standard_unit = "L/s"
                elif any(k in eq_lower for k in ["thresher", "ቲረሸር", "መውቂያ"]):
                    standard_unit = "kg/hr"
                elif any(k in eq_lower for k in ["truck", "tipper", "dump", "ቶን"]):
                    standard_unit = "Tons"
                elif any(k in eq_lower for k in ["mixer", "ኮንክሪት"]):
                    standard_unit = "m³"

            if standard_unit:
                return f"{formatted_num} {standard_unit}".strip()
            return formatted_num

        return cap_str

    def _normalize_capacity_input(self, text: str) -> str:
        """Parses multilingual input and standardizes the unit to English."""
        text_lower = text.lower().strip()
        if text_lower in ['any', 'ማንኛውም', 'kamiyyuu']:
            return "Any"

        # Updated regex to include commas ([\d,]+) for thousands separators
        num_match = re.search(r'([\d,]+(?:\.\d+)?)', text_lower)
        if not num_match:
            return text

        number_str = num_match.group(1)
        raw_unit = text_lower.replace(number_str, '', 1).strip()
        standard_unit = self.CAPACITY_TO_ENG.get(raw_unit, raw_unit)

        if standard_unit:
            return f"{number_str} {standard_unit}".strip()
        return number_str

    async def handle_capacity_output(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        if not update.message or not update.message.text:
            return self.CAPACITY_OUTPUT

        lang = context.user_data.get('lang', 'en')
        text = update.message.text.strip()
        flow = self._get_flow(context)

        standardized_capacity = self._normalize_capacity_input(text)

        if flow == 'list':
            context.user_data['capacity_output'] = standardized_capacity
            prompt = get_text(lang, "prompt_brand_list")
        else:
            context.user_data['min_required_capacity_output'] = standardized_capacity
            prompt = get_text(lang, "prompt_brand_find")

        await update.message.reply_text(prompt, parse_mode="Markdown")
        return self.BRAND_FEATURES

    async def handle_brand_features(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        if not update.message or not update.message.text:
            return self.BRAND_FEATURES

        lang = context.user_data.get('lang', 'en')
        text = update.message.text.strip()
        flow = self._get_flow(context)

        if flow == 'list':
            if '-' in text:
                brand, features = text.split('-', 1)
                context.user_data['brand'] = brand.strip()
                context.user_data['features'] = features.strip()
            else:
                context.user_data['brand'] = text
                context.user_data['features'] = "Standard"
            prompt = get_text(lang, "prompt_location_list")
        else:
            context.user_data['preferred_brand'] = text if text.lower() != 'none' else "Any"
            context.user_data['preferred_features'] = text if text.lower() != 'none' else "Any"
            prompt = get_text(lang, "prompt_location_find")

        await update.message.reply_text(prompt, parse_mode="Markdown")
        return self.LOCATION_DATA

    async def handle_location_data(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        if not update.message:
            return self.LOCATION_DATA

        lang = context.user_data.get('lang', 'en')
        if update.message.location:
            context.user_data['latitude'] = update.message.location.latitude
            context.user_data['longitude'] = update.message.location.longitude
            context.user_data['location_text'] = get_text(lang, "gps_coords_provided")
        elif update.message.text:
            context.user_data['latitude'] = None
            context.user_data['longitude'] = None
            context.user_data['location_text'] = update.message.text.strip()

        flow = self._get_flow(context)
        prompt = get_text(lang, "prompt_rate_list" if flow == 'list' else "prompt_rate_find")
        await update.message.reply_text(prompt, parse_mode="Markdown")
        return self.RATES_BUDGET

    async def handle_rates_budget(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        if not update.message or not update.message.text:
            return self.RATES_BUDGET

        lang = context.user_data.get('lang', 'en')
        text = update.message.text.strip()
        upper_text = text.upper()

        try:
            # 1. Detect currency preference from input text or fall back to context/ETB
            if "$" in text or "USD" in upper_text:
                currency = "USD"
            elif "ETB" in upper_text or "ብር" in text or "QARSHII" in upper_text or "BIRR" in upper_text:
                currency = "ETB"
            else:
                currency = context.user_data.get('currency', 'ETB')

            # 2. Smart flexible cleaning for various numeric formats (e.g. "5k", "10,000.00 ETB", "around 7500")
            cleaned_text = text.lower()

            # Handle shorthand multiplier 'k' (e.g., 5k -> 5000)
            multiplier = 1.0
            if 'k' in cleaned_text and not any(c.isalpha() for c in
                                               cleaned_text.replace('k', '').replace('etb', '').replace('usd',
                                                                                                        '').replace(
                                                       'birr', '').replace('ብር', '')):
                multiplier = 1000.0
                cleaned_text = cleaned_text.replace('k', '')

            # Strip currency words, symbols, and commas
            for token in ['$', 'usd', 'etb', 'birr', 'ብር', 'qarshii', ',', ' ']:
                cleaned_text = cleaned_text.replace(token, '')

            # Extract the first valid float number from the cleaned string using regex
            num_match = re.search(r'(\d+(?:\.\d+)?)', cleaned_text)
            if not num_match:
                # Polite multilingual guidance for invalid input
                guidance_msgs = {
                    "am": "⚠️ እባክዎ ትክክለኛ የቁጥር ዋጋ ያስገቡ (ለምሳሌ፦ <code>5000</code> ወይም <code>10000 ETB</code>)።",
                    "om": "⚠️ Maaloo gatii sirrii galchaa (fakkeenyaaf: <code>5000</code> ykn <code>10000 ETB</code>).",
                    "en": "⚠️ Please enter a valid numerical rate (e.g., <code>5000</code> or <code>10000 ETB</code>)."
                }
                try:
                    await update.message.reply_text(guidance_msgs.get(lang, guidance_msgs["en"]), parse_mode="Markdown")
                except Exception:
                    pass
                return self.RATES_BUDGET

            val = float(num_match.group(1)) * multiplier
            if val <= 0:
                raise ValueError("Rate must be greater than zero.")

            flow = self._get_flow(context)

            keyboard = [[
                get_text(lang, "btn_cond_exc"), get_text(lang, "btn_cond_good"), get_text(lang, "btn_cond_fair")
            ]]
            markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)

            # 3. Store both rate/budget and currency in user_data for Supabase persistence
            context.user_data['currency'] = currency
            if flow == 'list':
                context.user_data['hourly_rate'] = val
                context.user_data['price'] = val
                prompt = get_text(lang, "prompt_condition_list")
            else:
                context.user_data['max_acceptable_hourly_rate'] = val
                context.user_data['max_budget'] = val
                prompt = get_text(lang, "prompt_condition_find")

            try:
                await update.message.reply_text(prompt, reply_markup=markup, parse_mode="Markdown")
            except telegram.error.NetworkError as net_err:
                logger.warning(f"Network error sending condition prompt: {net_err}. Retrying plain text.")
                await update.message.reply_text(prompt, parse_mode="Markdown")

            return self.CONDITION_PREFS

        except (ValueError, TypeError):
            # Polite multilingual guidance for unparseable input
            guidance_msgs = {
                "am": "⚠️ ያስገቡት ዋጋ ግልጽ አይደለም። እባክዎ ቁጥር ብቻ (ለምሳሌ <code>5000</code>) በድጋሚ ይጻፉ።",
                "om": "⚠️ Gatiin galchitan hin baratamne. Maaloo lakkoof ብቻ (fakkeenyaaf <code>5000</code>) deebisaa barreessaa.",
                "en": "⚠️ We couldn't recognize that rate format. Please reply with a clean number (e.g., <code>5000</code>)."
            }
            try:
                await update.message.reply_text(guidance_msgs.get(lang, guidance_msgs["en"]), parse_mode="Markdown")
            except Exception:
                pass
            return self.RATES_BUDGET

        except Exception as e:
            logger.error(f"CRITICAL ERROR in handle_rates_budget: {str(e)}", exc_info=True)
            fallback_msg = {
                "am": "❌ የዋጋ መረጃውን በማስቀመጥ ላይ ችግር ተፈጥሯል። እባክዎ እንደገና ይሞክሩ።",
                "om": "❌ Ragaa gatii kuusuu irratti rakkoon uumameera. Maaloo irra deebisaa yaalaa.",
                "en": "❌ An unexpected error occurred processing your rate. Please try entering it again."
            }
            try:
                await update.message.reply_text(fallback_msg.get(lang, fallback_msg["en"]), parse_mode="Markdown")
            except Exception:
                pass
            return self.RATES_BUDGET

    async def handle_condition_prefs(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        lang = context.user_data.get('lang', 'en')
        try:
            condition_text = update.message.text.strip() if update.message and update.message.text else ""
            context.user_data['condition'] = condition_text
            flow = self._get_flow(context)

            if flow == 'list':
                prompt = get_text(lang, "prompt_photo_upload")
                if update.message:
                    await update.message.reply_text(prompt, reply_markup=ReplyKeyboardRemove(), parse_mode="Markdown")
                return self.PHOTO_UPLOAD
            else:
                prompt = get_text(lang, "prompt_contact_info")
                if update.message:
                    await update.message.reply_text(prompt, reply_markup=ReplyKeyboardRemove(), parse_mode="Markdown")
                return self.CONTACT_INFO

        except Exception as e:
            logger.error(f"Silent stall intercepted in handle_condition_prefs: {str(e)}", exc_info=True)
            flow_fallback = self._get_flow(context)
            if flow_fallback == 'list':
                fallback_prompt = "📸 *Condition saved.*\n\nPlease upload a clear photo of the equipment, or type 'Skip':"
                next_state = self.PHOTO_UPLOAD
            else:
                fallback_prompt = "📞 *Condition saved.*\n\nPlease provide your contact info so counterparties can reach you:"
                next_state = self.CONTACT_INFO

            if update.message:
                await update.message.reply_text(fallback_prompt, reply_markup=ReplyKeyboardRemove(),
                                                parse_mode="Markdown")
            return next_state

    async def handle_photo_upload(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        if not update.message:
            return self.PHOTO_UPLOAD

        lang = context.user_data.get('lang', 'en')
        skip_keyword = get_text(lang, "keyword_skip").lower()
        photo_file = None

        if update.message.photo:
            photo_file = await update.message.photo[-1].get_file()
        elif update.message.document:
            doc = update.message.document
            mime_type = getattr(doc, 'mime_type', '') or ''
            file_name = getattr(doc, 'file_name', '') or ''

            if mime_type.startswith('image/') or file_name.lower().endswith(('.jpg', '.jpeg', '.png', '.webp')):
                photo_file = await doc.get_file()

        if photo_file:
            file_bytes = await photo_file.download_as_bytearray()
            filename = f"equip_assets/{uuid.uuid4().hex[:12]}.jpg"
            progress_msg = await update.message.reply_text(get_text(lang, "msg_uploading_b2"))

            try:
                s3_url = await self.storage.upload_buffer(file_bytes, filename)
                context.user_data['asset_photo_url'] = s3_url
                context.user_data['photo_url'] = s3_url
                await progress_msg.edit_text(get_text(lang, "msg_photo_success"))
            except Exception as e:
                logger.error(f"Backblaze B2 Upload Failed: {str(e)}")
                context.user_data['asset_photo_url'] = None
                context.user_data['photo_url'] = None
                await progress_msg.edit_text(get_text(lang, "msg_photo_timeout"))
        else:
            if update.message.text and update.message.text.lower() not in ['skip', skip_keyword]:
                await update.message.reply_text(get_text(lang, "err_photo_invalid"))
                return self.PHOTO_UPLOAD

            context.user_data['asset_photo_url'] = None
            context.user_data['photo_url'] = None

        await update.message.reply_text(get_text(lang, "prompt_contact_info"), parse_mode="Markdown")
        return self.CONTACT_INFO

    @staticmethod
    def localize_capacity_output(capacity_str: str, lang: str) -> str:
        """Translates the standardized English capacity back to the user's language (EN, AM, OM)."""
        if not capacity_str or str(capacity_str).lower() in ["any", "none", "n/a"]:
            return get_text(lang, "any_keyword") or "Any"

        num_match = _NUMERIC_PATTERN.search(str(capacity_str))
        if not num_match:
            return str(capacity_str)

        number = num_match.group(1)
        eng_unit = str(capacity_str).lower().replace(number, "").strip()

        if lang == "am":
            am_map = {
                "tons": "ቶን", "ton": "ቶን",
                "kva": "kVA", "l/s": "L/s",
                "kw": "kW", "hp": "የፈረስ ጉልበት",
                "kg": "ኪ.ግ", "m³": "ሜትር ኩብ"
            }
            loc_unit = am_map.get(eng_unit, eng_unit)
            return f"{number} {loc_unit}".strip() if loc_unit else number

        elif lang == "om":
            om_map = {
                "tons": "Tooni", "ton": "Tooni",
                "kva": "kVA", "l/s": "L/s",
                "kw": "kW", "hp": "HP",
                "kg": "kg", "m³": "metira kiyuubii"
            }
            loc_unit = om_map.get(eng_unit, eng_unit)
            if loc_unit.lower() == "tooni":
                return f"{loc_unit} {number}"
            return f"{number} {loc_unit}".strip() if loc_unit else number

        return str(capacity_str)

    @staticmethod
    def _clean_equipment_type(equip_str: str) -> str:
        """Strips transaction terms rapidly using pre-compiled regex."""
        if not equip_str or not isinstance(equip_str, str):
            return "General Equipment"

        cleaned = _TRANSACTION_PATTERN.sub("", equip_str).strip()
        cleaned = _CLEAN_SPACES_PATTERN.sub(" ", cleaned).strip()
        return cleaned if cleaned else equip_str.strip()

    @staticmethod
    def _resolve_category_and_property_use(equip_type: str, raw_category: Optional[str] = None) -> Tuple[str, str]:
        """Maps equipment category with property_use and category as a high-level usage classification."""
        if (
                raw_category
                and str(raw_category).strip()
                and str(raw_category).strip().lower() not in ["general", "unknown", "none", "null", "any"]
        ):
            cat_clean = str(raw_category).strip().title()
            return cat_clean, cat_clean

        eq_lower = equip_type.lower()
        agri_keywords = (
            "thresher", "harvester", "tractor", "planter", "seeder", "plow", "plough", "combine", "haamaa", "traaktera",
            "traktara", "mower", "baler", "sprayer"
        )
        const_keywords = (
            "excavator", "loader", "dozer", "bulldozer", "backhoe", "crane", "compactor", "roller", "grader", "dumper",
            "komaatsuu", "komatsu", "caterpillar", "cat"
        )
        event_keywords = (
            "lighting", "light", "audio", "sound", "stage", "speaker", "mic", "microphone", "ifa", "sagalee",
            "parkiins", "perkins", "generator", "gen"
        )

        industrial_keywords = ("pump", "rig", "compressor", "welder", "forklift")

        if any(k in eq_lower for k in agri_keywords):
            cat = "Agricultural Equipment"
        elif any(k in eq_lower for k in const_keywords):
            cat = "Construction Equipment"
        elif any(k in eq_lower for k in event_keywords):
            cat = "Audio & Event Equipment"
        elif any(k in eq_lower for k in industrial_keywords):
            cat = "Industrial Equipment"
        else:
            cat = "General Equipment"

        return cat, cat

    async def handle_contact_info(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        if not update.message or not update.message.text:
            return self.CONTACT_INFO

        lang = context.user_data.get("lang", context.user_data.get("language", "en"))
        phone = update.message.text.strip()

        # Fast phone validation
        clean_phone_digits = phone.replace("+", "").replace(" ", "")
        if not clean_phone_digits.isdigit() or len(clean_phone_digits) < 9:
            err_phone = (
                    get_text(lang, "err_invalid_phone")
                    or "⚠️ Invalid phone format. Please enter a valid number:"
            )
            await update.message.reply_text(err_phone)
            return self.CONTACT_INFO

        context.user_data["contact_phone"] = phone
        user_id = update.effective_user.id if update.effective_user else 0
        flow = context.user_data.get("flow", "find")

        processing_text = (
                get_text(lang, "msg_processing_asset")
                or "⏳ Compiling equipment profile and syncing nodes..."
        )
        processing_msg = await update.message.reply_text(processing_text)

        try:
            # High-speed bulk parsing of numeric values in context.user_data
            num_keys = (
                "price", "max_budget", "capacity_output", "min_required_capacity",
                "min_required_capacity_output", "hours_used", "max_hours_used",
                "quantity", "bulk_quantity", "latitude", "longitude"
            )
            for nk in num_keys:
                val = context.user_data.get(nk)
                if val is not None:
                    if nk in ("quantity", "bulk_quantity"):
                        context.user_data[nk] = safe_int(val, default=1)
                    else:
                        context.user_data[nk] = safe_float(val)

            # Standardized Field Resolution
            raw_equip_input = (
                    context.user_data.get("equipment_type")
                    or context.user_data.get("asset_type")
                    or context.user_data.get("equipment")
                    or context.user_data.get("title")
                    or context.user_data.get("category")
                    or "General Equipment"
            )

            is_lexicon_match = False
            if isinstance(raw_equip_input, str):
                cleaned_lower = raw_equip_input.strip().lower()
                if cleaned_lower in REGIONAL_EQUIPMENT_LEXICON:
                    raw_equip_input = REGIONAL_EQUIPMENT_LEXICON[cleaned_lower]
                    is_lexicon_match = True

            equipment_type_val = self._clean_equipment_type(raw_equip_input)
            transaction_type_val = str(
                context.user_data.get("transaction_type")
                or context.user_data.get("deal_type")
                or context.user_data.get("listing_type")
                or "rental"
            ).strip().lower()

            raw_category_input = context.user_data.get("category") or context.user_data.get("property_use")
            category_val, property_use_val = self._resolve_category_and_property_use(
                equipment_type_val, raw_category_input
            )

            context.user_data["equipment_type"] = equipment_type_val
            context.user_data["transaction_type"] = transaction_type_val
            context.user_data["category"] = category_val
            context.user_data["property_use"] = property_use_val

            # ==========================================
            # PROVIDER REGISTRATION (flow == 'list')
            # ==========================================
            if flow == "list":
                quantity = safe_int(
                    context.user_data.get("quantity") or context.user_data.get("bulk_quantity"), default=1
                )
                asset_brand = (
                        context.user_data.get("brand")
                        or context.user_data.get("asset_brand")
                        or context.user_data.get("preferred_brand")
                        or "Unknown"
                )
                asset_features = (
                        context.user_data.get("features")
                        or context.user_data.get("amenities")
                        or "Standard"
                )

                capacity_val = safe_float(
                    context.user_data.get("capacity_output") or context.user_data.get("capacity")
                )
                hours_val = safe_float(
                    context.user_data.get("hours_used") or context.user_data.get("max_hours_used")
                )

                combined_amenities = f"Brand: {asset_brand} | Features: {asset_features}"
                if capacity_val > 0.0:
                    combined_amenities += f" | Capacity: {capacity_val}"

                final_photo_url = context.user_data.get("photo_url") or context.user_data.get("asset_photo_url")
                raw_price = (
                        context.user_data.get("price")
                        or context.user_data.get("hourly_rate")
                        or context.user_data.get("rate")
                )
                parsed_price = safe_float(raw_price)
                context.user_data["price"] = parsed_price

                # Location Resolution (Explicit Listing Location Key to prevent overwriting)
                location_val = apply_city_fixes(
                    context.user_data.get("listing_location_text")
                    or context.user_data.get("provider_location")
                    or context.user_data.get("location_text")
                    or context.user_data.get("location")
                )
                context.user_data["listing_location_text"] = location_val

                listing_data = {
                    "provider_id": user_id,
                    "user_id": user_id,
                    "vertical_type": "equipment",
                    "transaction_type": transaction_type_val,
                    "category": category_val,
                    "property_use": property_use_val,
                    "equipment_type": equipment_type_val,
                    "brand": asset_brand,
                    "capacity_output": capacity_val,
                    "hours_used": hours_val,
                    "location_text": location_val,
                    "latitude": safe_float(context.user_data.get("latitude")),
                    "longitude": safe_float(context.user_data.get("longitude")),
                    "price": parsed_price,
                    "currency": context.user_data.get("currency", "ETB"),
                    "amenities": combined_amenities,
                    "features": asset_features,
                    "photo_url": final_photo_url,
                    "contact_phone": phone,
                    "status": "active",
                    "bulk_quantity": quantity,
                    "quantity": quantity,
                }

                # AI Listing Parsing Safeguard (Optimized 7.0-second timeout to accommodate AFC initialization)
                if hasattr(self, "ai") and hasattr(self.ai, "parse_listing_to_english"):
                    try:
                        english_parsed_data = await asyncio.wait_for(
                            self.ai.parse_listing_to_english(listing_data), timeout=7.0
                        )
                        if isinstance(english_parsed_data, str):
                            match_json = _JSON_EXTRACT_PATTERN.search(english_parsed_data)
                            if match_json:
                                try:
                                    english_parsed_data = json.loads(match_json.group(0))
                                except json.JSONDecodeError:
                                    pass

                        if isinstance(english_parsed_data, dict) and english_parsed_data:
                            if title := english_parsed_data.get("title"):
                                clean_ai_title = self._clean_equipment_type(str(title))
                                if not is_lexicon_match and clean_ai_title:
                                    listing_data["equipment_type"] = clean_ai_title
                                    c_val, p_val = self._resolve_category_and_property_use(
                                        clean_ai_title, listing_data.get("category")
                                    )
                                    listing_data["category"] = c_val
                                    listing_data["property_use"] = p_val

                            if location := english_parsed_data.get("location"):
                                listing_data["location_text"] = location

                            if brand := english_parsed_data.get("brand"):
                                listing_data["brand"] = str(brand)

                            def assign_capacity(input_val):
                                if input_val:
                                    parsed = safe_float(input_val)
                                    if parsed > 0.0:
                                        listing_data["capacity_output"] = parsed

                            assign_capacity(
                                english_parsed_data.get("capacity_output")
                                or english_parsed_data.get("capacity")
                            )

                            specs = english_parsed_data.get("specifications", {})
                            if isinstance(specs, dict) and specs:
                                if extracted_brand := (
                                        specs.get("brand") or specs.get("manufacturer") or specs.get("make")
                                ):
                                    listing_data["brand"] = str(extracted_brand)

                                assign_capacity(
                                    specs.get("capacity")
                                    or specs.get("capacity_output")
                                    or specs.get("output")
                                    or specs.get("power")
                                )

                                if extracted_hours := (
                                        specs.get("hours_used") or specs.get("hours") or specs.get("mileage")
                                ):
                                    parsed_hours = safe_float(extracted_hours)
                                    if parsed_hours >= 0.0:
                                        listing_data["hours_used"] = parsed_hours

                            desc = str(english_parsed_data.get("description", "")).strip()
                            amenities_parts = []
                            if desc and desc.lower() not in ["none", "null", "n/a"]:
                                amenities_parts.append(desc)
                            if isinstance(specs, dict) and specs:
                                amenities_parts.append(f"Extracted Specs: {specs}")
                            if amenities_parts:
                                listing_data["amenities"] = " | ".join(amenities_parts)

                    except asyncio.TimeoutError:
                        logger.info("AI parsing skipped: 7.0s threshold exceeded. Falling back to heuristics/lexicon.")
                    except Exception as ai_err:
                        logger.warning(f"AI parsing mapping skipped: {ai_err}")

                listing_data["location_text"] = apply_city_fixes(listing_data.get("location_text"))
                listing_data["brand"] = apply_brand_fixes(listing_data.get("brand"))

                # Translation & Lexicon Safeguards (Rate-limit safe serial processing to prevent 429 floods)
                try:
                    fields_to_check = ["equipment_type", "location_text", "amenities", "brand"]

                    def safe_translate_sync(text: str) -> str:
                        if not text:
                            return text

                        cleaned_lower_lexicon = text.strip().lower()
                        if cleaned_lower_lexicon in REGIONAL_EQUIPMENT_LEXICON:
                            return REGIONAL_EQUIPMENT_LEXICON[cleaned_lower_lexicon]

                        words = set(_WORDS_PATTERN.findall(cleaned_lower_lexicon))
                        has_non_ascii = bool(_NON_ASCII_PATTERN.search(text))
                        has_oromo_terms = bool(words - KNOWN_ENGLISH_WORDS) and not text.isnumeric()

                        if not has_non_ascii and not has_oromo_terms:
                            return text

                        try:
                            lang_translator = GoogleTranslator(source="auto", target="en")
                            translation_result = lang_translator.translate(text)

                            if not translation_result or any(
                                    err in translation_result for err in
                                    ["Error 500", "Error 400", "Exception", "too many requests"]
                            ):
                                return text

                            if "(" in translation_result:
                                translation_result = translation_result.split("(")[0].strip()

                            cleaned_translation = translation_result.strip()
                            if cleaned_translation.lower() == "gods":
                                return "Guder"

                            return cleaned_translation

                        except Exception:
                            # Silent fallback without warning spam to keep telemetry clean
                            lower_text = text.lower()
                            if "ifa" in lower_text or "sagalee" in lower_text:
                                return "Pro Stage Lighting and Audio"
                            return text

                    def translate_mixed_string(text: str) -> str:
                        if not text:
                            return text
                        if "|" in text:
                            parts = [p.strip() for p in text.split("|")]
                            translated_parts = [safe_translate_sync(p) for p in parts]
                            return " | ".join(translated_parts)
                        return safe_translate_sync(text)

                    # Process fields sequentially with brief pacing to prevent Google Translate rate-limit blocks
                    for field in fields_to_check:
                        val = listing_data.get(field)
                        if val and isinstance(val, str):
                            if field == "location_text":
                                clean_loc = val.strip().lower()
                                if clean_loc in ETHIOPIAN_LOCATIONS:
                                    listing_data["location_text"] = ETHIOPIAN_LOCATIONS[clean_loc]
                                    continue
                                if not _NON_ASCII_PATTERN.search(val):
                                    continue

                            # Translate synchronously in thread with a tiny pause to respect rate limits
                            res = await asyncio.to_thread(translate_mixed_string, val)
                            if not res:
                                continue

                            clean_val = str(res)
                            for bad_pattern_re, good_brand in PHONETIC_FIXES_COMPILED:
                                clean_val = bad_pattern_re.sub(good_brand, clean_val)

                            if field == "equipment_type":
                                clean_val = self._clean_equipment_type(clean_val)
                                if is_lexicon_match:
                                    continue

                            listing_data[field] = clean_val

                            if field == "location_text":
                                listing_data["location_text"] = apply_city_fixes(clean_val)
                            elif field == "brand":
                                listing_data["brand"] = apply_brand_fixes(clean_val)

                            # Small pacing pause between translation fields to keep Google Translate happy
                            await asyncio.sleep(0.15)

                except Exception as safeguard_err:
                    logger.warning(f"Translation safeguard skipped: {safeguard_err}")

                combined_search_text = (
                    f"{listing_data.get('equipment_type', '')} "
                    f"{listing_data.get('category', '')} "
                    f"{listing_data.get('amenities', '')} "
                    f"{listing_data.get('brand', '')}"
                )
                curr_brand = str(listing_data.get("brand", "")).strip().lower()
                invalid_brands = {"none", "unknown", "", "nan", "error 500", "error", "null"}
                is_recognized = any(curr_brand == kb.lower() for kb in KNOWN_BRANDS)

                if not is_recognized or curr_brand in invalid_brands:
                    found_brand = None
                    for b in KNOWN_BRANDS:
                        if re.search(r"\b" + re.escape(b) + r"\b", combined_search_text, re.IGNORECASE):
                            found_brand = b
                            break
                    if found_brand:
                        listing_data["brand"] = found_brand

                curr_cap = listing_data.get("capacity_output", 0.0)
                if not curr_cap or float(curr_cap) == 0.0:
                    cap_match = _CAPACITY_MATCH_PATTERN.search(combined_search_text)
                    if cap_match:
                        listing_data["capacity_output"] = safe_float(cap_match.group(1))

                listing_record = None
                if hasattr(self, "db"):
                    # Fast DB listing creation loop
                    listing_tasks = [self.db.create_listing(listing_data) for _ in range(quantity)]
                    created_records = await asyncio.gather(*listing_tasks, return_exceptions=True)

                    valid_records = [r for r in created_records if r and not isinstance(r, Exception)]
                    if valid_records:
                        listing_record = valid_records[0]
                    else:
                        raise Exception("Supabase create_listing failed to return a valid record.")

                    logger.info(f"{quantity} Equipment listing(s) successfully created for User ID: {user_id}")

                    if (
                            hasattr(self, "matching")
                            and hasattr(self.matching, "run_provider_matching")
                            and listing_record
                    ):
                        rec_id = (
                            listing_record.get("id")
                            if isinstance(listing_record, dict)
                            else getattr(listing_record, "id", None)
                        )
                        if rec_id:
                            asyncio.create_task(self.matching.run_provider_matching(rec_id, "equipment"))

                await processing_msg.delete()

                if listing_record:
                    rec_dict = (
                        listing_record if isinstance(listing_record, dict) else listing_record.__dict__
                    )
                    return await self.trigger_reverse_matching(update, context, rec_dict)

                success_msg = (
                        get_text(lang, "msg_listing_success")
                        or f"✅ {quantity} Equipment ({listing_data.get('equipment_type', category_val)}) Successfully Registered!"
                )
                await update.message.reply_text(success_msg, parse_mode="Markdown")
                return await self.route_to_main_menu(update, context)

            # ==========================================
            # SEEKER MATCHING (flow == 'find')
            # ==========================================
            else:
                scan_msg = get_text(lang, "msg_scanning_nodes") or "🔍 Scanning available equipment..."
                await processing_msg.edit_text(scan_msg)

                # 1. Pre-check regional lexicon before hitting the AI model to save latency & quota
                if equipment_type_val and isinstance(equipment_type_val, str):
                    cleaned_lower = equipment_type_val.strip().lower()
                    if cleaned_lower in REGIONAL_EQUIPMENT_LEXICON:
                        equipment_type_val = REGIONAL_EQUIPMENT_LEXICON[cleaned_lower]
                        category_val, property_use_val = self._resolve_category_and_property_use(
                            equipment_type_val, category_val
                        )

                # 2. AI Parsing with separated Timeout & Exception handling
                if hasattr(self, "ai") and hasattr(self.ai, "parse_listing_to_english"):
                    try:
                        seeker_draft = {
                            "vertical_type": "equipment",
                            "transaction_type": transaction_type_val,
                            "category": category_val,
                            "property_use": property_use_val,
                            "equipment_type": equipment_type_val,
                            "brand": context.user_data.get("preferred_brand") or context.user_data.get("brand"),
                            "location_text": context.user_data.get("request_location_text") or context.user_data.get(
                                "location_text"),
                        }
                        # Increased timeout to 7s for stability under network load
                        parsed_seeker = await asyncio.wait_for(
                            self.ai.parse_listing_to_english(seeker_draft), timeout=7
                        )
                        if isinstance(parsed_seeker, str):
                            match_json = _JSON_EXTRACT_PATTERN.search(parsed_seeker)
                            if match_json:
                                try:
                                    parsed_seeker = json.loads(match_json.group(0))
                                except json.JSONDecodeError:
                                    pass

                        if isinstance(parsed_seeker, dict) and parsed_seeker.get("title"):
                            equipment_type_val = self._clean_equipment_type(parsed_seeker["title"])
                            category_val, property_use_val = self._resolve_category_and_property_use(
                                equipment_type_val, category_val
                            )

                    except asyncio.TimeoutError:
                        logger.info(
                            "Seeker AI parsing skipped: Request exceeded 7s threshold. Falling back to local lexicon/heuristics.")
                    except Exception as ai_err:
                        logger.warning(
                            f"Seeker AI parsing skipped due to API error: {ai_err}. Falling back to standard parser.")

                if equipment_type_val and isinstance(equipment_type_val, str):
                    cleaned_lower = equipment_type_val.strip().lower()
                    if cleaned_lower in REGIONAL_EQUIPMENT_LEXICON:
                        equipment_type_val = REGIONAL_EQUIPMENT_LEXICON[cleaned_lower]
                    elif _NON_ASCII_PATTERN.search(equipment_type_val):
                        translated_query = None
                        try:
                            translator = GoogleTranslator(source="auto", target="en")
                            translated_query = await asyncio.to_thread(
                                translator.translate, equipment_type_val
                            )
                        except Exception as trans_err:
                            logger.error(f"Seeker query translation failed: {trans_err}")

                        if translated_query and not any(
                                err in translated_query for err in ["Error 500", "Error 400", "Exception"]
                        ):
                            equipment_type_val = self._clean_equipment_type(
                                translated_query.split("(")[0].strip()
                            )
                        else:
                            lower_val = equipment_type_val.lower()
                            if any(k in lower_val for k in ("ifa", "sagalee", "audio", "lighting")):
                                equipment_type_val = "Pro Stage Lighting and Audio"
                            else:
                                equipment_type_val = "General Equipment"

                raw_budget = (
                        context.user_data.get("max_budget")
                        or context.user_data.get("max_acceptable_hourly_rate")
                        or context.user_data.get("budget")
                        or context.user_data.get("max_price")
                        or context.user_data.get("price")
                        or context.user_data.get("hourly_rate")
                )
                parsed_max_budget = safe_float(raw_budget)
                context.user_data["max_budget"] = parsed_max_budget

                pref_brand = (
                        context.user_data.get("preferred_brand")
                        or context.user_data.get("brand")
                        or context.user_data.get("asset_brand")
                        or "Any"
                )

                pref_features = (
                        context.user_data.get("preferred_features")
                        or context.user_data.get("features")
                        or context.user_data.get("amenities")
                        or "Any"
                )

                # Location Resolution (Explicit Seeker Location Key to prevent overwriting)
                location_val = apply_city_fixes(
                    context.user_data.get("request_location_text")
                    or context.user_data.get("seeker_location")
                    or context.user_data.get("location_text")
                    or context.user_data.get("location")
                )
                context.user_data["request_location_text"] = location_val

                quantity = safe_int(
                    context.user_data.get("quantity") or context.user_data.get("bulk_quantity"), default=1
                )
                cap_output = safe_float(
                    context.user_data.get("min_required_capacity_output")
                    or context.user_data.get("capacity_output")
                    or context.user_data.get("min_required_capacity")
                )
                hrs_used = safe_float(
                    context.user_data.get("max_hours_used") or context.user_data.get("hours_used")
                )

                req_payload = {
                    "seeker_id": user_id,
                    "user_id": user_id,
                    "vertical_type": "equipment",
                    "transaction_type": transaction_type_val,
                    "category": category_val,
                    "property_use": property_use_val,
                    "equipment_type": equipment_type_val,
                    "brand": pref_brand,
                    "preferred_brand": pref_brand,
                    "max_budget": parsed_max_budget,
                    "price": parsed_max_budget,
                    "currency": context.user_data.get("currency", "ETB"),
                    "location_text": location_val,
                    "latitude": safe_float(context.user_data.get("latitude")),
                    "longitude": safe_float(context.user_data.get("longitude")),
                    "contact_phone": phone,
                    "amenities": pref_features,
                    "required_amenities": pref_features,
                    "preferred_features": pref_features,
                    "capacity_output": cap_output,
                    "min_required_capacity_output": cap_output,
                    "hours_used": hrs_used,
                    "max_hours_used": hrs_used,
                    "bulk_quantity": quantity,
                    "quantity": quantity,
                    "status": "active",
                }

                # Persist request in background task to speed up matching pipeline response
                if hasattr(self, "db") and hasattr(self.db, "create_request"):
                    asyncio.create_task(self.db.create_request(req_payload))

                candidates = []
                if hasattr(self, "db") and hasattr(self.db, "fetch_equipment_candidates"):
                    candidates = await self.db.fetch_equipment_candidates(
                        listing_type=transaction_type_val, equipment_type=equipment_type_val
                    )

                if not candidates and hasattr(self, "db") and hasattr(self.db, "fetch_equipment_candidates"):
                    candidates = await self.db.fetch_equipment_candidates(
                        listing_type=transaction_type_val, equipment_type=None
                    )

                prefs = dict(context.user_data)
                prefs["equipment_type"] = equipment_type_val
                prefs["deal_type"] = transaction_type_val
                prefs["transaction_type"] = transaction_type_val
                prefs["category"] = category_val
                prefs["property_use"] = property_use_val
                prefs["max_budget"] = parsed_max_budget
                prefs["preferred_brand"] = pref_brand
                prefs["preferred_features"] = pref_features

                for list_key in ("preferred_brands", "desired_features"):
                    raw_val = prefs.get(list_key, [])
                    if isinstance(raw_val, str):
                        prefs[list_key] = [x.strip() for x in raw_val.split(",") if x.strip()]

                seeker_id = user_id
                if seeker_id:
                    str_seeker_id = str(seeker_id)
                    candidates = [
                        c for c in candidates
                        if str(c.get("user_id") or c.get("provider_id")) != str_seeker_id
                    ]

                for c in candidates:
                    c["equipment_type"] = (
                            c.get("equipment_type") or c.get("property_use") or c.get("category", "General")
                    )
                    c["deal_type"] = c.get("deal_type") or c.get("transaction_type", "rental")
                    c["cost"] = safe_float(c.get("cost", c.get("price", 0.0)))
                    c["location"] = str(c.get("location_text") or c.get("location") or "").strip()

                    if not c.get("brand"):
                        prop_use = str(c.get("property_use", "")).lower()
                        if "caterpillar" in prop_use or "cat" in prop_use:
                            c["brand"] = "Caterpillar"
                        else:
                            c["brand"] = c.get("property_use", "").split()[0] if c.get("property_use") else "Generic"

                    c["capacity_output"] = str(c.get("capacity_output", "Any"))
                    c["hours_used"] = safe_float(c.get("hours_used", 0.0))
                    c["is_compliant"] = bool(c.get("is_compliant", False))

                    raw_features = c.get("features", [])
                    if isinstance(raw_features, str):
                        c["features"] = [x.strip() for x in raw_features.split(",") if x.strip()]

                matches = []
                if hasattr(self, "matching") and hasattr(self.matching, "evaluate_matches"):
                    matches = await self.matching.evaluate_matches("equipment", prefs, candidates)

                if not matches and candidates and hasattr(self, "matching") and hasattr(self.matching,
                                                                                        "evaluate_matches"):
                    prefs["preferred_brand"] = "Any"
                    prefs["equipment_type"] = equipment_type_val
                    prefs["preferred_brands"] = []
                    prefs["desired_features"] = []
                    prefs["allow_semantic_fallback"] = True

                    if hasattr(self, "db") and hasattr(self.db, "fetch_equipment_candidates"):
                        broad_candidates = await self.db.fetch_equipment_candidates(
                            listing_type=transaction_type_val, equipment_type=None
                        )
                        if broad_candidates:
                            candidates = broad_candidates

                    matches = await self.matching.evaluate_matches("equipment", prefs, candidates)

                if not matches:
                    broadcast_txt = (
                            get_text(lang, "msg_request_broadcasted")
                            or "📢 No suitable equipment match found. Request broadcasted."
                    )
                    await update.message.reply_text(broadcast_txt)
                    return await self.route_to_main_menu(update, context)

                top_matches = matches[:3]
                context.user_data["equipment_top_matches"] = top_matches

                keyboard = []
                msg = (
                        get_text(lang, "MSG_TOP_EQUIP_MATCHES", count=len(top_matches))
                        or f"🎯 **Top {len(top_matches)} Equipment Matches Found!**\n\nPlease select an asset below to review and send an offer:"
                )

                for i, match_item in enumerate(top_matches, start=1):
                    match_id = str(match_item.get("id", uuid.uuid4()))
                    raw_title = (
                            match_item.get("equipment_type")
                            or match_item.get("property_use")
                            or match_item.get("brand")
                            or "Equipment"
                    )
                    display_title = str(raw_title).title()
                    cost = safe_float(match_item.get("cost", match_item.get("price", 0.0)))
                    score = int(match_item.get("_match_score", 95))

                    currency = str(
                        match_item.get("currency")
                        or match_item.get("curr")
                        or match_item.get("price_currency")
                        or context.user_data.get("currency")
                        or "ETB"
                    ).strip().upper()

                    button_label = f"🚜 #{i}: {display_title} ({cost:,.2f} {currency} - {score}%)"
                    keyboard.append(
                        [InlineKeyboardButton(button_label, callback_data=f"select_equip_match:{match_id}")]
                    )

                btn_decline = get_text(lang, "BTN_DECLINE_RETURN") or "❌ Decline & Return"
                keyboard.append([InlineKeyboardButton(btn_decline, callback_data="cancel_match")])

                await processing_msg.delete()
                await update.message.reply_text(
                    msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
                )
                return self.SELECTING_MATCH

        except Exception as e:
            logger.error(f"Equipment Pipeline failure: {e}", exc_info=True)
            try:
                err_alert = (
                        get_text(lang, "err_internal_pipeline")
                        or "⚠️ An unexpected error occurred. Please try again."
                )
                await processing_msg.edit_text(err_alert, parse_mode="Markdown")
            except Exception:
                if update.message:
                    await update.message.reply_text("⚠️ An internal pipeline error occurred. Please try again.")
            return await self.route_to_main_menu(update, context)

    async def trigger_reverse_matching(
            self,
            update: Update,
            context: ContextTypes.DEFAULT_TYPE,
            listing_record: dict,
    ) -> int:
        """
        Scans seeker_requests for active users looking for the newly listed equipment.
        Delegates scoring directly to MatchingEngine.evaluate_matches with full multilingual support.
        """
        lang = context.user_data.get(
            "lang", context.user_data.get("language", "en")
        )
        if lang not in ["en", "am", "om"]:
            lang = "en"

        # Multilingual translation dictionary for dynamic UI elements
        i18n = {
            "en": {
                "scanning": "🔍 Scanning active Seeker Requests for your equipment...",
                "no_matches": "✅ **Listing is Live!**\nCurrently, no pending seekers match this exact asset. We will notify you when a match arises.",
                "success_notice": "✅ Listing created successfully. (Reverse scan skipped due to system load).",
                "seeker_label": "Seeker",
                "budget_label": "Budget",
                "top_seekers_found": "🎯 **Top {count} Seekers Found!**\nThese users are actively requesting an asset like yours. Select a Seeker below to send an offer:"
            },
            "am": {
                "scanning": "🔍 ለግዢ/ኪራይ የቀረቡ ንቁ የፈላጊ ጥያቄዎችን በመቃኘት ላይ...",
                "no_matches": "✅ **ዝርዝርዎ ተለቋል!**\nአሁን ከዚህ ንብረት ጋር በትክክል የሚዛመድ ምንም ፈላጊ የለም። ተዛማጅ ሲኖር እናሳውዎታለን።",
                "success_notice": "✅ ዝርዝርዎ በተሳካ ሁኔታ ተፈጥሯል።",
                "seeker_label": "ፈላጊ",
                "budget_label": "በጀት",
                "top_seekers_found": "🎯 **{count} ዋና ፈላጊዎች ተገኝተዋል!**\nእነዚህ ተጠቃሚዎች የእርስዎን የመሰለ ንብረት ይፈልጋሉ። ቅናሽ ለመላክ ከታች አንዱን ይምረጡ፡"
            },
            "om": {
                "scanning": "🔍 Gaaffiiwwan barbaaddootaa hojii irra jiran qorachaa jira...",
                "no_matches": "✅ **Tarreeffamni keessan baheera!**\nAmmaaf barbaadaan meeshaa kana waliin kallattiin wal-fakkatu hin jiru. Yeroo argamu si beeksifna.",
                "success_notice": "✅ Tarreeffamni milkaa'inaan uumameera.",
                "seeker_label": "Barbaadaa",
                "budget_label": "Baajata",
                "top_seekers_found": "🎯 **Barbaaddootni gurguddoon {count} argamaniiru!**\nFayyadamtootni kunniin meeshaa akka keessanii barbaadaa jiru. Ergaa erguuf barbaadaa armaan gadii keessaa filadhaa:"
            }
        }

        def t(key, **kwargs):
            text_dict = i18n.get(lang, i18n["en"])
            template = text_dict.get(key, i18n["en"].get(key, key))
            return template.format(**kwargs)

        loading_text = get_text(lang, "msg_scanning_seekers") or t("scanning")

        msg_loading = None
        if update.message:
            msg_loading = await update.message.reply_text(loading_text)

        # Standardized schema attributes aligned with provider_listings table
        listing_type = (
                listing_record.get("transaction_type")
                or listing_record.get("deal_type")
                or "rental"
        )
        provider_id = safe_int(
            listing_record.get("provider_id")
            or listing_record.get("user_id")
            or (update.effective_user.id if update.effective_user else 0)
        )

        # Persistent Currency Alignment
        listing_currency = (
            str(
                listing_record.get("currency")
                or context.user_data.get("currency")
                or "ETB"
            )
            .strip()
            .upper()
        )
        context.user_data["currency"] = listing_currency

        # Cache listing rate explicitly for reverse match
        listing_price = safe_float(
            listing_record.get("price", listing_record.get("cost", 0.0))
        )
        context.user_data["equipment_price"] = listing_price
        context.user_data["listing_asking_rate"] = listing_price
        context.user_data["is_reverse_match"] = True

        # Capture provider asset title without polluting seeker scope
        provider_title = (
                listing_record.get("equipment_type")
                or listing_record.get("property_use")
                or listing_record.get("brand")
                or listing_record.get("title")
                or "Equipment Asset"
        )
        context.user_data["provider_listing_title"] = provider_title
        context.user_data["asset_title"] = provider_title

        try:
            # Query seeker_requests with schema-aligned column filters
            res = (
                await self.db.table("seeker_requests")
                .select("*")
                .eq("vertical_type", "equipment")
                .eq("transaction_type", listing_type)
                .eq("status", "active")
                .execute()
            )

            if not res or not res.data:
                res = (
                    await self.db.table("seeker_requests")
                    .select("*")
                    .eq("vertical_type", "equipment")
                    .eq("deal_type", listing_type)
                    .eq("status", "active")
                    .execute()
                )

            candidates = (
                res.data if res and hasattr(res, "data") and res.data else []
            )

            # Normalize listing record parameters for MatchingEngine
            listing_record["vertical_type"] = listing_record.get(
                "vertical_type", "equipment"
            )
            listing_record["equipment_type"] = provider_title
            listing_record["property_use"] = (
                    listing_record.get("property_use") or provider_title
            )
            listing_record["transaction_type"] = listing_type
            listing_record["price"] = listing_price
            listing_record["currency"] = listing_currency
            listing_record["location_text"] = str(
                listing_record.get("location_text")
                or listing_record.get("location")
                or ""
            ).strip()

            matches = []
            matching_engine = (
                self.matching
                if hasattr(self, "matching") and hasattr(self.matching, "evaluate_matches")
                else None
            )

            if matching_engine:
                for req in candidates:
                    req_seeker_id = safe_int(
                        req.get("seeker_id") or req.get("user_id")
                    )
                    if req_seeker_id == provider_id:
                        continue

                    seeker_prefs = {
                        "vertical_type": "equipment",
                        "equipment_type": (
                                req.get("equipment_type")
                                or req.get("property_use")
                                or "General Equipment"
                        ),
                        "property_use": (
                                req.get("property_use")
                                or req.get("equipment_type")
                                or "General Equipment"
                        ),
                        "transaction_type": (
                                req.get("transaction_type")
                                or req.get("deal_type", listing_type)
                        ),
                        "max_budget": safe_float(
                            req.get("max_budget") or req.get("budget", 0.0)
                        ),
                        "currency": (
                            str(req.get("currency") or listing_currency)
                            .strip()
                            .upper()
                        ),
                        "preferred_brand": (
                                req.get("preferred_brand")
                                or req.get("brand")
                                or "Any"
                        ),
                        "required_amenities": (
                                req.get("required_amenities")
                                or req.get("features")
                                or "Any"
                        ),
                        "location_text": str(
                            req.get("location_text")
                            or req.get("location")
                            or ""
                        ).strip(),
                    }

                    try:
                        eval_results = await matching_engine.evaluate_matches(
                            "equipment", seeker_prefs, [listing_record]
                        )
                        if eval_results:
                            score = safe_float(
                                eval_results[0].get("_match_score", 0.0)
                            )
                            if score > 0.0:
                                req["_match_score"] = score
                                matches.append(req)
                    except Exception as score_err:
                        logger.error(
                            f"Equipment reverse match scoring failure for request {req.get('id')}: {score_err}"
                        )

            matches.sort(
                key=lambda x: safe_float(x.get("_match_score", 0)), reverse=True
            )
            top_matches = matches[:3]

            if not top_matches:
                fallback_msg = get_text(lang, "msg_no_seeker_matches") or t("no_matches")
                if msg_loading:
                    await msg_loading.edit_text(
                        fallback_msg, parse_mode="Markdown"
                    )
                return await self.route_to_main_menu(update, context)

            context.user_data["seeker_top_matches"] = top_matches
            context.user_data["top_matches"] = top_matches
            context.user_data["last_created_listing_id"] = listing_record.get(
                "id"
            )

            keyboard = []
            for i, match in enumerate(top_matches, start=1):
                m_id = str(match.get("id"))
                budget = safe_float(
                    match.get("max_budget") or match.get("budget", 0.0)
                )
                score = int(safe_float(match.get("_match_score", 95)))
                curr = str(match.get("currency") or listing_currency).strip().upper()

                s_label = t("seeker_label")
                b_label = t("budget_label")

                # Fixed multi-line f-string syntax error
                btn_label = f"👤 #{i} {s_label} ({b_label}: {budget:,.2f} {curr} | {score}%)"

                keyboard.append(
                    [
                        InlineKeyboardButton(
                            btn_label,
                            callback_data=f"eq_select_seeker_match:{m_id}",
                        )
                    ]
                )

            btn_decline = get_text(lang, "BTN_DECLINE_RETURN") or "❌ Decline & Return"
            keyboard.append(
                [
                    InlineKeyboardButton(
                        btn_decline, callback_data="equip_back_to_top"
                    )
                ]
            )

            title_text = get_text(lang, "msg_top_seekers_found", count=len(top_matches)) or t("top_seekers_found",
                                                                                              count=len(top_matches))

            if msg_loading:
                await msg_loading.edit_text(
                    title_text,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="Markdown",
                )
            return self.SELECTING_MATCH

        except Exception as e:
            logger.error(f"Reverse matching failed: {e}", exc_info=True)
            if msg_loading:
                await msg_loading.edit_text(t("success_notice"))
            return await self.route_to_main_menu(update, context)

    # ==========================================
    # 2. MATCH SELECTION & DEAL LOCKING PIPELINE (MULTILINGUAL)
    # ==========================================
    async def handle_match_selection(
            self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        query = update.callback_query
        if query:
            await query.answer()

        lang = context.user_data.get(
            "lang", context.user_data.get("language", "en")
        )
        if lang not in ["en", "am", "om"]:
            lang = "en"

        # Multilingual translation dictionary for all inline keys and card elements
        i18n = {
            "en": {
                "err_request_not_found": "⚠️ Selected request is no longer active.",
                "err_asset_not_found": "⚠️ Selected equipment is no longer active.",
                "req_location_label": "📍 <b>Requested Location (Seeker):</b>",
                "prop_location_label": "📍 <b>Asset Location (Provider):</b>",
                "max_budget": "💰 <b>Max Budget:</b>",
                "select_offer_seeker": "<i>Select an option below to submit your offer to this seeker:</i>",
                "select_offer_equip": "Select an option below to submit your offer to the owner:",
                "btn_send_asking": "✅ Send Asking Rate ({price:,.0f} {currency})",
                "btn_accept_asking": "✅ Accept Asking Rate ({price:,.0f} {currency})",
                "rate_label": "💰 <b>Rental Rate:</b>",
                "capacity_label": "⚡ <b>Capacity/Output:</b>"
            },
            "am": {
                "err_request_not_found": "⚠️ የተመረጠው ጥያቄ አሁን ንቁ አይደለም።",
                "err_asset_not_found": "⚠️ የተመረጠው መሳሪያ አሁን ንቁ አይደለም።",
                "req_location_label": "📍 <b>የተጠየቀበት ቦታ (ፈላጊ):</b>",
                "prop_location_label": "📍 <b>የመሳሪያው የሚገኝበት ቦታ (አቅራቢ):</b>",
                "max_budget": "💰 <b>ከፍተኛ በጀት:</b>",
                "select_offer_seeker": "<i>ለዚህ ፈላጊ ቅናሽዎን ለመላክ ከታች ያለውን አማራጭ ይምረጡ፡</i>",
                "select_offer_equip": "ለባለቤቱ ቅናሽዎን ለመላክ ከታች ያለውን አማራጭ ይምረጡ፡",
                "btn_send_asking": "✅ የሚጠየቀውን ዋጋ ላክ ({price:,.0f} {currency})",
                "btn_accept_asking": "✅ የሚጠየቀውን ዋጋ ተቀበል ({price:,.0f} {currency})",
                "rate_label": "💰 <b>የኪራይ ዋጋ:</b>",
                "capacity_label": "⚡ <b>አቅም/ውጤት:</b>"
            },
            "om": {
                "err_request_not_found": "⚠️ Gaaffiin filatame ammaa hojii irra hin jiru.",
                "err_asset_not_found": "⚠️ Meeshaan filatame ammaa hojii irra hin jiru.",
                "req_location_label": "📍 <b>Requested Location (Seeker):</b>",
                "prop_location_label": "📍 <b>Asset Location (Provider):</b>",
                "max_budget": "💰 <b>Baajata Olaanaa:</b>",
                "select_offer_seeker": "<i>Barbaadaa kanaaf dhiyeessi keessan erguuf filannoo armaan gadii irraa filadhaa:</i>",
                "select_offer_equip": "Abbaa qabeenyaaf dhiyeessi keessan erguuf filannoo armaan gadii irraa filadhaa:",
                "btn_send_asking": "✅ Gatii Gaafatame Ergi ({price:,.0f} {currency})",
                "btn_accept_asking": "✅ Gatii Gaafatame Fudhachuuf ({price:,.0f} {currency})",
                "rate_label": "💰 <b>Gatii Kiraajii:</b>",
                "capacity_label": "⚡ <b>Dandeettii:</b>"
            }
        }

        def t(lng_key, **kwargs):
            text_dict = i18n.get(lang, i18n["en"])
            template = text_dict.get(lng_key, i18n["en"].get(lng_key, lng_key))
            return template.format(**kwargs)

        cb_data = query.data if query else ""

        is_reverse = (
                cb_data.startswith("select_seeker_match:")
                or cb_data.startswith("eq_select_seeker_match:")
                or "seeker" in cb_data
                or context.user_data.get("is_reverse_match", False)
        )
        context.user_data["is_reverse_match"] = is_reverse
        match_id = cb_data.split(":")[1] if ":" in cb_data else ""

        def sanitize_html_label(raw_val: str, fallback_html: str) -> str:
            if not raw_val or raw_val.startswith("msg_"):
                return fallback_html
            cleaned = raw_val.replace("{location}", "").strip()
            cleaned = re.sub(r"\*(.*?)\*", r"<b>\1</b>", cleaned)
            return cleaned

        # Localization helper for backend property use / categories
        def localize_property_use(prop_val: str, target_lang: str) -> str:
            if not prop_val:
                return ""
            v_lower = prop_val.strip().lower()
            mapping = {
                "agriculture & irrigation": {
                    "en": "Agriculture & Irrigation",
                    "am": "ግብርና እና መስኖ",
                    "om": "Qonna fi Jallisii"
                },
                "construction & earthmoving": {
                    "en": "Construction & Earthmoving",
                    "am": "ኮንስትራክሽን እና ቁፋሮ",
                    "om": "Ijaarsa fi Qotee Biyyoo"
                },
                "generator & power": {
                    "en": "Generator & Power",
                    "am": "ጄኔሬተሮች እና ሃይል",
                    "om": "Jeneraatoota fi Humna Elektirikii"
                },
                "event tech & communication": {
                    "en": "Event Tech & Communication",
                    "am": "ንግድ እና ዝግጅቶች",
                    "om": "Teeknoolojii Qophee fi Walqunnamtii"
                }
            }
            if v_lower in mapping:
                return mapping[v_lower].get(target_lang, mapping[v_lower]["en"])
            return prop_val.title()

        if is_reverse:
            top_matches = (
                    context.user_data.get("seeker_top_matches")
                    or context.user_data.get("top_matches")
                    or []
            )
            selected_request = next(
                (m for m in top_matches if str(m.get("id")) == str(match_id)),
                None,
            )

            if not selected_request:
                err_msg = get_text(lang, "err_request_not_found") or t("err_request_not_found")
                if query:
                    await query.edit_message_text(err_msg, parse_mode="HTML")
                return await self.route_to_main_menu(update, context)

            budget = safe_float(
                selected_request.get("max_budget")
                or selected_request.get("budget", 0.0)
            )
            currency = (
                str(
                    selected_request.get("currency")
                    or context.user_data.get("currency")
                    or "ETB"
                )
                .strip()
                .upper()
            )
            context.user_data["currency"] = currency

            target_seeker_id = safe_int(
                selected_request.get("seeker_id")
                or selected_request.get("user_id")
                or 0
            )
            provider_asking_price = context.user_data.get(
                "listing_asking_rate",
                context.user_data.get("equipment_price", budget),
            )

            req_equipment = str(
                selected_request.get("equipment_type")
                or selected_request.get("property_use")
                or selected_request.get("preferred_brand", "Equipment")
            ).title()

            # Explicit Seeker Location Storage (Never Overwritten by Listing Location)
            req_loc = str(
                selected_request.get("location_text")
                or selected_request.get("operating_region")
                or selected_request.get("destination_text")
                or "Any Location"
            ).strip()

            context.user_data["target_seeker_id"] = target_seeker_id
            context.user_data["active_equipment_deal_id"] = match_id
            context.user_data["request_id"] = match_id
            context.user_data["max_budget"] = budget
            context.user_data["seeker_budget"] = budget
            context.user_data["equipment_price"] = budget
            context.user_data["equipment_type"] = req_equipment
            context.user_data["request_location_text"] = req_loc

            seeker_req_title = f"Request for {req_equipment} in {req_loc}"
            context.user_data["seeker_request_title"] = seeker_req_title

            header_text = sanitize_html_label(
                get_text(lang, "msg_seeker_request_selected_equip"),
                "👤 <b>Seeker Request Selected:</b>",
            )

            card_msg = (
                f"⚡ <b>SMART MATCH SECURE DEAL</b> ⚡\n"
                f"───────────────────────\n"
                f"{header_text} <b>{html.escape(seeker_req_title)}</b>\n"
                f"{t('req_location_label')} <code>{html.escape(req_loc)}</code>\n"
                f"{t('max_budget')} <code>{budget:,.2f} {currency}</code>\n"
                f"───────────────────────\n"
                f"<i>{t('select_offer_seeker')}</i>"
            )

            btn_custom = (
                    get_text(lang, "BTN_SEND_OFFER") or "🤝 Enter Custom Offer"
            )
            btn_asking = t("btn_send_asking", price=provider_asking_price, currency=currency)
            btn_decline = (
                    get_text(lang, "BTN_DECLINE_RETURN") or "❌ Decline & Return"
            )

            keyboard = [
                [
                    InlineKeyboardButton(
                        btn_custom,
                        callback_data=f"equip_prompt_offer:{match_id}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        btn_asking, callback_data=f"lock_equip_deal:{match_id}"
                    )
                ],
                [
                    InlineKeyboardButton(
                        btn_decline, callback_data="equip_back_to_top"
                    )
                ],
            ]

            if query:
                await query.edit_message_text(
                    text=card_msg,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="HTML",
                )
            return self.SELECTING_MATCH

        else:
            top_matches = (
                    context.user_data.get("equipment_top_matches")
                    or context.user_data.get("top_matches")
                    or []
            )
            selected_equipment = next(
                (m for m in top_matches if str(m.get("id")) == str(match_id)),
                None,
            )

            if not selected_equipment:
                err_msg = get_text(lang, "err_asset_not_found") or t("err_asset_not_found")
                if query:
                    await query.edit_message_text(err_msg, parse_mode="HTML")
                return await self.route_to_main_menu(update, context)

            price = safe_float(
                selected_equipment.get("price")
                or selected_equipment.get("cost", 0.0)
            )
            currency = (
                str(
                    selected_equipment.get("currency")
                    or context.user_data.get("currency")
                    or "ETB"
                )
                .strip()
                .upper()
            )
            context.user_data["currency"] = currency

            provider_id = safe_int(
                selected_equipment.get("provider_id")
                or selected_equipment.get("user_id")
                or 0
            )

            raw_brand = str(
                selected_equipment.get("equipment_type")
                or selected_equipment.get("property_use")
                or selected_equipment.get("brand")
                or "Equipment"
            ).title()

            # Explicit Provider Listing Location Storage
            raw_loc = str(
                selected_equipment.get("location_text")
                or selected_equipment.get("operating_region")
                or selected_equipment.get("destination_text")
                or ""
            ).strip()
            brand, prop_loc = raw_brand, raw_loc

            raw_capacity = (
                    selected_equipment.get("capacity_output")
                    or selected_equipment.get("capacity")
                    or context.user_data.get("capacity_output")
            )

            equip_type = (
                    selected_equipment.get("equipment_type")
                    or selected_equipment.get("property_use")
                    or ""
            )

            formatted_cap = (
                self._format_capacity_output(raw_capacity, equip_type)
                if hasattr(self, "_format_capacity_output")
                else (str(raw_capacity) if raw_capacity else "N/A")
            )

            capacity = (
                self.localize_capacity_output(formatted_cap, lang)
                if hasattr(self, "localize_capacity_output")
                else formatted_cap
            )

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
                                        parsed_json.get("url")
                                        or parsed_json.get("file_id")
                                        or parsed_json.get("path")
                                )
                        except Exception:
                            pass
                    return cleaned if cleaned else None
                if isinstance(item, dict):
                    return (
                            item.get("url")
                            or item.get("file_id")
                            or item.get("path")
                            or item.get("image_url")
                    )
                return None

            for key in ["photo_url", "image_url", "photo_file_id", "images", "photos"]:
                val = selected_equipment.get(key)
                if val:
                    photo_ref = (
                        _extract_url_or_id(val[0])
                        if isinstance(val, list) and len(val) > 0
                        else _extract_url_or_id(val)
                    )
                    if photo_ref:
                        break

            asset_title = f"{brand} in {prop_loc}"
            context.user_data["active_equipment_deal_id"] = match_id
            context.user_data["last_created_listing_id"] = match_id
            context.user_data["equipment_price"] = price
            context.user_data["provider_id"] = provider_id
            context.user_data["asset_title"] = asset_title
            context.user_data["provider_listing_title"] = asset_title
            context.user_data["listing_location_text"] = prop_loc

            header_text = sanitize_html_label(
                get_text(lang, "msg_equipment_selected"),
                "🚜 <b>Equipment Selected:</b>",
            )

            requested_equipment = str(
                context.user_data.get("equipment_type")
                or context.user_data.get("property_use")
                or ""
            ).strip()

            has_diff = (
                    requested_equipment
                    and requested_equipment.lower() != brand.lower()
            )

            property_use_fallbacks = {
                "en": (
                    "🛠️ <b>Requested Equipment:</b>"
                    if has_diff
                    else "🛠️ <b>Equipment Type:</b>"
                ),
                "am": (
                    "🛠️ <b>የተጠየቀው መሳሪያ:</b>"
                    if has_diff
                    else "🛠️ <b>የመሳሪያ ዓይነት:</b>"
                ),
                "om": (
                    "🛠️ <b>Meeshaa Gaafatame:</b>"
                    if has_diff
                    else "🛠️ <b>Gosa Meeshaalee:</b>"
                ),
            }
            property_use_label = property_use_fallbacks.get(
                lang, property_use_fallbacks["en"]
            )

            raw_property_use = (
                requested_equipment
                if has_diff
                else (
                        selected_equipment.get("property_use")
                        or selected_equipment.get("equipment_type")
                        or brand
                )
            )

            localized_property_use = localize_property_use(str(raw_property_use), lang)

            # Retrieve Seeker Request Location if present in context or fetch via active request_id
            req_loc = str(
                context.user_data.get("request_location_text")
                or context.user_data.get("seeker_location")
                or context.user_data.get("location_text")
                or ""
            ).strip()

            rate_text = sanitize_html_label(
                get_text(lang, "msg_rental_rate"), t("rate_label")
            )
            capacity_label = sanitize_html_label(
                get_text(lang, "msg_capacity_output"), t("capacity_label")
            )
            prompt_offer_text = sanitize_html_label(
                get_text(lang, "msg_select_offer_option_equip"),
                t("select_offer_equip"),
            )

            property_use_line = f"{property_use_label} <code>{html.escape(localized_property_use)}</code>\n"
            req_location_line = f"{t('req_location_label')} <code>{html.escape(req_loc)}</code>\n"
            prop_location_line = f"{t('prop_location_label')} <code>{html.escape(prop_loc)}</code>\n"

            capacity_line = (
                f"{capacity_label} <code>{html.escape(str(capacity))}</code>\n"
                if capacity and str(capacity).strip().lower() not in ["n/a", "none", ""]
                else ""
            )

            card_msg = (
                f"⚡ <b>SMART MATCH SECURE DEAL</b> ⚡\n"
                f"───────────────────────\n"
                f"{header_text} <b>{html.escape(brand)}</b>\n"
                f"───────────────────────\n"
                f"{property_use_line}"
                f"{req_location_line}"
                f"{prop_location_line}"
                f"{rate_text} <code>{price:,.2f} {currency}</code>\n"
                f"{capacity_line}"
                f"───────────────────────\n"
                f"<i>{prompt_offer_text}</i>"
            )

            btn_custom = (
                    get_text(lang, "BTN_SEND_OFFER") or "🤝 Enter Custom Offer"
            )
            btn_asking = t("btn_accept_asking", price=price, currency=currency)
            btn_decline = (
                    get_text(lang, "BTN_DECLINE_RETURN") or "❌ Back to Top Matches"
            )

            keyboard = [
                [
                    InlineKeyboardButton(
                        btn_custom,
                        callback_data=f"equip_prompt_offer:{match_id}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        btn_asking, callback_data=f"lock_equip_deal:{match_id}"
                    )
                ],
                [
                    InlineKeyboardButton(
                        btn_decline, callback_data="equip_back_to_top"
                    )
                ],
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            presigned_url = None
            if photo_ref:
                img_service: Any = getattr(
                    self, "image_service", getattr(self, "storage", None)
                )
                if img_service and hasattr(img_service, "get_presigned_url_async"):
                    try:
                        presigned_url = (
                            await img_service.get_presigned_url_async(photo_ref)
                        )
                    except Exception as sign_err:
                        logger.warning(
                            f"Failed to generate pre-signed URL for '{photo_ref}': {sign_err}"
                        )

            photo_dispatched = False
            if presigned_url and query and query.message and update.effective_chat:
                try:
                    try:
                        await context.bot.delete_message(
                            chat_id=update.effective_chat.id,
                            message_id=query.message.message_id,
                        )
                    except (BadRequest, TelegramError):
                        pass

                    await context.bot.send_photo(
                        chat_id=update.effective_chat.id,
                        photo=presigned_url,
                        caption=card_msg,
                        reply_markup=reply_markup,
                        parse_mode="HTML",
                    )
                    photo_dispatched = True
                except (BadRequest, TelegramError) as photo_err:
                    logger.warning(f"Failed to dispatch photo card: {photo_err}")

            if not photo_dispatched and query and query.message:
                try:
                    await query.edit_message_text(
                        text=card_msg,
                        reply_markup=reply_markup,
                        parse_mode="HTML",
                    )
                except BadRequest:
                    if update.effective_chat:
                        await context.bot.send_message(
                            chat_id=update.effective_chat.id,
                            text=card_msg,
                            reply_markup=reply_markup,
                            parse_mode="HTML",
                        )

            return self.SELECTING_MATCH

    # ==========================================
    # 3. UNIFIED NAVIGATION & BACK TO TOP MATCHES (MULTILINGUAL)
    # ==========================================
    async def handle_back_to_top_matches(
            self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        """
        Refreshes top matches list seamlessly for both Forward matching (Equipment options)
        and Reverse matching (Seeker requests) without state loss or context switches,
        including automatic database fallback if session matches have expired.
        """
        query = update.callback_query
        if query:
            try:
                await query.answer()
            except Exception as e:
                logger.debug(f"Callback answer exception: {e}")

        lang = (
                context.user_data.get("lang")
                or context.user_data.get("language")
                or "en"
        )
        if lang not in ["en", "am", "om"]:
            lang = "en"

        i18n = {
            "en": {
                "no_matches_found": "⚠️ <b>Session Refreshed:</b> No previous matches were found in active session memory. Please run a new search.",
                "msg_no_matches_found": "⚠️ No active matches found at the moment. Returning to main menu.",
                "secure_deal_header": "⚡ <b>SMART MATCH SECURE DEAL</b> ⚡\n───────────────────────",
                "top_seekers_header": "🎯 <b>Top {count} Seeker Matches Found!</b>\n\nThese users are actively requesting an asset matching your equipment. Select a Seeker below to send your offer:",
                "top_eq_header": "🚜 <b>Top Matching Equipment Options:</b>\n\nPlease select one of the options below:",
                "seeker_label": "Seeker",
                "budget_label": "Budget",
                "btn_decline": "❌ Decline & Return",
                "btn_cancel": "❌ Cancel Search"
            },
            "am": {
                "no_matches_found": "⚠️ <b>ክፍለ-ጊዜው ታድሷል፡</b> በቀድሞው ክፍለ-ጊዜ ማህደረ ትውስታ ውስጥ ምንም የቀድሞ ተዛማጆች አልተገኙም። እባክዎ አዲስ ፍለጋ ያካሂዱ።",
                "msg_no_matches_found": "⚠️ በአሁኑ ጊዜ ምንም ንቁ ግጥሚያዎች አልተገኙም። ወደ ዋናው ምናሌ በመመለስ ላይ...",
                "secure_deal_header": "⚡ <b>ስማርት ማች አስተማማኝ ድርድር</b> ⚡\n───────────────────────",
                "top_seekers_header": "🎯 <b>የተገኙ {count} ከፍተኛ ፈላጊ ተዛማጆች!</b>\n\nእነዚህ ተጠቃሚዎች ከመሳሪያዎ ጋር የሚዛመድ ንብረት በንቃት እየጠየቁ ነው። ቅናሽዎን ለመላክ ከታች አንዱን ፈላጊ ይምረጡ፡",
                "top_eq_header": "🚜 <b>ከፍተኛ ተዛማጅ የመሳሪያ አማራጮች፡</b>\n\nእባክዎ ከታች ካሉት አማራጮች አንዱን ይምረጡ፡",
                "seeker_label": "ፈላጊ",
                "budget_label": "በጀት",
                "btn_decline": "❌ ውድቅ አድርግና ተመለስ",
                "btn_cancel": "❌ ፍለጋ ሰርዝ"
            },
            "om": {
                "no_matches_found": "⚠️ <b>Qajeeltichi Haaromfameera:</b> Yaadannoo yeroo ammaa keessatti walsimansiisaan duraa hin argamne. Maaloo barbaacha haaraa jalqabaa.",
                "msg_no_matches_found": "⚠️ Amma yeroo ammaa walitti dhufeenyi hojii irra jiru hin argamne. Gara menuu deebi'aa.",
                "secure_deal_header": "⚡ <b>SMART MATCH SECURE DEAL</b> ⚡\n───────────────────────",
                "top_seekers_header": "🎯 <b>Barbaaddootni Wal-gitan {count} Argamaniiru!</b>\n\nFayyadamtootni kunniin meeshaa keessanii wajjin wal-gitu gaafachaa jiru. Dhiyeessi keessan erguuf barbaadaa armaan gadii keessaa tokko filadhaa:",
                "top_eq_header": "🚜 <b>Filannoowwan Meeshaa Wal-gitan:</b>\n\nMaaloo filannoowwan armaan gadii keessaa tokko filadhaa:",
                "seeker_label": "Barbaadaa",
                "budget_label": "Baajata",
                "btn_decline": "❌ Diduu & Deebi'uu",
                "btn_cancel": "❌ Barbaacha Haqaa"
            }
        }

        def t(key, **kwargs):
            text_dict = i18n.get(lang, i18n["en"])
            template = text_dict.get(key, i18n["en"].get(key, key))
            return template.format(**kwargs)

        # Honor active session state first; only fallback to callback text if state is unset
        if "is_reverse_match" in context.user_data:
            is_reverse = bool(context.user_data.get("is_reverse_match"))
        elif query and query.data:
            qdata = query.data.lower()
            if "seeker" in qdata or "reverse" in qdata:
                is_reverse = True
            else:
                is_reverse = False
        else:
            is_reverse = False

        context.user_data["is_reverse_match"] = is_reverse

        # Universal Session Currency
        session_currency = str(
            context.user_data.get("currency")
            or context.user_data.get("equipment_currency")
            or "ETB"
        ).strip().upper()

        primary_key, secondary_key = (
            ("seeker_top_matches", "equipment_top_matches")
            if is_reverse
            else ("equipment_top_matches", "seeker_top_matches")
        )

        top_matches = (
                context.user_data.get(primary_key)
                or context.user_data.get("top_matches")
                or context.user_data.get(secondary_key)
                or []
        )

        # Database Fallback Check if session top_matches expired or missing
        if not top_matches and hasattr(self, "db") and self.db:
            user_id = update.effective_user.id if update.effective_user else 0
            try:
                if is_reverse:
                    listing_res = (
                        await self.db.table("provider_listings")
                        .select("id, equipment_type, property_use, brand, location_text, price, currency, status")
                        .eq("provider_id", int(user_id))
                        .eq("status", "active")
                        .order("created_at", desc=True)
                        .limit(5)
                        .execute()
                    )
                    if listing_res and hasattr(listing_res, "data") and listing_res.data:
                        listing_item = listing_res.data[0]
                        listing_id = listing_item.get("id")
                        context.user_data["last_created_listing_id"] = listing_id

                        req_res = (
                            await self.db.table("seeker_requests")
                            .select(
                                "id, seeker_id, equipment_type, property_use, location_text, max_budget, currency, status")
                            .eq("status", "active")
                            .limit(10)
                            .execute()
                        )
                        if req_res and hasattr(req_res, "data") and req_res.data:
                            top_matches = req_res.data
                            context.user_data["seeker_top_matches"] = top_matches
                else:
                    req_res = (
                        await self.db.table("seeker_requests")
                        .select(
                            "id, seeker_id, equipment_type, property_use, location_text, max_budget, currency, status")
                        .eq("seeker_id", int(user_id))
                        .eq("status", "active")
                        .order("created_at", desc=True)
                        .limit(1)
                        .execute()
                    )
                    if req_res and hasattr(req_res, "data") and req_res.data:
                        req_item = req_res.data[0]
                        context.user_data["request_id"] = req_item.get("id")

                        list_res = (
                            await self.db.table("provider_listings")
                            .select(
                                "id, provider_id, equipment_type, property_use, brand, location_text, price, currency, status")
                            .eq("status", "active")
                            .limit(10)
                            .execute()
                        )
                        if list_res and hasattr(list_res, "data") and list_res.data:
                            top_matches = list_res.data
                            context.user_data["equipment_top_matches"] = top_matches
            except Exception as db_fallback_err:
                logger.warning(f"Database fallback query failed in handle_back_to_top_matches: {db_fallback_err}")

        if not top_matches:
            logger.warning(
                "handle_back_to_top_matches called but no top matches found in context or DB fallback."
            )
            no_matches_text = get_text(lang, "msg_no_matches_found") or t("msg_no_matches_found")
            chat_id = update.effective_chat.id if update.effective_chat else None
            if chat_id:
                try:
                    if query and query.message:
                        try:
                            if getattr(query.message, "photo", None) or getattr(query.message, "caption", None):
                                await query.edit_message_caption(caption=no_matches_text, parse_mode="HTML")
                            else:
                                await query.edit_message_text(no_matches_text, parse_mode="HTML")
                        except Exception:
                            await context.bot.send_message(
                                chat_id=chat_id, text=no_matches_text, parse_mode="HTML"
                            )
                    else:
                        await context.bot.send_message(
                            chat_id=chat_id, text=no_matches_text, parse_mode="HTML"
                        )
                except Exception as err:
                    logger.error(f"Failed to send no-matches notice: {err}")

            return await self.route_to_main_menu(update, context)

        keyboard = []
        if is_reverse:
            context.user_data.pop("active_seeker_deal_id", None)
            context.user_data.pop("active_equipment_deal_id", None)

            header_text = (
                f"{t('secure_deal_header')}\n"
                f"{t('top_seekers_header', count=len(top_matches))}"
            )

            for i, match in enumerate(top_matches, start=1):
                match_id = str(match.get("id", match.get("request_id", "")))
                budget = safe_float(
                    match.get("max_budget") or match.get("budget") or match.get("price", 0.0)
                )
                curr = str(
                    match.get("currency") or session_currency
                ).strip().upper()
                score = int(
                    safe_float(match.get("_match_score", match.get("score", 95)))
                )

                s_label = t("seeker_label")
                b_label = t("budget_label")
                btn_label = f"👤 #{i} {s_label} ({b_label}: {budget:,.2f} {curr} | {score}%)"
                keyboard.append(
                    [
                        InlineKeyboardButton(
                            btn_label,
                            callback_data=f"eq_select_seeker_match:{match_id}",
                        )
                    ]
                )

            btn_decline = (
                    get_text(lang, "BTN_DECLINE_RETURN")
                    or get_text(lang, "BTN_SKIP")
                    or t("btn_decline")
            )
            keyboard.append(
                [InlineKeyboardButton(btn_decline, callback_data="cancel_match")]
            )

        else:
            context.user_data.pop("active_equipment_deal_id", None)
            context.user_data.pop("active_seeker_deal_id", None)

            header_text = (
                f"{t('secure_deal_header')}\n"
                f"{t('top_eq_header')}"
            )

            for idx, match in enumerate(top_matches, start=1):
                raw_brand = str(
                    match.get("brand")
                    or match.get("equipment_type")
                    or match.get("property_use")
                    or "Equipment"
                ).title()
                raw_loc = str(
                    match.get("location_text") or "Addis Ababa"
                )
                price = safe_float(
                    match.get("price") or match.get("cost") or match.get("max_budget") or match.get("budget", 0.0)
                )
                curr = str(
                    match.get("currency") or session_currency
                ).strip().upper()
                match_id = str(match.get("id", match.get("listing_id", "")))

                btn_text = f"{idx}. {raw_brand} - {raw_loc} ({price:,.0f} {curr})"
                keyboard.append(
                    [
                        InlineKeyboardButton(
                            btn_text, callback_data=f"select_equip_match:{match_id}"
                        )
                    ]
                )

            btn_cancel = get_text(lang, "BTN_CANCEL") or t("btn_cancel")
            keyboard.append(
                [InlineKeyboardButton(btn_cancel, callback_data="cancel_match")]
            )

        reply_markup = InlineKeyboardMarkup(keyboard)
        chat_id = update.effective_chat.id if update.effective_chat else None

        try:
            if query and query.message:
                if getattr(query.message, "photo", None) or getattr(query.message, "caption", None):
                    try:
                        await query.edit_message_caption(
                            caption=header_text,
                            reply_markup=reply_markup,
                            parse_mode="HTML"
                        )
                    except Exception:
                        if chat_id:
                            await context.bot.send_message(
                                chat_id=chat_id,
                                text=header_text,
                                reply_markup=reply_markup,
                                parse_mode="HTML",
                            )
                else:
                    try:
                        await query.edit_message_text(
                            text=header_text,
                            reply_markup=reply_markup,
                            parse_mode="HTML",
                        )
                    except Exception:
                        if chat_id:
                            await context.bot.send_message(
                                chat_id=chat_id,
                                text=header_text,
                                reply_markup=reply_markup,
                                parse_mode="HTML",
                            )
            elif chat_id:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=header_text,
                    reply_markup=reply_markup,
                    parse_mode="HTML",
                )
        except Exception as e:
            logger.error(
                f"Failed to refresh equipment top matches UI block: {e}"
            )
            if chat_id:
                try:
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=header_text,
                        reply_markup=reply_markup,
                        parse_mode="HTML",
                    )
                except Exception as send_err:
                    logger.error(
                        f"Failed to send fallback equipment top matches message: {send_err}"
                    )

        return getattr(self, "SELECTING_MATCH", 10)

    # ==========================================
    # 4. CUSTOM OFFER PROMPTING & RECEIVING
    # ==========================================

    async def _resolve_user_language_db_first(
            self, uid: int, context: ContextTypes.DEFAULT_TYPE
    ) -> str:
        """
        Helper function to fetch ground-truth language preference (DB -> Context -> English Default).
        Supports 'en', 'am', and 'om'.
        """
        if uid and hasattr(self, "db") and self.db:
            try:
                user_res = (
                    await self.db.table("users")
                    .select("preferred_language, language_code, language")
                    .eq("telegram_id", int(uid))
                    .maybe_single()
                    .execute()
                )
                if user_res and hasattr(user_res, "data") and user_res.data:
                    udata = user_res.data
                    pref = (
                            udata.get("preferred_language")
                            or udata.get("language_code")
                            or udata.get("language")
                            or ""
                    ).strip().lower()
                    if pref in ["en", "am", "om"]:
                        return pref
            except Exception as l_err:
                logger.warning(f"Failed to fetch language from DB for user {uid}: {l_err}")

        ctx_lang = (
                context.user_data.get("lang")
                or context.user_data.get("language")
                or "en"
        ).strip().lower()
        if ctx_lang in ["en", "am", "om"]:
            return ctx_lang
        return "en"

    async def prompt_custom_offer(
            self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        """
        Handles prompt custom offer with absolute database ground-truth enforcement
        and robust, precise equipment and location extraction across deals, seeker_requests, and provider_listings
        for both forward and reverse matching workflows, ensuring Asking Rate prioritizes provider listing price.
        """
        query = update.callback_query
        if query:
            await query.answer()
            if query.data and ":" in query.data:
                match_id = query.data.split(":")[1]
                context.user_data["active_equipment_deal_id"] = match_id

        effective_uid = update.effective_user.id if update.effective_user else 0
        user_lang = await self._resolve_user_language_db_first(effective_uid, context)

        active_deal_id = str(
            context.user_data.get("active_equipment_deal_id", "pending")
        )

        # ========================================================================
        # SINGLE SOURCE OF TRUTH: Initialize variables strictly from DB
        # ========================================================================
        is_reverse = False
        asking_price: float = 0.0
        resolved_currency: str = "ETB"
        fetched_req_loc = None
        fetched_prop_loc = None
        model_name = None
        brand_name = None
        property_use = None
        raw_cap_val = None
        req_eq = None
        list_eq = None

        def clean_text(val) -> str | None:
            if not val:
                return None
            s = str(val).strip()
            if not s or s.lower() in ["n/a", "none", "null", "-"]:
                return None
            return s

        if active_deal_id != "pending" and hasattr(self, "db") and self.db:
            try:
                # 1. Fetch from deals table
                deal_res = (
                    await self.db.table("deals")
                    .select(
                        "current_price, agreed_price, listing_id, request_id, currency, seeker_id, provider_id, vertical_type, origin, destination, asset_title"
                    )
                    .eq("id", active_deal_id)
                    .maybe_single()
                    .execute()
                )

                if deal_res and hasattr(deal_res, "data") and deal_res.data:
                    ddata = deal_res.data
                    current_user_id = effective_uid

                    if safe_int(ddata.get("seeker_id")) == current_user_id:
                        is_reverse = True

                    if clean_text(ddata.get("asset_title")):
                        model_name = clean_text(ddata.get("asset_title"))

                    if ddata.get("origin"):
                        fetched_req_loc = clean_text(ddata.get("origin"))
                    if ddata.get("destination"):
                        fetched_prop_loc = clean_text(ddata.get("destination"))

                    if ddata.get("currency"):
                        resolved_currency = str(ddata["currency"]).strip().upper()

                    # Prioritize active deal pricing if already established
                    deal_price = safe_float(ddata.get("current_price")) or safe_float(ddata.get("agreed_price"))
                    if deal_price > 0:
                        asking_price = deal_price

                    # 2. Fetch from provider_listings table using listing_id FIRST to prioritize provider's asking price
                    listing_id = ddata.get("listing_id")
                    if listing_id:
                        listing_res = (
                            await self.db.table("provider_listings")
                            .select(
                                "price, property_use, location_text, location, operating_region, destination_text, brand, make_model_year, currency, equipment_type, vehicle_type, capacity_output"
                            )
                            .eq("id", listing_id)
                            .maybe_single()
                            .execute()
                        )
                        if listing_res and hasattr(listing_res, "data") and listing_res.data:
                            ldata = listing_res.data

                            # Robust Provider Asset Location extraction
                            fetched_prop_loc = (
                                    clean_text(ldata.get("location_text"))
                                    or clean_text(ldata.get("location"))
                                    or clean_text(ldata.get("operating_region"))
                                    or clean_text(ldata.get("destination_text"))
                                    or fetched_prop_loc
                            )

                            # Prioritize provider listing price as the primary asking price if deal price isn't set
                            if asking_price <= 0 and ldata.get("price") is not None:
                                asking_price = safe_float(ldata["price"])

                            if resolved_currency == "ETB" and ldata.get("currency"):
                                resolved_currency = str(ldata["currency"]).strip().upper()

                            # Extract exact equipment name/model from provider listing
                            listing_equip = (
                                    clean_text(ldata.get("equipment_type"))
                                    or clean_text(ldata.get("make_model_year"))
                                    or clean_text(ldata.get("vehicle_type"))
                            )

                            generic_categories = [
                                "construction & earthmoving", "agriculture & irrigation",
                                "generators & power", "event tech & commercial", "residential",
                                "commercial"
                            ]
                            if listing_equip and listing_equip.lower() not in generic_categories:
                                list_eq = listing_equip
                                model_name = listing_equip

                            if ldata.get("brand"):
                                brand_name = ldata.get("brand")
                            if ldata.get("capacity_output"):
                                raw_cap_val = ldata.get("capacity_output")
                            if ldata.get("property_use"):
                                property_use = ldata.get("property_use")

                    # 3. Fetch from seeker_requests table using request_id (for seeker location & equipment preference)
                    request_id = ddata.get("request_id")
                    if request_id:
                        req_res = (
                            await self.db.table("seeker_requests")
                            .select(
                                "equipment_type, property_use, vehicle_type, capacity_output, currency, location_text, location, operating_region, destination_text, preferred_brand, max_budget"
                            )
                            .eq("id", request_id)
                            .maybe_single()
                            .execute()
                        )
                        if req_res and hasattr(req_res, "data") and req_res.data:
                            rdata = req_res.data

                            # Robust Seeker Location extraction
                            fetched_req_loc = (
                                    clean_text(rdata.get("location_text"))
                                    or clean_text(rdata.get("location"))
                                    or clean_text(rdata.get("operating_region"))
                                    or clean_text(rdata.get("destination_text"))
                                    or fetched_req_loc
                            )

                            if resolved_currency == "ETB" and rdata.get("currency"):
                                resolved_currency = str(rdata["currency"]).strip().upper()

                            # NOTE: Seeker max_budget fallback is intentionally removed here
                            # so Asking Rate absolutely remains bound to the provider's listing price.

                            req_eq = (
                                    clean_text(rdata.get("equipment_type"))
                                    or clean_text(rdata.get("property_use"))
                                    or clean_text(rdata.get("vehicle_type"))
                            )
                            if req_eq and not model_name:
                                model_name = req_eq

                            if rdata.get("capacity_output") and not raw_cap_val:
                                raw_cap_val = rdata.get("capacity_output")
                            if rdata.get("preferred_brand") and not brand_name:
                                brand_name = rdata.get("preferred_brand")

            except Exception as _err:
                logger.warning(f"Could not fetch deal ground-truth metadata in prompt_custom_offer: {_err}")

        # Fallback assignments if values remain unpopulated
        req_loc = fetched_req_loc or "Not Specified"
        prop_loc = fetched_prop_loc or "Not Specified"
        currency = resolved_currency
        model_name = model_name or req_eq or "Selected Equipment"

        # ========================================================================
        # MULTILINGUAL PROPERTY_USE / CATEGORY TRANSLATION HELPER (ALIGNED)
        # ========================================================================
        def localize_property_use(val: str, lang: str) -> str:
            if not val:
                return ""

            clean_val = str(val).strip()
            val_lower = clean_val.lower()

            mapping = {
                "agriculture & irrigation": {
                    "am": "🌾 ግብርና እና መስኖ",
                    "om": "🌾 Qonnaa fi Jallisii",
                    "en": "🌾 Agriculture & Irrigation"
                },
                "agricultural equipment": {
                    "am": "🌾 ግብርና እና መስኖ",
                    "om": "🌾 Qonnaa fi Jallisii",
                    "en": "🌾 Agriculture & Irrigation"
                },
                "construction & earthmoving": {
                    "am": "🚜 ኮንስትራክሽን እና ቁፋሮ",
                    "om": "🚜 ijaarsa fi Qotiisa",
                    "en": "🚜 Construction & Earthmoving"
                },
                "construction equipment": {
                    "am": "🚜 ኮንስትራክሽን እና ቁፋሮ",
                    "om": "🚜 ijaarsa fi Qotiisa",
                    "en": "🚜 Construction & Earthmoving"
                },
                "generators & power": {
                    "am": "⚡ ጄኔሬተሮች እና ሃይል",
                    "om": "⚡ Jeneraataroota fi Humna",
                    "en": "⚡ Generators & Power"
                },
                "event tech & commercial": {
                    "am": "🎪 ንግድ እና ዝግጅቶች",
                    "om": "🎪 Daldala fi Qophiilee",
                    "en": "🎪 Event Tech & Commercial"
                },
                "events & audio": {
                    "am": "🎪 ንግድ እና ዝግጅቶች",
                    "om": "🎪 Daldala fi Qophiilee",
                    "en": "🎪 Event Tech & Commercial"
                },
                "industrial & energy": {
                    "am": "⚡ ጄኔሬተሮች እና ሃይል",
                    "om": "⚡ Jeneraataroota fi Humna",
                    "en": "⚡ Generators & Power"
                },
                "residential": {
                    "am": "መኖሪያ ቤት",
                    "om": "Mana Jireenyaa",
                    "en": "Residential"
                },
                "commercial": {
                    "am": "ንግድ ቤት",
                    "om": "Mana Daldalaa",
                    "en": "Commercial"
                }
            }

            if val_lower in mapping:
                return mapping[val_lower].get(lang, mapping[val_lower]["en"])

            if "agri" in val_lower or "irrigation" in val_lower or "qonna" in val_lower or "እርሻ" in val_lower or "ግብርና" in val_lower:
                return mapping["agriculture & irrigation"].get(lang, "🌾 Agriculture & Irrigation")
            elif "const" in val_lower or "ijaarsa" in val_lower or "ኮንስትራክሽን" in val_lower or "earth" in val_lower:
                return mapping["construction & earthmoving"].get(lang, "🚜 Construction & Earthmoving")
            elif "gen" in val_lower or "power" in val_lower or "energ" in val_lower or "ሃይል" in val_lower or "ጄኔሬተር" in val_lower:
                return mapping["generators & power"].get(lang, "⚡ Generators & Power")
            elif "event" in val_lower or "audio" in val_lower or "tech" in val_lower or "commercial" in val_lower or "ዝግጅት" in val_lower:
                return mapping["event tech & commercial"].get(lang, "🎪 Event Tech & Commercial")

            return clean_val

        localized_prop_use = localize_property_use(property_use, user_lang)

        formatted_price = f"{asking_price:,.2f} {currency}"

        escaped_model = html.escape(str(model_name))
        escaped_req_loc = html.escape(str(req_loc))
        escaped_prop_loc = html.escape(str(prop_loc))
        escaped_brand = html.escape(str(brand_name)) if brand_name else None
        escaped_property_use = (
            html.escape(str(localized_prop_use)) if localized_prop_use else None
        )

        eq_differs = bool(
            req_eq
            and list_eq
            and str(req_eq).strip().lower() != str(list_eq).strip().lower()
        )

        req_esc = html.escape(str(req_eq)) if req_eq else escaped_model
        list_esc = html.escape(str(list_eq)) if list_eq else escaped_model

        if eq_differs:
            eq_display_en = f"🚜 <b>Requested:</b> <i>{req_esc}</i>\n🔄 <b>Proposed Alternative:</b> <i>{list_esc}</i>"
            eq_display_am = f"🚜 <b>የተጠየቀው፦</b> <i>{req_esc}</i>\n🔄 <b>የቀረበው አማራጭ፦</b> <i>{list_esc}</i>"
            eq_display_om = f"🚜 <b>Kan gaafatame:</b> <i>{req_esc}</i>\n🔄 <b>Filannoo dhihaate:</b> <i>{list_esc}</i>"
        else:
            eq_display_en = f"🚜 <b>Equipment:</b> <i>{escaped_model}</i>"
            eq_display_am = f"🚜 <b>መሳሪያ፦</b> <i>{escaped_model}</i>"
            eq_display_om = f"🚜 <b>Meeshaa:</b> <i>{escaped_model}</i>"

        formatted_capacity = (
            self._format_capacity_output(raw_cap_val, str(model_name))
            if hasattr(self, "_format_capacity_output")
            else str(raw_cap_val or "")
        )
        escaped_capacity = (
            html.escape(formatted_capacity) if formatted_capacity else None
        )

        cap_line_am = (
            f"⚙️ አቅም፦ <b>{escaped_capacity}</b>\n" if escaped_capacity else ""
        )
        cap_line_om = (
            f"⚙️ Dandeettii: <b>{escaped_capacity}</b>\n"
            if escaped_capacity
            else ""
        )
        cap_line_en = (
            f"⚙️ Capacity: <b>{escaped_capacity}</b>\n"
            if escaped_capacity
            else ""
        )

        use_line_am = (
            f"🛠️ አገልግሎት/ዓይነት፡ <b>{escaped_property_use}</b>\n"
            if escaped_property_use
            else ""
        )
        use_line_om = (
            f"🛠️ Fayyadamina/Gosa: <b>{escaped_property_use}</b>\n"
            if escaped_property_use
            else ""
        )
        use_line_en = (
            f"🛠️ Property Use: <b>{escaped_property_use}</b>\n"
            if escaped_property_use
            else ""
        )

        brand_line_am = (
            f"🏷️ ብራንድ፡ <b>{escaped_brand}</b>\n" if escaped_brand else ""
        )
        brand_line_om = (
            f"🏷️ Brandii: <b>{escaped_brand}</b>\n" if escaped_brand else ""
        )
        brand_line_en = (
            f"🏷️ Brand: <b>{escaped_brand}</b>\n" if escaped_brand else ""
        )

        prompts = {
            "am": {
                "title": "⚡ <b>ስማርት ማች አስተማማኝ ድርድር</b> ⚡",
                "subtitle": "💬 <b>የመወዳደሪያ ዋጋዎን ያስገቡ</b>",
                "req_loc_label": "📍 <b>የተጠየቀበት ቦታ (ፈላጊ):</b>",
                "prop_loc_label": "📍 <b>የመሳሪያው የሚገኝበት ቦታ (አቅራቢ):</b>",
                "rate_label": "የቀረበ ዋጋ፦" if is_reverse else "የተጠየቀ ዋጋ፦",
                "reply_prompt": f"እባክዎ የመወዳደሪያ ዋጋዎን በ {currency} ያስገቡ (ለምሳሌ <code>5000</code>)፦",
                "eq_display": eq_display_am,
                "use_line": use_line_am,
                "brand_line": brand_line_am,
                "cap_line": cap_line_am,
            },
            "om": {
                "title": "⚡ <b>SMART MATCH SECURE DEAL</b> ⚡",
                "subtitle": "💬 <b>Gatii dhiheessitan galchaa</b>",
                "req_loc_label": "📍 <b>Bakka Barbaadame (Barbaadaa):</b>",
                "prop_loc_label": "📍 <b>Bakka Meeshaan Argamu (Dhiyeessaa):</b>",
                "rate_label": "Gatii dhihaate:",
                "reply_prompt": f"Maaloo gatii dhiheessitan {currency} n galchaa (fakkeenyaaf <code>5000</code>):",
                "eq_display": eq_display_om,
                "use_line": use_line_om,
                "brand_line": brand_line_om,
                "cap_line": cap_line_om,
            },
            "en": {
                "title": "⚡ <b>SMART MATCH SECURE DEAL</b> ⚡",
                "subtitle": "💬 <b>Enter Proposed Counter-Offer Rate</b>" if is_reverse else "💬 <b>Enter Proposed Offer Rate</b>",
                "req_loc_label": "📍 <b>Requested Location (Seeker):</b>",
                "prop_loc_label": "📍 <b>Asset Location (Provider):</b>",
                "rate_label": "Current Proposed Rate:" if is_reverse else "Asking Rate:",
                "reply_prompt": f"Reply with your custom offer in {currency} (e.g., <code>5000</code>):",
                "eq_display": eq_display_en,
                "use_line": use_line_en,
                "brand_line": brand_line_en,
                "cap_line": cap_line_en,
            },
        }

        p = prompts.get(user_lang, prompts["en"])

        prompt_msg = (
            f"{p['title']}\n"
            f"───────────────────────\n"
            f"{p['subtitle']}\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"{p['eq_display']}\n"
            f"{p['use_line']}"
            f"{p['brand_line']}"
            f"{p['cap_line']}"
            f"{p['req_loc_label']} <i>{escaped_req_loc}</i>\n"
            f"{p['prop_loc_label']} <i>{escaped_prop_loc}</i>\n"
            f"💰 <b>{p['rate_label']}</b> <code>{formatted_price}</code>\n\n"
            f"👇 <i>{p['reply_prompt']}</i>"
        )

        chat_id = update.effective_chat.id if update.effective_chat else None
        try:
            if query and query.message and getattr(query.message, "photo", None):
                try:
                    await query.edit_message_caption(
                        caption=prompt_msg, parse_mode="HTML"
                    )
                except Exception:
                    if chat_id:
                        await context.bot.delete_message(
                            chat_id=chat_id, message_id=query.message.message_id
                        )
                        await context.bot.send_message(
                            chat_id=chat_id, text=prompt_msg, parse_mode="HTML"
                        )
            elif query:
                await query.edit_message_text(prompt_msg, parse_mode="HTML")
            elif chat_id:
                await context.bot.send_message(
                    chat_id=chat_id, text=prompt_msg, parse_mode="HTML"
                )
        except Exception:
            if chat_id:
                await context.bot.send_message(
                    chat_id=chat_id, text=prompt_msg, parse_mode="HTML"
                )

        return getattr(self, "ENTERING_OFFER", 1)

    async def receive_custom_offer(
            self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        effective_uid = update.effective_user.id if update.effective_user else 0
        sender_lang = await self._resolve_user_language_db_first(effective_uid, context)

        translations = {
            "en": {
                "err_invalid_price": "⚠️ <b>Invalid Amount Format</b>\n\nPlease enter numbers only without spaces or currency letters (e.g. <code>5000</code>).",
                "sys_error": "⚠️ <b>System Error:</b> Could not record counter-offer. Please try again.",
                "counter_transmitted": "✅ <b>Counter-Offer Transmitted!</b>",
                "incoming_counter": "📩 <b>INCOMING COUNTER-OFFER</b>",
                "deal_header": "⚡ <b>SMART MATCH SECURE DEAL</b> ⚡",
                "asset": "🚜 <b>Asset:</b>",
                "requested": "🚜 <b>Requested:</b>",
                "proposed_alt": "🔄 <b>Proposed Alternative:</b>",
                "capacity": "⚙️ <b>Capacity:</b>",
                "new_proposed_rate": "💰 <b>New Proposed Rate:</b>",
                "proposed_rate": "💵 <b>Proposed Rate:</b>",
                "status_awaiting": "📌 <b>Status:</b> Awaiting Counterparty Review",
                "sender_footer": "<i>Your counter-offer has been recorded and transmitted to the counterparty.</i>",
                "recipient_footer": "⚡ <i>The counterparty has submitted a new counter-offer. Please review and select an option below:</i>",
                "btn_accept": "✅ Accept Offer",
                "btn_counter": "💬 Counter Offer",
                "btn_decline": "❌ Decline Deal",
            },
            "am": {
                "err_invalid_price": "⚠️ <b>ልክ ያልሆነ የምንዛሬ መጠን ቅርጸት</b>\n\nእባክዎ ክፍተት ወይም የገንዘብ ምልክት ሳይጠቀሙ ቁጥሮችን ብቻ ያስገቡ (ለምሳሌ <code>5000</code>)።",
                "sys_error": "⚠️ <b>የስርዓት ስህተት:</b> የመወዳደሪያ ዋጋውን መመዝገብ አልተቻለም። እባክዎ እንደገና ይሞክሩ።",
                "counter_transmitted": "✅ <b>የመወዳደሪያ ዋጋ ተላልፏል!</b>",
                "incoming_counter": "📩 <b>አዲስ የመወዳደሪያ ዋጋ ደርሷል</b>",
                "deal_header": "⚡ <b>ስማርት ማች አስተማማኝ ድርድር</b> ⚡",
                "asset": "🚜 <b>ንብረት:</b>",
                "requested": "🚜 <b>የተጠየቀው:</b>",
                "proposed_alt": "🔄 <b>የቀረበው አማራጭ:</b>",
                "capacity": "⚙️ <b>አቅም:</b>",
                "new_proposed_rate": "💰 <b>አዲስ የቀረበ ዋጋ:</b>",
                "proposed_rate": "💵 <b>የቀረበ ዋጋ:</b>",
                "status_awaiting": "📌 <b>ሁኔታ:</b> የተቃራኒ ወገንን ግምገማ በመጠበቅ ላይ",
                "sender_footer": "<i>የመወዳደሪያ ዋጋዎ ተመዝግቦ ለተቃራኒው ወገን ተላልፏል።</i>",
                "recipient_footer": "⚡ <i>ተቃራኒው ወገን አዲስ የመወዳደሪያ ዋጋ አቅርቧል። እባክዎ ከዚህ በታች ያለውን አማራጭ ይምረጡ:</i>",
                "btn_accept": "✅ ቅናሹን ተቀበል",
                "btn_counter": "💬 ተለዋጭ ዋጋ አቅርብ",
                "btn_decline": "❌ ድርድር ውድቅ አድርግ",
            },
            "om": {
                "err_invalid_price": "⚠️ <b>Hangi Galchitan Sirrii Mit</b>\n\nMaaloo bakka duwwaa ykn mallattoo qarshii malee lakkoofsa qofa galchaa (fakkeenyaaf <code>5000</code>).",
                "sys_error": "⚠️ <b>Dogoggora Sirnaa:</b> Gatiin dhihaate galmaa'uu hin dandeenye. Maaloo irra deebi'aa yaalaa.",
                "counter_transmitted": "✅ <b>Dhiyeessiin Jijjiiraa Dabarsameera!</b>",
                "incoming_counter": "📩 <b>DHIYEESSII JIJJIIRAA HAARAA ARGAMEERA</b>",
                "deal_header": "⚡ <b>SMART MATCH SECURE DEAL</b> ⚡",
                "asset": "🚜 <b>Meeshaa:</b>",
                "requested": "🚜 <b>Kan gaafatame:</b>",
                "proposed_alt": "🔄 <b>Filannoo dhihaate:</b>",
                "capacity": "⚙️ <b>Dandeettii:</b>",
                "new_proposed_rate": "💰 <b>Gatii Haaraa Dhiyaate:</b>",
                "proposed_rate": "💵 <b>Gatii Dhiyaate:</b>",
                "status_awaiting": "📌 <b>Haala:</b> Eegumsa Gamaaggama Qaama Biraatiif",
                "sender_footer": "<i>Gatiin dhiheessitan galmaa'ee qaama biraatiif dabarsameera.</i>",
                "recipient_footer": "⚡ <i>Qaama biraatii dhiyeessiin jijjiiraa haaraan dhufeera. Maaloo ilaaluun filannoo armaan gadii filadhaa:</i>",
                "btn_accept": "✅ Fudhadhu",
                "btn_counter": "💬 Gatii Jijjiiraa",
                "btn_decline": "❌ Diduu",
            }
        }

        def t(key, target_lang=sender_lang):
            return translations.get(target_lang, translations["en"]).get(key, translations["en"].get(key, key))

        if not update.message or not update.message.text:
            return getattr(self, "ENTERING_OFFER", 11)

        clean_text = update.message.text.lower()
        for token in [",", " ", "etb", "usd", "eur", "birr"]:
            clean_text = clean_text.replace(token, "")
        clean_text = clean_text.strip()

        try:
            custom_offer = float(clean_text)
            if custom_offer <= 0:
                raise ValueError("Price must be greater than zero")
        except ValueError:
            err_text = get_text(sender_lang, "err_invalid_price") or t("err_invalid_price", sender_lang)
            await update.message.reply_text(err_text, parse_mode="HTML")
            return getattr(self, "ENTERING_OFFER", 11)

        context.user_data["equipment_price"] = custom_offer

        active_deal_id = str(
            context.user_data.get("active_equipment_deal_id")
            or context.user_data.get("active_deal_id")
            or "pending"
        )

        is_reverse = context.user_data.get("is_reverse_match", False)
        logger.debug(
            f"Processing custom offer {custom_offer} for deal ID '{active_deal_id}' (is_reverse={is_reverse})"
        )

        is_real_deal = False
        deal_data = {}
        if active_deal_id != "pending" and hasattr(self, "db") and self.db:
            try:
                deal_res = (
                    await self.db.table("deals")
                    .select(
                        "seeker_id, provider_id, asset_title, currency, request_id, listing_id"
                    )
                    .eq("id", active_deal_id)
                    .maybe_single()
                    .execute()
                )
                if deal_res and hasattr(deal_res, "data") and deal_res.data:
                    deal_data = deal_res.data
                    is_real_deal = True
            except Exception as e:
                logger.warning(f"Error checking deal existence in DB: {e}")

        if is_real_deal:
            try:
                seeker_id = safe_int(
                    deal_data.get("seeker_id")
                    or context.user_data.get("seeker_id", 0)
                )
                provider_id = safe_int(
                    deal_data.get("provider_id")
                    or context.user_data.get("provider_id", 0)
                )
                raw_asset_title = deal_data.get(
                    "asset_title"
                ) or context.user_data.get("asset_title", "Equipment Asset")

                # Dynamic Currency Resolution Hierarchy (Deal > Context > Fallback)
                currency = str(
                    deal_data.get("currency")
                    or context.user_data.get("currency")
                    or context.user_data.get("equipment_currency")
                    or "ETB"
                ).strip().upper()
                context.user_data["currency"] = currency

                # Persist updated price and currency in DB
                await (
                    self.db.table("deals")
                    .update(
                        {
                            "current_price": custom_offer,
                            "currency": currency,
                            "status": "negotiating",
                            "vertical_type": "equipment",
                        }
                    )
                    .eq("id", active_deal_id)
                    .execute()
                )

                formatted_price = f"{custom_offer:,.2f} {currency}"
                sender_id = effective_uid

                if sender_id == seeker_id:
                    recipient_id = provider_id
                elif sender_id == provider_id:
                    recipient_id = seeker_id
                else:
                    recipient_id = (
                        provider_id
                        if provider_id and provider_id != sender_id
                        else seeker_id
                    )

                recipient_lang = await self._resolve_user_language_db_first(recipient_id, context)

                req_eq = context.user_data.get("request_equipment_type")
                list_eq = context.user_data.get("listing_equipment_type")

                req_cap_raw = context.user_data.get("request_capacity")
                list_cap_raw = context.user_data.get("listing_capacity")

                try:
                    if deal_data.get("request_id") and (
                            not req_eq or not req_cap_raw
                    ):
                        r_res = (
                            await self.db.table("seeker_requests")
                            .select(
                                "equipment_type, property_use, capacity_output, currency"
                            )
                            .eq("id", deal_data["request_id"])
                            .maybe_single()
                            .execute()
                        )
                        if r_res and r_res.data:
                            if r_res.data.get("currency") and not deal_data.get("currency"):
                                currency = str(r_res.data["currency"]).strip().upper()
                                context.user_data["currency"] = currency
                            req_eq = (
                                    req_eq
                                    or r_res.data.get("equipment_type")
                                    or r_res.data.get("property_use")
                            )
                            req_cap_raw = req_cap_raw or r_res.data.get(
                                "capacity_output"
                            )

                    if deal_data.get("listing_id") and (
                            not list_eq or not list_cap_raw
                    ):
                        l_res = (
                            await self.db.table("provider_listings")
                            .select(
                                "equipment_type, property_use, capacity_output, currency"
                            )
                            .eq("id", deal_data["listing_id"])
                            .maybe_single()
                            .execute()
                        )
                        if l_res and l_res.data:
                            if l_res.data.get("currency") and not deal_data.get("currency"):
                                currency = str(l_res.data["currency"]).strip().upper()
                                context.user_data["currency"] = currency
                            list_eq = (
                                    list_eq
                                    or l_res.data.get("equipment_type")
                                    or l_res.data.get("property_use")
                            )
                            list_cap_raw = list_cap_raw or l_res.data.get(
                                "capacity_output"
                            )
                except Exception as e:
                    logger.warning(
                        f"Error fetching equipment metadata for cards: {e}"
                    )

                formatted_req_cap = (
                    self._format_capacity_output(
                        req_cap_raw, str(req_eq or raw_asset_title)
                    )
                    if hasattr(self, "_format_capacity_output")
                    else str(req_cap_raw or "")
                )
                formatted_list_cap = (
                    self._format_capacity_output(
                        list_cap_raw, str(list_eq or raw_asset_title)
                    )
                    if hasattr(self, "_format_capacity_output")
                    else str(list_cap_raw or "")
                )

                cap_display_text = (
                        formatted_list_cap
                        or formatted_req_cap
                        or (
                            self._format_capacity_output(
                                context.user_data.get("capacity_output"),
                                str(raw_asset_title),
                            )
                            if hasattr(self, "_format_capacity_output")
                            else ""
                        )
                )

                # Sender Confirmation Card (Uses Sender Language)
                cap_display_sender = (
                    f"{t('capacity', sender_lang)} <code>{html.escape(cap_display_text)}</code>\n"
                    if cap_display_text
                    else ""
                )

                req_esc = html.escape(str(req_eq)) if req_eq else html.escape(str(raw_asset_title))
                list_esc = html.escape(str(list_eq)) if list_eq else html.escape(str(raw_asset_title))

                eq_differs = bool(
                    req_eq
                    and list_eq
                    and str(req_eq).strip().lower() != str(list_eq).strip().lower()
                )

                if eq_differs:
                    asset_display_sender = f"{t('requested', sender_lang)} <i>{req_esc}</i>\n{t('proposed_alt', sender_lang)} <i>{list_esc}</i>"
                else:
                    asset_display_sender = (
                        f"{t('asset', sender_lang)} <i>{html.escape(str(raw_asset_title))}</i>"
                    )

                confirm_card = (
                    f"{t('deal_header', sender_lang)}\n"
                    f"───────────────────────\n"
                    f"{t('counter_transmitted', sender_lang)}\n"
                    f"━━━━━━━━━━━━━━━━━━━\n"
                    f"{asset_display_sender}\n"
                    f"{cap_display_sender}"
                    f"{t('new_proposed_rate', sender_lang)} <code>{formatted_price}</code>\n"
                    f"{t('status_awaiting', sender_lang)}\n\n"
                    f"<i>{t('sender_footer', sender_lang)}</i>"
                )
                await update.message.reply_text(confirm_card, parse_mode="HTML")

                if recipient_id and recipient_id > 0:
                    # Recipient Notification Card (Uses Recipient Language)
                    cap_display_recipient = (
                        f"{t('capacity', recipient_lang)} <code>{html.escape(cap_display_text)}</code>\n"
                        if cap_display_text
                        else ""
                    )

                    if eq_differs:
                        asset_display_recipient = f"{t('requested', recipient_lang)} <i>{req_esc}</i>\n{t('proposed_alt', recipient_lang)} <i>{list_esc}</i>"
                    else:
                        asset_display_recipient = (
                            f"{t('asset', recipient_lang)} <i>{html.escape(str(raw_asset_title))}</i>"
                        )

                    recipient_notify_card = (
                        f"{t('deal_header', recipient_lang)}\n"
                        f"───────────────────────\n"
                        f"{t('incoming_counter', recipient_lang)}\n"
                        f"━━━━━━━━━━━━━━━━━━━\n"
                        f"{asset_display_recipient}\n"
                        f"{cap_display_recipient}"
                        f"{t('proposed_rate', recipient_lang)} <code>{formatted_price}</code>\n\n"
                        f"⚡ <i>{t('recipient_footer', recipient_lang)}</i>"
                    )
                    action_keyboard = [
                        [
                            InlineKeyboardButton(
                                t("btn_accept", recipient_lang),
                                callback_data=f"equip_deal_accept:{active_deal_id}",
                            ),
                            InlineKeyboardButton(
                                t("btn_counter", recipient_lang),
                                callback_data=f"equip_deal_counter:{active_deal_id}",
                            ),
                        ],
                        [
                            InlineKeyboardButton(
                                t("btn_decline", recipient_lang),
                                callback_data=f"equip_deal_decline:{active_deal_id}",
                            )
                        ],
                    ]
                    reply_markup = InlineKeyboardMarkup(action_keyboard)
                    try:
                        await context.bot.send_message(
                            chat_id=recipient_id,
                            text=recipient_notify_card,
                            parse_mode="HTML",
                            reply_markup=reply_markup,
                        )
                    except Exception as notify_err:
                        logger.error(
                            f"Failed to dispatch counter-offer notification to user {recipient_id}: {notify_err}"
                        )
                else:
                    logger.error(
                        f"Invalid recipient_id ({recipient_id}) for deal {active_deal_id}."
                    )

                context.user_data.pop("active_equipment_deal_id", None)
                context.user_data.pop("active_deal_id", None)
                return await self.route_to_main_menu(update, context)

            except Exception as e:
                logger.error(
                    f"Error updating counter-offer price in DB: {e}",
                    exc_info=True,
                )
                await update.message.reply_text(
                    t("sys_error", sender_lang),
                    parse_mode="HTML",
                )
                return await self.route_to_main_menu(update, context)

        target_id = (
            active_deal_id
            if active_deal_id != "pending"
            else str(
                context.user_data.get("target_listing_id")
                or context.user_data.get("selected_listing_id")
                or "pending"
            )
        )

        return await self._process_deal_lock(
            update, context, target_id, custom_offer
        )

    # ==========================================
    # 5. DEAL LOCK EXECUTION & TRANSMISSION
    # ==========================================
    async def execute_deal_lock(
            self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        query = update.callback_query
        if query:
            await query.answer()

        match_id = (
            query.data.split(":")[1]
            if query and ":" in query.data
            else context.user_data.get("active_equipment_deal_id")
        )

        is_reverse = context.user_data.get("is_reverse_match", False)

        if is_reverse:
            agreed_price = safe_float(
                context.user_data.get("listing_asking_rate")
                or context.user_data.get("equipment_price", 0.0)
            )
        else:
            agreed_price = safe_float(
                context.user_data.get("equipment_price", 0.0)
            )

        return await self._process_deal_lock(
            update, context, str(match_id), agreed_price
        )

    async def _process_deal_lock(
            self,
            update: Update,
            context: ContextTypes.DEFAULT_TYPE,
            target_id: str,
            agreed_price: float,
    ) -> int:
        callback_query = update.callback_query
        is_reverse = context.user_data.get("is_reverse_match", False)
        chat_id = update.effective_chat.id if update.effective_chat else None

        sender_lang = context.user_data.get(
            "lang", context.user_data.get("language", "en")
        )
        if sender_lang not in ["en", "am", "om"]:
            sender_lang = "en"

        translations = {
            "en": {
                "deal_header": "⚡ <b>SMART MATCH SECURE DEAL</b> ⚡",
                "transmitting": "⚡ <b>SMART MATCH SECURE DEAL</b> ⚡\n───────────────────────\n🔄 <i>Transmitting offer to counterparty...</i>",
                "err_missing_seeker": "⚠️ <b>System Error:</b> Missing Seeker or Listing reference for reverse match.",
                "err_missing_provider": "⚠️ <b>System Error:</b> Could not verify equipment provider ID.",
                "deal_blocked": "⚡ <b>SMART MATCH SECURE DEAL</b> ⚡\n───────────────────────\n🚫 <b>Negotiation Blocked</b>\n\nThis asset is currently tied up in an active negotiation.",
                "system_error": "⚡ <b>SMART MATCH SECURE DEAL</b> ⚡\n───────────────────────\n⚠️ <b>System Error:</b> Could not complete offer submission.",
                "provider_offer_transmitted": "✅ <b>Provider Offer Transmitted!</b>",
                "offer_transmitted": "✅ <b>Offer Transmitted!</b>",
                "new_offer_received": "📩 <b>NEW OFFER RECEIVED</b>",
                "new_offer_received_alt": "📩 <b>NEW OFFER RECEIVED (ALTERNATIVE MATCH)</b>",
                "counter_offer_received": "📩 <b>NEW COUNTER-OFFER RECEIVED</b>",
                "transaction": "🏷️ <b>Transaction:</b>",
                "asset": "🚜 <b>Asset:</b>",
                "asset_request": "🚜 <b>Asset Request:</b>",
                "proposed_alternative": "🔄 <b>Proposed Alternative:</b>",
                "selected_equipment": "🔄 <b>Selected Equipment:</b>",
                "equipment": "🚜 <b>Equipment:</b>",
                "seeker_location": "📍 <b>Requested Location (Seeker):</b>",
                "provider_location": "📍 <b>Asset Location (Provider):</b>",
                "capacity": "⚙️ <b>Capacity:</b>",
                "offered_rate": "💰 <b>Offered Rate:</b>",
                "proposed_rate": "💵 <b>Proposed Rate:</b>",
                "current_proposed_rate": "💰 <b>Current Proposed Rate:</b>",
                "status_awaiting_seeker": "📌 <b>Status:</b> Awaiting Seeker Action",
                "status_awaiting_provider": "📌 <b>Status:</b> Awaiting Provider Review",
                "sender_footer": "<i>Your offer has been delivered to the seeker. You will be notified immediately when they respond.</i>",
                "sender_footer_provider": "<i>Your offer has been securely delivered to the owner. You will be notified immediately when they respond.</i>",
                "recipient_footer_seeker": "⚡ <i>Please review and select an option below:</i>",
                "recipient_footer_provider": "⚡ <i>A seeker has submitted an offer for your equipment listing. Please select an option below:</i>",
                "counter_notice": "⚡ <i>The counterparty has submitted a new counter-offer. Please select an option below:</i>",
                "btn_accept": "✅ Accept Offer",
                "btn_counter": "💬 Counter Offer",
                "btn_decline": "❌ Decline Deal",
            },
            "am": {
                "deal_header": "⚡ <b>ስማርት ማች አስተማማኝ ድርድር</b> ⚡",
                "transmitting": "⚡ <b>ስማርት ማች አስተማማኝ ድርድር</b> ⚡\n───────────────────────\n🔄 <i>ጥያቄው ወደ ተቀባዩ ወገን እየተላለፈ ነው...</i>",
                "err_missing_seeker": "⚠️ <b>የስርዓት ስህተት:</b> ለተገላቢጦሽ ግጥሚያ የፈላጊ ወይም የዝርዝር ማጣቀሻ አልተገኘም።",
                "err_missing_provider": "⚠️ <b>የስርዓት ስህተት:</b> የመሳሪያ አቅራቢ መለያውን ማረጋገጥ አልተቻለም።",
                "deal_blocked": "⚡ <b>ስማርት ማች አስተማማኝ ድርድር</b> ⚡\n───────────────────────\n🚫 <b>ድርድር ታግዷል</b>\n\nይህ ንብረት በአሁኑ ጊዜ በሌላ ንቁ ድርድር ላይ ይገኛል።",
                "system_error": "⚡ <b>ስማርት ማች አስተማማኝ ድርድር</b> ⚡\n───────────────────────\n⚠️ <b>የስርዓት ስህተት:</b> የቅናሽ ማቅረቢያውን ማጠናቀቅ አልተቻለም።",
                "provider_offer_transmitted": "✅ <b>የአቅራቢው ቅናሽ ተላልፏል!</b>",
                "offer_transmitted": "✅ <b>ቅናሽ ተላልፏል!</b>",
                "new_offer_received": "📩 <b>አዲስ ቅናሽ ደርሷል</b>",
                "new_offer_received_alt": "📩 <b>አዲስ ቅናሽ ደርሷል (ተለዋጭ ግጥሚያ)</b>",
                "counter_offer_received": "📩 <b>አዲስ የመወዳደሪያ ዋጋ ደርሷል</b>",
                "transaction": "🏷️ <b>ግብይት:</b>",
                "asset": "🚜 <b>ንብረት:</b>",
                "asset_request": "🚜 <b>የንብረት ጥያቄ:</b>",
                "proposed_alternative": "🔄 <b>የቀረበ ተለዋጭ:</b>",
                "selected_equipment": "🔄 <b>የተመረጠ መሳሪያ:</b>",
                "equipment": "🚜 <b>መሳሪያ:</b>",
                "seeker_location": "📍 <b>የተጠየቀበት ቦታ (ፈላጊ):</b>",
                "provider_location": "📍 <b>የመሳሪያው የሚገኝበት ቦታ (አቅራቢ):</b>",
                "capacity": "⚙️ <b>አቅም:</b>",
                "offered_rate": "💰 <b>የቀረበ ዋጋ:</b>",
                "proposed_rate": "💵 <b>የቀረበ ዋጋ:</b>",
                "current_proposed_rate": "💰 <b>የአሁን የቀረበ ዋጋ:</b>",
                "status_awaiting_seeker": "📌 <b>ሁኔታ:</b> የፈላጊውን ምላሽ በመጠበቅ ላይ",
                "status_awaiting_provider": "📌 <b>ሁኔታ:</b> የአቅራቢውን ግምገማ በመጠበቅ ላይ",
                "sender_footer": "<i>ቅናሽዎ ለፈላጊው ደርሷል። ምላሽ ሲሰጡ ወዲያውኑ ይነገሮዎታል።</i>",
                "sender_footer_provider": "<i>ቅናሽዎ ደህንነቱ በተጠበቀ ሁኔታ ለባለቤቱ ደርሷል። ምላሽ ሲሰጡ ወዲያውኑ ይነገሮዎታል።</i>",
                "recipient_footer_seeker": "⚡ <i>እባክዎ ከዚህ በታች ያለውን አማራጭ ይምረጡ:</i>",
                "recipient_footer_provider": "⚡ <i>አንድ ፈላጊ በመሳሪያዎ ዝርዝር ላይ ቅናሽ አቅርቧል። እባክዎ ከዚህ በታች ያለውን አማራጭ ይምረጡ:</i>",
                "counter_notice": "⚡ <i>ተቃራኒው ወገን አዲስ የመወዳደሪያ ዋጋ አቅርቧል። እባክዎ ከዚህ በታች ያለውን አማራጭ ይምረጡ:</i>",
                "btn_accept": "✅ ቅናሹን ተቀበል",
                "btn_counter": "💬 ተለዋጭ ዋጋ አቅርብ",
                "btn_decline": "❌ ድርድር ውድቅ አድርግ",
            },
            "om": {
                "deal_header": "⚡ <b>SMART MATCH SECURE DEAL</b> ⚡",
                "transmitting": "⚡ <b>SMART MATCH SECURE DEAL</b> ⚡\n───────────────────────\n🔄 <i>Dhiyeessiin gara qaama biraatti dabarsamaa jira...</i>",
                "err_missing_seeker": "⚠️ <b>Dogoggora Sirnaa:</b> Walitti dhufeenya jijjiirraaf ragaan barbaadaa ykn tarreeffamaa dhabameera.",
                "err_missing_provider": "⚠️ <b>Dogoggora Sirnaa:</b> Eenyummaan dhiyeessaa meeshichaa mirkaneessuu hin danda'amre.",
                "deal_blocked": "⚡ <b>SMART MATCH SECURE DEAL</b> ⚡\n───────────────────────\n🚫 <b>Daldalli Cufameera</b>\n\nMeeshaan kun amma daldala hojii irra jiru jalatti argama.",
                "system_error": "⚡ <b>SMART MATCH SECURE DEAL</b> ⚡\n───────────────────────\n⚠️ <b>Dogoggora Sirnaa:</b> Dhiyeessiicha xumuruu hin danda'amne.",
                "provider_offer_transmitted": "✅ <b>Dhiyeessiin Dhiyeessaa Dabarsameera!</b>",
                "offer_transmitted": "✅ <b>Dhiyeessiin Dabarsameera!</b>",
                "new_offer_received": "📩 <b>DHIYEESSII HAARAAN ARGAMEERA</b>",
                "new_offer_received_alt": "📩 <b>DHIYEESSII HAARAAN ARGAMEERA (WALAKKAAN WAL FAKKAATU)</b>",
                "counter_offer_received": "📩 <b>GATII DORGOMMII HAARAAN ARGAMEERA</b>",
                "transaction": "🏷️ <b>Gosa Daldalaa:</b>",
                "asset": "🚜 <b>Meeshaa:</b>",
                "asset_request": "🚜 <b>Gaaffii Meeshaa:</b>",
                "proposed_alternative": "🔄 <b>Filannoo Dhiyaate:</b>",
                "selected_equipment": "🔄 <b>Meeshaa Filatame:</b>",
                "equipment": "🚜 <b>Meeshaa:</b>",
                "seeker_location": "📍 <b>Bakka Barbaadame (Barbaadaa):</b>",
                "provider_location": "📍 <b>Bakka Meeshaan Argamu (Dhiyeessaa):</b>",
                "capacity": "⚙️ <b>Dandeettii:</b>",
                "offered_rate": "💰 <b>Gatii Dhiyaate:</b>",
                "proposed_rate": "💵 <b>Gatii Dhiyaate:</b>",
                "current_proposed_rate": "💰 <b>Gatii Ammaa Dhiyaate:</b>",
                "status_awaiting_seeker": "📌 <b>Haala:</b> Eegumsa Barbaadaa",
                "status_awaiting_provider": "📌 <b>Haala:</b> Eegumsa Gamaaggama Dhiyeessaa",
                "sender_footer": "<i>Dhiyeessiin keessan barbaadaaf qaqqabeera. Yeroo deebii kennan battalumatti beeksifamtu.</i>",
                "sender_footer_provider": "<i>Dhiyeessiin keessanmilkaa'inaan abbaa qabeenyaaf qaqqabeera. Yeroo deebii kennan battalumatti beeksifamtu.</i>",
                "recipient_footer_seeker": "⚡ <i>Maaloo ilaaluun filannoo armaan gadii irraa filadhaa:</i>",
                "recipient_footer_provider": "⚡ <i>Barbaadaan tokko tarreeffama meeshaa keessaniif dhiyeessi dhiyeessuseera. Maaloo filannoo armaan gadii filadhaa:</i>",
                "counter_notice": "⚡ <i>Qaamni biraa gatii dorgommii haaraa dhiyeesseera. Maaloo filannoo armaan gadii filadhaa:</i>",
                "btn_accept": "✅ Fudhadhu",
                "btn_counter": "💬 Gatii Jijjiiraa",
                "btn_decline": "❌ Diduu",
            }
        }

        def get_t(lang_code):
            return translations.get(lang_code, translations["en"])

        def t(lng_key, target_lang=sender_lang):
            return get_t(target_lang).get(lng_key, translations["en"].get(lng_key, lng_key))

        async def fetch_user_language(uid: int) -> str:
            if not uid or not hasattr(self, "db") or not self.db:
                return "en"
            try:
                user_res = (
                    await self.db.table("users")
                    .select("preferred_language, language_code, language")
                    .eq("telegram_id", int(uid))
                    .maybe_single()
                    .execute()
                )
                if user_res and hasattr(user_res, "data") and user_res.data:
                    udata = user_res.data
                    pref = (
                            udata.get("preferred_language")
                            or udata.get("language_code")
                            or udata.get("language")
                            or "en"
                    ).strip().lower()
                    if pref in ["en", "am", "om"]:
                        return pref
            except Exception as u_err:
                logger.warning(f"Could not fetch user preferred language for {uid}: {u_err}")
            return "en"

        def localize_transaction_type(tx_val: str, target_lang: str) -> str:
            if not tx_val:
                return "Rental"
            tx_mapping = {
                "rental": {
                    "en": "Rental",
                    "am": "ኪራይ",
                    "om": "Kiraa"
                },
                "sale": {
                    "en": "Sale",
                    "am": "ሽያጭ",
                    "om": "Gurgurtaa"
                }
            }
            v_lower = tx_val.strip().lower()
            if v_lower in tx_mapping:
                return tx_mapping[v_lower].get(target_lang, tx_mapping[v_lower]["en"])
            return tx_val.title()

        def _clean_loc(plhd_val):
            if not plhd_val:
                return ""
            s = str(plhd_val).strip()
            if s.lower() in ["n/a", "none", "not specified", "null", "undefined", "-"]:
                return ""
            return s

        currency = str(
            context.user_data.get("currency")
            or context.user_data.get("equipment_currency")
            or "ETB"
        ).strip().upper()

        async def send_response(text: str):
            if callback_query:
                try:
                    await callback_query.edit_message_text(
                        text=text, parse_mode="HTML"
                    )
                except Exception:
                    try:
                        await callback_query.edit_message_caption(
                            caption=text, parse_mode="HTML"
                        )
                    except Exception:
                        if chat_id:
                            await context.bot.send_message(
                                chat_id=chat_id, text=text, parse_mode="HTML"
                            )
            elif chat_id:
                await context.bot.send_message(
                    chat_id=chat_id, text=text, parse_mode="HTML"
                )

        user_id = update.effective_user.id if update.effective_user else 0

        if is_reverse:
            provider_id = user_id
            seeker_id = safe_int(context.user_data.get("target_seeker_id"))
            listing_id = context.user_data.get("last_created_listing_id")

            if not seeker_id or not listing_id:
                await send_response(t("err_missing_seeker", sender_lang))
                return await self.route_to_main_menu(update, context)
        else:
            seeker_id = user_id
            provider_id = safe_int(context.user_data.get("provider_id"))
            listing_id = target_id

            if not provider_id:
                await send_response(t("err_missing_provider", sender_lang))
                return await self.route_to_main_menu(update, context)

        await send_response(t("transmitting", sender_lang))

        try:
            deals_module = getattr(self, "deals", self)

            fetched_prop_eq = None
            fetched_prop_loc = None
            fetched_tx_type = None
            fetched_prop_cap = None
            fetched_ldata = None

            if hasattr(self, "db") and self.db and listing_id:
                try:
                    listing_res = (
                        await self.db.table("provider_listings")
                        .select(
                            "equipment_type, property_use, brand, location_text, operating_region, destination_text, transaction_type, capacity_output, currency, photo_url, contact_phone, status, latitude, longitude"
                        )
                        .eq("id", str(listing_id))
                        .maybe_single()
                        .execute()
                    )
                    if listing_res and hasattr(listing_res, "data") and listing_res.data:
                        ldata = listing_res.data
                        fetched_ldata = ldata
                        fetched_prop_eq = (
                                ldata.get("equipment_type")
                                or ldata.get("property_use")
                                or ldata.get("brand")
                        )
                        fetched_prop_loc = (
                                _clean_loc(ldata.get("location_text"))
                                or _clean_loc(ldata.get("operating_region"))
                                or _clean_loc(ldata.get("destination_text"))
                        )
                        fetched_tx_type = ldata.get("transaction_type")
                        fetched_prop_cap = ldata.get("capacity_output")
                        if ldata.get("currency"):
                            currency = str(ldata["currency"]).strip().upper()
                            context.user_data["currency"] = currency
                except Exception as db_err:
                    logger.warning(f"Could not fetch listing details from DB: {db_err}")

            request_id = (
                    context.user_data.get("request_id")
                    or context.user_data.get("seeker_request_id")
                    or context.user_data.get("active_request_id")
            )
            fetched_req_eq = None
            fetched_req_loc = None
            fetched_req_cap = None
            fetched_rdata = None

            if hasattr(self, "db") and self.db:
                try:
                    req_res = None
                    if request_id:
                        req_res = (
                            await self.db.table("seeker_requests")
                            .select(
                                "equipment_type, property_use, location_text, operating_region, destination_text, capacity_output, currency, contact_phone, latitude, longitude"
                            )
                            .eq("id", str(request_id))
                            .maybe_single()
                            .execute()
                        )

                    if (not req_res or not getattr(req_res, "data", None)) and seeker_id:
                        req_res = (
                            await self.db.table("seeker_requests")
                            .select(
                                "equipment_type, property_use, location_text, operating_region, destination_text, capacity_output, currency, contact_phone, latitude, longitude"
                            )
                            .eq("seeker_id", int(seeker_id))
                            .order("created_at", desc=True)
                            .limit(1)
                            .execute()
                        )
                        if req_res and getattr(req_res, "data", None) and isinstance(req_res.data, list) and len(
                                req_res.data) > 0:
                            req_res.data = req_res.data[0]

                    if req_res and hasattr(req_res, "data") and req_res.data:
                        rdata = req_res.data
                        fetched_rdata = rdata
                        fetched_req_eq = rdata.get("equipment_type") or rdata.get("property_use")
                        fetched_req_loc = (
                                _clean_loc(rdata.get("location_text"))
                                or _clean_loc(rdata.get("operating_region"))
                                or _clean_loc(rdata.get("destination_text"))
                        )
                        fetched_req_cap = rdata.get("capacity_output")
                        if rdata.get("currency"):
                            currency = str(rdata["currency"]).strip().upper()
                            context.user_data["currency"] = currency
                except Exception as db_err:
                    logger.warning(f"Could not fetch seeker request details from DB: {db_err}")

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
                                        parsed_json.get("url")
                                        or parsed_json.get("file_id")
                                        or parsed_json.get("path")
                                )
                        except Exception:
                            pass
                    return cleaned if cleaned else None
                if isinstance(item, dict):
                    return (
                            item.get("url")
                            or item.get("file_id")
                            or item.get("path")
                            or item.get("image_url")
                    )
                return None

            sources_to_check = []
            if fetched_ldata:
                sources_to_check.append(fetched_ldata)
            if fetched_rdata:
                sources_to_check.append(fetched_rdata)
            sources_to_check.append(context.user_data)

            for source in sources_to_check:
                for key in ["photo_url", "image_url", "photo_file_id", "images", "photos", "asset_photo_url"]:
                    val = source.get(key)
                    if val:
                        photo_ref = (
                            _extract_url_or_id(val[0])
                            if isinstance(val, list) and len(val) > 0
                            else _extract_url_or_id(val)
                        )
                        if photo_ref:
                            break
                if photo_ref:
                    break

            presigned_url = None
            if photo_ref:
                img_service: Any = getattr(
                    self, "image_service", getattr(self, "storage", None)
                )
                if img_service and hasattr(img_service, "get_presigned_url_async"):
                    try:
                        presigned_url = await img_service.get_presigned_url_async(photo_ref)
                    except Exception as sign_err:
                        logger.warning(f"Failed to generate pre-signed URL for '{photo_ref}': {sign_err}")

            prop_eq = (
                    fetched_prop_eq
                    or context.user_data.get("provider_equipment_type")
                    or context.user_data.get("asset_title")
                    or "Equipment"
            )

            prop_loc = (
                    fetched_prop_loc
                    or _clean_loc(fetched_ldata.get("location_text") if fetched_ldata else "")
                    or _clean_loc(fetched_ldata.get("operating_region") if fetched_ldata else "")
                    or _clean_loc(fetched_ldata.get("destination_text") if fetched_ldata else "")
                    or _clean_loc(context.user_data.get("provider_location"))
                    or _clean_loc(context.user_data.get("listing_location"))
                    or _clean_loc(context.user_data.get("location_text"))
                    or "N/A"
            )

            req_eq = (
                    fetched_req_eq
                    or context.user_data.get("seeker_equipment_type")
                    or context.user_data.get("equipment_type")
                    or prop_eq
            )

            req_loc = (
                    fetched_req_loc
                    or _clean_loc(fetched_rdata.get("location_text") if fetched_rdata else "")
                    or _clean_loc(fetched_rdata.get("operating_region") if fetched_rdata else "")
                    or _clean_loc(fetched_rdata.get("destination_text") if fetched_rdata else "")
                    or _clean_loc(context.user_data.get("seeker_location"))
                    or _clean_loc(context.user_data.get("request_location"))
                    or _clean_loc(context.user_data.get("location_text"))
                    or _clean_loc(context.user_data.get("origin"))
                    or _clean_loc(context.user_data.get("location"))
                    or "N/A"
            )

            raw_transaction_type = (
                    fetched_tx_type
                    or context.user_data.get("transaction_type")
                    or "Rental"
            )

            # FIX: Suppress internal notification dispatch in initiate_negotiation by setting notify=False and omitting bot
            if hasattr(deals_module, "initiate_negotiation"):
                try:
                    deal_id_str = await deals_module.initiate_negotiation(
                        seeker_id=safe_int(seeker_id),
                        provider_id=safe_int(provider_id),
                        listing_id=listing_id,
                        vertical_type="equipment",
                        initial_offer=agreed_price,
                        asset_title=prop_eq,
                        is_reverse=is_reverse,
                        notify=False,
                    )
                except TypeError:
                    deal_id_str = await deals_module.initiate_negotiation(
                        seeker_id=safe_int(seeker_id),
                        provider_id=safe_int(provider_id),
                        listing_id=listing_id,
                        vertical_type="equipment",
                        initial_offer=agreed_price,
                        asset_title=prop_eq,
                        is_reverse=is_reverse,
                    )
            else:
                deal_id_str = None

            if deal_id_str:
                if hasattr(self, "db") and self.db:
                    try:
                        update_payload = {
                            "currency": currency,
                            "origin": req_loc,
                            "destination": prop_loc,
                        }
                        if request_id:
                            update_payload["request_id"] = request_id

                        await (
                            self.db.table("deals")
                            .update(update_payload)
                            .eq("id", deal_id_str)
                            .execute()
                        )
                    except Exception as deal_update_err:
                        logger.warning(f"Could not update currency/location on deal creation: {deal_update_err}")

                formatted_price = f"{agreed_price:,.2f} {currency}"

                prop_cap_formatted = (
                    self._format_capacity_output(
                        fetched_prop_cap
                        or context.user_data.get("listing_capacity")
                        or context.user_data.get("capacity_output"),
                        str(prop_eq),
                    )
                    if hasattr(self, "_format_capacity_output")
                    else str(fetched_prop_cap or "")
                )
                req_cap_formatted = (
                    self._format_capacity_output(
                        fetched_req_cap
                        or context.user_data.get("request_capacity"),
                        str(req_eq),
                    )
                    if hasattr(self, "_format_capacity_output")
                    else str(fetched_req_cap or "")
                )

                display_cap = prop_cap_formatted or req_cap_formatted

                has_eq_diff = bool(
                    req_eq
                    and prop_eq
                    and req_eq.strip().lower() != prop_eq.strip().lower()
                )

                recipient_id = safe_int(seeker_id if is_reverse else provider_id)
                recipient_lang = await fetch_user_language(recipient_id)

                def get_sender_str(sen_key):
                    return t(sen_key, sender_lang)

                def get_recipient_str(rec_key):
                    return t(rec_key, recipient_lang)

                sender_tx_type = localize_transaction_type(str(raw_transaction_type), sender_lang)
                recipient_tx_type = localize_transaction_type(str(raw_transaction_type), recipient_lang)

                sender_cap_display = (
                    f"{get_sender_str('capacity')} <i>{html.escape(display_cap)}</i>\n"
                    if display_cap
                    else ""
                )
                recipient_cap_display = (
                    f"{get_recipient_str('capacity')} <i>{html.escape(display_cap)}</i>\n"
                    if display_cap
                    else ""
                )

                seeker_loc_sender = f"{get_sender_str('seeker_location')} <i>{html.escape(req_loc)}</i>"
                provider_loc_sender = f"{get_sender_str('provider_location')} <i>{html.escape(prop_loc)}</i>"

                seeker_loc_recipient = f"{get_recipient_str('seeker_location')} <i>{html.escape(req_loc)}</i>"
                provider_loc_recipient = f"{get_recipient_str('provider_location')} <i>{html.escape(prop_loc)}</i>"

                # Multilingual Subtitle Dictionary for Reverse Match Counter-Offers
                reverse_offer_descriptions = {
                    "en": "ℹ️ <i>The provider offered a price on your equipment request.</i>\n",
                    "am": "ℹ️ <i>አቅራቢው በጠየቁት የመሳሪያ ጥያቄ ላይ የዋጋ እቅድ አቅርቧል።</i>\n",
                    "om": "ℹ️ <i>Dhiyeessaan gaaffii meeshaa keessan irratti gatii dhiheesseera.</i>\n"
                }

                if is_reverse:
                    # Resolve recipient language or string helper to fetch the description line
                    reverse_desc_line = reverse_offer_descriptions.get(
                        recipient_lang, reverse_offer_descriptions["en"]
                    )

                    sender_confirm_card = (
                        f"{get_sender_str('deal_header')}\n"
                        f"───────────────────────\n"
                        f"{get_sender_str('provider_offer_transmitted')}\n"
                        f"━━━━━━━━━━━━━━━━━━━\n"
                        f"{get_sender_str('transaction')} <i>{html.escape(sender_tx_type)}</i>\n"
                        f"{get_sender_str('asset')} <i>{html.escape(prop_eq)}</i>\n"
                        f"{sender_cap_display}"
                        f"{seeker_loc_sender}\n"
                        f"{provider_loc_sender}\n"
                        f"{get_sender_str('offered_rate')} <code>{formatted_price}</code>\n"
                        f"{get_sender_str('status_awaiting_seeker')}\n\n"
                        f"<i>{get_sender_str('sender_footer')}</i>"
                    )

                    asset_display_recipient = (
                        f"{get_recipient_str('asset_request')} <i>{html.escape(req_eq)}</i>\n{get_recipient_str('proposed_alternative')} <i>{html.escape(prop_eq)}</i>"
                        if has_eq_diff
                        else f"{get_recipient_str('asset_request')} <i>{html.escape(req_eq)}</i>"
                    )

                    recipient_notify_card = (
                        f"{get_recipient_str('deal_header')}\n"
                        f"───────────────────────\n"
                        f"{get_recipient_str('counter_offer_received')}\n"
                        f"{reverse_desc_line}"
                        f"━━━━━━━━━━━━━━━━━━━\n"
                        f"{get_recipient_str('transaction')} <i>{html.escape(recipient_tx_type)}</i>\n"
                        f"{asset_display_recipient}\n"
                        f"{recipient_cap_display}\n"
                        f"{seeker_loc_recipient}\n"
                        f"{provider_loc_recipient}\n"
                        f"{get_recipient_str('proposed_rate')} <code>{formatted_price}</code>\n\n"
                        f"<i>{get_recipient_str('counter_notice')}</i>"
                    )
                else:
                    asset_display_sender = (
                        f"{get_sender_str('asset_request')} <i>{html.escape(req_eq)}</i>\n{get_sender_str('selected_equipment')} <i>{html.escape(prop_eq)}</i>"
                        if has_eq_diff
                        else f"{get_sender_str('asset')} <i>{html.escape(prop_eq)}</i>"
                    )

                    sender_confirm_card = (
                        f"{get_sender_str('deal_header')}\n"
                        f"───────────────────────\n"
                        f"{get_sender_str('offer_transmitted')}\n"
                        f"━━━━━━━━━━━━━━━━━━━\n"
                        f"{get_sender_str('transaction')} <i>{html.escape(sender_tx_type)}</i>\n"
                        f"{asset_display_sender}\n"
                        f"{sender_cap_display}\n"
                        f"{seeker_loc_sender}\n"
                        f"{provider_loc_sender}\n"
                        f"{get_sender_str('proposed_rate')} <code>{formatted_price}</code>\n"
                        f"{get_sender_str('status_awaiting_provider')}\n\n"
                        f"<i>{get_sender_str('sender_footer_provider')}</i>"
                    )

                    asset_display_recipient = (
                        f"{get_recipient_str('equipment')} <i>{html.escape(prop_eq)}</i>"
                    )

                    recipient_notify_card = (
                        f"{get_recipient_str('deal_header')}\n"
                        f"───────────────────────\n"
                        f"{get_recipient_str('counter_offer_received')}\n"
                        f"━━━━━━━━━━━━━━━━━━━\n"
                        f"{get_recipient_str('transaction')} <i>{html.escape(recipient_tx_type)}</i>\n"
                        f"{asset_display_recipient}\n"
                        f"{recipient_cap_display}\n"
                        f"{seeker_loc_recipient}\n"
                        f"{provider_loc_recipient}\n"
                        f"{get_recipient_str('offered_rate')} <code>{formatted_price}</code>\n\n"
                        f"<i>{get_recipient_str('counter_notice')}</i>"
                    )

                await send_response(sender_confirm_card)

                action_keyboard = [
                    [
                        InlineKeyboardButton(
                            get_recipient_str("btn_accept"),
                            callback_data=f"equip_deal_accept:{deal_id_str}",
                        ),
                        InlineKeyboardButton(
                            get_recipient_str("btn_counter"),
                            callback_data=f"equip_deal_counter:{deal_id_str}",
                        ),
                    ],
                    [
                        InlineKeyboardButton(
                            get_recipient_str("btn_decline"),
                            callback_data=f"equip_deal_decline:{deal_id_str}",
                        )
                    ],
                ]
                reply_markup = InlineKeyboardMarkup(action_keyboard)

                try:
                    if recipient_id and recipient_id > 0:
                        if presigned_url:
                            await context.bot.send_photo(
                                chat_id=recipient_id,
                                photo=presigned_url,
                                caption=recipient_notify_card,
                                parse_mode="HTML",
                                reply_markup=reply_markup,
                            )
                        else:
                            await context.bot.send_message(
                                chat_id=recipient_id,
                                text=recipient_notify_card,
                                parse_mode="HTML",
                                reply_markup=reply_markup,
                            )
                except Exception as notify_err:
                    logger.error(f"Failed to dispatch deal notification to user {recipient_id}: {notify_err}")
            else:
                await send_response(t("deal_blocked", sender_lang))

        except Exception as e:
            logger.error(f"Unexpected error during deal lock processing: {e}", exc_info=True)
            await send_response(t("system_error", sender_lang))

        context.user_data.pop("is_reverse_match", None)
        return await self.route_to_main_menu(update, context)

    async def handle_match_cancellation(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        query = update.callback_query
        if query:
            await query.answer("Search cancelled.")
        lang = context.user_data.get('lang', 'en')

        context.user_data.pop('equipment_top_matches', None)
        context.user_data.pop('seeker_top_matches', None)
        context.user_data.pop('is_reverse_match', None)
        cancel_msg = get_text(lang, "msg_match_cancelled") or "❌ Search cancelled. Returning to main menu."

        chat_id = update.effective_chat.id if update.effective_chat else None
        try:
            message = query.message if query else None
            if message and hasattr(message, "text") and message.text:
                await query.edit_message_text(cancel_msg, parse_mode="HTML")
            elif message and hasattr(message, "caption") and message.caption:
                await query.edit_message_caption(cancel_msg, parse_mode="HTML")
            else:
                if message and chat_id:
                    await context.bot.delete_message(
                        chat_id=chat_id,
                        message_id=message.message_id
                    )
                if chat_id:
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=cancel_msg,
                        parse_mode="HTML"
                    )
        except Exception as e:
            logger.error(f"Failed to edit message during match cancellation: {e}")

        return await self.route_to_main_menu(update, context)

    # ==========================================
    # ESCAPE PATHS
    # ==========================================
    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        lang = context.user_data.get('lang', 'en')
        cancel_text = get_text(lang,
                               "msg_process_cancelled") or "🛑 <b>Process cancelled.</b> Returning to main menu..."
        chat_id = update.effective_chat.id if update.effective_chat else None

        if update.callback_query:
            query = update.callback_query
            try:
                await query.answer()
            except Exception:
                pass

            try:
                await query.edit_message_text(text=cancel_text, parse_mode="HTML")
            except BadRequest as edit_err:
                err_msg = str(edit_err).lower()
                if "no text in the message" in err_msg or "message to edit" in err_msg:
                    try:
                        await query.edit_message_caption(caption=cancel_text, parse_mode="HTML")
                    except BadRequest:
                        if chat_id:
                            await context.bot.send_message(chat_id=chat_id, text=cancel_text, parse_mode="HTML")
                else:
                    if chat_id:
                        await context.bot.send_message(chat_id=chat_id, text=cancel_text, parse_mode="HTML")
        elif update.message:
            await update.message.reply_text(cancel_text, parse_mode="HTML")

        if chat_id:
            logger.info(f"User {chat_id} cancelled equipment workflow. Executing route_to_main_menu.")

        return await self.route_to_main_menu(update, context)

    async def handle_category_recovery(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """
        Recovers dropped sessions (state None) when users click category buttons,
        ensuring user_data context is initialized before delegation.
        """
        if not context.user_data.get("flow"):
            context.user_data["flow"] = "find"  # Safe default fallback
        if "lang" not in context.user_data:
            context.user_data["lang"] = "en"

        return await self.handle_category(update, context)

    # ==========================================
    # 6. MASTER HANDLER REGISTRATION
    # ==========================================
    def get_conversation_handler(self) -> ConversationHandler:
        """
        Constructs and returns a fully isolated, production-grade ConversationHandler
        for Equipment Rental workflows. Uses strict regex patterns and namespaced callback prefixes
        to permanently prevent state corruption and unwanted flow switching.
        """
        from src.smartmatch.handlers.main_menu import MainMenuHandler

        # Common deal action handlers to be shared across states for robust accessibility
        deal_action_handlers = [
            CallbackQueryHandler(self.deal_accept_handler, pattern=r"^equip_deal_accept:"),
            CallbackQueryHandler(self.deal_counter_handler, pattern=r"^equip_deal_counter:"),
            CallbackQueryHandler(self.deal_decline_handler, pattern=r"^equip_deal_decline:"),
        ]

        return ConversationHandler(
            entry_points=[
                             CommandHandler("list_equipment", self.start_list_equipment),
                             CommandHandler("find_equipment", self.start_find_equipment),
                             MessageHandler(
                                 filters.Text(MainMenuHandler.get_labels_for_action("btn_list_equipment")),
                                 self.start_list_equipment
                             ),
                             MessageHandler(
                                 filters.Text(MainMenuHandler.get_labels_for_action("btn_find_equipment")),
                                 self.start_find_equipment
                             ),
                             CallbackQueryHandler(self.start_list_equipment, pattern=r"^list_equipment$"),
                             CallbackQueryHandler(self.start_find_equipment, pattern=r"^find_equipment$"),
                             CallbackQueryHandler(self.prompt_initial_offer, pattern=r"^equipment_offer:"),
                         ] + deal_action_handlers,
            states={
                None: [
                          CallbackQueryHandler(self.start_list_equipment, pattern=r"^list_equipment$"),
                          CallbackQueryHandler(self.start_find_equipment, pattern=r"^find_equipment$"),
                      ] + deal_action_handlers,
                self.AWAITING_EQUIPMENT_OFFER: [
                                                   MessageHandler(filters.TEXT & ~filters.COMMAND,
                                                                  self.process_initial_offer),
                                                   CallbackQueryHandler(self.route_to_main_menu,
                                                                        pattern=r"^cancel_vertical_offer$")
                                               ] + deal_action_handlers,
                self.FLOW_SELECTION: [
                                         CallbackQueryHandler(self.handle_category, pattern=r"^cat_"),
                                         CallbackQueryHandler(self.route_to_main_menu,
                                                              pattern=r"^(cancel_match|cancel_vertical_offer)$")
                                     ] + deal_action_handlers,
                self.EQUIPMENT_CATEGORY: [
                                             CallbackQueryHandler(self.handle_category, pattern=r"^cat_"),
                                             CallbackQueryHandler(self.route_to_main_menu,
                                                                  pattern=r"^(cancel_match|cancel_vertical_offer)$")
                                         ] + deal_action_handlers,
                self.EQUIPMENT_TYPE: [
                                         MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_equipment_type),
                                         CallbackQueryHandler(self.route_to_main_menu,
                                                              pattern=r"^(cancel_match|cancel_vertical_offer)$")
                                     ] + deal_action_handlers,
                self.CAPACITY_OUTPUT: [
                                          MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_capacity_output),
                                          CallbackQueryHandler(self.route_to_main_menu,
                                                               pattern=r"^(cancel_match|cancel_vertical_offer)$")
                                      ] + deal_action_handlers,
                self.BRAND_FEATURES: [
                                         MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_brand_features),
                                         CallbackQueryHandler(self.route_to_main_menu,
                                                              pattern=r"^(cancel_match|cancel_vertical_offer)$")
                                     ] + deal_action_handlers,
                self.LOCATION_DATA: [
                                        MessageHandler((filters.TEXT | filters.LOCATION) & ~filters.COMMAND,
                                                       self.handle_location_data),
                                        CallbackQueryHandler(self.route_to_main_menu,
                                                             pattern=r"^(cancel_match|cancel_vertical_offer)$")
                                    ] + deal_action_handlers,
                self.RATES_BUDGET: [
                                       MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_rates_budget),
                                       CallbackQueryHandler(self.route_to_main_menu,
                                                            pattern=r"^(cancel_match|cancel_vertical_offer)$")
                                   ] + deal_action_handlers,
                self.CONDITION_PREFS: [
                                          MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_condition_prefs),
                                          CallbackQueryHandler(self.route_to_main_menu,
                                                               pattern=r"^(cancel_match|cancel_vertical_offer)$")
                                      ] + deal_action_handlers,
                self.PHOTO_UPLOAD: [
                                       MessageHandler(
                                           (filters.PHOTO | filters.Document.ALL | filters.TEXT) & ~filters.COMMAND,
                                           self.handle_photo_upload),
                                       CallbackQueryHandler(self.route_to_main_menu,
                                                            pattern=r"^(cancel_match|cancel_vertical_offer)$")
                                   ] + deal_action_handlers,
                self.CONTACT_INFO: [
                                       MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_contact_info),
                                       CallbackQueryHandler(self.route_to_main_menu,
                                                            pattern=r"^(cancel_match|cancel_vertical_offer)$")
                                   ] + deal_action_handlers,
                self.SELECTING_MATCH: [
                                          CallbackQueryHandler(self.handle_match_selection,
                                                               pattern=r"^(eq_)?select_(equip|seeker)_match:"),
                                          CallbackQueryHandler(self.prompt_custom_offer,
                                                               pattern=r"^equip_prompt_offer:"),
                                          CallbackQueryHandler(self.execute_deal_lock, pattern=r"^lock_equip_deal:"),
                                          CallbackQueryHandler(
                                              self.handle_back_to_top_matches,
                                              pattern=r"^(equip|seeker|eq)_back_to_top$",
                                          ),
                                          CallbackQueryHandler(self.route_to_main_menu,
                                                               pattern=r"^(cancel_match|cancel_vertical_offer)$")
                                      ] + deal_action_handlers,
                self.ENTERING_OFFER: [
                                         MessageHandler(filters.TEXT & ~filters.COMMAND, self.receive_custom_offer),
                                         CallbackQueryHandler(self.route_to_main_menu,
                                                              pattern=r"^(cancel_match|cancel_vertical_offer)$")
                                     ] + deal_action_handlers
            },
            fallbacks=[
                CommandHandler("cancel", self.route_to_main_menu),
                CommandHandler("start", self.route_to_main_menu),
                CallbackQueryHandler(self.route_to_main_menu,
                                     pattern=r"^(cancel_match|cancel_vertical_offer|equip_back_to_top|equip_deal_decline:)$")
            ],
            name="equipment_rental_conversation",
            persistent=True,
            per_user=True,
            per_chat=True,
            per_message=False,
            allow_reentry=True,
            conversation_timeout=None
        )


# ==========================================
# 7. DEPENDENCY INJECTION BOILERPLATE
# ==========================================
def setup_equipment_handler(db, ai, matching, billing, storage, deals_handler, queue) -> ConversationHandler:
    handler = EquipmentRentalHandler(
        db_manager=db,
        ai_engine=ai,
        matching_engine=matching,
        billing_service=billing,
        image_service=storage,
        deals_handler=deals_handler,
        queue_manager=queue
    )
    return handler.get_conversation_handler()
