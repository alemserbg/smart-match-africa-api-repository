import asyncio
import datetime
import json
import logging
import math
import socket
import urllib.parse
import re
from typing import Any, Dict, Tuple, Optional
import requests

logger = logging.getLogger(__name__)

# Import Settings safely
try:
    from src.smartmatch.config import settings
except ImportError:
    class FallbackSettings:
        REDIS_URL: Optional[str] = None


    settings = FallbackSettings()

# ==========================================
# 1. REDIS / VALKEY ASYNCHRONOUS CLIENT SETUP
# ==========================================
redis_client: Optional[Any] = None

try:
    import redis.asyncio as redis
    from redis.exceptions import ConnectionError as RedisConnectionError, TimeoutError as RedisTimeoutError

    HAS_REDIS = True
except ImportError:
    redis = None
    HAS_REDIS = False
    RedisConnectionError = Exception
    RedisTimeoutError = Exception
    logger.warning("redis package not installed. OSRM responses will not be cached.")


async def init_redis_cache(redis_url: Optional[str] = None, max_attempts: int = 30, delay: int = 10) -> None:
    """Initializes global asynchronous Redis/Valkey client with retry logic for cold-starts/DNS."""
    global redis_client
    if not HAS_REDIS:
        return

    url = redis_url or getattr(settings, 'REDIS_URL', None)
    if not url:
        logger.warning("No Redis/Valkey URL provided. Cache will be disabled.")
        return

    try:
        if redis_client is None:
            logger.info("Initializing connection parameters for OSRM Redis cache...")
            redis_client = redis.from_url(
                url,
                decode_responses=True,
                socket_connect_timeout=30,
                socket_timeout=30
            )

        attempt = 0
        while attempt < max_attempts:
            attempt += 1
            try:
                await redis_client.ping()
                logger.info("✅ Redis/Valkey cache client initialized successfully for OSRM routing.")
                return
            except (RedisConnectionError, RedisTimeoutError,
                    asyncio.TimeoutError, OSError, socket.gaierror) as conn_err:
                if attempt >= max_attempts:
                    logger.critical(
                        f"❌ Redis/Valkey service failed to wake up or resolve after {max_attempts * delay} seconds.")
                    redis_client = None
                    raise conn_err

                logger.info(
                    f"⏳ Waiting for Redis/Valkey DNS/VM to provision... "
                    f"(Attempt {attempt}/{max_attempts}). Retrying in {delay}s..."
                )
                await asyncio.sleep(delay)

    except Exception as e:
        logger.critical(f"❌ Critical failure initializing Redis client in reserve__quantity_location.py: {e}",
                        exc_info=True)
        redis_client = None

# Sentinels used to filter out raw database/user placeholders
INVALID_SENTINELS = {
    "none", "null", "not specified", "hin ibsamne", "አልተገለጸም", "", "n/a", "na", "undefined",
    "unknown", "nan", "not_specified", "0", "0.0"
}


def _is_valid_coordinate(lat: Any, lon: Any) -> bool:
    """Validates if lat/lon are valid non-zero floating point coordinates."""
    if lat is None or lon is None:
        return False
    try:
        latitude = float(lat)
        longitude = float(lon)
        return (
            -90.0 <= latitude <= 90.0
            and -180.0 <= longitude <= 180.0
            and (latitude != 0.0 or longitude != 0.0)
        )
    except (ValueError, TypeError):
        return False


def _clean_location_string(val: Optional[Any]) -> Optional[str]:
    """Helper to clean and filter sentinel values from location fields."""
    if val is None:
        return None
    cleaned = str(val).strip()
    if cleaned.lower() in INVALID_SENTINELS:
        return None
    return cleaned


