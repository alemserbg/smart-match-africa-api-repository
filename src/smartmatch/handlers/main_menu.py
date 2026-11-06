import inspect
import logging
import re
import time
from typing import List, Any

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    Message,
    BotCommand
)
from telegram.error import BadRequest
from telegram.ext import (
    ContextTypes,
    MessageHandler,
    ConversationHandler,
    CommandHandler,
    ChatMemberHandler,
    CallbackQueryHandler,
    filters
)

# Import the centralized translation engine and reverse-lookup helpers
from src.smartmatch.utils.translations import get_text, get_keyboard_labels

# Initialize production-grade structured logger
logger = logging.getLogger("smartmatch.handlers.main_menu")


class MainMenuHandler:
    """
    Manages the permanent, persistent ReplyKeyboardMarkup menu interface with full
    multilingual support.
    """

    def __init__(
            self,
            vehicle_handler: Any,
            equipment_handler: Any,
            freight_ride_handler: Any,
            housing_handler: Any,
            db: Any = None,
            supabase: Any = None
    ) -> None:
        if not all([vehicle_handler, equipment_handler, freight_ride_handler, housing_handler]):
            logger.critical("🚨 Core Domain Injection Scarcity: Dependent vertical domains are uninstantiated.")
            raise ValueError("MainMenuHandler requires all injected vertical handlers to be completely initialized.")

        self.vehicle_h = vehicle_handler
        self.equipment_h = equipment_handler
        self.freight_ride_h = freight_ride_handler
        self.housing_h = housing_handler
        self.db = db
        self.supabase = supabase

        logger.info("✅ MainMenuHandler successfully instantiated.")

    @staticmethod
    def _md_to_html(text: str) -> str:
        """
        Converts Markdown syntax (*bold*, _italic_, > blockquote) to HTML
        to prevent raw markup leaking when sending messages with parse_mode='HTML'.
        """
        if not text:
            return ""

        # Convert line-based Markdown blockquotes (> text) to HTML <blockquote>
        lines = text.split('\n')
        converted_lines = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith('>'):
                cleaned = stripped.lstrip('>').strip()
                converted_lines.append(f"<blockquote>{cleaned}</blockquote>")
            else:
                converted_lines.append(line)
        text = '\n'.join(converted_lines)

        # Convert *bold* to <b>bold</b>
        text = re.sub(r'\*(.*?)\*', r'<b>\1</b>', text)
        # Convert _italic_ to <i>italic</i>
        text = re.sub(r'_(.*?)_', r'<i>\1</i>', text)
        # Clean separation between adjacent tags and words
        text = re.sub(r'(</b>|</i>|</blockquote>)([^\s\n])', r'\1 \2', text)
        return text

    @staticmethod
    def reset_user_flow(context: ContextTypes.DEFAULT_TYPE, new_vertical: str):
        stale_keys = [
            'vertical_type', 'transaction_type', 'property_use',
            'min_bedrooms', 'min_sq_meters', 'cargo_weight_kgs',
            'party_size', 'vehicle_type', 'make_model_year',
            'max_budget', 'required_amenities', 'location_text',
            'price', 'bulk_quantity'
        ]
        for key in stale_keys:
            context.user_data.pop(key, None)

        context.user_data['vertical_type'] = new_vertical
        logger.info(f"🧹 User session wiped. Switched active vertical to: {new_vertical}")

    @classmethod
    def get_labels_for_action(cls, action_key: str) -> List[str]:
        return get_keyboard_labels(action_key)

    @classmethod
    def get_persistent_keyboard(cls, lang: str = "en") -> ReplyKeyboardMarkup:
        keyboard = [
            [
                KeyboardButton(get_text(lang, "btn_list_house")),
                KeyboardButton(get_text(lang, "btn_find_house")),
            ],
            [
                KeyboardButton(get_text(lang, "btn_list_vehicle")),
                KeyboardButton(get_text(lang, "btn_find_vehicle")),
            ],
            [
                KeyboardButton(get_text(lang, "btn_list_equipment")),
                KeyboardButton(get_text(lang, "btn_find_equipment")),
            ],
            [
                KeyboardButton(get_text(lang, "btn_freight")),
                KeyboardButton(get_text(lang, "btn_ride")),
            ],
        ]
        return ReplyKeyboardMarkup(
            keyboard, resize_keyboard=True, is_persistent=True
        )

    @staticmethod
    def get_language_inline_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🇬🇧 English", callback_data="set_lang:en"),
                InlineKeyboardButton("🇪🇹 አማርኛ (Amharic)", callback_data="set_lang:am"),
                InlineKeyboardButton("🌳 Afaan Oromoo", callback_data="set_lang:om")
            ]
        ])

    @classmethod
    async def setup_bot_commands(cls, application: Any) -> None:
        try:
            commands = [
                BotCommand("start", "🚀 Start / Main Menu"),
                BotCommand("menu", "📋 Show Persistent Main Menu"),
                BotCommand("language", "🌐 Change Language / ቋንቋ ቀይር"),
                BotCommand("setlang", "🌐 Set Language Preference"),
                BotCommand("cancel", "❌ Cancel Current Action")
            ]
            await application.bot.set_my_commands(commands)
            logger.info("Native Telegram escape commands initialized successfully.")
        except Exception as e:
            logger.error(f"CRITICAL: Failed to register native system command arrays: {str(e)}", exc_info=True)

    async def _hydrate_user_language(self, user_id: int, context: ContextTypes.DEFAULT_TYPE) -> str:
        if context.user_data and context.user_data.get("lang"):
            return context.user_data["lang"]

        db = (
                getattr(self, "db", None)
                or (context.bot_data.get("db") if context.bot_data else None)
                or (
                    context.application.bot_data.get("db")
                    if context.application and context.application.bot_data
                    else None
                )
        )

        lang_code = "en"
        if db and hasattr(db, "get_user_language"):
            try:
                res = db.get_user_language(user_id)
                if inspect.isawaitable(res):
                    res = await res
                if res and res in ["en", "am", "om"]:
                    lang_code = res
            except Exception as e:
                logger.warning(f"Could not fetch user language from DB for User {user_id}: {e}")

        if context.user_data is not None:
            context.user_data["lang"] = lang_code
            context.user_data["language"] = lang_code

        return lang_code

    async def select_preferred_language(self, update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
        prompt_text = (
            "👋 **Welcome to Smart Match Africa!**\n\n"
            "Please select your preferred language to continue:\n"
            "እባክዎ ቋንቋ ይምረጡ:\n"
            "Maaloo afaan barbaaddan filadhaa:"
        )

        if update.message:
            await update.message.reply_text(
                text=prompt_text,
                reply_markup=self.get_language_inline_keyboard(),
                parse_mode="Markdown"
            )
        elif update.callback_query:
            try:
                await update.callback_query.answer()
                await update.callback_query.edit_message_text(
                    text=prompt_text,
                    reply_markup=self.get_language_inline_keyboard(),
                    parse_mode="Markdown"
                )
            except BadRequest as br_err:
                err_msg = str(br_err).lower()
                if "message is not modified" in err_msg or "message to edit not found" in err_msg:
                    logger.debug(f"Inline language selector state already synced: {br_err}")
                else:
                    logger.warning(f"BadRequest while editing language inline keyboard: {br_err}")
            except Exception as e:
                logger.error(f"Error updating language inline display context: {str(e)}")

    async def show_main_menu(
            self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        """Core UI deployment engine."""
        user_id = update.effective_user.id if update.effective_user else None

        if context.user_data is None:
            context.user_data = {}

        chat_id = update.effective_chat.id if update.effective_chat else None

        if not chat_id:
            logger.error(
                f"Execution Error: Missing target chat context for User ID {user_id}"
            )
            return ConversationHandler.END

        # Anti-Duplication Lock: Prevent duplicate menu triggers within 3 seconds
        now = time.time()
        last_sent = context.user_data.get("_last_menu_sent_time", 0)
        if now - last_sent < 3.0:
            logger.debug(f"Anti-duplication block: Suppressed duplicate show_main_menu call for User {user_id}")
            return ConversationHandler.END
        context.user_data["_last_menu_sent_time"] = now

        # Phase 1: Re-hydrate user language preference from DB if missing
        if user_id and not context.user_data.get("lang"):
            await self._hydrate_user_language(user_id, context)

            # Intercept Brand New Users without language configuration
            if context.user_data.get("is_new_user") and not context.user_data.get("lang"):
                logger.info(
                    f"New User {user_id} detected. Redirecting to onboarding language selector."
                )
                context.user_data["is_new_user"] = False
                await self.select_preferred_language(update, context)
                return ConversationHandler.END

        lang = context.user_data.get("lang", "en")
        logger.info(
            f"User {user_id} ({lang}) requested persistent main marketplace deployment."
        )

        # Phase 2: Anti-Bleeding State Management - Purge transactional workflow variables
        stale_keys = [
            key for key in context.user_data.keys()
            if key.startswith("flow_step_") or key in (
                "current_flow", "waiting_for_input", "active_negotiation", "in_flow"
            )
        ]

        for key in stale_keys:
            context.user_data.pop(key, None)

        # Phase 3: Clean up inline keyboard prompt safely
        if update.callback_query:
            try:
                await update.callback_query.answer()
            except Exception:
                pass

            if isinstance(update.callback_query.message, Message):
                try:
                    await update.callback_query.message.delete()
                except BadRequest as br_err:
                    err_msg = str(br_err).lower()
                    if (
                            "message to delete not found" in err_msg
                            or "message can't be deleted" in err_msg
                    ):
                        try:
                            await update.callback_query.edit_message_reply_markup(
                                reply_markup=None
                            )
                        except Exception:
                            pass

        # Phase 4: Fetch text and sanitize Markdown to HTML to eliminate raw asterisks/brackets
        raw_welcome = get_text(lang, "welcome_back") or (
            "⚡ <b>WELCOME TO SMART MATCH AFRICA!</b> ⚡\n\n"
            "Our permanent menu is locked at the bottom of your screen.\n\n"
            "<b>Quick Capabilities:</b>\n"
            "• 🏠 <b>Housing:</b> List or find residential & commercial properties\n"
            "• 🚘 <b>Vehicles:</b> Offer or request personal & commercial vehicles\n"
            "• 🚜 <b>Equipment:</b> Heavy machinery & agricultural equipment\n"
            "• 📦 <b>Logistics:</b> Freight transport & city ride services\n\n"
            "👇 <b>Select an option from the keyboard below to start:</b>"
        )

        welcome_text = self._md_to_html(raw_welcome)

        await context.bot.send_message(
            chat_id=chat_id,
            text=welcome_text,
            reply_markup=self.get_persistent_keyboard(lang),
            parse_mode="HTML",
            read_timeout=30.0,
            write_timeout=30.0
        )
        return ConversationHandler.END

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        user_id = update.effective_user.id if update.effective_user else None
        invite_payload = context.args[0] if context.args else None

        context.user_data.clear()

        if invite_payload:
            context.user_data["invite_payload"] = invite_payload
            logger.info(f"Captured deep-link referral parameter: {invite_payload}")

        if user_id:
            await self._hydrate_user_language(user_id, context)

        await self.show_main_menu(update, context)
        return ConversationHandler.END

    async def language_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id if update.effective_chat else None
        if not chat_id:
            return

        prompt_text = "🌐 **Select Your Language / ቋንቋ ይምረጡ / Afaan Filadhaa:**"

        try:
            if update.message:
                await update.message.reply_text(
                    text=prompt_text,
                    reply_markup=self.get_language_inline_keyboard(),
                    parse_mode="Markdown",
                    read_timeout=30.0,
                    write_timeout=20.0,
                    connect_timeout=15.0
                )
            elif update.callback_query and update.effective_chat:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=prompt_text,
                    reply_markup=self.get_language_inline_keyboard(),
                    parse_mode="Markdown",
                    read_timeout=30.0,
                    write_timeout=30.0,
                    connect_timeout=30.0
                )
        except Exception as e:
            logger.error(f"Failed to send language prompt in language_command: {e}")
            try:
                # Fallback alert if the primary message fails due to timeout
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="⚠️ Connection timed out while loading language options. Please try typing /setlang again."
                )
            except Exception:
                pass

    async def handle_language_callback(
            self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        query = update.callback_query
        if not query:
            return

        try:
            data = query.data or "set_lang:en"
            lang_code = data.replace("set_lang:", "").replace("lang_", "").strip()
            if lang_code not in ["en", "am", "om"]:
                lang_code = "en"

            user_id = update.effective_user.id if update.effective_user else None

            if context.user_data is None:
                context.user_data = {}

            context.user_data["lang"] = lang_code
            context.user_data["language"] = lang_code
            context.user_data["is_new_user"] = False

            toast_msg = {
                "en": "✅ Language set to English",
                "am": "✅ ቋንቋዎ ወደ አማርኛ ተቀይሯል",
                "om": "✅ Luuqaan keessan gara Afaan Oromootti jijjiirameera",
            }.get(lang_code, "✅ Language updated.")

            try:
                await query.answer(text=toast_msg)
            except Exception:
                pass

            db_updated = False
            if user_id:
                db = (
                        getattr(self, "db", None)
                        or (context.bot_data.get("db") if context.bot_data else None)
                        or (
                            context.application.bot_data.get("db")
                            if context.application and context.application.bot_data
                            else None
                        )
                )

                if db and hasattr(db, "update_user_language"):
                    res = db.update_user_language(user_id, lang_code)
                    if inspect.isawaitable(res):
                        res = await res
                    if res:
                        db_updated = True

            logger.info(
                f"Callback update captured: User {user_id} set lang to '{lang_code}' (DB synced: {db_updated})"
            )
            await self.show_main_menu(update, context)

        except Exception as e:
            logger.error(
                f"Failed to cleanly process inline language callback loop: {e}",
                exc_info=True,
            )

    async def auto_greet_new_member(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        prompt_text = (
            "👋 **Welcome to Smart Match Africa!**\n\n"
            "Please select your preferred language to continue:\n"
            "እባክዎ ቋንቋ ይምረጡ:\n"
            "Maaloo afaan barbaaddan filadhaa:"
        )

        if update.my_chat_member:
            event_member = update.my_chat_member
            if event_member.new_chat_member.status in ["member", "administrator"]:
                try:
                    await context.bot.send_message(
                        chat_id=event_member.chat.id,
                        text=prompt_text,
                        reply_markup=self.get_language_inline_keyboard(),
                        parse_mode="Markdown"
                    )
                except Exception as e:
                    logger.error(f"Bot attachment auto-greeting failed: {str(e)}")
            return

        if update.chat_member:
            event_member = update.chat_member
            old_status = event_member.old_chat_member.status
            new_status = event_member.new_chat_member.status

            if old_status in ["left", "kicked"] and new_status in ["member", "administrator"]:
                try:
                    await context.bot.send_message(
                        chat_id=event_member.chat.id,
                        text=prompt_text,
                        reply_markup=self.get_language_inline_keyboard(),
                        parse_mode="Markdown"
                    )
                except Exception as e:
                    logger.error(f"User join auto-greeting failed: {str(e)}")
            return

        if update.message and update.message.new_chat_members:
            for member in update.message.new_chat_members:
                if member.id == context.bot.id:
                    continue
                try:
                    await context.bot.send_message(
                        chat_id=update.message.chat_id,
                        text=prompt_text,
                        reply_markup=self.get_language_inline_keyboard(),
                        parse_mode="Markdown"
                    )
                except Exception as e:
                    logger.error(f"Service message auto-greeting failed: {str(e)}")

    async def route_menu_click(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> Any:
        if not update.message or not update.message.text:
            return

        raw_text = update.message.text.strip()
        user_id = update.effective_user.id if update.effective_user else "Unknown"

        if context.user_data is None:
            context.user_data = {}

        user_data = context.user_data
        lang = user_data.get("lang", "en")

        # 1. Clean and normalize Unicode variation selectors (\ufe0f, \ufe0e) and whitespace
        clean_text = "".join(c for c in raw_text if ord(c) not in (0xFE0F, 0xFE0E)).strip()
        norm_text_lower = clean_text.lower()

        logger.info(
            f"📥 MainMenuHandler routing click for User {user_id} ({lang}): '{raw_text}' [Clean: '{clean_text}']")

        target_routes = {
            "btn_list_house": self.housing_h.start_list_house,
            "btn_find_house": self.housing_h.start_find_house,
            "btn_freight": self.freight_ride_h.start_freight,
            "btn_ride": self.freight_ride_h.start_ride,
            "btn_list_vehicle": self.vehicle_h.start_list_vehicle,
            "btn_find_vehicle": self.vehicle_h.start_find_vehicle,
            "btn_list_equipment": self.equipment_h.start_list_equipment,
            "btn_find_equipment": self.equipment_h.start_find_equipment
        }

        # 2. Robust Multi-Tier Action Matching
        matched_action_key = None
        for key in target_routes.keys():
            current_lang_label = get_text(lang, key) or ""
            all_labels = get_keyboard_labels(key) or []

            # Clean labels for normalized comparison
            clean_labels = [
                "".join(c for c in lbl if ord(c) not in (0xFE0F, 0xFE0E)).strip()
                for lbl in ([current_lang_label] + all_labels) if lbl
            ]

            # Direct equality or case-insensitive match
            if clean_text in clean_labels or any(norm_text_lower == lbl.lower() for lbl in clean_labels):
                matched_action_key = key
                break

            # Substring fallback for customized emoji representations
            if any(lbl.lower() in norm_text_lower or norm_text_lower in lbl.lower() for lbl in clean_labels if
                   len(lbl) > 4):
                matched_action_key = key
                break

        # 3. Route Execution
        if matched_action_key:
            logger.info(f"🚦 Menu router verified action token: '{matched_action_key}' for User: {user_id} ({lang})")

            user_data["current_flow"] = matched_action_key
            stale_keys = [k for k in user_data.keys() if k.startswith("flow_step_")]
            for k in stale_keys:
                user_data.pop(k, None)

            handler_function = target_routes[matched_action_key]
            try:
                return await handler_function(update, context)
            except Exception as handler_err:
                logger.error(
                    f"❌ Error executing route handler '{matched_action_key}' for User {user_id}: {handler_err}",
                    exc_info=True)
                await update.message.reply_text("⚠️ Unable to launch selected section. Please try again or tap /start.")
                return

        # 4. Active Handoff Evaluation (Protect active conversations & forms)
        is_negotiating = bool(
            user_data.get("active_negotiation_id")
            and str(user_data.get("active_negotiation_id")).lower() != "none"
            and (user_data.get("awaiting_counter_input") or user_data.get("intent_to_counter") or user_data.get(
                "awaiting_counter_offer"))
        )

        active_state = user_data.get("current_state")
        is_filling_form = bool(
            active_state is not None or
            user_data.get("is_processing_job") is True
        )

        is_numeric_input = clean_text.replace('.', '', 1).isdigit()

        if is_negotiating or is_filling_form or is_numeric_input:
            logger.debug(f"Handoff pass-through: Input payload '{raw_text}' left untouched for active step processing.")
            return

        # 5. Unrecognized Input Fallback
        logger.warning(
            f"Unrecognized root interaction target: '{raw_text}' from User {user_id}. Presenting main menu fallback."
        )
        await self.show_main_menu(update, context)

    def get_main_menu_handlers(self) -> List[Any]:
        return [
            CommandHandler(command=["start", "menu"], callback=self.start_command),
            CommandHandler(command=["language", "setlang"], callback=self.language_command),
            CallbackQueryHandler(callback=self.handle_language_callback, pattern="^(set_lang:|lang_)"),
            ChatMemberHandler(self.auto_greet_new_member, ChatMemberHandler.MY_CHAT_MEMBER),
            ChatMemberHandler(self.auto_greet_new_member, ChatMemberHandler.CHAT_MEMBER),
            MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, callback=self.auto_greet_new_member),
            MessageHandler(filters.TEXT & ~filters.COMMAND, callback=self.route_menu_click)
        ]
