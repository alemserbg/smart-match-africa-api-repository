import logging
import asyncio
import inspect
import datetime
import math
import difflib
import json
import requests
from functools import lru_cache
import socket
from typing import Dict, Any, List, Optional, Tuple
import re
import redis.asyncio as redis
from redis.exceptions import ConnectionError as RedisConnectionError, TimeoutError as RedisTimeoutError

from fastapi import FastAPI, BackgroundTasks, status
from pydantic import BaseModel, Field
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from src.smartmatch.config import settings


# ==========================================
# PRODUCTION INITIALIZATION & CONFIG
# ==========================================

# 1. Unified Logger Setup
logger = logging.getLogger("smartmatch.matching")

# 2. FastAPI Application Initialization
app = FastAPI(
    title="SmartMatch Matching Engine",
    description="Asynchronous multi-vertical matching and webhook handling service.",
    version="1.0.0"
)

# 3. Reusable HTTP Session for OSRM API (Connection Pooling)
osrm_session = requests.Session()

# 4. Redis / Valkey Asynchronous Client Setup
redis_client: Optional[Any] = None
try:
    HAS_REDIS = True
except ImportError:
    redis = None
    HAS_REDIS = False
    # Fallback definitions to prevent NameError and PyCharm static analysis warnings
    RedisConnectionError = Exception
    RedisTimeoutError = Exception
    logger.warning("redis package not installed. OSRM responses will not be cached.")


async def init_redis_cache(redis_url: Optional[str] = None, max_attempts: int = 30, delay: int = 10) -> None:
    """
    Initializes the global asynchronous Redis/Valkey client connection.
    Incorporates resilient retry logic and expanded timeout windows for cold-starts and DNS propagation.
    """
    global redis_client
    if not HAS_REDIS:
        return

    url = redis_url or settings.REDIS_URL
    if not url:
        logger.warning("No Redis/Valkey URL provided. Cache will be disabled.")
        return

    try:
        if redis_client is None:
            logger.info("Initializing connection parameters for OSRM Redis cache...")
            redis_client = redis.from_url(
                url,
                decode_responses=True,
                socket_connect_timeout=30,  # 30s timeout for cold starts
                socket_timeout=30
            )

        attempt = 0
        while attempt < max_attempts:
            attempt += 1
            try:
                await redis_client.ping()
                logger.info("✅ Redis/Valkey cache client initialized successfully for OSRM routing.")
                return
            except (RedisConnectionError, RedisTimeoutError, asyncio.TimeoutError,
                    OSError, socket.gaierror) as conn_err:
                if attempt >= max_attempts:
                    logger.critical(f"❌ Redis/Valkey service failed to wake up or resolve after "
                                    f"{max_attempts * delay} seconds.")
                    redis_client = None
                    raise conn_err

                logger.info(
                    f"⏳ Waiting for Redis/Valkey DNS/VM to provision... "
                    f"(Attempt {attempt}/{max_attempts}). Retrying in {delay}s..."
                )
                await asyncio.sleep(delay)

    except Exception as e:
        logger.critical(f"❌ Critical failure initializing Redis client in matching.py: {e}", exc_info=True)
        redis_client = None


SHARED_CITY_SYNONYMS = {
    # Addis Ababa Region / Sheger
    "addis ababa": "addis_ababa", "አዲስ አበባ": "addis_ababa", "አዲስ አበበ": "addis_ababa", "finfinne": "addis_ababa", "finfinnee": "addis_ababa", "sheger": "addis_ababa", "ሸገር": "addis_ababa",
    # Adama / Nazareth
    "adama": "adama", "nazareth": "adama", "nazereth": "adama", "ናዝሬት": "adama", "adaamaa": "adama", "mojo": "mojo", "ሞጆ": "mojo",
    # Bishoftu / Debre Zeit
    "beshoftu": "bishoftu", "bishoftu": "bishoftu", "debre zeit": "bishoftu", "ቢሾፍቱ": "bishoftu", "ደብረ ዘይት": "bishoftu", "bishoftuu": "bishoftu",
    # Hawassa / Sidama Hubs
    "hawassa": "hawassa", "awasa": "hawassa", "ሐዋሳ": "hawassa", "hawasaa": "hawassa", "yirgalem": "yirgalem", "ይርጋለም": "yirgalem", "aleta wondo": "aleta_wondo", "አሌታ ወንዶ": "aleta_wondo",
    # Dire Dawa & Harari Region
    "dire dawa": "dire_dawa", "dire daawa": "dire_dawa", "dire dawaa": "dire_dawa", "ድሬ ዳዋ": "dire_dawa", "dirre dhawaa": "dire_dawa", "harar": "harar", "harrar": "harar", "ሐረር": "harar", "hararii": "harar",
    # Amhara Region Hubs
    "bahir dar": "bahir_dar", "bahirdar": "bahir_dar", "ባህር ዳር": "bahir_dar", "gondar": "gondar", "gonder": "gondar", "ጎንደር": "gondar", "dessie": "dessie", "dese": "dessie", "ደሴ": "dessie",
    "kombolcha": "kombolcha", "ኮምቦልቻ": "kombolcha", "debre markos": "debre_markos", "ደብረ ማርቆስ": "debre_markos", "debre tabor": "debre_tabor", "ደብረ ታቦር": "debre_tabor",
    "debre berhan": "debre_berhan", "ደብረ ብርሃን": "debre_berhan", "woldia": "woldia", "woldeya": "woldia", "ወልድያ": "woldia", "finote selam": "finote_selam", "ፍኖተ ሰላም": "finote_selam", "metema": "metema", "መተማ": "metema",
    # Tigray Region Hubs
    "mekelle": "mekelle", "mekele": "mekelle", "መቐለ": "mekelle", "mek’ele": "mekelle", "axum": "axum", "aksum": "axum", "አክሱም": "axum", "adwa": "adwa", "አድዋ": "adwa",
    "adigrat": "adigrat", "አዲግራት": "adigrat", "shire": "shire", "inda selassie": "shire", "እንገደ ስላሴ": "shire", "ሽሬ": "shire", "alamata": "alamata", "አላማጣ": "alamata", "maychew": "maychew", "ማይጨው": "maychew", "wukro": "wukro", "ውቅሮ": "wukro", "humera": "humera", "ሁመራ": "humera",
    # Oromia Region Hubs
    "jimma": "jimma", "jima": "jimma", "ጅማ": "jimma", "jiimaa": "jimma", "nekemte": "nekemte", "nekemtee": "nekemte", "ነቀምቴ": "nekemte", "naqamtee": "nekemte",
    "ambo": "ambo", "hambo": "ambo", "አምቦ": "ambo", "amboo": "ambo", "shashemene": "shashemene", "shashamane": "shashemene", "ሻሸመኔ": "shashemene",
    "asella": "asella", "azela": "asella", "አሰላ": "asella", "goba": "goba", "ጎባ": "goba", "robe": "robe", "ሮቤ": "robe", "negele borana": "negele_borana", "ነጌሌ ቦረና": "negele_borana",
    "yabelo": "yabelo", "ያቤሎ": "yabelo", "moyale": "moyale", "ሞያሌ": "moyale", "bedele": "bedele", "በደሌ": "bedele", "gimbi": "gimbi", "ጊምቢ": "gimbi",
    "dembi dollo": "dembi_dollo", "ደምቢዶሎ": "dembi_dollo", "holeta": "holeta", "ሆለታ": "holeta", "sululta": "sululta", "ሱሉልታ": "sululta", "sendafa": "sendafa", "ሰንዳፋ": "sendafa",
    "awash": "awash", "አዋሽ": "awash", "metahara": "metahara", "መተሐራ": "metahara", "gelemso": "gelemso", "ገለምሶ": "gelemso", "chiro": "chiro", "ጪሮ": "chiro", "bedesa": "bedesa", "በደሳ": "bedesa", "agaro": "agaro", "አጋሮ": "agaro",
    "gudar": "gudar", "ጉደር": "gudar", "ginchi": "ginchi", "ጊንጪ": "ginchi",
    # SNNPR / South Ethiopia / Central Ethiopia Hubs
    "arba minch": "arba_minch", "arbaminch": "arba_minch", "አርባ ምንጭ": "arba_minch", "sodo": "sodo", "wolaita sodo": "sodo", "welaita sodo": "sodo", "ሶዶ": "sodo", "ወላይታ ሶዶ": "sodo",
    "hosaena": "hosaena", "hosaina": "hosaena", "hossana": "hosaena", "ሆሳዕና": "hosaena", "welkite": "welkite", "welqite": "welkite", "wolkite": "welkite", "ውልኬታ": "welkite", "ወልቂጤ": "welkite",
    "butajira": "butajira", "ቡታጅራ": "butajira", "dilla": "dilla", "ዲላ": "dilla", "durame": "durame", "ዱራሜ": "durame", "bonga": "bonga", "ቦንጋ": "bonga",
    "mizan teferi": "mizan_teferi", "ሚዛን ተፈሪ": "mizan_teferi", "jinka": "jinka", "ጂንካ": "jinka", "konso": "konso", "ኮንሶ": "konso", "sawla": "sawla", "ሳውላ": "sawla",
    # Somali Region Hubs
    "jijiga": "jijiga", "jijjiga": "jijiga", "ጅጅጋ": "jijiga", "jigjiga": "jijiga", "gode": "gode", "ጎዴ": "gode", "hargeisa": "hargeisa", "ሀርጌሳ": "hargeisa",
    # Afar, Gambela, Benishangul, & International Neighbors
    "semera": "semera", "ሰመራ": "semera", "gambela": "gambela", "gambella": "gambela", "ጋምቤላ": "gambela", "assosa": "assosa", "asosa": "assosa", "አሶሳ": "assosa",
    "batu": "batu", "zeway": "batu", "zewai": "batu", "ባቱ": "batu", "ዘዋይ": "batu", "djibouti": "djibouti", "ጅቡቲ": "djibouti", "berbera": "berbera", "በርበራ": "berbera",
}

SHARED_LOCATION_STOPWORDS = {
    "freight", "cargo", "transport", "route", "trip", "from", "to", "via",
    "station", "terminal", "city", "town", "area", "region", "zone",
    "express", "line", "service", "shipper", "carrier", "request", "selected",
    "port", "seeker"
}

# ==========================================
# 5. Environment & Semantic AI Bypass (RAM Optimized)
# ==========================================

# Force-disable semantic heavy models to fit Render 512MB RAM free limit
HAS_SEMANTICS = False
SentenceTransformer = None
util = None
logger.info("⚡ Lightweight production mode: Semantic AI model loading disabled to conserve RAM.")


