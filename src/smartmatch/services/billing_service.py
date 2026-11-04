import logging
import asyncio
import time
from datetime import datetime, timezone
from typing import Dict, Any, Optional, Tuple, TYPE_CHECKING
import httpx
from decimal import Decimal, ROUND_HALF_UP
from bs4 import BeautifulSoup
import re
import uuid

# Import only during static type checking to prevent circular imports
if TYPE_CHECKING:
    from supabase import AsyncClient

logger = logging.getLogger("smartmatch.billing")

DEFAULT_USD_TO_ETB_RATE: float = 160.82


async def sync_currency_rates(
        supabase: Any,
        live_rate: Optional[float] = None
) -> Dict[str, Any]:
    """
    Syncs live and fallback currency rates to the Supabase 'currencies' table.
    Ensures USD stores the multiplier to ETB (e.g. 160.82) and ETB defaults to 1.0.
    Maintains structural alignment with "currencies_rows.csv".
    """
    if live_rate is None or live_rate <= 0:
        live_rate = 160.82  # Baseline fallback
        logger.warning(f"Using fallback rate of {live_rate} ETB/USD for Supabase sync.")

    now_iso = datetime.now(timezone.utc).isoformat()

    # Corrected schema alignment for 'currencies' table
    records = [
        {
            "currency_code": "USD",
            "rate_to_etb": round(float(live_rate), 4),
            "updated_at": now_iso
        },
        {
            "currency_code": "ETB",
            "rate_to_etb": 1.0,
            "updated_at": now_iso
        }
    ]

    try:
        response = await supabase.table("currencies").upsert(
            records,
            on_conflict="currency_code"
        ).execute()

        logger.info(f"✅ Currency rates synchronized with Supabase at {now_iso}.")
        return {
            "status": "success",
            "synced_at": now_iso,
            "usd_etb_rate": live_rate,
            "data": getattr(response, "data", [])
        }
    except Exception as e:
        logger.error(f"❌ Failed to sync currency rates: {str(e)}", exc_info=True)
        return {"status": "error", "message": str(e)}


