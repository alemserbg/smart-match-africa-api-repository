import logging
from typing import Any, Optional
from telegram import Update
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CommandHandler,
    MessageHandler,
    filters
)
from telegram.error import TelegramError

from src.smartmatch.services.ai_engine import SmartMatchAIEngine
from src.smartmatch.services.db_manager import DatabaseManager
from src.smartmatch.utils.queue_manager import QueueManager
from src.smartmatch.utils.translations import get_text

# Production structured logging
logger = logging.getLogger("smartmatch.verification")


class DriverVerificationHandler:
    """
    Production-grade Driver Verification handler for Smart Match Africa.

    Offloads heavy multimodal AI payload analysis into background processing queues
    to ensure zero blocking on the main Telegram update loop. Fully localized
    for Amharic, Afaan Oromo, and English with bulletproof fallbacks and error handling.
    """

    # State flags for Conversation Routing
    AWAITING_LICENSE_PHOTO = 1

    def __init__(self,
                 db_manager: DatabaseManager,
                 ai_engine: SmartMatchAIEngine,
                 queue_manager: QueueManager,
                 telegram_app: Any  # Injected python-telegram-bot Application instance
                 ):
        """
        Initializes the verification orchestrator with injected platform layers.
        """
        self.db = db_manager
        self.ai = ai_engine
        self.queue = queue_manager
        self.telegram_app = telegram_app

    async def _get_user_lang(self, user_id: int, context: Optional[ContextTypes.DEFAULT_TYPE] = None) -> str:
        """
        Resolves the preferred language for a given user.
        Checks active session context first to minimize DB hits, with robust fallback.
        """
        if context and context.user_data and "language" in context.user_data:
            lang = context.user_data.get("language")
            if lang:
                return str(lang).lower().strip().split("_")[0]

        try:
            if hasattr(self.db, "get_user_language"):
                lang = await self.db.get_user_language(user_id)
                if lang:
                    return str(lang).lower().strip().split("_")[0]
        except Exception as db_err:
            logger.warning(f"Error fetching user language from DB for user {user_id}: {db_err}. Defaulting to 'en'.")

        return "en"

    @staticmethod
    def _get_safe_text(lang: str, key: str, fallback: str) -> str:
        """
        Safely retrieves translated text with guaranteed fallback to prevent empty outputs.
        """
        try:
            text = get_text(lang, key)
            if text and isinstance(text, str) and text.strip() and text != key:
                return text
        except Exception as e:
            logger.debug(f"Translation lookup error for key '{key}' in lang '{lang}': {e}")
        return fallback

    async def start_verification(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """
        Entry Point: Initiates the step-by-step verification pipeline in the user's language.
        """
        user_id = update.effective_user.id
        lang = await self._get_user_lang(user_id, context)

        default_start_msg = (
            "🪪 *Driver & Identity Verification*\n\n"
            "Please upload a clear photo of your valid driver's license or official ID card to verify your account."
        )
        msg_text = self._get_safe_text(lang, "verify_start_message", default_start_msg)

        try:
            await update.message.reply_text(
                msg_text,
                parse_mode="Markdown"
            )
        except TelegramError as tg_err:
            logger.warning(f"Markdown parsing failed for start_verification: {tg_err}. Retrying without parse_mode...")
            try:
                await update.message.reply_text(msg_text, parse_mode=None)
            except Exception as inner_err:
                logger.error(f"Failed to send start_verification message to user {user_id}: {inner_err}")

        return self.AWAITING_LICENSE_PHOTO

    async def process_license_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """
        Edge Ingress: Intercepts the photo upload, downloads the binary payload,
        and queues it instantly to keep the chat interface non-blocking.
        """
        user = update.effective_user
        chat_id = update.effective_chat.id
        lang = await self._get_user_lang(user.id, context)

        # Catch textual inputs when an image attachment is strictly required
        if not update.message.photo:
            fallback_err = "⚠️ Please upload an image of your driver's license or ID card to proceed with verification."
            err_msg = self._get_safe_text(lang, "error_invalid_input_photo_required", fallback_err)
            try:
                await update.message.reply_text(err_msg, parse_mode="Markdown")
            except TelegramError:
                await update.message.reply_text(err_msg, parse_mode=None)
            return self.AWAITING_LICENSE_PHOTO

        # Notify user instantly before kicking off heavy tasks
        default_proc_msg = "⏳ Processing your verification document. Please hold on..."
        proc_text = self._get_safe_text(lang, "verify_processing_document", default_proc_msg)

        try:
            processing_msg = await update.message.reply_text(proc_text, parse_mode="Markdown")
        except TelegramError:
            processing_msg = await update.message.reply_text(proc_text, parse_mode=None)

        try:
            # 1. Fetch highest resolution photo variation straight to an in-memory bytearray
            photo_file = await update.message.photo[-1].get_file()
            image_bytes = await photo_file.download_as_bytearray()

            # 2. Enqueue the task to background threads via the QueueManager
            await self.queue.enqueue(
                self._process_verification_async,
                TaskPriority=1,  # Balanced priority level for verification sequences
                chat_id=chat_id,
                user_id=user.id,
                image_payload=bytes(image_bytes),
                msg_id=processing_msg.message_id,
                retry_count=0
            )

            logger.info(f"Driver identity payload from user {user.id} safely offloaded to the async queue.")

            # Close out conversational state immediately so user can issue other bot commands
            return ConversationHandler.END

        except Exception as e:
            logger.error(f"Failed to enqueue or stream verification media for user {user.id}: {str(e)}")
            fallback_fail = "❌ Failed to process upload. Please try again later with /verify."
            fail_text = self._get_safe_text(lang, "verify_upload_failed", fallback_fail)
            try:
                await processing_msg.edit_text(fail_text, parse_mode="Markdown")
            except Exception:
                try:
                    await processing_msg.edit_text(fail_text, parse_mode=None)
                except Exception:
                    pass
            return ConversationHandler.END

    # ==========================================
    # ASYNCHRONOUS BACKGROUND VISION WORKER
    # ==========================================
    async def _process_verification_async(
            self,
            chat_id: int,
            user_id: int,
            image_payload: bytes,
            msg_id: int,
            retry_count: int = 0
    ) -> None:
        """
        Internal async worker executing off the main HTTP/polling thread.
        Uses Gemini Vision via the SmartMatchAIEngine to parse and verify identity assets.
        Updates the UI dynamically in the user's preferred language.
        """
        # Resolve language inside the background thread
        lang = await self._get_user_lang(user_id)

        # Security verification engineering prompt (kept in English for the AI engine)
        verification_prompt = (
            "Verify this is a valid official driver's license or government identity card. "
            "Ensure that text is legible, it represents a genuine identity document, "
            "and there are no obvious digital manipulations, overlays, or spoofing attempts."
        )

        try:
            # 1. Execute Multimodal AI Verification via the injected service layer
            is_verified = await self.ai.verify_document_identity(image_payload, verification_prompt)

            if is_verified:
                # Persist verified state safely with an immutable audit log
                if hasattr(self.db, "log_audit_action"):
                    await self.db.log_audit_action(
                        action="DRIVER_VERIFIED_AI",
                        endpoint="users.update_verified_state",
                        parameters={"telegram_id": str(user_id), "is_verified": True}
                    )

                success_fallback = "✅ *Verification Successful!*\nYour driver identity has been verified successfully."
                success_text = self._get_safe_text(lang, "verify_success_message", success_fallback)

                # 3. Update the Telegram UI directly from the background worker thread
                try:
                    await self.telegram_app.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=msg_id,
                        text=success_text,
                        parse_mode="Markdown"
                    )
                except TelegramError:
                    await self.telegram_app.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=msg_id,
                        text=success_text,
                        parse_mode=None
                    )
                logger.info(f"User {user_id} successfully passed automated AI verification.")
            else:
                fail_val_fallback = "⚠️ *Verification Failed*\nThe uploaded document could not be verified or is unclear. Please try /verify again with a clearer image."
                fail_val_text = self._get_safe_text(lang, "verify_failed_validation", fail_val_fallback)

                # 4. Handle soft validation failure (e.g., blurry image, invalid document)
                try:
                    await self.telegram_app.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=msg_id,
                        text=fail_val_text,
                        parse_mode="Markdown"
                    )
                except TelegramError:
                    await self.telegram_app.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=msg_id,
                        text=fail_val_text,
                        parse_mode=None
                    )
                logger.warning(f"User {user_id} failed identity verification validation checks.")

        except Exception as e:
            logger.error(f"Error encountered during background vision execution for user {user_id}: {str(e)}")

            # --- FAULT TOLERANCE: 3-TIER RETRY QUEUE POLICY ---
            max_retries = 3
            if retry_count < max_retries:
                logger.warning(
                    f"Re-queueing verification task for user {user_id}. Attempt {retry_count + 1} of {max_retries}...")
                await self.queue.enqueue(
                    self._process_verification_async,
                    TaskPriority=2,  # Lower priority on retries to ensure normal traffic passes
                    chat_id=chat_id,
                    user_id=user_id,
                    image_payload=image_payload,
                    msg_id=msg_id,
                    retry_count=retry_count + 1
                )
            else:
                # Max retries exhausted, safely update UI to notify user of an infrastructure timeout
                logger.critical(f"FATAL: Max retries ({max_retries}) exhausted for user {user_id} driver verification.")
                timeout_fallback = "❌ System timeout during verification. Please try again later."
                timeout_text = self._get_safe_text(lang, "error_system_timeout", timeout_fallback)
                try:
                    try:
                        await self.telegram_app.bot.edit_message_text(
                            chat_id=chat_id,
                            message_id=msg_id,
                            text=timeout_text,
                            parse_mode="Markdown"
                        )
                    except TelegramError:
                        await self.telegram_app.bot.edit_message_text(
                            chat_id=chat_id,
                            message_id=msg_id,
                            text=timeout_text,
                            parse_mode=None
                        )
                except Exception as t_err:
                    logger.error(f"Failed to deliver final timeout message to chat {chat_id}: {str(t_err)}")

    # ==========================================
    # CONVERSATIONAL STATE CONTROL
    # ==========================================
    async def cancel_verification(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """
        Allows the driver to gracefully break out of the step-by-step pipeline.
        """
        user_id = update.effective_user.id
        lang = await self._get_user_lang(user_id, context)

        cancel_fallback = "❌ Verification process cancelled."
        cancel_text = self._get_safe_text(lang, "verify_cancelled_message", cancel_fallback)

        try:
            await update.message.reply_text(cancel_text, parse_mode="Markdown")
        except TelegramError:
            await update.message.reply_text(cancel_text, parse_mode=None)

        return ConversationHandler.END

    def get_verification_handler(self) -> ConversationHandler:
        """
        Assembles and exports the configuration parameters for registration in main.py.
        """
        return ConversationHandler(
            entry_points=[CommandHandler('verify', self.start_verification)],
            states={
                self.AWAITING_LICENSE_PHOTO: [
                    MessageHandler(filters.PHOTO | filters.TEXT, self.process_license_photo)
                ],
            },
            fallbacks=[CommandHandler('cancel', self.cancel_verification)],
            per_chat=True,
            per_user=True
        )