# ==========================================
# SEMANTIC MATCHING AI ENGINE (LIGHTWEIGHT FALLBACK)
# ==========================================
class SemanticMatcher:
    """
    Lightweight mock manager preserving all structural hooks and method signatures
    while utilizing strict string and token matching to operate with near-zero RAM.
    """
    model = None

    @classmethod
    def load_model(cls):
        # Intentionally a no-op to prevent loading torch & sentence-transformers into RAM
        pass

    @classmethod
    @lru_cache(maxsize=2048)
    def _get_cached_embedding(cls, _text: str) -> Optional[Any]:
        """Mock method for embedding generation returning None."""
        return None

    @classmethod
    def is_functionally_similar(cls, text1: str, text2: str, threshold: float = 0.65) -> bool:
        """Evaluates strings using a token overlap ratio checked against the threshold."""
        if not text1 or not text2:
            return False

        t1, t2 = str(text1).lower().strip(), str(text2).lower().strip()
        if t1 == t2:
            return True

        words1, words2 = set(t1.split()), set(t2.split())
        if not words1 or not words2:
            return False

        if words1.issubset(words2) or words2.issubset(words1):
            return True

        # Calculate actual token similarity score so 'threshold' is fully utilized
        common = words1.intersection(words2)
        smaller_size = min(len(words1), len(words2))
        similarity_score = len(common) / smaller_size if smaller_size > 0 else 0.0

        return similarity_score >= threshold

    @classmethod
    def batch_similarity(
            cls,
            query_text: str,
            candidate_texts: List[str],
            _threshold: float = 0.65
    ) -> List[Tuple[str, float, bool]]:
        """Vectorized batch evaluation fallback using token set matching."""
        if not query_text or not candidate_texts:
            return [(c, 0.0, False) for c in candidate_texts]

        clean_query = str(query_text).lower().strip()
        query_words = set(clean_query.split())
        results = []

        for cand_raw in candidate_texts:
            cand_clean = str(cand_raw).lower().strip()
            cand_words = set(cand_clean.split())
            matched = (
                    clean_query == cand_clean
                    or query_words.issubset(cand_words)
                    or cand_words.issubset(query_words)
                    or bool(query_words.intersection(cand_words))
            )
            results.append((cand_raw, 1.0 if matched else 0.0, matched))
        return results


# =====================================================================
# UTILITY FUNCTIONS & RECORD NORMALIZATION
# =====================================================================

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
        clean_val = re.sub(r"[^\d.]", "", str(val).replace(",", ""))
        return float(clean_val) if clean_val else default
    except (ValueError, TypeError):
        return default


def safe_list(val: Any) -> List[str]:
    """Normalizes list or comma-separated string inputs to a list of clean strings."""
    if not val:
        return []
    if isinstance(val, list):
        return [str(x).strip().lower() for x in val if str(x).strip()]
    if isinstance(val, str):
        return [x.strip().lower() for x in val.split(',') if x.strip()]
    return []


def _clean_val(val: Any, default: str = "") -> str:
    """Removes raw placeholders like 'not_specified', 'N/A', 'None', 'null'."""
    if val is None:
        return default
    s = str(val).strip()
    if s.lower() in {"none", "null", "not_specified", "not specified", "n/a", "na", "undefined", ""}:
        return default
    return s


def _format_mmy(data: dict, keys: list = None) -> str:
    """
    Constructs or extracts a clean Make/Model/Year string from dictionary records,
    filtering out placeholders.
    """
    if not keys:
        keys = ['make_model_year', 'preferred_make_model_year', 'vehicle_type', 'property_use']

    for k in keys:
        val = _clean_val(data.get(k))
        if val:
            return val

    year = _clean_val(data.get('year'))
    brand = _clean_val(data.get('brand') or data.get('make'))
    model = _clean_val(data.get('model'))

    constructed = " ".join(filter(None, [year, brand, model])).strip()
    return constructed if constructed else "Standard Vehicle Request"


def normalize_record(raw: Dict[str, Any], is_seeker: bool = False) -> Dict[str, Any]:
    """
    Aligns raw database columns from provider_listings and seeker_requests
    to standardized keys required by VehicleScorer.
    """
    if not isinstance(raw, dict):
        return {}
    record = dict(raw)
    record['is_seeker'] = is_seeker

    # 1. Align Make/Model/Year with Title fallback
    mmy = safe_str(record.get('make_model_year'))
    title = safe_str(record.get('title'))
    generic_categories = {'van', 'minivan', 'car', 'suv', 'sedan', 'truck', 'bus'}

    if not mmy or mmy.lower() in generic_categories:
        record['make_model_year'] = title if title else mmy

    # 2. Extract Fuel Type from unstructured text if missing
    fuel_val = safe_str(record.get('fuel_type')).lower()
    if not fuel_val or fuel_val in {'', 'n/a', 'none', 'not specified', 'not_specified'}:
        full_text = f"{title} {mmy} {safe_str(record.get('description'))}".lower()
        if 'hybrid' in full_text:
            record['fuel_type'] = 'hybrid'
        elif 'electric' in full_text or 'ev' in full_text:
            record['fuel_type'] = 'electric'
        elif 'diesel' in full_text:
            record['fuel_type'] = 'diesel'
        elif 'petrol' in full_text or 'gasoline' in full_text or 'gas' in full_text:
            record['fuel_type'] = 'petrol'

    # 3. Budget / Price Field Normalization
    if is_seeker:
        record['max_budget'] = safe_float(record.get('max_budget') or record.get('price') or record.get('budget'))
    else:
        record['price'] = safe_float(record.get('price'))

    return record


# =====================================================================
# VERTICAL SCORING ENGINES
# =====================================================================

class BaseScorer:
    """Base scoring module containing shared utility criteria algorithms."""

    @staticmethod
    def calculate_text_similarity(str1: str, str2: str) -> float:
        """Fuzzy string similarity for text-based fallback when GPS pins are missing."""
        if not str1 or not str2:
            return 0.0
        return difflib.SequenceMatcher(None, str1.lower().strip(), str2.lower().strip()).ratio()

    @staticmethod
    def ilike_match(str1: str, str2: str) -> bool:
        """
        Mimics SQL's .ilike behavior for Python strings.
        Performs a case-insensitive substring match to handle slight naming variations.
        """
        if not str1 or not str2:
            return False
        s1 = str1.strip().lower()
        s2 = str2.strip().lower()
        return s1 in s2 or s2 in s1

    @staticmethod
    def is_currency_mismatch(curr1: str, curr2: str) -> bool:
        """Strict currency rule: checks currency consistency."""
        c1 = (curr1 or 'ETB').strip().upper()
        c2 = (curr2 or 'ETB').strip().upper()
        return c1 != c2

    @staticmethod
    def score_budget(
        cost: float,
        budget: float,
        weight: float = 30.0,
        seeker_currency: str = 'ETB',
        provider_currency: str = 'ETB'
    ) -> float:
        """
        Scores budget match between seeker max_budget and provider price with stepped ratio decay.
        """
        cost_val = safe_float(cost)
        budget_val = safe_float(budget)

        if BaseScorer.is_currency_mismatch(seeker_currency, provider_currency):
            return 0.0

        if budget_val <= 0 or cost_val <= 0:
            return weight * 0.50

        if cost_val <= budget_val:
            return weight

        excess_ratio = (cost_val - budget_val) / budget_val
        if excess_ratio <= 0.10:
            return weight * 0.80
        elif excess_ratio <= 0.25:
            return weight * 0.50
        elif excess_ratio <= 0.50:
            return weight * 0.20
        return 0.0

    @staticmethod
    def score_features(
        required_features: Any = None,
        provided_features_str: Any = "",
        weight: float = 15.0,
        desired: Any = None,
        provided: Any = None
    ) -> float:
        """
        Scores feature/amenity overlaps with support for flexible positional and keyword signatures.
        """
        feats = safe_list(required_features if required_features is not None else desired)
        prov_raw = provided_features_str if provided_features_str != "" else provided
        prov_str = safe_str(prov_raw).lower()

        if not feats:
            return weight * 0.50
        if not prov_str:
            return weight * 0.20

        matched = sum(1 for feat in feats if feat in prov_str)
        return weight * (matched / len(feats))


