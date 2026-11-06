import asyncio
import html
import logging
import re
import inspect
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union, Set

import httpx
from telegram.error import NetworkError
from telegram import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import RetryAfter, TelegramError, TimedOut
from telegram.ext import (
    ApplicationHandlerStop,
    CallbackQueryHandler,
    ConversationHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from src.smartmatch.services.billing_service import BillingService
from src.smartmatch.services.db_manager import DatabaseManager
from src.smartmatch.services.reserve__quantity_location import ReservationHelper
from src.smartmatch.utils.translations import get_text

logger = logging.getLogger("smartmatch.deals")


# ==========================================
# DEFENSIVE ESCAPERS & UTILITIES
# ==========================================
def escape_md(text: Any) -> str:
    """
    Defensively escapes characters that can break Telegram's Markdown parser.
    Ensures safe injection of user data into formatted strings.
    """
    if text is None:
        return ""
    return re.sub(r"([_*`\[])", r"\\\1", str(text))


def convert_md_to_html(text: str) -> str:
    """Converts legacy Markdown formatting tokens into valid HTML tags."""
    if not text:
        return ""
    text = re.sub(r'`([^`]+)`', r'<code>\1</code>', text)
    text = re.sub(r'\*([^*]+)\*', r'<b>\1</b>', text)
    text = re.sub(r'_([^_]+)_', r'<i>\1</i>', text)
    return text


def esc(text: Any) -> str:
    """Safely escape HTML special characters for Telegram rendering."""
    if text is None:
        return ""
    return html.escape(str(text), quote=True)


# ==========================================
# TELEGRAM NOTICE FORMATTER
# ==========================================
class TelegramNoticeFormatter:
    """Centralized Telegram HTML notice builder supporting multi-language templates."""

    TEMPLATES: Dict[str, Dict[str, Dict[str, str]]] = {
        "provider_accepts": {
            "en": {
                "header": "✅ <b>Offer Accepted</b>",
                "counter_header": "✅ <b>Counter-Offer Accepted</b>",
                "provider_body": "You accepted the offer of <b>{price} {currency}</b> for <i>{asset_title}</i>. Awaiting seeker's commission payment..",
                "provider_counter_body": "You accepted the counter-offer of <b>{price} {currency}</b> for <i>{asset_title}</i>. Awaiting seeker's commission payment..",
                "seeker_body": "🎉 <b>Offer Accepted!</b>\n\nThe provider accepted your offer of <b>{price} {currency}</b> for <i>{asset_title}</i>.\n\n{details}\n\nTo view details and contact information, please select a payment method below to pay the commission.",
                "seeker_counter_body": "🎉 <b>Counter-Offer Accepted!</b>\n\nThe provider accepted your counter-offer of <b>{price} {currency}</b> for <i>{asset_title}</i>.\n\n{details}\n\nTo view details and contact information, please select a payment method below to pay the commission."
            },
            "am": {
                "header": "✅ <b>ማቅረቢያ ተቀባይነት አግኝቷል</b>",
                "counter_header": "✅ <b>የመደራደሪያ ዋጋ ማቅረቢያ ተቀባይነት አግኝቷል</b>",
                "provider_body": "ለ <i>{asset_title}</i> የ <b>{price} {currency}</b> ማቅረቢያን ተቀብለዋል። የፈላጊው የኮሚሽን ክፍያ በመጠባበቅ ላይ።",
                "provider_counter_body": "ለ <i>{asset_title}</i> የ <b>{price} {currency}</b> ተቃራኒ ዋጋ ተቀብለዋል። የፈላጊው የኮሚሽን ክፍያ በመጠባበቅ ላይ።",
                "seeker_body": "🎉 <b>ማቅረቢያ ተቀባይነት አግኝቷል!</b>\n\nአቅራቢው ለ <i>{asset_title}</i> የ <b>{price} {currency}</b> ማቅረቢያዎን ተቀብለዋል።\n\n{details}\n\nዝርዝር መረጃዎችን እና የመገናኛ ስልክ ቁጥር ለማግኘት፥ እባክዎ ከታች የክፍያ አማራጭ በመምረጥ የኮሚሽን ክፍያ ይፈጽሙ።",
                "seeker_counter_body": "🎉 <b>የመደራደሪያ ዋጋ ማቅረቢያ ተቀባይነት አግኝቷል!</b>\n\nአቅራቢው ለ <i>{asset_title}</i> ያቀረቡትን የ <b>{price} {currency}</b> ተቃራኒ ዋጋ ተቀብሏል።\n\n{details}\n\nእባክዎን ዝርዝር መረጃ እና የስልክ ቁጥር ለማግኘት ከታች ያለውን ተመራጭ የክፍያ አማራጭ በመጠቀም ኮሚሽኑን ይክፈሉ።"
            },
            "om": {
                "header": "✅ <b>Dhiheessiin Fudhatameera</b>",
                "counter_header": "✅ <b>Gatiin Wal Jijjiirame Fudhatameera</b>",
                "provider_body": "Dhiheessi <i>{asset_title}</i>-f <b>{price} {currency}</b> fudhatteetta. Kafaltii komishinii barbaaddichaa eeggachaa jira.",
                "provider_counter_body": "Gatii jijjiiramaa <i>{asset_title}</i>-f <b>{price} {currency}</b> fudhatteetta. Kafaltii komishinii barbaaddichaa eeggachaa jira.",
                "seeker_body": "🎉 <b>Dhiheessiin Fudhatameera!</b>\n\nDhiyeessaan dhiheessi kee <i>{asset_title}</i>f <b>{price} {currency}</b> fudhateera.\n\n{details}\n\nOdeeffannoo bal'aa fi bilbila quunnamtii argachuuf, maaloo kaffaltii komishinii raawwachuuf filannoo kaffaltii gadii filadhaa.",
                "seeker_counter_body": "🎉 <b>Gatiin Wal Jijjiirame Fudhatameera!</b>\n\nDhiyeessaan gatii jijjiiramaa <i>{asset_title}</i>f <b>{price} {currency}</b> fudhateera.\n\n{details}\n\nOdeeffannoo bal'aa fi bilbila quunnamtii argachuuf, maaloo kaffaltii komishinii raawwachuuf filannoo kaffaltii gadii filadhaa."
            }
        },
        "seeker_accepts": {
            "en": {
                "seeker_body": "✅ <b>Offer Accepted!</b>\n\nYou accepted the offer of <b>{price} {currency}</b> for <i>{asset_title}</i>.\n\n{details}\n\nTo view details and contact information, please select a payment method below to pay the commission.",
                "seeker_counter_body": "✅ <b>Counter-Offer Accepted!</b>\n\nYou accepted the counter-offer of <b>{price} {currency}</b> for <i>{asset_title}</i>.\n\n{details}\n\nTo view details and contact information, please select a payment method below to pay the commission.",
                "provider_body": "🎉 <b>Offer Accepted!</b>\n\nThe seeker accepted your offer of <b>{price} {currency}</b> for <i>{asset_title}</i>.\n\n{details}\n\nWe are awaiting the seeker's commission payment.",
                "provider_counter_body": "🎉 <b>Counter-Offer Accepted!</b>\n\nThe seeker accepted your counter-offer of <i>{asset_title}</i> for <b>{price} {currency}</b>.\n\n{details}\n\nWe are awaiting the seeker's commission payment."
            },
            "am": {
                "seeker_body": "✅ <b>ማቅረቢያ ተቀባይነት አግኝቷል!</b>\n\nለ <i>{asset_title}</i> የቀረበውን የ <b>{price} {currency}</b> ማቅረቢያ ተቀብለዋል።\n\n{details}\n\nእባክዎን ዝርዝር መረጃ እና የስልክ ቁጥር ለማግኘት ከታች ያለውን ተመራጭ የክፍያ አማራጭ በመጠቀም ኮሚሽኑን ይክፈሉ።",
                "seeker_counter_body": "✅ <b>የመደራደሪያ ዋጋ ማቅረቢያ ተቀባይነት አግኝቷል!</b>\n\nለ <i>{asset_title}</i> የ <b>{price} {currency}</b> ተቃራኒ ዋጋ ተቀብለዋል።\n\n{details}\n\nእባክዎን ዝርዝር መረጃ እና የስልክ ቁጥር ለማግኘት ከታች ያለውን ተመራጭ የክፍያ አማራጭ በመጠቀም ኮሚሽኑን ይክፈሉ።",
                "provider_body": "🎉 <b>ማቅረቢያ ተቀባይነት አግኝቷል!</b>\n\nፈላጊው ለ <i>{asset_title}</i> ያቀረቡትን የ <b>{price} {currency}</b> ማቅረቢያ ተቀብሏል።\n\n{details}\n\nየፈላጊውን የኮሚሽን ክፍያ በመጠባበቅ ላይ",
                "provider_counter_body": "🎉 <b>የመደራደሪያ ዋጋ ማቅረቢያ ተቀባይነት አግኝቷል!</b>\n\nፈላጊው ለ <i>{asset_title}</i> ያቀረቡትን የ <b>{price} {currency}</b> ተቃራኒ ዋጋ ተቀብሏል።\n\n{details}\n\nየፈላጊውን የኮሚሽን ክፍያ በመጠባበቅ ላይ"
            },
            "om": {
                "seeker_body": "✅ <b>Dhiheessiin Fudhatameera!</b>\n\nDhiheessii <i>{asset_title}</i>f <b>{price} {currency}</b> fudhatteetta.\n\n{details}\n\nOdeeffannoo bal'aa fi bilbila quunnamtii argachuuf, maaloo kaffaltii komishinii raawwachuuf filannoo kaffaltii gadii filadhaa.",
                "seeker_counter_body": "✅ <b>Gatiin Wal Jijjiirame Fudhatameera!</b>\n\nGatii jijjiiramaa <i>{asset_title}</i>f <b>{price} {currency}</b> fudhatteetta.\n\n{details}\n\nOdeeffannoo bal'aa fi bilbila quunnamtii argachuuf, maaloo kaffaltii komishinii raawwachuuf filannoo kaffaltii gadii filadhaa.",
                "provider_body": "🎉 <b>Dhiheessiin Fudhatameera!</b>\n\nBarbaadaan dhiheessii keessan <i>{asset_title}</i>f <b>{price} {currency}</b> fudhateera.\n\n{details}\n\nKafaltii komishinii barbaaddichaa eegaa jirra..",
                "provider_counter_body": "🎉 <b>Gatiin Wal Jijjiirame Fudhatameera!</b>\n\nBitataan gatii jijjiiramaa <i>{asset_title}</i>f <b>{price} {currency}</b> fudhateera.\n\n{details}\n\nKafaltii komishinii barbaaddichaa eegaa jirra.."
            }
        }
    }

    @classmethod
    def build_notice(
        cls,
        action_type: str,
        role: str,
        lang: str,
        price: str,
        currency: str,
        asset_title: str,
        details_block: str,
        is_reverse: bool = False
    ) -> str:
        """Renders sanitized Telegram HTML notice based on role, language, and deal parameters."""
        lang = lang if lang in ["en", "am", "om"] else "en"
        templates = cls.TEMPLATES.get(action_type, {}).get(lang, cls.TEMPLATES[action_type]["en"])

        if role == "provider":
            key = "provider_counter_body" if is_reverse else "provider_body"
            body_fmt = templates.get(key, "")
            text = f"{body_fmt}\n\n{details_block}" if details_block else body_fmt
        else:
            key = "seeker_counter_body" if is_reverse else "seeker_body"
            text = templates.get(key, "")

        return text.format(
            price=esc(price),
            currency=esc(currency),
            asset_title=esc(asset_title),
            details=details_block
        ).strip()


# ==========================================
# QUEUE MANAGER MIXIN
# ==========================================
class QueueFlushManagerMixin:
    """
    Mixin component providing context-aware notification queue flushing capabilities
    for the DealsHandler state machine. Fully converted to Markdown.
    """

    async def check_and_flush_queue(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user:
            return

        user_id = update.effective_user.id
        queue: List[Dict[str, Any]] = context.user_data.pop("notification_queue", [])

        if not queue:
            return

        lang = await self._get_user_lang(user_id, context) if hasattr(self, '_get_user_lang') else 'en'

        # Safety Cap: Prevent spam-flooding
        max_queue_items = 10
        if len(queue) > max_queue_items:
            logger.warning(f"User {user_id} queue exceeded capacity. Truncating.")
            queue = queue[-max_queue_items:]

            overflow_warning = get_text(lang, "msg_queue_overflow") or "⚠️ *Notice:* Your notification queue exceeded the limit. Older updates dropped."
            try:
                await context.bot.send_message(chat_id=user_id, text=overflow_warning, parse_mode="Markdown")
                await asyncio.sleep(0.3)
            except TelegramError:
                pass

        header_msg = get_text(lang, "msg_missed_alerts") or "🔔 *You have missed updates:*"
        try:
            await context.bot.send_message(chat_id=user_id, text=header_msg, parse_mode="Markdown")
            await asyncio.sleep(0.4)
        except TelegramError:
            pass

        fallback_label = get_text(lang, "lbl_queued_notification") or "📝 *Queued Notification:*"
        successful_deliveries = 0

        for index, delayed_msg in enumerate(queue):
            text_content = delayed_msg.get("text", fallback_label)
            reply_markup = delayed_msg.get("reply_markup", None)

            try:
                await context.bot.send_message(
                    chat_id=user_id, text=text_content, reply_markup=reply_markup, parse_mode="Markdown"
                )
                successful_deliveries += 1
                await asyncio.sleep(0.35)

            except RetryAfter as flood_err:
                logger.warning(f"Telegram FloodControl for {user_id}. Backing off {flood_err.retry_after}s.")
                await asyncio.sleep(flood_err.retry_after + 0.1)
                try:
                    await context.bot.send_message(
                        chat_id=user_id, text=text_content, reply_markup=reply_markup, parse_mode="Markdown"
                    )
                    successful_deliveries += 1
                except TelegramError as retry_err:
                    logger.error(f"Retry failed for user {user_id}: {retry_err}")
            except TimedOut:
                await asyncio.sleep(1.0)
            except TelegramError:
                continue


# ==========================================
# MAIN DEALS HANDLER
# ==========================================

class DealsHandler(QueueFlushManagerMixin):
    """
    Production-grade distributed state machine coordinating escrowed
    negotiations across disparate vertical asset categories.
    Fully localized, protected against race conditions, and strictly formatted in Markdown.
    """

    def __init__(
        self,
        db_manager: DatabaseManager,
        billing_service: BillingService,
        telegram_app: Any,
        webhook_handler: Optional[Any] = None
    ):
        self.db = db_manager
        self.billing = billing_service
        self.telegram_app = telegram_app
        self.webhook_handler = webhook_handler

    def _get_bot_instance(self, context: Optional[ContextTypes.DEFAULT_TYPE] = None):
        """Helper to reliably resolve the Telegram bot instance."""
        if hasattr(self, 'telegram_app') and self.telegram_app and hasattr(self.telegram_app, 'bot'):
            return self.telegram_app.bot
        if context and hasattr(context, 'bot') and context.bot:
            return context.bot
        if hasattr(self, 'bot') and getattr(self, 'bot', None):
            return self.bot
        return None

    def _resolve_webhook_handler(self, context: Optional[ContextTypes.DEFAULT_TYPE] = None):
        """Helper to reliably resolve WebhookHandler instance."""
        handler = (
                (context.bot_data.get("webhook_handler") if context else None)
                or getattr(self, "webhook_handler", None)
                or getattr(self, "gateway", None)
        )
        if not handler or not hasattr(handler, "initialize_ethiopian_payment"):
            if hasattr(self, "billing") and hasattr(self.billing, "webhook_handler"):
                handler = self.billing.webhook_handler
            elif context and context.bot_data.get("billing_service"):
                handler = getattr(context.bot_data.get("billing_service"), "webhook_handler", None)
        return handler

    def _resolve_db_service(self, webhook_handler=None, context: Optional[ContextTypes.DEFAULT_TYPE] = None):
        """Helper to reliably resolve Database Service."""
        return (
                getattr(webhook_handler, "db", None)
                or getattr(self, "db", None)
                or (context.bot_data.get("db_manager") if context else None)
        )

    @classmethod
    def _build_payment_keyboard(cls, deal_id: str, amount_etb_str: str, amount_usd_str: str,
                                lang: str = "en") -> InlineKeyboardMarkup:
        """Constructs a unified localized inline keyboard using callback actions."""
        btn_local = get_text(lang, "btn_pay_local", price=amount_etb_str)
        if not btn_local or "[btn_pay_local]" in btn_local:
            if lang == "om":
                btn_local = f"🇪🇹 Waaletii Biyya Keessaan Komishinii Kaffalaa ({amount_etb_str} ETB)"
            elif lang == "am":
                btn_local = f"🇪🇹 በአገር ውስጥ ዋሌቶች ኮሚሽን ይክፈሉ ({amount_etb_str} ETB)"
            else:
                btn_local = f"💳 Pay Commission via Local Wallets ({amount_etb_str} ETB)"

        btn_intl = get_text(lang, "btn_pay_intl", price=amount_usd_str)
        if not btn_intl or "[btn_pay_intl]" in btn_intl:
            if lang == "om":
                btn_intl = f"🌍 Kaardii Idil-addunyaa / PayPal (${amount_usd_str} USD)"
            elif lang == "am":
                btn_intl = f"🌍 ዓለም አቀፍ ካርድ / PayPal (${amount_usd_str} USD)"
            else:
                btn_intl = f"🌍 International Cards / PayPal (${amount_usd_str} USD)"

        return InlineKeyboardMarkup([
            [InlineKeyboardButton(text=btn_local, callback_data=f"pay_local_{deal_id}")],
            [InlineKeyboardButton(text=btn_intl, callback_data=f"pay_intl_{deal_id}")]
        ])

    # ========================================================================
    # LANGUAGE RESOLUTION HELPERS (ISOLATED & SESSION-SAFE)
    # ========================================================================
    async def _get_user_lang(self, user_id: int, context: Optional[ContextTypes.DEFAULT_TYPE] = None) -> str:
        if context and context.user_data:
            cached_lang = context.user_data.get("lang") or context.user_data.get("language")
            if cached_lang:
                return str(cached_lang).lower()[:2]

        try:
            lang = None
            if hasattr(self.db, 'get_user_language'):
                lang = await self.db.get_user_language(user_id)
            elif hasattr(self.db, 'supabase') and self.db.supabase:
                res = await self.db.supabase.table("users").select("language").eq("telegram_id", user_id).maybe_single().execute()
                if res and hasattr(res, 'data') and res.data:
                    lang = res.data.get("language")

            resolved_lang = str(lang).lower()[:2] if lang else "en"

            if context and context.user_data:
                context.user_data["lang"] = resolved_lang
                context.user_data["language"] = resolved_lang

            return resolved_lang
        except Exception as err:
            logger.warning(f"Error fetching language for user {user_id}: {err}")
            return "en"

    async def _get_target_user_lang_isolated(self, target_chat_id: int) -> str:
        try:
            if hasattr(self.db, 'supabase') and self.db.supabase:
                res = await self.db.supabase.table("users").select("language").eq("telegram_id", target_chat_id).maybe_single().execute()

                if res and hasattr(res, 'data') and res.data and res.data.get("language"):
                    return str(res.data.get("language")).lower()[:2]

            elif hasattr(self.db, 'get_user_language'):
                raw_lang = await self.db.get_user_language(target_chat_id)
                if raw_lang:
                    return str(raw_lang).lower()[:2]
        except Exception as err:
            logger.warning(f"Could not fetch isolated target lang for {target_chat_id}: {err}")

        return "en"

    # ------------------------------------------------------------------------
    # ATTRIBUTE EXTRACTION ENGINE (LANGUAGE-NEUTRAL & KEY-RESILIENT)
    # ------------------------------------------------------------------------

    @staticmethod
    def format_asset_title(entity: Dict[str, Any],
                           vertical_type: str = "general") -> str:
        """Formats asset titles across multi-vertical domains (Housing, Vehicle, Equipment,

        Freight, Ride) while eliminating notification cross-contamination,
        multilingual system noise, and generic bare brand mixups.
        """
        if not entity or not isinstance(entity, dict):
            fallback_label = str(vertical_type
                                 or "General").strip().capitalize()
            return f"{fallback_label} Asset" if fallback_label != "General" else "Matched Asset Listing"

        # Normalize vertical type with fallback support
        v_type = str(vertical_type or entity.get("vertical_type")
                     or "general").lower().strip()

        INVALID_TERMS = {
            "",
            "N/A",
            "NONE",
            "NULL",
            "UNSPECIFIED",
            "ANY",
            "0",
            "0.0",
            "NOT_SPECIFIED",
            "NOT SPECIFIED",
            "UNDEFINED",
        }

        # Helper: Clean invalid terms & strip hardcoded multilingual/system prompt artifacts
        # Helper: Clean invalid terms & strip hardcoded multilingual/system prompt artifacts
        def clean_val(val: Any) -> str:
            if val is None:
                return ""
            s = str(val).strip()
            if s.upper() in INVALID_TERMS:
                return ""

            # Strip hardcoded multilingual system labels and emojis
            s = re.sub(r"^(?:Fe'umsaa|ጭነት|Freight|Imala|ራይድ|Ride)\s*-\s*", "", s, flags=re.IGNORECASE)
            s = re.sub(r"\(Gabatee:[^)]+\)", "", s, flags=re.IGNORECASE)

            # STRICT VERTICAL ISOLATION (Crush cross-contamination at the source)
            if v_type not in ["freight", "ride", "freight_ride", "cargo"]:
                s = re.sub(r"📍\s*(?:Karaa|መስመር|Route|Location):[^\n]+", "", s, flags=re.IGNORECASE)
                s = re.sub(r"\([^)]*➡️[^)]*\)", "", s)
                s = s.replace("➡️", "")

            if v_type not in ["freight", "freight_ride", "cargo", "logistics"]:
                s = re.sub(r"⚖️\s*(?:Ulfaatina|ክብደት|Weight|Cargo):[^\n]+", "", s, flags=re.IGNORECASE)
                s = re.sub(r"\b\d+(?:\.\d+)?\s*(?:kg|kgs|ton|tons|quintal|t)\b", "", s, flags=re.IGNORECASE)

            if v_type not in ["ride", "passenger", "taxi", "freight_ride"]:
                s = re.sub(r"👥\s*(?:Baay'ina.*|የሰው.*|Party.*|Passengers|Seats):[^\n]+", "", s, flags=re.IGNORECASE)

            return s.strip(" -:")

        # Safely extract raw candidate attributes
        title = clean_val(
            entity.get("title")
            or entity.get("asset_title")
            or entity.get("name")
        )
        brand = clean_val(
            entity.get("brand")
            or entity.get("make")
            or entity.get("vehicle_make")
            or entity.get("manufacturer")
        )
        model = clean_val(entity.get("model") or entity.get("vehicle_model"))
        year = clean_val(entity.get("year") or entity.get("model_year"))
        mmy = clean_val(entity.get("make_model_year"))

        eq_type = clean_val(
            entity.get("equipment_type") or entity.get("category")
        )
        v_type_val = clean_val(
            entity.get("vehicle_type")
            or entity.get("car_type")
            or entity.get("truck_type")
            or entity.get("cargo_type")
        )

        plate = clean_val(entity.get("plate_number") or entity.get("plate"))
        plate_str = f" [{plate}]" if plate else ""

        # Discard generic title if it merely repeats a bare brand name
        if title and brand and title.lower() == brand.lower():
            title = ""

        # Construct unified make/model/year descriptor
        mmy_composite = mmy or " ".join(
            [p for p in [year, brand, model] if p]
        )

        # --------------------------------------------------------------------
        # 1. FREIGHT & LOGISTICS (Isolated Cargo & Route Context)
        # --------------------------------------------------------------------
        if v_type in [
            "freight",
            "freight_ride",
            "cargo",
            "logistics",
            "transport_freight",
        ]:
            cargo_str = (
                    clean_val(
                        entity.get("cargo_type")
                        or entity.get("truck_type")
                        or entity.get("vehicle_type")
                        or entity.get("category")
                    )
                    or "Freight Transport"
            )

            origin = clean_val(
                entity.get("origin")
                or entity.get("origin_city")
                or entity.get("pickup_location")
                or entity.get("from_location")
            )
            destination = clean_val(
                entity.get("destination")
                or entity.get("destination_city")
                or entity.get("dropoff_location")
                or entity.get("to_location")
            )

            route_parts = [r for r in [origin, destination] if r]

            if len(route_parts) == 2:
                return f"{cargo_str}{plate_str} ({route_parts[0]} ➡️ {route_parts[1]})"
            if len(route_parts) == 1:
                return f"{cargo_str}{plate_str} ({route_parts[0]})"
            if title and "➡️" in title:
                return f"{title}{plate_str}"
            if mmy_composite:
                return f"{cargo_str} - {mmy_composite}{plate_str}"
            return f"{cargo_str}{plate_str}"

        # --------------------------------------------------------------------
        # 2. RIDE & PASSENGER SERVICES (Strictly Isolated from General Vehicles)
        # --------------------------------------------------------------------
        elif v_type in ["ride", "passenger", "taxi", "ride_service"]:
            origin = clean_val(
                entity.get("origin")
                or entity.get("pickup_location")
                or entity.get("from_location")
            )
            destination = clean_val(
                entity.get("destination")
                or entity.get("dropoff_location")
                or entity.get("to_location")
            )
            route_parts = [r for r in [origin, destination] if r]

            ride_prefix = v_type_val or (
                f"{mmy_composite} Ride" if mmy_composite else "Ride Service"
            )

            if len(route_parts) == 2:
                return f"{ride_prefix}{plate_str} ({route_parts[0]} ➡️ {route_parts[1]})"
            if len(route_parts) == 1:
                return f"{ride_prefix}{plate_str} ({route_parts[0]})"
            if v_type_val and mmy_composite:
                return f"{v_type_val} - {mmy_composite}{plate_str}"
            if title and "➡️" not in title:
                return f"{title}{plate_str}"
            return f"{ride_prefix}{plate_str}"

        # --------------------------------------------------------------------
        # 3. VEHICLES (Automobiles & Personal Transport)
        # --------------------------------------------------------------------
        elif v_type in ["vehicle", "car", "auto", "transport"]:
            if v_type_val and mmy_composite:
                return f"{v_type_val} - {mmy_composite}{plate_str}"
            if mmy_composite:
                return f"{mmy_composite}{plate_str}"
            if v_type_val:
                return f"{v_type_val}{plate_str}"
            if title:
                if brand and brand.lower() not in title.lower():
                    return f"{title} ({brand}){plate_str}"
                return f"{title}{plate_str}"
            return (
                f"Vehicle ({brand}){plate_str}"
                if brand
                else f"Vehicle Item{plate_str}"
            )

        # --------------------------------------------------------------------
        # 4. HOUSING & REAL ESTATE
        # --------------------------------------------------------------------
        elif v_type in ["housing", "real_estate", "house", "property"]:
            prop_use = clean_val(
                entity.get("property_use") or entity.get("category")
            )
            loc = clean_val(
                entity.get("location")
                or entity.get("location_text")
                or entity.get("city")
                or entity.get("subcity")
            )
            use_type = prop_use.title() if prop_use else "Property"

            if title:
                if loc and loc.lower() not in title.lower():
                    return f"{title} - {loc}"
                return title
            if loc:
                return f"{use_type} - {loc}"
            return f"{use_type} Listing"

        # --------------------------------------------------------------------
        # 5. EQUIPMENT & MACHINERY
        # --------------------------------------------------------------------
        elif v_type in ["equipment", "equipment_rental", "machinery", "tools"]:
            equip_type_title = eq_type.title() if eq_type else "Equipment"

            if equip_type_title != "Equipment" and mmy_composite:
                return f"{equip_type_title} ({mmy_composite})"
            if equip_type_title != "Equipment" and brand:
                return f"{equip_type_title} ({brand})"
            if equip_type_title != "Equipment":
                return equip_type_title
            if title:
                if brand and brand.lower() not in title.lower():
                    return f"{title} ({brand})"
                return title
            if mmy_composite:
                return f"Equipment Unit ({mmy_composite})"
            return f"Equipment Unit ({brand})" if brand else "Equipment Item"

        # --------------------------------------------------------------------
        # FALLBACK / GENERAL MATCH
        # --------------------------------------------------------------------
        if eq_type and mmy_composite:
            return f"{eq_type.title()} ({mmy_composite})"
        if v_type_val and mmy_composite:
            return f"{v_type_val} - {mmy_composite}{plate_str}"
        if title:
            return title
        if mmy_composite:
            return mmy_composite
        return f"Asset ({brand})" if brand else "Matched Asset Listing"

    def get_action_handlers(self) -> list:
        """
        Returns handlers incorporating per-chat and per-user isolated ConversationHandlers
        to prevent cross-workflow state pollution during parallel asset negotiations.
        """

        # Isolated ConversationHandler for handling dynamic price counter-offers safely
        counter_offer_conversation = ConversationHandler(
            entry_points=[
                # 1. Accept Handler
                CallbackQueryHandler(
                    self.handle_deal_accept,
                    pattern=r"^(deal_accept|seeker_accept|provider_accept)(?::.+)?$"
                ),
                # 2. Counter Offer Handlers
                CallbackQueryHandler(
                    self.handle_deal_counter,
                    pattern=r"^(deal_counter|seeker_counter|provider_counter|(seeker|provider)_counter_prompt|counter_prompt)(?::.+)?$"
                ),
                # 3. Deal Decline/Termination Handlers
                CallbackQueryHandler(
                    self.handle_deal_decline,
                    pattern=r"^(deal_decline|decline_deal|reject_deal)(?::.+)?$"
                ),
                # 4. Unified Payment Checkout Handlers
                CallbackQueryHandler(
                    self.handle_payment_checkout,
                    pattern=r"^pay_(local|intl)_"
                ),
                # 5. Negotiation Cancellation Callbacks
                CallbackQueryHandler(
                    self.cancel_negotiation_sequence,
                    pattern=r"^(cancel_counter_offer|cancel_counter|cancel_negotiation|cancel_deal)(?::.+)?$"
                ),
                # 6. Command Handlers
                CommandHandler("cancel", self.cancel_negotiation_command)
            ],
            states={
                # Define your state constant or integer here (e.g., WAITING_FOR_COUNTER_INPUT = 1)
                1: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_counter_offer_input)
                ]
            },
            fallbacks=[
                CommandHandler("cancel", self.cancel_negotiation_command),
                CallbackQueryHandler(
                    self.cancel_negotiation_sequence,
                    pattern=r"^(cancel_counter_offer|cancel_counter|cancel_negotiation|cancel_deal)(?::.+)?$"
                )
            ],
            per_chat=True,  # Isolates state per Telegram chat ID
            per_user=True,  # Isolates state per Telegram user ID
            per_message=False,  # Allows handling state updates cleanly across message edits
            name="counter_offer_workflow",
            persistent=False
        )

        return [
            # 1. Isolated Multi-Step Negotiation Flow
            counter_offer_conversation,

            # 2. Bind directly to standard wrapper handle_deal_accept
            CallbackQueryHandler(
                self.handle_deal_accept,
                pattern=r"^(deal_accept|seeker_accept|provider_accept)(?::.+)?$"
            ),

            # 3. Deal Decline/Termination Handlers
            CallbackQueryHandler(
                self.handle_deal_decline,
                pattern=r"^(deal_decline|decline_deal|reject_deal)(?::.+)?$"
            ),

            # 4. Unified Payment Checkout Handlers
            CallbackQueryHandler(
                self.handle_payment_checkout,
                pattern=r"^pay_(local|intl)_"
            ),

            # 5. Standalone Negotiation Cancellation Callbacks
            CallbackQueryHandler(
                self.cancel_negotiation_sequence,
                pattern=r"^(cancel_counter_offer|cancel_counter|cancel_negotiation|cancel_deal)(?::.+)?$"
            ),

            # 6. Global Command Handlers
            CommandHandler("cancel", self.cancel_negotiation_command)
        ]

    def get_text_interceptor(self) -> MessageHandler:
        return MessageHandler(filters.TEXT & ~filters.COMMAND, self.process_counter_message)

    # =========================================================================
    # EXPLICIT BUTTON ENDPOINTS
    # =========================================================================
    async def handle_deal_accept(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        query = update.callback_query
        if not query or not query.data:
            return getattr(self, "MAIN_MENU", 0)
        await query.answer()

        user_id = query.from_user.id

        # Safe payload parsing to prevent unpacking errors
        parts = str(query.data).split(":", 1)
        deal_id_str = parts[1] if len(parts) > 1 else None

        # Helper function for local language normalization
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

        # Ground-truth database language resolver aligned strictly with users table schema
        async def _resolve_db_user_lang(chat_id: Optional[int], stored_lang: Optional[str] = None) -> str:
            db_client = getattr(self, 'db', getattr(self, 'db_manager', None))
            if db_client and hasattr(db_client, 'table') and chat_id is not None:
                try:
                    lang_res = await db_client.table('users').select(
                        'preferred_language, language, language_code').eq(
                        'telegram_id', int(chat_id)).maybe_single().execute()
                    if lang_res and getattr(lang_res, 'data', None):
                        db_data = lang_res.data
                        for col in ['preferred_language', 'language', 'language_code']:
                            norm_db = _normalize_lang(db_data.get(col))
                            if norm_db:
                                return norm_db
                except Exception as lang_err:
                    logger.warning(f"Failed to fetch ground-truth language from DB for user {chat_id}: {lang_err}")

            norm_stored = _normalize_lang(stored_lang)
            if norm_stored:
                return norm_stored

            if context and hasattr(context, 'user_data') and isinstance(context.user_data, dict):
                for lang_key in ['preferred_language', 'language_code', 'language']:
                    norm_ctx = _normalize_lang(context.user_data.get(lang_key))
                    if norm_ctx:
                        return norm_ctx

            return "en"

        # Fallback language for initial feedback if deal lookup fails
        fallback_lang = await _resolve_db_user_lang(user_id)

        if not deal_id_str or deal_id_str == "None":
            await query.edit_message_text(
                get_text(fallback_lang,
                         "error_transaction_invalid") or "⚠️ This deal offer is invalid or failed to process correctly."
            )
            return getattr(self, "MAIN_MENU", 0)

        # 1. Fetch deal record from DB to get exact seeker and provider IDs
        db = getattr(self, 'db', getattr(self, 'db_manager', None))
        deal: Dict[str, Any] = {}
        if db and hasattr(db, 'table'):
            res = await db.table('deals').select('*').eq('id', deal_id_str).maybe_single().execute()
            if res and getattr(res, 'data', None):
                deal = res.data

        if not deal:
            await query.edit_message_text(
                get_text(fallback_lang, "error_transaction_invalid") or "⚠️ Deal not found."
            )
            return getattr(self, "MAIN_MENU", 0)

        raw_seeker_id = deal.get('seeker_id') or deal.get('buyer_id')
        raw_provider_id = deal.get('provider_id') or deal.get('seller_id')

        seeker_chat_id: Optional[int] = int(raw_seeker_id) if raw_seeker_id is not None else None
        provider_chat_id: Optional[int] = int(raw_provider_id) if raw_provider_id is not None else None

        # Verify authorization with user alert feedback
        if (provider_chat_id is None or provider_chat_id != user_id) and (
                seeker_chat_id is None or seeker_chat_id != user_id):
            await query.answer("⚠️ You are not authorized to accept this deal.", show_alert=True)
            return getattr(self, "MAIN_MENU", 0)

        # =========================================================================
        # 🛡️ ROBUST PRICE RESOLUTION: Ensure the latest agreed price is stored in DB
        # =========================================================================
        final_agreed_price = 0.0

        # Inspect negotiation_history for the latest counter-offer entry
        history = deal.get("negotiation_history")
        if isinstance(history, list) and len(history) > 0:
            for entry in reversed(history):
                if isinstance(entry, dict):
                    offer_val = entry.get("offer") or entry.get("price") or entry.get("amount")
                    if offer_val is not None:
                        try:
                            parsed_val = float(offer_val)
                            if parsed_val > 0:
                                final_agreed_price = parsed_val
                                break
                        except (ValueError, TypeError):
                            pass

        # Fallback to current_price or agreed_price if negotiation_history tail is empty
        if final_agreed_price <= 0:
            if deal.get("current_price") is not None and float(deal.get("current_price")) > 0:
                final_agreed_price = float(deal.get("current_price"))
            elif deal.get("agreed_price") is not None and float(deal.get("agreed_price")) > 0:
                final_agreed_price = float(deal.get("agreed_price"))

        # Update database and in-memory deal object with the resolved latest price
        if final_agreed_price > 0 and db and hasattr(db, 'table'):
            deal["agreed_price"] = final_agreed_price
            deal["current_price"] = final_agreed_price
            try:
                await db.table("deals").update({
                    "agreed_price": final_agreed_price,
                    "current_price": final_agreed_price,
                    "updated_at": datetime.utcnow().isoformat()
                }).eq("id", deal_id_str).execute()
                logger.info(f"✅ Synchronized final agreed price for Deal {deal_id_str}: {final_agreed_price}")
            except Exception as sync_err:
                logger.warning(f"Failed to synchronize final agreed price in handle_deal_accept: {sync_err}")

        # 2. Resolve Seeker and Provider languages COMPLETELY independently from DB ground-truth
        s_lang = await _resolve_db_user_lang(seeker_chat_id,
                                             deal.get("seeker_lang")) if seeker_chat_id is not None else "en"
        p_lang = await _resolve_db_user_lang(provider_chat_id,
                                             deal.get("provider_lang")) if provider_chat_id is not None else "en"

        # Determine which user is performing the action and assign their isolated language
        acting_user_lang = p_lang if provider_chat_id is not None and user_id == provider_chat_id else s_lang
        context.user_data['acting_user_lang'] = acting_user_lang
        context.user_data['seeker_lang_cache'] = s_lang
        context.user_data['provider_lang_cache'] = p_lang

        # 3. Await execution helper and return valid integer state for ConversationHandler
        await self._execute_deal_acceptance(query, context, deal_id_str, deal)
        return getattr(self, "MAIN_MENU", 0)

    async def handle_deal_decline(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if not query or not query.data:
            return
        await query.answer()

        user_id = query.from_user.id
        lang = await self._get_user_lang(user_id, context)

        parts = str(query.data).split(":", 1)
        deal_id_str = parts[1] if len(parts) > 1 else None

        if not deal_id_str or deal_id_str == "None":
            await query.edit_message_text(
                get_text(lang, "error_transaction_invalid") or "⚠️ Deal not found."
            )
            return

        deal = await self.db.get_deal_by_id(deal_id_str)
        if not deal:
            await query.edit_message_text(
                get_text(lang, "error_transaction_invalid") or "⚠️ Deal not found."
            )
            return

        # Pass 'query' (CallbackQuery) to align with _execute_deal_termination parameter hints
        await self._execute_deal_termination(query, context, deal_id_str, deal)

    async def handle_deal_counter(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if not query or not query.data:
            return
        await query.answer()

        user_id = query.from_user.id
        lang = await self._get_user_lang(user_id, context)

        parts = str(query.data).split(":", 1)
        deal_id_str = parts[1] if len(parts) > 1 else None

        if not deal_id_str or deal_id_str == "None":
            await query.edit_message_text(get_text(lang, "error_transaction_invalid") or "⚠️ Deal not found.")
            return

        deal = await self.db.get_deal_by_id(deal_id_str)
        if not deal:
            await query.edit_message_text(get_text(lang, "error_transaction_invalid") or "⚠️ Deal not found.")
            return

        if user_id == int(deal.get("seeker_id", 0)):
            target_role = "seeker"
        elif user_id == int(deal.get("provider_id", 0)):
            target_role = "provider"
        else:
            await query.answer("⚠️ You are not authorized to counter this deal.", show_alert=True)
            return

        # Store exact deal_id in active_negotiation_id for counter-offer processing
        context.user_data['active_negotiation_id'] = deal_id_str
        context.user_data['negotiation_role'] = target_role
        context.user_data['awaiting_counter_input'] = True
        context.user_data['intent_to_counter'] = True

        await self._prompt_counter_value_entry(query, context, deal_id_str)

    async def handle_counter_offer_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Receives the numeric counter-offer input from the user and updates the existing deal session.
           Includes a smart fallback to automatically resolve active deals if the button click was missed."""
        if not update.message or not update.message.text:
            return 1

        text_input = update.message.text.strip()
        try:
            counter_price = float(text_input.replace(",", ""))
        except ValueError:
            await update.message.reply_text("⚠️ Please enter a valid numeric amount for your counter-offer.")
            return 1

        user_id = update.effective_user.id
        db = getattr(self, 'db', getattr(self, 'db_manager', None))
        deal_id_str = context.user_data.get("active_negotiation_id")

        # 🧠 SMART FALLBACK: If user forgot to click the "Counter Offer" button, automatically find their latest active deal
        if not deal_id_str and db:
            try:
                if hasattr(db, 'supabase') and db.supabase:
                    resp = await db.supabase.table('deals') \
                        .select('*') \
                        .eq('deal_state', 'negotiating') \
                        .or_(f"seeker_id.eq.{user_id},provider_id.eq.{user_id}") \
                        .order('updated_at', desc=True) \
                        .limit(1) \
                        .execute()
                    if resp and getattr(resp, 'data', None) and len(resp.data) > 0:
                        deal_data = resp.data[0]
                        deal_id_str = deal_data['id']
                        context.user_data['active_negotiation_id'] = deal_id_str
                        logger.info(
                            f"💡 Smart fallback resolved active deal {deal_id_str} for user {user_id} without button click.")
            except Exception as lookup_err:
                logger.debug(f"Smart deal lookup fallback failed: {lookup_err}")

        if not deal_id_str:
            await update.message.reply_text(
                "⚠️ No active negotiation session found. Please select 'Counter Offer' from your listing notification.")
            context.user_data.pop("active_negotiation_id", None)
            return ConversationHandler.END

        # 1. Fetch existing deal record to maintain history and roles
        try:
            deal = None
            if hasattr(db, 'get_deal_by_id'):
                deal = await db.get_deal_by_id(deal_id_str)
            elif hasattr(db, 'supabase'):
                resp = await db.supabase.table('deals').select('*').eq('id', deal_id_str).maybe_single().execute()
                if resp and getattr(resp, 'data', None):
                    deal = resp.data

            if not deal:
                await update.message.reply_text("⚠️ Deal session has expired or is invalid.")
                context.user_data.pop("active_negotiation_id", None)
                return ConversationHandler.END

            # 2. Update negotiation history and current price
            history = deal.get("negotiation_history") or []
            if isinstance(history, list):
                history.append({
                    "sender_id": user_id,
                    "offer": counter_price,
                    "timestamp": datetime.utcnow().isoformat()
                })

            update_payload = {
                'current_price': counter_price,
                'deal_state': 'negotiating',
                'negotiation_history': history,
                'updated_at': datetime.utcnow().isoformat()
            }

            if hasattr(db, 'supabase') and db.supabase:
                await db.supabase.table('deals').update(update_payload).eq('id', deal_id_str).execute()
            elif hasattr(db, 'update_deal_price'):
                await db.update_deal_price(deal_id_str, counter_price)

            # ✅ FIXED: Corrected f-string formatting syntax bug (*{counter_price:,.2f} ETB*)
            await update.message.reply_text(
                f"✅ Your counter-offer of *{counter_price:,.2f} ETB* has been submitted and sent to the counterparty!",
                parse_mode="Markdown"
            )

            # 3. Notify the counterpart directly without re-spawning a new negotiation handshake
            seeker_id = int(deal.get("seeker_id", 0))
            provider_id = int(deal.get("provider_id", 0))
            target_chat_id = provider_id if user_id == seeker_id else seeker_id

            if target_chat_id and hasattr(self, '_notify_counterparty'):
                await self._notify_counterparty(
                    chat_id=target_chat_id,
                    deal_id=deal_id_str,
                    deal=deal,
                    new_price=counter_price,
                    sender_role="Provider" if user_id == provider_id else "Seeker"
                )

        except Exception as e:
            logger.error(f"Failed to submit counter offer for deal {deal_id_str}: {e}")
            await update.message.reply_text("⚠️ Failed to record counter-offer. Please try again.")
            context.user_data.pop("active_negotiation_id", None)
            return ConversationHandler.END  # ✅ FIXED: Guaranteed state return on exception to prevent drop to None

        context.user_data.pop("active_negotiation_id", None)
        return ConversationHandler.END

    # ------------------------------------------------------------------------
    # INTERACTIVE UI HELPERS
    # ------------------------------------------------------------------------
    async def _prompt_counter_value_entry(self, query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE,
                                          deal_id_str: str) -> None:
        user_id = query.from_user.id
        raw_lang = await self._get_user_lang(user_id, context)
        lang = str(raw_lang).lower()[:2] if raw_lang else "en"

        currency = "ETB"
        try:
            deal = {}
            if hasattr(self.db, 'supabase') and self.db.supabase:
                resp = await self.db.supabase.table('deals').select('*').eq('id', deal_id_str).execute()
                if resp and getattr(resp, 'data', None):
                    deal = resp.data[0] if isinstance(resp.data, list) and len(resp.data) > 0 else {}
            elif hasattr(self.db, 'get_deal_by_id'):
                fetched_deal = await self.db.get_deal_by_id(deal_id_str)
                if isinstance(fetched_deal, dict):
                    deal = fetched_deal

            currency = str(deal.get("currency", "ETB")).upper()
            listing_id = deal.get("listing_id")
            if listing_id:
                if hasattr(self.db, 'supabase') and self.db.supabase:
                    l_resp = await self.db.supabase.table('provider_listings').select('currency').eq('id',
                                                                                                     listing_id).maybe_single().execute()
                    if l_resp and getattr(l_resp, 'data', None):
                        currency = str(l_resp.data.get('currency') or currency).upper()
                elif hasattr(self.db, 'get_listing_by_id'):
                    l_fetched = await self.db.get_listing_by_id(listing_id)
                    if isinstance(l_fetched, dict):
                        currency = str(l_fetched.get('currency') or currency).upper()
        except Exception as curr_err:
            logger.warning(f"Could not resolve currency for counter prompt: {curr_err}")

        prompt_template = get_text(lang, "prompt_enter_counter_price")
        if not prompt_template or prompt_template == "prompt_enter_counter_price":
            prompt_template = "💰 *Enter Counter Offer*\n\nPlease reply with your new price in {currency} (e.g., 3600000):"

        safe_currency = escape_md(currency)
        prompt_text = prompt_template.format(
            currency=safe_currency) if "{currency}" in prompt_template else prompt_template.replace("ETB",
                                                                                                    safe_currency)
        cancel_btn = get_text(lang, "btn_cancel_counter") or "❌ Cancel"

        reply_markup = InlineKeyboardMarkup(
            [[InlineKeyboardButton(cancel_btn, callback_data=f"cancel_counter_offer:{deal_id_str}")]]
        )

        try:
            await query.edit_message_caption(
                caption=prompt_text,
                reply_markup=reply_markup,
                parse_mode="Markdown"
            )
        except Exception:
            try:
                await query.edit_message_text(
                    text=prompt_text,
                    reply_markup=reply_markup,
                    parse_mode="Markdown"
                )
            except Exception:
                await context.bot.send_message(
                    chat_id=query.message.chat.id if query.message and query.message.chat else query.from_user.id,
                    text=prompt_text,
                    reply_markup=reply_markup,
                    parse_mode="HTML"
                )

    # ========================================================================
    # SESSION STATE PURGING HELPER
    # ========================================================================
    @staticmethod
    def _clear_negotiation_state(user_data: dict) -> None:
        """
        Purges all transient negotiation, deal, route, and listing state keys
        from context.user_data to permanently prevent session state pollution.
        """
        if not isinstance(user_data, dict):
            return

        keys_to_purge = [
            "negotiation_role", "awaiting_counter_input", "intent_to_counter",
            "current_state", "@", "is_processing_job", "pending_deal_id",
            "active_negotiation_id", "active_deal_id", "deal_id",
            "origin", "destination", "origin_text", "destination_text",
            "from_location", "to_location", "route", "route_string", "path",
            "cargo_weight_kgs", "cargo_weight", "weight_kgs", "weight",
            "vehicle_plate", "plate_number", "plate", "license_plate", "plate_no",
            "listing_id", "request_id", "seeker_request_id",
            "vertical_type", "transaction_type", "party_size", "passengers",
            "seats", "brand", "property_use", "equipment_type", "vehicle_type",
            "make_model_year"
        ]

        for key in keys_to_purge:
            user_data.pop(key, None)

    # ========================================================================
    # HELPER: ANTI-LANGUAGE & CROSS-VERTICAL MIXUP SANITIZER
    # ========================================================================
    @staticmethod
    def _clean_asset_title_language_mixup(title: str, vertical_type: str = "general") -> str:
        """
        Removes hardcoded inline Oromo/Amharic label artifacts and strictly isolates
        cross-vertical contamination (e.g., purging Freight routes from Equipment).
        """
        if not title:
            return ""
        cleaned = str(title).strip()
        v_type = str(vertical_type).lower().strip()

        # 1. Strip language specific system mixups
        cleaned = re.sub(r"(?:Fe'umsaa|ጭነት|Freight|Imala|ራይድ|Ride)\s*-\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\(Gabatee:[^)]+\)", "", cleaned, flags=re.IGNORECASE)

        # 2. STRICT VERTICAL ISOLATION - The "Forever" Fix
        # Strip Route/Direction from NON-Freight and NON-Ride
        if v_type not in ["freight", "ride", "freight_ride", "cargo", "passenger"]:
            cleaned = re.sub(r"📍\s*(?:Karaa|መስመር|Route|Location)\s*:[^\n]*", "", cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(r"\([^)]*➡️[^)]*\)", "", cleaned)
            cleaned = cleaned.replace("➡️", "")

        # Strip Weight/Cargo from NON-Freight
        if v_type not in ["freight", "freight_ride", "cargo", "logistics"]:
            cleaned = re.sub(r"⚖️\s*(?:Ulfaatina|ክብደት|Weight|Cargo)\s*:[^\n]*", "", cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(r"\b\d+(?:\.\d+)?\s*(?:kg|kgs|ton|tons|quintal|t)\b", "", cleaned, flags=re.IGNORECASE)

        # Strip Passengers/Seats from NON-Ride
        if v_type not in ["ride", "passenger", "taxi", "freight_ride"]:
            cleaned = re.sub(r"👥\s*(?:Baay'ina.*|የሰው.*|Party.*|Imaltoota|ተሳፋሪዎች|Seats)\s*:[^\n]*", "", cleaned,
                             flags=re.IGNORECASE)
            cleaned = re.sub(r"\b\d+\s*(?:seats|passengers|pax)\b", "", cleaned, flags=re.IGNORECASE)

        # Cleanup leftover empty brackets and loose formatting
        cleaned = re.sub(r"\(\s*\)", "", cleaned)
        cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" -:,")
        return cleaned

    # ========================================================================
    # HELPER: Weight Extraction from required_amenities
    # ========================================================================
    @staticmethod
    def extract_weight_from_amenities(amenities_data: Any) -> float:
        if not amenities_data:
            return 0.0
        text = str(amenities_data)
        match = re.search(r'weight\s*[:=]\s*([\d.]+)', text, re.IGNORECASE)
        if match:
            try:
                return float(match.group(1))
            except (ValueError, TypeError):
                pass
        return 0.0

    # ------------------------------------------------------------------------
    # THE CONTEXT-AWARE INTERCEPTOR (MULTI-VERTICAL LANGUAGE-MIXUP FIX)
    # ------------------------------------------------------------------------
    async def process_counter_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message or not update.message.text:
            return

        user_id = update.effective_user.id
        text_payload = update.message.text.strip()
        user_data_ref = context.user_data

        is_filling_form = (user_data_ref.get("current_state") is not None or len(
            user_data_ref.get("@", {})) > 0 or user_data_ref.get("is_processing_job") is True)
        is_menu_click = text_payload.startswith('/') or any(
            char in text_payload for char in ['🔎', '🏠', '🚗', '🚘', '🚜', '🏗️', '🛠', '📦', '📱', '⚙️', '🔙'])

        if is_menu_click:
            self._clear_negotiation_state(user_data_ref)
            return

        if is_filling_form:
            return

        # --------------------------------------------------------------------
        # FLEXIBLE NUMERIC & CURRENCY PARSER
        # --------------------------------------------------------------------
        text_upper = text_payload.upper()

        # 1. Detect explicit currency provided in text payload (if any)
        explicit_currency = None
        if any(token in text_upper for token in ["USD", "$", "DOLLAR"]):
            explicit_currency = "USD"
        elif any(token in text_upper for token in ["ETB", "BIRR", "BR", "ብር"]):
            explicit_currency = "ETB"

        # 2. Clean text payload to extract pure numeric value
        clean_text = re.sub(r'(?i)\b(?:usd|etb|birr|br|dollars?|ብር)\b|[$€£,]', '', text_payload)
        clean_text = re.sub(r'(?i)(usd|etb|birr|br|ብር)', '', clean_text).strip()
        clean_numeric = re.sub(r'(\d)\s+(\d)', r'\1\2', clean_text).strip()

        is_numeric = False
        try:
            parsed_val = float(clean_numeric)
            if parsed_val > 0:
                is_numeric = True
        except (ValueError, TypeError):
            is_numeric = False

        deal_id_str = user_data_ref.get("active_negotiation_id")
        role = user_data_ref.get("negotiation_role")
        awaiting_counter = user_data_ref.get("awaiting_counter_input", False)
        intent_to_counter = user_data_ref.get("intent_to_counter", False)

        is_valid_deal_state = bool(deal_id_str and deal_id_str != "None" and role and awaiting_counter)

        # Helper to normalize language code strictly
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

        # Robust database language resolver aligned with users table schema
        async def get_db_user_lang(uid: Any, default_lang: str = "en") -> str:
            if not uid:
                return default_lang
            try:
                int_uid = int(uid)
                database = getattr(self, 'db', getattr(self, 'db_manager', None))
                if database and hasattr(database, 'table'):
                    response = await database.table('users').select(
                        'preferred_language, language_code, language').eq(
                        'telegram_id', int_uid
                    ).maybe_single().execute()
                    if response and getattr(response, 'data', None):
                        db_data = response.data
                        for col in ['preferred_language', 'language_code', 'language']:
                            norm = _normalize_lang(db_data.get(col))
                            if norm:
                                return norm
            except Exception as err:
                logger.warning(f"Could not fetch user language from DB for {uid}: {err}")
            return default_lang

        # Database-First Ground-Truth Language Resolution
        db_user_lang = await get_db_user_lang(user_id, default_lang="")
        session_lang = (
                user_data_ref.get(f'lang_{user_id}') or
                user_data_ref.get('language_code') or
                user_data_ref.get('language') or
                user_data_ref.get('preferred_language')
        )
        lang = _normalize_lang(db_user_lang) or _normalize_lang(session_lang) or "en"

        if not is_valid_deal_state:
            if is_numeric and intent_to_counter:
                self._clear_negotiation_state(user_data_ref)
                conflict_msg = get_text(lang, "err_deal_blocked_conflict")
                if not conflict_msg or conflict_msg == "err_deal_blocked_conflict":
                    conflict_msg = "⚠️ *Negotiation Blocked*\n\nYour session expired or another user is interacting. Please refresh."

                await update.message.reply_text(conflict_msg, parse_mode="Markdown")
                raise ApplicationHandlerStop()
            return

        try:
            counter_price = float(clean_numeric)
            if counter_price <= 0:
                raise ValueError()
        except ValueError:
            err_invalid = get_text(lang, "error_invalid_numeric_input")
            if not err_invalid or err_invalid == "error_invalid_numeric_input":
                err_invalid = "❌ Invalid amount. Please enter a valid number."

            await update.message.reply_text(text=err_invalid, parse_mode="Markdown")
            raise ApplicationHandlerStop()

        try:
            deal = {}
            if hasattr(self.db, 'supabase') and self.db.supabase:
                resp = await self.db.supabase.table('deals').select('*').eq('id', deal_id_str).execute()
                if resp and getattr(resp, 'data', None):
                    deal = resp.data[0] if isinstance(resp.data, list) and len(resp.data) > 0 else {}
            elif hasattr(self.db, 'get_deal_by_id'):
                fetched_deal = await self.db.get_deal_by_id(deal_id_str)
                if isinstance(fetched_deal, dict):
                    deal = fetched_deal

            if not deal or deal.get("deal_state") != "negotiating":
                expired_msg = get_text(lang, "err_deal_expired")
                if not expired_msg or expired_msg == "err_deal_expired":
                    expired_msg = "⚠️ This deal has expired or is no longer active."

                await update.message.reply_text(expired_msg, parse_mode="Markdown")
                self._clear_negotiation_state(user_data_ref)
                raise ApplicationHandlerStop()

            listing_id = deal.get("listing_id")
            listing = {}

            if listing_id:
                try:
                    if hasattr(self.db, 'supabase') and self.db.supabase:
                        l_resp = await self.db.supabase.table('provider_listings').select('*').eq('id',
                                                                                                  listing_id).execute()
                        if l_resp and getattr(l_resp, 'data', None):
                            listing = l_resp.data[0] if isinstance(l_resp.data, list) and len(
                                l_resp.data) > 0 else {}
                    elif hasattr(self.db, 'get_listing_by_id'):
                        fetched_listing = await self.db.get_listing_by_id(listing_id)
                        if isinstance(fetched_listing, dict):
                            listing = fetched_listing
                except Exception as l_err:
                    logger.warning(f"Could not fetch listing details for currency resolution: {l_err}")

            # Currency preference: Explicit user message currency > Listing currency > Deal currency > Default (ETB)
            currency = str(explicit_currency or listing.get('currency') or deal.get('currency') or "ETB").upper()

            vertical_type = str(
                deal.get("vertical_type") or listing.get("vertical_type") or listing.get("type",
                                                                                         "asset")).lower().strip()

            asset_title = deal.get("asset_title") or self.format_asset_title(listing, vertical_type)

            history_entry = {
                'actor': user_id,
                'offer': counter_price,
                'time': str(datetime.now()),
                'action': 'counter_offer',
                'currency': currency
            }

            update_payload = {
                'current_price': counter_price,
                'currency': currency,
                'negotiation_history': deal.get('negotiation_history', []) + [history_entry],
                'deal_state': 'negotiating',
                'updated_at': datetime.utcnow().isoformat()
            }

            if hasattr(self.db, 'supabase') and self.db.supabase:
                await self.db.supabase.table('deals').update(update_payload).eq('id', deal_id_str).execute()
            elif hasattr(self.db, 'update_negotiation_state'):
                await self.db.update_negotiation_state(
                    str(deal_id_str), "negotiating", counter_price, update_payload['negotiation_history']
                )

            raw_target_id = deal.get("seeker_id") if role == "provider" else deal.get("provider_id")
            target_chat_id = None

            try:
                target_chat_id = int(raw_target_id)
            except (ValueError, TypeError):
                try:
                    user_res = await self.db.supabase.table("users").select("telegram_id").eq("id",
                                                                                              raw_target_id).maybe_single().execute()
                    if user_res and hasattr(user_res, 'data') and user_res.data:
                        target_chat_id = int(user_res.data.get("telegram_id"))
                except Exception as id_err:
                    logger.warning(f"Could not resolve Telegram ID for {raw_target_id}: {id_err}")

            if not target_chat_id:
                err_unavail = get_text(lang, "err_target_unavailable")
                if not err_unavail or err_unavail == "err_target_unavailable":
                    err_unavail = "⚠️ Could not deliver offer. Target user is unavailable."
                await update.message.reply_text(err_unavail, parse_mode="Markdown")
                self._clear_negotiation_state(user_data_ref)
                raise ApplicationHandlerStop()

            # Strict Database Ground-Truth Resolution for Target User Language
            db_target_lang = await get_db_user_lang(target_chat_id, default_lang="")
            target_lang = _normalize_lang(db_target_lang) or await self._get_target_user_lang_isolated(
                target_chat_id) or "en"

            user_data_ref["lang"] = lang
            user_data_ref["language"] = lang

            source_actor = "provider" if role == "provider" else "seeker"
            translated_actor = get_text(target_lang, f"role_{source_actor}")
            if not translated_actor or translated_actor == f"role_{source_actor}":
                translated_actor = source_actor.title()

            v_lower = str(vertical_type).lower().strip()
            v_key = f"vertical_{v_lower}"
            translated_vertical = get_text(target_lang, v_key)
            if not translated_vertical or translated_vertical == v_key:
                if target_lang == "om":
                    vertical_translations = {
                        "housing": "Manni / Manneen",
                        "vehicle": "Konkolaataa",
                        "freight": "Fe'umsaa / Geejjiba",
                        "equipment": "Meeshaalee / Qabeenya",
                        "ride": "Imala",
                        "asset": "Qabeenya"
                    }
                elif target_lang == "am":
                    vertical_translations = {
                        "housing": "ቤት / መኖሪያ",
                        "vehicle": "ተሽከርካሪ",
                        "freight": "ዕቃ ማጓጓዣ",
                        "equipment": "መሳሪያዎች",
                        "ride": "ራይድ",
                        "asset": "ንብረት"
                    }
                else:
                    vertical_translations = {
                        "housing": "Housing",
                        "vehicle": "Vehicle",
                        "freight": "Freight",
                        "equipment": "Equipment Rental",
                        "ride": "Ride",
                        "asset": "Asset"
                    }
                translated_vertical = vertical_translations.get(v_lower, v_lower.upper())

            def translate_property_type(raw_type: str, user_lang: str = "en") -> str:
                """
                Translates raw housing/property types into English, Amharic, or Afaan Oromo.
                """
                if not raw_type:
                    return ""

                clean_key = str(raw_type).strip().lower()

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
                    return translations[clean_key].get(user_lang, translations[clean_key]["en"])

                # Partial substring check for terms like "Commercial Building" or "Residential Villa"
                if "commercial plot" in clean_key:
                    return translations["commercial plot"].get(user_lang, "Commercial Plot")
                if "residential plot" in clean_key:
                    return translations["residential plot"].get(user_lang, "Residential Plot")
                if "commercial" in clean_key:
                    return translations["commercial"].get(user_lang, "Commercial")
                if "residential" in clean_key:
                    return translations["residential"].get(user_lang, "Residential")

                return str(raw_type).title()

            # --------------------------------------------------------------------
            # PROPERTY USE & ASSET TYPE NORMALIZATION & LOCALIZATION HELPER
            # --------------------------------------------------------------------
            def translate_property_use(use_str: str, lang_code: str) -> str:
                clean_use = str(use_str).lower().replace('use_', '').replace('request for ', '').replace('in ',
                                                                                                         ' ').replace(
                    '_', ' ').strip()

                mapping = {
                    'commercial_plot': {'om': 'Lafa Daldalaa', 'am': 'የንግድ ቦታ', 'en': 'Commercial Plot'},
                    'commercial plot': {'om': 'Lafa Daldalaa', 'am': 'የንግድ ቦታ', 'en': 'Commercial Plot'},
                    'residential_plot': {'om': 'Lafa Jireenyaa', 'am': 'መኖሪያ ቦታ', 'en': 'Residential Plot'},
                    'residential plot': {'om': 'Lafa Jireenyaa', 'am': 'መኖሪያ ቦታ', 'en': 'Residential Plot'},
                    'commercial': {'om': 'Daldalaa', 'am': 'ንግድ', 'en': 'Commercial'},
                    'residential': {'om': 'Jireenyaa', 'am': 'መኖሪያ', 'en': 'Residential'},
                    'apartment': {'om': 'Mana Appartimentii', 'am': 'አፓርታማ', 'en': 'Apartment'},
                    'villa': {'om': 'Villa', 'am': 'ቪላ', 'en': 'Villa'},
                    'office': {'om': 'Ofisii', 'am': 'ቢሮ', 'en': 'Office'},
                    'shop': {'om': 'Suuqii', 'am': 'ሱቅ', 'en': 'Shop'},
                    'warehouse': {'om': 'Kuusaa', 'am': 'መጋዘን', 'en': 'Warehouse'}
                }

                if clean_use in mapping:
                    return mapping[clean_use].get(lang_code, mapping[clean_use]['en'])

                return clean_use.title()

            # --------------------------------------------------------------------
            # MULTI-VERTICAL LANGUAGE MIXUP SANITIZATION
            # --------------------------------------------------------------------
            raw_asset_title = str(asset_title or "").strip()

            # Normalize and translate property type slugs (e.g. commercial_plot -> Lafa Daldalaa / የንግድ ቦታ)
            if hasattr(self, 'translate_property_type'):
                raw_asset_title = self.translate_property_type(raw_asset_title, target_lang)
            elif 'translate_property_type' in globals():
                raw_asset_title = translate_property_type(raw_asset_title, target_lang)
            else:
                raw_asset_title = translate_property_use(raw_asset_title, target_lang)

            PREFIX_MAP = {
                "housing": {"am": ["ቤት", "መኖሪያ"], "om": ["Mana", "Manni"], "en": ["Housing", "House"]},
                "vehicle": {"am": ["ተሽከርካሪ", "መኪና"], "om": ["Konkolaataa"], "en": ["Vehicle", "Car"]},
                "equipment": {"am": ["መሳሪያዎች", "ዕቃዎች"], "om": ["Meeshaalee", "Meeshaa"], "en": ["Equipment"]},
                "freight": {"am": ["ዕቃ ማጓጓዣ", "ጭነት"], "om": ["Fe'umsaa", "Fe'umsa", "Geejjiba"],
                            "en": ["Freight", "Cargo"]},
                "ride": {"am": ["ራይድ"], "om": ["Imala"], "en": ["Ride"]},
                "asset": {"am": ["ንብረት"], "om": ["Qabeenya"], "en": ["Asset"]}
            }

            for vert, lang_map in PREFIX_MAP.items():
                for l_code, prefixes in lang_map.items():
                    if l_code == target_lang:
                        continue
                    for pfx in prefixes:
                        pattern = rf"^{re.escape(pfx)}\b\s*[-:]?\s*"
                        if re.search(pattern, raw_asset_title, re.IGNORECASE):
                            target_pfx = PREFIX_MAP[vert].get(target_lang, PREFIX_MAP[vert]["en"])[0]
                            raw_asset_title = re.sub(pattern, f"{target_pfx} ", raw_asset_title,
                                                     flags=re.IGNORECASE)
                            break

            # Scrub embedded cross-language & cross-vertical attribute lines
            if target_lang == "am":
                raw_asset_title = re.sub(r"👥\s*Imaltoota\s*:\s*\d+", "", raw_asset_title, flags=re.IGNORECASE)
                raw_asset_title = re.sub(r"🏷️\s*Gosa\s+Daldalaa\s*:[^\n]*", "", raw_asset_title,
                                         flags=re.IGNORECASE)
                raw_asset_title = re.sub(r"🏠\s*Qabeenya\s*:[^\n]*", "", raw_asset_title, flags=re.IGNORECASE)
            elif target_lang == "om":
                raw_asset_title = re.sub(r"👥\s*ተሳፋሪዎች\s*:\s*\d+", "", raw_asset_title)
                raw_asset_title = re.sub(r"🏷️\s*ግብይት\s*:[^\n]*", "", raw_asset_title)
                raw_asset_title = re.sub(r"🏠\s*ንብረት\s*:[^\n]*", "", raw_asset_title)
            elif target_lang == "en":
                raw_asset_title = re.sub(r"👥\s*(?:Imaltoota|ተሳፋሪዎች)\s*:\s*\d+", "", raw_asset_title,
                                         flags=re.IGNORECASE)
                raw_asset_title = re.sub(r"🏷️\s*(?:Gosa\s+Daldalaa|ግብይት)\s*:[^\n]*", "", raw_asset_title,
                                         flags=re.IGNORECASE)

            # Prevent vertical leakage in cross-language sweeps
            if v_lower not in ["freight", "ride", "freight_ride"]:
                raw_asset_title = re.sub(r"📍\s*(?:Route|Karaa|መስመር)\s*:[^\n]*", "", raw_asset_title,
                                         flags=re.IGNORECASE)
                raw_asset_title = re.sub(r"\([^)]*➡️[^)]*\)", "", raw_asset_title)
                raw_asset_title = raw_asset_title.replace("➡️", "")

            if v_lower not in ["ride", "passenger"]:
                raw_asset_title = re.sub(r"👥\s*(?:Party Size|Imaltoota|ተሳፋሪዎች)\s*:[^\n]*", "", raw_asset_title,
                                         flags=re.IGNORECASE)

            if v_lower not in ["freight", "freight_ride", "cargo"]:
                raw_asset_title = re.sub(r"⚖️\s*(?:Cargo Weight|Ulfaatina|ክብደት)\s*:[^\n]*", "", raw_asset_title,
                                         flags=re.IGNORECASE)

            raw_asset_title = re.sub(r"\s+", " ", raw_asset_title).strip(" -:")

            safe_asset_title = escape_md(raw_asset_title)
            safe_translated_actor = escape_md(str(translated_actor or ""))
            safe_translated_vertical = escape_md(str(translated_vertical or ""))
            safe_currency = escape_md(str(currency or "ETB"))
            safe_price = escape_md(f"{counter_price:,.2f}")

            notice_template = get_text(target_lang, "notice_counter_received")
            if not notice_template or notice_template == "notice_counter_received":
                notice_template = "🔄 *Negotiation Update*\n\n📂 *Type:* {vertical_type}\n🏠 *Asset:* _{asset_title}_\n👤 *From:* {source_actor}\n💰 *Counter:* *{price} {currency}*"

            opposing_notice = notice_template.format(
                vertical_type=safe_translated_vertical,
                asset_title=safe_asset_title,
                source_actor=safe_translated_actor,
                price=safe_price,
                currency=safe_currency
            ) if "{currency}" in notice_template else notice_template.format(
                vertical_type=safe_translated_vertical,
                asset_title=safe_asset_title,
                source_actor=safe_translated_actor,
                price=safe_price
            ).replace("ETB", safe_currency)

            # Post-Formatting Purity Sweep
            if target_lang == "am":
                opposing_notice = re.sub(r"👥\s*imaltoota\s*:\s*\d+\s*\n?", "", opposing_notice, flags=re.IGNORECASE)
                opposing_notice = re.sub(r"🏷️\s*gosa daldalaa\s*:[^\n]*\n?", "", opposing_notice,
                                         flags=re.IGNORECASE)
                opposing_notice = re.sub(r"\bImala\b", "ራይድ", opposing_notice, flags=re.IGNORECASE)
                opposing_notice = re.sub(r"\bKonkolaataa\b", "ተሽከርካሪ", opposing_notice, flags=re.IGNORECASE)
                opposing_notice = re.sub(r"\bMeeshaalee\b", "መሳሪያዎች", opposing_notice, flags=re.IGNORECASE)
                opposing_notice = re.sub(r"\bFe'umsaa\b", "ዕቃ ማጓጓዣ", opposing_notice, flags=re.IGNORECASE)
                opposing_notice = re.sub(r"\bManni\b", "ቤት", opposing_notice, flags=re.IGNORECASE)
            elif target_lang == "om":
                opposing_notice = re.sub(r"👥\s*ተሳፋሪዎች\s*:\s*\d+\s*\n?", "", opposing_notice)
                opposing_notice = re.sub(r"🏷️\s*ግብይት\s*:[^\n]*\n?", "", opposing_notice)
                opposing_notice = re.sub(r"\bራይድ\b", "Imala", opposing_notice)
                opposing_notice = re.sub(r"\bተሽከርካሪ\b", "Konkolaataa", opposing_notice)
                opposing_notice = re.sub(r"\bመሳሪያዎች\b", "Meeshaalee", opposing_notice)
                opposing_notice = re.sub(r"\bጭነት\b", "Fe'umsaa", opposing_notice)
                opposing_notice = re.sub(r"\bመኖሪያ\b|\bቤት\b", "Manni", opposing_notice)

            btn_accept = get_text(target_lang, "btn_accept_price")
            btn_accept = btn_accept if btn_accept and btn_accept != "btn_accept_price" else "✅ Accept Price"

            btn_counter = get_text(target_lang, "btn_counter_offer")
            btn_counter = btn_counter if btn_counter and btn_counter != "btn_counter_offer" else "📉 Counter Offer"

            btn_decline = get_text(target_lang, "btn_decline_deal")
            btn_decline = btn_decline if btn_decline and btn_decline != "btn_decline_deal" else "❌ Decline Deal"

            keyboard = [
                [
                    InlineKeyboardButton(btn_accept, callback_data=f"deal_accept:{deal_id_str}"),
                    InlineKeyboardButton(btn_counter, callback_data=f"deal_counter:{deal_id_str}")
                ],
                [InlineKeyboardButton(btn_decline, callback_data=f"deal_decline:{deal_id_str}")]
            ]

            current_user_lang = lang or 'en'
            success_msg_temp = get_text(current_user_lang, "msg_counter_sent")

            is_corrupted_amharic = (current_user_lang == "am" and "dhiheessiin" in str(success_msg_temp).lower())

            if not success_msg_temp or success_msg_temp == "msg_counter_sent" or is_corrupted_amharic:
                if current_user_lang == "am":
                    success_msg_temp = "✅ የመወዳደሪያ ዋጋዎ *{price} {currency}* በተሳካ ሁኔታ ተልኳል!"
                elif current_user_lang == "om":
                    success_msg_temp = "✅ Dhiheessiin kee dhiyeessaaf ergameera: *{price} {currency}*"
                else:
                    success_msg_temp = "✅ Counter offer of *{price} {currency}* sent successfully!"

            formatted_success_msg = (
                success_msg_temp.format(price=safe_price, currency=safe_currency)
                if "{currency}" in success_msg_temp
                else success_msg_temp.format(price=safe_price).replace("ETB", safe_currency)
            )

            await update.message.reply_text(
                text=formatted_success_msg,
                parse_mode="Markdown"
            )

            try:
                await context.bot.send_message(
                    chat_id=target_chat_id,
                    text=opposing_notice,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="Markdown"
                )
            except TelegramError as send_err:
                logger.warning(
                    f"Direct message to {target_chat_id} failed ({send_err}). Storing in persistent queue.")
                target_user_data = context.application.user_data.setdefault(target_chat_id, {})
                target_user_data.setdefault("notification_queue", []).append({
                    "text": opposing_notice,
                    "reply_markup": InlineKeyboardMarkup(keyboard)
                })

            self._clear_negotiation_state(user_data_ref)
            raise ApplicationHandlerStop()

        except ApplicationHandlerStop:
            raise
        except Exception as msg_err:
            logger.error(f"Failed to process counter: {str(msg_err)}", exc_info=True)
            err_unexp = get_text(lang, "err_unexpected")
            if not err_unexp or err_unexp == "err_unexpected":
                err_unexp = "⚠️ An unexpected error occurred."

            await update.message.reply_text(err_unexp, parse_mode="Markdown")
            self._clear_negotiation_state(user_data_ref)
            raise ApplicationHandlerStop()

    # ========================================================================
    # DEAL INITIATION PIPELINE (FORWARD & REVERSE BIDIRECTIONAL MATCHING)
    # ========================================================================

    async def initiate_negotiation(
            self,
            seeker_id: int,
            provider_id: int,
            listing_id: str,
            vertical_type: Optional[str] = None,
            initial_offer: float = 0.0,
            request_id: Optional[str] = None,  # <--- Request ID accepted here
            asset_title: Optional[str] = None,
            transaction_type: Optional[str] = None,
            is_reverse: bool = False,
            bot: Optional[Any] = None,
            notify: Optional[bool] = None,
            **_kwargs: Any
    ) -> Optional[str]:
        context = _kwargs.get('context')
        silent = _kwargs.get('silent', False)
        notify_peer = _kwargs.get('notify_peer', True)
        notify_seeker = _kwargs.get('notify_seeker', True)
        currency = _kwargs.get('currency', 'ETB')

        # =========================================================================
        # 🛡️ PERMANENT GUARD: Prevent Deal IDs from violating provider_listings fkey
        # =========================================================================
        db_client = getattr(self, 'db', getattr(self, 'db_manager', None))
        supabase_client = getattr(db_client, 'supabase', None) if db_client else getattr(self, 'supabase', None)

        resolved_listing_id = listing_id
        if supabase_client and resolved_listing_id:
            try:
                # Check if the ID exists in provider_listings
                listing_check = await supabase_client.table("provider_listings").select("id").eq("id",
                                                                                                 resolved_listing_id).maybe_single().execute()
                if not listing_check or not listing_check.data:
                    # It's not a listing ID! Check if it's an existing Deal ID
                    deal_lookup = await supabase_client.table("deals").select("listing_id").eq("id",
                                                                                               resolved_listing_id).maybe_single().execute()
                    if deal_lookup and deal_lookup.data and deal_lookup.data.get("listing_id"):
                        resolved_listing_id = deal_lookup.data.get("listing_id")
                        logger.info(
                            f"🛡️ Guard successfully resolved actual listing_id {resolved_listing_id} from passed Deal ID {listing_id}"
                        )
                    else:
                        logger.error(
                            f"❌ Foreign Key Error: ID '{resolved_listing_id}' is neither a valid listing nor an existing deal."
                        )
                        return None
            except Exception as guard_err:
                logger.warning(f"Listing ID validation guard warning: {guard_err}")

        # 💡 SMART REQUEST ID RESOLUTION: Auto-resolve seeker request if not explicitly provided
        if not request_id and supabase_client:
            try:
                resolved_vertical = vertical_type or _kwargs.get('vertical') or 'general'
                req_res = (
                    await supabase_client.table('seeker_requests')
                    .select('id')
                    .eq('seeker_id', seeker_id)
                    .eq('vertical_type', resolved_vertical)
                    .order('created_at', desc=True)
                    .limit(1)
                    .execute()
                )
                if req_res and req_res.data:
                    request_id = req_res.data[0].get('id')
                    logger.info(
                        f"💡 Auto-resolved seeker request ID {request_id} for deal initiation (Seeker: {seeker_id})")
            except Exception as req_err:
                logger.debug(f"Could not auto-resolve request_id during initiation: {req_err}")

        # ⚡ SMART NOTIFICATION RESOLUTION: Check explicit notify, silent flags, or handlers
        if notify is not None:
            should_notify = notify
        elif 'notify' in _kwargs:
            should_notify = _kwargs['notify']
        elif silent or (not notify_peer and not notify_seeker):
            should_notify = False
        elif context is not None:
            should_notify = _kwargs.get('notify', False)
        else:
            should_notify = True

        if context and hasattr(context, 'user_data') and isinstance(context.user_data, dict):
            self._clear_negotiation_state(context.user_data)

        resolved_bot = bot or _kwargs.get('bot')
        if not resolved_bot:
            if context and hasattr(context, 'bot') and context.bot:
                resolved_bot = context.bot
            elif hasattr(self, 'bot') and self.bot:
                resolved_bot = self.bot
            elif hasattr(self, 'telegram_app') and self.telegram_app:
                resolved_bot = getattr(self.telegram_app, 'bot', None)

        # 🛡️ ROBUST LOGGING & BOT AWARENESS: Suppress warnings to DEBUG when notifications are disabled
        if not resolved_bot:
            if should_notify:
                logger.error(
                    "Telegram bot instance could not be resolved in initiate_negotiation while notifications are enabled."
                )
            else:
                logger.debug(
                    f"Telegram bot instance not resolved for listing {resolved_listing_id}, "
                    "but notifications are disabled (notify=False / silent). Skipping safely."
                )

        is_reverse_match = bool(
            is_reverse
            or _kwargs.get('is_reverse')
            or _kwargs.get('is_reverse_match')
            or str(_kwargs.get('match_direction', '')).lower() == 'reverse'
            or str(_kwargs.get('match_type', '')).lower() == 'reverse'
            or str(_kwargs.get('direction', '')).lower() == 'reverse'
        )

        match_label = "Reverse Match" if is_reverse_match else "Forward Match"
        logger.info(
            f"Spawning deal negotiation ({match_label}): Seeker {seeker_id} <-> Provider {provider_id} on Listing {resolved_listing_id} (notify={should_notify})"
        )

        if str(seeker_id) == str(provider_id):
            logger.warning(
                f"Blocked self-offer attempt: User {seeker_id} attempted to offer on own listing {resolved_listing_id}"
            )
            lang = str(_kwargs.get('lang') or _kwargs.get('language') or 'en').lower().strip()
            update = _kwargs.get('update')
            if lang in ['om', 'or', 'oromoo']:
                self_offer_err = "⚠️ Qabeenya keessan irratti gatii dhiheessuu hin dandeessan."
            elif lang in ['am', 'amharic']:
                self_offer_err = "⚠️ በራስዎ ንብረት ላይ ዋጋ ማቅረብ አይችሉም።"
            else:
                self_offer_err = "⚠️ You cannot submit an offer on your own listing."
            if update and hasattr(update, 'message') and update.message:
                await update.message.reply_text(self_offer_err)
            return None

        # ✅ FIXED: Define safe_initial_offer early so it is available for all deal status checks & updates
        safe_initial_offer = float(initial_offer or 0.0)

        # =====================================================================
        # 🔒 ROBUST EXISTING DEAL STATUS & PRICE SYNC RESOLUTION
        # =====================================================================
        existing_deal = None
        deal_id_str = None

        if supabase_client and resolved_listing_id:
            try:
                existing_res = await supabase_client.table('deals').select('*').eq('listing_id',
                                                                                   resolved_listing_id).eq(
                    'seeker_id', seeker_id).eq('provider_id', provider_id).order('created_at', desc=True).limit(
                    1).maybe_single().execute()
                if existing_res and existing_res.data:
                    existing_deal = existing_res.data
            except Exception as lookup_err:
                logger.warning(f"Could not check existing deal for listing {resolved_listing_id}: {lookup_err}")

        # If an existing pending/negotiating deal is found, update it; otherwise create a new pending deal
        if existing_deal and existing_deal.get('deal_state') in ['pending', 'negotiating']:
            deal_id_str = existing_deal.get('id')
            try:
                update_payload = {
                    "current_price": safe_initial_offer,
                    "agreed_price": safe_initial_offer,
                    "updated_at": datetime.utcnow().isoformat()
                }
                if currency:
                    update_payload["currency"] = currency
                if asset_title:
                    update_payload["asset_title"] = asset_title

                await supabase_client.table('deals').update(update_payload).eq('id', deal_id_str).execute()
                logger.info(
                    f"🔄 Active or pending deal {deal_id_str} found for listing {resolved_listing_id}. Updated price to {safe_initial_offer}.")
                return deal_id_str
            except Exception as update_err:
                logger.warning(f"Failed to update existing deal {deal_id_str}: {update_err}")

        # Proceed with safe deal insertion using the verified resolved_listing_id
        payload = {
            "seeker_id": seeker_id,
            "provider_id": provider_id,
            "listing_id": resolved_listing_id,
            "vertical_type": vertical_type or _kwargs.get('domain', 'freight'),
            "current_price": safe_initial_offer,
            "agreed_price": safe_initial_offer,
            "currency": currency,
            "asset_title": asset_title or "Transport Asset",
            "deal_state": "negotiating",
            "status": "pending",
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat()
        }

        try:
            if db_client and hasattr(db_client, 'create_pending_deal'):
                response = await db_client.create_pending_deal(payload)
                return response
            elif supabase_client:
                res = await supabase_client.table('deals').insert(payload).execute()
                if res and res.data:
                    return res.data[0].get('id')
        except Exception as db_err:
            logger.error(f"Failed to create pending deal in initiate_negotiation: {db_err}")

        if existing_deal:
            deal_id_str = str(existing_deal.get('id'))
            deal_state = str(existing_deal.get('deal_state', '')).lower().strip()
            deal_status = str(existing_deal.get('status', '')).lower().strip()
            payment_status = str(existing_deal.get('payment_status', '')).lower().strip()

            # Check if already finalized, paid, or fully locked
            is_locked = (
                    deal_state in ['finalized', 'completed', 'paid', 'locked']
                    or deal_status in ['finalized', 'completed', 'paid', 'locked']
                    or payment_status in ['paid', 'completed']
            )

            if is_locked:
                logger.info(
                    f"🚫 Deal {deal_id_str} for listing {listing_id} is already finalized and paid. Locking negotiation.")
                lang = str(_kwargs.get('lang') or _kwargs.get('language') or 'en').lower().strip()

                if lang in ['om', 'or']:
                    locked_msg = "⚠️ Qabeenyi kun kaffaltiidhaan xumurameera ykn cufameera; amma irra deebi'amee mormamuu hin danda'u."
                elif lang in ['am', 'amharic']:
                    locked_msg = "⚠️ ይህ ንብረት/ዕቃ አስቀድሞ በክፍያ ተጠናቋል/ተቆልፏል፤ ዳግም መደራደር አይቻልም።"
                else:
                    locked_msg = "⚠️ This item under negotiation is already finalized, paid, and locked."

                update_obj = _kwargs.get('update')
                if update_obj and hasattr(update_obj, 'message') and update_obj.message:
                    await update_obj.message.reply_text(locked_msg)
                elif resolved_bot and seeker_id:
                    try:
                        await resolved_bot.send_message(chat_id=int(seeker_id), text=locked_msg)
                    except Exception:
                        pass
                return None
            else:
                # Active/Pending/Stuck deal: Update agreed_price & current_price with the new offer
                logger.info(
                    f"🔄 Active or pending deal {deal_id_str} found for listing {listing_id}. Updating agreed price to {safe_initial_offer}.")
                try:
                    history = existing_deal.get('negotiation_history', []) or []
                    if not isinstance(history, list):
                        history = []
                    history.append({
                        'actor': provider_id if is_reverse_match else seeker_id,
                        'offer': float(safe_initial_offer),
                        'time': str(datetime.now()),
                        'action': 'updated_offer',
                        'match_type': 'reverse' if is_reverse_match else 'forward'
                    })

                    await supabase_client.table('deals').update({
                        'agreed_price': float(safe_initial_offer),
                        'current_price': float(safe_initial_offer),
                        'deal_state': 'negotiating',
                        'status': 'pending',
                        'negotiation_history': history,
                        'updated_at': datetime.utcnow().isoformat()
                    }).eq('id', deal_id_str).execute()

                    # ✅ CRITICAL FIX: Return deal_id_str so workflow continuation succeeds
                    return deal_id_str

                except Exception as update_err:
                    logger.warning(f"Failed to update existing deal price for {deal_id_str}: {update_err}")

            lang = str(_kwargs.get('lang') or _kwargs.get('language') or 'en').lower().strip()
            update = _kwargs.get('update')

            if lang in ['om', 'or', 'oromoo', 'afaan_oromoo', 'afaan-oromoo']:
                self_offer_err = "⚠️ Qabeenya keessان irratti gatii dhiheessuu hin dandeessan."
            elif lang in ['am', 'amharic']:
                self_offer_err = "⚠️ በራስዎ ንብረት ላይ ዋጋ ማቅረብ አይችሉም።"
            else:
                self_offer_err = "⚠️ You cannot submit an offer on your own listing."

            if update and hasattr(update, 'message') and update.message:
                await update.message.reply_text(self_offer_err)

            return None

        try:
            safe_initial_offer = float(initial_offer or 0.0)
            safe_vertical = str(vertical_type or "general").lower().strip()
            listing = {}

            # 1. Fetch listing details
            try:
                if hasattr(self.db, 'supabase') and self.db.supabase:
                    resp = await self.db.supabase.table('provider_listings').select('*').eq('id',
                                                                                            listing_id).execute()
                    if resp and getattr(resp, 'data', None):
                        listing = resp.data[0] if isinstance(resp.data, list) and len(resp.data) > 0 else {}
                elif hasattr(self.db, 'get_listing_by_id'):
                    fetched = await self.db.get_listing_by_id(listing_id)
                    if isinstance(fetched, dict):
                        listing = fetched
            except Exception as fetch_err:
                logger.warning(f"Could not fetch explicit asset details for notification: {fetch_err}")

            if not isinstance(listing, dict):
                listing = {}

            if not vertical_type or safe_vertical in ["general", "asset"]:
                safe_vertical = str(
                    listing.get('vertical_type', _kwargs.get('vertical_type', 'general'))).lower().strip()

            # 2. Fetch seeker_requests row explicitly with robust fallback matching
            fetched_seeker_data = {}
            req_id = (
                    _kwargs.get('request_id')
                    or _kwargs.get('seeker_request_id')
                    or _kwargs.get('req_id')
                    or _kwargs.get('request_id_str')
            )
            try:
                if hasattr(self.db, 'supabase') and self.db.supabase:
                    if req_id:
                        s_resp = await self.db.supabase.table('seeker_requests').select(
                            '*'
                        ).eq('id', req_id).execute()
                    else:
                        s_resp = await self.db.supabase.table('seeker_requests').select(
                            '*'
                        ).eq('seeker_id', seeker_id).order('created_at', desc=True).limit(3).execute()

                    if s_resp and getattr(s_resp, 'data', None) and len(s_resp.data) > 0:
                        matched_rows = s_resp.data
                        fetched_seeker_data = matched_rows[0]
                        if len(matched_rows) > 1 and safe_vertical:
                            for row in matched_rows:
                                if str(row.get('vertical_type', '')).lower() == safe_vertical:
                                    fetched_seeker_data = row
                                    break

                        loc_val = fetched_seeker_data.get('location_text') or fetched_seeker_data.get('origin')
                        if loc_val:
                            fetched_seeker_data.setdefault('origin', loc_val)
                            fetched_seeker_data.setdefault('origin_text', loc_val)

            except Exception as s_err:
                logger.warning(f"Could not fetch data from seeker_requests: {s_err}")

            # 3. Aggregate seeker sources & candidate objects
            seeker_request_obj = (
                    _kwargs.get('seeker_request')
                    or _kwargs.get('seeker_req')
                    or _kwargs.get('request')
                    or _kwargs.get('seeker_data')
                    or {}
            )

            seeker_sources = []
            if fetched_seeker_data:
                seeker_sources.append(fetched_seeker_data)

            if isinstance(seeker_request_obj, dict) and seeker_request_obj:
                seeker_sources.append(seeker_request_obj)

            for parent in [_kwargs or {}]:
                for sub_key in ['seeker_request', 'request', 'seeker_data', 'seeker_payload']:
                    sub_dict = parent.get(sub_key)
                    if isinstance(sub_dict, dict) and sub_dict not in seeker_sources:
                        seeker_sources.append(sub_dict)

            candidate_sources = [listing] + list(seeker_sources) + [_kwargs or {}]

            INVALID_PLACEHOLDERS = {"", "N/A", "NOT_SPECIFIED", "NONE", "NULL", "UNSPECIFIED", "ANY", "0", "0.0",
                                    "NOE", "NOT SPECIFIED"}

            def get_clean_param(keys: list, default: str = "", custom_sources: list = None) -> str:
                srcs_to_check = custom_sources if custom_sources is not None else candidate_sources
                for srcs in srcs_to_check:
                    if not isinstance(srcs, dict):
                        continue
                    for k in keys:
                        cl_val = srcs.get(k)
                        if cl_val is not None:
                            cleaned = str(cl_val).strip()
                            if cleaned and cleaned.upper() not in INVALID_PLACEHOLDERS:
                                return cleaned
                return default

            raw_vertical_str = safe_vertical.lower().strip()

            # ==================================================================
            # STRICT VERTICAL-GATED PARAMETER EXTRACTION
            # ==================================================================
            raw_origin = ""
            raw_dest = ""
            raw_route = ""

            if raw_vertical_str in ["freight", "ride", "housing"]:
                raw_origin = get_clean_param(
                    ['origin', 'origin_text', 'location_text', 'departure_text', 'from_location'])
                raw_dest = get_clean_param(['destination', 'destination_text', 'dest_text', 'to_location'])
                raw_route = get_clean_param(['route_string', 'route', 'path'])

            safe_origin = escape_md(raw_origin) if raw_origin else ""
            safe_dest = escape_md(raw_dest) if raw_dest else ""
            safe_route_raw = escape_md(raw_route) if raw_route else ""

            if safe_origin and safe_dest:
                safe_route = f"{safe_origin} ➡️ {safe_dest}"
            elif safe_route_raw:
                safe_route = safe_route_raw
            elif safe_origin:
                safe_route = safe_origin
            elif safe_dest:
                safe_route = f"➡️ {safe_dest}"
            else:
                safe_route = ""

            # --- VEHICLE PLATE EXTRACTION ---
            raw_plate = ""
            if raw_vertical_str in ["vehicle", "freight", "ride"]:
                raw_plate = get_clean_param(['vehicle_plate', 'plate_number', 'plate', 'license_plate', 'plate_no'])
            safe_plate = escape_md(raw_plate) if raw_plate else ""

            # --- STRICT ACTUAL CARGO WEIGHT EXTRACTION (SEEKER-SIDE ONLY) ---
            raw_weight_val = None
            raw_provider_capacity = None

            if raw_vertical_str == "freight":
                def is_valid_weight(v) -> bool:
                    if v is None:
                        return False
                    s_val = str(v).strip().upper()
                    if s_val in INVALID_PLACEHOLDERS or s_val in ("", "0", "0.0", "NONE", "NULL", "N/A"):
                        return False
                    try:
                        return float(re.sub(r"[^\d.]", "", s_val.replace(",", ""))) > 0
                    except (ValueError, TypeError):
                        return True

                # 1. Strictly seeker-side cargo weight keys (EXCLUDING provider capacity fields)
                seeker_cargo_keys = [
                    "cargo_weight_kgs", "cargo_weight", "weight_kgs",
                    "requested_weight_kgs", "weight"
                ]

                # 2. Provider capacity keys (kept separate to avoid confusing vehicle capacity with actual cargo weight)
                provider_capacity_keys = [
                    "max_weight_kgs", "capacity_kgs", "max_weight", "capacity"
                ]

                # Check seeker sources first for actual cargo weight
                for src in seeker_sources:
                    if not isinstance(src, dict):
                        continue
                    for key in seeker_cargo_keys:
                        val = src.get(key)
                        if is_valid_weight(val):
                            raw_weight_val = val
                            break
                    if raw_weight_val is not None:
                        break

                # Check general kwargs/payload if not found in seeker sources (excluding provider listing)
                if raw_weight_val is None:
                    for src in [_kwargs or {}]:
                        if not isinstance(src, dict):
                            continue
                        for key in seeker_cargo_keys:
                            val = src.get(key)
                            if is_valid_weight(val):
                                raw_weight_val = val
                                break
                        if raw_weight_val is not None:
                            break

                seeker_amenities = (
                        fetched_seeker_data.get('required_amenities')
                        or _kwargs.get('required_amenities')
                        or listing.get('required_amenities')
                )

                amenities_blob = " ".join([
                    str(seeker_amenities or ""),
                    str(seeker_request_obj.get("amenities", "")),
                    str(seeker_request_obj.get("description", "")),
                    str(_kwargs.get("description", ""))
                ])

                # Fallback regex extraction from descriptions/amenities blobs for actual cargo weight
                if raw_weight_val is None and amenities_blob.strip():
                    wm = re.search(
                        r"(?:Cargo Weight|Ulfaatina Fe’iichaa|የዕቃ ክብደት|Cargo|Fe’iicha)\s*[:=]?\s*([\d.,]+\s*(?:kg|kgs|ton|tons|quintal|t)?)\b",
                        amenities_blob,
                        re.IGNORECASE)
                    if wm:
                        parsed_val = wm.group(1).strip()
                        if is_valid_weight(parsed_val):
                            raw_weight_val = parsed_val

                # Capture provider capacity separately for reference/logging if needed
                for src in [listing] + candidate_sources:
                    if not isinstance(src, dict):
                        continue
                    for key in provider_capacity_keys:
                        val = src.get(key)
                        if is_valid_weight(val):
                            raw_provider_capacity = val
                            break
                    if raw_provider_capacity is not None:
                        break

            safe_weight = ""
            if raw_weight_val is not None:
                try:
                    w_num = float(re.sub(r"[^\d.]", "", str(raw_weight_val).replace(",", "")))
                    if w_num > 0:
                        safe_weight = f"{w_num:,.1f}".rstrip('0').rstrip('.') + " kg"
                except (ValueError, TypeError):
                    if str(raw_weight_val).strip().upper() not in INVALID_PLACEHOLDERS:
                        safe_weight = str(raw_weight_val).strip()

            try:
                safe_weight_baseline = float(re.sub(r"[^\d.]", "", str(raw_weight_val or 0.0)))
            except (ValueError, TypeError):
                safe_weight_baseline = 0.0

            # --- PASSENGER / PARTY SIZE EXTRACTION ---
            raw_passengers = None
            if raw_vertical_str == "ride":
                for src in seeker_sources:
                    if not isinstance(src, dict):
                        continue
                    for key in ["party_size", "passengers", "requested_seats", "seats"]:
                        val = src.get(key)
                        if val is not None and str(val).strip().upper() not in INVALID_PLACEHOLDERS:
                            raw_passengers = val
                            break
                    if raw_passengers is not None:
                        break

                amenities_blob = get_clean_param(["required_amenities", "amenities", "description"])

                if not raw_passengers:
                    raw_passengers = get_clean_param(["party_size", "passengers", "requested_seats", "seats"])

                if not raw_passengers and amenities_blob.strip():
                    pm = re.search(
                        r"(?:Party Size|Passengers|Seats|Imaltoota|ተሳፋሪዎች|👥)\s*[:=]?\s*(\d+)",
                        amenities_blob,
                        re.IGNORECASE
                    )
                    if pm:
                        raw_passengers = pm.group(1).strip()

            safe_passengers = ""
            if raw_passengers:
                try:
                    p_num = int(float(re.sub(r"[^\d.]", "", str(raw_passengers))))
                    if p_num > 0:
                        safe_passengers = str(p_num)
                except (ValueError, TypeError):
                    safe_passengers = str(raw_passengers).strip()

            raw_equipment_type = ""
            raw_vehicle_type = ""
            raw_make_model_year = ""
            raw_brand = ""

            if raw_vertical_str in ["equipment", "housing"]:
                raw_equipment_type = get_clean_param(["equipment_type", "property_use", "category"])
            if raw_vertical_str in ["vehicle", "freight", "ride"]:
                raw_vehicle_type = get_clean_param(["vehicle_type", "car_type", "type"])
            if raw_vertical_str in ["equipment", "vehicle"]:
                raw_make_model_year = get_clean_param(["make_model_year", "model_year", "model"])
                raw_brand = get_clean_param(["brand", "make", "manufacturer"])

            safe_equipment_type = escape_md(raw_equipment_type) if raw_equipment_type else ""
            safe_vehicle_type = escape_md(raw_vehicle_type) if raw_vehicle_type else ""
            safe_make_model_year = escape_md(raw_make_model_year) if raw_make_model_year else ""
            safe_brand = escape_md(raw_brand) if raw_brand else ""

            if not asset_title:
                asset_title = self.format_asset_title(listing, raw_vertical_str)
                if asset_title == "Matched Asset Listing" and seeker_sources:
                    asset_title = self.format_asset_title(seeker_sources[0], raw_vertical_str)

            clean_asset_title = self._clean_asset_title_language_mixup(str(asset_title or ""), raw_vertical_str)

            if not clean_asset_title or clean_asset_title == "Matched Asset Listing":
                clean_asset_title = self.format_asset_title({
                    "equipment_type": raw_equipment_type,
                    "vehicle_type": raw_vehicle_type,
                    "make_model_year": raw_make_model_year,
                    "brand": raw_brand,
                    "title": listing.get("title")
                }, raw_vertical_str)

            clean_asset_title = re.sub(r'\b[fF][sS][rR]\b', 'FSR', clean_asset_title)
            clean_asset_title = " ".join(
                [w.upper() if w.lower() in ['fsr', 'suv', 'ev', 'abs', 'awd', '4wd'] else w for w in
                 clean_asset_title.split()])

            if not transaction_type:
                transaction_type = listing.get('transaction_type', _kwargs.get('transaction_type', 'Rental'))

            # 🔒 SINGLE SOURCE OF TRUTH INITIATION CURRENCY RESOLUTION
            context_obj = _kwargs.get('context')
            session_currency = None
            if context_obj and hasattr(context_obj, 'user_data') and isinstance(context_obj.user_data, dict):
                session_currency = (
                        context_obj.user_data.get('currency') or
                        context_obj.user_data.get('housing_currency') or
                        context_obj.user_data.get('fare_currency')
                )

            # Priority Order: Provider Listing (Ground Truth) -> Explicit Keyword -> Session Memory -> Default ETB
            raw_currency = (
                    listing.get('currency') or  # 👈 ABSOLUTE SINGLE SOURCE OF TRUTH FIRST
                    _kwargs.get('currency') or
                    session_currency or
                    'ETB'
            )
            currency = str(raw_currency).strip().upper()

            if currency not in ["ETB", "USD", "EUR", "GBP"]:
                currency = "ETB"

            history = [
                {
                    'actor': provider_id if is_reverse_match else seeker_id,
                    'offer': safe_initial_offer,
                    'time': str(datetime.now()),
                    'action': 'initial_offer',
                    'match_type': 'reverse' if is_reverse_match else 'forward'
                }
            ]

            # Persist actual seeker cargo weight and currency in deals table
            deal_id = await self.db.create_pending_deal(
                seeker_id=seeker_id,
                provider_id=provider_id,
                listing_id=listing_id,
                seeker_request_id=req_id,
                vertical_type=safe_vertical,
                current_price=safe_initial_offer,
                currency=currency,  # ✅ FIXED: Explicitly save the currency
                deal_state="negotiating",
                negotiation_history=history,
                asset_title=clean_asset_title,
                transaction_type=transaction_type,
                weight=safe_weight_baseline,
                cargo_weight_kgs=safe_weight_baseline
            )

            if not deal_id:
                logger.error(f"Failed to create pending deal in database for listing {listing_id}")
                return None

            deal_id_str = str(deal_id)

            if context and hasattr(context, 'user_data') and isinstance(context.user_data, dict):
                self._clear_negotiation_state(context.user_data)
                context.user_data["active_negotiation_id"] = deal_id_str

            # ⚡ PRIORITY HANDLER CHECK: If notify is False, skip internal notifications and return deal_id immediately
            if not should_notify:
                logger.info(
                    f"Notification skipped in initiate_negotiation for deal {deal_id_str} (notify=False). Vertical handler will process messaging."
                )
                return deal_id_str

            # ==================================================================
            # INTERNAL NOTIFICATION PIPELINE (ONLY EXECUTED IF NOTIFY=TRUE)
            # ==================================================================
            safe_currency = escape_md(currency)
            safe_price = escape_md(f"{safe_initial_offer:,.0f}")

            session_lang = _kwargs.get('lang') or _kwargs.get('language')
            initiator_id = provider_id if is_reverse_match else seeker_id
            target_id = seeker_id if is_reverse_match else provider_id

            async def resolve_lang(uid: int) -> str:
                if session_lang and uid == initiator_id:
                    s_lng = str(session_lang).lower().strip()
                    if s_lng in ["or", "om", "oromoo", "afaan_oromoo", "afaan-oromoo"]:
                        return "om"
                    if s_lng in ["am", "amharic"]:
                        return "am"
                    if s_lng in ["en", "english"]:
                        return "en"

                try:
                    if hasattr(self, "_get_target_user_lang_isolated"):
                        raw_lang = await self._get_target_user_lang_isolated(uid)
                    else:
                        raw_lang = "en"

                    lng = str(raw_lang).lower().strip() if raw_lang else "en"
                    if lng in ["or", "om", "oromoo", "afaan_oromoo", "afaan-oromoo"]:
                        return "om"
                    if lng in ["am", "amharic"]:
                        return "am"
                    return "en"
                except Exception:
                    return "en"

            seeker_lang = await resolve_lang(seeker_id)
            provider_lang = await resolve_lang(provider_id)
            initiator_lang = provider_lang if is_reverse_match else seeker_lang
            target_lang = seeker_lang if is_reverse_match else provider_lang

            # --- INITIATOR CONFIRMATION NOTICE ---
            if is_reverse_match:
                templates = {
                    "om": "✅ Dhiheessiin kee barbaadaaf ergameera: *{price} {currency}*",
                    "am": "✅ የ *{price} {currency}* ማቅረቢያዎ ለፈላጊው ተልኳል!",
                    "en": "✅ Your offer of *{price} {currency}* has been sent to the seeker!",
                }
            else:
                templates = {
                    "om": "✅ Dhiheessiin kee dhiyeessaaf ergameera: *{price} {currency}*",
                    "am": "✅ የ *{price} {currency}* ማቅረቢያዎ ለአቅራቢው ተልኳል!",
                    "en": "✅ Your offer of *{price} {currency}* has been sent to the provider!",
                }

            template = templates.get(initiator_lang, templates["en"])
            initiator_notice = template.format(price=safe_price, currency=safe_currency)

            if resolved_bot:
                try:
                    await resolved_bot.send_message(
                        chat_id=initiator_id, text=initiator_notice, parse_mode="Markdown"
                    )
                except Exception as init_msg_err:
                    logger.warning(
                        f"Could not send deal initiator confirmation message: {init_msg_err}"
                    )

            # --- TARGET RECIPIENT NOTICE & MULTILINGUAL DICTIONARIES ---
            VERTICAL_TRANSLATIONS = {
                "am": {"housing": "ቤት", "vehicle": "ተሽከርካሪ", "equipment": "መሳሪያዎች", "freight": "ዕቃ ማጓዣ",
                       "ride": "ራይድ", "general": "ንብረት"},
                "om": {"housing": "Mana", "vehicle": "Konkolaataa", "equipment": "Meeshaalee",
                       "freight": "Fe'umsaa", "ride": "Imala", "general": "Qabeenya"},
                "en": {"housing": "Housing", "vehicle": "Vehicle", "equipment": "Equipment", "freight": "Freight",
                       "ride": "Ride", "general": "Asset"}
            }

            TRANSACTION_TRANSLATIONS = {
                "am": {"rental": "ኪራይ", "sale": "ሽያጭ", "sell": "ሽያጭ", "lease": "ሊዝ", "rent": "ኪራይ", "service": "አገልግሎት",
                       "booking": "ቦታ ማስያዝ"},
                "om": {"rental": "Kiraa", "sale": "Gurgurtaa", "sell": "Gurgurtaa", "lease": "Liizii", "rent": "Kiraa",
                       "service": "Tajaajila", "booking": "Qabiinsa"},
                "en": {"rental": "Rental", "sale": "Sale", "sell": "Sale", "lease": "Lease", "rent": "Rental",
                       "service": "Service",
                       "booking": "Booking"}
            }

            VERTICAL_CONFIG = {
                "housing": {"emoji": "🏠", "label": {"om": "Mana", "am": "ቤት/ንብረት", "en": "Property"}},
                "vehicle": {"emoji": "🚗", "label": {"om": "Konkolaataa", "am": "ተሽከርካሪ", "en": "Vehicle"}},
                "equipment": {"emoji": "⚙️", "label": {"om": "Meeshaa", "am": "መሳሪያ", "en": "Equipment"}},
                "freight": {"emoji": "📦", "label": {"om": "Fe'umsa", "am": "የጭነት ዕቃ", "en": "Freight/Cargo"}},
                "ride": {"emoji": "🚖", "label": {"om": "Imala", "am": "ጉዞ", "en": "Trip"}},
                "general": {"emoji": "🏷️", "label": {"om": "Qabeenya", "am": "ንብረት", "en": "Asset"}}
            }

            v_cfg = VERTICAL_CONFIG.get(raw_vertical_str, VERTICAL_CONFIG["general"])
            v_emoji = v_cfg["emoji"]
            v_asset_label = v_cfg["label"].get(target_lang, v_cfg["label"]["en"])

            localized_asset_title = clean_asset_title
            localized_vertical = VERTICAL_TRANSLATIONS.get(target_lang, VERTICAL_TRANSLATIONS["en"]).get(
                raw_vertical_str, safe_vertical.title()
            )

            clean_tx_type = str(transaction_type or "rental").lower().strip()
            TRANSLATION_DICT = TRANSACTION_TRANSLATIONS.get(target_lang, TRANSACTION_TRANSLATIONS["en"])

            if clean_tx_type in TRANSLATION_DICT:
                localized_tx_type = TRANSLATION_DICT[clean_tx_type]
            else:
                localized_tx_type = TRANSACTION_TRANSLATIONS["en"].get(clean_tx_type, str(transaction_type).title())

            safe_asset_title = escape_md(localized_asset_title)
            safe_localized_vertical = escape_md(str(localized_vertical))
            safe_localized_tx_type = escape_md(str(localized_tx_type))

            title_has_route = safe_route and (safe_origin and safe_origin in safe_asset_title)

            # --- LANGUAGE-PURE ATTRIBUTE BUILDER ---
            attr_lines = ""
            if raw_vertical_str == "equipment":
                if safe_equipment_type:
                    lbl = "Gosa Meeshaa" if target_lang == "om" else (
                        "የመሳሪያ አይነት" if target_lang == "am" else "Equipment Type")
                    attr_lines += f"⚙️ *{lbl}:* {safe_equipment_type}\n"
                if safe_make_model_year:
                    lbl = "Moodeela/Bara" if target_lang == "om" else (
                        "ሞዴል/ዓ.ም" if target_lang == "am" else "Model/Year")
                    attr_lines += f"📅 *{lbl}:* {safe_make_model_year}\n"
                if safe_brand:
                    lbl = "Braandii" if target_lang == "om" else ("ብራንድ" if target_lang == "am" else "Brand")
                    attr_lines += f"🏷️ *{lbl}:* {safe_brand}\n"

            elif raw_vertical_str == "vehicle":
                if safe_vehicle_type:
                    lbl = "Gosa Konkolaataa" if target_lang == "om" else (
                        "የተሽከርካሪ አይነት" if target_lang == "am" else "Vehicle Type")
                    attr_lines += f"🚗 *{lbl}:* {safe_vehicle_type}\n"
                if safe_make_model_year:
                    lbl = "Moodeela/Bara" if target_lang == "om" else (
                        "ሞዴል/ዓ.ም" if target_lang == "am" else "Make/Model/Year")
                    attr_lines += f"📅 *{lbl}:* {safe_make_model_year}\n"
                if safe_plate:
                    lbl = "Gabatee" if target_lang == "om" else (
                        "የሰሌዳ ቁጥር" if target_lang == "am" else "Vehicle Plate")
                    attr_lines += f"🚘 *{lbl}:* {safe_plate}\n"

            elif raw_vertical_str == "freight":
                if safe_route and not title_has_route:
                    lbl = "Kallattii" if target_lang == "om" else ("መሥመር" if target_lang == "am" else "Route")
                    attr_lines += f"📍 *{lbl}:* {safe_route}\n"
                if safe_weight:
                    lbl = "Ulfaatina Fe’iichaa" if target_lang == "om" else (
                        "የዕቃ ክብደት" if target_lang == "am" else "Cargo Weight")
                    attr_lines += f"⚖️ *{lbl}:* {safe_weight}\n"
                if safe_vehicle_type:
                    lbl = "Gosa Konkolaataa" if target_lang == "om" else (
                        "የተሽከርካሪ አይነት" if target_lang == "am" else "Requested Vehicle")
                    attr_lines += f"🚛 *{lbl}:* {safe_vehicle_type}\n"

            elif raw_vertical_str == "ride":
                if safe_route and not title_has_route:
                    lbl = "Kallattii" if target_lang == "om" else ("መሥመር" if target_lang == "am" else "Route")
                    attr_lines += f"📍 *{lbl}:* {safe_route}\n"
                if safe_passengers:
                    lbl = "Baay'ina Imaltootaa" if target_lang == "om" else (
                        "የተሳፋሪዎች ብዛት" if target_lang == "am" else "Party Size")
                    attr_lines += f"👥 *{lbl}:* {safe_passengers}\n"
                if safe_vehicle_type:
                    lbl = "Gosa Konkolaataa" if target_lang == "om" else (
                        "የተሽከርካሪ አይነት" if target_lang == "am" else "Preferred Vehicle")
                    attr_lines += f"🚗 *{lbl}:* {safe_vehicle_type}\n"

            elif raw_vertical_str == "housing":
                if safe_equipment_type:
                    lbl = "Fayyadama" if target_lang == "om" else (
                        "የአገልግሎት አይነት" if target_lang == "am" else "Property Use")
                    attr_lines += f"🏠 *{lbl}:* {safe_equipment_type}\n"
                if safe_origin:
                    lbl = "Bakka" if target_lang == "om" else ("ቦታ" if target_lang == "am" else "Location")
                    attr_lines += f"📍 *{lbl}:* {safe_origin}\n"

            # =========================================================================
            # 🌐 ASSET TITLE SANITIZATION & PREFIX TRANSLATION HELPER
            # =========================================================================
            def sanitize_asset_title(title_text: str, lang_title: str) -> str:
                if not title_text:
                    return ""

                # Map of domain prefixes to clean localized terms based on target language
                prefix_translations = {
                    "om": {
                        "የጭነት": "Fe'umsa",
                        "የጉዞ": "Imala",
                        "Freight": "Fe'umsa",
                        "Ride": "Imala",
                        "የጭነት Mercedes": "Fe'umsa Mercedes",
                        "የጉዞ Mercedes": "Imala Mercedes",
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

                clean_title = str(title_text).strip()
                lang_map = prefix_translations.get(lang_title, prefix_translations["en"])

                for old_prefix, new_prefix in lang_map.items():
                    if clean_title.startswith(old_prefix):
                        clean_title = clean_title.replace(old_prefix, new_prefix, 1)

                return clean_title.strip()

            # ✅ Robustly retrieve asset title with None-safe context and locals inspection
            safe_user_data = getattr(context, 'user_data', None) if 'context' in locals() and context else {}
            if not isinstance(safe_user_data, dict):
                safe_user_data = {}

            raw_asset_title = (
                    safe_user_data.get('asset_title')
                    or locals().get('clean_asset_title')
                    or locals().get('deal', {}).get('asset_title', 'Transport Asset')
            )
            safe_asset_title = html.escape(sanitize_asset_title(raw_asset_title, target_lang))

            # =========================================================================
            # 🌐 LOCALIZED REVERSE OFFER DESCRIPTIONS
            # =========================================================================
            reverse_descriptions = {
                "en": "ℹ️ <i>The provider offered a price on your service request:</i>\n",
                "am": "ℹ️ <i>አቅራቢው ባቀረቡት የአገልግሎት ጥያቄ ላይ ዋጋ አቅርቧል:</i>\n",
                "om": "ℹ️ <i>Dhiheessaan kun tajaajila gaafattan irratti gatii dhiheesseera:</i>\n"
            }

            current_reverse_desc = reverse_descriptions.get(target_lang, reverse_descriptions["en"])

            # Strictly constructed language-pure target notice
            if target_lang == "om":
                prefix = "🔄 *Dhiheessii Garagalfamaa Haaraa" if is_reverse else "🔔 *Dhiheessii Haaraa"
                sub_desc = f"\n\n💬 _{current_reverse_desc}_" if is_reverse else ""
                target_notice = (
                    f"{prefix} ({safe_localized_vertical})!*\n"
                    f"{sub_desc}\n\n"
                    f"🏷️ *Gosa Daldalaa:* {safe_localized_tx_type}\n"
                    f"{v_emoji} *{v_asset_label}:* _{safe_asset_title}_\n"
                    f"{attr_lines}"
                    f"💰 *Gatii Dhihaate:* *{safe_price} {safe_currency}*"
                )
            elif target_lang == "am":
                prefix = "🔄 *አዲስ የተገላቢጦሽ ማቅረቢያ" if is_reverse else "🔔 *አዲስ የማቅረቢያ ጥያቄ"
                sub_desc = f"\n\n💬 _{current_reverse_desc}_" if is_reverse else ""
                target_notice = (
                    f"{prefix} ({safe_localized_vertical})!*\n"
                    f"{sub_desc}\n\n"
                    f"🏷️ *ግብይት:* {safe_localized_tx_type}\n"
                    f"{v_emoji} *{v_asset_label}:* _{safe_asset_title}_\n"
                    f"{attr_lines}"
                    f"💰 *የቀረበ ዋጋ:* *{safe_price} {safe_currency}*"
                )
            else:
                prefix = "🔄 *New Reverse Match Offer" if is_reverse else "🔔 *New Offer Received"
                sub_desc = f"\n\n💬 _{current_reverse_desc}_" if is_reverse else ""
                target_notice = (
                    f"{prefix} ({safe_localized_vertical})!*\n"
                    f"{sub_desc}\n\n"
                    f"🏷️ *Transaction:* {safe_localized_tx_type}\n"
                    f"{v_emoji} *{v_asset_label}:* _{safe_asset_title}_\n"
                    f"{attr_lines}"
                    f"💰 *Offered Price:* *{safe_price} {safe_currency}*"
                )

            # Language-pure inline keyboard buttons
            if target_lang == "om":
                btn_accept_text = "✅ Gatii Fudhadhu"
                btn_counter_text = "📉 Gatii Wal Jijjiiri"
                btn_decline_text = "❌ Walii Galtee Didii"
            elif target_lang == "am":
                btn_accept_text = "✅ ዋጋውን ተቀበል"
                btn_counter_text = "📉 የመደራደሪያ ዋጋ አቅርብ"
                btn_decline_text = "❌ ስምምነቱን ውድቅ አድርግ"
            else:
                btn_accept_text = "✅ Accept Price"
                btn_counter_text = "📉 Counter Offer"
                btn_decline_text = "❌ Decline Deal"

            keyboard = [
                [
                    InlineKeyboardButton(btn_accept_text, callback_data=f"deal_accept:{deal_id_str}"),
                    InlineKeyboardButton(btn_counter_text, callback_data=f"deal_counter:{deal_id_str}")
                ],
                [
                    InlineKeyboardButton(btn_decline_text, callback_data=f"deal_decline:{deal_id_str}")
                ]
            ]

            if resolved_bot:
                await resolved_bot.send_message(
                    chat_id=target_id,
                    text=target_notice,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="Markdown"
                )
            return deal_id_str

        except Exception as init_err:
            err_str = str(init_err).lower()
            if "unique_active_asset_deal" in err_str or "duplicate key value" in err_str:
                logger.warning(f"Double-match prevented for listing {listing_id}")
            else:
                logger.error(f"Failed to cleanly initialize handshake: {init_err}", exc_info=True)
            return None

    # In-memory lock sets to prevent concurrent or duplicate deal acceptance executions
    _processed_deal_callback_ids: Set[str] = set()
    _executing_deal_locks: Set[str] = set()

    async def handle_deal_acceptance_callback(
            self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Answers the webhook immediately to prevent retries and offloads execution with idempotency checks."""
        query = update.callback_query
        if query:
            # 🛡️ EARLY ENTRY IDEMPOTENCY GUARD
            if query.id in self._processed_deal_callback_ids:
                logger.warning(f"🛡️ Intercepted duplicate deal acceptance callback ID: {query.id}")
                try:
                    await query.answer(text="⏳ Processing already underway...", show_alert=False)
                except Exception:
                    pass
                return

            self._processed_deal_callback_ids.add(query.id)
            if len(self._processed_deal_callback_ids) > 200:
                self._processed_deal_callback_ids.clear()

            try:
                await query.answer()
            except Exception as e:
                logger.warning(f"Failed to answer callback query early: {e}")

        asyncio.create_task(self._process_deal_acceptance_background(update, context))

    async def _process_deal_acceptance_background(
            self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Background worker to extract parameters, fetch deal data, and invoke deal acceptance execution with concurrency protection."""
        query = update.callback_query
        if not query or not query.data:
            logger.error("Callback query or query data is missing in background deal processing.")
            return

        parts = query.data.split(":", 1)
        if len(parts) < 2:
            logger.error(f"Malformed callback data format: {query.data}")
            return

        deal_id_str = parts[1]

        # 🛡️ CONCURRENCY GUARD: Prevent parallel background workers for the exact same deal ID
        if deal_id_str in self._executing_deal_locks:
            logger.warning(f"🛡️ Concurrent execution blocked: Deal {deal_id_str} acceptance is already in progress.")
            return

        self._executing_deal_locks.add(deal_id_str)

        try:
            deal = None
            try:
                if hasattr(self, "db_manager") and hasattr(self.db_manager, "get_deal"):
                    deal = await self.db_manager.get_deal(deal_id_str)
                elif hasattr(self, "db") and hasattr(self.db, "get_deal_by_id"):
                    deal = await self.db.get_deal_by_id(deal_id_str)
                elif hasattr(self, "get_deal"):
                    deal = await self.get_deal(deal_id_str)
                elif hasattr(self, "db") and hasattr(self.db, "supabase"):
                    res = await self.db.supabase.table("deals").select("*").eq("id", deal_id_str).single().execute()
                    deal = res.data if res else None
            except Exception as err:
                logger.error(f"Failed to fetch deal record for {deal_id_str}: {err}")
                return

            if not deal:
                logger.error(f"Deal record {deal_id_str} not found in database.")
                return

            # Check if deal is already finalized or pending payment to avoid duplicate notification triggers
            if deal.get("deal_state") in ["finalized", "pending_payment"] and deal.get("payment_initiated"):
                logger.info(
                    f"ℹ️ Deal {deal_id_str} state is already {deal.get('deal_state')}. Skipping duplicate execution.")
                return

            await self._execute_deal_acceptance(update, context, deal_id_str, deal)

        finally:
            # Always release lock when completed or if an error occurs
            self._executing_deal_locks.discard(deal_id_str)

    # ========================================================================
    # UNLIMITED COUNTER-OFFER HANDLER
    # ========================================================================

    async def process_counter_offer(
            self,
            deal_id_str: str,
            proposer_id: int,
            new_price: float,
            _context: ContextTypes.DEFAULT_TYPE
    ) -> bool:
        """
        Allows unlimited counter-offers between seeker and provider.
        Appends offer to negotiation_history and flips active_turn without upper limit checks.
        """
        supabase_client = getattr(self.db, 'supabase', None)
        if not supabase_client:
            logger.error("Supabase client uninitialized for counter offer processing.")
            return False

        try:
            res = await supabase_client.table('deals').select('*').eq('id', deal_id_str).single().execute()
            deal = res.data if res else None
            if not deal:
                return False

            history = deal.get("negotiation_history") or []
            if not isinstance(history, list):
                history = []

            seeker_id = int(deal["seeker_id"])
            provider_id = int(deal["provider_id"])

            next_turn = provider_id if proposer_id == seeker_id else seeker_id
            proposer_role = "seeker" if proposer_id == seeker_id else "provider"

            new_step = {
                "step": len(history) + 1,
                "proposer_id": proposer_id,
                "proposer_role": proposer_role,
                "price": new_price,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            history.append(new_step)

            update_payload = {
                "current_price": new_price,
                "negotiation_history": history,
                "last_offer_by": proposer_id,
                "active_turn_user_id": next_turn,
                "deal_state": "negotiating",
                "updated_at": datetime.now(timezone.utc).isoformat()
            }

            await supabase_client.table('deals').update(update_payload).eq('id', deal_id_str).execute()
            logger.info(f"Unlimited counter-offer step {len(history)} recorded for deal {deal_id_str}.")
            return True

        except Exception as e:
            logger.error(f"Failed to record counter offer for deal {deal_id_str}: {e}", exc_info=True)
            return False

    # ========================================================================
    # BACKGROUND NOTIFICATION DISPATCHER (STRICTLY ISOLATED & RETRY-CAPABLE)
    # ========================================================================

    @staticmethod
    async def _dispatch_party_notification_async(
            bot_instance: Any,
            chat_id: int,
            text_content: str,
            reply_markup: Optional[InlineKeyboardMarkup] = None,
            target_lang: Optional[str] = None,
            supabase_client: Optional[Any] = None
    ) -> None:
        """
        Background worker to send Telegram notifications asynchronously with
        strict recipient language isolation, verification, and automatic retry
        logic with exponential backoff for transient network resilience.
        """
        try:
            resolved_lang = target_lang
            if supabase_client and chat_id:
                try:
                    res = await supabase_client.table('users').select(
                        'preferred_language, language, lang, language_code'
                    ).eq('telegram_id', int(chat_id)).maybe_single().execute()
                    if res and getattr(res, 'data', None):
                        db_data = res.data
                        for col in ['preferred_language', 'language', 'lang', 'language_code']:
                            raw_db_lang = db_data.get(col)
                            if raw_db_lang:
                                clean_db = str(raw_db_lang).lower().strip().replace("-", "_").split("_")[0]
                                if clean_db in ("om", "or", "oromoo"):
                                    resolved_lang = "om"
                                    break
                                elif clean_db in ("am", "amharic", "amh"):
                                    resolved_lang = "am"
                                    break
                                elif clean_db in ("en", "english"):
                                    resolved_lang = "en"
                                    break
                except Exception as db_lang_err:
                    logger.warning(
                        f"Could not verify database language for background dispatch to {chat_id}: {db_lang_err}")

            if resolved_lang:
                logger.info(f"Target recipient {chat_id} isolated language verified as: [{resolved_lang}]")

            formatted_text = convert_md_to_html(text_content)

            # Robust retry loop with exponential backoff (2s -> 4s -> 8s)
            max_retries = 3
            for attempt in range(1, max_retries + 1):
                try:
                    await bot_instance.send_message(
                        chat_id=chat_id,
                        text=formatted_text,
                        reply_markup=reply_markup,
                        parse_mode="HTML"
                    )
                    logger.info(f"✅ Successfully dispatched isolated background message to chat_id {chat_id}")
                    return
                except (NetworkError, httpx.HTTPError) as net_err:
                    logger.warning(
                        f"⚠️ Network error dispatching notification to chat {chat_id} (Attempt {attempt}/{max_retries}): {net_err}"
                    )
                    if attempt == max_retries:
                        raise
                    await asyncio.sleep(2 ** attempt)
                except TelegramError as tg_err:
                    # Non-retryable Telegram API rejections (e.g. user blocked bot)
                    logger.error(f"❌ Telegram API rejection for chat {chat_id}: {tg_err}")
                    break

        except Exception as exc:
            logger.error(f"❌ Background notification delivery failed for chat_id {chat_id}: {exc}", exc_info=True)

    # ========================================================================
    # DEAL AGREEMENT FINALIZATION & TERMINATION (ZERO LANGUAGE CONTAMINATION)
    # ========================================================================

    async def _execute_deal_acceptance(
            self,
            query_or_update: Union[CallbackQuery, Update],
            context: ContextTypes.DEFAULT_TYPE,
            deal_id_str: str,
            deal: dict
    ) -> None:
        """Backward compatibility wrapper for protected hook references."""
        return await self.execute_deal_acceptance(query_or_update, context, deal_id_str, deal)

    async def execute_deal_acceptance(
            self,
            query_or_update: Union[CallbackQuery, Update],
            context: ContextTypes.DEFAULT_TYPE,
            deal_id_str: str,
            deal: dict
    ) -> None:
        query = query_or_update.callback_query if isinstance(query_or_update, Update) else query_or_update
        if not query:
            logger.error(f"Cannot execute deal acceptance for {deal_id_str}: CallbackQuery is None.")
            return

        supabase_client = getattr(self.db, 'supabase', None)
        user_id = query.from_user.id
        seeker_chat_id = int(deal["seeker_id"])
        provider_chat_id = int(deal["provider_id"])

        def html_esc(val: Any) -> str:
            if val is None:
                return ""
            if callable(escape_md):
                return escape_md(str(val))
            return str(val)

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

        async def _resolve_db_user_lang(chat_id: int, stored_lang: Optional[str] = None) -> str:
            if supabase_client and chat_id:
                try:
                    res = await supabase_client.table('users').select(
                        'preferred_language, language, language_code').eq(
                        'telegram_id', int(chat_id)).maybe_single().execute()
                    if res and getattr(res, 'data', None):
                        db_data = res.data
                        for col in ['preferred_language', 'language', 'language_code']:
                            norm_db = _normalize_lang(db_data.get(col))
                            if norm_db:
                                return norm_db
                except Exception as lang_err:
                    logger.warning(
                        f"Failed to fetch ground-truth language from DB for user {chat_id}: {lang_err}")

            norm_stored = _normalize_lang(stored_lang)
            if norm_stored:
                return norm_stored

            if context and hasattr(context, 'user_data') and isinstance(context.user_data, dict):
                for lang_key in ['preferred_language', 'language_code', 'language']:
                    norm_ctx = _normalize_lang(context.user_data.get(lang_key))
                    if norm_ctx:
                        return norm_ctx

            return "en"

        s_lang = await _resolve_db_user_lang(seeker_chat_id, deal.get("seeker_lang"))
        p_lang = await _resolve_db_user_lang(provider_chat_id, deal.get("provider_lang"))

        if user_id == provider_chat_id:
            user_lang = p_lang
        elif user_id == seeker_chat_id:
            user_lang = s_lang
        else:
            user_lang = await _resolve_db_user_lang(user_id)

        user_lang = _normalize_lang(user_lang) or "en"

        i18n_toast = {
            "processing": {
                "en": "⏳ Processing deal acceptance...",
                "am": "⏳ የስምምነት መቀበል በመሰራጨት ላይ ነው...",
                "om": "⏳ Waliigaltee simachuu irratti..."
            },
            "btn_processing": {
                "en": "⏳ Processing...",
                "am": "⏳ በመሰራጨት ላይ...",
                "om": "⏳ Process irratti..."
            },
            "unauthorized": {
                "en": "⚠️ Unauthorized action. You are not a party to this deal.",
                "am": "⚠️ ያልተፈቀደ እርምጃ። የእዚህ ስምምነት ተሳታፊ አይደሉም።",
                "om": "⚠️ Gocha heeyyama hin qabne. Isin qaama waliigaltee kanaa miti."
            },
            "already_in_progress": {
                "en": "⚠️ Payment or processing already in progress for this deal.",
                "am": "⚠️ ለዚህ ስምምነት ክፍያ ወይም ሂደት አስቀድሞ ተጀምሯል።",
                "om": "⚠️ Waliigaltee kanaaf kaffaltiiniis ta'e adeemsi eegalameera."
            },
            "invalid_state": {
                "en": "⚠️ This deal is not in the negotiating phase.",
                "am": "⚠️ ይህ ስምምነት በድርድር ደረጃ ላይ አይደለም።",
                "om": "⚠️ Waliigalteon kun sadarkaa marii/mormii irra hin jiru."
            },
            "invalid_price": {
                "en": "⚠️ System Error: Invalid deal price. Please contact support.",
                "am": "⚠️ የሲስተም ስህተት: የተሳሳተ የስምምነት ዋጋ። እባክዎ ድጋፍ ያግኙ።",
                "om": "⚠️ Dogoggora Sirnaa: Gatii waliigaltee sirrii hin taane. Maaloo deeggarsa qunnamaa."
            },
            "general_error": {
                "en": "⚠️ System error occurred while processing your acceptance. Please try again.",
                "am": "⚠️ የስምምነት መቀበሉን ሲያካሂድ የሲስተም ስህተት አጋጥሟል። እባክዎ እንደገና ይሞክሩ።",
                "om": "⚠️ Waliigaltee simachuu irratti dogoggorri sirnaa mudateera. Maaloo irra deebii yaali."
            }
        }

        async def edit_message_safe(
                text: str,
                reply_markup: Optional[InlineKeyboardMarkup] = None,
                parse_mode: str = "HTML"
        ):
            if query and query.message:
                try:
                    await query.edit_message_caption(
                        caption=text,
                        reply_markup=reply_markup,
                        parse_mode=parse_mode
                    )
                except Exception:
                    try:
                        await query.edit_message_text(
                            text=text,
                            reply_markup=reply_markup,
                            parse_mode=parse_mode
                        )
                    except Exception:
                        try:
                            await query.message.reply_text(
                                text=text,
                                reply_markup=reply_markup,
                                parse_mode=parse_mode
                            )
                        except Exception as fallback_err:
                            logger.warning(f"All message edit/send fallbacks failed: {fallback_err}")
            elif query:
                try:
                    await query.edit_message_text(
                        text=text,
                        reply_markup=reply_markup,
                        parse_mode=parse_mode
                    )
                except Exception as text_err:
                    logger.warning(f"Failed to edit message text: {text_err}")

        try:
            await query.answer(i18n_toast["processing"].get(user_lang, i18n_toast["processing"]["en"]))
        except Exception:
            pass

        try:
            await query.edit_message_reply_markup(
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton(
                        text=i18n_toast["btn_processing"].get(user_lang, i18n_toast["btn_processing"]["en"]),
                        callback_data="noop")]]
                )
            )
        except Exception:
            pass

        if user_id not in [seeker_chat_id, provider_chat_id]:
            try:
                await query.answer(
                    text=i18n_toast["unauthorized"].get(user_lang, i18n_toast["unauthorized"]["en"]),
                    show_alert=True)
            except Exception:
                pass
            return

        # --------------------------------------------------------------------
        # FETCH FRESH DEAL RECORD DIRECTLY FROM SUPABASE TO PREVENT AMOUNT DISCREPANCIES
        # --------------------------------------------------------------------
        active_deal = deal
        if supabase_client:
            try:
                fresh_deal_res = await supabase_client.table('deals').select('*').eq('id',
                                                                                     deal_id_str).maybe_single().execute()
                if fresh_deal_res and getattr(fresh_deal_res, 'data', None):
                    active_deal = fresh_deal_res.data
            except Exception as fresh_err:
                logger.warning(f"Could not fetch fresh deal record for {deal_id_str}: {fresh_err}")

        # PRIORITIZE agreed_price FIRST TO PRESERVE ACCURATE COMMISSION CALCULATIONS
        def _extract_valid_price(*price_sources) -> float:
            for val in price_sources:
                if val is not None:
                    try:
                        p_float = float(val)
                        if p_float > 0:
                            return p_float
                    except (ValueError, TypeError):
                        continue
            return 0.0

        agreed_price = _extract_valid_price(
            active_deal.get("agreed_price"),
            active_deal.get("current_price"),
            deal.get("agreed_price"),
            deal.get("current_price"),
            active_deal.get("price"),
            deal.get("price")
        )

        vertical_type = str(
            active_deal.get("vertical_type") or deal.get("vertical_type") or "asset"
        ).lower().strip()

        # --------------------------------------------------------------------
        # EARLY FETCHING OF RELATED RECORDS (Listing & Seeker Request)
        # --------------------------------------------------------------------
        req_id = active_deal.get("seeker_request_id") or active_deal.get("request_id")
        list_id = active_deal.get("listing_id")

        seeker_req = {}
        if req_id and supabase_client:
            try:
                sr_res = await supabase_client.table('seeker_requests').select('*').eq('id',
                                                                                       req_id).maybe_single().execute()
                if sr_res and sr_res.data:
                    seeker_req = sr_res.data
            except Exception as e:
                logger.warning(f"Could not fetch seeker_requests for {req_id}: {e}")

        listing = {}
        if list_id:
            if hasattr(self.db, 'get_listing_by_id'):
                listing = await self.db.get_listing_by_id(list_id)
            elif hasattr(self.db, 'get_request_by_id'):
                listing = await self.db.get_request_by_id(list_id)
            elif supabase_client:
                try:
                    l_res = await supabase_client.table('provider_listings').select('*').eq('id',
                                                                                            list_id).maybe_single().execute()
                    if l_res and l_res.data:
                        listing = l_res.data
                except Exception as e:
                    logger.warning(f"Could not fetch listing from supabase: {e}")

        # ====================================================================
        # 🔒 SINGLE SOURCE OF TRUTH ACCEPTANCE CURRENCY RESOLUTION
        # ====================================================================
        history_list = active_deal.get("negotiation_history") or deal.get("negotiation_history") or []
        history_currency = None
        if isinstance(history_list, list) and history_list:
            latest_step = history_list[-1]
            if isinstance(latest_step, dict):
                history_currency = latest_step.get("currency") or latest_step.get("fare_currency")

        session_currency = None
        if context and hasattr(context, 'user_data') and isinstance(context.user_data, dict):
            session_currency = (
                    context.user_data.get('currency') or
                    context.user_data.get('housing_currency') or
                    context.user_data.get('fare_currency')
            )

        # Priority Order: Provider Listing (Single Source of Truth) -> Negotiation History -> Session -> Default ETB
        raw_currency = (
                (listing.get("currency") if listing else None) or  # 👈 ABSOLUTE SINGLE SOURCE OF TRUTH FIRST
                history_currency or
                session_currency or
                active_deal.get("currency") or
                deal.get("currency") or
                (seeker_req.get("currency") if seeker_req else None) or
                "ETB"
        )
        currency = str(raw_currency).strip().upper()

        if currency not in ["ETB", "USD", "EUR", "GBP"]:
            currency = "ETB"

        # ATOMIC LOCK & UPDATE ALIGNED WITH DEALS TABLE SCHEMA
        if supabase_client:
            try:
                atomic_res = await supabase_client.table('deals').update({
                    'deal_state': 'pending_payment',
                    'status': 'pending',
                    'agreed_price': agreed_price,
                    'current_price': agreed_price,
                    'currency': currency,  # ✅ Lock explicit currency back into deals table
                    'payment_initiated': True,
                    'accepted_by': user_id,
                    'updated_at': datetime.now(timezone.utc).isoformat()
                }).eq('id', deal_id_str).in_('deal_state', ['negotiating', 'pending_payment']).execute()

                if not atomic_res or not getattr(atomic_res, 'data', None):
                    try:
                        await query.answer(
                            text=i18n_toast["already_in_progress"].get(
                                user_lang, i18n_toast["already_in_progress"]["en"]
                            ),
                            show_alert=True
                        )
                    except Exception:
                        pass
                    return
            except Exception as atomic_err:
                logger.warning(f"Atomic locking failed for deal {deal_id_str}: {atomic_err}")

        current_state = active_deal.get("deal_state", "negotiating")
        if current_state not in ['negotiating', 'pending_payment']:
            try:
                await query.answer(
                    text=i18n_toast["invalid_state"].get(user_lang, i18n_toast["invalid_state"]["en"]),
                    show_alert=True
                )
            except Exception:
                pass
            return

        if context and hasattr(context, 'user_data') and isinstance(context.user_data, dict):
            if hasattr(self, '_clear_negotiation_state'):
                self._clear_negotiation_state(context.user_data)

        safe_currency = escape_md(currency)

        is_reverse = (
                bool(active_deal.get("is_reverse") or active_deal.get("is_reverse_match"))
                or str(active_deal.get("match_type", "")).lower() == "reverse"
                or str(active_deal.get("match_direction", "")).lower() == "reverse"
                or str(active_deal.get("direction", "")).lower() == "reverse"
        )
        if not is_reverse and isinstance(active_deal.get("negotiation_history"), list) and active_deal.get(
                "negotiation_history"):
            first_step = active_deal["negotiation_history"][0]
            if isinstance(first_step, dict) and first_step.get("match_type") == "reverse":
                is_reverse = True

        if agreed_price <= 0:
            err_msg = i18n_toast["invalid_price"].get(user_lang, i18n_toast["invalid_price"]["en"])
            await edit_message_safe(text=err_msg, parse_mode="Markdown")
            return

        cargo_weight_kgs = (
                seeker_req.get("cargo_weight_kgs")
                or seeker_req.get("weight_kgs")
                or active_deal.get("cargo_weight_kgs")
                or active_deal.get("weight_kgs")
        )

        vehicle_plate = active_deal.get("vehicle_plate") or active_deal.get("plate_number") or (
                listing and listing.get("vehicle_plate"))

        def _format_vertical_name(v_type_raw: str, lang_code: str) -> str:
            v_clean = str(v_type_raw or "asset").lower().strip()
            v_map = {
                "equipment": {"en": "Equipment", "am": "መሳሪያ", "om": "Meeshaa"},
                "vehicle": {"en": "Vehicle", "am": "ተሽከርካሪ", "om": "Konkolaataa"},
                "freight": {"en": "Freight / Cargo", "am": "የጭነት አገልግሎት", "om": "Fe'umsa"},
                "ride": {"en": "Ride / Passenger", "am": "የተሳፋሪ ጉዞ", "om": "Imala / Imaltoota"},
                "housing": {"en": "Housing / Real Estate", "am": "መኖሪያ / ቤት", "om": "Mana / Manneen"},
            }
            lang = _normalize_lang(lang_code) or "en"
            if v_clean in v_map:
                return v_map[v_clean].get(lang, v_map[v_clean]["en"])
            return v_clean.title()

        def _build_sanitized_details_block(lang_code: str) -> str:
            lang = _normalize_lang(lang_code) or "en"
            lines = []

            def is_valid(val) -> bool:
                if val is None:
                    return False
                s_val = str(val).strip().lower()
                return s_val not in ["hin ibsamne", "not specified", "n/a", "none", "null", "", "0", "ያልተጠቀሰ"]

            LABELS = {
                "category": {"am": "📂 ምድብ", "om": "📂 Ramaddii", "en": "📂 Category"},
                "tx_type": {"am": "🏷 የንግድ ዓይነት", "om": "🏷️ Gosa Daldalaa", "en": "🏷️ Service Type"},
                "eq_type": {"am": "⚙️ የመሳሪያ አይነት", "om": "⚙️ Gosa Meeshaa", "en": "⚙️ Equipment Type"},
                "v_type": {"am": "🚗 የተሽከርካሪ አይነት", "om": "🚗 Gosa Konkolaataa", "en": "🚗 Vehicle Type"},
                "model_year": {"am": "📅 ሞዴል/ዓ.ም", "om": "📅 Moodeela/Bara", "en": "📅 Model/Year"},
                "brand": {"am": "🏷️ ብራንድ", "om": "🏷️ Braandii", "en": "🏷️ Brand"},
                "plate": {"am": "🚙 የሰሌዳ ቁጥር", "om": "🚙 Lakk. Gabatee", "en": "🚙 Vehicle Plate"},
                "freight_plate": {"am": "🚛 የሰሌዳ ቁጥር", "om": "🚛 Lakk. Gabatee", "en": "🚛 Vehicle Plate"},
                "route": {"am": "📍 መሥመር", "om": "📍 Karaa", "en": "📍 Route"},
                "weight": {"am": "⚖️ የጭነት ክብደት", "om": "⚖️ Ulfaatina Fe'umsaa", "en": "⚖️ Cargo Weight"},
                "party_size": {"am": "👥 የተሳፋሪዎች ብዛት", "om": "👥 Baay'ina Imaltootaa", "en": "👥 Party Size"},
                "property_use": {"am": "🏠 የአገልግሎት አይነት", "om": "🏠 Fayyadama", "en": "🏠 Property Use"},
                "location": {"am": "📍 ቦታ", "om": "📍 Bakka", "en": "📍 Location"},
                "offered_asset": {"am": "🛠️ የተስማሙበት ንብረት (አቅራቢ)", "om": "🛠️ Qabeenya Waliigalame (Dhiyeessaa)",
                                  "en": "🛠️ Agreed Asset (Listing)"},
                "requested_asset": {"am": "📋 የመጀመሪያ ጥያቄ (ፈላጊ)", "om": "📋 Gaaffii Jalqabaa (Barbaadaa)",
                                    "en": "📋 Original Request (Seeker)"},
            }

            TRANSACTION_TYPE_NAMES = {
                "rental": {"am": "ኪራይ", "om": "Kiraa", "en": "Rental"},
                "rent": {"am": "ኪራይ", "om": "Kiraa", "en": "Rent"},
                "sale": {"am": "ሽያጭ", "om": "Gurgurtaa", "en": "Sale"},
                "sell": {"am": "ሽያጭ", "om": "Gurgurtaa", "en": "Sale"},
                "lease": {"am": "ሊዝ", "om": "Liizii", "en": "Lease"},
            }

            PROPERTY_USE_NAMES = {
                "residential": {"am": "መኖሪያ ቤት", "om": "Mana Jireenyaa", "en": "Residential"},
                "commercial": {"am": "ንግድ ቤት", "om": "Mana Daldalaa", "en": "Commercial"},
                "residential plot": {"am": "የመኖሪያ ሜዳ/ቦታ", "om": "Lafa Jireenyaa", "en": "Residential Plot"},
                "commercial plot": {"am": "የንግድ ሜዳ/ቦታ", "om": "Lafa Daldalaa", "en": "Commercial Plot"},
                "agricultural": {"am": "ግብርና", "om": "Qonnaa", "en": "Agricultural"}
            }

            def get_label(key: str) -> str:
                return LABELS.get(key, {}).get(lang) or LABELS.get(key, {}).get("en", "")

            def get_tx_val(raw_val: str) -> str:
                raw = str(raw_val).strip().lower()
                return (
                        TRANSACTION_TYPE_NAMES.get(raw, {}).get(lang)
                        or TRANSACTION_TYPE_NAMES.get(raw, {}).get("en")
                        or raw.title()
                )

            def get_prop_val(raw_val: str) -> str:
                raw = str(raw_val).strip().lower()
                return (
                        PROPERTY_USE_NAMES.get(raw, {}).get(lang)
                        or PROPERTY_USE_NAMES.get(raw, {}).get("en")
                        or raw.title()
                )

            v_type = vertical_type.lower().strip()
            v_display = _format_vertical_name(v_type, lang)
            lines.append(f"{get_label('category')}: {html_esc(v_display)}")

            tx_type = (
                    active_deal.get("transaction_type")
                    or (listing and listing.get("transaction_type"))
                    or seeker_req.get("transaction_type")
            )
            if is_valid(tx_type):
                lines.append(f"{get_label('tx_type')}: {html_esc(get_tx_val(tx_type))}")

            if v_type == "equipment":
                # Explicitly differentiate Provider Listing vs Seeker Request, strictly prioritizing Provider Listing (`listing`)
                listing_eq = listing.get("equipment_type") or listing.get("property_use") if listing else None
                seeker_eq = seeker_req.get("equipment_type") or seeker_req.get("property_use") if seeker_req else None

                eq_type = (
                        active_deal.get("equipment_type")
                        or listing_eq
                        or seeker_eq
                )

                listing_mmy = listing.get("make_model_year") if listing else None
                seeker_mmy = seeker_req.get("make_model_year") if seeker_req else None
                mmy = (
                        active_deal.get("make_model_year")
                        or listing_mmy
                        or seeker_mmy
                )

                listing_brand = listing.get("brand") if listing else None
                seeker_brand = seeker_req.get("brand") or seeker_req.get("preferred_brand") if seeker_req else None
                brand = (
                        active_deal.get("brand")
                        or listing_brand
                        or seeker_brand
                )

                if is_valid(listing_eq):
                    lines.append(f"🛠️ {get_label('offered_asset')}: {html_esc(str(listing_eq))}")
                elif is_valid(eq_type):
                    lines.append(f"{get_label('eq_type')}: {html_esc(str(eq_type))}")

                if is_valid(seeker_eq) and seeker_eq != listing_eq:
                    lines.append(f"📋 {get_label('requested_asset')}: {html_esc(str(seeker_eq))}")

                if is_valid(mmy):
                    lines.append(f"{get_label('model_year')}: {html_esc(str(mmy))}")
                if is_valid(brand):
                    lines.append(f"{get_label('brand')}: {html_esc(str(brand))}")

            elif v_type == "vehicle":
                listing_v = listing.get("vehicle_type") if listing else None
                seeker_v = seeker_req.get("vehicle_type") if seeker_req else None

                v_type_val = (
                        active_deal.get("vehicle_type")
                        or listing_v
                        or seeker_v
                )

                listing_mmy = listing.get("make_model_year") if listing else None
                seeker_mmy = seeker_req.get("make_model_year") if seeker_req else None
                mmy = (
                        active_deal.get("make_model_year")
                        or listing_mmy
                        or seeker_mmy
                )

                if is_valid(listing_v):
                    lines.append(f"🛠️ {get_label('offered_asset')}: {html_esc(str(listing_v))}")
                elif is_valid(v_type_val):
                    lines.append(f"{get_label('v_type')}: {html_esc(str(v_type_val))}")

                if is_valid(seeker_v) and seeker_v != listing_v:
                    lines.append(f"📋 {get_label('requested_asset')}: {html_esc(str(seeker_v))}")

                if is_valid(mmy):
                    lines.append(f"{get_label('model_year')}: {html_esc(str(mmy))}")
                if is_valid(vehicle_plate):
                    lines.append(f"{get_label('plate')}: {html_esc(str(vehicle_plate))}")

            elif v_type == "freight":
                orig = (
                        active_deal.get("origin_text")
                        or active_deal.get("origin")
                        or seeker_req.get("location_text")
                        or seeker_req.get("origin")
                )
                dest = (
                        active_deal.get("destination_text")
                        or active_deal.get("destination")
                        or seeker_req.get("destination_text")
                        or seeker_req.get("destination")
                )

                if is_valid(orig) and is_valid(dest):
                    lines.append(f"{get_label('route')}: {html_esc(str(orig))} ➡️ {html_esc(str(dest))}")
                if is_valid(cargo_weight_kgs):
                    w_val = str(cargo_weight_kgs).strip()
                    weight_str = f"{w_val} kg" if not w_val.lower().endswith("kg") else w_val
                    lines.append(f"{get_label('weight')}: {html_esc(weight_str)}")
                if is_valid(vehicle_plate):
                    lines.append(f"{get_label('freight_plate')}: {html_esc(str(vehicle_plate))}")

            elif v_type == "ride":
                orig = (
                        active_deal.get("origin_text")
                        or active_deal.get("origin")
                        or seeker_req.get("location_text")
                        or seeker_req.get("origin")
                )
                dest = (
                        active_deal.get("destination_text")
                        or active_deal.get("destination")
                        or seeker_req.get("destination_text")
                        or seeker_req.get("destination")
                )
                p_size = (
                        active_deal.get("party_size")
                        or seeker_req.get("party_size")
                        or seeker_req.get("passengers")
                )

                if is_valid(orig) and is_valid(dest):
                    lines.append(f"{get_label('route')}: {html_esc(str(orig))} ➡️ {html_esc(str(dest))}")
                if is_valid(p_size):
                    lines.append(f"{get_label('party_size')}: {html_esc(str(p_size))}")

            elif v_type == "housing":
                listing_p = listing.get("property_use") if listing else None
                seeker_p = seeker_req.get("property_use") if seeker_req else None

                p_use = (
                        active_deal.get("property_use")
                        or listing_p
                        or seeker_p
                )
                orig = (
                        active_deal.get("origin_text")
                        or active_deal.get("origin")
                        or seeker_req.get("location_text")
                        or seeker_req.get("origin")
                )

                if is_valid(listing_p):
                    lines.append(f"🏠 {get_label('offered_asset')}: {html_esc(get_prop_val(listing_p))}")
                elif is_valid(p_use):
                    lines.append(f"{get_label('property_use')}: {html_esc(get_prop_val(p_use))}")

                if is_valid(seeker_p) and seeker_p != listing_p:
                    lines.append(f"📋 {get_label('requested_asset')}: {html_esc(get_prop_val(seeker_p))}")

                if is_valid(orig):
                    lines.append(f"{get_label('location')}: {html_esc(str(orig))}")

            return "\n".join(lines)

        def _get_asset_title(target_lang: str) -> str:
            t_lang = _normalize_lang(target_lang) or "en"

            raw_title = active_deal.get("asset_title")
            title = raw_title if raw_title is not None else ""

            invalid_titles = {
                "Matched Asset Listing", "Matched Asset",
                "የተጣጣመ ንብረት", "የተጣመረ ንብረት",
                "Qabeenya Wal-qabate", "Qabeenya Wal-gitaa",
                "", None
            }

            def has_ethiopic_script(s: str) -> bool:
                return bool(s and any(4608 < ord(char) < 5023 for char in s))

            def is_invalid(t: str) -> bool:
                return not t or t in invalid_titles or (t_lang == "om" and has_ethiopic_script(t))

            def safely_format_title(source_obj) -> str:
                if not source_obj or not hasattr(self, 'format_asset_title'):
                    return ""

                func = getattr(self, 'format_asset_title')
                try:
                    sig = inspect.signature(func)
                    params = sig.parameters
                    kwargs = {}

                    param_names = list(params.keys())
                    if param_names:
                        kwargs[param_names[0]] = source_obj

                    if 'vertical_type' in params or any(p.kind == p.VAR_KEYWORD for p in params.values()):
                        if 'vertical_type' in locals() or 'vertical_type' in globals():
                            kwargs['vertical_type'] = vertical_type

                    if 'lang' in params:
                        kwargs['lang'] = t_lang
                    elif 'target_lang' in params:
                        kwargs['target_lang'] = t_lang

                    result = func(**kwargs)
                    return str(result) if result is not None else ""
                except Exception:
                    return ""

            if is_invalid(title):
                formatted = safely_format_title(listing)
                if formatted:
                    title = formatted

            if is_invalid(title):
                formatted = safely_format_title(seeker_req)
                if formatted:
                    title = formatted

            if is_invalid(title):
                title = (
                    "Qabeenya Wal-gitaa" if t_lang == "om"
                    else "የተጣጣመ ንብረት" if t_lang == "am"
                    else "Matched Asset Listing"
                )

            return title.strip()

        # ====================================================================
        # COMMISSION CALCULATION FULLY DELEGATED TO `BillingService`
        # ====================================================================
        billing_inst = getattr(self, 'billing', None) or locals().get('billing_service') or getattr(self,
                                                                                                    'billing_service',
                                                                                                    None)
        if not billing_inst:
            raise RuntimeError("BillingService instance is uninitialized.")

        commission_usd = 0.0
        commission_etb = 0.0

        try:
            # 1. Convert agreed price to ETB using BillingService.convert
            conv_res = await billing_inst.convert(
                amount=float(agreed_price),
                from_currency=currency,
                to_currency="ETB"
            )
            agreed_price_etb = conv_res.get("converted_amount", float(agreed_price))

            safe_vertical = vertical_type.lower().strip()
            logistics_domains = {'freight_ride', 'freight', 'logistics', 'ride'}

            # 2. Delegate to BillingService commission calculation functions
            if any(ld in safe_vertical for ld in logistics_domains):
                commission_etb = billing_inst.calculate_logistics_commission(agreed_price_etb, currency="ETB")
            else:
                commission_etb = billing_inst.calculate_asset_commission(agreed_price_etb, currency="ETB")

            # 3. Convert calculated ETB commission to USD using BillingService.convert
            usd_conv = await billing_inst.convert(
                amount=commission_etb,
                from_currency="ETB",
                to_currency="USD"
            )
            commission_usd = usd_conv.get("converted_amount", commission_etb / 160.82)

            # Enforce USD bounds
            commission_usd = max(float(billing_inst.MIN_COMMISSION_USD),
                                 min(commission_usd, float(billing_inst.MAX_COMMISSION_USD)))
            commission_etb = max(float(billing_inst.MIN_COMMISSION),
                                 min(commission_etb, float(billing_inst.MAX_ASSET_COMMISSION)))

        except Exception as calc_err:
            logger.warning(f"BillingService commission delegation fallback: {calc_err}")
            commission_etb = float(agreed_price) * 0.01 if currency == "ETB" else float(
                agreed_price) * 160.82 * 0.01
            commission_usd = commission_etb / 160.82

        formatted_comm_usd = f"{commission_usd:,.2f}"
        formatted_comm_etb = f"{commission_etb:,.2f}"
        raw_formatted_price = f"{agreed_price:,.2f}"
        safe_agreed_price = escape_md(raw_formatted_price)

        try:
            seeker_phone = None
            provider_phone = None

            if supabase_client:

                try:
                    s_res = await supabase_client.table('users').select('phone_number').eq(
                        'telegram_id', seeker_chat_id
                    ).maybe_single().execute()
                    if s_res and getattr(s_res, 'data', None):
                        seeker_phone = s_res.data.get('phone_number')
                except Exception as e:
                    logger.warning(f"Could not fetch seeker phone for {seeker_chat_id}: {e}")

                try:
                    p_res = await supabase_client.table('users').select('phone_number').eq(
                        'telegram_id', provider_chat_id
                    ).maybe_single().execute()
                    if p_res and getattr(p_res, 'data', None):
                        provider_phone = p_res.data.get('phone_number')
                except Exception as e:
                    logger.warning(f"Could not fetch provider phone for {provider_chat_id}: {e}")

                if not provider_phone and list_id:
                    try:
                        l_res = await supabase_client.table('provider_listings').select(
                            'contact_phone'
                        ).eq('id', list_id).maybe_single().execute()
                        if l_res and getattr(l_res, 'data', None):
                            provider_phone = l_res.data.get('contact_phone')
                    except Exception as e:
                        logger.warning(f"Could not fetch provider phone fallback: {e}")

                parsed_weight = 0.0
                if cargo_weight_kgs not in (None, ""):
                    try:
                        parsed_weight = float(re.sub(r"[^\d.]", "", str(cargo_weight_kgs)))
                    except (ValueError, TypeError):
                        parsed_weight = 0.0

                orig_loc = active_deal.get("origin") or seeker_req.get("origin") or ""
                dest_loc = active_deal.get("destination") or seeker_req.get("destination") or ""

                deal_updates = {
                    'agreed_price': agreed_price,
                    'current_price': agreed_price,
                    'currency': currency,
                    'seeker_phone': seeker_phone,
                    'provider_phone': provider_phone,
                    'origin': orig_loc,
                    'destination': dest_loc,
                    'cargo_weight_kgs': parsed_weight,
                    'plate_number': str(vehicle_plate) if vehicle_plate else None,
                    'requested_quantity': active_deal.get('requested_quantity', 1)
                }

                await supabase_client.table('deals').update(deal_updates).eq('id', deal_id_str).execute()

                commission_record = {
                    'deal_id': deal_id_str,
                    'user_id': seeker_chat_id,
                    'gross_amount': agreed_price,
                    'commission_fee': commission_etb if currency == "ETB" else commission_usd,
                    'currency_code': currency,
                    'processed_at': datetime.now(timezone.utc).isoformat()
                }
                try:
                    await supabase_client.table('commissions').upsert(
                        commission_record, on_conflict='deal_id'
                    ).execute()
                except Exception as comm_err:
                    logger.warning(f"Could not insert commission entry for deal {deal_id_str}: {comm_err}")

                if list_id and 'ReservationHelper' in globals():
                    FIXED_ASSETS = {'housing', 'house', 'vehicle', 'equipment', 'equipment_rental'}
                    safe_vertical = str(vertical_type or "general").lower()
                    if any(fa in safe_vertical for fa in FIXED_ASSETS):
                        req_qty = int(active_deal.get('requested_quantity', 1))
                        await ReservationHelper.reserve_fixed_asset_quantity(
                            supabase=supabase_client,
                            listing_id=list_id,
                            deal_id=deal_id_str,
                            requested_qty=req_qty
                        )

            # ✅ Delegate commission billing initialization to BillingService
            checkout_url = None
            try:
                checkout_url = await asyncio.wait_for(
                    billing_inst.initiate_commission_billing(
                        deal_id=str(deal_id_str),
                        agreed_price=float(agreed_price),
                        seeker_id=seeker_chat_id,
                        target_payment_currency=str(currency)
                    ),
                    timeout=6.0
                )
            except Exception as bill_err:
                logger.error(f"Failed to generate checkout link for deal {deal_id_str}: {bill_err}")

            bot_instance = None
            if hasattr(self, 'telegram_app') and self.telegram_app and hasattr(self.telegram_app, 'bot'):
                bot_instance = self.telegram_app.bot
            elif context and hasattr(context, 'bot') and context.bot:
                bot_instance = context.bot
            elif hasattr(self, 'bot') and self.bot:
                bot_instance = self.bot

            if user_id == provider_chat_id:
                p_title = _get_asset_title(p_lang)
                p_details = _build_sanitized_details_block(p_lang)

                if is_reverse:
                    if p_lang == "om":
                        provider_confirmation = f"✅ Gatii jijjiiramaa <i>{html_esc(p_title)}</i>-f <b>{safe_agreed_price} {safe_currency}</b> fudhatteetta. Kafaltii komishinii barbaaddichaa eeggachaa jira.\n\n{p_details}"
                    elif p_lang == "am":
                        provider_confirmation = f"✅ ለ <i>{html_esc(p_title)}</i> የ <b>{safe_agreed_price} {safe_currency}</b> ተቃራኒ ዋጋ ተቀብለዋል። የፈላጊው የኮሚሽን ክፍያ በመጠባበቅ ላይ።\n\n{p_details}"
                    else:
                        provider_confirmation = f"✅ You accepted the counter-offer of <b>{safe_agreed_price} {safe_currency}</b> for <i>{html_esc(p_title)}</i>. Awaiting seeker's commission payment..\n\n{p_details}"
                else:
                    if p_lang == "om":
                        provider_confirmation = f"✅ Dhiheessi <i>{html_esc(p_title)}</i>-f <b>{safe_agreed_price} {safe_currency}</b> fudhatteetta. Kafaltii komishinii barbaaddichaa eeggachaa jira.\n\n{p_details}"
                    elif p_lang == "am":
                        provider_confirmation = f"✅ ለ <i>{html_esc(p_title)}</i> የ <b>{safe_agreed_price} {safe_currency}</b> ማቅረቢያን ተቀብለዋል። የፈላጊው የኮሚሽን ክፍያ በመጠባበቅ ላይ።\n\n{p_details}"
                    else:
                        provider_confirmation = f"✅ You accepted the offer of <b>{safe_agreed_price} {safe_currency}</b> for <i>{html_esc(p_title)}</i>. Awaiting seeker's commission payment..\n\n{p_details}"

                await edit_message_safe(
                    text=convert_md_to_html(provider_confirmation),
                    parse_mode="HTML"
                )

                s_title = _get_asset_title(s_lang)
                s_details = _build_sanitized_details_block(s_lang)

                if s_lang == "om":
                    btn_local = f"🇪🇹 Waaletii Biyya Keessaan Komishinii Kaffalaa ({formatted_comm_etb} ETB)"
                    btn_intl = f"🌍 Kaardii Idil-addunyaa ykn PayPal'n Komishinii Kaffalaa (${formatted_comm_usd} USD)"
                elif s_lang == "am":
                    btn_local = f"🇪🇹 በአገር ውስጥ ዋሌቶች ኮሚሽን ይክፈሉ ({formatted_comm_etb} ETB)"
                    btn_intl = f"🌍 በአለም አቀፍ ካርዶች ወይም ፔይፓል (PayPal) ኮሚሽን ይክፈሉ (${formatted_comm_usd} USD)"
                else:
                    btn_local = f"🇪🇹 Pay Commission via Local Wallets ({formatted_comm_etb} ETB)"
                    btn_intl = f"🌍 Pay Commission via International Cards / PayPal (${formatted_comm_usd} USD)"

                local_btn = (
                    InlineKeyboardButton(text=btn_local, url=checkout_url)
                    if checkout_url and str(checkout_url).startswith("http")
                    else InlineKeyboardButton(text=btn_local, callback_data=f"pay_local_{deal_id_str}")
                )
                seeker_billing_keyboard = [
                    [local_btn],
                    [InlineKeyboardButton(text=btn_intl, callback_data=f"pay_intl_{deal_id_str}")]
                ]

                if is_reverse:
                    if s_lang == "om":
                        seeker_billing_notice = f"🎉 <b>Gatiin Wal Jijjiirame Fudhatameera!</b>\n\nDhiyeessaan gatii jijjiiramaa <i>{html_esc(s_title)}</i>f <b>{safe_agreed_price} {safe_currency}</b> fudhateera.\n\n{s_details}\n\nOdeeffannoo bal'aa fi bilbila quunnamtii argachuuf, maaloo kaffaltii komishinii raawwachuuf filannoo kaffaltii gadii filadhu."
                    elif s_lang == "am":
                        seeker_billing_notice = f"🎉 <b>የመደራደሪያ ዋጋ ማቅረቢያ ተቀባይነት አግኝቷል!</b>\n\nአቅራቢው ለ <i>{html_esc(s_title)}</i> ያቀረቡትን የ <b>{safe_agreed_price} {safe_currency}</b> ተቃራኒ ዋጋ ተቀብሏል።\n\n{s_details}\n\nዝርዝር መረጃዎችን እና የመገናኛ ስልክ ቁጥር ለማግኘት፥ እባክዎ ከታች የክፍያ አማራጭ በመምረጥ የኮሚሽን ክፍያ ይፈጽሙ።"
                    else:
                        seeker_billing_notice = f"🎉 <b>Counter-Offer Accepted!</b>\n\nThe provider accepted your counter-offer of <b>{safe_agreed_price} {safe_currency}</b> for <i>{html_esc(s_title)}</i>.\n\n{s_details}\n\nTo view details and contact information, please select a payment method below to pay the commission."
                else:
                    if s_lang == "om":
                        seeker_billing_notice = f"🎉 <b>Dhiheessiin Fudhatameera!</b>\n\nDhiyeessaan dhiheessi kee <i>{html_esc(s_title)}</i>f <b>{safe_agreed_price} {safe_currency}</b> fudhateera.\n\n{s_details}\n\nOdeeffannoo bal'aa fi bilbila quunnamtii argachuuf, maaloo kaffaltii komishinii raawwachuuf filannoo kaffaltii gadii filadhu."
                    elif s_lang == "am":
                        seeker_billing_notice = f"🎉 <b>ማቅረቢያ ተቀባይነት አግኝቷል!</b>\n\nአቅራቢው ለ <i>{html_esc(s_title)}</i> የ <b>{safe_agreed_price} {safe_currency}</b> ማቅረቢያዎን ተቀብለዋል።\n\n{s_details}\n\nዝርዝር መረጃዎችን እና የመገናኛ ስልክ ቁጥር ለማግኘት፥ እባክዎ ከታች የክፍያ አማራጭ በመምረጥ የኮሚሽን ክፍያ ይፈጽሙ።"
                    else:
                        seeker_billing_notice = f"🎉 <b>Offer Accepted!</b>\n\nThe provider accepted your offer of <b>{safe_agreed_price} {safe_currency}</b> for <i>{html_esc(s_title)}</i>.\n\n{s_details}\n\nTo view details and contact information, please select a payment method below to pay the commission."

                if bot_instance:
                    asyncio.create_task(
                        self._dispatch_party_notification_async(
                            bot_instance=bot_instance,
                            chat_id=seeker_chat_id,
                            text_content=seeker_billing_notice,
                            reply_markup=InlineKeyboardMarkup(seeker_billing_keyboard)
                        )
                    )

            elif user_id == seeker_chat_id:
                s_title = _get_asset_title(s_lang)
                s_details = _build_sanitized_details_block(s_lang)

                if s_lang == "om":
                    btn_local = f"🇪🇹 Waaletii Biyya Keessaan Komishinii Kaffalaa ({formatted_comm_etb} ETB)"
                    btn_intl = f"🌍 Kaardii Idil-addunyaa ykn PayPal'n Komishinii Kaffalaa (${formatted_comm_usd} USD)"
                elif s_lang == "am":
                    btn_local = f"🇪🇹 በአገር ውስጥ ዋሌቶች ኮሚሽን ይክፈሉ ({formatted_comm_etb} ETB)"
                    btn_intl = f"🌍 በአለም አቀፍ ካርዶች ወይም ፔይፓል (PayPal) ኮሚሽን ይክፈሉ (${formatted_comm_usd} USD)"
                else:
                    btn_local = f"🇪🇹 Pay Commission via Local Wallets ({formatted_comm_etb} ETB)"
                    btn_intl = f"🌍 Pay Commission via International Cards / PayPal (${formatted_comm_usd} USD)"

                local_btn = (
                    InlineKeyboardButton(text=btn_local, url=checkout_url)
                    if checkout_url and str(checkout_url).startswith("http")
                    else InlineKeyboardButton(text=btn_local, callback_data=f"pay_local_{deal_id_str}")
                )
                seeker_billing_keyboard = [
                    [local_btn],
                    [InlineKeyboardButton(text=btn_intl, callback_data=f"pay_intl_{deal_id_str}")]
                ]

                if is_reverse:
                    if s_lang == "om":
                        seeker_confirmation = f"✅ <b>Dhiheessiin Fudhatameera!</b>\n\nDhiheessii <i>{html_esc(s_title)}</i>f <b>{safe_agreed_price} {safe_currency}</b> fudhatteetta.\n\n{s_details}\n\nOdeeffannoo bal'aa fi bilbila quunnamtii argachuuf, maaloo kaffaltii komishinii raawwachuuf filannoo kaffaltii gadii filadhaa."
                    elif s_lang == "am":
                        seeker_confirmation = f"✅ <b>ማቅረቢያ ተቀባይነት አግኝቷል!</b>\n\nለ <i>{html_esc(s_title)}</i> የቀረበውን የ <b>{safe_agreed_price} {safe_currency}</b> ማቅረቢያ ተቀብለዋል።\n\n{s_details}\n\nእባክዎን ዝርዝር መረጃ እና የስልክ ቁጥር ለማግኘት ከታች ያለውን ተመራጭ የክፍያ አማራጭ በመጠቀም ኮሚሽኑን ይክፈሉ።"
                    else:
                        seeker_confirmation = f"✅ <b>Offer Accepted!</b>\n\nYou accepted the offer of <b>{safe_agreed_price} {safe_currency}</b> for <i>{html_esc(s_title)}</i>.\n\n{s_details}\n\nTo view details and contact information, please select a payment method below to pay the commission."
                else:
                    if s_lang == "om":
                        seeker_confirmation = f"✅ <b>Gatiin Wal Jijjiirame Fudhatameera!</b>\n\nGatii jijjiiramaa <i>{html_esc(s_title)}</i>f <b>{safe_agreed_price} {safe_currency}</b> fudhatteetta.\n\n{s_details}\n\nOdeeffannoo bal'aa fi bilbila quunnamtii argachuuf, maaloo kaffaltii komishinii raawwachuuf filannoo kaffaltii gadii filadhaa."
                    elif s_lang == "am":
                        seeker_confirmation = f"✅ <b>የመደራደሪያ ዋጋ ማቅረቢያ ተቀባይነት አግኝቷል!</b>\n\nለ <i>{html_esc(s_title)}</i> የ <b>{safe_agreed_price} {safe_currency}</b> ተቃራኒ ዋጋ ተቀብለዋል።\n\n{s_details}\n\nእባክዎን ዝርዝር መረጃ እና የስልክ ቁጥር ለማግኘት ከታች ያለውን ተመራጭ የክፍያ አማራጭ በመጠቀም ኮሚሽኑን ይክፈሉ።"
                    else:
                        seeker_confirmation = f"✅ <b>Counter-Offer Accepted!</b>\n\nYou accepted the counter-offer of <b>{safe_agreed_price} {safe_currency}</b> for <i>{html_esc(s_title)}</i>.\n\n{s_details}\n\nTo view details and contact information, please select a payment method below to pay the commission."

                await edit_message_safe(
                    text=convert_md_to_html(seeker_confirmation),
                    reply_markup=InlineKeyboardMarkup(seeker_billing_keyboard),
                    parse_mode="HTML"
                )

                p_title = _get_asset_title(p_lang)
                p_details = _build_sanitized_details_block(p_lang)

                if is_reverse:
                    if p_lang == "om":
                        provider_notice = f"🎉 <b>Dhiheessiin Fudhatameera!</b>\n\nBarbaadaan dhiheessii keessan <i>{html_esc(p_title)}</i>f <b>{safe_agreed_price} {safe_currency}</b> fudhateera.\n\n{p_details}\n\nKafaltii komishinii barbaaddichaa eegaa jirra.."
                    elif p_lang == "am":
                        provider_notice = f"🎉 <b>ማቅረቢያ ተቀባይነት አግኝቷል!</b>\n\nፈላጊው ለ <i>{html_esc(p_title)}</i> ያቀረቡትን የ <b>{safe_agreed_price} {safe_currency}</b> ማቅረቢያ ተቀብሏል።\n\n{p_details}\n\nየፈላጊውን የኮሚሽን ክፍያ በመጠባበቅ ላይ"
                    else:
                        provider_notice = f"🎉 <b>Offer Accepted!</b>\n\nThe seeker accepted your offer of <b>{safe_agreed_price} {safe_currency}</b> for <i>{html_esc(p_title)}</i>.\n\n{p_details}\n\nWe are awaiting the seeker's commission payment."
                else:
                    if p_lang == "om":
                        provider_notice = f"🎉 <b>Gatiin Wal Jijjiirame Fudhatameera!</b>\n\nBitataan gatii jijjiiramaa <i>{html_esc(p_title)}</i>f <b>{safe_agreed_price} {safe_currency}</b> fudhateera.\n\n{p_details}\n\nKafaltii komishinii barbaaddichaa eegaa jirra.."
                    elif p_lang == "am":
                        provider_notice = f"🎉 <b>የመደራደሪያ ዋጋ ማቅረቢያ ተቀባይነት አግኝቷል!</b>\n\nፈላጊው ለ <i>{html_esc(p_title)}</i> ያቀረቡትን የ <b>{safe_agreed_price} {safe_currency}</b> ተቃራኒ ዋጋ ተቀብሏል።\n\n{p_details}\n\nየፈላጊውን የኮሚሽን ክፍያ በመጠባበቅ ላይ"
                    else:
                        provider_notice = f"🎉 <b>Counter-Offer Accepted!</b>\n\nThe seeker accepted your counter-offer of <b>{safe_agreed_price} {safe_currency}</b> for <i>{html_esc(p_title)}</i>.\n\n{p_details}\n\nWe are awaiting the seeker's commission payment."

                if bot_instance:
                    asyncio.create_task(
                        self._dispatch_party_notification_async(
                            bot_instance=bot_instance,
                            chat_id=provider_chat_id,
                            text_content=provider_notice
                        )
                    )

        except Exception as exc:
            logger.error(f"❌ Failed to execute deal acceptance for deal {deal_id_str}: {str(exc)}",
                         exc_info=True)
            err_msg = i18n_toast["general_error"].get(user_lang, i18n_toast["general_error"]["en"])

            try:
                await query.answer(text=err_msg, show_alert=True)
            except Exception:
                await edit_message_safe(text=err_msg)

    @staticmethod
    async def prompt_payment_method(
            update: Update,
            _context: ContextTypes.DEFAULT_TYPE,
            deal_id: str,
            amount_etb: float,
            amount_usd: float,
            lang: str = "en"
    ) -> None:
        """
        Presents the user with interactive inline buttons to choose between
        local Ethiopian wallets (ETB) and international payment rails (USD),
        revealing the exact USD commission upfront for international payers.
        Seamlessly handles photo and text cards to avoid 'There is no text in the message to edit' errors.
        """
        callback_query = update.callback_query
        if callback_query:
            try:
                # Immediate callback answer to unlock Telegram client UI
                await callback_query.answer()
            except Exception:
                pass

        # 1. Local Wallet Button Translation (ETB)
        btn_local = get_text(lang, "btn_pay_local", price=f"{amount_etb:,.2f}")
        if not btn_local or "[btn_pay_local]" in btn_local:
            if lang == "om":
                btn_local = f"🇪🇹 Waaletii Biyya Keessaan Komishinii Kaffalaa ({amount_etb:,.2f} ETB)"
            elif lang == "am":
                btn_local = f"🇪🇹 በአገር ውስጥ ዋሌቶች ኮሚሽን ይክፈሉ ({amount_etb:,.2f} ETB)"
            else:
                btn_local = f"💳 Pay via Telebirr / Local Wallets ({amount_etb:,.2f} ETB)"

        # 2. International Card/PayPal Button Translation (USD - Transparent Commission)
        btn_intl = get_text(lang, "btn_pay_intl", price=f"{amount_usd:,.2f}")
        if not btn_intl or "[btn_pay_intl]" in btn_intl:
            if lang == "om":
                btn_intl = f"🌍 Kaardii Idil-addunyaa / PayPal (${amount_usd:,.2f} USD)"
            elif lang == "am":
                btn_intl = f"🌍 ዓለም አቀፍ ካርድ / PayPal (${amount_usd:,.2f} USD)"
            else:
                btn_intl = f"🌍 Pay via Credit Card / PayPal (${amount_usd:,.2f} USD)"

        # 3. Dynamic Inline Keyboard Construction
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(text=btn_local, callback_data=f"pay_local_{deal_id}")],
            [InlineKeyboardButton(text=btn_intl, callback_data=f"pay_intl_{deal_id}")]
        ])

        # 4. Prompt Header Message Translation
        message_text = get_text(lang, "prompt_select_payment_method")
        if not message_text or "[prompt_select_payment_method]" in message_text:
            if lang == "om":
                message_text = (
                    "💳 <b>Mala Kaffaltii Filadhu</b>\n\n"
                    f"Komishinii idil-addunyaa: <b>${amount_usd:,.2f} USD</b>\n"
                    "Maaloo komishinii kee kaffaluuf mala kaffaltii armaan gadii filadhu:"
                )
            elif lang == "am":
                message_text = (
                    "💳 <b>የክፍያ ዘዴ ይምረጡ</b>\n\n"
                    f"ዓለም አቀፍ የኮሚሽን ክፍያ: <b>${amount_usd:,.2f} USD</b>\n"
                    "እባክዎ የኮሚሽን ክፍያዎን በጥንቃቄ ለማጠናቀቅ ከታች ያለውን የክፍያ አማራጭ ይምረጡ፡"
                )
            else:
                message_text = (
                    "💳 <b>Select Your Payment Method</b>\n\n"
                    f"International Commission Fee: <b>${amount_usd:,.2f} USD</b> (Zero hidden conversion fees)\n\n"
                    "Choose your preferred checkout gateway below to complete your commission fee securely:"
                )

        formatted_html = convert_md_to_html(message_text)

        # Fail-safe UI dispatch order
        if callback_query and callback_query.message:
            try:
                await callback_query.edit_message_caption(
                    caption=formatted_html,
                    reply_markup=keyboard,
                    parse_mode="HTML"
                )
            except Exception:
                try:
                    await callback_query.edit_message_text(
                        text=formatted_html,
                        reply_markup=keyboard,
                        parse_mode="HTML"
                    )
                except Exception:
                    await _context.bot.send_message(
                        chat_id=update.effective_chat.id,
                        text=formatted_html,
                        reply_markup=keyboard,
                        parse_mode="HTML"
                    )
        elif update.message:
            await update.message.reply_text(
                text=formatted_html,
                reply_markup=keyboard,
                parse_mode="HTML"
            )
        else:
            await _context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=formatted_html,
                reply_markup=keyboard,
                parse_mode="HTML"
            )

    async def handle_payment_checkout(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """
        Unified payment handler ensuring immediate callback response and visual button locking
        to eliminate double-clicks and button spinning during remote billing gateway execution.
        Features robust caption/text edit fallbacks to seamlessly support photo and text deal cards.
        """
        query = update.callback_query
        if not query:
            return

        cb_data = query.data or ""
        is_intl = cb_data.startswith("pay_intl_")
        prefix = "pay_intl_" if is_intl else "pay_local_"
        deal_id = cb_data.replace(prefix, "").strip()

        lang = self._get_lang(context) if hasattr(self, '_get_lang') else context.user_data.get('lang', 'en')

        # =========================================================================
        # ⚡ 1. IMMEDIATE CALLBACK ACKNOWLEDGEMENT (Prevents 30s timeout expiration)
        # =========================================================================
        ack_text = {
            "om": "Gara bakka kaffaltiitti isin ceesisaa jira..." if is_intl else "Gara kaffaltii naannootti isin ceesisaa jira...",
            "am": "ወደ ዓለም አቀፍ ክፍያ በመገናኘት ላይ..." if is_intl else "ወደ ሀገር ውስጥ ክፍያ በመገናኘት ላይ...",
            "en": "Connecting to global checkout..." if is_intl else "Connecting to local checkout..."
        }.get(lang, "Connecting to checkout...")

        try:
            await query.answer(ack_text)
        except Exception as ack_err:
            logger.warning(f"Could not answer callback query: {ack_err}")

        # =========================================================================
        # 🔒 2. IMMEDIATE UI LOCKING (Replaces buttons with a disabled processing state)
        # =========================================================================
        processing_label = {
            "om": "⏳ Kaffaltiin qophaa'aa jira... Maaloo eegaa",
            "am": "⏳ ክፍያ በመዘጋጀት ላይ ነው... እባክዎ ይቆዩ",
            "en": "⏳ Preparing checkout... Please wait"
        }.get(lang, "⏳ Preparing checkout... Please wait")

        lock_keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(text=processing_label, callback_data="noop_processing")]
        ])

        try:
            await query.edit_message_reply_markup(reply_markup=lock_keyboard)
        except Exception as lock_err:
            logger.warning(f"Could not apply temporary lock markup: {lock_err}")

        # Helper to edit caption first, text second, send message third
        async def edit_or_send(text_payload: str, reply_markup: Optional[InlineKeyboardMarkup] = None):
            if query and query.message:
                try:
                    await query.edit_message_caption(
                        caption=text_payload,
                        reply_markup=reply_markup,
                        parse_mode="HTML"
                    )
                except Exception:
                    try:
                        await query.edit_message_text(
                            text=text_payload,
                            reply_markup=reply_markup,
                            parse_mode="HTML"
                        )
                    except Exception:
                        await context.bot.send_message(
                            chat_id=update.effective_chat.id,
                            text=text_payload,
                            reply_markup=reply_markup,
                            parse_mode="HTML"
                        )
            else:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=text_payload,
                    reply_markup=reply_markup,
                    parse_mode="HTML"
                )

        billing_service = (
                context.bot_data.get("billing_service")
                or getattr(self, "billing", None)
                or getattr(self, "billing_service", None)
        )

        if not billing_service or not hasattr(billing_service, "initiate_commission_billing"):
            logger.error("handle_payment_checkout error: BillingService is uninitialized.")
            err_unavail = {
                "om": "❌ Tajaajilli kaffaltii yeroof hin jiru. Maaloo booda irra deebi'aa yaala.",
                "am": "❌ የክፍያ አገልግሎቱ ለጊዜው አይሰራም። እባክዎቆይተው እንደገና ይሞክሩ።",
                "en": "❌ Payment service is temporarily unavailable. Please try again later."
            }.get(lang, "❌ Payment service is temporarily unavailable. Please try again later.")
            await edit_or_send(err_unavail)
            return

        user = query.from_user
        target_currency = "USD" if is_intl else "ETB"

        # =========================================================================
        # ⚙️ 3. HEAVY API & DATABASE WORK (Safely executed after query is already answered)
        # =========================================================================
        try:
            db_service = getattr(billing_service, "db_client", None) or getattr(self, "db", None)
            agreed_price = 0.0

            if db_service and hasattr(db_service, "table"):
                res = await db_service.table('deals').select('agreed_price').eq('id', deal_id).maybe_single().execute()
                if res and getattr(res, 'data', None):
                    agreed_price = float(res.data.get('agreed_price', 0.0))

            checkout_url = await billing_service.initiate_commission_billing(
                deal_id=deal_id,
                agreed_price=agreed_price,
                seeker_id=user.id,
                target_payment_currency=target_currency
            )

            if not checkout_url or not str(checkout_url).startswith("http"):
                logger.error(f"Failed to obtain valid checkout URL for deal {deal_id}. Returned: {checkout_url}")
                err_gateway = {
                    "om": "⚠️ Liinkii kaffaltii uumuu hin danda'amne. Maaloo irra deebi'a yaala.",
                    "am": "⚠️ የክፍያ ሊንክ ማመንጨት አልተቻለም። እባክዎ እንደገና ይሞክሩ።",
                    "en": "⚠️ Unable to generate payment gateway link at this moment. Please try again or choose another payment method."
                }.get(lang, "⚠️ Unable to generate payment gateway link at this moment.")
                await edit_or_send(err_gateway)
                return

            display_fee_str = "calculated fee"
            if db_service and hasattr(db_service, "table"):
                inv_res = await db_service.table('invoices').select('amount, currency').eq('deal_id', deal_id).order(
                    'created_at', desc=True).limit(1).execute()
                if inv_res and inv_res.data:
                    inv = inv_res.data[0]
                    display_fee_str = f"{float(inv['amount']):,.2f} {inv['currency']}"

            # =========================================================================
            # 💳 4. FINAL UI RENDER WITH DIRECT GATEWAY URL
            # =========================================================================
            if is_intl:
                btn_text = (
                    "🌍 Fuula Kaffaltii USD Idil-addunyaa" if lang == "om"
                    else "🌍 ዓለም አቀፍ የUSD መክፈያ ገጽ" if lang == "am"
                    else "🌍 Global USD Checkout Page"
                )
                header_str = "🔗 <b>International Payment Ready (USD)</b>"
                footer_text = (
                    f"Commission Fee: <b>{display_fee_str}</b>\n\n"
                    "Odeeffannoo bal'aa fi bilbila quunnamtii argachuuf, maaloo kaffaltii komishinii USD furtuu amansiisaa gadii tuquun kaffala (fkn, Visa, Mastercard, ykn PayPal'n)." if lang == "om"
                    else f"የኮሚሽን ክፍያ: <b>{display_fee_str}</b>\n\nዝርዝር መረጃ እና ስልክ ለማግኘት፣ እባክዎ ከታች ያለውን አስተማማኝ ቁልፍ በመጫን ኮሚሽኑን በUSD ይክፈሉ (ለምሳሌ በVisa፣ Mastercard ወይም PayPal)።" if lang == "am"
                    else f"Exact Commission: <b>{display_fee_str}</b>\n\nTo get detailed information and contact numbers, please pay the commission in USD by clicking the secure button below (e.g., via Visa, Mastercard, or PayPal)."
                )
            else:
                btn_text = (
                    "🇪🇹 Kaffaltii Naannoo Banuuf" if lang == "om"
                    else "🇪🇹 የሀገር ውስጥ ክፍያ ይክፈቱ" if lang == "am"
                    else "🇪🇹 Open Telebirr / CBE Checkout"
                )
                header_str = "🔗 <b>Local Payment Ready</b>"
                footer_text = (
                    f"Commission Fee: <b>{display_fee_str}</b>\n\n"
                    "Kaffaltii kee xumuruuf liinkii armaan gadii tuqi." if lang == "om"
                    else f"የኮሚሽን ክፍያ: <b>{display_fee_str}</b>\n\nክፍያዎን ለማጠናቀቅ ከታች ያለውን ደህንነቱ የተጠበቀ ቁልፍ ይጫኑ።" if lang == "am"
                    else f"Commission Fee: <b>{display_fee_str}</b>\n\nClick the secure button below to complete your payment."
                )

            keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(text=btn_text, url=str(checkout_url))]])
            msg_text = f"{header_str}\n\n{footer_text}"

            await edit_or_send(msg_text, reply_markup=keyboard)

        except Exception as exc:
            logger.error(f"Failed to process payment checkout callback for deal {deal_id}: {exc}", exc_info=True)
            err_failed = {
                "om": "❌ Liinkii kaffaltii uumuun hinda'amne. Maaloo irra deebi'a yaala.",
                "am": "❌ የክፍያ ሊንክ መፍጠር አልተቻለም። እባክዎ እንደገና ይሞክሩ።",
                "en": "❌ Failed to generate payment link. Please try again or contact support."
            }.get(lang, "❌ Failed to generate payment link. Please try again or contact support.")

            await edit_or_send(err_failed)

    async def _execute_deal_termination(
            self,
            query: CallbackQuery,
            _context: ContextTypes.DEFAULT_TYPE,
            deal_id_str: str,
            deal: dict
    ) -> None:
        logger.info(f"Terminating deal {deal_id_str} | Raw deal data: {deal}")

        user_id = query.from_user.id
        provider_chat_id = int(deal["provider_id"])
        seeker_chat_id = int(deal["seeker_id"])

        logger.info(
            f"Termination actors -> Acting User: {user_id} | Provider: {provider_chat_id} | Seeker: {seeker_chat_id}"
        )

        is_reverse = (
                bool(deal.get("is_reverse") or deal.get("is_reverse_match"))
                or str(deal.get("match_type", "")).lower() == "reverse"
                or str(deal.get("match_direction", "")).lower() == "reverse"
                or str(deal.get("direction", "")).lower() == "reverse"
        )
        if not is_reverse and isinstance(deal.get("negotiation_history"), list) and deal.get("negotiation_history"):
            first_step = deal["negotiation_history"][0]
            if isinstance(first_step, dict) and first_step.get("match_type") == "reverse":
                is_reverse = True

        supabase_client = getattr(self.db, 'supabase', None)

        async def _resolve_lang(chat_id: int, stored_lang: str | None) -> str:
            if stored_lang and stored_lang in ["en", "am", "om"]:
                return stored_lang.lower().strip()

            db_lang = None
            if supabase_client:
                try:
                    res = await supabase_client.table('users').select('language, preferred_language').eq('telegram_id',
                                                                                                         chat_id).maybe_single().execute()
                    if res and res.data:
                        db_lang = res.data.get('preferred_language') or res.data.get('language')
                except Exception as lang_err:
                    logger.warning(f"Failed to fetch language for user {chat_id}: {lang_err}")

            if db_lang and str(db_lang).lower().strip() in ["en", "am", "om"]:
                return str(db_lang).lower().strip()
            return "en"

        s_lang = await _resolve_lang(seeker_chat_id, deal.get("seeker_lang"))
        p_lang = await _resolve_lang(provider_chat_id, deal.get("provider_lang"))

        if user_id == provider_chat_id:
            acting_lang = p_lang
            opposing_chat_id = seeker_chat_id
            opposing_lang = s_lang
        elif user_id == seeker_chat_id:
            acting_lang = s_lang
            opposing_chat_id = provider_chat_id
            opposing_lang = p_lang
        else:
            acting_lang = "en"
            opposing_chat_id = seeker_chat_id
            opposing_lang = s_lang

        logger.info(f"Resolved opposing party -> Opposing Chat ID: {opposing_chat_id} | Opposing Lang: {opposing_lang}")

        await self.db.update_deal_status(deal_id_str, "cancelled")

        declined_self_msg = get_text(acting_lang, "notice_deal_declined_self")
        if not declined_self_msg or "[notice_deal_declined_self]" in declined_self_msg:
            if is_reverse:
                declined_self_msg = (
                    "❌ <b>Dhiheessii/gaaffii kana diddeetta.</b>" if acting_lang == "om"
                    else "❌ <b>ይህን ጥያቄ/ማቅረቢያ ውድቅ አድርገዋል።</b>" if acting_lang == "am"
                    else "❌ <b>You have declined this request/offer.</b>"
                )
            else:
                declined_self_msg = (
                    "❌ <b>Waliigaltee kana diddeetta.</b>" if acting_lang == "om"
                    else "❌ <b>ይህን ስምምነት ውድቅ አድርገዋል።</b>" if acting_lang == "am"
                    else "❌ <b>You have declined this deal.</b>"
                )

        # =========================================================================
        # SAFE SELF-MESSAGE EDIT / CAPTION DISPATCH (PREVENTS SILENT FAILURE)
        # =========================================================================
        has_photo = bool(query.message and getattr(query.message, "photo", None))
        edit_success = False

        if has_photo:
            try:
                await query.edit_message_caption(caption=declined_self_msg, parse_mode="HTML")
                edit_success = True
            except Exception as photo_err:
                logger.warning(f"Could not edit message caption: {photo_err}")
                try:
                    clean_msg = declined_self_msg.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace(
                        "</i>", "")
                    await query.edit_message_caption(caption=clean_msg, parse_mode=None)
                    edit_success = True
                except Exception as photo_fallback_err:
                    logger.warning(f"Plain text caption edit also failed: {photo_fallback_err}")
        else:
            try:
                await query.edit_message_text(text=declined_self_msg, parse_mode="HTML")
                edit_success = True
            except Exception as text_err:
                logger.warning(f"Could not edit self message text: {text_err}")
                try:
                    clean_msg = declined_self_msg.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace(
                        "</i>", "")
                    await query.edit_message_text(text=clean_msg, parse_mode=None)
                    edit_success = True
                except Exception as text_fallback_err:
                    logger.warning(f"Plain text message edit also failed: {text_fallback_err}")

        # ✅ Type-Safe Fallback: Using _context.bot.send_message instead of query.message.reply_text
        if not edit_success:
            try:
                await _context.bot.send_message(
                    chat_id=query.from_user.id,
                    text=declined_self_msg,
                    parse_mode="HTML"
                )
            except Exception as reply_err:
                logger.error(f"Failed fallback message dispatch to acting user: {reply_err}")

        # Cascading Bot instance resolution
        bot = (
                getattr(_context, "bot", None)
                or getattr(query, "bot", None)
                or (getattr(self, "telegram_app", None) and getattr(self.telegram_app, "bot", None))
                or getattr(self, "bot", None)
        )

        if not bot:
            logger.error("CRITICAL: Unable to resolve Telegram Bot instance for termination alert.")
            return

        # =========================================================================
        # OPPOSING USER NOTIFICATION DISPATCH
        # =========================================================================
        if opposing_chat_id and opposing_chat_id > 0:
            declined_opp_msg = get_text(opposing_lang, "notice_deal_declined_opposing")
            if not declined_opp_msg or "[notice_deal_declined_opposing]" in declined_opp_msg:
                if is_reverse:
                    if user_id == provider_chat_id:
                        declined_opp_msg = (
                            "❌ <b>Dhiyeessaan dhiheessii/gaaffii kee diddeera.</b>" if opposing_lang == "om"
                            else "❌ <b>አቅራቢው ጥያቄዎን ውድቅ አድርጎታል።</b>" if opposing_lang == "am"
                            else "❌ <b>The provider has declined your request.</b>"
                        )
                    else:
                        declined_opp_msg = (
                            "❌ <b>Barbaadaan dhiheessii keessan diddeera.</b>" if opposing_lang == "om"
                            else "❌ <b>ፈላጊው ያቀረቡትን ማቅረቢያ ውድቅ አድርጎታል።</b>" if opposing_lang == "am"
                            else "❌ <b>The seeker has declined your offer.</b>"
                        )
                else:
                    declined_opp_msg = (
                        "❌ <b>Gama biraatiin walii galtee kana diddeera.</b>" if opposing_lang == "om"
                        else "❌ <b>ተቃራኒው አካል ስምምነቱን ውድቅ አድርጎታል።</b>" if opposing_lang == "am"
                        else "❌ <b>The other party has declined the deal.</b>"
                    )

            try:
                await bot.send_message(
                    chat_id=opposing_chat_id,
                    text=declined_opp_msg,
                    parse_mode="HTML"
                )
                logger.info(f"Successfully dispatched deal termination notice to opposing user {opposing_chat_id}")
            except Exception as html_err:
                logger.warning(
                    f"HTML send failed for opposing user {opposing_chat_id}, falling back to plain text: {html_err}"
                )
                clean_opp_msg = (
                    declined_opp_msg.replace("<b>", "")
                    .replace("</b>", "")
                    .replace("<i>", "")
                    .replace("</i>", "")
                )
                await bot.send_message(
                    chat_id=opposing_chat_id,
                    text=clean_opp_msg,
                    parse_mode=None
                )
        else:
            logger.error(f"CRITICAL: Invalid opposing_chat_id ({opposing_chat_id}). Cannot dispatch termination alert.")

    # ------------------------------------------------------------------------
    # CANCELLATION HANDLERS
    # ------------------------------------------------------------------------
    @classmethod
    async def cancel_negotiation_sequence(cls, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()

        lang = context.user_data.get("language", "en") if context.user_data else "en"
        cancel_msg = get_text(lang, "msg_negotiation_cancelled") or "❌ Negotiation action cancelled."
        await query.edit_message_text(cancel_msg, parse_mode="Markdown")

        context.user_data.pop("active_negotiation_id", None)
        context.user_data.pop("negotiation_role", None)
        context.user_data.pop("awaiting_counter_input", None)
        context.user_data.pop("intent_to_counter", None)

    @classmethod
    async def cancel_negotiation_command(cls, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if context.user_data.get("active_negotiation_id"):
            lang = context.user_data.get("language", "en") if context.user_data else "en"
            cancel_msg = get_text(lang, "msg_negotiation_cancelled") or "❌ Action cancelled."

            context.user_data.pop("active_negotiation_id", None)
            context.user_data.pop("negotiation_role", None)
            context.user_data.pop("awaiting_counter_input", None)
            context.user_data.pop("intent_to_counter", None)

            await update.message.reply_text(cancel_msg, parse_mode="Markdown")
            raise ApplicationHandlerStop()