class CurrencyConverter:
    """
    Resilient Currency Converter with multi-tier fallbacks, strict timeout guards,
    and circuit-breaker cooldowns to handle remote scraping failures smoothly.
    """

    DEFAULT_RATE: float = 160.82
    SCRAPE_TIMEOUT_SECONDS: float = 10.0
    CACHE_TTL_SECONDS: float = 3600.0
    COOLDOWN_SECONDS: float = 300.0

    def __init__(self, db_client: Optional[Any] = None, default_rate: float = DEFAULT_USD_TO_ETB_RATE):
        self.db_client = db_client
        self._default_rate: float = default_rate
        self._memory_cache_rate: Optional[float] = None
        self._memory_cache_time: float = 0.0
        self._last_failure_time: float = 0.0

    @property
    def db(self) -> Optional[Any]:
        """Centralized helper to resolve Supabase client instance safely."""
        if self.db_client is None:
            return None
        return getattr(self.db_client, "supabase", self.db_client)

    @classmethod
    async def fetch_api_exchange_rate(cls, client: httpx.AsyncClient) -> Optional[float]:
        """
        Fast-track fetch from structured JSON APIs before parsing HTML.
        """
        api_urls = [
            "https://open.er-api.com/v6/latest/USD",
            "https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1/currencies/usd.json"
        ]
        for url in api_urls:
            try:
                res = await client.get(url, timeout=3.0)
                if res.status_code == 200:
                    data = res.json()
                    # Handle Open Exchange Rates format
                    if "rates" in data and "ETB" in data["rates"]:
                        rate = float(data["rates"]["ETB"])
                        if rate >= 120.0:
                            return round(rate, 4)
                    # Handle Fawaz Ahmed API format
                    elif "usd" in data and "etb" in data["usd"]:
                        rate = float(data["usd"]["etb"])
                        if rate >= 120.0:
                            return round(rate, 4)
            except Exception as e:
                logger.debug(f"JSON API check skipped for {url}: {e}")
        return None

    @classmethod
    async def scrape_nbe_usd_to_etb(cls) -> Optional[float]:
        """
        Scrapes NBE daily exchange rates with multi-source fallback:
        1. Fast JSON exchange APIs.
        2. Structured BeautifulSoup DOM table extraction.
        """
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        request_timeout = httpx.Timeout(4.0, connect=2.5)

        async with httpx.AsyncClient(verify=False, follow_redirects=True, timeout=request_timeout) as client:
            # Step 1: Query structured JSON exchange APIs
            api_rate = await cls.fetch_api_exchange_rate(client)
            if api_rate:
                logger.info(f"Successfully retrieved USD/ETB rate from structured API: {api_rate}")
                return api_rate

            # Step 2: HTML Scraping with precise DOM row isolation
            urls = [
                "https://nbe.gov.et/exchange/",
                "https://nbe.gov.et/transaction-exchange-rates-for-major-currencies-against-birr/",
                "https://nbe.gov.et/"
            ]

            for url in urls:
                try:
                    res = await client.get(url, headers=headers)
                    if res.status_code != 200:
                        continue

                    soup = BeautifulSoup(res.text, 'html.parser')
                    rows = soup.find_all('tr')

                    for row in rows:
                        row_text = row.get_text(separator=' ', strip=True).upper()
                        # Strict boundary match to prevent false positives
                        if re.search(r'\b(USD|US\s*DOLLAR)\b', row_text):
                            cells = row.find_all(['td', 'th'])
                            extracted_rates = []

                            for cell in cells:
                                raw_text = cell.get_text().strip().replace(',', '')
                                # Skip date strings (e.g. 2026-09-24 or 24/09/2026)
                                if re.search(r'\d{2,4}[-/]\d{1,2}[-/]\d{2,4}', raw_text):
                                    continue

                                matches = re.findall(r'\b\d{2,3}\.\d{1,4}\b', raw_text)
                                for val_str in matches:
                                    val = float(val_str)
                                    if 120.0 <= val <= 300.0:
                                        extracted_rates.append(val)

                            # Return the maximum rate found (selling/indicative rate)
                            if extracted_rates:
                                active_rate = max(extracted_rates)
                                logger.info(f"Scraped active USD/ETB rate {active_rate} from {url}")
                                return active_rate

                except Exception as err:
                    logger.debug(f"Failed parsing NBE target {url}: {err}")
                    continue

        return None

    async def _safe_scrape_live_rate(self) -> Optional[float]:
        """Executes live scrape bounded by strict asyncio timeout and circuit-breaker state."""
        now = time.time()

        # Circuit Breaker: Skip scraping if recent failure occurred within cooldown window
        if (now - self._last_failure_time) < self.COOLDOWN_SECONDS:
            logger.info("Scraper in circuit-breaker cooldown window. Bypassing live request.")
            return self._memory_cache_rate  # Serve cached rate during cooldown if available

        try:
            # Enforce hard upper bound timeout across all scrape endpoints
            live_rate = await asyncio.wait_for(
                self.scrape_nbe_usd_to_etb(),
                timeout=self.SCRAPE_TIMEOUT_SECONDS
            )
            if live_rate:
                self._memory_cache_rate = live_rate
                self._memory_cache_time = now
                self._last_failure_time = 0.0  # Reset circuit breaker on success
                return live_rate
        except asyncio.TimeoutError:
            logger.warning(
                f"Live rate scrape timed out after {self.SCRAPE_TIMEOUT_SECONDS}s. Entering cooldown and fallback tiers."
            )
            self._last_failure_time = now
        except Exception as err:
            logger.warning(f"Live rate scrape failed ({err}). Entering cooldown and fallback tiers.")
            self._last_failure_time = now

        return self._memory_cache_rate

    async def get_latest_exchange_rate(self) -> float:
        """
        Fetches active exchange rate using a 5-tier resilience cascade.
        """
        now = time.time()

        # Tier 1: Try Live Scrape with asyncio timeout guard
        live_rate = await self._safe_scrape_live_rate()
        if live_rate:
            if self.db_client is not None:
                db_target = getattr(self.db_client, "supabase", self.db_client)
                await sync_currency_rates(db_target, live_rate)
            return live_rate

        # Tier 2: Serve valid in-memory cached rate if within TTL
        if self._memory_cache_rate and (now - self._memory_cache_time < self.CACHE_TTL_SECONDS):
            logger.info(
                f"Serving cached exchange rate: {self._memory_cache_rate} ETB/USD "
                f"(Age: {int(now - self._memory_cache_time)}s)"
            )
            return self._memory_cache_rate

        # Explicit `: Any` type annotation resolves PyCharm's static inspection warning
        db: Any = getattr(self.db_client, "supabase", self.db_client) if self.db_client is not None else None
        if db is not None:
            # Tier 3: Query system_config table
            try:
                res = await db.table("system_config").select("value").eq("key", "usd_to_etb_rate").maybe_single().execute()
                if res and getattr(res, "data", None) and res.data.get("value"):
                    rate = float(res.data["value"])
                    self._memory_cache_rate = rate
                    self._memory_cache_time = now
                    return rate
            except Exception as db_err:
                logger.error(f"Error reading system_config rate: {db_err}")

            # Tier 4: Query currencies table
            try:
                res = await db.table("currencies").select("rate_to_etb").eq("currency_code", "USD").maybe_single().execute()
                if rate_data := getattr(res, "data", None):
                    if rate_val := rate_data.get("rate_to_etb"):
                        rate = float(rate_val)
                        self._memory_cache_rate = rate
                        self._memory_cache_time = now
                        return rate
            except Exception as db_err:
                logger.error(f"Error reading currencies table rate: {db_err}")

        # Tier 5: Static System Constant Fallback
        logger.warning(
            f"All live and cached exchange rate sources failed/timed out. "
            f"Using static default: {self._default_rate} ETB/USD"
        )
        return float(self._default_rate)