class EquipmentScorer(BaseScorer):
    ALLOWED_CATEGORIES = {
        "construction & earthmoving",
        "agriculture & irrigation",
        "generator & power",
        "event tech & communication",
    }

    # Brand taxonomy compiled across key equipment domains
    KNOWN_BRANDS = {
        # Construction & Earthmoving
        "caterpillar", "cat", "case", "komatsu", "volvo", "volvo ce", "xcmg",
        "sany", "bomag", "vögele", "vogele", "wirtgen", "deere", "john deere",
        # Agriculture & Irrigation
        "sonalika", "dasmesh", "kci", "mahindra", "jangeer", "kgn",
        "massey ferguson", "massey", "ferguson", "case ih", "new holland", "claas", "john deere",
        # Generator, Power & Industrial Compressors
        "cummins", "perkins", "siemens", "abb", "kubota", "baldor",
        "ingersoll rand", "kaeser", "sullair", "atlas copco", "doosan", "quincy",
        # Event Tech & Specialized Logistics
        "et heavy", "hulu cart", "yenenesh", "a3s", "eyob demissie", "trident",
    }

    # Standardized Functional Machine Families (Refined to separate combine/grain harvesters from root/tuber harvesters)
    FUNCTION_FAMILIES = {
        "grain_combine": {"combine", "axial-flow", "rotary", "conventional", "stripper"},
        "root_tuber_harvester": {"potato", "beet", "tuber", "root", "carrot", "onion", "digger"},
        "forage_harvester": {"forage", "silage"},
        "harvester": {"harvester", "reaper"},
        "earthmoving": {"excavator", "loader", "dozer", "backhoe", "grader", "motor grader", "paver"},
        "tillage": {"tractor", "tiller", "plow", "cultivator"},
        "power_compressor": {"generator", "genset", "compressor", "pump"},
    }

    @classmethod
    def _strip_brands(cls, text: str) -> str:
        """Strips known brand names from equipment titles prior to functional checking."""
        if not text:
            return ""
        cleaned = text.lower()
        # Sort by length descending to match multi-word brands before single tokens
        for brand in sorted(cls.KNOWN_BRANDS, key=len, reverse=True):
            pattern = r"\b" + re.escape(brand) + r"\b"
            cleaned = re.sub(pattern, "", cleaned)
        return re.sub(r"\s+", " ", cleaned).strip()

    @classmethod
    def _normalize_category(cls, raw_category: Any) -> str:
        """Normalizes and resolves raw category inputs into standard allowed category taxonomy."""
        if not raw_category:
            return ""
        cleaned = re.sub(r"[^\w\s&]", "", str(raw_category)).strip().lower()
        cleaned = re.sub(r"\s+", " ", cleaned)

        if cleaned in cls.ALLOWED_CATEGORIES:
            return cleaned

        if "agriculture" in cleaned or "irrigation" in cleaned or "farming" in cleaned:
            return "agriculture & irrigation"
        if "construction" in cleaned or "earthmoving" in cleaned:
            return "construction & earthmoving"
        if "generator" in cleaned or "power" in cleaned or "generators" in cleaned:
            return "generator & power"
        if "event" in cleaned or "communication" in cleaned or "eventtech" in cleaned:
            return "event tech & communication"

        return cleaned

    @classmethod
    def _get_functional_family(cls, text: str) -> str:
        """Extracts standardized functional family after stripping brand noise."""
        clean_text = cls._strip_brands(text)
        for family, keywords in cls.FUNCTION_FAMILIES.items():
            if any(kw in clean_text for kw in keywords):
                return family
        return ""

    @staticmethod
    def _extract_coordinates(data: Dict[str, Any]) -> Optional[Tuple[float, float]]:
        """
        Robustly extracts latitude and longitude from provider_listings or seeker_requests dictionaries,
        supporting multiple key aliases, nested dictionaries, and coordinate tuples/lists.
        """
        if not data or not isinstance(data, dict):
            return None

        lat_keys = ["latitude", "lat", "location_lat"]
        lon_keys = ["longitude", "lon", "lng", "location_lon"]

        lat_val = 0.0
        lon_val = 0.0

        # Check top-level keys independently
        for k in lat_keys:
            if data.get(k) is not None:
                lat_val = safe_float(data.get(k))
                if lat_val != 0.0:
                    break

        for k in lon_keys:
            if data.get(k) is not None:
                lon_val = safe_float(data.get(k))
                if lon_val != 0.0:
                    break

        if lat_val != 0.0 or lon_val != 0.0:
            return lat_val, lon_val

        # Check nested 'location' or 'coordinates' structures
        loc = data.get("location") or data.get("coordinates")
        if isinstance(loc, dict):
            lat = safe_float(loc.get("lat") or loc.get("latitude") or loc.get("location_lat"))
            lon = safe_float(loc.get("lon") or loc.get("lng") or loc.get("longitude") or loc.get("location_lon"))
            if lat != 0.0 or lon != 0.0:
                return lat, lon
        elif isinstance(loc, (list, tuple)) and len(loc) >= 2:
            try:
                return float(loc[0]), float(loc[1])
            except (ValueError, TypeError):
                pass

        return None

    @staticmethod
    def _calculate_haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """
        Calculates the great-circle distance in kilometers between two geographic coordinates
        with complete numerical stability and syntax correction.
        """
        if lat1 is None or lon1 is None or lat2 is None or lon2 is None:
            return 0.0

        try:
            lat1, lon1, lat2, lon2 = float(lat1), float(lon1), float(lat2), float(lon2)
        except (ValueError, TypeError):
            return 0.0

        R = 6371.0  # Earth radius in km
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)

        a = (
                math.sin(dlat / 2.0) ** 2
                + math.cos(math.radians(lat1))
                * math.cos(math.radians(lat2))
                * math.sin(dlon / 2.0) ** 2
        )

        # Clamp 'a' to [0.0, 1.0] to prevent math domain errors from floating-point inaccuracies
        a = max(0.0, min(1.0, a))

        c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
        return R * c

    @staticmethod
    def calculate_score(user_prefs: Dict[str, Any], provider_data: Dict[str, Any]) -> float:
        prov_vertical = safe_str(provider_data.get("vertical_type")).lower()
        if prov_vertical and prov_vertical != "equipment":
            return 0.0

        user_tx = safe_str(user_prefs.get("transaction_type")).lower()
        prov_tx = safe_str(provider_data.get("transaction_type")).lower()
        if user_tx and prov_tx and user_tx != prov_tx:
            return 0.0

        s_curr = user_prefs.get("currency", "ETB")
        p_curr = provider_data.get("currency", "ETB")
        if BaseScorer.is_currency_mismatch(s_curr, p_curr):
            return 0.0

        # Strict Category Check
        raw_user_cat = (
            user_prefs.get("equipment_category")
            or user_prefs.get("category")
            or user_prefs.get("property_use")
        )
        raw_prov_cat = (
            provider_data.get("equipment_category")
            or provider_data.get("category")
            or provider_data.get("property_use")
        )

        user_cat = EquipmentScorer._normalize_category(raw_user_cat)
        prov_cat = EquipmentScorer._normalize_category(raw_prov_cat)

        if user_cat in EquipmentScorer.ALLOWED_CATEGORIES and prov_cat in EquipmentScorer.ALLOWED_CATEGORIES:
            if user_cat != prov_cat:
                return 0.0

        # Equipment Type & Functional Similarity Matching
        user_equip = safe_str(user_prefs.get("equipment_type") or user_prefs.get("title"))
        equip_type = safe_str(provider_data.get("equipment_type") or provider_data.get("title"))

        if user_equip:
            if not equip_type:
                return 0.0

            u_fam = EquipmentScorer._get_functional_family(user_equip)
            p_fam = EquipmentScorer._get_functional_family(equip_type)

            # Rejection 1: Explicit functional family mismatch (e.g. Grain Combine vs Root/Tuber Harvester)
            if u_fam and p_fam and u_fam != p_fam:
                return 0.0

            # Additional Cross-Family Safety: Prevent grain/combine searches from matching root/tuber harvesters
            if u_fam == "grain_combine" and p_fam == "root_tuber_harvester":
                return 0.0
            if u_fam == "root_tuber_harvester" and p_fam == "grain_combine":
                return 0.0

            # Strip brand tokens for pure functional comparison
            clean_user_equip = EquipmentScorer._strip_brands(user_equip)
            clean_equip_type = EquipmentScorer._strip_brands(equip_type)

            required_threshold = 0.50 if (u_fam and p_fam and u_fam == p_fam) else 0.75

            if not SemanticMatcher.is_functionally_similar(
                clean_user_equip, clean_equip_type, threshold=required_threshold
            ):
                return 0.0

        score = 0.0
        total_points = 100.0

        # Proximity & Distance Matching
        user_coords = EquipmentScorer._extract_coordinates(user_prefs)
        prov_coords = EquipmentScorer._extract_coordinates(provider_data)

        if user_coords and prov_coords:
            dist_km = EquipmentScorer._calculate_haversine(
                user_coords[0], user_coords[1], prov_coords[0], prov_coords[1]
            )

            max_dist = (
                safe_float(user_prefs.get("max_distance"))
                or safe_float(user_prefs.get("max_radius"))
                or safe_float(user_prefs.get("radius_km"))
            )

            if 0 < max_dist < dist_km:
                return 0.0

            prox_weight = 25.0
            total_points += prox_weight

            effective_radius = max_dist if max_dist > 0 else 500.0
            if dist_km <= 5.0:
                score += prox_weight
            else:
                decay_score = max(0.0, prox_weight * (1.0 - (dist_km / effective_radius)))
                score += decay_score

        # Budget Scoring
        cost = safe_float(provider_data.get("price") or provider_data.get("cost"))
        budget = safe_float(user_prefs.get("max_budget"))
        score += EquipmentScorer.score_budget(cost, budget, 30.0, s_curr, p_curr)

        # Capacity Output Scoring (Fixed key fallback)
        prov_cap = safe_float(provider_data.get("capacity_output") or provider_data.get("capacity"))
        req_cap = safe_float(
            user_prefs.get("min_required_capacity")
            or user_prefs.get("min_required_capacity_output")
            or user_prefs.get("capacity_output")
        )
        if req_cap == 0 or prov_cap >= req_cap:
            score += 30.0
        elif prov_cap >= req_cap * 0.8:
            score += 15.0

        # Brand Preference Scoring (Penalty applied via 10 pts vs 20 pts)
        brand = safe_str(provider_data.get("brand")).lower()
        pref_brand = safe_str(user_prefs.get("preferred_brand")).lower()
        if not pref_brand or pref_brand == "any" or SemanticMatcher.is_functionally_similar(pref_brand, brand):
            score += 20.0
        else:
            score += 10.0

        # Features & Specifications Scoring (Fixed key fallback)
        raw_features = (
                provider_data.get("amenities")
                or provider_data.get("features")
                or provider_data.get("specifications")
        )
        req_features_list = safe_list(
            user_prefs.get("required_amenities")
            or user_prefs.get("required_features")
            or user_prefs.get("preferred_features")
            or user_prefs.get("desired_features")
        )
        score += EquipmentScorer.score_features(
            req_features_list,
            safe_str(raw_features),
            20.0,
        )

        return round((score / total_points) * 100, 2)


class HousingScorer(BaseScorer):

    @staticmethod
    def calculate_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """
        Calculates the great-circle distance between two points on Earth (in km)
        using the Haversine formula with complete null-safety and type validation.
        """
        if lat1 is None or lon1 is None or lat2 is None or lon2 is None:
            return 0.0

        try:
            lat1, lon1, lat2, lon2 = float(lat1), float(lon1), float(lat2), float(lon2)
        except (ValueError, TypeError):
            return 0.0

        R = 6371.0  # Earth radius in kilometers
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)

        a = (
                math.sin(dlat / 2) ** 2 +
                math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
        )

        # ✅ Fixed: Added missing multiplication (*) and subtraction (-) operators
        c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))

        return R * c

    @staticmethod
    def _is_plot_or_land(prop_str: str) -> bool:
        if not prop_str:
            return False
        clean_val = safe_str(prop_str).lower()
        return any(keyword in clean_val for keyword in ['plot', 'land', 'site', 'parcel', 'ground'])

    @staticmethod
    def extract_canonical_city(data: Dict[str, Any]) -> Optional[str]:
        if not data or not isinstance(data, dict):
            return None

        sorted_synonyms = sorted(SHARED_CITY_SYNONYMS.keys(), key=len, reverse=True)

        # 1. Prioritize explicit 'city' field if provided by the UI/Bot
        explicit_city = safe_str(data.get('city')).lower()
        if explicit_city:
            for synonym in sorted_synonyms:
                if synonym in explicit_city:
                    return SHARED_CITY_SYNONYMS[synonym]

        # 2. Fallback to broad location texts
        location_fields = ['location', 'location_text', 'address', 'origin', 'pickup_location', 'subcity']
        combined_text = " ".join([safe_str(data.get(k)).lower() for k in location_fields if data.get(k)]).strip()

        if combined_text:
            for synonym in sorted_synonyms:
                if synonym in combined_text:
                    return SHARED_CITY_SYNONYMS[synonym]

        return None

    @staticmethod
    def calculate_score(user_prefs: Dict[str, Any], provider_data: Dict[str, Any]) -> float:
        prov_vertical = safe_str(provider_data.get('vertical_type')).lower()
        if prov_vertical and prov_vertical != 'housing':
            return 0.0

        user_tx = safe_str(user_prefs.get('transaction_type')).lower()
        prov_tx = safe_str(provider_data.get('transaction_type')).lower()
        if user_tx and prov_tx and user_tx != prov_tx:
            return 0.0

        # --- 1. STRICT CROSS-CITY & PROXIMITY FILTERING ---
        user_city = HousingScorer.extract_canonical_city(user_prefs)
        prov_city = HousingScorer.extract_canonical_city(provider_data)

        # Guard A: Both are recognized cities but mismatch
        if user_city and prov_city and user_city != prov_city:
            return 0.0

        # Guard B: Explicit 'city' fields mismatch
        u_city_raw = safe_str(user_prefs.get('city')).lower()
        p_city_raw = safe_str(provider_data.get('city')).lower()
        if u_city_raw and p_city_raw and u_city_raw != p_city_raw:
            if not (user_city and prov_city and user_city == prov_city):
                return 0.0

        # Guard C: User specified a canonical city, but provider city was unmapped/None
        if user_city and not prov_city:
            p_combined = " ".join([
                safe_str(provider_data.get(k)).lower()
                for k in ['city', 'subcity', 'location_text', 'location', 'address']
                if provider_data.get(k)
            ]).strip()

            valid_user_synonyms = [syn for syn, canonical in SHARED_CITY_SYNONYMS.items() if canonical == user_city]
            if not any(syn in p_combined for syn in valid_user_synonyms):
                return 0.0

        # Guard D: Provider specified a canonical city, but user city was unmapped/None
        if prov_city and not user_city:
            u_combined = " ".join([
                safe_str(user_prefs.get(k)).lower()
                for k in ['city', 'subcity', 'location_text', 'location', 'address']
                if user_prefs.get(k)
            ]).strip()

            valid_prov_synonyms = [syn for syn, canonical in SHARED_CITY_SYNONYMS.items() if canonical == prov_city]
            if not any(syn in u_combined for syn in valid_prov_synonyms):
                return 0.0

        prop_type = safe_str(provider_data.get('property_use'))
        user_prop = safe_str(user_prefs.get('property_use'))

        user_is_plot = HousingScorer._is_plot_or_land(user_prop)
        prov_is_plot = HousingScorer._is_plot_or_land(prop_type)

        if user_is_plot != prov_is_plot:
            return 0.0

        if user_tx in ['rent', 'rental'] and prov_is_plot and not user_is_plot:
            return 0.0

        s_curr = user_prefs.get('currency', 'ETB')
        p_curr = provider_data.get('currency', 'ETB')
        if BaseScorer.is_currency_mismatch(s_curr, p_curr):
            return 0.0

        def _get_coords(data: dict):
            if hasattr(BaseScorer, 'extract_coordinates'):
                return BaseScorer.extract_coordinates(data)
            return data.get('latitude'), data.get('longitude')

        # Robust coordinate extraction using schema-aligned keys
        s_lat, s_lon = _get_coords(user_prefs)
        p_lat, p_lon = _get_coords(provider_data)

        if s_lat and s_lon and p_lat and p_lon:
            try:
                dist = HousingScorer.calculate_distance(float(s_lat), float(s_lon), float(p_lat), float(p_lon))
                max_allowed_dist = 25.0 if user_tx in ['sell', 'sale', 'buy'] else 10.0
                if dist > max_allowed_dist:
                    return 0.0
            except (ValueError, TypeError):
                pass

        if not SemanticMatcher.is_functionally_similar(user_prop, prop_type, threshold=0.60):
            return 0.0

        # --- DYNAMIC SCORING ENGINE (PROPORTIONAL WEIGHTS) ---
        score = 0.0
        total_possible = 0.0

        # 1. Budget Score (Weight: 35 Points)
        total_possible += 35.0
        cost = safe_float(provider_data.get('price'))
        budget = safe_float(user_prefs.get('max_budget'))
        if budget > 0 and cost > 0:
            if cost <= budget:
                score += 35.0
            else:
                over_ratio = (cost - budget) / budget
                if over_ratio <= 0.30:  # Allow up to 30% over budget with scaled partial credit
                    # ✅ Fixed syntax: added missing multiplication (*) and subtraction (-) operators
                    score += 35.0 * (1.0 - (over_ratio / 0.30))

        # 2. Neighborhood / Subcity Score (Weight: 20 Points)
        u_sub = " ".join(
            [safe_str(user_prefs.get(k)).lower() for k in ['subcity', 'location_text', 'location', 'address']]).strip()
        p_sub = " ".join([safe_str(provider_data.get(k)).lower() for k in
                          ['subcity', 'location_text', 'location', 'address']]).strip()

        if u_sub and p_sub:
            total_possible += 20.0
            u_words = set(re.findall(r'\b\w+\b', u_sub))
            p_words = set(re.findall(r'\b\w+\b', p_sub))

            generic_stopwords = {'the', 'near', 'around', 'in', 'at', 'area'}
            city_tokens = set()
            if user_city:
                city_tokens.update(re.findall(r'\b\w+\b', user_city.lower()))
            if prov_city:
                city_tokens.update(re.findall(r'\b\w+\b', prov_city.lower()))

            stopwords = generic_stopwords.union(city_tokens)
            u_meaningful = u_words - stopwords
            p_meaningful = p_words - stopwords

            if u_meaningful and p_meaningful and u_meaningful.intersection(p_meaningful):
                score += 20.0
            elif not u_meaningful or not p_meaningful:
                score += 15.0

        # 3. Bedrooms Score (Weight: 20 Points)
        total_possible += 20.0
        prov_beds = safe_float(provider_data.get('bedrooms'))
        req_beds = safe_float(user_prefs.get('min_bedrooms'))
        if req_beds == 0 or prov_beds >= req_beds:
            score += 20.0
        elif prov_beds >= req_beds - 1:
            score += 10.0

        # 4. Square Meters Score with Proportional Partial Credit (Weight: 15 Points)
        total_possible += 15.0
        prov_sqm = safe_float(provider_data.get('sq_meters'))
        req_sqm = safe_float(user_prefs.get('min_sq_meters'))
        if req_sqm == 0 or prov_sqm >= req_sqm:
            score += 15.0
        elif prov_sqm > 0:
            ratio = prov_sqm / req_sqm
            if ratio >= 0.70:
                # ✅ Fixed syntax: added missing multiplication (*) operator
                score += 15.0 * ratio

        # 5. Features / Amenities Score (Weight: 10 Points)
        total_possible += 10.0
        score += HousingScorer.score_features(
            safe_list(user_prefs.get('required_amenities')),
            safe_str(provider_data.get('amenities')), 10.0
        )

        if total_possible == 0.0:
            return 0.0

        final_percentage = round((score / total_possible) * 100.0, 2)
        return max(0.0, min(100.0, final_percentage))


