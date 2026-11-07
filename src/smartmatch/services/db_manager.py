import logging
import json
import uuid
import math
import asyncio
from datetime import datetime, timezone
import inspect
from typing import Optional, Any, Dict, Tuple, List, Callable
import socket
import random
import re
import redis.asyncio as aioredis
from supabase import acreate_client, AsyncClient, AsyncClientOptions
from redis.exceptions import ConnectionError as RedisConnectionError, TimeoutError as RedisTimeoutError

from src.smartmatch.config import settings

# Global logger for database activity
logger = logging.getLogger(__name__)


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculates the distance in meters between two GPS coordinates."""
    R = 6371000  # Radius of earth in meters
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


class DatabaseManager:
    """
    Production-grade database service layer for Smart Match Africa.
    Handles user lifecycle, progressive state tracking, draft wizard captures,
    geospatial lookups, unified transaction ledgering, and high-frequency
    telemetry processing via the Supabase HTTP SDK and Redis.
    """

    def __init__(self, supabase_client: Optional[AsyncClient] = None, redis_client: Optional[aioredis.Redis] = None):
        self._default_lang = "en"
        self._supported_languages = ("en", "am", "om")
        self.supabase: Optional[AsyncClient] = supabase_client
        self.redis = redis_client
        self.logger = logging.getLogger(__name__)

        # Redis auto-reconnect requirements
        self.redis_url = settings.REDIS_URL
        self._reconnect_lock = asyncio.Lock()

    async def connect(self) -> None:
        """Establishes connections to Supabase (HTTPS) and Redis (TCP)."""
        if self.supabase and self.redis:
            self.logger.info("DatabaseManager utilizing existing injected pools.")
            return

        max_retries = 5
        retry_delay = 5  # seconds

        for attempt in range(1, max_retries + 1):
            try:
                self.logger.info(f"Infrastructure connection attempt {attempt}/{max_retries}...")

                # 1. Supabase API Init (Bypasses Port 5432 blocks)
                if not self.supabase:
                    self.logger.info("Initializing Supabase Async HTTP Client with extended timeout...")
                    client_options = AsyncClientOptions(postgrest_client_timeout=30, storage_client_timeout=30)

                    self.supabase = await acreate_client(
                        settings.SUPABASE_URL,
                        settings.SUPABASE_ANON_KEY,
                        options=client_options
                    )
                    self.logger.info("Supabase connection established.")

                # 2. Redis Init with Reconnect-Friendly Parameters
                if not self.redis:
                    self.logger.info("Initializing Redis connection...")
                    self.redis = aioredis.from_url(
                        self.redis_url,
                        encoding="utf-8",
                        decode_responses=False,
                        socket_connect_timeout=10,
                        socket_timeout=10,
                        retry_on_timeout=True
                    )
                    await self.redis.ping()
                    self.logger.info("Redis cache connection established.")

                return

            except Exception as e:
                if self.redis:
                    await self.redis.aclose()
                    self.redis = None
                self.supabase = None

                self.logger.warning(f"Connection attempt {attempt} failed: {str(e)}")

                if attempt == max_retries:
                    self.logger.critical("All 5 infrastructure connection attempts failed.")
                    raise e

                await asyncio.sleep(retry_delay)

    async def close(self) -> None:
        if self.redis:
            await self.redis.aclose()
            self.redis = None
            self.logger.info("Redis connection closed gracefully.")
        if self.supabase:
            self.supabase = None
            self.logger.info("Supabase HTTP client reference cleared.")

    # -------------------------------------------------------------------------
    # REDIS AUTO-RECONNECT LOGIC
    # -------------------------------------------------------------------------

    async def _reconnect_redis(self, max_retries: int = 5, base_delay: float = 1.0) -> bool:
        """Re-initializes the Async Redis connection pool with exponential backoff, jitter, and background health checks."""
        if not self.redis_url:
            self.logger.error("❌ Cannot execute Redis auto-reconnect: REDIS_URL is not configured.")
            return False

        for attempt in range(1, max_retries + 1):
            try:
                if self.redis:
                    try:
                        await self.redis.aclose()
                    except Exception:
                        pass

                self.redis = aioredis.from_url(
                    self.redis_url,
                    encoding="utf-8",
                    decode_responses=False,
                    socket_connect_timeout=10,
                    socket_timeout=10,
                    socket_keepalive=True,
                    health_check_interval=30,
                    retry_on_timeout=True
                )

                await asyncio.wait_for(self.redis.ping(), timeout=3.0)
                self.logger.info(f"✅ Redis auto-reconnect succeeded on attempt {attempt}.")
                return True

            except socket.gaierror as dns_err:
                # Explicitly trap DNS failures to differentiate from auth/timeout bugs
                self.logger.error(f"🌐 Network Error (Attempt {attempt}): Unable to resolve Aiven hostname. {dns_err}")
            except Exception as err:
                err_msg = str(err) or repr(err)
                self.logger.warning(f"⚠️ Redis connection error: {err_msg}")

            if attempt < max_retries:
                # Exponential backoff with random jitter (0.1s to 1.0s)
                delay = (base_delay * (2 ** (attempt - 1))) + random.uniform(0.1, 1.0)
                self.logger.warning(f"⏳ Retrying Redis connection in {delay:.2f}s...")
                await asyncio.sleep(delay)

        self.logger.error(
            "❌ Redis automatic reconnect failed after maximum retries. Proceeding without cache fallback.")
        return False

    async def ensure_redis(self) -> bool:
        """
        Verifies active Redis connection. If connection is dropped or uninitialized,
        triggers an atomic reconnection attempt.
        """
        if not self.redis:
            if not self.redis_url:
                return False
            async with self._reconnect_lock:
                # Check again inside the lock in case another task initialized it
                if not self.redis:
                    return await self._reconnect_redis()
                return True

        try:
            await asyncio.wait_for(self.redis.ping(), timeout=1.5)
            return True
        except (socket.gaierror, asyncio.TimeoutError, Exception) as e:
            err_type = "DNS failure" if isinstance(e, socket.gaierror) else "Connection drop"
            self.logger.warning(f"🔄 Active Redis connection lost ({err_type}). Triggering auto-reconnect...")

            async with self._reconnect_lock:
                try:
                    # Final check before destroying the pool; another coroutine may have fixed it
                    await asyncio.wait_for(self.redis.ping(), timeout=1.0)
                    return True
                except Exception:
                    return await self._reconnect_redis()

    async def execute_redis(self, command_func: Callable, *args: Any, **kwargs: Any) -> Any:
        """Safely executes any Redis command with auto-reconnect and soft fallback."""
        if not await self.ensure_redis():
            self.logger.warning("Skipping Redis operation (Cache service currently unavailable).")
            return None

        try:
            return await command_func(*args, **kwargs)
        except (RedisConnectionError, RedisTimeoutError, socket.gaierror) as conn_err:
            self.logger.warning(f"Redis operation failed ({conn_err}). Retrying after auto-reconnect...")
            if await self._reconnect_redis():
                try:
                    return await command_func(*args, **kwargs)
                except Exception as retry_err:
                    self.logger.error(f"Redis operation failed on second attempt post-reconnect: {retry_err}")
            return None
        except Exception as general_err:
            self.logger.error(f"Unexpected Redis command error: {general_err}")
            return None

    # -------------------------------------------------------------------------
    # HEALTH CHECK
    # -------------------------------------------------------------------------

    async def health_check(self) -> bool:
        """Primary health check verifying Supabase API while keeping Redis soft-checked."""
        api_healthy, redis_healthy = False, False

        # 1. Primary DB Check (Critical)
        if self.supabase:
            try:
                # Table query check for active database pool with 5.0s timeout safeguard
                res = await asyncio.wait_for(
                    self.supabase.table("users").select("telegram_id").limit(1).execute(),
                    timeout=5.0
                )
                api_healthy = res is not None
            except asyncio.TimeoutError:
                self.logger.error("❌ Supabase table query ping timed out after 5.0s")
                try:
                    # Fallback to session check if table query times out
                    await asyncio.wait_for(self.supabase.auth.get_session(), timeout=3.0)
                    api_healthy = True
                except Exception as fallback_err:
                    self.logger.error(f"❌ Supabase session fallback failed after timeout: {str(fallback_err)}")
            except Exception as e:
                self.logger.warning(f"Supabase table query failed ({str(e)}), attempting auth fallback...")
                try:
                    # Fallback to session check if table query raises query/permission errors
                    await asyncio.wait_for(self.supabase.auth.get_session(), timeout=3.0)
                    api_healthy = True
                except Exception as fallback_err:
                    self.logger.error(
                        f"❌ Supabase API health check diagnostic failure: {str(fallback_err)}",
                        exc_info=True
                    )

        # 2. Redis Soft Check with Auto-Reconnect Trigger
        try:
            redis_healthy = await self.ensure_redis()
        except Exception as redis_err:
            self.logger.warning(f"Redis soft-check diagnostic failure: {str(redis_err)}")
            redis_healthy = False

        # Log diagnostic warning if cache is offline, but do not trigger HTTP 503
        if api_healthy and not redis_healthy:
            self.logger.info("System status: DEGRADED (Primary DB healthy, Redis warming up/reconnecting)")

        # System is reported healthy to the gateway if primary database API is up
        return api_healthy

    # ==========================================
    # SAFE TYPE CONVERTERS & HELPERS
    # ==========================================
    @staticmethod
    def _safe_str(val: Any, default: str = "") -> str:
        return str(val).strip() if val is not None else default

    @staticmethod
    def _safe_int(val: Any, default: int = 0) -> int:
        try:
            return (
                int(val)
                if val is not None and str(val).strip() != ""
                else default
            )
        except (ValueError, TypeError):
            return default

    @staticmethod
    def _safe_float(val: Any, default: float = 0.0) -> float:
        try:
            return (
                float(val)
                if val is not None and str(val).strip() != ""
                else default
            )
        except (ValueError, TypeError):
            return default

    @staticmethod
    def _validate_property_use(
            vertical_type_val: str, property_use: Any
    ) -> str:
        if property_use is None:
            return ""

        prop_str = str(property_use).strip()
        vertical = (
            str(vertical_type_val).strip().lower() if vertical_type_val else ""
        )

        # Freight and ride bypass category restrictions and accept free text
        if vertical in {"freight", "ride"}:
            return prop_str

        # Normalize input: remove emojis/symbols (preserve &), collapse whitespace, convert to lowercase
        cleaned = re.sub(r"[^\w\s&]", "", prop_str).strip().lower()
        cleaned = re.sub(r"\s+", " ", cleaned)

        VALID_CATEGORIES = {
            "housing": {
                "residential",
                "commercial",
                "residential plot",
                "commercial plot",
            },
            "vehicle": {
                "sedan",
                "suv",
                "van/minivan",
                "bus/minibus",
                "truck",
                "moto",
            },
            "equipment": {
                "construction & earthmoving",
                "agriculture & irrigation",
                "generator & power",
                "event tech & communication",
            },
        }

        if vertical in VALID_CATEGORIES:
            allowed = VALID_CATEGORIES[vertical]
            if cleaned in allowed:
                return cleaned

            # Housing fallbacks
            if vertical == "housing":
                if "commercial plot" in cleaned or "commercial land" in cleaned:
                    return "commercial plot"
                if "commercial" in cleaned:
                    return "commercial"
                if (
                        "residential plot" in cleaned
                        or "residential land" in cleaned
                ):
                    return "residential plot"
                if "residential" in cleaned:
                    return "residential"

            # Equipment fallbacks
            if vertical == "equipment":
                if "agriculture" in cleaned or "irrigation" in cleaned:
                    return "agriculture & irrigation"
                if "construction" in cleaned or "earthmoving" in cleaned:
                    return "construction & earthmoving"
                if "generator" in cleaned or "power" in cleaned:
                    return "generator & power"
                if "event" in cleaned or "communication" in cleaned:
                    return "event tech & communication"

            # Vehicle fallbacks
            if vertical == "vehicle":
                if "bus" in cleaned or "minibus" in cleaned:
                    return "bus/minibus"
                if "van" in cleaned or "minivan" in cleaned:
                    return "van/minivan"

            raise ValueError(
                f"Invalid property_use '{prop_str}' for vertical '{vertical}'. Allowed options: {allowed}"
            )
        return prop_str

    # Shared retry helper for Supabase insertions
    async def _execute_insert(self, table_name: str, final_payload: dict) -> dict:
        max_retries = 3
        delay = 2

        for attempt in range(1, max_retries + 1):
            try:
                response = (
                    await self.supabase.table(table_name)
                    .insert(final_payload)
                    .execute()
                )
                return response.data[0] if response.data else {}
            except Exception as e:
                try:
                    self.logger.warning(
                        f"Attempt {attempt}/{max_retries} failed to insert into {table_name}: {e}"
                    )
                except AttributeError:
                    logging.warning(
                        f"Attempt {attempt}/{max_retries} failed to insert into {table_name}: {e}"
                    )

                if attempt == max_retries:
                    try:
                        self.logger.error(
                            f"Critical: All retries exhausted for table '{table_name}'."
                        )
                    except AttributeError:
                        pass
                    raise e

                await asyncio.sleep(delay)
                delay *= 2

        return {}

    async def update_user_language(self, user_id: int, lang_code: str) -> bool:
        """Updates language columns in Supabase for a given user."""
        self.logger.info(
            f"⚙️ DBManager attempting to update language to '{lang_code}' for user_id: {user_id} (type: {type(user_id)})")

        if not self.supabase:
            self.logger.error("❌ CRITICAL: Supabase client is None inside DBManager!")
            return False

        try:
            now_iso = datetime.now(timezone.utc).isoformat()
            update_payload = {
                "preferred_language": lang_code,
                "language_code": lang_code,
                "language": lang_code,
                "updated_at": now_iso
            }

            # Try with integer telegram_id
            response = self.supabase.table('users').update(update_payload).eq('telegram_id', user_id).execute()
            if inspect.isawaitable(response):
                response = await response

            self.logger.info(f"📊 Supabase integer update raw response: {response}")

            if response and hasattr(response, 'data') and isinstance(response.data, list) and len(response.data) > 0:
                self.logger.info(f"✅ Successfully updated language for user {user_id}")
                return True

            # Fallback: Try with string telegram_id
            response = self.supabase.table('users').update(update_payload).eq('telegram_id', str(user_id)).execute()
            if inspect.isawaitable(response):
                response = await response

            self.logger.info(f"📊 Supabase string fallback update raw response: {response}")

            if response and hasattr(response, 'data') and isinstance(response.data, list) and len(response.data) > 0:
                self.logger.info(f"✅ Successfully updated language for user {user_id} (string fallback)")
                return True

            self.logger.warning(
                f"⚠️ Supabase update returned 0 rows modified for user_id: {user_id}. Check if the user exists in the 'users' table.")
            return False

        except Exception as e:
            self.logger.error(f"❌ Exception during Supabase language update for {user_id}: {str(e)}", exc_info=True)
            return False

    async def get_user_language(self, user_id: int) -> str:
        if not isinstance(user_id, int) or user_id <= 0:
            return self._default_lang

        if not self.supabase:
            return self._default_lang

        try:
            response = await self.supabase.table('users').select('language_code').eq('telegram_id', user_id).limit(
                1).execute()
            if response.data and len(response.data) > 0:
                cleaned_lang = str(response.data[0].get('language_code', '')).strip().lower()
                if cleaned_lang in self._supported_languages:
                    return cleaned_lang
            return self._default_lang
        except Exception as e:
            self.logger.critical(f"Database language lookup failure for {user_id}: {str(e)}", exc_info=True)
            return self._default_lang

    async def log_audit_action(self, action: str, endpoint: str, parameters: dict) -> None:
        """Replaces `execute_with_audit` as raw SQL is no longer supported via SDK."""
        if not self.supabase:
            return

        try:
            await self.supabase.table('audit_logs').insert({
                "action": action,
                "query": endpoint,
                "parameters": json.dumps(parameters),
                "status": "SUCCESS"
            }).execute()
        except Exception as e:
            self.logger.critical(f"CRITICAL: Audit system failed to record a transaction fault! {e}")

    # ==========================================
    # PROGRESSIVE STATE & DRAFT CONVERSION ENGINES
    # ==========================================

    async def save_user_state(self, user_id: int, key: str, value: Any) -> None:
        if not self.supabase:
            return

        try:
            # Read
            response = await self.supabase.table('user_states').select('state_data').eq('user_id',
                                                                                        user_id).maybe_single().execute()
            current_state = response.data.get('state_data', {}) if response.data else {}

            # Modify
            current_state[key] = str(value)

            # Write
            await self.supabase.table('user_states').upsert({
                "user_id": user_id,
                "state_data": current_state
            }, on_conflict="user_id").execute()
        except Exception as e:
            self.logger.error(f"Failed to save user state: {e}")

    async def save_draft_transaction(self, user_id: int, current_step: str, payload: Dict[str, Any],
                                     photo_url: Optional[str] = None) -> None:
        if not self.supabase:
            return

        try:
            # Ensure dependency exists
            await self.supabase.table('users').upsert({
                "telegram_id": user_id,
                "full_name": "Pending",
                "phone_number": "PENDING"
            }, on_conflict="telegram_id", ignore_duplicates=True).execute()

            # Read existing draft
            response = await self.supabase.table('draft_transactions').select('data_payload, photo_url').eq('user_id',
                                                                                                            user_id).maybe_single().execute()

            # Modify
            current_payload = response.data.get('data_payload', {}) if response.data and response.data.get(
                'data_payload') else {}
            current_payload.update(payload)
            final_photo = photo_url if photo_url else (response.data.get('photo_url') if response.data else None)

            # Write
            await self.supabase.table('draft_transactions').upsert({
                "user_id": user_id,
                "current_step": current_step,
                "data_payload": current_payload,
                "photo_url": final_photo,
                "updated_at": datetime.utcnow().isoformat()
            }, on_conflict="user_id").execute()

        except Exception as e:
            self.logger.error(f"Failed to save draft transaction: {e}")

    async def get_draft_transaction(self, user_id: int) -> Optional[Dict[str, Any]]:
        if not self.supabase:
            return None

        response = await self.supabase.table('draft_transactions').select('current_step, data_payload, photo_url').eq(
            'user_id', user_id).maybe_single().execute()

        if response.data:
            return {
                "current_step": response.data.get("current_step"),
                "data": response.data.get("data_payload", {}),
                "photo_url": response.data.get("photo_url")
            }
        return None

    async def clear_draft_transaction(self, user_id: int) -> None:
        if not self.supabase:
            return
        await self.supabase.table('draft_transactions').delete().eq('user_id', user_id).execute()

    async def save_negotiation_state(self, chat_id: int, state_data: Dict[str, Any]) -> None:
        if not self.supabase:
            return
        await self.supabase.table('negotiation_states').upsert({
            "chat_id": chat_id,
            "data": state_data,
            "updated_at": datetime.utcnow().isoformat()
        }, on_conflict="chat_id").execute()

    async def get_negotiation_state(self, chat_id: int) -> Optional[Dict[str, Any]]:
        if not self.supabase:
            return None
        response = await self.supabase.table('negotiation_states').select('data').eq('chat_id',
                                                                                     chat_id).maybe_single().execute()
        return response.data.get('data') if response.data else None

    # ==========================================
    # ASSET CREATION & REGISTRATION
    # ==========================================

    async def ensure_user_exists(self, user_id: int, full_name: str, phone_number: str) -> None:
        # 1. Search for existing user by Telegram ID (The unique bot identity)
        response = await self.supabase.table('users').select('*').eq('telegram_id', user_id).execute()

        # 2. If user exists, we do nothing (they are already registered)
        if response.data and len(response.data) > 0:
            logger.info(f"User {user_id} already exists. Skipping creation.")
            return

        # 3. If user does NOT exist, only then attempt to insert
        try:
            await self.supabase.table('users').insert({
                'telegram_id': user_id,
                'full_name': full_name,
                'phone_number': phone_number
            }).execute()
            logger.info(f"New user {user_id} registered successfully.")

        except Exception as e:
            # If the error is a duplicate phone number, we log it but don't crash
            if "23505" in str(e) or "unique constraint" in str(e).lower():
                logger.warning(f"Phone number {phone_number} is already associated with another account.")
                # Depending on your business logic, you might want to link the telegram_id to this existing phone number
            else:
                logger.error(f"Failed to create user: {e}")
                raise

    async def get_user_latest_active_negotiation(self, user_id: int) -> dict | None:
        # Query your database table (e.g., Supabase 'deals') for the latest active negotiation
        response = await self.supabase.table("deals") \
            .select("*") \
            .or_(f"seeker_id.eq.{user_id},provider_id.eq.{user_id}") \
            .eq("deal_state", "negotiating") \
            .order("created_at", desc=True) \
            .limit(1) \
            .maybe_single() \
            .execute()
        return response.data if response else None

    # ==========================================
    # 2. SEEKER REQUESTS CREATION ENGINE
    # ==========================================
    async def create_request(self, data: dict) -> dict:
        if not self.supabase:
            raise RuntimeError("Supabase client uninitialized.")

        # Map legacy keys
        if "category" in data and "vertical_type" not in data:
            data["vertical_type"] = data.pop("category")
        if "location" in data and "location_text" not in data:
            data["location_text"] = data.pop("location")

        # Filter internal metadata keys
        cleaned_data = {
            k: v for k, v in data.items() if not k.startswith("_")
        }

        vertical_type = self._safe_str(cleaned_data.get("vertical_type"))
        raw_property_use = cleaned_data.get(
            "property_use"
        ) or cleaned_data.get("vehicle_type")
        validated_property_use = self._validate_property_use(
            vertical_type, raw_property_use
        )

        lat = self._safe_float(cleaned_data.get("latitude"), 0.0)
        lon = self._safe_float(cleaned_data.get("longitude"), 0.0)
        wkt_geography = (
            f"SRID=4326;POINT({lon} {lat})"
            if (lat != 0.0 or lon != 0.0)
            else None
        )

        # Robust extraction checking both 'fuel_type' and 'acceptable_fuel_type' keys from user_data
        fuel_type = self._safe_str(
            cleaned_data.get("fuel_type")
            or cleaned_data.get("acceptable_fuel_type")
        )

        # Construct sanitized payload for seeker requests
        payload = {
            **cleaned_data,
            "vertical_type": vertical_type,
            "property_use": validated_property_use,
            "seeker_id": self._safe_int(cleaned_data.get("seeker_id")),
            "max_budget": self._safe_float(
                cleaned_data.get("max_budget")
                or cleaned_data.get("budget")
                or cleaned_data.get("price"),
                0.0,
            ),
            "currency": cleaned_data.get("currency", "ETB"),
            "location_text": self._safe_str(cleaned_data.get("location_text")),
            "destination_text": self._safe_str(
                cleaned_data.get("destination_text")
            ),
            "operating_region": self._safe_str(
                cleaned_data.get("operating_region")
            ),
            "latitude": lat,
            "longitude": lon,
            "location_geog": wkt_geography,
            "equipment_type": self._safe_str(
                cleaned_data.get("equipment_type")
            ),
            "vehicle_type": self._safe_str(
                cleaned_data.get("vehicle_type") or validated_property_use
            ),
            "make_model_year": self._safe_str(
                cleaned_data.get("make_model_year")
            ),
            "fuel_type": fuel_type,  # Key fix
            "preferred_brand": self._safe_str(
                cleaned_data.get("preferred_brand")
                or cleaned_data.get("brand")
                or cleaned_data.get("manufacturer")
            ),
            "required_amenities": cleaned_data.get("required_amenities") or cleaned_data.get("amenities"),
            "min_bedrooms": self._safe_int(
                cleaned_data.get("min_bedrooms")
                or cleaned_data.get("bedrooms"),
                0,
            ),
            "min_sq_meters": self._safe_float(
                cleaned_data.get("min_sq_meters")
                or cleaned_data.get("sq_meters"),
                0.0,
            ),
            "bulk_quantity": self._safe_int(
                cleaned_data.get("bulk_quantity"), 1
            ),
            "cargo_weight_kgs": self._safe_float(
                cleaned_data.get("cargo_weight_kgs")
                or cleaned_data.get("max_weight_kgs"),
                0.0,
            ),
            "party_size": self._safe_int(
                cleaned_data.get("party_size")
                or cleaned_data.get("available_seats"),
                0,
            ),
            "capacity_output": self._safe_float(
                cleaned_data.get("capacity_output"), 0.0
            ),
            "status": cleaned_data.get("status", "active"),
        }

        # Allowlist matching seeker_requests table schema
        valid_columns = {
            "id",
            "seeker_id",
            "vertical_type",
            "transaction_type",
            "property_use",
            "location_text",
            "destination_text",
            "operating_region",
            "latitude",
            "longitude",
            "location_geog",
            "max_budget",
            "currency",
            "min_bedrooms",
            "min_sq_meters",
            "required_amenities",
            "contact_phone",
            "status",
            "created_at",
            "updated_at",
            "bulk_quantity",
            "cargo_weight_kgs",
            "party_size",
            "make_model_year",
            "vehicle_type",
            "preferred_brand",
            "capacity_output",
            "equipment_type",
            "fuel_type",  # Passed schema filter
        }

        final_payload = {k: v for k, v in payload.items() if k in valid_columns}
        return await self._execute_insert("seeker_requests", final_payload)

    async def fetch_pending_requests(self, vertical_type: str = None) -> list:
        """Fetch pending seeker requests for reverse provider matching from Supabase."""
        try:
            if not self.supabase:
                raise RuntimeError("Supabase client is not initialized.")

            query = self.supabase.table("seeker_requests").select("*").eq("status", "active")
            if vertical_type:
                query = query.eq("vertical_type", vertical_type)

            response = await query.execute()
            return response.data if response and hasattr(response, 'data') else []
        except Exception as e:
            self.logger.error(f"Error fetching pending requests: {e}")
            return []

    # ==========================================
    # 1. PROVIDER LISTINGS CREATION ENGINE
    # ==========================================
    async def create_listing(self, data: dict) -> dict:
        if not self.supabase:
            raise RuntimeError("Supabase client uninitialized.")

        # Map legacy keys
        if "category" in data and "vertical_type" not in data:
            data["vertical_type"] = data.pop("category")
        if "location" in data and "location_text" not in data:
            data["location_text"] = data.pop("location")

        # Safely preserve title/description info
        title_val = data.pop("title", None)
        desc_val = data.pop("description", None)
        extra_info = []
        if title_val:
            extra_info.append(str(title_val))
        if desc_val:
            extra_info.append(str(desc_val))

        existing_amenities = data.get("amenities")
        if extra_info:
            combined_extra = " - ".join(extra_info)
            if existing_amenities:
                data["amenities"] = f"{existing_amenities} | {combined_extra}"
            else:
                data["amenities"] = combined_extra

        # Filter internal metadata keys
        cleaned_data = {
            k: v for k, v in data.items() if not k.startswith("_")
        }

        vertical_type = self._safe_str(cleaned_data.get("vertical_type"))
        raw_property_use = cleaned_data.get(
            "property_use"
        ) or cleaned_data.get("vehicle_type")
        validated_property_use = self._validate_property_use(
            vertical_type, raw_property_use
        )

        # Extract coordinates & PostGIS WKT string
        lat = self._safe_float(cleaned_data.get("latitude"), 0.0)
        lon = self._safe_float(cleaned_data.get("longitude"), 0.0)
        wkt_geography = (
            f"SRID=4326;POINT({lon} {lat})"
            if (lat != 0.0 or lon != 0.0)
            else None
        )

        # Robust extraction checking both 'fuel_type' and 'acceptable_fuel_type' keys from user_data
        fuel_type = self._safe_str(
            cleaned_data.get("fuel_type")
            or cleaned_data.get("acceptable_fuel_type")
        )

        # Construct sanitized payload
        payload = {
            **cleaned_data,
            "vertical_type": vertical_type,
            "property_use": validated_property_use,
            "provider_id": self._safe_int(cleaned_data.get("provider_id")),
            "price": self._safe_float(cleaned_data.get("price"), 0.0),
            "bulk_quantity": self._safe_int(
                cleaned_data.get("bulk_quantity"), 1
            ),
            "currency": cleaned_data.get("currency", "ETB"),
            "location_text": self._safe_str(cleaned_data.get("location_text")),
            "destination_text": self._safe_str(
                cleaned_data.get("destination_text")
            ),
            "operating_region": self._safe_str(
                cleaned_data.get("operating_region")
            ),
            "latitude": lat,
            "longitude": lon,
            "location_geog": wkt_geography,
            "equipment_type": self._safe_str(
                cleaned_data.get("equipment_type")
            ),
            "vehicle_type": self._safe_str(
                cleaned_data.get("vehicle_type") or validated_property_use
            ),
            "make_model_year": self._safe_str(
                cleaned_data.get("make_model_year")
            ),
            "fuel_type": fuel_type,  # Key fix
            "brand": self._safe_str(
                cleaned_data.get("brand") or cleaned_data.get("manufacturer")
            ),
            "vehicle_plate": self._safe_str(
                cleaned_data.get("vehicle_plate")
                or cleaned_data.get("plate_number")
            ),
            "bedrooms": self._safe_int(
                cleaned_data.get("bedrooms")
                or cleaned_data.get("number_of_bedrooms"),
                0,
            ),
            "sq_meters": self._safe_float(
                cleaned_data.get("sq_meters") or cleaned_data.get("sq_m"), 0.0
            ),
            "photo_url": cleaned_data.get("photo_url") or cleaned_data.get("asset_photo_url"),
            "status": cleaned_data.get("status", "active"),
            "max_weight_kgs": self._safe_float(
                cleaned_data.get("max_weight_kgs")
                or cleaned_data.get("capacity_kgs"),
                0.0,
            ),
            "available_seats": self._safe_int(
                cleaned_data.get("available_seats"), 0
            ),
            "capacity_output": self._safe_float(
                cleaned_data.get("capacity_output"), 0.0
            ),
            "hours_used": self._safe_float(cleaned_data.get("hours_used"), 0.0),
        }

        payload.pop("asset_photo_url", None)

        # Allowlist matching provider_listings table schema
        valid_columns = {
            "id",
            "provider_id",
            "vertical_type",
            "transaction_type",
            "property_use",
            "equipment_type",
            "location_text",
            "destination_text",
            "operating_region",
            "latitude",
            "longitude",
            "location_geog",
            "price",
            "currency",
            "bedrooms",
            "sq_meters",
            "amenities",
            "photo_url",
            "contact_phone",
            "status",
            "created_at",
            "updated_at",
            "bulk_quantity",
            "max_weight_kgs",
            "available_seats",
            "make_model_year",
            "vehicle_type",
            "capacity_output",
            "hours_used",
            "brand",
            "vehicle_plate",
            "fuel_type",  # Passed schema filter
            "years_vehicle_used",
        }

        final_payload = {k: v for k, v in payload.items() if k in valid_columns}
        return await self._execute_insert("provider_listings", final_payload)

    async def get_listing_by_id(self, listing_id: str | int):
        """Fetch a specific provider listing by its ID from Supabase."""
        try:
            if not self.supabase:
                raise RuntimeError("Supabase client is not initialized. Call connect() first.")

            response = await self.supabase.table("provider_listings").select("*").eq("id",
                                                                                     listing_id).maybe_single().execute()
            return response.data if response and hasattr(response, 'data') else None
        except Exception as e:
            self.logger.error(f"Error fetching listing {listing_id}: {e}")
            return None

    async def fetch_active_candidates(self, vertical_type: str, location_query: str = "") -> List[Dict[str, Any]]:
        """
        Fetches active provider listings matching a specific vertical type and optional location.
        """
        if not self.supabase:
            return []

        try:
            query = self.supabase.table('provider_listings') \
                .select('*') \
                .eq('vertical_type', vertical_type) \
                .eq('status', 'active')

            if location_query and location_query.strip():
                query = query.ilike('location_text', f'%{location_query.strip()}%')

            response = await query.execute()

            if response and hasattr(response, 'data') and response.data:
                return response.data

            return []

        except Exception as e:
            logger.error(f"⚠️ Error fetching active candidates for vertical '{vertical_type}': {e}")
            return []

    from typing import List, Dict, Any

    async def fetch_vehicle_candidates(self, listing_type: str, vehicle_type: str) -> List[Dict[str, Any]]:
        if not self.supabase:
            return []

        # 1. TRANSACTION TYPE NORMALIZATION
        raw_trans = (listing_type or '').lower().strip()
        if raw_trans in ['rent', 'rental', 'hire']:
            trans_variants = ['rent', 'rental']
        elif raw_trans in ['sell', 'sale', 'buy', 'purchase']:
            trans_variants = ['sell', 'sale']
        elif raw_trans:
            trans_variants = [raw_trans]
        else:
            trans_variants = []

        # 2. VEHICLE CATEGORY & ALIAS MAPPING
        raw_vtype = (vehicle_type or '').lower().strip()

        category_variants_map = {
            'bus': ['bus', 'minibus', 'bus/minibus'],
            'minibus': ['bus', 'minibus', 'bus/minibus'],
            'car': ['sedan', 'car', 'automobile'],
            'sedan': ['sedan', 'car', 'automobile'],
            'suv': ['suv'],
            'van': ['van', 'cargo_van', 'minivan', 'van/minivan'],
            'cargo_van': ['van', 'cargo_van', 'minivan', 'van/minivan'],
            'minivan': ['van', 'cargo_van', 'minivan', 'van/minivan'],
            'truck': ['truck', 'lorii', 'loli', 'commercial'],
            'moto': ['moto', 'motorcycle', 'bike', 'baajajii', 'bajaj'],
            'motorcycle': ['moto', 'motorcycle', 'bike', 'baajajii', 'bajaj'],
            'bike': ['moto', 'motorcycle', 'bike', 'baajajii', 'bajaj']
        }

        vtype_variants = category_variants_map.get(raw_vtype, [raw_vtype] if raw_vtype else [])

        try:
            # --- TIER 1: Strict Query with Extended .ilike Filters ---
            query = self.supabase.table('provider_listings') \
                .select('*') \
                .eq('vertical_type', 'vehicle') \
                .eq('status', 'active')

            if trans_variants:
                query = query.in_('transaction_type', trans_variants)

            if vtype_variants:
                or_conditions = []
                for var in vtype_variants:
                    # Includes property_use, vehicle_type, make_model_year, and amenities
                    or_conditions.append(f"property_use.ilike.%{var}%")
                    or_conditions.append(f"vehicle_type.ilike.%{var}%")
                    or_conditions.append(f"make_model_year.ilike.%{var}%")
                    or_conditions.append(f"amenities.ilike.%{var}%")

                query = query.or_(",".join(or_conditions))

            response = await query.execute()
            candidates = response.data if response.data else []

            # --- TIER 2: First Fallback (Drop Category/Model Filters, Keep Transaction Variants) ---
            if not candidates and vtype_variants:
                print(
                    f"⚠️ Strict query for vehicle_type='{raw_vtype}' returned 0 results. Dropping vehicle category filter.")
                fb_query = self.supabase.table('provider_listings') \
                    .select('*') \
                    .eq('vertical_type', 'vehicle') \
                    .eq('status', 'active')

                if trans_variants:
                    fb_query = fb_query.in_('transaction_type', trans_variants)

                response = await fb_query.execute()
                candidates = response.data if response.data else []

            # --- TIER 3: Ultimate Fallback (Fetch ALL active vehicles) ---
            if not candidates:
                print(
                    "⚠️ Transaction/Property query returned 0 results. Executing ultimate fallback: fetching all active vehicles.")
                ultimate_query = self.supabase.table('provider_listings') \
                    .select('*') \
                    .eq('vertical_type', 'vehicle') \
                    .eq('status', 'active')

                response = await ultimate_query.execute()
                candidates = response.data if response.data else []

            # --- 3. METADATA PARSING & HARMONIZATION FOR SCORING ---
            for item in candidates:
                amenities = item.get('amenities', '')

                # Extract make_model_year if missing or null string
                mmy = item.get('make_model_year')
                if not mmy or str(mmy).lower() in ['n/a', 'none', 'nan', '']:
                    if isinstance(amenities, str) and 'Model:' in amenities:
                        try:
                            item['make_model_year'] = amenities.split('Model:')[1].split(',')[0].strip()
                        except IndexError:
                            pass

                # Extract fuel_type if missing or null string
                fuel = item.get('fuel_type')
                if not fuel or str(fuel).lower() in ['n/a', 'none', 'nan', '']:
                    if isinstance(amenities, str) and 'Fuel:' in amenities:
                        try:
                            item['fuel_type'] = amenities.split('Fuel:')[1].split(',')[0].strip()
                        except IndexError:
                            pass

            print(
                f"DEBUG: Final search result count: {len(candidates)} (Query: {trans_variants or 'any'} / {vtype_variants or 'any'})")
            return candidates

        except Exception as e:
            print(f"⚠️ Error fetching vehicle candidates: {e}")
            return []

    async def fetch_equipment_candidates(self,
                                         listing_type: str = "", equipment_type: str = "") -> List[Dict[str, Any]]:
        """
        Fetches equipment candidates using tokenized keyword-based partial matching (.ilike),
        normalizes transaction/property types, and extracts amenities metadata.
        """
        if not self.supabase:
            return []

        # 1. NORMALIZATION MAPPERS
        transaction_map = {
            'buy': 'sell',
            'sale': 'sell',
            'purchase': 'sell',
            'rent': 'rental',
            'hire': 'rental',
            'lease': 'rental'
        }

        equipment_map = {
            'excavator': 'excavator',
            'digger': 'excavator',
            'dozer': 'bulldozer',
            'bulldozer': 'bulldozer',
            'loader': 'wheel loader',
            'wheel_loader': 'wheel loader',
            'backhoe': 'backhoe loader',
            'crane': 'crane',
            'grader': 'motor grader',
            'forklift': 'forklift',
            'compactor': 'roller',
            'roller': 'roller'
        }

        # Defensive argument normalization
        norm_listing = str(listing_type).lower().strip() if listing_type else ""
        norm_etype = str(equipment_type).lower().strip() if equipment_type else ""

        if norm_etype in ["none", "null", "any", "all", "0", ""]:
            norm_etype = ""
        if norm_listing in ["none", "null", "any", "all", "0", ""]:
            norm_listing = ""

        search_trans = transaction_map.get(norm_listing, norm_listing) if norm_listing else None
        search_etype = equipment_map.get(norm_etype, norm_etype) if norm_etype else None

        try:
            # --- OPTIMIZED QUERY: Base Filter ---
            query = self.supabase.table('provider_listings') \
                .select('*') \
                .eq('vertical_type', 'equipment') \
                .eq('status', 'active')

            if search_trans:
                query = query.eq('transaction_type', search_trans)

            # --- TOKENIZED KEYWORD SEARCH (.ilike across valid columns) ---
            if search_etype:
                stopwords = {"rental", "for", "none", "hire", "lease", "service", "the", "a", "an"}
                keywords = [
                    word.lower().strip()
                    for word in search_etype.split()
                    if word.lower().strip() not in stopwords
                ]

                # Fallback to the raw search string if all words are filtered out
                if not keywords:
                    keywords = [search_etype]

                # Build flexible OR conditions checking valid text columns only
                conditions = []
                for kw in keywords:
                    conditions.append(f"property_use.ilike.%{kw}%")
                    conditions.append(f"brand.ilike.%{kw}%")
                    conditions.append(f"amenities.ilike.%{kw}%")

                query = query.or_(",".join(conditions))

            response = await query.execute()
            candidates = response.data if response and hasattr(response, 'data') and response.data else []

            # --- ULTIMATE FALLBACK: Broad Safety Net ---
            if not candidates and search_etype:
                logger.info(
                    f"⚠️ Tokenized query for '{search_etype}' returned 0 results. Executing fallback: fetching all active equipment.")
                fb_query = self.supabase.table('provider_listings') \
                    .select('*') \
                    .eq('vertical_type', 'equipment') \
                    .eq('status', 'active')
                if search_trans:
                    fb_query = fb_query.eq('transaction_type', search_trans)
                fb_response = await fb_query.execute()
                candidates = fb_response.data if fb_response and hasattr(fb_response,
                                                                         'data') and fb_response.data else []

            # 2. PARSE AMENITIES METADATA FOR EQUIPMENT
            for item in candidates:
                amenities = item.get('amenities', '')

                # Extract Brand prioritizing native DB column
                db_brand = item.get('brand')
                if db_brand:
                    item['preferred_brands'] = [b.strip() for b in db_brand.split(',')]
                elif isinstance(amenities, str) and 'Brands:' in amenities:
                    try:
                        brand_part = amenities.split('Brands:')[1].split('|')[0].strip()
                        item['brand'] = brand_part
                        item['preferred_brands'] = [b.strip() for b in brand_part.split(',')]
                    except IndexError:
                        pass

                # Extract Features if present
                if isinstance(amenities, str) and 'Features:' in amenities:
                    try:
                        feat_part = amenities.split('Features:')[1].strip()
                        item['features'] = [f.strip() for f in feat_part.split('-') if f.strip()]
                    except IndexError:
                        pass
                elif not item.get('features') and isinstance(amenities, str):
                    item['features'] = [f.strip() for f in amenities.replace('|', '-').split('-') if f.strip()]

            logger.info(
                f"DEBUG: Final equipment candidate count: {len(candidates)} (Query: {search_trans or 'any'} / {search_etype or 'any'})")
            return candidates

        except Exception as e:
            try:
                logger.error(f"⚠️ Error fetching equipment candidates: {e}")
            except NameError:
                print(f"⚠️ Error fetching equipment candidates: {e}")
            return []

    async def fetch_freight_ride_candidates(
            self,
            domain: str,
            location_query: str = "",
            specific_type: str = "",
            destination_query: str = "",
            operating_region: str = ""
    ) -> List[Dict[str, Any]]:
        """
        Robustly fetches freight and ride candidates from provider_listings.
        Queries verified schema columns only to prevent PostgreSQL 42703 undefined column errors.
        """
        if not self.supabase:
            return []

        vertical = 'freight' if domain.lower() == 'freight' else 'ride'
        search_type = specific_type.strip() if specific_type else None
        loc_clean = location_query.strip() if location_query else ""
        dest_clean = destination_query.strip() if destination_query else ""
        region_clean = operating_region.strip() if operating_region else ""

        try:
            # Build spatial filters across verified location columns
            loc_or_conditions = []
            if loc_clean:
                loc_or_conditions.extend([
                    f"location_text.ilike.%{loc_clean}%",
                    f"operating_region.ilike.%{loc_clean}%"
                ])
            if dest_clean:
                loc_or_conditions.extend([
                    f"destination_text.ilike.%{dest_clean}%",
                    f"operating_region.ilike.%{dest_clean}%"
                ])
            if region_clean and region_clean not in (loc_clean, dest_clean):
                loc_or_conditions.append(f"operating_region.ilike.%{region_clean}%")

            # --- TIER 1: Strict Query (Location + Verified Property Use Column) ---
            query = self.supabase.table('provider_listings') \
                .select('*') \
                .eq('vertical_type', vertical) \
                .eq('status', 'active')

            # Use verified column 'property_use' for asset matching in Supabase
            if search_type and search_type.lower() != 'any':
                query = query.ilike('property_use', f'%{search_type}%')

            if loc_or_conditions:
                query = query.or_(",".join(loc_or_conditions))

            response = await query.execute()
            candidates = response.data if response and response.data else []

            # --- TIER 2: Fallback (Relax Asset Type Filter) ---
            if not candidates and search_type and search_type.lower() != 'any':
                fb_query = self.supabase.table('provider_listings') \
                    .select('*') \
                    .eq('vertical_type', vertical) \
                    .eq('status', 'active')

                if loc_or_conditions:
                    fb_query = fb_query.or_(",".join(loc_or_conditions))

                response = await fb_query.execute()
                candidates = response.data if response.data else []

            # --- TIER 3: Ultimate Fallback (All Active Domain Nodes) ---
            if not candidates:
                ult_query = self.supabase.table('provider_listings') \
                    .select('*') \
                    .eq('vertical_type', vertical) \
                    .eq('status', 'active')

                response = await ult_query.execute()
                candidates = response.data if response.data else []

            # --- 4. SCHEMA ALIGNMENT & DATA UNPACKING ---
            invalid_placeholders = {"", "N/A", "NOT_SPECIFIED", "NONE", "NULL", "UNSPECIFIED", "ANY"}

            for item in candidates:
                item['price'] = float(item.get('price', 0.0))
                lat = item.get('latitude')
                lon = item.get('longitude')
                loc_text = item.get('location_text', '')
                dest_text = item.get('destination_text', '') or ''

                raw_region = item.get('operating_region', '')
                clean_region = str(raw_region).strip()
                item['operating_region'] = '' if clean_region.upper() in invalid_placeholders else clean_region

                amenities = str(item.get('amenities', ''))

                item['vehicle_plate'] = 'N/A'
                if 'Plate:' in amenities:
                    try:
                        item['vehicle_plate'] = amenities.split('Plate:')[1].split('|')[0].strip()
                    except IndexError:
                        pass

                if not dest_text and 'Dest:' in amenities:
                    try:
                        dest_text = amenities.split('Dest:')[1].split('|')[0].strip()
                    except IndexError:
                        pass

                # Resolve asset classification using verified native column
                resolved_type = item.get('property_use') or 'Any'

                if vertical == 'ride':
                    item['departure_lat'] = lat
                    item['departure_lon'] = lon
                    item['departure_text'] = loc_text
                    item['destination_text'] = dest_text
                    item['vehicle_type'] = resolved_type

                    db_seats = item.get('available_seats')
                    if db_seats is not None and int(db_seats) > 0:
                        item['seats_available'] = int(db_seats)
                    elif 'Seats:' in amenities:
                        try:
                            item['seats_available'] = int(amenities.split('Seats:')[1].split('|')[0].strip())
                        except (IndexError, ValueError):
                            item['seats_available'] = 1
                    else:
                        item['seats_available'] = 1

                elif vertical == 'freight':
                    item['origin_lat'] = lat
                    item['origin_lon'] = lon
                    item['origin_text'] = loc_text
                    item['destination_text'] = dest_text
                    item['truck_type'] = resolved_type
                    item['trailer_type'] = resolved_type
                    item['carrier_rating'] = 5.0
                    item['max_legal_speed_mph'] = 55.0
                    item['has_hazmat'] = False

                    db_weight = item.get('max_weight_kgs')
                    if db_weight is not None and float(db_weight) > 0:
                        item['capacity_kgs'] = float(db_weight)
                    elif 'Weight:' in amenities:
                        try:
                            wt_str = amenities.split('Weight:')[1].split('kg')[0].strip()
                            item['capacity_kgs'] = float(wt_str)
                        except (IndexError, ValueError):
                            item['capacity_kgs'] = 0.0
                    else:
                        item['capacity_kgs'] = 0.0

                    if 'Hazmat: Yes' in amenities or item.get('is_hazardous'):
                        item['has_hazmat'] = True

            return candidates

        except Exception as e:
            print(f"⚠️ Error fetching robust {vertical} candidates: {e}")
            return []

    # ==========================================
    # DEAL MANAGEMENT METHODS (DB MANAGER)
    # ==========================================
    async def create_pending_deal(
            self,
            seeker_id: int,
            provider_id: int,
            listing_id: str,
            vertical_type: str = "general",
            current_price: float = 0.0,
            _deal_state: str = "negotiating",
            negotiation_history: list = None,
            asset_title: str = None,
            weight: float = 0.0,
            request_id: Optional[str] = None,  # ✅ Explicitly accept request_id parameter
            **kwargs
    ) -> Optional[str]:
        """
        Initializes a new deal record or seamlessly resumes an existing active/pending deal session
        in progress. Hardened against session contamination, vertical/currency switching vulnerabilities,
        and concurrent race conditions by prioritizing the database as the single source of truth.
        """
        if not self.supabase:
            logger.error("❌ Supabase client unavailable in DatabaseManager.")
            return None

        # 1. Identity Safety Guard: Prevent self-negotiation state corruptions
        if str(seeker_id) == str(provider_id):
            logger.error(f"🚨 Aborted deal creation: Seeker and Provider IDs are identical ({seeker_id}).")
            return None

        history = negotiation_history if negotiation_history is not None else []

        # 2. Resolve request_id, handling alternative or legacy keyword arguments gracefully
        resolved_request_id = (
                request_id
                or kwargs.pop("seeker_request_id", None)
                or kwargs.pop("request_id", None)
        )

        resolved_listing_id = listing_id
        authoritative_vertical = vertical_type
        authoritative_currency = kwargs.get("currency", "ETB")

        # 3. SINGLE SOURCE OF TRUTH ENFORCEMENT & CURRENCY/VERTICAL LOCKDOWN
        try:
            # Check listing ground truth if listing_id provided
            if resolved_listing_id:
                listing_check = await self.supabase.table("provider_listings").select("*").eq("id",
                                                                                              resolved_listing_id).maybe_single().execute()
                if listing_check and listing_check.data:
                    listing_data = listing_check.data
                    provider_id = int(listing_data.get('provider_id', provider_id))
                    authoritative_vertical = str(listing_data.get('vertical_type', vertical_type)).lower().strip()
                    authoritative_currency = str(listing_data.get('currency', authoritative_currency)).upper().strip()
                    if not asset_title:
                        asset_title = listing_data.get('vehicle_type') or listing_data.get(
                            'equipment_type') or listing_data.get('property_use') or listing_data.get('title')

            # Check seeker request ground truth if request_id provided
            if resolved_request_id:
                req_check = await self.supabase.table("seeker_requests").select("*").eq("id",
                                                                                        resolved_request_id).maybe_single().execute()
                if req_check and req_check.data:
                    req_data = req_check.data
                    seeker_id = int(req_data.get('seeker_id', seeker_id))
                    authoritative_vertical = str(req_data.get('vertical_type', authoritative_vertical)).lower().strip()
                    authoritative_currency = str(req_data.get('currency', authoritative_currency)).upper().strip()
                    if not asset_title:
                        asset_title = req_data.get('property_use') or req_data.get('vehicle_type') or req_data.get(
                            'equipment_type')
        except Exception as truth_err:
            logger.warning(f"Database single source of truth lookup warning in create_pending_deal: {truth_err}")

        # 4. ROBUST RESUMPTION PRE-CHECK: Prevent creating new deal if an active/interrupted one exists
        try:
            existing_query = (
                self.supabase.table('deals')
                .select('*')
                .eq('seeker_id', seeker_id)
                .eq('provider_id', provider_id)
            )
            if resolved_listing_id:
                existing_query = existing_query.eq('listing_id', resolved_listing_id)
            elif resolved_request_id:
                existing_query = existing_query.eq('request_id', resolved_request_id)

            existing_res = await existing_query.order('created_at', desc=True).limit(1).maybe_single().execute()
            if existing_res and existing_res.data:
                existing_deal = existing_res.data
                deal_state_val = str(existing_deal.get('deal_state', '')).lower().strip()
                deal_status_val = str(existing_deal.get('status', '')).lower().strip()
                payment_status_val = str(existing_deal.get('payment_status', '')).lower().strip()

                # If deal is already finalized or paid, do not resume as negotiating
                is_locked = (
                        deal_state_val in ['finalized', 'completed', 'paid', 'locked']
                        or deal_status_val in ['finalized', 'completed', 'paid', 'locked']
                        or payment_status_val in ['paid', 'completed']
                )

                if not is_locked:
                    deal_id = existing_deal.get('id')
                    # Resume session: update price/history if new offer provided, otherwise keep existing session intact
                    update_payload = {
                        "current_price": float(current_price or existing_deal.get('current_price', 0.0)),
                        "agreed_price": float(current_price or existing_deal.get('agreed_price', 0.0)),
                        "vertical_type": authoritative_vertical,
                        "currency": authoritative_currency,
                        "updated_at": datetime.utcnow().isoformat()
                    }
                    if asset_title:
                        update_payload["asset_title"] = asset_title
                    if resolved_request_id:
                        update_payload["request_id"] = resolved_request_id

                    await self.supabase.table('deals').update(update_payload).eq('id', deal_id).execute()
                    logger.info(
                        f"🔄 Resumed interrupted active deal session {deal_id} for Seeker {seeker_id} <-> Provider {provider_id}.")
                    return deal_id
        except Exception as resumption_err:
            logger.debug(f"Resumption pre-check lookup failed or skipped: {resumption_err}")

        # 5. Dynamic Payload Construction (Aligned with database schema tables)
        deal_id = str(uuid.uuid4())
        payload = {
            "id": deal_id,
            "seeker_id": int(seeker_id),
            "provider_id": int(provider_id),
            "listing_id": resolved_listing_id,
            "request_id": resolved_request_id,
            "vertical_type": authoritative_vertical,
            "current_price": float(current_price),
            "agreed_price": float(current_price),
            "currency": authoritative_currency,
            "deal_state": "negotiating",
            "status": "pending",
            "negotiation_history": history,
            "asset_title": asset_title or "Matched Asset Listing",
            "weight": float(weight),
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat()
        }

        # Integrate any extra keyword args passed dynamically
        for k, v in kwargs.items():
            if k not in payload and k not in ["seeker_request_id", "currency"]:
                payload[k] = v

        try:
            await self.supabase.table('deals').insert(payload).execute()
            logger.info(
                f"🎯 Successfully created new deal {deal_id} for Seeker {seeker_id} <-> Provider {provider_id} (Request ID: {resolved_request_id})")
            return deal_id
        except Exception as e:
            # Fallback for concurrent race condition handling (unique constraint violation)
            if "unique_active_asset_deal" in str(e) or "23505" in str(e):
                logger.warning(
                    f"⚠️ Concurrent insertion conflict on listing {resolved_listing_id}. Retrieving existing deal...")
                try:
                    fallback_query = (
                        self.supabase.table('deals')
                        .select('id')
                        .eq('seeker_id', seeker_id)
                        .eq('provider_id', provider_id)
                    )
                    if resolved_listing_id:
                        fallback_query = fallback_query.eq('listing_id', resolved_listing_id)

                    fallback_res = await fallback_query.order('created_at', desc=True).limit(1).maybe_single().execute()
                    if fallback_res and fallback_res.data:
                        return fallback_res.data.get('id')
                except Exception as lookup_err:
                    logger.error(f"Failed fallback lookup during insertion conflict: {lookup_err}")
                return None

            logger.error(f"Database insertion failed for new deal pipeline: {e}")
            raise

    async def get_deal_by_id(self, deal_id: str) -> Optional[Dict[str, Any]]:
        """
        Fetches a deal record by its UUID.
        Safely returns the deal dictionary if found, or None if invalid, missing, or errored.
        Robustly handles both list and single-object response formats from Supabase.
        """
        # 1. Guard against empty inputs, invalid string representations, or uninitialized DB client
        if not deal_id or deal_id == "None" or not self.supabase:
            return None

        try:
            # 2. Execute query safely using standard execution
            response = await self.supabase.table('deals').select('*').eq('id', deal_id).execute()

            # 3. Verify response contains data and safely extract the dictionary record
            if response and hasattr(response, 'data') and response.data:
                data = response.data

                # If Supabase returns a list of records
                if isinstance(data, list):
                    return data[0] if len(data) > 0 else None

                # If Supabase returns a single dictionary object directly
                elif isinstance(data, dict):
                    return data

            return None

        except Exception as e:
            logger.error(f"Error retrieving deal record for ID '{deal_id}': {str(e)}")
            return None

    async def update_deal_price(self, deal_id: str, new_price: float) -> bool:
        if not self.supabase:
            return False
        response = await self.supabase.table('deals').update({
            "current_price": float(new_price),
            "updated_at": datetime.utcnow().isoformat()
        }).eq('id', deal_id).execute()
        return len(response.data) > 0

    async def _sync_listing_status_for_deal(self, deal_id: str, deal_record: dict = None) -> None:
        """
        Robust synchronization helper ensuring both provider_listings and seeker_requests
        are updated to 'finalized' upon deal completion.
        """
        try:
            now_iso = datetime.now(timezone.utc).isoformat()

            # 1. Fetch deal record if not provided
            if not deal_record:
                res = await self.supabase.table('deals').select('*').eq('id', deal_id).execute()
                if res and hasattr(res, 'data') and res.data:
                    deal_record = res.data[0]
                else:
                    logger.warning(f"⚠️ Could not fetch deal record for ID {deal_id} during status synchronization.")
                    return

            listing_id = deal_record.get('listing_id')
            request_id = deal_record.get('request_id')
            seeker_id = deal_record.get('seeker_id')
            vertical = str(deal_record.get('vertical_type') or '').lower().strip()

            # 2. Sync Provider Listing Status
            if listing_id:
                if not vertical:
                    listing_res = await self.supabase.table('provider_listings').select('vertical_type').eq('id',
                                                                                                            listing_id).execute()
                    if listing_res and hasattr(listing_res, 'data') and listing_res.data:
                        vertical = str(listing_res.data[0].get('vertical_type', '')).lower().strip()

                FIXED_ASSET_VERTICALS = {'housing', 'house', 'vehicle', 'vehicles', 'equipment', 'equipment_rental'}

                if any(v in vertical for v in FIXED_ASSET_VERTICALS):
                    await self.supabase.table('provider_listings').update({
                        'status': 'finalized',
                        'availability_status': 'reserved',
                        'updated_at': now_iso
                    }).eq('id', listing_id).execute()
                    logger.info(f"✅ Provider listing {listing_id} updated to 'finalized' (reserved).")
                else:
                    logger.info(f"ℹ️ Dynamic service listing {listing_id} retained as 'active'.")

            # 3. Multi-Tier Smart Fallback for Seeker Request Resolution
            if not request_id and seeker_id:
                try:
                    # Tier A: Try finding any unfinalized request for this seeker and vertical
                    query = self.supabase.table('seeker_requests').select('id, status') \
                        .eq('seeker_id', seeker_id)

                    if vertical:
                        query = query.eq('vertical_type', vertical)

                    req_res = await query.order('created_at', desc=True).limit(5).execute()

                    if req_res and req_res.data:
                        # Filter for active/pending/negotiating first
                        active_reqs = [r for r in req_res.data if
                                       r.get('status') in ['active', 'pending', 'negotiating']]
                        target_req = active_reqs[0] if active_reqs else req_res.data[0]

                        request_id = target_req.get('id')
                        logger.info(
                            f"💡 Smart resolved seeker request ID {request_id} for deal {deal_id} (vertical: {vertical})")

                        # Back-populate the deal record for future reference
                        await self.supabase.table('deals').update({'request_id': request_id}).eq('id',
                                                                                                 deal_id).execute()
                except Exception as fb_err:
                    logger.warning(f"Smart fallback seeker request lookup failed for deal {deal_id}: {fb_err}")

            # Tier B Absolute Fallback: If still missing, query the absolute latest request by seeker_id
            if not request_id and seeker_id:
                try:
                    last_res = await self.supabase.table('seeker_requests').select('id') \
                        .eq('seeker_id', seeker_id) \
                        .order('created_at', desc=True) \
                        .limit(1) \
                        .execute()
                    if last_res and last_res.data:
                        request_id = last_res.data[0].get('id')
                        logger.info(
                            f"💡 Absolute fallback resolved seeker request ID {request_id} for seeker {seeker_id}")
                except Exception as abs_err:
                    logger.error(f"Absolute fallback failed: {abs_err}")

            # 4. Sync Seeker Request Status to 'finalized' with Guaranteed Execution
            if request_id:
                update_res = await self.supabase.table('seeker_requests').update({
                    'status': 'finalized',
                    'updated_at': now_iso
                }).eq('id', request_id).execute()

                if update_res and getattr(update_res, 'data', None):
                    logger.info(f"✅ Seeker request {request_id} status successfully updated to 'finalized'.")
                else:
                    logger.warning(
                        f"⚠️ Seeker request {request_id} update executed, but no matching record returned data.")
            else:
                logger.error(
                    f"❌ CRITICAL: Could not resolve any seeker request for deal {deal_id} (Seeker ID: {seeker_id}).")

        except Exception as sync_err:
            logger.error(f"Failed to sync provider listing & seeker request status for deal '{deal_id}': {sync_err}",
                         exc_info=True)

    async def update_deal_status(self, deal_id: str, status: str) -> bool:
        """
        Updates the deal status/state and timestamp.
        Conditionally syncs provider_listings status if status is 'finalized'.
        Returns True if successful, False otherwise.
        """
        # Guard against uninitialized client or invalid IDs
        if not self.supabase or not deal_id or deal_id == "None":
            return False

        try:
            # Force lowercase to match state definitions
            safe_status = status.lower()
            now_iso = datetime.utcnow().isoformat()

            # 🛠️ CRITICAL FIX: Use "deal_state" instead of "status" to match database schema
            response = await self.supabase.table('deals').update({
                "deal_state": safe_status,
                "updated_at": now_iso
            }).eq('id', deal_id).execute()

            # Safely evaluate response.data to prevent AttributeError crashes
            success = False
            deal_record = None
            if response and hasattr(response, 'data') and isinstance(response.data, list) and len(response.data) > 0:
                success = True
                deal_record = response.data[0]

            # 🛠️ CASCADE LISTING STATUS UPDATE ON FINALIZED
            if success and safe_status == 'finalized':
                await self._sync_listing_status_for_deal(deal_id, deal_record)

            return success

        except Exception as e:
            logger.error(f"Failed to update deal '{deal_id}' to status '{status}': {str(e)}")
            # Re-raise so the DealsHandler can catch it and show an error message to the user
            raise

    async def update_negotiation_state(
            self,
            deal_id: str,
            deal_state: str,
            current_price: float = None,
            negotiation_history: list = None
    ) -> bool:
        """
        Updates an active deal's state, current price, and negotiation history in Supabase.
        Conditionally syncs provider_listings status if deal_state is 'finalized'.
        Returns True on success, False on failure.
        """
        if not self.supabase or not deal_id:
            return False

        safe_state = str(deal_state).lower()
        now_iso = datetime.utcnow().isoformat()

        payload = {
            "deal_state": safe_state,
            "updated_at": now_iso
        }

        if current_price is not None:
            payload["current_price"] = current_price

        if negotiation_history is not None:
            payload["negotiation_history"] = negotiation_history

        try:
            response = await self.supabase.table('deals').update(payload).eq('id', deal_id).execute()

            success = False
            deal_record = None
            if response and hasattr(response, 'data') and isinstance(response.data, list) and len(response.data) > 0:
                success = True
                deal_record = response.data[0]

            # 🛠️ CASCADE LISTING STATUS UPDATE ON FINALIZED
            if success and safe_state == 'finalized':
                await self._sync_listing_status_for_deal(deal_id, deal_record)

            return success
        except Exception as e:
            logger.error(f"Failed to update negotiation state for deal '{deal_id}': {str(e)}")
            return False

    # ==========================================
    # GEOSPATIAL TELEMETRY & LIVE LOCATION ENGINES
    # ==========================================

    async def update_asset_location(self, telegram_id: int, current_lat: float, current_lon: float) -> None:
        """
        Updates the live geospatial location of an active mobility user (driver or passenger)
        in both the high-speed Redis cache and the persistent Supabase database,
        populating both scalar coordinates and the PostGIS location_geog WKT column.
        """
        # Changed cache key to generic 'location' since the user could be a provider or seeker
        redis_key = f"location:{telegram_id}"
        geo_payload = f"{current_lat},{current_lon}"

        # Generate Well-Known Text (WKT) point string for PostGIS geography (Longitude first, Latitude second)
        wkt_geography = f"SRID=4326;POINT({float(current_lon)} {float(current_lat)})"

        # 1. High-speed Redis Cache Update
        if self.redis:
            try:
                await self.redis.set(redis_key, geo_payload, ex=43200)  # 12-hour expiration
            except Exception as red:
                self.logger.error(f"Redis high-frequency write failure for telegram_id {telegram_id}: {red}")

        # 2. Persistent Supabase Database Update
        if self.supabase:
            try:
                # Update Active Provider Listings (Drivers / Freight Carriers)
                await self.supabase.table('provider_listings').update({
                    "latitude": float(current_lat),
                    "longitude": float(current_lon),
                    "location_geog": wkt_geography
                }).eq("provider_id", telegram_id) \
                    .in_("vertical_type", ["ride", "freight"]) \
                    .eq("status", "active").execute()

                # Update Active Seeker Requests (Passengers / Cargo Owners)
                await self.supabase.table('seeker_requests').update({
                    "latitude": float(current_lat),
                    "longitude": float(current_lon),
                    "location_geog": wkt_geography
                }).eq("seeker_id", telegram_id) \
                    .in_("vertical_type", ["ride", "freight"]) \
                    .eq("status", "active").execute()

            except Exception as e:
                self.logger.error(f"Supabase location replication failure for telegram_id {telegram_id}: {e}")

    async def get_asset_location(self, telegram_id: int) -> Tuple[Optional[float], Optional[float]]:
        """
        Retrieves the most recent known location of an active mobility user,
        falling back from the Redis cache to the Supabase tables if necessary.
        """
        redis_key = f"location:{telegram_id}"

        # 1. Try High-speed Redis Cache First
        if self.redis:
            try:
                cached_data = await self.redis.get(redis_key)
                if cached_data:
                    lat_str, lon_str = cached_data.decode('utf-8').split(',')
                    return float(lat_str), float(lon_str)
            except Exception as ce:
                self.logger.error(f"Failed to fetch location from Redis cache for {telegram_id}: {ce}")

        # 2. Fallback to Persistent Supabase Database
        if self.supabase:
            try:
                # Check active Provider Listings first
                prov_response = await self.supabase.table('provider_listings') \
                    .select('latitude, longitude') \
                    .eq('provider_id', telegram_id) \
                    .in_('vertical_type', ['ride', 'freight']) \
                    .eq('status', 'active') \
                    .limit(1).maybe_single().execute()

                if prov_response.data and prov_response.data.get("latitude") is not None:
                    lat = float(prov_response.data["latitude"])
                    lon = float(prov_response.data["longitude"])
                    if self.redis:
                        await self.redis.set(redis_key, f"{lat},{lon}", ex=43200)
                    return lat, lon

                # Check active Seeker Requests if not found in Providers
                seek_response = await self.supabase.table('seeker_requests') \
                    .select('latitude, longitude') \
                    .eq('seeker_id', telegram_id) \
                    .in_('vertical_type', ['ride', 'freight']) \
                    .eq('status', 'active') \
                    .limit(1).maybe_single().execute()

                if seek_response.data and seek_response.data.get("latitude") is not None:
                    lat = float(seek_response.data["latitude"])
                    lon = float(seek_response.data["longitude"])
                    if self.redis:
                        await self.redis.set(redis_key, f"{lat},{lon}", ex=43200)
                    return lat, lon

            except Exception as e:
                self.logger.error(f"Supabase location fetch failure for telegram_id {telegram_id}: {e}")

        return None, None

    # ==========================================
    # CANDIDATE MATRIX EXTRACTION ENGINES
    # ==========================================

    async def get_available_candidates(self, category: str) -> List[Dict[str, Any]]:
        if not self.supabase:
            return []

        response = await self.supabase.table('provider_listings') \
            .select('*') \
            .eq('vertical_type', category) \
            .eq('status', 'active') \
            .execute()

        candidates = response.data if response.data else []

        if not candidates or not self.redis:
            return candidates

        try:
            async with self.redis.pipeline(transaction=False) as pipe:
                for candidate in candidates:
                    await pipe.get(f"driver:{candidate['provider_id']}:location")
                cached_locations = await pipe.execute()

            for candidate, loc_data in zip(candidates, cached_locations):
                if loc_data:
                    try:
                        lat_str, lon_str = loc_data.decode('utf-8').split(',')
                        candidate['latitude'] = float(lat_str)
                        candidate['longitude'] = float(lon_str)
                    except (ValueError, AttributeError):
                        pass
            return candidates
        except Exception as e:
            self.logger.error(f"Redis pipeline runtime processing error: {e}")
            return candidates

    async def fetch_proximity_matches(self, lon: float, lat: float, radius_meters: int, asset_category: str) -> List[Dict[str, Any]]:
        """
        API-First approach: Pulls active data, calculates Haversine proximity mathematically
        in-memory to avoid needing custom PostGIS RPC configurations.
        """
        if not self.supabase:
            return []

        try:
            # CORRECTED: Use .not_.filter('column', 'is', 'null') instead of .is_null()
            # The # type: ignore comment below silences PyCharm if it still flags the chained dynamic attributes.
            response = await self.supabase.table('provider_listings') \
                .select('*') \
                .eq('vertical_type', asset_category) \
                .eq('status', 'active') \
                .not_.filter('latitude', 'is', 'null') \
                .execute()  # type: ignore

            all_active = response.data if response.data else []
            valid_candidates = []

            for record in all_active:
                record_lat = record.get('latitude')
                record_lon = record.get('longitude')

                if record_lat is not None and record_lon is not None:
                    # Assumes haversine_distance is imported or defined globally in your module
                    dist = haversine_distance(lat, lon, float(record_lat), float(record_lon))
                    if dist <= radius_meters:
                        record['dist_meters'] = dist
                        valid_candidates.append(record)

            # Sort ascending by distance
            valid_candidates.sort(key=lambda x: x.get('dist_meters', 0))
            return valid_candidates

        except Exception as e:
            self.logger.error(f"Failed to execute proximity lookup: {e}", exc_info=True)
            return []

    # ==========================================
    # SECURE REVENUE GATE & DISPATCH SYSTEMS
    # ==========================================
    def table(self, table_name: str):
        """
        Passthrough method to expose the standard Supabase query builder.
        Note: This is deliberately NOT async so that standard chaining works:
        e.g., await db.table('deals').select('*').execute()
        """
        if not self.supabase:
            raise RuntimeError("Supabase client uninitialized.")
        return self.supabase.table(table_name)

    async def resolve_successful_payment(self, deal_id: str) -> str:
        """
        RECOMMENDED ALTERNATIVE to raw table chaining.
        Production gate checking existing deal states before resolving pipelines.
        Prevents dual matching, accidental duplicate tracking, and double payouts.
        Now automatically resolves null phone numbers and agreed_price upon completion.
        """
        from datetime import datetime

        if not self.supabase:
            self.logger.error("Supabase client uninitialized during payment resolution.")
            return "System Error"

        try:
            # 1. Fetch deal status PLUS the fields needed to resolve the missing data
            deal = await self.supabase.table('deals').select(
                'status', 'payment_status', 'seeker_id', 'provider_id', 'current_price'
            ).eq('id', deal_id).maybe_single().execute()

            if not deal.data:
                return "Deal not found"

            # 2. Idempotency Check: If already paid or finalized, ignore the duplicate webhook entirely.
            current_status = deal.data.get('status')
            payment_status = deal.data.get('payment_status')

            if payment_status == 'paid' or current_status in ['paid', 'finalized']:
                return "Transaction already processed."

            # 3. Fetch phone numbers dynamically from the users table
            seeker_id = deal.data.get('seeker_id')
            provider_id = deal.data.get('provider_id')
            final_price = deal.data.get('current_price')

            seeker_phone = None
            provider_phone = None

            # Fetch Seeker Phone (Note: Change 'id' to 'telegram_id' if that is your primary key)
            if seeker_id:
                s_req = await self.supabase.table('users').select('phone_number').eq('id',
                                                                                     seeker_id).maybe_single().execute()
                if s_req.data:
                    seeker_phone = s_req.data.get('phone_number')

            # Fetch Provider Phone
            if provider_id:
                p_req = await self.supabase.table('users').select('phone_number').eq('id',
                                                                                     provider_id).maybe_single().execute()
                if p_req.data:
                    provider_phone = p_req.data.get('phone_number')

            # 4. Safely transition state AND populate the missing columns in one call
            # DO NOT include 'dispatch_sent': True here. The notification sender handles that lock.
            await self.supabase.table('deals').update({
                'status': 'finalized',
                'payment_status': 'paid',
                'deal_state': 'completed',
                'seeker_phone': seeker_phone,
                'provider_phone': provider_phone,
                'agreed_price': final_price,
                'updated_at': datetime.utcnow().isoformat()
            }).eq('id', deal_id).execute()

            return "Proceed"

        except Exception as e:
            self.logger.error(f"Idempotency and payment resolution failed for {deal_id}: {e}", exc_info=True)
            return "System Error"

    async def create_deal_with_commission(self, seeker_id: int, provider_id: int, seeker_phone: str,
                                          provider_phone: str,
                                          agreed_amount: float, commission_amount: float, domain: str,
                                          target_listing_id: Optional[int] = None, currency_code: str = "ETB") -> str:
        if not self.supabase:
            raise RuntimeError("Supabase client uninitialized.")

        try:
            # Sequential execution mapping
            deal_payload = {
                "seeker_id": seeker_id,
                "provider_id": provider_id,
                "seeker_phone": seeker_phone,
                "provider_phone": provider_phone,
                "agreed_amount": agreed_amount,
                "commission_amount": commission_amount,
                "payment_status": "pending",
                "domain": domain,
                "target_listing_id": target_listing_id
            }

            deal_resp = await self.supabase.table('deals').insert(deal_payload).execute()
            deal_id = deal_resp.data[0]['id']

            comm_payload = {
                "deal_id": deal_id,
                "deal_type": domain,
                "gross_amount": agreed_amount,
                "commission_fee": commission_amount,
                "currency_code": currency_code
            }

            await self.supabase.table('commissions').insert(comm_payload).execute()
            return str(deal_id)

        except Exception as e:
            self.logger.error(f"Transaction failure for deal/commission cascade: {e}")
            raise e

    async def verify_payment_status(self, deal_id: str) -> bool:
        """
        Verifies payment status and automatically backfills any null
        seeker_phone, provider_phone, or agreed_price fields for paid deals.
        """
        if not self.supabase:
            return False

        try:
            # Fetch payment status along with fields needed for null resolution
            response = await self.supabase.table('deals').select(
                'payment_status', 'seeker_id', 'provider_id', 'current_price',
                'seeker_phone', 'provider_phone', 'agreed_price'
            ).eq('id', deal_id).maybe_single().execute()

            if not response.data:
                return False

            deal_data = response.data
            is_paid = deal_data.get('payment_status') == 'paid'

            # Self-healing check: if paid but any key fields are null, resolve and update them
            if is_paid:
                updates = {}

                if not deal_data.get('seeker_phone') and deal_data.get('seeker_id'):
                    s_req = await self.supabase.table('users').select('phone_number').eq('id', deal_data.get(
                        'seeker_id')).maybe_single().execute()
                    if s_req.data and s_req.data.get('phone_number'):
                        updates['seeker_phone'] = s_req.data.get('phone_number')

                if not deal_data.get('provider_phone') and deal_data.get('provider_id'):
                    p_req = await self.supabase.table('users').select('phone_number').eq('id', deal_data.get(
                        'provider_id')).maybe_single().execute()
                    if p_req.data and p_req.data.get('phone_number'):
                        updates['provider_phone'] = p_req.data.get('phone_number')

                if deal_data.get('agreed_price') is None and deal_data.get('current_price') is not None:
                    updates['agreed_price'] = deal_data.get('current_price')

                if updates:
                    await self.supabase.table('deals').update(updates).eq('id', deal_id).execute()

            return is_paid

        except Exception as e:
            if hasattr(self, 'logger') and self.logger:
                self.logger.error(f"Failed to verify payment status and backfill deal {deal_id}: {e}", exc_info=True)
            return False

    async def get_contact_pair_for_dispatch(self, deal_id: str) -> Tuple[int, int, str, str]:
        """
        Retrieves contact pair for dispatch with corrected lookup keys
        matching the public.users schema (telegram_id and phone_number).
        """
        if not self.supabase:
            raise RuntimeError("Supabase client uninitialized.")

        # 1. Fetch deal data
        response = await self.supabase.table('deals').select(
            'seeker_id, provider_id, seeker_phone, provider_phone, payment_status, status'
        ).eq('id', deal_id).maybe_single().execute()

        row = response.data
        if not row:
            raise ValueError(f"Deal lookup failed: {deal_id}")

        seeker_id = row.get('seeker_id')
        provider_id = row.get('provider_id')
        seeker_phone = row.get('seeker_phone')
        provider_phone = row.get('provider_phone')

        # 2. Fallback: Fetch from 'public.users' using 'telegram_id' and 'phone_number'
        if not seeker_phone and seeker_id:
            user_seeker = await self.supabase.table('users').select('phone_number').eq('telegram_id',
                                                                                       seeker_id).maybe_single().execute()
            seeker_phone = user_seeker.data.get('phone_number') if user_seeker.data else None

        if not provider_phone and provider_id:
            user_provider = await self.supabase.table('users').select('phone_number').eq('telegram_id',
                                                                                         provider_id).maybe_single().execute()
            provider_phone = user_provider.data.get('phone_number') if user_provider.data else None

        # 3. CRITICAL: Validate that we have valid numbers before dispatching
        if not seeker_phone or not provider_phone:
            raise ValueError(
                f"Dispatch blocked: Missing contact phone for Deal {deal_id}. (Seeker: {seeker_phone}, Provider: {provider_phone})")

        # 4. Validate condition: Payment must be 'paid' OR deal must be 'finalized'
        is_paid = row.get('payment_status') == 'paid'
        is_finalized = row.get('status') == 'finalized'

        if not (is_paid or is_finalized):
            raise PermissionError("Revenue Protection Guard: Dispatch blocked until payment cleared.")

        return (seeker_id, provider_id, seeker_phone, provider_phone)

    async def mark_dispatch_complete(self, deal_id: str) -> None:
        if not self.supabase:
            return

        await self.supabase.table('deals').update({
            "dispatch_sent": True,
            "updated_at": datetime.utcnow().isoformat()
        }).eq('id', deal_id).execute()

    # ==========================================
    # CLOUD STORAGE MAPPING
    # ==========================================

    async def update_asset_image_url(self, asset_id: int, image_url: str) -> None:
        if not self.supabase:
            return
        await self.supabase.table('assets').update({
            "image_url": image_url,
            "updated_at": datetime.utcnow().isoformat()
        }).eq('id', asset_id).execute()

    async def soft_delete_asset(self, asset_id: int) -> None:
        if not self.supabase:
            return
        await self.supabase.table('assets').update({
            "status": 'archived',
            "deleted_at": datetime.utcnow().isoformat()
        }).eq('id', asset_id).execute()


# Global Instance
db_manager = DatabaseManager()
