import asyncio
import hashlib
import json
import logging
import re
import time
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Type, TypeVar, Optional, Dict, Any, List, Union

from deep_translator import GoogleTranslator
from google import genai
from google.genai import types
from pydantic import BaseModel, Field, ValidationError

from src.smartmatch.config import settings

# Setup production-grade structured logging
logger = logging.getLogger(__name__)

# Concurrency throttling to prevent Gemini API connection saturation
ai_semaphore = asyncio.Semaphore(10)

T = TypeVar("T", bound=BaseModel)


class AsyncRateLimiter:
    """
    Sliding-window rate limiter preventing outgoing requests from exceeding
    Gemini API RPM (Requests Per Minute) quotas.
    """

    def __init__(self, max_calls: int = 10, period: float = 60.0):
        self.max_calls = max_calls
        self.period = period
        self.timestamps: List[float] = []
        self._lock = asyncio.Lock()

    async def acquire(self):
        async with self._lock:
            now = time.monotonic()
            # Clean up timestamps outside current sliding window
            self.timestamps = [t for t in self.timestamps if now - t < self.period]

            if len(self.timestamps) >= self.max_calls:
                sleep_time = self.period - (now - self.timestamps[0]) + 0.5
                logger.warning(
                    f"[RateLimiter] Local rate limit hit ({self.max_calls} req/{self.period}s). "
                    f"Throttling execution for {sleep_time:.2f}s..."
                )
                await asyncio.sleep(sleep_time)
                now = time.monotonic()
                self.timestamps = [t for t in self.timestamps if now - t < self.period]

            self.timestamps.append(now)


# Global rate limiter instance (configured to 10 requests / 60s for Free Tier stability)
rate_limiter = AsyncRateLimiter(max_calls=10, period=60.0)


def sanitize_schema_for_gemini(schema: Dict[str, Any]) -> Dict[str, Any]:
    """
    Recursively strips unsupported OpenAPI schema keys such as 'additionalProperties' and '$defs'
    from Pydantic schemas to ensure 100% compatibility with Gemini Developer API mode.
    """
    if not isinstance(schema, dict):
        return schema

    cleaned = {}
    for key, value in schema.items():
        if key in ("additionalProperties", "$defs"):
            continue
        if isinstance(value, dict):
            cleaned[key] = sanitize_schema_for_gemini(value)
        elif isinstance(value, list):
            cleaned[key] = [
                sanitize_schema_for_gemini(item) if isinstance(item, dict) else item
                for item in value
            ]
        else:
            cleaned[key] = value
    return cleaned


# ==========================================
# PYDANTIC STRUCTURED SCHEMAS FOR TRANSLATION
# ==========================================
class NormalizedAsset(BaseModel):
    category: str = Field(
        description="The primary asset category: Housing, Vehicle, Equipment, Freight, or Ride"
    )
    title: str = Field(
        description="A clean, concise title describing the asset, strictly in English"
    )
    description: str = Field(
        description="A detailed description of the asset, strictly in English"
    )
    price: float = Field(
        default=0.0,
        description="Extracted numerical price value"
    )
    bulk_quantity: int = Field(
        default=1,
        description="Total bulk quantity or number of available units, defaulting to 1"
    )
    currency: str = Field(
        default="ETB",
        description="Currency code: ETB or USD"
    )
    location: str = Field(
        description="Location name normalized to English spelling (e.g., 'Bole Medhanialem')"
    )
    specifications: Dict[str, Any] = Field(
        default_factory=dict,
        description="Key-value pairs of technical specs (e.g., bedrooms, model, capacity) strictly in English"
    )


class TranslatedAsset(BaseModel):
    title: str = Field(
        description="The translated asset title in the target language"
    )
    description: str = Field(
        description="The translated detailed description in the target language"
    )
    location: str = Field(
        description="The translated location name in the target language"
    )
    specifications: Dict[str, Any] = Field(
        default_factory=dict,
        description="Key-value specifications translated accurately into the target language"
    )