# ==========================================
# VEHICLE SCORER ENGINE
# ==========================================

class VehicleScorer(BaseScorer):
    """
    High-precision scoring engine for vehicle matching. Fully aligned with
    VehicleHandler, handle_contact_info, provider_listings, and seeker_requests tables.
    """

    # Normalized transaction mapping aligning buy/sell and rent/lease channels
    TRANSACTION_MAP = {
        'buy': 'sell', 'buying': 'sell', 'purchase': 'sell', 'buyer': 'sell',
        'sell': 'sell', 'selling': 'sell', 'for sale': 'sell', 'sale': 'sell', 'seller': 'sell',
        'rent': 'rent', 'rental': 'rent', 'renting': 'rent', 'for rent': 'rent',
        'lease': 'rent', 'leasing': 'rent', 'for lease': 'rent'
    }

    # Strict 6 canonical vehicle categories matching UI values
    CANONICAL_CATEGORIES = {'sedan', 'suv', 'van', 'bus', 'truck', 'moto'}

    # Extended vehicle taxonomy with model keywords and handle_contact_info compound strings
    VEHICLE_TYPE_SYNONYMS = {
        'sedan': {
            'sedan', 'saloon', 'hatchback', 'coupe', 'convertible', 'passenger car', 'car', 'automobile',
            'corolla', 'camry', 'civic', 'accord', 'yaris', 'lancer', 'c-class', 'e-class', '3 series', '5 series'
        },
        'suv': {
            'suv', 'crossover', '4x4', '4wd', 'cuv', 'prado', 'land cruiser', 'patrol', 'rav4', 'cr-v', 'crv',
            'tucson', 'sportage', 'santa fe', 'explorer', 'fortuner'
        },
        'van': {
            'van', 'minivan', 'mpv', 'multi-purpose vehicle', 'cargo van', 'passenger van', 'panel van', 'van/minivan',
            'odyssey', 'sienna', 'sprinter', 'transit', 'vito', 'alphard', 'hiace', 'urvan', 'caravan', 'nv200',
            'express', 'promaster', 'movano', 'transporter', 'multivan', 'v-class', 'viano', 'pacifica', 'carnival',
            'staria'
        },
        'bus': {
            'bus', 'minibus', 'microbus', 'coaster', 'coach', 'bus/minibus', 'rosa'
        },
        'truck': {
            'truck', 'pickup', 'pickup truck', 'lorry', 'tipper', 'dumper', 'trailer', 'cargo truck',
            'actros', 'fuso', 'isuzu', 'sinotruk', 'howo', 'hilux'
        },
        'moto': {
            'moto', 'motorcycle', 'motorbike', 'scooter', 'moped', 'bajaj', 'tuk tuk', 'vespa'
        }
    }

    # Wildcard and default strings produced by handle_contact_info
    WILDCARD_TERMS = {
        'any', 'all', 'n/a', 'none', 'not specified', 'not_specified',
        'standard vehicle request', 'undefined', ''
    }

    IGNORED_FUELS = {'n/a', 'none', 'not specified', 'not_specified', 'any', ''}

    @classmethod
    def _normalize_tx(cls, tx_str: str) -> str:
        """Normalizes transaction string to core domain ('sell' or 'rent')."""
        if not tx_str:
            return ""
        clean = tx_str.lower().strip()
        return cls.TRANSACTION_MAP.get(clean, clean)

    @classmethod
    def is_transaction_compatible(cls, user_tx: str, prov_tx: str) -> bool:
        """Validates transaction compatibility across normalized domains."""
        if not user_tx or not prov_tx:
            return True
        u = cls._normalize_tx(user_tx)
        p = cls._normalize_tx(prov_tx)
        if u == p:
            return True
        complementary_pairs = {('buy', 'sell'), ('sell', 'buy'), ('rent', 'rent'), ('lease', 'lease')}
        return (u, p) in complementary_pairs

    @staticmethod
    def calculate_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """
        Calculates the great-circle distance between two points on Earth (in km)
        using latitude and longitude coordinates from provider_listings and seeker_requests.
        """
        if lat1 is None or lon1 is None or lat2 is None or lon2 is None:
            return 0.0

        try:
            lat1, lon1, lat2, lon2 = float(lat1), float(lon1), float(lat2), float(lon2)
        except (ValueError, TypeError):
            return 0.0

        R = 6371.0
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)

        a = (math.sin(dlat / 2) ** 2 +
             math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2)

        # ✅ Fixed: Added missing multiplication operator (*)
        c = 2 * math.asin(math.sqrt(a))

        return R * c

    @staticmethod
    def score_distance(distance_km: float, max_radius_km: float, weight: float) -> float:
        """Scores distance proximity up to a maximum radius for matching."""
        if distance_km is None or max_radius_km <= 0:
            return weight * 0.5

        if distance_km <= max_radius_km:
            # ✅ Fixed: Added missing subtraction operator (-)
            return weight * (1.0 - (distance_km / max_radius_km) * 0.5)
        else:
            excess = distance_km - max_radius_km
            # ✅ Fixed: Added missing subtraction operator (-)
            penalty_factor = max(0.0, 1.0 - (excess / max_radius_km))

            # ✅ Fixed: Added missing multiplication operator (*)
            return weight * 0.2 * penalty_factor

    @staticmethod
    def score_condition_years(user_min_used: Optional[int], prov_years_used: Optional[int],
                              weight: float = 15.0) -> float:
        """
        Scores vehicle condition / years used (10% to 15% weight allocation).
        - If user_min_used is 0, seeker requires a brand new vehicle (years_vehicle_used == 0).
        - If user_min_used > 0, evaluates proximity to preferred used vehicle age.
        """
        if user_min_used is None:
            return weight * 0.5  # Neutral score if unspecified

        u_min = int(user_min_used)
        p_years = int(prov_years_used) if prov_years_used is not None else 0

        # Seeker requires a brand new vehicle (min_used_years == 0)
        if u_min == 0:
            if p_years == 0:
                return weight  # Perfect match: brand new (15.0 points)
            else:
                # Progressive penalty for offering a used vehicle when seeker wanted brand new
                if p_years == 1:
                    return weight * 0.70
                elif p_years <= 2:
                    return weight * 0.40
                elif p_years <= 5:
                    return weight * 0.20
                else:
                    return weight * 0.10

        # Seeker is looking for a used vehicle (min_used_years > 0)
        else:
            if p_years == 0:
                # Brand new vehicle when seeker wanted used
                return weight * 0.75

            diff = abs(p_years - u_min)
            if diff == 0:
                return weight  # Exact match on years used
            elif diff == 1:
                return weight * 0.85
            elif diff <= 3:
                return weight * 0.60
            else:
                return weight * 0.30

    @staticmethod
    def parse_mmy(mmy_str: str) -> Tuple[str, Optional[int]]:
        """Extracts clean base make/model string and 4-digit model year."""
        if not mmy_str:
            return "", None

        mmy_clean = mmy_str.strip()

        match = re.search(r'\b(19\d{2}|20\d{2})\b', mmy_clean)
        year = int(match.group(1)) if match else None

        base_mmy = re.sub(r'\b(19\d{2}|20\d{2})\b', '', mmy_clean)
        base_mmy = re.sub(r'[^\w\s]', ' ', base_mmy)
        base_mmy = re.sub(r'\s+', ' ', base_mmy).strip().lower()

        return base_mmy, year

    @classmethod
    def get_canonical_category(cls, veh_str: str) -> Optional[str]:
        """Maps raw category, property_use, or descriptive vehicle strings to canonical categories."""
        if not veh_str:
            return None

        raw_clean = str(veh_str).lower().strip()
        clean = re.sub(r'^vtype_', '', raw_clean)
        clean_no_punct = re.sub(r'[^\w\s]', ' ', clean)
        clean_no_punct = re.sub(r'\s+', ' ', clean_no_punct).strip()

        if not clean_no_punct:
            return None

        # 1. Direct canonical category match
        if raw_clean in cls.CANONICAL_CATEGORIES:
            return raw_clean
        if clean_no_punct in cls.CANONICAL_CATEGORIES:
            return clean_no_punct

        # 2. Check exact match in category synonym sets
        for cat, syns in cls.VEHICLE_TYPE_SYNONYMS.items():
            if raw_clean in syns or clean_no_punct in syns:
                return cat

        # 3. Check phrase and model keyword synonyms
        for cat, syns in cls.VEHICLE_TYPE_SYNONYMS.items():
            for syn in syns:
                if syn in clean_no_punct or syn in raw_clean:
                    return cat

        # 4. Check token-based word boundary matching
        tokens = set(clean_no_punct.split())
        for cat, syns in cls.VEHICLE_TYPE_SYNONYMS.items():
            for syn in syns:
                if ' ' not in syn and syn in tokens:
                    return cat

        return None

    @classmethod
    def resolve_category(cls, record: Dict[str, Any]) -> Optional[str]:
        """Resolves canonical category from category-level and detail fields across schema variants."""
        if not record:
            return None

        for key in ('category', 'property_use', 'vehicle_category', 'type'):
            val = safe_str(record.get(key))
            if val:
                cat = cls.get_canonical_category(val)
                if cat:
                    return cat

        for key in ('vehicle_type', 'make_model_year', 'preferred_make_model_year', 'make_model'):
            val = safe_str(record.get(key))
            if val:
                cat = cls.get_canonical_category(val)
                if cat:
                    return cat

        return None

    @classmethod
    def resolve_specific_vehicle(cls, record: Dict[str, Any]) -> str:
        """Resolves specific make/model/year string from detail-level fields."""
        if not record:
            return ""

        for key in ('make_model_year', 'preferred_make_model_year', 'make_model', 'brand_model'):
            val = safe_str(record.get(key))
            if val:
                return val

        mmy = _format_mmy(record)
        if mmy:
            return mmy

        return safe_str(record.get('vehicle_type'))

    @classmethod
    def score_mmy(cls, user_mmy: str, prov_mmy: str, weight: float = 20.0) -> float:
        """Scores Make/Model and Year independently with flexible penalty for cross-model matching."""
        u_clean = (user_mmy or "").strip().lower()
        p_clean = (prov_mmy or "").strip().lower()

        if not u_clean or not p_clean or u_clean in cls.WILDCARD_TERMS or p_clean in cls.WILDCARD_TERMS:
            return weight * 0.40

        user_base, user_year = cls.parse_mmy(user_mmy)
        prov_base, prov_year = cls.parse_mmy(prov_mmy)

        # 1. Base Make & Model Scoring (80% of MMY Weight)
        mm_weight = weight * 0.80
        mm_score = mm_weight * 0.15

        if user_base and prov_base:
            if user_base == prov_base or BaseScorer.ilike_match(prov_base, user_base):
                mm_score = mm_weight
            else:
                u_tokens = set(user_base.split())
                p_tokens = set(prov_base.split())
                if u_tokens and p_tokens:
                    overlap = len(u_tokens & p_tokens) / max(len(u_tokens), len(p_tokens))
                    mm_score = max(mm_weight * 0.15, mm_weight * overlap)

        # 2. Year Proximity Scoring (20% of MMY Weight)
        year_weight = weight * 0.20
        year_score = year_weight * 0.30

        if user_year is not None and prov_year is not None:
            diff = abs(user_year - prov_year)
            if diff == 0:
                year_score = year_weight
            elif diff <= 2:
                year_score = year_weight * 0.70
            elif diff <= 5:
                year_score = year_weight * 0.40
            else:
                year_score = year_weight * 0.15

        return mm_score + year_score

    @classmethod
    def calculate_score(cls, raw_user_prefs: Dict[str, Any], raw_provider_data: Dict[str, Any]) -> float:
        user_prefs = normalize_record(raw_user_prefs, is_seeker=True)
        provider_data = normalize_record(raw_provider_data, is_seeker=False)

        # 1. Vertical Type Check
        prov_vertical = safe_str(provider_data.get('vertical_type')).lower()
        if prov_vertical and prov_vertical != 'vehicle':
            return 0.0

        # 2. Normalized Transaction Type Check
        user_tx = safe_str(user_prefs.get('transaction_type'))
        prov_tx = safe_str(provider_data.get('transaction_type'))
        if not cls.is_transaction_compatible(user_tx, prov_tx):
            return 0.0

        # 3. Currency Check
        s_curr = safe_str(user_prefs.get('currency'), 'ETB').upper()
        p_curr = safe_str(provider_data.get('currency'), 'ETB').upper()
        if BaseScorer.is_currency_mismatch(s_curr, p_curr):
            return 0.0

        # 4. Strict Category Filtering (sedan, suv, van, bus, truck, moto)
        user_cat = cls.resolve_category(user_prefs)
        prov_cat = cls.resolve_category(provider_data)

        if user_cat and prov_cat and user_cat != prov_cat:
            return 0.0

        score = 0.0

        # 5. Budget Scoring (Max 25.0)
        cost = safe_float(provider_data.get('price'))
        budget = safe_float(user_prefs.get('max_budget') or user_prefs.get('budget'))
        if budget > 0:
            score += BaseScorer.score_budget(cost, budget, weight=25.0, seeker_currency=s_curr,
                                             provider_currency=p_curr)
        else:
            score += 20.0  # Neutral score when seeker budget is unspecified

        # 6. Specific Make / Model / Year Scoring (Max 20.0)
        user_spec = cls.resolve_specific_vehicle(user_prefs)
        prov_spec = cls.resolve_specific_vehicle(provider_data)
        score += cls.score_mmy(user_spec, prov_spec, weight=20.0)

        # 7. Fuel Type Scoring (Max 12.5)
        user_fuel = safe_str(user_prefs.get('fuel_type')).lower()
        prov_fuel = safe_str(provider_data.get('fuel_type')).lower()
        fuel_weight = 12.5

        if user_fuel and prov_fuel and user_fuel not in cls.IGNORED_FUELS and prov_fuel not in cls.IGNORED_FUELS:
            if BaseScorer.ilike_match(prov_fuel, user_fuel):
                score += fuel_weight
            else:
                score += fuel_weight * 0.80
        else:
            score += fuel_weight * 0.50

        # 8. Amenities / Features Scoring (Max 12.5)
        req_amenities = safe_list(user_prefs.get('required_amenities') or user_prefs.get('amenities'))
        prov_amenities_str = safe_str(provider_data.get('amenities'))
        score += BaseScorer.score_features(req_amenities, prov_amenities_str, weight=12.5)

        # 9. Vehicle Condition / Years Used Scoring (Max 15.0 / 15%)
        user_min_used = None
        if 'min_used_years' in user_prefs and user_prefs['min_used_years'] is not None:
            try:
                user_min_used = int(user_prefs['min_used_years'])
            except (ValueError, TypeError):
                pass

        prov_years_used = 0
        if 'years_vehicle_used' in provider_data and provider_data['years_vehicle_used'] is not None:
            try:
                prov_years_used = int(provider_data['years_vehicle_used'])
            except (ValueError, TypeError):
                pass

        score += cls.score_condition_years(user_min_used, prov_years_used, weight=15.0)

        # 10. Location & Proximity Matching (Max 15.0)
        user_lat = user_prefs.get('latitude')
        user_lon = user_prefs.get('longitude')
        prov_lat = provider_data.get('latitude')
        prov_lon = provider_data.get('longitude')

        if all(v is not None for v in [user_lat, user_lon, prov_lat, prov_lon]):
            distance_km = cls.calculate_distance(
                safe_float(user_lat), safe_float(user_lon),
                safe_float(prov_lat), safe_float(prov_lon)
            )
            max_radius = safe_float(user_prefs.get('max_distance_km'), 3000.0)
            score += cls.score_distance(distance_km, max_radius, 15.0)
        else:
            u_loc = safe_str(
                user_prefs.get('location') or user_prefs.get('city') or user_prefs.get('region')).lower().strip()
            p_loc = safe_str(provider_data.get('location') or provider_data.get('city') or provider_data.get(
                'region')).lower().strip()

            if u_loc and p_loc:
                if u_loc == p_loc or u_loc in p_loc or p_loc in u_loc:
                    score += 15.0
                else:
                    score += 7.5
            else:
                score += 10.0

        return min(round(score, 2), 100.0)