class BillingService:
    """
    Production-grade Financial Ledger and Payment Orchestrator.
    Manages deal commission billing, dynamic currency conversions,
    and gateway invoicing interfaces.
    """

    LOGISTICS_COMMISSION_RATE = Decimal("0.01")

    # Currency-specific Commission Bounds
    MIN_COMMISSION = Decimal("5.00")  # 5 ETB
    MAX_ASSET_COMMISSION = Decimal("5000.00")  # 5000 ETB

    MIN_COMMISSION_USD = Decimal("1.00")  # 1 USD
    MAX_COMMISSION_USD = Decimal("200.00")  # 200 USD

    TIER_1_CAP = Decimal("20000.00")
    TIER_1_RATE = Decimal("0.01")
    TIER_2_CAP = Decimal("100000.00")
    TIER_2_RATE = Decimal("0.005")
    TIER_3_CAP = Decimal("1860000.00")
    TIER_3_RATE = Decimal("0.0025")

    def __init__(self, webhook_handler: Optional[Any] = None, db_client: Optional[Any] = None):
        self.webhook_handler = webhook_handler
        self.db_client = db_client
        self.supabase = db_client

        # Bind self.db explicitly (unwrapping underlying client if wrapped in DBManager)
        self.db = getattr(db_client, "client", db_client) if db_client else None

        self.currency_converter = CurrencyConverter(db_client=self.db_client)

    async def convert(
            self,
            amount: float,
            from_currency: str,
            to_currency: str,
            custom_rate: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Single unified currency conversion engine supporting USD <-> ETB swaps.
        """
        from_curr = str(from_currency).strip().upper()
        to_curr = str(to_currency).strip().upper()
        amt = float(amount)

        if from_curr == to_curr:
            return {
                "original_amount": round(amt, 2),
                "original_currency": from_curr,
                "converted_amount": round(amt, 2),
                "target_currency": to_curr,
                "applied_rate": 1.0
            }

        # Resolve rate dynamically if custom_rate is not provided
        rate = float(custom_rate) if custom_rate and float(
            custom_rate) > 0 else await self.currency_converter.get_latest_exchange_rate()

        if from_curr == "USD" and to_curr == "ETB":
            converted = round(amt * rate, 2)
        elif from_curr == "ETB" and to_curr == "USD":
            converted = round(amt / rate, 2)
        else:
            raise ValueError(f"Unsupported currency pair conversion: {from_curr} to {to_curr}")

        return {
            "original_amount": round(amt, 2),
            "original_currency": from_curr,
            "converted_amount": converted,
            "target_currency": to_curr,
            "applied_rate": round(rate, 4)
        }

    @classmethod
    def calculate_logistics_commission(cls, agreed_price: float, currency: str = "ETB") -> float:
        """
        Calculates a flat 1% commission for freight/ride logistics,
        enforcing currency-specific minimum floors and maximum caps (5-5000 ETB / 1-200 USD).
        """
        price = Decimal(str(agreed_price))
        commission = price * cls.LOGISTICS_COMMISSION_RATE
        curr = str(currency).strip().upper()

        if curr == "USD":
            final_fee = max(commission, cls.MIN_COMMISSION_USD)
            final_fee = min(final_fee, cls.MAX_COMMISSION_USD)
        else:
            final_fee = max(commission, cls.MIN_COMMISSION)
            final_fee = min(final_fee, cls.MAX_ASSET_COMMISSION)

        return float(final_fee.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

    @classmethod
    def calculate_asset_commission(cls, value: float, currency: str = "ETB") -> float:
        """
        Calculates a tiered commission for housing, equipment, and vehicles,
        bound by currency-specific floor and ceiling limits (5-5000 ETB / 1-200 USD).
        """
        val = Decimal(str(value))
        curr = str(currency).strip().upper()

        max_cap = cls.MAX_COMMISSION_USD if curr == "USD" else cls.MAX_ASSET_COMMISSION
        min_comm = cls.MIN_COMMISSION_USD if curr == "USD" else cls.MIN_COMMISSION

        if val > cls.TIER_3_CAP:
            return float(max_cap.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

        commission = Decimal("0.0")

        tier1_val = min(val, cls.TIER_1_CAP)
        commission += tier1_val * cls.TIER_1_RATE

        if val > cls.TIER_1_CAP:
            tier2_val = min(val - cls.TIER_1_CAP, cls.TIER_2_CAP - cls.TIER_1_CAP)
            commission += tier2_val * cls.TIER_2_RATE

        if val > cls.TIER_2_CAP:
            tier3_val = val - cls.TIER_2_CAP
            commission += tier3_val * cls.TIER_3_RATE

        final_fee = max(commission, min_comm)
        final_fee = min(final_fee, max_cap)
        return float(final_fee.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

    async def initiate_deal_invoice(
            self,
            deal_id: uuid.UUID,
            chat_id: int,
            final_charge_amount: float,
            final_currency: str,
            tx_ref: str,
            domain: str,
            agreed_price: float,
            original_currency: str
    ) -> str:
        """
        Generates a Chapa checkout link by delegating the API call to the webhook handler,
        and persists or updates the pending invoice record in the invoices table.
        Enforces strict currency validation to prevent USD/ETB mismatches during checkout link creation.
        """
        db_client = getattr(self, "db_client", None) or getattr(self, "db", None) or getattr(self, "supabase", None)
        if db_client and hasattr(db_client, "client"):
            db_client = db_client.client

        if not db_client or not hasattr(db_client, "table"):
            logger.critical("Billing database client handle is uninitialized or invalid.")
            raise RuntimeError("Billing sync operation failed before outbound gateway processing.")

        # -------------------------------------------------------------------------
        # 1. CURRENCY SANITY & NORMALIZATION GUARD
        # -------------------------------------------------------------------------
        resolved_original_currency = str(original_currency or "ETB").strip().upper()
        resolved_final_currency = str(final_currency or "ETB").strip().upper()

        # Safety override: If original currency passed as ETB but final_currency is USD (or vice-versa),
        # or if the amount structure indicates USD pricing, enforce correct currency label.
        if resolved_original_currency == "ETB" and resolved_final_currency == "USD":
            resolved_original_currency = "USD"
        elif resolved_original_currency == "USD" and resolved_final_currency not in {"USD", "ETB"}:
            resolved_final_currency = "USD"

        # Normalize currency for Chapa gateway compatibility
        supported_chapa_currencies = {"ETB", "USD"}
        gateway_currency = resolved_final_currency if resolved_final_currency in supported_chapa_currencies else "ETB"

        # -------------------------------------------------------------------------
        # 2. DELEGATE OUTBOUND API PROCESSING TO WEBHOOK HANDLER
        # -------------------------------------------------------------------------
        if not self.webhook_handler:
            logger.critical("Webhook handler not injected; cannot process gateway authorization payload.")
            raise RuntimeError("Payment infrastructure service is temporarily unreachable.")

        try:
            clean_amount = round(float(final_charge_amount), 2)
            logger.info(
                f"Delegating {domain} commission checkout link creation for Deal {deal_id}. "
                f"Agreed Price: {agreed_price} {resolved_original_currency} | "
                f"Calculated Fee: {clean_amount} {gateway_currency}"
            )

            checkout_url = await self.webhook_handler.initialize_payment(
                amount=clean_amount,
                email=f"user_{chat_id}@smartmatch.africa",
                first_name=f"User_{chat_id}",
                tx_ref=tx_ref,
                currency=gateway_currency,
                custom_title="SMA Commission"
            )

        except Exception as he:
            logger.critical(f"Transport/delegation error reaching payment edge nodes: {str(he)}")
            raise RuntimeError("Payment infrastructure service is temporarily unreachable.")

        now_iso = datetime.now(timezone.utc).isoformat()

        # -------------------------------------------------------------------------
        # 3. PERSIST OR UPDATE INVOICE RECORD
        # -------------------------------------------------------------------------
        try:
            invoice_payload = {
                "deal_id": str(deal_id),
                "chat_id": int(chat_id),
                "tx_ref": tx_ref,
                "amount": clean_amount,
                "currency": gateway_currency,
                "status": "pending",
                "checkout_url": checkout_url,
                "updated_at": now_iso
            }

            await db_client.table("invoices") \
                .upsert(invoice_payload, on_conflict="deal_id") \
                .execute()

        except Exception as db_err:
            logger.critical(f"Failed to record invoice in invoices table: {db_err}", exc_info=True)
            raise RuntimeError("Failed to create invoice record with transaction reference.")

        return checkout_url

    async def initiate_commission_billing(
            self,
            deal_id: str,
            agreed_price: float,
            seeker_id: int,
            listing_id: Optional[int] = None,
            target_payment_currency: str = "ETB"
    ) -> str:
        """
        Orchestrates fee calculation and triggers deal invoicing via the invoices table.
        """
        logger.info(
            f"Triggered commission billing orchestration loop for Deal ID: {deal_id} "
            f"(Passed Listing ID: {listing_id})"
        )

        if not deal_id or not isinstance(deal_id, str):
            raise ValueError(f"Invalid deal identification token format provided: {type(deal_id)}")

        if agreed_price <= 0:
            raise ValueError(f"Agreed contract price parameter must be strictly positive. Got: {agreed_price}")

        if not self.db_client:
            logger.critical("Database reference client is missing inside BillingService context.")
            raise RuntimeError("Database engine client is unavailable to process billing records.")

        try:
            parsed_deal_uuid = uuid.UUID(deal_id.strip())
        except ValueError as uuid_err:
            raise ValueError(f"Provided deal token '{deal_id}' is not a valid UUID: {uuid_err}")

        # 1. Fetch deal metadata from deals table (read-only inspection)
        try:
            response = await self.db_client.table("deals") \
                .select("vertical_type, currency, request_id, listing_id") \
                .eq("id", str(parsed_deal_uuid)) \
                .execute()

            if not response.data:
                raise ValueError(f"No valid deal instance found for ID: {deal_id}")

            deal_record = response.data[0]
            raw_vertical_type = deal_record.get("vertical_type")
            deal_currency = str(deal_record.get("currency") or "ETB").strip().upper()

        except Exception as db_err:
            logger.critical(f"Supabase SDK failure inspecting contract tracking fields: {db_err}", exc_info=True)
            raise RuntimeError("Internal transaction service failed to verify deal records.")

        # 2. Perform exchange rate conversion to ETB for tier calculation
        conversion_res = await self.convert(
            amount=float(agreed_price),
            from_currency=deal_currency,
            to_currency="ETB"
        )
        base_agreed_price_etb = conversion_res["converted_amount"]

        # 3. Normalize vertical domain and calculate commission fee in ETB
        vertical_normalization_map = {
            "freight_ride": "freight_ride", "freight": "freight_ride", "logistics": "freight_ride",
            "ride": "freight_ride", "housing": "housing", "real_estate": "housing",
            "equipment": "equipment", "equipment_rental": "equipment",
            "vehicle": "vehicle", "automotive": "vehicle"
        }
        normalized_domain = vertical_normalization_map.get(str(raw_vertical_type).lower().strip(), "vehicle")

        if normalized_domain == "freight_ride":
            charge_amount_etb = self.calculate_logistics_commission(base_agreed_price_etb, currency="ETB")
        elif normalized_domain in ["housing", "equipment", "vehicle"]:
            charge_amount_etb = self.calculate_asset_commission(base_agreed_price_etb, currency="ETB")
        else:
            logger.error(f"Unknown domain mapping '{normalized_domain}'. Applying max asset commission.")
            charge_amount_etb = float(self.MAX_ASSET_COMMISSION)

        # 4. Convert calculated commission fee to target payment currency and enforce currency-specific bounds
        target_curr = target_payment_currency.strip().upper()
        if target_curr != "ETB":
            fee_conversion = await self.convert(
                amount=charge_amount_etb,
                from_currency="ETB",
                to_currency=target_curr
            )
            converted_fee = Decimal(str(fee_conversion["converted_amount"]))

            if target_curr == "USD":
                final_fee = max(converted_fee, self.MIN_COMMISSION_USD)
                final_fee = min(final_fee, self.MAX_COMMISSION_USD)
            else:
                final_fee = max(converted_fee, self.MIN_COMMISSION)
                final_fee = min(final_fee, self.MAX_ASSET_COMMISSION)

            final_charge_amount = float(final_fee.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
            final_currency = target_curr
        else:
            final_fee = max(Decimal(str(charge_amount_etb)), self.MIN_COMMISSION)
            final_fee = min(final_fee, self.MAX_ASSET_COMMISSION)
            final_charge_amount = float(final_fee.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
            final_currency = "ETB"

        tx_ref = f"sm_{parsed_deal_uuid.hex[:8]}_{int(datetime.now(timezone.utc).timestamp())}"

        # 5. Delegate invoice record creation directly to initiate_deal_invoice
        return await self.initiate_deal_invoice(
            deal_id=parsed_deal_uuid,
            chat_id=int(seeker_id),
            final_charge_amount=final_charge_amount,
            final_currency=final_currency,
            tx_ref=tx_ref,
            domain=normalized_domain,
            agreed_price=agreed_price,
            original_currency=deal_currency
        )

    async def confirm_payment(
            self, tx_ref: str, max_retries: int = 3,
            retry_delay: float = 1.5) -> Tuple[uuid.UUID, int]:
        """
        Updates financial records to mark an invoice as 'paid'. Invoked by webhook handler.
        Includes an internal retry polling loop to handle async database replication/commit lag.
        """
        if not self.db_client:
            raise RuntimeError("Database client unavailable.")

        for attempt in range(1, max_retries + 1):
            try:
                response = await self.db_client.table("invoices") \
                    .update({"status": "paid", "updated_at": datetime.now(timezone.utc).isoformat()}) \
                    .eq("tx_ref", tx_ref) \
                    .execute()

                if response and response.data:
                    row = response.data[0]
                    logger.info(f"✅ Invoice payment confirmed for TX Reference: {tx_ref}")
                    return uuid.UUID(row["deal_id"]), int(row["chat_id"])
            except Exception as db_err:
                logger.warning(f"Database query attempt {attempt}/{max_retries} failed for tx_ref {tx_ref}: {db_err}")

            if attempt < max_retries:
                logger.warning(
                    f"⚠️ Transaction reference '{tx_ref}' not found on attempt {attempt}/{max_retries}. "
                    f"Retrying in {retry_delay}s due to potential DB commit lag..."
                )
                await asyncio.sleep(retry_delay)

        logger.error(f"❌ Failed to locate invoice for transaction reference after {max_retries} attempts: {tx_ref}")
        raise ValueError(f"No transaction reference matches the identifier: {tx_ref}")

    async def finalize_transaction(
            self,
            deal_id: str,
            user_id: str,
            amount: float,
            commission: float,
            currency: str
    ) -> bool:
        """
        Logs the commission execution and strictly transitions the final status state mapping.
        Includes an idempotency check to handle retry logic gracefully, aligned with commission tracking schemas.
        """
        if not self.db_client:
            raise RuntimeError("Database client unavailable.")

        # 1. Idempotency Check: Verify if already finalized before proceeding
        deal_check = await self.db_client.table("deals").select("status").eq("id", deal_id).single().execute()

        if not deal_check.data:
            raise ValueError(f"Deal {deal_id} not found.")

        if deal_check.data.get("status") == "finalized":
            logger.info(f"Deal {deal_id} already finalized. Skipping duplicate execution.")
            return True

        # 2. Log the commission record via upsert to guarantee collision safety on retries
        await self.db_client.table("commissions").upsert({
            "deal_id": deal_id,
            "user_id": str(user_id),
            "gross_amount": float(amount),
            "commission_fee": float(commission),
            "currency_code": currency.upper(),
            "status": "paid",
            "updated_at": datetime.now(timezone.utc).isoformat()
        }, on_conflict="deal_id").execute()

        # 3. Update the deal status to 'finalized'
        result = await self.db_client.table("deals") \
            .update({"status": "finalized", "updated_at": datetime.now(timezone.utc).isoformat()}) \
            .eq("id", deal_id) \
            .execute()

        if not result.data:
            raise ValueError(f"Failed to finalize deal {deal_id} due to unexpected database state.")

        logger.info(f"Transaction finalized successfully for deal: {deal_id}")
        return True