def _extract_location_data(
        data: Optional[Dict[str, Any]],
        is_provider: bool = True
) -> Tuple[Optional[float], Optional[float], Optional[str], Optional[str]]:
    """
    Extracts lat, lon, location_text, and operating_region from a source dict.
    Supports provider_listings, seeker_requests, and deal column variants.
    """
    if not isinstance(data, dict):
        return None, None, None, None

    lat, lon, text, region = None, None, None, None

    # 1. Coordinate extraction
    coord_raw = (
        data.get("coordinates")
        or data.get("gps_location")
        or data.get("location_coords")
    )

    if isinstance(coord_raw, (list, tuple)) and len(coord_raw) == 2:
        lat, lon = coord_raw[0], coord_raw[1]
    elif isinstance(coord_raw, dict):
        lat = coord_raw.get("lat") or coord_raw.get("latitude")
        lon = coord_raw.get("lon") or coord_raw.get("lng") or coord_raw.get("longitude")
    elif isinstance(coord_raw, str) and "," in coord_raw:
        parts = coord_raw.split(",")
        if len(parts) == 2:
            lat, lon = parts[0].strip(), parts[1].strip()

    # Fallback to individual keys
    if not _is_valid_coordinate(lat, lon):
        if is_provider:
            lat = data.get("latitude") or data.get("lat") or data.get("location_lat") or data.get("provider_lat")
            lon = data.get("longitude") or data.get("lon") or data.get("location_lon") or data.get("provider_lon")
        else:
            lat = (
                data.get("latitude")
                or data.get("lat")
                or data.get("destination_lat")
                or data.get("pickup_lat")
                or data.get("seeker_lat")
            )
            lon = (
                data.get("longitude")
                or data.get("lon")
                or data.get("destination_lon")
                or data.get("pickup_lon")
                or data.get("seeker_lon")
            )

    if not _is_valid_coordinate(lat, lon):
        lat, lon = None, None

    # 2. Location Text extraction
    if is_provider:
        raw_text = (
            data.get("location_text")
            or data.get("location")
            or data.get("site_location")
            or data.get("equipment_location")
            or data.get("origin")
            or data.get("address")
        )
    else:
        raw_text = (
            data.get("location_text")
            or data.get("location")
            or data.get("destination")
            or data.get("dropoff_location")
            or data.get("pickup_location")
            or data.get("work_site")
            or data.get("origin")
            or data.get("address")
        )

    # 3. Operating Region extraction
    raw_region = (
        data.get("operating_region")
        or data.get("region")
        or data.get("city")
        or data.get("zone")
        or data.get("woreda")
    )

    text = _clean_location_string(raw_text)
    region = _clean_location_string(raw_region)

    return lat, lon, text, region