class FreightScorer(BaseScorer):
    CITY_SYNONYMS = SHARED_CITY_SYNONYMS
    LOCATION_STOPWORDS = SHARED_LOCATION_STOPWORDS

    FREIGHT_STOPWORDS = {
        "service", "services", "transport", "transportation", "freight", "cargo",
        "van", "delivery", "hauling", "trailer", "truck", "rental", "lease",
        "logistics", "shipping", "carrier", "vehicle", "auto", "car"
    }

    @staticmethod
    def ilike_match(str1: str, str2: str) -> bool:
        """
        Universally robust matching for any freight, truck, or trailer type across
        complex descriptive variants and service terminology.
        """
        if not str1 or not str2:
            return False

        def normalize(s: str) -> str:
            cleaned = "".join(c if c.isalnum() or c.isspace() else " " for c in s.lower())
            return " ".join(cleaned.split())

        norm1 = normalize(str1)
        norm2 = normalize(str2)

        if norm1 == norm2 or norm1 in norm2 or norm2 in norm1:
            return True

        tokens1 = set(norm1.split())
        tokens2 = set(norm2.split())

        if not tokens1 or not tokens2:
            return False

        # Isolate core equipment/vehicle identifiers by filtering out general logistics stopwords
        core1 = {t for t in tokens1 if t not in FreightScorer.FREIGHT_STOPWORDS}
        core2 = {t for t in tokens2 if t not in FreightScorer.FREIGHT_STOPWORDS}

        if core1 and core2:
            if core1.issubset(core2) or core2.issubset(core1):
                return True
            core_common = core1.intersection(core2)
            smaller_core = min(len(core1), len(core2))
            if smaller_core > 0 and (len(core_common) / smaller_core >= 0.6):
                return True

        common = tokens1.intersection(tokens2)
        smaller_size = min(len(tokens1), len(tokens2))
        match_ratio = len(common) / smaller_size if smaller_size > 0 else 0.0

        return match_ratio >= 0.6 or tokens1.issubset(tokens2) or tokens2.issubset(tokens1)

    @staticmethod
    def extract_weight(record: Dict[str, Any]) -> float:
        weight = safe_float(
            record.get('cargo_weight_kgs')
            or record.get('weight_kgs')
            or record.get('max_weight_kgs')
            or record.get('capacity_kgs')
        )
        if weight > 0:
            return weight

        text = f"{record.get('amenities', '')} {record.get('required_amenities', '')}"
        match = re.search(r'(?:weight|kg|cargo):\s*([\d,]+\.?\d*)\s*kg?', text, re.IGNORECASE)
        if match:
            try:
                return float(match.group(1).replace(',', ''))
            except ValueError:
                pass
        return 0.0

    @staticmethod
    async def _async_haversine_freight_fallback(
            start_lat: float, start_lon: float, end_lat: float, end_lon: float, max_truck_speed_mph: float = 55.0
    ) -> Dict[str, Any]:
        """Asynchronous robust Haversine distance and HOS-compliant ETA fallback for freight."""
        try:
            lat1, lon1, lat2, lon2 = map(float, [start_lat, start_lon, end_lat, end_lon])
            if (lat1 == 0.0 and lon1 == 0.0) or (lat2 == 0.0 and lon2 == 0.0):
                distance_miles = 50.0
            else:
                r1, l1, r2, l2 = map(math.radians, [lat1, lon1, lat2, lon2])
                dlat = r2 - r1
                dlon = l2 - l1
                a = math.sin(dlat / 2) ** 2 + math.cos(r1) * math.cos(r2) * math.sin(dlon / 2) ** 2
                distance_miles = 3958.8 * (2 * math.asin(math.sqrt(a)))
        except (ValueError, TypeError):
            distance_miles = 50.0

        freight_hours = distance_miles / max_truck_speed_mph
        hos_break_minutes = math.floor(freight_hours / 8) * 30 if freight_hours > 8 else 0
        total_freight_minutes = (freight_hours * 60) + hos_break_minutes
        final_eta = datetime.datetime.now() + datetime.timedelta(minutes=total_freight_minutes)

        return {
            "status": "Success",
            "distance_miles": round(distance_miles, 1),
            "pure_driving_hours": round(freight_hours, 1),
            "legal_breaks_added_minutes": hos_break_minutes,
            "total_travel_minutes": round(total_freight_minutes),
            "estimated_arrival": final_eta.strftime("%Y-%m-%d %I:%M %p"),
        }

    @staticmethod
    async def get_osrm_freight_eta(
            start_lat: float, start_lon: float, end_lat: float, end_lon: float, max_truck_speed_mph: float = 55.0
    ) -> Dict[str, Any]:
        cache_key = f"osrm:freight:v2:{start_lat}:{start_lon}:{end_lat}:{end_lon}:{max_truck_speed_mph}"

        client = globals().get('redis_client') or (redis_client if 'redis_client' in globals() else None)
        if client:
            try:
                cached_data = await client.get(cache_key)
                if cached_data:
                    return json.loads(cached_data)
            except Exception as e:
                logger.debug(f"Cache read failed for freight route: {e}")

        # Validate coordinates before OSRM request
        if not (start_lat and start_lon and end_lat and end_lon) or (
                float(start_lat) == 0.0 and float(start_lon) == 0.0):
            return await FreightScorer._async_haversine_freight_fallback(start_lat, start_lon, end_lat, end_lon,
                                                                         max_truck_speed_mph)

        coordinates = f"{start_lon},{start_lat};{end_lon},{end_lat}"
        url = f"http://project-osrm.org/route/v1/driving/{coordinates}?overview=false"

        def _fetch():
            return requests.get(url, timeout=2.5)  # Fast 2.5s timeout

        success_data = None
        try:
            response = await asyncio.to_thread(_fetch)
            if response.status_code == 200:
                data = response.json()
                if data.get("code") == "Ok" and data.get("routes"):
                    success_data = data["routes"][0]
            else:
                logger.debug(f"OSRM HTTP status {response.status_code} for freight. Using Haversine fallback.")
        except Exception as e:
            logger.debug(f"OSRM commercial routing engine failure ({e}). Falling back to Haversine estimation.")

        if success_data:
            raw_car_seconds = success_data["duration"]
            distance_miles = success_data["distance"] * 0.000621371

            avg_car_speed_mph = (distance_miles / (raw_car_seconds / 3600)) if raw_car_seconds > 0 else 1.0
            if avg_car_speed_mph > max_truck_speed_mph:
                freight_hours = distance_miles / max_truck_speed_mph
            else:
                freight_hours = (raw_car_seconds / 3600) * 1.15

            hos_break_minutes = math.floor(freight_hours / 8) * 30 if freight_hours > 8 else 0
            total_freight_minutes = (freight_hours * 60) + hos_break_minutes
            final_eta = datetime.datetime.now() + datetime.timedelta(minutes=total_freight_minutes)

            result = {
                "status": "Success",
                "distance_miles": round(distance_miles, 1),
                "pure_driving_hours": round(freight_hours, 1),
                "legal_breaks_added_minutes": hos_break_minutes,
                "total_travel_minutes": round(total_freight_minutes),
                "estimated_arrival": final_eta.strftime("%Y-%m-%d %I:%M %p"),
            }
        else:
            result = await FreightScorer._async_haversine_freight_fallback(start_lat, start_lon, end_lat, end_lon,
                                                                           max_truck_speed_mph)

        if client:
            try:
                ttl = 86400 if success_data else 3600
                await client.setex(cache_key, ttl, json.dumps(result))
            except Exception as e:
                logger.debug(f"Cache write failed for freight route: {e}")

        return result

    @classmethod
    async def calculate_score(cls, shipper_prefs: Dict[str, Any], provider_data: Dict[str, Any]) -> float:
        s_curr = shipper_prefs.get('currency', 'ETB')
        p_curr = provider_data.get('currency', 'ETB')
        if cls.is_currency_mismatch(s_curr, p_curr):
            return 0.0

        prov_type = safe_str(provider_data.get('property_use') or provider_data.get('truck_type'))
        req_type = safe_str(shipper_prefs.get('property_use') or shipper_prefs.get('required_truck_type'))

        if req_type and req_type.lower() not in ('any', 'all', 'general', '') and \
                prov_type and prov_type.lower() not in ('any', 'all', ''):
            if not cls.ilike_match(req_type, prov_type):
                return 0.0

        req_weight = cls.extract_weight(shipper_prefs)
        prov_capacity = cls.extract_weight(provider_data)
        if 0 < prov_capacity < req_weight:
            return 0.0

        req_cargo_type = safe_str(shipper_prefs.get('cargo_type', 'general'))
        if cls.ilike_match(req_cargo_type, 'hazardous') and not provider_data.get('has_hazmat', False) or \
                provider_data.get('has_hazmat_license', False):
            return 0.0

        s_orig_raw = safe_str(shipper_prefs.get('origin_text') or shipper_prefs.get('origin') or shipper_prefs.get(
            'origin_loc') or shipper_prefs.get('location_text'))
        s_dest_raw = safe_str(
            shipper_prefs.get('destination_text') or shipper_prefs.get('destination') or shipper_prefs.get('dest_loc'))

        p_orig_raw = safe_str(
            provider_data.get('origin_text') or provider_data.get('origin') or provider_data.get('location_text'))
        p_dest_raw = safe_str(provider_data.get('destination_text') or provider_data.get('destination'))

        def parse_route_string(raw_text: str, explicit_dest: str) -> Tuple[str, str]:
            if not raw_text:
                return "", explicit_dest

            text = raw_text
            for prefix in ["shipper request selected:", "freight route:", "route:", "trip:", "from:", "cargo:"]:
                if text.lower().startswith(prefix):
                    text = text[len(prefix):].strip()

            if explicit_dest:
                return text, explicit_dest

            delimiters = [" ➔ ", " -> ", " - ", " to ", " እስከ "]
            for delim in delimiters:
                if delim in text:
                    parts = text.split(delim, 1)
                    return parts[0].strip(), parts[1].strip()

            return text, ""

        s_orig_text, s_dest_text = parse_route_string(s_orig_raw, s_dest_raw)
        p_orig_text, p_dest_text = parse_route_string(p_orig_raw, p_dest_raw)

        def clean_and_tokenize(loc: str) -> set:
            if not loc:
                return set()
            cleaned = "".join(c if c.isalnum() or c.isspace() else " " for c in loc.lower())
            words = set(cleaned.split())
            return {w for w in words if len(w) > 2 and w not in cls.LOCATION_STOPWORDS}

        def canonicalize_location(loc: str) -> str:
            if not loc:
                return ""
            loc_lower = loc.lower().strip()
            for syn, canonical in cls.CITY_SYNONYMS.items():
                if syn == loc_lower or syn in loc_lower.split():
                    return canonical
            return loc_lower

        def lexical_location_match(loc1: str, loc2: str) -> float:
            if not loc1 or not loc2:
                return 0.0
            c1 = canonicalize_location(loc1)
            c2 = canonicalize_location(loc2)
            if c1 == c2 and c1 != "":
                return 1.0

            words1 = clean_and_tokenize(loc1)
            words2 = clean_and_tokenize(loc2)
            if not words1 or not words2:
                return 0.0

            return 1.0 if words1.intersection(words2) else 0.0

        orig_lexical = lexical_location_match(s_orig_text, p_orig_text) > 0.0
        dest_lexical = lexical_location_match(s_dest_text, p_dest_text) > 0.0

        orig_sim = cls.calculate_text_similarity(s_orig_text, p_orig_text) if (s_orig_text and p_orig_text) else 0.0
        dest_sim = cls.calculate_text_similarity(s_dest_text, p_dest_text) if (s_dest_text and p_dest_text) else 0.0

        origin_explicitly_different = bool(s_orig_text and p_orig_text and not orig_lexical and orig_sim < 0.85)
        dest_explicitly_different = bool(s_dest_text and p_dest_text and not dest_lexical and dest_sim < 0.85)
        if origin_explicitly_different and dest_explicitly_different:
            return 0.0

        prov_region = safe_str(
            provider_data.get('operating_region') or provider_data.get('location_text') or provider_data.get(
                'service_area'))
        region_orig_match = lexical_location_match(s_orig_text, prov_region) > 0.0
        region_dest_match = lexical_location_match(s_dest_text, prov_region) > 0.0

        t_lat = provider_data.get('origin_lat') or provider_data.get('latitude')
        t_lon = provider_data.get('origin_lon') or provider_data.get('longitude')

        d_lat = shipper_prefs.get('destination_lat') or shipper_prefs.get('latitude')
        d_lon = shipper_prefs.get('destination_lon') or shipper_prefs.get('longitude')

        coord_match_success = False
        freight_eta = None

        def is_valid_coord(lat, lon):
            return lat is not None and lon is not None and not (float(lat) == 0.0 and float(lon) == 0.0)

        if is_valid_coord(t_lat, t_lon) and is_valid_coord(d_lat, d_lon):
            max_speed = safe_float(provider_data.get('max_legal_speed_mph', 55.0))
            freight_eta = await cls.get_osrm_freight_eta(t_lat, t_lon, d_lat, d_lon, max_speed)
            if freight_eta and freight_eta.get("status") == "Success":
                coord_match_success = True

        origin_is_common = orig_lexical or orig_sim >= 0.85 or coord_match_success
        dest_is_common = dest_lexical or dest_sim >= 0.85 or coord_match_success

        is_semantic_fallback = shipper_prefs.get('allow_semantic_fallback', False)
        if not is_semantic_fallback and not origin_is_common and not dest_is_common:
            return 0.0

        if not origin_is_common and not dest_is_common:
            return 0.0

        if dest_is_common and s_orig_text and p_orig_text and not origin_is_common:
            return 0.0

        if origin_is_common and s_dest_text and p_dest_text and not dest_is_common:
            return 0.0

        score = 0.0
        eta_days = 999.0

        if coord_match_success and freight_eta:
            eta_days = freight_eta["total_travel_minutes"] / 1440.0
            provider_data['computed_freight_metrics'] = freight_eta
            score += 20.0

        score += (10.0 if orig_lexical else (10.0 * orig_sim))
        score += (10.0 if dest_lexical else (10.0 * dest_sim))

        req_region = safe_str(shipper_prefs.get('operating_region') or shipper_prefs.get('required_region'))
        if req_region and prov_region:
            region_sim = cls.calculate_text_similarity(req_region, prov_region)
            score += (15.0 * region_sim)
        elif region_orig_match or region_dest_match:
            score += 15.0

        max_days = safe_float(shipper_prefs.get('max_delivery_days', 0))
        if eta_days != 999.0:
            if max_days > 0 and eta_days <= max_days:
                score += 30.0
            elif max_days > 0 and (eta_days - max_days) <= 2:
                score += 15.0
            elif max_days == 0:
                score += 15.0
        else:
            score += 15.0

        cost = safe_float(provider_data.get('price'))
        budget = safe_float(shipper_prefs.get('max_budget'))
        score += cls.score_budget(cost, budget, 30.0, s_curr, p_curr)

        rating = safe_float(provider_data.get('carrier_rating', 0.0))
        if rating >= 4.5:
            score += 20.0
        elif rating >= 4.0:
            score += 10.0

        return min(round(score, 2), 100.0)