class SmartMatchAIEngine:
    """
    Production-grade AI Integration, Bi-Directional Translation Middleware,
    and Spatial Query Orchestrator hardened against 429 RESOURCE_EXHAUSTED errors
    using Python Deep Translator as a resilient fallback layer.
    """

    def __init__(self, db_client: Optional[Any] = None):
        """
        Initializes the Smart Match AI engine.
        :param db_client: Connected Supabase AsyncClient instance.
        """
        self.client = genai.Client(api_key=settings.GEMINI_API_KEY)

        # Pull model choices dynamically from settings with standard fallbacks
        # Updated to gemini-3.5-flash-lite (Free tier available, ultra-high efficiency)
        self.model_name = getattr(settings, "GEMINI_MODEL_FLASH", "gemini-3.5-flash-lite")

        # Fallback model pointing to stable high-capability tier
        self.fallback_model = getattr(settings, "GEMINI_MODEL_PRO", "gemini-2.5-pro")

        self.db_client = db_client

        # Strict execution timeout for fast fallback handling (default: 3 seconds)
        self.ai_timeout = getattr(settings, "AI_TRANSLATION_TIMEOUT", 3.0)

        # Free-Tier Quota Optimization: Bounded LRU cache with concurrency protection
        self._cache_max_entries = 1000
        self._translation_cache: OrderedDict[str, Dict[str, Any]] = OrderedDict()
        self._cache_lock = asyncio.Lock()

    async def get_cached_translation(self, cache_key: str) -> Optional[Dict[str, Any]]:
        """
        Thread-safe retrieval from LRU cache. Moves hit keys to the end to maintain LRU order.
        """
        async with self._cache_lock:
            if cache_key in self._translation_cache:
                self._translation_cache.move_to_end(cache_key)
                return self._translation_cache[cache_key]
        return None

    async def set_cached_translation(self, cache_key: str, data: Dict[str, Any]) -> None:
        """
        Thread-safe entry insertion into LRU cache with automatic eviction when capacity is exceeded.
        """
        async with self._cache_lock:
            if cache_key in self._translation_cache:
                self._translation_cache.move_to_end(cache_key)
            self._translation_cache[cache_key] = data

            if len(self._translation_cache) > self._cache_max_entries:
                self._translation_cache.popitem(last=False)

    @staticmethod
    def _normalize_language_code(lang: str) -> str:
        """
        Maps language identifiers to standard ISO-639-1 codes used by Deep Translator.
        """
        code = str(lang or "").strip().lower()
        if code in ["am", "amharic", "አማርኛ"]:
            return "am"
        if code in ["om", "oromo", "afaan oromo", "afaan_oromo", "or"]:
            return "om"
        return "en"

    async def _deep_translate_text(
            self,
            text: str,
            target_lang: str = "en",
            source_lang: str = "auto"
    ) -> str:
        """
        Asynchronous non-blocking wrapper around Python Deep Translator (GoogleTranslator)
        with integrated LRU caching and exponential backoff retry handling for rate limits.
        """
        if not text or not str(text).strip():
            return text

        target_code = self._normalize_language_code(target_lang)
        cleaned_text = str(text).strip()

        # 1. 🛡️ Check LRU cache first to prevent redundant API calls
        cache_key = f"{source_lang}:{target_code}:{cleaned_text}"
        cached_data = await self.get_cached_translation(cache_key)
        if cached_data and isinstance(cached_data, dict) and "translated" in cached_data:
            return cached_data["translated"]

        # 2. 🔄 Retry loop with exponential backoff for rate limits (429 / Too Many Requests)
        max_retries = 3
        for attempt in range(1, max_retries + 1):
            try:
                translated = await asyncio.to_thread(
                    GoogleTranslator(source=source_lang, target=target_code).translate,
                    text
                )
                result = translated or text

                # 3. 💾 Save successful translation to LRU cache
                await self.set_cached_translation(cache_key, {"translated": result})
                return result

            except Exception as e:
                err_str = str(e).lower()
                is_rate_limited = "too many requests" in err_str or "429" in err_str

                if is_rate_limited and attempt < max_retries:
                    # Exponential backoff: 2s, 4s, 8s...
                    sleep_time = (2 ** attempt) * 1.0
                    logger.warning(
                        f"[DeepTranslator Rate Limit] Attempt {attempt}/{max_retries} rate limited. "
                        f"Backing off for {sleep_time:.2f}s before retry..."
                    )
                    await asyncio.sleep(sleep_time)
                else:
                    logger.warning(f"[DeepTranslator Fallback Failed] Error translating to '{target_code}': {e}")
                    break

        return text

    async def _safe_generate_content(
            self,
            contents: Any,
            config: Optional[types.GenerateContentConfig] = None,
            model_override: Optional[str] = None
    ) -> types.GenerateContentResponse:
        """
        Central execution wrapper for all Gemini API calls.
        Enforces local sliding-window rate limiting and dynamically parses 429 retry delays
        to guarantee requests never fail or crash under quota pressure.
        """
        model = model_override or self.model_name
        max_retries = 5

        for attempt in range(1, max_retries + 1):
            # 1. Enforce local sliding-window RPM limit
            await rate_limiter.acquire()

            # 2. Enforce concurrency semaphore
            async with ai_semaphore:
                try:
                    response = await asyncio.to_thread(
                        self.client.models.generate_content,
                        model=model,
                        contents=contents,
                        config=config
                    )
                    return response

                except Exception as e:
                    err_str = str(e)
                    is_429 = "429" in err_str or "RESOURCE_EXHAUSTED" in err_str

                    if is_429:
                        # Extract dynamic retry delay requested by Gemini API
                        match = re.search(r"retry in (\d+(?:\.\d+)?)s", err_str, re.IGNORECASE)
                        if not match:
                            match = re.search(r"retryDelay': '(\d+)s'", err_str)

                        if match:
                            sleep_time = float(match.group(1)) + 1.5
                        else:
                            sleep_time = (2 ** attempt) * 10  # Backoff: 20s, 40s, 80s...

                        logger.warning(
                            f"[Gemini 429 Quota Ceiling] Attempt {attempt}/{max_retries} rate limited. "
                            f"Sleeping for {sleep_time:.2f}s before retry..."
                        )
                        await asyncio.sleep(sleep_time)

                    else:
                        logger.error(f"[Gemini API Error] Exception on attempt {attempt}: {err_str}")
                        if attempt == max_retries:
                            raise e
                        await asyncio.sleep(2)

        raise RuntimeError("Max retries exceeded for Gemini API content generation.")

    # ==========================================
    # BI-DIRECTIONAL TRANSLATION MIDDLEWARE
    # ==========================================
    async def parse_listing_to_english(
            self,
            listing_data: Union[str, Dict[str, Any]],
            vertical_hint: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        INBOUND PIPELINE: Intercepts raw user input, normalizes it via Gemini,
        and instantly falls back to Deep Translator targeted parsing when rate-limited or timed out.
        """
        # 1. Ensure we have a string representation for the Gemini prompt
        if isinstance(listing_data, dict):
            raw_text_for_prompt = json.dumps(listing_data, ensure_ascii=False, indent=2)
        else:
            raw_text_for_prompt = str(listing_data)

        prompt = f"""
        You are an expert translation and data extraction engine for the Ethiopian marketplace @SmartMatchAfricaBot.
        Analyze the following raw user asset listing. It may be written in English, Amharic (Ge'ez or Latin script), 
        or Afaan Oromo.

        Your Instructions:
        1. Identify the core asset category (Housing, Vehicle, Equipment, Freight, or Ride).
        2. Translate ALL descriptive text parameters (title, description, specifications, location) completely into 
        English.
        3. Extract price, bulk_quantity (total units/items, defaulting to 1), currency (ETB/USD), and numerical specs 
        accurately.
        4. Normalize landmark names to standard English spellings (e.g., "ቦሌ" -> "Bole", "ፒያሳ" -> "Piassa").

        Vertical Context Hint: {vertical_hint or 'Auto-Detect'}
        User Raw Input:
        \"\"\"{raw_text_for_prompt}\"\"\"
        """
        schema_dict = sanitize_schema_for_gemini(NormalizedAsset.model_json_schema())
        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=schema_dict,
            temperature=0.1,
        )

        try:
            # Attempt AI generation with sliding-window rate limit and 5s timeout protection
            await rate_limiter.acquire()
            async with ai_semaphore:
                response = await asyncio.wait_for(
                    asyncio.to_thread(
                        self.client.models.generate_content,
                        model=self.model_name,
                        contents=prompt,
                        config=config
                    ),
                    timeout=5
                )

                # Handle potential stringified JSON responses from Gemini
                response_text = response.text
                if isinstance(response_text, str) and response_text.strip().startswith('```'):
                    response_text = response_text.strip('```json').strip('```').strip()

                parsed_data = NormalizedAsset.model_validate_json(response_text)
                return parsed_data.model_dump()

        except asyncio.TimeoutError:
            logger.info(
                "[Gemini Timeout] Inbound parsing exceeded 5s threshold. Routing to Deep Translator fallback.")
        except Exception as e:
            err_str = str(e)
            if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                logger.warning(
                    "[Gemini Quota Exceeded] Routing input through Deep Translator targeted dictionary fallback."
                )
            else:
                logger.error(f"Inbound AI parse error: {err_str}. Falling back to Deep Translator dictionary parser.")

        # ==========================================
        # TARGETED DICTIONARY FALLBACK (Preserves original dictionary structure & keys)
        # ==========================================
        if isinstance(listing_data, dict):
            translated_dict = listing_data.copy()
            text_fields = ['property_use', 'location_text', 'amenities', 'brand', 'features', 'equipment_type',
                           'title', 'description']

            for key, value in translated_dict.items():
                if key in text_fields and isinstance(value, str) and value.strip():
                    try:
                        translated_val = await self._deep_translate_text(
                            text=value,
                            target_lang="en",
                            source_lang="auto"
                        )
                        translated_dict[key] = translated_val
                    except Exception as dt_err:
                        logger.warning(f"DeepTranslator failed on field '{key}': {dt_err}")

            return translated_dict

        # ==========================================
        # RAW STRING FALLBACK
        # ==========================================
        else:
            try:
                safe_text = str(listing_data)[:4500]
                english_text = await self._deep_translate_text(
                    text=safe_text,
                    target_lang="en",
                    source_lang="auto"
                )
                heuristic_result = self._heuristic_listing_parser(english_text, vertical_hint)

                # Merge back any known vertical hint if parser missed it
                if vertical_hint and not heuristic_result.get('vertical_type'):
                    heuristic_result['vertical_type'] = vertical_hint

                return heuristic_result
            except Exception as text_dt_err:
                logger.error(f"Text translation fallback failed: {text_dt_err}")
                return {"vertical_type": vertical_hint or "general", "status": "active"}

    @staticmethod
    def _heuristic_listing_parser(raw_user_text: str, vertical_hint: Optional[str] = None) -> Dict[str, Any]:
        """
        Deterministic regex backup parser fully aligned with the Supabase 'provider_listings' table schema.
        """
        safe_text = str(raw_user_text or "").strip()

        # Extract Bedrooms
        bed_match = re.search(r'(\d+)\s*(?:bed|bedroom|bedrooms)', safe_text, re.IGNORECASE)
        bedrooms = int(bed_match.group(1)) if bed_match else 0

        # Extract Sq Meters
        sq_match = re.search(r'(\d+)\s*(?:sqm|sq\.m|square\s*meters|m2)', safe_text, re.IGNORECASE)
        sq_meters = int(sq_match.group(1)) if sq_match else 0

        # Extract Price (ignoring year numbers like 2012, 2024)
        price_val = 0.0
        price_match = re.search(r'(?:price|cost|birr|etb|\$|\bUSD\b)[:\s]*([\d,]+(?:\.\d+)?)', safe_text, re.IGNORECASE)
        if price_match:
            try:
                price_val = float(price_match.group(1).replace(',', ''))
            except ValueError:
                price_val = 0.0
        else:
            nums = re.findall(r'\b(\d{3,}(?:,\d{3})*(?:\.\d+)?)\b', safe_text)
            for n in nums:
                val = float(n.replace(',', ''))
                if val < 1900 or val > 2030:
                    price_val = val
                    break

        currency = "USD" if "$" in safe_text or "USD" in safe_text.upper() else "ETB"

        # Bulk Quantity
        qty_match = re.search(r'(\d+)\s*(?:pcs|items|units|cars|houses|qty)', safe_text, re.IGNORECASE)
        bulk_qty = int(qty_match.group(1)) if qty_match else 1

        # Transaction Type & Property Use
        trans_type = "rent"
        if "sell" in safe_text.lower() or "sale" in safe_text.lower():
            trans_type = "sell"
        elif vertical_hint in ["equipment", "freight", "ride"]:
            trans_type = "rental" if vertical_hint == "equipment" else "service"

        prop_use = "residential" if vertical_hint == "housing" else (vertical_hint or "general")
        if "commercial" in safe_text.lower():
            prop_use = "commercial"
        elif vertical_hint == "vehicle":
            prop_use = "sedan"
        elif vertical_hint == "equipment":
            prop_use = "Excavator"

        return {
            "vertical_type": vertical_hint or "housing",
            "transaction_type": trans_type,
            "property_use": prop_use,
            "location_text": "Addis Ababa",
            "price": price_val,
            "currency": currency,
            "bedrooms": bedrooms,
            "sq_meters": sq_meters,
            "amenities": safe_text[:255],
            "bulk_quantity": bulk_qty,
            "status": "active"
        }

    async def translate_asset_to_target_language(
            self,
            english_asset_data: Dict[str, Any],
            target_language: str
    ) -> Dict[str, Any]:
        """
        OUTBOUND PIPELINE: Takes English structured data from Supabase and translates
        display fields into the user's target language (English, Amharic, or Oromo).
        Uses Gemini JSON schema mode with 5s timeout protection, falling back to Deep Translator.
        """
        lang_clean = self._normalize_language_code(target_language)

        # 1. Quota Bypass: If user language is English, skip translation calls completely
        if lang_clean == "en":
            return english_asset_data

        # Normalize language name for Gemini prompt
        target_lang_name = "Amharic" if lang_clean == "am" else "Afaan Oromo"

        # 2. Cache Lookup
        record_id = str(english_asset_data.get("id") or english_asset_data.get("title", ""))
        cache_key = hashlib.md5(f"{record_id}_{lang_clean}".encode("utf-8")).hexdigest()

        if cache_key in self._translation_cache:
            logger.debug(f"Translation cache hit for key: {cache_key}")
            cached_translation = self._translation_cache[cache_key]
            out_payload = english_asset_data.copy()
            out_payload.update(cached_translation)
            return out_payload

        # 3. Prepare payload to translate
        translatable_fields = {
            "title": english_asset_data.get("title", ""),
            "description": english_asset_data.get("description", ""),
            "location": english_asset_data.get("location_text") or english_asset_data.get("location", ""),
            "specifications": english_asset_data.get("specifications") or english_asset_data.get("amenities") or {}
        }

        prompt = f"""
        You are an expert translator specializing in Ethiopian marketplace localization (English, Amharic, Afaan Oromo).
        Translate the following English asset record into {target_lang_name}.

        Rules:
        - Translate titles, descriptions, and specification labels into natural, culturally accurate {target_lang_name}.
        - DO NOT translate or alter numbers, dates, monetary amounts, plate numbers, or IDs.
        - Keep technical automotive/property terms clear and understandable.

        English Source JSON:
        {json.dumps(translatable_fields, ensure_ascii=False)}
        """

        schema_dict = sanitize_schema_for_gemini(TranslatedAsset.model_json_schema())
        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=schema_dict,
            temperature=0.2,
        )

        try:
            logger.debug(f"Translating record '{record_id}' to {target_lang_name} via {self.model_name}")

            # 1. Enforce local sliding-window RPM limit
            await rate_limiter.acquire()

            # 2. Fast single attempt with 5s timeout protection
            async with ai_semaphore:
                response = await asyncio.wait_for(
                    asyncio.to_thread(
                        self.client.models.generate_content,
                        model=self.model_name,
                        contents=prompt,
                        config=config
                    ),
                    timeout=5
                )

            translated_obj = TranslatedAsset.model_validate_json(response.text).model_dump()

            # Store in Cache
            if len(self._translation_cache) >= self._cache_max_entries:
                self._translation_cache.pop(next(iter(self._translation_cache)))
            self._translation_cache[cache_key] = translated_obj

            final_output = english_asset_data.copy()
            final_output.update(translated_obj)
            return final_output

        except asyncio.TimeoutError:
            logger.info(
                f"[Gemini Timeout] Outbound translation for record '{record_id}' exceeded 5s. Routing to Deep "
                f"Translator fallback.")
        except Exception as e:
            err_str = str(e)
            if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                logger.warning(
                    f"[Gemini 429 Quota Ceiling] Fast routing record '{record_id}' ({lang_clean}) to Deep Translator "
                    f"fallback."
                )
            else:
                logger.error(
                    f"[Outbound Gemini Translation Failed: {e}] Switching to Deep Translator fallback for lang '"
                    f"{lang_clean}'."
                )

        # Deep Translator Secondary Fallback Pipeline (~200ms execution time)
        translated_obj = await self._fallback_translate_dict(translatable_fields, target_lang=lang_clean)

        # Cache Deep Translator Result
        if len(self._translation_cache) >= self._cache_max_entries:
            self._translation_cache.pop(next(iter(self._translation_cache)))
        self._translation_cache[cache_key] = translated_obj

        final_output = english_asset_data.copy()
        final_output.update(translated_obj)
        return final_output

    async def _fallback_translate_dict(
            self,
            translatable_fields: Dict[str, Any],
            target_lang: str
    ) -> Dict[str, Any]:
        """
        Translates text fields and nested dictionaries concurrently using Deep Translator
        to eliminate sequential HTTP latency.
        """

        async def trans(text: str):
            if not text or not str(text).strip():
                return text
            return await self._deep_translate_text(text, target_lang=target_lang)

        # 1. Prepare base field coroutines
        coros = [
            trans(translatable_fields.get("title", "")),
            trans(translatable_fields.get("description", "")),
            trans(translatable_fields.get("location", ""))
        ]

        # 2. Prepare specification coroutines
        raw_specs = translatable_fields.get("specifications", {})
        spec_keys = []
        spec_coros = []

        if isinstance(raw_specs, dict):
            for k, v in raw_specs.items():
                if isinstance(v, str):
                    spec_keys.append(k)
                    spec_coros.append(trans(v))

        # 3. Execute all HTTP translation requests simultaneously
        all_results = await asyncio.gather(*coros, *spec_coros)

        # 4. Reconstruct the dictionary
        translated_title = all_results[0]
        translated_desc = all_results[1]
        translated_loc = all_results[2]

        translated_specs = {}
        if isinstance(raw_specs, dict):
            # Copy over non-string specs (like integers/booleans) untouched
            for k, v in raw_specs.items():
                if not isinstance(v, str):
                    translated_specs[k] = v

            # Map the translated string results back to their keys
            spec_results = all_results[3:]
            for k, res in zip(spec_keys, spec_results):
                translated_specs[k] = res

        elif isinstance(raw_specs, str):
            translated_specs = await trans(raw_specs)

        return {
            "title": translated_title,
            "description": translated_desc,
            "location": translated_loc,
            "specifications": translated_specs
        }

    # ==========================================
    # DISTRIBUTED STATE MANAGEMENT (MULTI-POD SAFE)
    # ==========================================
    async def save_negotiation_state(self, chat_id: int, state_data: Dict[str, Any]) -> None:
        """Saves conversational negotiation state directly to Supabase."""
        if not self.db_client:
            raise RuntimeError("Database client not initialized within SmartMatchAIEngine.")

        try:
            payload = {
                "chat_id": chat_id,
                "data": state_data,
                "updated_at": datetime.now(timezone.utc).isoformat()
            }
            await self.db_client.table("negotiation_states").upsert(payload, on_conflict="chat_id").execute()
            logger.debug(f"Negotiation state successfully stored for Chat ID: {chat_id}")
        except Exception as e:
            logger.error(f"Failed to save negotiation state via Supabase for Chat ID {chat_id}: {str(e)}")
            raise e

    async def get_negotiation_state(self, chat_id: int) -> Optional[Dict[str, Any]]:
        """Retrieves persistent negotiation session state from the shared datastore."""
        if not self.db_client:
            raise RuntimeError("Database client not initialized within SmartMatchAIEngine.")

        try:
            response = await self.db_client.table("negotiation_states").select("data").eq("chat_id", chat_id).execute()
            if response.data:
                record_data = response.data[0].get("data")
                if isinstance(record_data, str):
                    return json.loads(record_data)
                return record_data
            return None
        except Exception as e:
            logger.error(f"Failed to retrieve negotiation state via Supabase for Chat ID {chat_id}: {str(e)}")
            return None

    async def update_negotiation(self, chat_id: int, price: float, currency: str, status: str) -> None:
        """Updates specific parameters of an active negotiation session."""
        current_state = await self.get_negotiation_state(chat_id) or {}
        current_state.update({
            "agreed_price": price,
            "currency": currency,
            "status": status
        })
        await self.save_negotiation_state(chat_id, current_state)
        logger.info(f"Negotiation updated for {chat_id}: status='{status}' ({price} {currency})")

    async def get_agreed_price(self, chat_id: int) -> Dict[str, Any]:
        """Retrieves finalized negotiation parameters for a given chat session."""
        state = await self.get_negotiation_state(chat_id)
        if not state:
            return {"price": 0.0, "currency": "ETB"}
        return {
            "price": float(state.get("agreed_price", 0.0)),
            "currency": state.get("currency", "ETB")
        }

    # ==========================================
    # DOMAIN-SPECIFIC GEOSPATIAL SEARCHES
    # ==========================================
    async def fetch_proximity_matches(
            self,
            lon: float,
            lat: float,
            radius_meters: int,
            asset_category: str
    ) -> List[Dict[str, Any]]:
        """Executes high-performance PostGIS proximity search via Supabase RPC."""
        if not self.db_client:
            raise RuntimeError("Database client not initialized within SmartMatchAIEngine.")

        try:
            response = await self.db_client.rpc(
                "fetch_proximity_matches",
                {
                    "lon": lon,
                    "lat": lat,
                    "radius_meters": float(radius_meters),
                    "asset_category": asset_category
                }
            ).execute()
            return response.data or []
        except Exception as e:
            logger.critical(f"Geospatial proximity routing failed inside Supabase cluster: {str(e)}", exc_info=True)
            return []

    # ==========================================
    # GENERAL STRUCTURED EXTRACTION & AUDITING
    # ==========================================
    async def extract_structured_data(self, prompt: str, schema: Type[T]) -> Optional[T]:
        """Parses unformatted input into structured Pydantic objects."""
        schema_dict = sanitize_schema_for_gemini(schema.model_json_schema())
        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=schema_dict,
            temperature=0.1,
        )

        try:
            response = await self._safe_generate_content(contents=prompt, config=config)
            return schema.model_validate_json(response.text)
        except ValidationError as ve:
            logger.error(f"Pydantic validation schema mismatch: {str(ve)}")
            return None
        except Exception as e:
            logger.error(f"AI Extraction failed with primary engine ({self.model_name}): {str(e)}")
            if self.model_name == "gemini-2.0-flash":
                try:
                    response = await self._safe_generate_content(
                        contents=prompt,
                        config=config,
                        model_override=self.fallback_model
                    )
                    return schema.model_validate_json(response.text)
                except Exception as fallback_e:
                    logger.critical(f"Fallback model {self.fallback_model} exhausted: {str(fallback_e)}")
            return None

    async def verify_document_identity(self, image_data: bytes, prompt: str) -> bool:
        """Multimodal verification of IDs/Licenses to prevent fraud."""
        system_instruction = (
            "You are a strict security auditor. Analyze the provided identity document. "
            "If the document is valid, legible, and matches the criteria in the prompt, "
            "respond with exactly 'VERIFIED'. "
            "If there are signs of tampering, it is unreadable, or doesn't match, respond with 'REJECTED: [Reason]'."
        )
        contents = [
            types.Part.from_text(text=system_instruction),
            types.Part.from_bytes(data=image_data, mime_type='image/jpeg'),
            types.Part.from_text(text=prompt)
        ]
        config = types.GenerateContentConfig(temperature=0.0)

        try:
            response = await self._safe_generate_content(contents=contents, config=config)
            result_text = response.text.strip().upper()
            return "VERIFIED" in result_text and "REJECTED" not in result_text
        except Exception as e:
            logger.error(f"Identity verification layer error: {str(e)}")
            return False

    async def detect_language_and_intent(self, text: str) -> dict:
        """Multilingual routing: Detects English, Amharic, or Afaan Oromo and user intent."""
        prompt = f"""
        Analyze the following user input text. Identify the primary language and core functional intent.
        Return ONLY valid JSON.

        Text: "{text}"
        Format Specification: 
        {{
            "language": "en" | "am" | "or" | "unknown", 
            "intent": "freight_request" | "ride_request" | "asset_registration" | "housing_search" | "vehicle_search" 
            | "equipment_search" | "unknown"
        }}
        """
        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.1
        )

        try:
            response = await self._safe_generate_content(contents=prompt, config=config)
            return json.loads(response.text)
        except Exception as e:
            logger.error(f"Language and intent detection failed: {str(e)}")
            return {"language": "en", "intent": "unknown"}

    async def generate_matching_summary(self, matches: List[Dict[str, Any]]) -> str:
        """Generates a human-friendly conversational summary for matching results."""
        prompt = (
            f"Summarize these {len(matches)} matching options for a customer in a helpful, "
            "concise tone suitable for a quick Telegram message. "
            "Highlight the best options based on price and proximity. "
            "CRITICAL SECURITY RULE: NEVER include phone numbers, contact names, or exact contact details. "
            f"Raw Database Results: {json.dumps(matches, default=str)}"
        )
        config = types.GenerateContentConfig(temperature=0.7)

        try:
            response = await self._safe_generate_content(contents=prompt, config=config)
            return response.text.strip()
        except Exception as e:
            logger.error(f"Conversational summary synthesis failed: {str(e)}")
            return "✅ I found several matching options for your search! Please review the details below."