class ReservationHelper:
    """Helper module to handle:

    1. Bulk quantity reservations and 48-hour expiration rollbacks.
    2. Dynamic time-based reservations for Ride/Freight (ETA + Delivery time).
    3. Google Maps link generation for physical rendezvous.
    4. Post-payment reservation confirmations.
    """

    # ==========================================
    # 1. FIXED ASSETS: HYBRID QUANTITY MAPPING & 48H EXPIRATION
    # ==========================================
    @staticmethod
    async def reserve_fixed_asset_quantity(
            supabase: Any, listing_id: str, deal_id: str, requested_qty: int = 1
    ) -> None:
        """Deducts requested quantity by mapping bulk_quantity and available_quantity together.

        Called during deal acceptance.
        """
        try:
            res = await supabase.table('provider_listings').select(
                'bulk_quantity, available_quantity, status'
            ).eq('id', listing_id).single().execute()

            listing = res.data
            if not listing:
                return

            current_qty = int(
                listing.get('bulk_quantity')
                if listing.get('bulk_quantity') is not None
                else listing.get('available_quantity', 1)
            )
            new_qty = max(0, current_qty - requested_qty)

            qty_field = (
                'bulk_quantity'
                if 'bulk_quantity' in listing and listing.get('bulk_quantity') is not None
                else 'available_quantity'
            )

            update_payload = {
                qty_field: new_qty,
                'updated_at': datetime.datetime.utcnow().isoformat()
            }

            if new_qty == 0:
                update_payload['status'] = 'reserved'

            await supabase.table('provider_listings').update(update_payload).eq('id', listing_id).execute()
            await supabase.table('deals').update({'requested_quantity': requested_qty}).eq('id', deal_id).execute()

            logger.info(
                f"Reserved {requested_qty} units for listing {listing_id} using '{qty_field}'. Remaining: {new_qty}."
            )

        except Exception as e:
            logger.error(f"Error reserving fixed asset quantity: {e}")

    @staticmethod
    async def rollback_expired_reservations(supabase: Any) -> int:
        """Finds deals stuck in 'pending_payment' for > 48 hours.

        Reverts deal status, cancels reservation status, and restores inventory on provider listings.
        """
        try:
            threshold_time = (datetime.datetime.utcnow() - datetime.timedelta(hours=48)).isoformat()

            res = await supabase.table('deals').select(
                'id, listing_id, requested_quantity, vertical_type'
            ).eq('deal_state', 'pending_payment').lt('updated_at', threshold_time).execute()

            expired_deals = res.data or []
            rolled_back_count = 0

            for deal in expired_deals:
                deal_id = deal['id']
                listing_id = deal.get('listing_id')
                qty_to_restore = int(deal.get('requested_quantity') or 1)

                # Aligned update including reservation_status
                await supabase.table('deals').update({
                    'deal_state': 'expired',
                    'status': 'cancelled_unpaid',
                    'reservation_status': 'cancelled',  # <-- Essential for constraint alignment
                    'updated_at': datetime.datetime.utcnow().isoformat()
                }).eq('id', deal_id).execute()

                if listing_id and deal.get('vertical_type') in ['housing', 'vehicle', 'equipment']:
                    l_res = await supabase.table('provider_listings').select(
                        'bulk_quantity, available_quantity, status'
                    ).eq('id', listing_id).single().execute()

                    if l_res.data:
                        listing_data = l_res.data
                        current_qty = int(
                            listing_data.get('bulk_quantity')
                            if listing_data.get('bulk_quantity') is not None
                            else listing_data.get('available_quantity', 0)
                        )
                        new_qty = current_qty + qty_to_restore

                        qty_field = (
                            'bulk_quantity'
                            if 'bulk_quantity' in listing_data and listing_data.get('bulk_quantity') is not None
                            else 'available_quantity'
                        )

                        l_payload = {
                            qty_field: new_qty,
                            'updated_at': datetime.datetime.utcnow().isoformat()
                        }

                        if listing_data.get('status') == 'reserved':
                            l_payload['status'] = 'active'

                        await supabase.table('provider_listings').update(l_payload).eq('id', listing_id).execute()

                rolled_back_count += 1

            logger.info(f"Rolled back {rolled_back_count} expired 48h reservations.")
            return rolled_back_count

        except Exception as e:
            logger.error(f"Error executing 48h expiration rollback: {e}")
            return 0

    # ==========================================
    # 2. LOGISTICS: CAPACITY & TIME-BASED RESERVATION
    # ==========================================
    @staticmethod
    async def process_logistics_reservation(
            supabase: Any, deal_data: Dict[str, Any], listing_data: Dict[str, Any]
    ) -> None:
        """
        Checks if Ride or Freight is fully occupied or capacity threshold is met.
        Calculates ETA + Delivery Time with robust OSRM fallback & speed-based distance estimation,
        and marks the listing as 'reserved_in_transit' with schema cache fallback protection.
        """
        vertical = str(deal_data.get('vertical_type') or listing_data.get('vertical_type') or '').lower()
        if vertical not in ['ride', 'freight', 'cargo', 'transport']:
            return

        listing_id = deal_data.get('listing_id') or listing_data.get('id')
        if not listing_id:
            return

        is_full = False

        # Check Ride Capacity
        if vertical == 'ride':
            party_size = int(float(deal_data.get('party_size') or 1))
            available_seats = int(
                float(
                    listing_data.get('available_seats')
                    or listing_data.get('seats_available')
                    or listing_data.get('seats')
                    or 1
                )
            )
            if party_size >= available_seats:
                is_full = True

        # Check Freight Capacity
        elif vertical in ['freight', 'cargo', 'transport']:
            cargo_weight = float(
                deal_data.get('cargo_weight_kgs')
                or deal_data.get('weight_kgs')
                or deal_data.get('weight')
                or 0
            )
            max_weight = float(
                listing_data.get('max_weight_kgs')
                or listing_data.get('capacity_kgs')
                or listing_data.get('cargo_weight_kgs')
                or 0
            )
            if cargo_weight > 0 and max_weight > 0 and (cargo_weight >= max_weight * 0.95):
                is_full = True
            elif cargo_weight > 0 and max_weight == 0:
                # If listing capacity isn't strictly set, default full on substantial cargo
                is_full = True
            else:
                # Fallback if neither weight is explicitly zero
                is_full = True

        if is_full:
            # Extract coordinates with robust fallback across deal, listing, and origin/destination text
            o_lat = deal_data.get('origin_lat') or listing_data.get('latitude')
            o_lon = deal_data.get('origin_lon') or listing_data.get('longitude')
            d_lat = deal_data.get('destination_lat') or listing_data.get('destination_lat')
            d_lon = deal_data.get('destination_lon') or listing_data.get('destination_lon')

            total_minutes = 120  # Fallback default (2 hours)

            max_speed = float(listing_data.get('max_legal_speed_mph') or listing_data.get('max_speed_mph') or 55.0)

            if o_lat is not None and o_lon is not None and d_lat is not None and d_lon is not None:
                try:
                    lat1, lon1, lat2, lon2 = float(o_lat), float(o_lon), float(d_lat), float(d_lon)
                    if not (lat1 == 0.0 and lon1 == 0.0) and not (lat2 == 0.0 and lon2 == 0.0):
                        eta_data = await ReservationHelper._calculate_osrm_duration(
                            lat1, lon1, lat2, lon2, vertical, max_speed
                        )
                        if eta_data.get("status") == "Success":
                            total_minutes = int(eta_data.get("total_travel_minutes", 120))
                except (ValueError, TypeError) as coord_err:
                    logger.debug(f"Coordinate conversion error during logistics reservation: {coord_err}")

            reserved_until = datetime.datetime.utcnow() + datetime.timedelta(minutes=total_minutes)

            # Update Listing payload with Schema Cache Failure Fallback Guard (PGRST204)
            update_payload = {
                'status': 'reserved_in_transit',
                'reserved_until': reserved_until.isoformat(),
                'updated_at': datetime.datetime.utcnow().isoformat()
            }

            try:
                await supabase.table('provider_listings').update(update_payload).eq('id', listing_id).execute()
            except Exception as err:
                err_msg = getattr(err, 'message', str(err))
                err_code = getattr(err, 'code', '')
                if 'reserved_until' in err_msg or err_code == 'PGRST204':
                    logger.warning(
                        f"PostgREST schema cache missing 'reserved_until'. Retrying update without timestamp: {err}"
                    )
                    update_payload.pop('reserved_until', None)
                    await supabase.table('provider_listings').update(update_payload).eq('id', listing_id).execute()
                else:
                    raise err

            # Record total calculated transit duration on deal record with PGRST204 schema guard
            deal_id = deal_data.get('id')
            if deal_id:
                try:
                    await supabase.table('deals').update({
                        'transit_duration_minutes': total_minutes,
                        'updated_at': datetime.datetime.utcnow().isoformat()
                    }).eq('id', deal_id).execute()
                except Exception as deal_err:
                    err_msg = getattr(deal_err, 'message', str(deal_err))
                    err_code = getattr(deal_err, 'code', '')
                    if 'transit_duration_minutes' in err_msg or err_code == 'PGRST204':
                        logger.warning(
                            f"PostgREST schema cache missing 'transit_duration_minutes'. Skipping column update: {deal_err}"
                        )
                    else:
                        logger.error(f"Failed to record transit duration on deal {deal_id}: {deal_err}")

            logger.info(
                f"Logistics listing {listing_id} fully loaded. "
                f"Reserved for {total_minutes} mins (Until {reserved_until})."
            )

    @staticmethod
    async def confirm_reservation(supabase: Any, deal_id: str) -> None:
        """
        Confirms asset reservation upon successful payment completion.
        Updates deal reservation status and triggers inventory or logistics status updates.
        """
        try:
            res = await supabase.table('deals').select('*').eq('id', deal_id).single().execute()
            deal_data = res.data if res else None
            if not deal_data:
                logger.warning(f"No deal record found for ID {deal_id} during confirm_reservation.")
                return

            listing_id = deal_data.get('listing_id')
            vertical = str(deal_data.get('vertical_type', '')).lower()
            requested_qty = int(deal_data.get('requested_quantity') or 1)

            # Mark deal as finalized and confirmed
            await supabase.table('deals').update({
                'deal_state': 'finalized',
                'reservation_status': 'confirmed',
                'updated_at': datetime.datetime.utcnow().isoformat()
            }).eq('id', deal_id).execute()

            if not listing_id:
                return

            l_res = await supabase.table('provider_listings').select('*').eq('id', listing_id).single().execute()
            listing_data = l_res.data if l_res else {}

            # Execute logistics transit calculations or fixed asset quantity deductions
            if vertical in ['ride', 'freight', 'cargo', 'transport']:
                await ReservationHelper.process_logistics_reservation(
                    supabase, deal_data, listing_data
                )
            else:
                await ReservationHelper.reserve_fixed_asset_quantity(
                    supabase, listing_id, deal_id, requested_qty
                )

            logger.info(f"✅ Reservation confirmed for deal {deal_id} and listing {listing_id}.")

        except Exception as e:
            logger.error(f"Error executing confirm_reservation for deal {deal_id}: {e}")

    @staticmethod
    async def _calculate_osrm_duration(
            start_lat: float, start_lon: float, end_lat: float, end_lon: float, vertical: str,
            max_truck_speed_mph: float = 55.0
    ) -> Dict[str, Any]:
        """
        Calculates transit duration via OSRM with lightning-fast Redis/Valkey caching,
        HOS rules, 2.5s timeout, negative/fallback caching, and robust mathematical
        Haversine fallback if OSRM times out or fails.
        """
        is_freight = vertical in ['freight', 'cargo', 'transport']
        cache_key = (
            f"osrm:freight:v2:{start_lat}:{start_lon}:{end_lat}:{end_lon}:{max_truck_speed_mph}"
            if is_freight
            else f"osrm:ride:v2:{start_lat}:{start_lon}:{end_lat}:{end_lon}"
        )

        client = globals().get('redis_client') or (redis_client if 'redis_client' in globals() else None)
        has_redis_active = HAS_REDIS if 'HAS_REDIS' in globals() else (client is not None)

        # 1. Redis/Valkey Cache Lookup
        if has_redis_active and client:
            try:
                cached_data = await client.get(cache_key)
                if cached_data:
                    return json.loads(cached_data)
            except Exception as cache_err:
                logger.debug(f"Cache read failed for OSRM route: {cache_err}")

        # 2. Validate coordinates before making outbound HTTP calls
        try:
            lat1_f, lon1_f, lat2_f, lon2_f = float(start_lat), float(start_lon), float(end_lat), float(end_lon)
            has_valid_coords = not ((lat1_f == 0.0 and lon1_f == 0.0) or (lat2_f == 0.0 and lon2_f == 0.0))
        except (ValueError, TypeError):
            has_valid_coords = False

        success_data = None
        if has_valid_coords:
            coordinates = f"{start_lon},{start_lat};{end_lon},{end_lat}"
            url = f"http://project-osrm.org/route/v1/driving/{coordinates}?overview=false"

            def _fetch():
                return requests.get(url, timeout=2.5)  # Fast 2.5s timeout to prevent thread hanging

            try:
                response = await asyncio.to_thread(_fetch)
                if response.status_code == 200:
                    data = response.json()
                    if data.get("code") == "Ok" and data.get("routes"):
                        success_data = data["routes"][0]
                else:
                    logger.debug(f"OSRM HTTP status {response.status_code} for route. Using Haversine fallback.")
            except Exception as err:
                logger.debug(f"OSRM API request timed out or failed ({err}). Engaging robust distance fallback.")

        # 3. Process Route Data or Compute Haversine Mathematical Fallback
        if success_data:
            raw_car_seconds = success_data["duration"]
            distance_miles = success_data["distance"] * 0.000621371
        else:
            # Haversine Formula Fallback for 100% uptime when OSRM is delayed/down or coords are invalid
            try:
                r1, l1, r2, l2 = map(math.radians, [float(start_lat), float(start_lon), float(end_lat), float(end_lon)])
                dlat = r2 - r1
                dlon = l2 - l1
                a = math.sin(dlat / 2) ** 2 + math.cos(r1) * math.cos(r2) * math.sin(dlon / 2) ** 2
                c = 2 * math.asin(math.sqrt(a))
                distance_miles = 3958.8 * c  # Earth radius in miles
            except (ValueError, TypeError):
                distance_miles = 50.0  # Default fallback distance in miles

            avg_fallback_speed = max_truck_speed_mph if is_freight else 40.0
            raw_car_seconds = (distance_miles / avg_fallback_speed) * 3600

        if is_freight:
            avg_car_speed_mph = (distance_miles / (raw_car_seconds / 3600)) if raw_car_seconds > 0 else 1.0
            if avg_car_speed_mph > max_truck_speed_mph:
                freight_hours = distance_miles / max_truck_speed_mph
            else:
                freight_hours = (raw_car_seconds / 3600) * 1.15

            hos_break_minutes = math.floor(freight_hours / 8) * 30 if freight_hours > 8 else 0
            total_minutes = round((freight_hours * 60) + hos_break_minutes + 45)  # 45 min loading buffer
        else:
            total_minutes = round((raw_car_seconds / 60) + 15)  # 15 min pickup buffer

        result = {
            "status": "Success",
            "total_travel_minutes": total_minutes
        }

        # 4. Cache Store (24h TTL for success, 1h TTL for fallback/negative results)
        if has_redis_active and client:
            try:
                ttl = 86400 if success_data else 3600
                await client.setex(cache_key, ttl, json.dumps(result))
            except Exception as cache_err:
                logger.debug(f"Cache write failed for OSRM route: {cache_err}")

        return result

    @staticmethod
    def generate_map_link(
            lat: Optional[Any],
            lon: Optional[Any],
            location_text: Optional[str],
            operating_region: Optional[str] = None,
            country: Optional[str] = None,
    ) -> str:
        """
        Generates a clickable Google Maps URL using valid GPS coordinates if available,
        or falls back to an intelligently structured query supporting any location worldwide.
        Guarantees 'https://www.google.com/maps?q=...' format to prevent 404 errors.
        """
        # 1. Coordinate-based link (ignoring 0.0, 0.0 Null Island coordinates)
        if _is_valid_coordinate(lat, lon):
            try:
                latitude = float(lat)
                longitude = float(lon)
                return f"https://www.google.com/maps?q={latitude:.6f},{longitude:.6f}"
            except (ValueError, TypeError):
                pass

        # 2. Text fallback with global administrative and route cleaning
        cleaned_text = _clean_location_string(location_text)
        if cleaned_text:
            # Strip prefixes safely
            cleaned_text = re.sub(r"^(?:Fe'umsaa|ጭነት|Freight)\s*-\s*", "", cleaned_text,
                                  flags=re.IGNORECASE).strip()

            # Clean transit route delimiters (e.g., "Ambo ➔ Addis Ababa" -> "Ambo")
            for delimiter in [" ➔ ", " -> ", " - ", " to ", " እስከ "]:
                if delimiter in cleaned_text:
                    cleaned_text = cleaned_text.split(delimiter, 1)[0].strip()
                    break

            query_tokens = [cleaned_text]

            # Append operating region if distinct
            clean_region = _clean_location_string(operating_region)
            if clean_region and clean_region.lower() not in cleaned_text.lower():
                query_tokens.append(clean_region)

            # Append country context globally if provided and missing
            clean_country = _clean_location_string(country)
            if clean_country and clean_country.lower() not in cleaned_text.lower():
                query_tokens.append(clean_country)

            search_query = ", ".join(query_tokens)
            encoded_query = urllib.parse.quote_plus(search_query)

            return f"https://www.google.com/maps?q={encoded_query}"

        return ""

    @classmethod
    def generate_party_map_links(
            cls,
            listing: Optional[Dict[str, Any]] = None,
            request_data: Optional[Dict[str, Any]] = None,
            deal: Optional[Dict[str, Any]] = None,
            context_user_data: Optional[Dict[str, Any]] = None,
            default_country: Optional[str] = None
    ) -> Dict[str, str]:
        """
        Generates distinct, correctly aligned Google Maps links using context_user_data first,
        falling back to entity payload data (listing/request_data/deal):
        - provider_map_url: Provider/Asset location link (linked to Seeker Payment Confirmation card).
        - seeker_map_url: Seeker/Client location link (linked to Provider Match Finalized card).
        """
        context_user_data = context_user_data or {}
        listing = listing or {}
        request_data = request_data or {}
        deal = deal or {}

        # --- Extract Provider / Asset Location ---
        p_lat, p_lon, p_text, p_region = _extract_location_data(
            context_user_data.get("provider_location") or context_user_data.get("provider"),
            is_provider=True
        )

        if not (_is_valid_coordinate(p_lat, p_lon) or p_text):
            l_lat, l_lon, l_text, l_region = _extract_location_data(listing, is_provider=True)
            d_lat, d_lon, d_text, d_region = _extract_location_data(deal, is_provider=True)

            p_lat = p_lat if _is_valid_coordinate(p_lat, p_lon) else (
                l_lat if _is_valid_coordinate(l_lat, l_lon) else d_lat)
            p_lon = p_lon if _is_valid_coordinate(p_lat, p_lon) else (
                l_lon if _is_valid_coordinate(l_lat, l_lon) else d_lon)
            p_text = _clean_location_string(p_text) or _clean_location_string(l_text) or _clean_location_string(
                d_text)
            p_region = _clean_location_string(p_region) or _clean_location_string(
                l_region) or _clean_location_string(
                d_region)

        # --- Extract Seeker / Client Location ---
        s_lat, s_lon, s_text, s_region = _extract_location_data(
            context_user_data.get("seeker_location") or context_user_data.get("seeker"),
            is_provider=False
        )

        if not (_is_valid_coordinate(s_lat, s_lon) or s_text):
            r_lat, r_lon, r_text, r_region = _extract_location_data(request_data, is_provider=False)
            d_lat, d_lon, d_text, d_region = _extract_location_data(deal, is_provider=False)

            s_lat = s_lat if _is_valid_coordinate(s_lat, s_lon) else (
                r_lat if _is_valid_coordinate(r_lat, r_lon) else d_lat)
            s_lon = s_lon if _is_valid_coordinate(s_lat, s_lon) else (
                r_lon if _is_valid_coordinate(r_lon, r_lon) else d_lon)
            s_text = _clean_location_string(s_text) or _clean_location_string(r_text) or _clean_location_string(
                d_text)
            s_region = _clean_location_string(s_region) or _clean_location_string(
                r_region) or _clean_location_string(
                d_region)

        # --- Dynamic Country Resolution (Record/Context Priority, No Hardcoded Fallback) ---
        provider_country = (
                listing.get("country")
                or deal.get("provider_country")
                or deal.get("country")
                or context_user_data.get("provider_country")
                or context_user_data.get("country")
                or default_country
        )

        seeker_country = (
                request_data.get("country")
                or deal.get("seeker_country")
                or deal.get("country")
                or context_user_data.get("seeker_country")
                or context_user_data.get("country")
                or default_country
        )

        # Generate party-specific links
        provider_map_url = cls.generate_map_link(
            p_lat, p_lon, p_text, operating_region=p_region, country=provider_country
        )
        seeker_map_url = cls.generate_map_link(
            s_lat, s_lon, s_text, operating_region=s_region, country=seeker_country
        )

        return {
            "provider_map_url": provider_map_url,  # Seeker Payment Confirmation Card
            "seeker_map_url": seeker_map_url,  # Provider Match Finalized Card
            "dispatch_map_url": provider_map_url  # Fallback reference
        }