class RideScorer(BaseScorer):
    CITY_SYNONYMS = SHARED_CITY_SYNONYMS
    LOCATION_STOPWORDS = SHARED_LOCATION_STOPWORDS

    TIER_MAPPINGS = {
        "economy": {
            "english": ["economy", "budget", "standard", "basic", "saving"],
            "amharic": ["ኢኮኖሚ", "መደበኛ", "መሠረታዊ", "ተመጣጣኝ"],
            "oromo": ["diinagdee", "sadarkaa_jalqabaa", "salphaa", "qusannoo", "ikonomii"]
        },
        "comfort": {
            "english": ["comfort", "comfortable", "medium", "intermediate", "family"],
            "amharic": ["ኮምፎርት", "ምቹ", "መካከለኛ", "ቤተሰብ"],
            "oromo": ["mijataa", "giddu_galeessa", "maatii", "tolchaa", "koomfortii"]
        },
        "luxury": {
            "english": ["luxury", "premium", "vip", "executive", "high-end", "deluxe"],
            "amharic": ["ቅንጡ", "ፕሪሚየም", "ቪአይፒ", "የልዩ", "ሉክስ"],
            "oromo": ["qananii", "premium", "vip", "gama_olmaa", "addaa", "minibaasii"]
        }
    }

    @classmethod
    def extract_vehicle_tier(cls, title_text: str) -> str:
        if not title_text:
            return ""
        text_lower = str(title_text).lower()
        cleaned_text = re.sub(r'[^\w\s]', ' ', text_lower).replace('_', ' ')

        for tier, categories in cls.TIER_MAPPINGS.items():
            for lang, keywords in categories.items():
                for kw in keywords:
                    kw_normalized = kw.replace("_", " ")
                    if re.search(r'\b' + re.escape(kw_normalized) + r'\b', cleaned_text):
                        return tier
        return ""

    @classmethod
    def ilike_match(cls, str1: str, str2: str) -> bool:
        if not str1 or not str2:
            return False

        tier1 = cls.extract_vehicle_tier(str1)
        tier2 = cls.extract_vehicle_tier(str2)

        if tier1 and tier2 and tier1 != tier2:
            return False

        def normalize(s: str) -> str:
            cleaned = "".join(c if c.isalnum() or c.isspace() or c == '_' else " " for c in s.lower())
            cleaned = cleaned.replace('_', ' ')
            words = cleaned.split()
            all_tier_words = {kw for cat in cls.TIER_MAPPINGS.values() for lang_kw in cat.values() for kw in lang_kw}
            filtered_words = [w for w in words if w not in all_tier_words]
            return " ".join(filtered_words if filtered_words else words)

        norm1 = normalize(str1)
        norm2 = normalize(str2)

        if norm1 == norm2 or norm1 in norm2 or norm2 in norm1:
            return True

        tokens1 = set(norm1.split())
        tokens2 = set(norm2.split())

        if not tokens1 or not tokens2:
            return False

        common = tokens1.intersection(tokens2)
        union = tokens1.union(tokens2)
        jaccard_sim = len(common) / len(union) if len(union) > 0 else 0.0
        smaller_size = min(len(tokens1), len(tokens2))
        match_ratio = len(common) / smaller_size if smaller_size > 0 else 0.0

        return match_ratio >= 0.6 or jaccard_sim >= 0.4 or tokens1.issubset(tokens2) or tokens2.issubset(tokens1)

    @staticmethod
    def extract_seats(record: Dict[str, Any]) -> int:
        seats = int(
            safe_float(record.get('party_size') or record.get('seats_available') or record.get('available_seats')))
        if seats > 0:
            return seats

        text = f"{record.get('amenities', '')} {record.get('required_amenities', '')}"
        match = re.search(r'(?:seats|party size|capacity):\s*(\d+)', text, re.IGNORECASE)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                pass
        return 0

    @staticmethod
    async def _async_haversine_ride_fallback(
            start_lat: float, start_lon: float, end_lat: float, end_lon: float, avg_speed_kmh: float = 40.0
    ) -> Dict[str, Any]:
        """Asynchronous robust Haversine distance and duration fallback for passenger rides."""
        try:
            lat1, lon1, lat2, lon2 = map(float, [start_lat, start_lon, end_lat, end_lon])
            if (lat1 == 0.0 and lon1 == 0.0) or (lat2 == 0.0 and lon2 == 0.0):
                km = 15.0
            else:
                r1, l1, r2, l2 = map(math.radians, [lat1, lon1, lat2, lon2])
                dlat = r2 - r1
                dlon = l2 - l1
                a = math.sin(dlat / 2) ** 2 + math.cos(r1) * math.cos(r2) * math.sin(dlon / 2) ** 2
                km = 6371 * (2 * math.asin(math.sqrt(a)))
        except (ValueError, TypeError):
            km = 15.0

        duration_minutes = round((km / avg_speed_kmh) * 60)
        eta_time = datetime.datetime.now() + datetime.timedelta(minutes=duration_minutes)

        return {
            "status": "Success",
            "duration_minutes": duration_minutes,
            "arrival_time": eta_time.strftime("%I:%M %p"),
        }

    @staticmethod
    async def get_osrm_eta(start_lat: float, start_lon: float, end_lat: float, end_lon: float) -> Dict[str, Any]:
        cache_key = f"osrm:ride:v2:{start_lat}:{start_lon}:{end_lat}:{end_lon}"

        client = globals().get('redis_client') or redis_client
        if client:
            try:
                cached_data = await client.get(cache_key)
                if cached_data:
                    return json.loads(cached_data)
            except Exception as e:
                logger.debug(f"Cache read failed for ride route: {e}")

        # Validate coordinates before OSRM request
        if not (start_lat and start_lon and end_lat and end_lon) or (float(start_lat) == 0.0 and float(start_lon) == 0.0):
            return await RideScorer._async_haversine_ride_fallback(start_lat, start_lon, end_lat, end_lon)

        coordinates = f"{start_lon},{start_lat};{end_lon},{end_lat}"
        url = f"http://project-osrm.org/route/v1/driving/{coordinates}?overview=false"

        def _fetch():
            return requests.get(url, timeout=2.5)  # Fast 2.5s timeout

        success_data = None
        try:
            response = await asyncio.to_thread(_fetch)
            if response.status_code == 200:
                data = response.json()
                if data.get("code") == "Ok" and data.get("routes"):
                    success_data = data["routes"][0]
            else:
                logger.debug(f"OSRM HTTP status {response.status_code} for ride route. Using Haversine fallback.")
        except Exception as e:
            logger.debug(f"OSRM API routing failure for ride ETA ({e}). Falling back to Haversine estimation.")

        if success_data:
            duration_seconds = success_data["duration"]
            duration_minutes = round(duration_seconds / 60)
            eta_time = datetime.datetime.now() + datetime.timedelta(seconds=duration_seconds)

            result = {
                "status": "Success",
                "duration_minutes": duration_minutes,
                "arrival_time": eta_time.strftime("%I:%M %p"),
            }
        else:
            result = await RideScorer._async_haversine_ride_fallback(start_lat, start_lon, end_lat, end_lon)

        if client:
            try:
                ttl = 86400 if success_data else 3600
                await client.setex(cache_key, ttl, json.dumps(result))
            except Exception:
                pass

        return result

    @classmethod
    async def calculate_score(cls, passenger_prefs: Dict[str, Any], provider_data: Dict[str, Any]) -> float:
        s_curr = passenger_prefs.get('currency', 'ETB')
        p_curr = provider_data.get('currency', 'ETB')
        if cls.is_currency_mismatch(s_curr, p_curr):
            return 0.0

        prov_type = safe_str(provider_data.get('property_use') or provider_data.get('vehicle_type'))
        user_type = safe_str(passenger_prefs.get('property_use') or passenger_prefs.get('vehicle_type'))

        if user_type and not cls.ilike_match(user_type, prov_type):
            return 0.0

        needed_seats = passenger_prefs.get('seats_needed', 1)
        available_seats = cls.extract_seats(provider_data)
        if available_seats < needed_seats:
            return 0.0

        p_dep_raw = safe_str(
            passenger_prefs.get('departure_text') or passenger_prefs.get('origin_text') or passenger_prefs.get(
                'location_text')
        )
        d_dep_raw = safe_str(
            provider_data.get('departure_text') or provider_data.get('origin_text') or provider_data.get(
                'location_text')
        )
        p_dest_raw = safe_str(passenger_prefs.get('destination_text'))
        d_dest_raw = safe_str(provider_data.get('destination_text'))

        def parse_route_string(raw_text: str, explicit_dest: str) -> Tuple[str, str]:
            if not raw_text:
                return "", explicit_dest

            text = raw_text
            for prefix in ["seeker request selected:", "transport route:", "route:", "trip:", "from:"]:
                if text.lower().startswith(prefix):
                    text = text[len(prefix):].strip()

            if explicit_dest:
                return text, explicit_dest

            delimiters = [" ➔ ", " -> ", " - ", " to ", " እስከ "]
            for delim in delimiters:
                if delim in text:
                    parts = text.split(delim, 1)
                    return parts[0].strip(), parts[1].strip()

            return text, ""

        p_dep_text, p_dest_text = parse_route_string(p_dep_raw, p_dest_raw)
        d_dep_text, d_dest_text = parse_route_string(d_dep_raw, d_dest_raw)

        def clean_and_tokenize(loc: str) -> set:
            if not loc:
                return set()
            cleaned = "".join(c if c.isalnum() or c.isspace() else " " for c in loc.lower())
            words = set(cleaned.split())
            return {w for w in words if len(w) > 2 and w not in cls.LOCATION_STOPWORDS}

        def canonicalize_location(loc: str) -> str:
            if not loc:
                return ""
            loc_lower = loc.lower().strip()
            for syn, canonical in cls.CITY_SYNONYMS.items():
                if syn == loc_lower or syn in loc_lower.split():
                    return canonical
            return loc_lower

        def lexical_location_match(loc1: str, loc2: str) -> float:
            if not loc1 or not loc2:
                return 0.0
            c1 = canonicalize_location(loc1)
            c2 = canonicalize_location(loc2)
            if c1 == c2 and c1 != "":
                return 1.0

            words1 = clean_and_tokenize(loc1)
            words2 = clean_and_tokenize(loc2)
            if not words1 or not words2:
                return 0.0

            return 1.0 if words1.intersection(words2) else 0.0

        origin_lexical = lexical_location_match(p_dep_text, d_dep_text) > 0.0
        dest_lexical = lexical_location_match(p_dest_text, d_dest_text) > 0.0

        dep_sim = cls.calculate_text_similarity(p_dep_text, d_dep_text) if (p_dep_text and d_dep_text) else 0.0
        dest_sim = cls.calculate_text_similarity(p_dest_text, d_dest_text) if (p_dest_text and d_dest_text) else 0.0

        reverse_lexical = (lexical_location_match(p_dep_text, d_dest_text) > 0.0 and lexical_location_match(p_dest_text, d_dep_text) > 0.0)

        origin_explicitly_different = bool(p_dep_text and d_dep_text and not origin_lexical and not reverse_lexical and dep_sim < 0.85)
        dest_explicitly_different = bool(p_dest_text and d_dest_text and not dest_lexical and not reverse_lexical and dest_sim < 0.85)
        if origin_explicitly_different and dest_explicitly_different:
            return 0.0

        p_dep_lat = passenger_prefs.get('departure_lat') or passenger_prefs.get('latitude')
        p_dep_lon = passenger_prefs.get('departure_lon') or passenger_prefs.get('longitude')

        d_dep_lat = provider_data.get('departure_lat') or provider_data.get('latitude')
        d_dep_lon = provider_data.get('departure_lon') or provider_data.get('longitude')

        coord_match_success = False
        eta_res = None

        def is_valid_coord(lat, lon):
            return lat is not None and lon is not None and not (float(lat) == 0.0 and float(lon) == 0.0)

        if is_valid_coord(p_dep_lat, p_dep_lon) and is_valid_coord(d_dep_lat, d_dep_lon):
            eta_res = await cls.get_osrm_eta(d_dep_lat, d_dep_lon, p_dep_lat, p_dep_lon)
            if eta_res and eta_res.get("status") == "Success" and eta_res.get("duration_minutes", 999) < 30:
                coord_match_success = True

        origin_is_common = origin_lexical or reverse_lexical or dep_sim >= 0.85 or coord_match_success
        dest_is_common = dest_lexical or reverse_lexical or dest_sim >= 0.85

        if not origin_is_common and not dest_is_common:
            return 0.0

        score = 0.0

        if coord_match_success and eta_res:
            duration = eta_res["duration_minutes"]
            if duration < 15:
                score += 30.0
            else:
                score += max(0.0, 30.0 - duration)
        else:
            if not origin_lexical and not reverse_lexical and dep_sim < 0.6:
                return 0.0
            score += 30.0 if (origin_lexical or reverse_lexical) else (30.0 * (dep_sim ** 1.5))

        if p_dest_text and d_dest_text:
            if not dest_lexical and not reverse_lexical and dest_sim < 0.6:
                return 0.0
            score += 30.0 if (dest_lexical or reverse_lexical) else (30.0 * (dest_sim ** 1.5))
        else:
            score += 30.0

        req_region = safe_str(passenger_prefs.get('operating_region') or passenger_prefs.get('required_region'))
        prov_region = safe_str(provider_data.get('operating_region') or provider_data.get('location_text'))
        if req_region and prov_region:
            region_sim = cls.calculate_text_similarity(req_region, prov_region)
            score += (10.0 * region_sim)

        cost = safe_float(provider_data.get('price'))
        budget = safe_float(passenger_prefs.get('max_budget'))
        score += cls.score_budget(cost, budget, 30.0, s_curr, p_curr)

        return min(round(score, 2), 100.0)


# ==========================================
# MAIN BIDIRECTIONAL MATCHING ENGINE
# ==========================================
class MatchingEngine:
    def __init__(self, db_manager: Any = None):
        self.db = db_manager
        SemanticMatcher.load_model()

    @staticmethod
    async def evaluate_matches(
            vertical_type: str,
            user_prefs: Dict[str, Any],
            candidates: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Core asynchronous evaluation process handling both sync and async scorers."""
        if not candidates:
            return []

        results = []
        scorer_map = {
            'equipment': EquipmentScorer.calculate_score,
            'housing': HousingScorer.calculate_score,
            'vehicle': VehicleScorer.calculate_score,
            'freight': FreightScorer.calculate_score,
            'ride': RideScorer.calculate_score
        }

        score_func = scorer_map.get(str(vertical_type).lower(), EquipmentScorer.calculate_score)

        for cand in candidates:
            try:
                res = score_func(user_prefs, cand)

                if inspect.isawaitable(res):
                    score = await res
                else:
                    score = res

                score = float(score) if score is not None else 0.0

                if score > 0:
                    cand['_match_score'] = score
                    results.append(cand)
            except Exception as e:
                logger.error(f"Scoring error on candidate {cand.get('id')}: {e}")

        return sorted(results, key=lambda x: x.get('_match_score', 0.0), reverse=True)

    async def run_provider_matching(self, listing_id: str, vertical_type: str, bot_instance: Any = None):
        try:
            if not self.db:
                logger.error("Reverse matching failed: DB context missing.")
                return

            listing = await self.db.get_listing_by_id(listing_id)
            if not listing:
                return

            provider_id = str(listing.get('provider_id'))
            pending_requests = await self.db.fetch_pending_requests(vertical_type=vertical_type)
            if not pending_requests:
                return

            bot = bot_instance if bot_instance else getattr(self.db, 'bot', None)
            if not bot:
                return

            for req in pending_requests:
                seeker_id = str(req.get('seeker_id'))
                if seeker_id == provider_id:
                    continue

                seeker_prefs = {
                    'property_use': req.get('property_use'),
                    'max_budget': req.get('max_budget', 0.0),
                    'currency': req.get('currency', 'ETB'),
                    'min_bedrooms': req.get('min_bedrooms', 0.0),
                    'min_sq_meters': req.get('min_sq_meters', 0.0),
                    'required_amenities': req.get('required_amenities', ''),
                    'preferred_brand': req.get('preferred_brand', 'Any'),
                    'cargo_weight_kgs': req.get('cargo_weight_kgs', 0.0),
                    'party_size': req.get('party_size', 0.0),
                    'min_required_capacity': req.get('min_required_capacity', 0.0)
                }

                matches = await self.evaluate_matches(vertical_type, seeker_prefs, [listing])

                if matches:
                    top_match = matches[0]
                    score = int(top_match.get('_match_score', 0))

                    if score >= 75:
                        brand_title = str(top_match.get('property_use') or top_match.get('brand') or
                                          vertical_type).title()
                        cost = float(top_match.get('price') or top_match.get('cost') or 0.0)
                        match_id = str(top_match.get('id'))

                        # Dynamically fetch currency with fallback to 'ETB'
                        currency = str(
                            top_match.get('currency') or 'ETB'
                        ).upper()

                        msg = (
                            f"🎉 <b>New Match Alert!</b>\n\n"
                            f"Good news! A provider just listed a <b>{brand_title}</b> "
                            f"that matches your pending request.\n\n"
                            f"💵 <b>Rate:</b> {cost:,.2f} {currency}\n"
                            f"🎯 <b>Match Score:</b> {score}%\n\n"
                            f"Would you like to review this match and send an offer?"
                        )

                        keyboard = [
                            [InlineKeyboardButton(f"👀 View {brand_title}", callback_data=f"select_match:{match_id}")],
                            [InlineKeyboardButton("Dismiss", callback_data="ignore_match")]
                        ]

                        try:
                            await bot.send_message(
                                chat_id=int(seeker_id),
                                text=msg,
                                reply_markup=InlineKeyboardMarkup(keyboard),
                                parse_mode="HTML"
                            )
                        except Exception as e:
                            logger.error(f"Failed to push notification to Seeker {seeker_id}: {e}")

        except Exception as e:
            logger.error(f"Failed to execute run_provider_matching: {e}", exc_info=True)

    async def run_seeker_matching(self, request_id: str, vertical_type: str, bot_instance: Any = None):
        try:
            if not self.db:
                logger.error("Forward matching failed: DB context missing.")
                return

            request_data = await self.db.get_seeker_request_by_id(request_id)
            if not request_data:
                return

            seeker_id = str(request_data.get('seeker_id'))
            seeker_prefs = {
                'property_use': request_data.get('property_use'),
                'max_budget': request_data.get('max_budget', 0.0),
                'currency': request_data.get('currency', 'ETB'),
                'min_bedrooms': request_data.get('min_bedrooms', 0.0),
                'min_sq_meters': request_data.get('min_sq_meters', 0.0),
                'required_amenities': request_data.get('required_amenities', ''),
                'preferred_brand': request_data.get('preferred_brand', 'Any'),
                'cargo_weight_kgs': request_data.get('cargo_weight_kgs', 0.0),
                'party_size': request_data.get('party_size', 0.0),
                'min_required_capacity': request_data.get('min_required_capacity', 0.0)
            }

            active_listings = await self.db.fetch_active_listings(vertical_type=vertical_type)
            if not active_listings:
                return

            valid_candidates = [c for c in active_listings if str(c.get('provider_id')) != seeker_id]
            if not valid_candidates:
                return

            matches = await self.evaluate_matches(vertical_type, seeker_prefs, valid_candidates)

            if matches:
                high_quality_matches = [m for m in matches if m.get('_match_score', 0) >= 75]

                if high_quality_matches:
                    bot = bot_instance if bot_instance else getattr(self.db, 'bot', None)
                    if not bot:
                        return

                    match_count = len(high_quality_matches)
                    top_match = high_quality_matches[0]
                    brand_title = str(top_match.get('brand') or top_match.get('property_use') or vertical_type).title()

                    msg = (
                        f"🎯 <b>Instant Matches Found!</b>\n\n"
                        f"We found <b>{match_count}</b> active listings that highly match your new request.\n"
                        f"Top Match: <b>{brand_title}</b> ({int(top_match.get('_match_score'))}% match).\n\n"
                        f"Would you like to review these matches now?"
                    )

                    keyboard = [
                        [InlineKeyboardButton("👀 View Matches", callback_data=f"view_matches:{request_id}")],
                        [InlineKeyboardButton("Later", callback_data="ignore_match")]
                    ]

                    try:
                        await bot.send_message(
                            chat_id=int(seeker_id),
                            text=msg,
                            reply_markup=InlineKeyboardMarkup(keyboard),
                            parse_mode="HTML"
                        )
                    except Exception as e:
                        logger.error(f"Failed to push notification to Seeker {seeker_id}: {e}")

        except Exception as e:
            logger.error(f"Failed to execute run_seeker_matching: {e}", exc_info=True)


# ==========================================
# FASTAPI WEBHOOK & BACKGROUND PROCESSING
# ==========================================

class MatchRequest(BaseModel):
    match_id: str
    user_id: int
    payload: Dict[str, Any] = Field(default_factory=dict)


async def run_heavy_matching_pipeline(match_id: str, payload: Dict[str, Any]) -> None:
    """
    Executes heavy semantic embedding, candidate ranking,
    and notification dispatch outside the HTTP request lifecycle.
    """
    try:
        logger.info(f"Starting background match processing for: {match_id}")

        vertical_type = payload.get("vertical_type", "equipment")
        direction = payload.get("direction", "seeker")
        request_id = payload.get("request_id")
        listing_id = payload.get("listing_id")

        engine = MatchingEngine()

        if direction == "seeker" and request_id:
            await engine.run_seeker_matching(request_id=request_id, vertical_type=vertical_type)
        elif direction == "provider" and listing_id:
            await engine.run_provider_matching(listing_id=listing_id, vertical_type=vertical_type)
        else:
            candidates = payload.get("candidates", [])
            user_prefs = payload.get("user_prefs", {})
            if candidates and user_prefs:
                matches = await engine.evaluate_matches(vertical_type, user_prefs, candidates)
                logger.info(f"Evaluated {len(matches)} matches for pipeline {match_id}")

        logger.info(f"Completed background match processing for: {match_id}")
    except Exception as err:
        logger.error(f"Failed background task for {match_id}: {err}", exc_info=True)


@app.post("/webhook/match", status_code=status.HTTP_202_ACCEPTED)
async def handle_match_webhook(
        request: MatchRequest,
        background_tasks: BackgroundTasks
):
    # Enqueue task to run right after sending response
    background_tasks.add_task(
        run_heavy_matching_pipeline,
        match_id=request.match_id,
        payload=request.payload
    )

    # Return immediate response to satisfy webhook timeout limits
    return {
        "status": "queued",
        "match_id": request.match_id
    }