class LocationService:

    @staticmethod
    def generate_map_link(
            lat: Optional[Any],
            lon: Optional[Any],
            location_text: Optional[str],
            operating_region: Optional[str] = None,
            country: Optional[str] = None,
    ) -> str:
        """Delegates to ReservationHelper for centralized link generation."""
        return ReservationHelper.generate_map_link(
            lat,
            lon,
            location_text,
            operating_region=operating_region,
            country=country,
        )

    @staticmethod
    async def get_or_fetch_party_locations(
            supabase_client: Any,
            deal_record: Dict[str, Any],
            context_user_data: Optional[Dict[str, Any]] = None
    ) -> Dict[str, str]:
        """
        Retrieves map URLs by prioritizing context_user_data, then deal_record,
        and fetching directly from provider_listings and seeker_requests tables if absent.
        """
        listing_id = deal_record.get("listing_id")
        request_id = deal_record.get("seeker_request_id") or deal_record.get("request_id")
        seeker_id = deal_record.get("seeker_id")

        listing = {}
        request_data = {}

        # 1. Fetch from provider_listings table
        if listing_id:
            try:
                l_res = (
                    await supabase_client.table("provider_listings")
                    .select("*")
                    .eq("id", listing_id)
                    .maybe_single()
                    .execute()
                )
                if l_res and getattr(l_res, "data", None):
                    listing = l_res.data
            except Exception:
                pass

        # 2. Fetch from seeker_requests table using request_id
        if request_id:
            try:
                r_res = (
                    await supabase_client.table("seeker_requests")
                    .select("*")
                    .eq("id", request_id)
                    .maybe_single()
                    .execute()
                )
                if r_res and getattr(r_res, "data", None):
                    request_data = r_res.data
            except Exception:
                pass

        # Primary Fallback: If request_id was NULL in deals, fetch seeker's latest active request
        if not request_data and seeker_id:
            try:
                vertical = deal_record.get("vertical_type")
                query = (
                    supabase_client.table("seeker_requests")
                    .select("*")
                    .eq("seeker_id", seeker_id)
                )
                if vertical:
                    query = query.eq("vertical_type", vertical)

                r_res = (
                    await query.order("created_at", desc=True)
                    .limit(1)
                    .execute()
                )
                if r_res and getattr(r_res, "data", None) and len(r_res.data) > 0:
                    request_data = r_res.data[0]
            except Exception:
                pass

        return ReservationHelper.generate_party_map_links(
            listing=listing,
            request_data=request_data,
            deal=deal_record,
            context_user_data=context_user_data
        )

    @staticmethod
    async def dispatch_deal_atomically(
            supabase_client: Any,
            deal_id: str,
            deal_record: Dict[str, Any],
            context_user_data: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Updates the deal reservation status and persists party-specific map URLs."""
        map_urls = await LocationService.get_or_fetch_party_locations(
            supabase_client=supabase_client,
            deal_record=deal_record,
            context_user_data=context_user_data
        )

        response = (
            await supabase_client.table("deals")
            .update({
                "status": "dispatched",
                "dispatch_map_url": map_urls["provider_map_url"],
                "seeker_map_url": map_urls["seeker_map_url"],
                "provider_map_url": map_urls["provider_map_url"],
                "updated_at": "now()"
            })
            .eq("id", deal_id)
            .eq("dispatch_sent", False)
            .execute()
        )

        return len(response.data) > 0 if getattr(response, "data", None) else False
